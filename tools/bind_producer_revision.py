#!/usr/bin/env python3
"""Bind the manual lint-baseline generator to its producers' exact revision.

Issue #264. ``.github/workflows/rtl-lint-baseline.yml`` regenerates
``tb/rtl-lint-baseline.json`` from a completed ``tb-sim.yml`` run's
``oneshot-*`` artifacts. Each harness records ``git rev-parse HEAD`` of the
tree it ran on as ``identity.git_head``, and the aggregate gate
(``tools/qualify_rtl_modules.py``) refuses evidence whose ``git_head`` is not
its own HEAD. What the producers' HEAD *is* depends on the run's event:

* ``push`` -- ``actions/checkout`` checks out the pushed commit, so the
  recorded revision equals the run's ``headSha``;
* ``pull_request`` -- ``actions/checkout`` checks out GitHub's test merge
  commit (``refs/pull/<N>/merge``), so the recorded revision is a merge commit
  whose parents are ``[base, headSha]`` and which is NOT ``headSha`` itself.

The generator therefore cannot check out its dispatched ref and compare that
with ``headSha``: on a PR run the two identities differ by construction. This
helper makes the binding explicit and fail-closed:

``recorded``
    Read every integrated lane's evidence record from the downloaded
    artifacts and print the single revision they all name. Absent or
    malformed records, a non-SHA ``git_head``, or lanes that disagree are
    errors -- one generator run binds to one revision.

``verify`` (run after checking that revision out)
    Require ``git rev-parse HEAD`` to equal the recorded revision, the work
    tree to be clean, and the revision to belong to the run: equal to
    ``headSha`` for ``push``; a two-parent merge commit whose second parent is
    ``headSha`` for ``pull_request``. Any other event is refused.

Source-digest freshness, ``git_tree_dirty`` and the per-record HEAD match are
still enforced afterwards by ``qualify_rtl_modules.py`` itself; this helper
only decides *which* revision that check is allowed to run on.

Usage::

    python3 tools/bind_producer_revision.py recorded --artifacts DIR
    python3 tools/bind_producer_revision.py verify --artifacts DIR \\
        --run-head-sha SHA --run-event {push,pull_request}
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

from qualify_rtl_modules import INTEGRATED_LANES  # noqa: E402

SHA_RE = re.compile(r"^[0-9a-f]{40}$")

#: tb-sim.yml's triggers. Anything else has no defined producer checkout.
SUPPORTED_EVENTS = ("push", "pull_request")


def _is_sha(value: object) -> bool:
    return isinstance(value, str) and SHA_RE.match(value) is not None


def recorded_revision(
    artifacts: Path, lanes: Optional[Sequence[str]] = None
) -> tuple[Optional[str], list]:
    """Return ``(revision, errors)`` named by every lane's evidence record."""

    names = list(lanes) if lanes is not None else list(INTEGRATED_LANES)
    errors: list = []
    heads: dict[str, str] = {}
    for name in names:
        lane = INTEGRATED_LANES[name]
        path = artifacts / lane.name / lane.evidence_name
        if not path.is_file():
            errors.append("%s evidence record %s is absent" % (name, path))
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            errors.append("%s evidence record is unreadable/malformed: %s"
                          % (name, error))
            continue
        identity = record.get("identity") if isinstance(record, dict) else None
        head = identity.get("git_head") if isinstance(identity, dict) else None
        if not _is_sha(head):
            errors.append("%s identity.git_head is not a 40-character "
                          "lowercase commit SHA: %r" % (name, head))
            continue
        heads[name] = head
    if not names:
        errors.append("no integrated lane was named; nothing to bind")
    distinct = sorted(set(heads.values()))
    if len(distinct) > 1:
        errors.append(
            "producers recorded different revisions (%s); one generator run "
            "binds to exactly one revision"
            % ", ".join("%s=%s" % item for item in sorted(heads.items()))
        )
    if errors:
        return None, errors
    return distinct[0], []


