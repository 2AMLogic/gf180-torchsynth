"""CI wiring guard for the six landed RTL golden-vector lanes (issue #187).

Six landed lanes each ship a bit-exact RTL-vs-golden-vector flow behind
``tb/run_tb.py <lane>``, and each lane's unittest wrapper only runs that
flow when Icarus Verilog is present (``@unittest.skipUnless(has_iverilog(),
...)`` in ``tests/test_adsr_engine.py``, ``tests/test_patch_control.py``,
``tests/test_lfo_vca_engine.py``, ``tests/test_mod_matrix_engine.py``,
``tests/test_vco_engine.py``, and ``tests/test_square_saw_vco_engine.py``).
``.github/workflows/ci.yml`` never installs Icarus, so those wrappers only
ever run in ``.github/workflows/tb-sim.yml``.

This module does not run Icarus or any RTL itself — it is a plain text
guard against the wiring being silently dropped from
``.github/workflows/tb-sim.yml`` (AGENTS.md/CLAUDE.md: "a test that did not
run must never be reported as a pass"). It intentionally avoids depending
on a YAML parser: ``.github/workflows/ci.yml`` (which runs this module)
never installs one, and the wiring being checked is a small, line-oriented
shape, so plain-text regex checks are the more honest tool for the job.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "tb-sim.yml"

# lane -> tb/run_tb.py subcommand, matching the table in issue #187.
LANES = {
    "adsr": "adsr",  # issue #70
    "patch": "patch",  # issue #69
    "lfo": "lfo",  # issue #71
    "modmatrix": "modmatrix",  # issue #72
    "vco": "vco",  # issue #73
    "vco2": "vco2",  # issue #74
}

# GitHub Actions job-level step markers are lines of the form
# "      - name: ..." / "      - run: ..." / "      - uses: ..." at
# 6-space indent (two 2-space job/steps levels, one list-item dash). Job
# headers are 2-space-indented keys directly under "jobs:".
_STEP_MARKER = re.compile(r"\n(?=      - )")
_JOB_HEADER = re.compile(r"^  [A-Za-z0-9_-]+:\s*$", re.MULTILINE)


def _read_workflow() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _job_blocks(text: str) -> list:
    """Split the workflow's ``jobs:`` section into per-job text blocks."""
    starts = [m.start() for m in _JOB_HEADER.finditer(text)]
    starts.append(len(text))
    return [text[starts[i]:starts[i + 1]] for i in range(len(starts) - 1)]


def _step_chunks(job_block: str) -> list:
    return _STEP_MARKER.split(job_block)


def _lane_command_pattern(subcommand: str) -> re.Pattern:
    # \b on both sides so "vco" never matches inside "vco2" (and vice
    # versa): both are all-word-character tokens, so \b only lands at a
    # true token boundary here.
    return re.compile(
        r"tb/run_tb\.py(?:\s+--\S+\s+\S+)*\s+\b" + re.escape(subcommand) + r"\b"
    )


class TestSixLanesWiredIntoCi(unittest.TestCase):
    """Every landed ``tb/run_tb.py <lane>`` command must run in CI."""

    @classmethod
    def setUpClass(cls):
        cls.text = _read_workflow()
        cls.jobs = _job_blocks(cls.text)

    def test_workflow_file_exists(self):
        self.assertTrue(
            WORKFLOW_PATH.is_file(),
            "expected %s to exist" % WORKFLOW_PATH,
        )

    def test_workflow_runs_on_every_pull_request(self):
        # A lane wired into a job that never triggers on PRs would satisfy
        # a naive substring check while still never gating a PR.
        self.assertRegex(self.text, r"(?m)^on:\s*$")
        self.assertIn("pull_request:", self.text)

    def test_every_lane_command_is_present(self):
        for lane, subcommand in LANES.items():
            with self.subTest(lane=lane):
                pattern = _lane_command_pattern(subcommand)
                self.assertRegex(
                    self.text,
                    pattern,
                    "tb/run_tb.py %s is not wired into %s; a landed RTL "
                    "lane must not be silently dropped from CI (issue "
                    "#187)" % (subcommand, WORKFLOW_PATH),
                )

    def test_every_lane_step_carries_a_bounded_timeout(self):
        for lane, subcommand in LANES.items():
            with self.subTest(lane=lane):
                chunk = self._lane_step_chunk(subcommand)
                self.assertIn(
                    "timeout-minutes:",
                    chunk,
                    "the %s lane step has no timeout-minutes bound "
                    "(issue #187 AC: wall-clock per job/step must be "
                    "bounded and recorded); step text:\n%s"
                    % (subcommand, chunk),
                )

    def test_every_lane_step_can_actually_fail_the_job(self):
        for lane, subcommand in LANES.items():
            with self.subTest(lane=lane):
                chunk = self._lane_step_chunk(subcommand)
                self.assertNotRegex(
                    chunk,
                    r"continue-on-error:\s*true",
                    "the %s lane step is marked continue-on-error; a "
                    "failing RTL comparison must fail the job, not pass "
                    "silently" % subcommand,
                )

    def test_every_lane_runs_in_a_job_with_icarus_installed(self):
        for lane, subcommand in LANES.items():
            with self.subTest(lane=lane):
                job = self._lane_job_block(subcommand)
                self.assertIn(
                    "iverilog",
                    job,
                    "the %s lane's job never installs Icarus Verilog; "
                    "without it the lane's RTL comparison cannot run and "
                    "must not be reported as skipped or passed"
                    % subcommand,
                )

    def _lane_job_block(self, subcommand: str) -> str:
        pattern = _lane_command_pattern(subcommand)
        for job in self.jobs:
            if pattern.search(job):
                return job
        self.fail(
            "tb/run_tb.py %s is not wired into any job in %s"
            % (subcommand, WORKFLOW_PATH)
        )

    def _lane_step_chunk(self, subcommand: str) -> str:
        pattern = _lane_command_pattern(subcommand)
        job = self._lane_job_block(subcommand)
        for chunk in _step_chunks(job):
            if pattern.search(chunk):
                return chunk
        self.fail(
            "tb/run_tb.py %s is not wired into any step in %s"
            % (subcommand, WORKFLOW_PATH)
        )


class TestGuardFailsIfALaneIsRemoved(unittest.TestCase):
    """Deleting a lane's command line must fail this guard, not skip it."""

    def test_removing_a_lane_line_fails_the_presence_check(self):
        text = _read_workflow()
        # Simulate a regression: drop the vco2 lane's command line, as if
        # someone edited the step's ``run:`` line away.
        mutated = re.sub(
            r"\n\s*run: python3 tb/run_tb\.py vco2\s*",
            "\n",
            text,
            count=1,
        )
        self.assertNotEqual(
            mutated, text, "the vco2 lane line was not found to remove"
        )
        pattern = _lane_command_pattern("vco2")
        self.assertNotRegex(
            mutated,
            pattern,
            "the mutation did not actually remove the vco2 lane command; "
            "this guard test is not exercising the failure path it claims to",
        )


if __name__ == "__main__":
    unittest.main()
