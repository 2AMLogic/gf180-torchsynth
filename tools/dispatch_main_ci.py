#!/usr/bin/env python3
"""Select and dispatch the heavy main suites (CI, TB sim) for the newest main.

Issue #284.  ``ci.yml`` and ``tb-sim.yml`` no longer run on every push to
main.  ``.github/workflows/ci-main-schedule.yml`` calls this helper on a
schedule; for each suite it decides, for the *current* tip of main, one of
(the tip is re-read immediately before each suite is acted on and before each
dispatch; if main advanced, every suite not yet acted on is re-selected):

``reuse``     a completed, successful execution of that exact workflow on that
              exact SHA already exists (every required job, every matrix leg,
              succeeded).  Nothing is dispatched; the evidence run is reported.
``active``    an execution for that workflow/SHA is queued or in progress.  No
              redundant dispatch.
``dispatch``  no usable evidence.  The suite is dispatched on ``main``.

Evidence rules (all must hold; anything else is *not* evidence):

* same repository, same workflow file, ``head_branch == main``;
* ``head_sha`` equals the selected main SHA exactly;
* event is ``push``, ``schedule`` or ``workflow_dispatch`` (never
  ``pull_request``; a PR run checks out a merge commit and proves nothing about
  main);
* the *newest* such run (by run number, then attempt) is the only candidate, so
  a later failed/cancelled/timed-out run is never hidden by an older green one;
* its conclusion is ``success`` AND its jobs include every required job (and
  every matrix leg) with conclusion ``success`` -- a skipped required job or a
  missing producer is not a pass, and no other job may be non-successful.

API, authentication and parse failures (including malformed run-list
responses while observing a dispatch) raise ``SelectorError``; they are never
inferred to be a cache hit or a "not yet visible" run.  The dispatcher itself is never evidence: it is a
different workflow.  After dispatching, the resulting run's own ``head_sha`` is
reported as authoritative for what executed; it is never relabelled as the
selected SHA.

Standard library only (runs on the stock runner python and in ci.yml).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable

API = "https://api.github.com"
PER_PAGE = 100
MAX_PAGES = 20
MAX_SELECTION_ROUNDS = 3
EVIDENCE_EVENTS = frozenset({"push", "schedule", "workflow_dispatch"})
ACTIVE_STATUSES = frozenset({"queued", "in_progress", "waiting", "requested", "pending"})

# Required jobs per suite: name -> exact job display names that must exist and
# succeed.  Mirrors the heavy-suite inventory; tests/test_ci_scheduling.py
# checks it against the workflow files so the two cannot drift.
SUITES: dict[str, dict[str, Any]] = {
    "ci.yml": {
        "required_jobs": [
            "contract (3.11)",
            "contract (3.12)",
            "contract (3.13)",
        ],
    },
    "tb-sim.yml": {
        "required_jobs": [
            "sim-selftest (3.11)",
            "sim-selftest (3.12)",
            "sim-selftest (3.13)",
            "sim-lanes-vco",
            "sim-lanes-engines",
            "oneshot-tail-chain",
            "oneshot-whole-voice",
            "rtl-module-qualification",
        ],
    },
}


class SelectorError(RuntimeError):
    """API/auth/parse failure: must surface as a failed step, never a hit."""


class GitHubApi:
    """Minimal REST client; ``get``/``post`` raise SelectorError on any failure."""

    def __init__(self, token: str) -> None:
        if not token:
            raise SelectorError("GITHUB_TOKEN is required")
        self._token = token

    def _request(self, method: str, path: str, body: Any = None) -> Any:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            API + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raise SelectorError(f"{method} {path}: HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise SelectorError(f"{method} {path}: {exc}") from exc
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise SelectorError(f"{method} {path}: unparseable response") from exc

    def get(self, path: str) -> Any:
        return self._request("GET", path)

    def post(self, path: str, body: Any) -> Any:
        return self._request("POST", path, body)


def paginate(api: Any, path: str, key: str) -> list[dict]:
    """Collect every page of a list endpoint; a malformed page is an error."""
    items: list[dict] = []
    sep = "&" if "?" in path else "?"
    for page in range(1, MAX_PAGES + 1):
        data = api.get(f"{path}{sep}per_page={PER_PAGE}&page={page}")
        if not isinstance(data, dict) or not isinstance(data.get(key), list):
            raise SelectorError(f"{path}: response lacks list {key!r}")
        batch = data[key]
        items.extend(batch)
        if len(batch) < PER_PAGE:
            return items
    raise SelectorError(f"{path}: more than {MAX_PAGES} pages; refusing to guess")


def main_sha(api: Any, repo: str, branch: str) -> str:
    data = api.get(f"/repos/{repo}/commits/{branch}")
    sha = data.get("sha") if isinstance(data, dict) else None
    if not isinstance(sha, str) or len(sha) != 40:
        raise SelectorError(f"cannot resolve {branch} tip for {repo}")
    return sha


def _run_matches(run: dict, repo: str, branch: str, sha: str, workflow: str) -> bool:
    """Same repo, workflow file, branch and exact SHA, non-PR event."""
    path = str(run.get("path", "")).split("@", 1)[0]
    repo_of = lambda key: (run.get(key) or {}).get("full_name")  # noqa: E731
    return (
        path == f".github/workflows/{workflow}"
        and run.get("head_branch") == branch
        and run.get("head_sha") == sha
        and run.get("event") in EVIDENCE_EVENTS
        and repo_of("repository") == repo
        and repo_of("head_repository") == repo
    )


def _order(run: dict) -> tuple[int, int]:
    return int(run.get("run_number", 0)), int(run.get("run_attempt", 1))


def jobs_problems(jobs: list[dict], required: list[str]) -> list[str]:
    """Why these jobs do not prove the suite passed (empty list == they do)."""
    problems: list[str] = []
    by_name: dict[str, list[dict]] = {}
    for job in jobs:
        by_name.setdefault(str(job.get("name")), []).append(job)
    for name in required:
        found = by_name.get(name)
        if not found:
            problems.append(f"required job {name!r} absent")
        elif any(j.get("conclusion") != "success" for j in found):
            problems.append(f"required job {name!r} conclusion != success")
    for job in jobs:
        if job.get("conclusion") != "success":
            problems.append(f"job {job.get('name')!r} conclusion {job.get('conclusion')!r}")
    return sorted(set(problems))


def select_suite(api: Any, repo: str, branch: str, sha: str, workflow: str) -> dict:
    """Decide reuse / active / dispatch for one suite at one SHA."""
    spec = SUITES[workflow]
    runs = paginate(
        api,
        f"/repos/{repo}/actions/workflows/{workflow}/runs?branch={branch}&head_sha={sha}",
        "workflow_runs",
    )
    cands = sorted(
        (r for r in runs if _run_matches(r, repo, branch, sha, workflow)), key=_order
    )
    result: dict[str, Any] = {"workflow": workflow, "sha": sha}
    for run in reversed(cands):
        status = run.get("status")
        if status != "completed" and status not in ACTIVE_STATUSES:
            raise SelectorError(f"run {run.get('id')} has unrecognised status {status!r}")
        if status in ACTIVE_STATUSES:
            return {**result, "action": "active", "run_url": run.get("html_url"),
                    "reason": f"run {run.get('id')} is {run.get('status')}"}
    if not cands:
        return {**result, "action": "dispatch", "reason": "no execution for this SHA"}
    newest = cands[-1]
    if newest.get("conclusion") != "success":
        return {**result, "action": "dispatch", "run_url": newest.get("html_url"),
                "reason": f"newest run concluded {newest.get('conclusion')!r}"}
    jobs = paginate(
        api, f"/repos/{repo}/actions/runs/{newest['id']}/jobs?filter=latest", "jobs"
    )
    problems = jobs_problems(jobs, spec["required_jobs"])
    if problems:
        return {**result, "action": "dispatch", "run_url": newest.get("html_url"),
                "reason": "; ".join(problems)}
    return {**result, "action": "reuse", "run_url": newest.get("html_url"),
            "evidence_sha": newest["head_sha"], "reason": "exact-SHA successful run"}


def select_all(api: Any, repo: str, branch: str, workflows: list[str]) -> tuple[str, list[dict]]:
    """Select against the tip, re-checking that main did not advance mid-way."""
    for _ in range(MAX_SELECTION_ROUNDS):
        sha = main_sha(api, repo, branch)
        decisions = [select_suite(api, repo, branch, sha, w) for w in workflows]
        if main_sha(api, repo, branch) == sha:
            return sha, decisions
    raise SelectorError(f"{branch} kept advancing during selection; retry next window")


def dispatch(api: Any, repo: str, branch: str, workflow: str) -> None:
    api.post(f"/repos/{repo}/actions/workflows/{workflow}/dispatches", {"ref": branch})


def _run_list(data: Any, path: str) -> list[dict]:
    """Validate a workflow-runs list response; malformed shapes are errors.

    A well-formed empty list is legitimate (the run is not visible yet); a
    non-dict, a missing/non-list ``workflow_runs`` or an entry without an
    integer ``id`` is a parse failure and must never read as "nothing there".
    """
    if not isinstance(data, dict) or not isinstance(data.get("workflow_runs"), list):
        raise SelectorError(f"{path}: response lacks list 'workflow_runs'")
    runs = data["workflow_runs"]
    for run in runs:
        if not isinstance(run, dict) or not isinstance(run.get("id"), int) \
                or isinstance(run.get("id"), bool):
            raise SelectorError(f"{path}: malformed workflow run entry {run!r:.80}")
    return runs


def _dispatch_runs_path(repo: str, branch: str, workflow: str) -> str:
    return (f"/repos/{repo}/actions/workflows/{workflow}/runs"
            f"?branch={branch}&event=workflow_dispatch&per_page=10")


def observe_run(api: Any, repo: str, branch: str, workflow: str, known_ids: set,
                sleep: Callable[[float], None] = time.sleep, tries: int = 6) -> dict | None:
    """Find the run our dispatch created; its head_sha is authoritative.

    ``None`` only means a well-formed response did not list a new run yet;
    malformed responses raise ``SelectorError``.
    """
    path = _dispatch_runs_path(repo, branch, workflow)
    for _ in range(tries):
        sleep(5)
        for run in _run_list(api.get(path), path):
            if run["id"] not in known_ids:
                sha = run.get("head_sha")
                if not isinstance(sha, str) or len(sha) != 40:
                    raise SelectorError(f"{path}: run {run['id']} lacks a valid head_sha")
                return run
    return None


def known_run_ids(api: Any, repo: str, branch: str, workflow: str) -> set:
    path = _dispatch_runs_path(repo, branch, workflow)
    return {r["id"] for r in _run_list(api.get(path), path)}


def _describe(d: dict) -> str:
    line = f"{d['workflow']}: {d['action']} @ {d['sha']} ({d['reason']})"
    if d.get("run_url"):
        line += f" evidence/run: {d['run_url']}"
    if d["action"] == "reuse":
        line += f" sha {d['evidence_sha']}"
    return line


def run_once(api: Any, repo: str, branch: str, dry_run: bool,
             sleep: Callable[[float], None] = time.sleep) -> tuple[str, list[str]]:
    """Act on each suite, re-checking the tip before every decision.

    Main can advance between selection and action (notably while a dispatch is
    being observed).  Immediately before each suite's decision is acted on --
    and again immediately before a dispatch POST -- the tip is re-read; if it
    moved, every suite not yet acted on is re-selected at the new tip.  The
    number of re-selections is bounded by ``MAX_SELECTION_ROUNDS``.
    """
    pending = list(SUITES)
    lines: list[str] = []
    reselections = 0
    sha = ""

    def advanced(current: str) -> bool:
        nonlocal reselections
        tip = main_sha(api, repo, branch)
        if tip == current:
            return False
        reselections += 1
        lines.append(f"{branch} advanced {current} -> {tip}; re-selecting {', '.join(pending)}")
        if reselections > MAX_SELECTION_ROUNDS:
            raise SelectorError(
                f"{branch} kept advancing before dispatch; retry next window. "
                f"Done so far: {' | '.join(lines)}"
            )
        return True

    while pending:
        sha, decisions = select_all(api, repo, branch, pending)
        lines.append(f"selected {branch} @ {sha}")
        stale = False
        for d in decisions:
            if advanced(sha):
                stale = True
                break
            line = _describe(d)
            if d["action"] == "dispatch" and not dry_run:
                before = known_run_ids(api, repo, branch, d["workflow"])
                if advanced(sha):
                    stale = True
                    break
                dispatch(api, repo, branch, d["workflow"])
                run = observe_run(api, repo, branch, d["workflow"], before, sleep)
                if run is None:
                    line += " -> dispatched; resulting run not yet visible"
                else:
                    note = "" if run["head_sha"] == sha else f" (differs from selected {sha})"
                    line += (f" -> dispatched run {run.get('html_url')}"
                             f" executed sha {run['head_sha']}{note}")
            lines.append(line)
            pending.remove(d["workflow"])
        if not stale:
            break
    return sha, lines


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    p.add_argument("--branch", default="main")
    p.add_argument("--dry-run", action="store_true", help="select only; never dispatch")
    args = p.parse_args(argv)
    try:
        if not args.repo:
            raise SelectorError("--repo or GITHUB_REPOSITORY is required")
        api = GitHubApi(os.environ.get("GITHUB_TOKEN", ""))
        _, lines = run_once(api, args.repo, args.branch, args.dry_run)
    except SelectorError as exc:
        print(f"::error::dispatch_main_ci: {exc}", file=sys.stderr)
        return 1
    text = "\n".join(lines)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("### Main CI dispatcher\n\n" + "\n".join(f"- {l}" for l in lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
