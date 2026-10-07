"""Main-CI scheduling guards (issue #284).  Stdlib only, no network.

Covers the selector (tools/dispatch_main_ci.py) with a fake API, and the
workflow inventory (triggers, concurrency, permissions, preserved heavy jobs)
as plain-text checks, with negative fixtures showing each guard rejects its
defect.  The live scheduled run and the seven-day measurement are NOT covered
here; see docs/CI-SCHEDULING.md.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import dispatch_main_ci as d  # noqa: E402

WF = ROOT / ".github" / "workflows"
REPO = "2AMLogic/gf180-torchsynth"
SHA = "a" * 40
OTHER = "b" * 40


class FakeApi:
    def __init__(self, tip=SHA, runs=None, jobs=None, fail=None, advance=None):
        self.tip, self.runs, self.jobs = tip, runs or {}, jobs or {}
        self.fail, self.advance = fail, advance  # advance: list of tips by call
        self.posts, self.tip_calls = [], 0

    def get(self, path):
        if self.fail and self.fail in path:
            raise d.SelectorError("boom")
        if "/commits/" in path:
            self.tip_calls += 1
            if self.advance:
                return {"sha": self.advance[min(self.tip_calls - 1, len(self.advance) - 1)]}
            return {"sha": self.tip}
        page = int(re.search(r"[?&]page=(\d+)", path).group(1)) if "&page=" in path else 1
        m = re.search(r"workflows/([\w.-]+)/runs", path)
        if m:
            items = self.runs.get(m.group(1), [])
            key = "workflow_runs"
        else:
            rid = int(re.search(r"runs/(\d+)/jobs", path).group(1))
            items, key = self.jobs.get(rid, []), "jobs"
        return {key: items[(page - 1) * d.PER_PAGE: page * d.PER_PAGE]}

    def post(self, path, body):
        self.posts.append((path, body))
        return {}


def run(wf="ci.yml", rid=1, number=1, sha=SHA, status="completed", conclusion="success",
        event="push", branch="main", repo=REPO, attempt=1):
    return {"id": rid, "run_number": number, "run_attempt": attempt, "head_sha": sha,
            "status": status, "conclusion": conclusion, "event": event,
            "head_branch": branch, "path": f".github/workflows/{wf}@refs/heads/main",
            "repository": {"full_name": repo}, "head_repository": {"full_name": repo},
            "html_url": f"https://example/runs/{rid}"}


def good_jobs(wf):
    return [{"name": n, "conclusion": "success"} for n in d.SUITES[wf]["required_jobs"]]


def decide(runs, jobs=None, wf="ci.yml", sha=SHA):
    api = FakeApi(runs={wf: runs}, jobs=jobs or {r["id"]: good_jobs(wf) for r in runs})
    return d.select_suite(api, REPO, "main", sha, wf)


class SelectorTests(unittest.TestCase):
    def test_no_history_dispatches(self):
        self.assertEqual(decide([])["action"], "dispatch")

    def test_exact_sha_full_success_reuses_with_evidence(self):
        r = decide([run()])
        self.assertEqual(r["action"], "reuse")
        self.assertEqual(r["evidence_sha"], SHA)
        self.assertEqual(r["run_url"], "https://example/runs/1")

    def test_tb_sim_reuse_needs_producers_and_aggregate(self):
        self.assertEqual(decide([run("tb-sim.yml")], wf="tb-sim.yml")["action"], "reuse")
        for missing in ("oneshot-tail-chain", "oneshot-whole-voice", "rtl-module-qualification",
                        "sim-selftest (3.13)"):
            jobs = [j for j in good_jobs("tb-sim.yml") if j["name"] != missing]
            r = decide([run("tb-sim.yml")], {1: jobs}, "tb-sim.yml")
            self.assertEqual(r["action"], "dispatch", missing)

    def test_mismatched_evidence_is_not_evidence(self):
        for bad in (run(sha=OTHER), run(wf="tb-sim.yml"), run(branch="feature"),
                    run(event="pull_request"), run(repo="fork/x")):
            self.assertEqual(decide([bad])["action"], "dispatch", bad)

    def test_dispatcher_run_is_not_evidence(self):
        r = run(wf="ci-main-schedule.yml")
        self.assertEqual(decide([r])["action"], "dispatch")

    def test_every_non_success_conclusion_dispatches(self):
        for c in ("failure", "cancelled", "skipped", "timed_out", "neutral",
                  "action_required", "stale", "startup_failure", None):
            self.assertEqual(decide([run(conclusion=c)])["action"], "dispatch", c)

    def test_skipped_required_job_is_not_a_pass(self):
        jobs = good_jobs("ci.yml")
        jobs[1]["conclusion"] = "skipped"
        self.assertEqual(decide([run()], {1: jobs})["action"], "dispatch")

    def test_unrequired_failed_job_blocks_reuse(self):
        jobs = good_jobs("ci.yml") + [{"name": "extra", "conclusion": "failure"}]
        self.assertEqual(decide([run()], {1: jobs})["action"], "dispatch")

    def test_active_run_suppresses_dispatch(self):
        for status in ("queued", "in_progress", "waiting"):
            r = decide([run(status=status, conclusion=None)])
            self.assertEqual(r["action"], "active", status)

    def test_later_failed_attempt_not_hidden_by_older_green(self):
        runs = [run(rid=1, number=1), run(rid=2, number=2, conclusion="failure")]
        self.assertEqual(decide(runs, {1: good_jobs("ci.yml")})["action"], "dispatch")
        runs = [run(rid=1, number=1), run(rid=2, number=2, conclusion="cancelled")]
        self.assertEqual(decide(runs, {1: good_jobs("ci.yml")})["action"], "dispatch")

    def test_later_green_supersedes_older_failure(self):
        runs = [run(rid=1, number=1, conclusion="failure"), run(rid=2, number=2)]
        r = decide(runs, {2: good_jobs("ci.yml")})
        self.assertEqual((r["action"], r["run_url"]), ("reuse", "https://example/runs/2"))

    def test_pagination_finds_old_page_runs(self):
        noise = [run(rid=100 + i, number=100 + i, sha=OTHER) for i in range(d.PER_PAGE)]
        api = FakeApi(runs={"ci.yml": noise + [run(rid=1, number=1)]}, jobs={1: good_jobs("ci.yml")})
        self.assertEqual(d.select_suite(api, REPO, "main", SHA, "ci.yml")["action"], "reuse")

    def test_api_failure_is_an_error_not_a_hit(self):
        for fail in ("/runs?", "/jobs"):
            api = FakeApi(runs={"ci.yml": [run()]}, jobs={1: good_jobs("ci.yml")}, fail=fail)
            with self.assertRaises(d.SelectorError):
                d.select_suite(api, REPO, "main", SHA, "ci.yml")

    def test_malformed_response_is_an_error(self):
        class Bad(FakeApi):
            def get(self, path):
                return {"unexpected": 1}
        with self.assertRaises(d.SelectorError):
            d.select_suite(Bad(), REPO, "main", SHA, "ci.yml")

    def test_missing_token_is_an_error(self):
        with self.assertRaises(d.SelectorError):
            d.GitHubApi("")

    def test_main_advancing_is_reselected_then_gives_up(self):
        api = FakeApi(advance=[SHA, OTHER, OTHER, OTHER], runs={})
        sha, decisions = d.select_all(api, REPO, "main", ["ci.yml"])
        self.assertEqual(sha, OTHER)
        api = FakeApi(advance=[chr(97 + i) * 40 for i in range(10)])
        with self.assertRaises(d.SelectorError):
            d.select_all(api, REPO, "main", ["ci.yml"])

    def test_run_once_dispatches_missing_and_reports_authoritative_sha(self):
        api = FakeApi(runs={"ci.yml": [run()], "tb-sim.yml": []}, jobs={1: good_jobs("ci.yml")})
        new = run("tb-sim.yml", rid=9, number=9, sha=OTHER, status="queued", conclusion=None,
                  event="workflow_dispatch")
        orig = api.get

        def get(path):
            if "event=workflow_dispatch" in path and api.posts:
                return {"workflow_runs": [new]}
            if "event=workflow_dispatch" in path:
                return {"workflow_runs": []}
            return orig(path)
        api.get = get
        _, lines = d.run_once(api, REPO, "main", False, sleep=lambda s: None)
        self.assertEqual([p for p, _ in api.posts],
                         [f"/repos/{REPO}/actions/workflows/tb-sim.yml/dispatches"])
        self.assertEqual(api.posts[0][1], {"ref": "main"})
        text = "\n".join(lines)
        self.assertIn("ci.yml: reuse", text)
        self.assertIn(f"executed sha {OTHER}", text)
        self.assertIn("differs from selected", text)

    def test_dry_run_never_dispatches(self):
        api = FakeApi(runs={"ci.yml": [], "tb-sim.yml": []})
        d.run_once(api, REPO, "main", True, sleep=lambda s: None)
        self.assertEqual(api.posts, [])


def read(name):
    return (WF / name).read_text()


STANZA = (
    "concurrency:\n"
    "  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}\n"
    "  cancel-in-progress: ${{ github.event_name == 'pull_request' }}\n"
)


def top_level_has_stanza(text):
    return re.search(r"^" + re.escape(STANZA), text, re.M) is not None


def triggers(text):
    block = re.search(r"^on:\n((?:[ \t#].*\n|\n)+)", text, re.M).group(1)
    return set(re.findall(r"^  ([a-z_]+):", block, re.M))


def pushes_to_main(text):
    return bool(re.search(r"^  push:\n(?:    .*\n)*?    branches: \[main\]", text, re.M))


def structural_problems(files):
    """files: {name: text}.  Returns defects in the scheduling policy."""
    out = []
    for name, text in files.items():
        if name == "rtl-lint-baseline.yml":
            continue
        if (pushes_to_main(text) or name in ("ci.yml", "tb-sim.yml", "ci-main-schedule.yml")) \
                and not top_level_has_stanza(text):
            out.append(f"{name}: missing top-level concurrency stanza")
        if re.search(r"cancel-in-progress:\s*true", text):
            out.append(f"{name}: unconditional cancel-in-progress")
    for name in ("ci.yml", "tb-sim.yml"):
        t = files[name]
        if pushes_to_main(t):
            out.append(f"{name}: still runs on push to main")
        if "workflow_dispatch" not in triggers(t) or "pull_request" not in triggers(t):
            out.append(f"{name}: lost PR or manual trigger")
    s = files["ci-main-schedule.yml"]
    if "cron: \"17 */3 * * *\"" not in s:
        out.append("schedule: wrong cron")
    if not re.search(r"permissions:\n  contents: read\n  actions: write\n", s):
        out.append("schedule: permissions are not exactly contents:read, actions:write")
    if "pull_request" in triggers(s) or "push" in triggers(s):
        out.append("schedule: must not trigger on push/PR")
    for name, text in files.items():
        if name != "ci-main-schedule.yml" and re.search(r"actions:\s*write", text):
            out.append(f"{name}: unexpected actions: write")
    if re.search(r"secrets\.(?!GITHUB_TOKEN)", s):
        out.append("schedule: stored token")
    return out


class WorkflowInventoryTests(unittest.TestCase):
    def files(self):
        return {p.name: p.read_text() for p in WF.glob("*.yml")}

    def test_policy_holds(self):
        self.assertEqual(structural_problems(self.files()), [])

    def test_smaller_suites_keep_push_and_pr(self):
        for name, text in self.files().items():
            if name in ("ci.yml", "tb-sim.yml", "ci-main-schedule.yml", "rtl-lint-baseline.yml"):
                continue
            self.assertTrue(pushes_to_main(text) and "pull_request" in triggers(text), name)

    def test_capabilities_refresh_keeps_guard_and_has_no_job_concurrency(self):
        t = read("capabilities.yml")
        job = t[t.index("\n  refresh:"):]
        self.assertNotIn("concurrency:", job)
        self.assertIn("github.ref == 'refs/heads/main'", job)
        self.assertIn("github.repository == '2AMLogic/gf180-torchsynth'", job)
        self.assertIn("contents: write", job)
        self.assertIn("cancel-in-progress: ${{ github.event_name == 'pull_request' }}", t)

    def test_heavy_job_inventory_matches_selector(self):
        for wf, spec in d.SUITES.items():
            text = read(wf)
            for name in spec["required_jobs"]:
                base = name.split(" (")[0]
                self.assertRegex(text, rf"(?m)^  {re.escape(base)}:\n", name)
        self.assertIn('python: ["3.11", "3.12", "3.13"]', read("ci.yml"))
        tb = read("tb-sim.yml")
        self.assertIn('python: ["3.11", "3.12", "3.13"]', tb)
        self.assertIn("needs: [oneshot-tail-chain, oneshot-whole-voice]", tb)

    def test_dispatcher_cannot_replace_simulation(self):
        s = read("ci-main-schedule.yml")
        self.assertNotIn("run_tb.py", s)
        self.assertNotIn("upload-artifact", s)

    # Negative fixtures: each guard rejects the defect it names.
    def mutated(self, name, fn):
        files = self.files()
        files[name] = fn(files[name])
        return structural_problems(files)

    def test_negative_fixtures(self):
        cases = {
            "missing stanza": ("mutations.yml", lambda t: t.replace(STANZA, "")),
            "unconditional cancel": ("mutations.yml",
                lambda t: t.replace("cancel-in-progress: ${{ github.event_name == 'pull_request' }}",
                                    "cancel-in-progress: true")),
            "push restored": ("ci.yml", lambda t: t.replace("  workflow_dispatch:\n",
                              "  push:\n    branches: [main]\n  workflow_dispatch:\n")),
            "manual removed": ("tb-sim.yml", lambda t: t.replace("  workflow_dispatch:\n", "")),
            "wrong cron": ("ci-main-schedule.yml", lambda t: t.replace("*/3", "*/1")),
            "write perms": ("ci-main-schedule.yml", lambda t: t.replace("contents: read", "contents: write")),
            "pr write": ("ci.yml", lambda t: t.replace("contents: read", "contents: read\n  actions: write")),
            "stored token": ("ci-main-schedule.yml", lambda t: t.replace("secrets.GITHUB_TOKEN", "secrets.PAT")),
            "dispatcher on push": ("ci-main-schedule.yml", lambda t: t.replace(
                "  workflow_dispatch:\n", "  workflow_dispatch:\n  pull_request:\n", 1)),
        }
        for label, (name, fn) in cases.items():
            self.assertTrue(self.mutated(name, fn), label)


if __name__ == "__main__":
    unittest.main()