def binding_errors(
    revision: str,
    *,
    run_head_sha: str,
    run_event: str,
    parents: Sequence[str],
) -> list:
    """Why ``revision`` (with git-observed ``parents``) is not this run's."""

    if not _is_sha(revision):
        return ["revision %r is not a 40-character lowercase commit SHA"
                % (revision,)]
    if not _is_sha(run_head_sha):
        return ["run headSha %r is not a 40-character lowercase commit SHA"
                % (run_head_sha,)]
    if run_event == "push":
        if revision != run_head_sha:
            return ["push run: producers recorded %s but the run's headSha is "
                    "%s; the evidence is not this run's revision"
                    % (revision, run_head_sha)]
        return []
    if run_event == "pull_request":
        if revision == run_head_sha:
            return ["pull_request run: producers recorded the PR head %s "
                    "itself, but a pull_request checkout is the test merge "
                    "commit; refusing an evidence identity the run could not "
                    "have produced" % revision]
        if len(parents) != 2:
            return ["pull_request run: %s has %d parent(s), not the 2 of a "
                    "test merge commit" % (revision, len(parents))]
        if parents[1] != run_head_sha:
            return ["pull_request run: %s merges PR head %s, not the run's "
                    "headSha %s; the evidence is from a different (stale) "
                    "revision" % (revision, parents[1], run_head_sha)]
        return []
    return ["run event %r is not one of %s; no producer checkout is defined "
            "for it" % (run_event, ", ".join(SUPPORTED_EVENTS))]


def checkout_errors(revision: str, head: Optional[str], status: str) -> list:
    """Why the current checkout is not exactly, and cleanly, ``revision``."""

    errors = []
    if head != revision:
        errors.append("git rev-parse HEAD is %s, not the producers' recorded "
                      "revision %s" % (head, revision))
    if status.strip():
        errors.append("the work tree is not clean:\n%s" % status.rstrip())
    return errors


def _git(repo: Path, *args: str) -> Optional[str]:
    completed = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True
    )
    return completed.stdout if completed.returncode == 0 else None


def verify_checkout(
    artifacts: Path, repo: Path, *, run_head_sha: str, run_event: str
) -> tuple[Optional[str], list]:
    revision, errors = recorded_revision(artifacts)
    if revision is None:
        return None, errors
    head = (_git(repo, "rev-parse", "HEAD") or "").strip() or None
    status = _git(repo, "status", "--porcelain")
    if status is None:
        return revision, ["git status failed in %s" % repo]
    errors += checkout_errors(revision, head, status)
    line = _git(repo, "rev-list", "--parents", "-n", "1", revision)
    if line is None:
        return revision, errors + ["cannot read the parents of %s; is it "
                                   "fetched?" % revision]
    parents = line.split()[1:]
    errors += binding_errors(revision, run_head_sha=run_head_sha,
                             run_event=run_event, parents=parents)
    return revision, errors


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("recorded", help="print the producers' revision")
    rec.add_argument("--artifacts", type=Path, required=True)
    ver = sub.add_parser("verify", help="bind HEAD to the producers' run")
    ver.add_argument("--artifacts", type=Path, required=True)
    ver.add_argument("--run-head-sha", required=True)
    ver.add_argument("--run-event", required=True)
    ver.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)

    if args.command == "recorded":
        revision, errors = recorded_revision(args.artifacts)
    else:
        revision, errors = verify_checkout(
            args.artifacts, args.repo,
            run_head_sha=args.run_head_sha, run_event=args.run_event,
        )
    if errors:
        for error in errors:
            print("ERROR: %s" % error, file=sys.stderr)
        return 1
    if args.command == "recorded":
        print(revision)
    else:
        print("BOUND: HEAD %s is the %s run's producer revision (headSha %s)"
              % (revision, args.run_event, args.run_head_sha))
    return 0


if __name__ == "__main__":
    sys.exit(main())
