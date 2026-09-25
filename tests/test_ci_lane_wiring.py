"""CI wiring guard for the landed RTL golden-vector lanes (issue #187).

Each landed lane ships a bit-exact RTL-vs-golden-vector flow behind
``tb/run_tb.py <lane>``, and each lane's unittest wrapper only runs that
flow when Icarus Verilog is present (``@unittest.skipUnless(has_iverilog(),
...)`` in ``tests/test_adsr_engine.py``, ``tests/test_patch_control.py``,
``tests/test_lfo_vca_engine.py``, ``tests/test_mod_matrix_engine.py``,
``tests/test_vco_engine.py``, ``tests/test_square_saw_vco_engine.py``,
``tests/test_audio_mix_engine.py``, and
``tests/test_normalization_replay_engine.py``; ``tests/test_noise_stream.py``
has no gated wrapper at all — it states outright that the RTL "is exercised
by ``tb/run_tb.py noise`` (Icarus; runs in CI ...)", which is only true if
this workflow runs it). ``.github/workflows/ci.yml`` never installs Icarus,
so those wrappers only ever run in ``.github/workflows/tb-sim.yml``.

``LANES`` below is the load-bearing list: a lane that lands in
``tb/run_tb.py`` without being added here *and* to ``tb-sim.yml`` keeps
reporting as ``skipped`` on every PR, which is the exact defect this module
exists to prevent. ``mix``/``normreplay``/``noise`` (issues #76/#77/#75)
landed after issue #187 was scoped and are included for that reason.

This module does not run Icarus or any RTL itself — it is a plain text
guard against the wiring being silently dropped from
``.github/workflows/tb-sim.yml`` (AGENTS.md/CLAUDE.md: "a test that did not
run must never be reported as a pass"). It intentionally avoids depending
on a YAML parser: ``.github/workflows/ci.yml`` (which runs this module)
never installs one, and the wiring being checked is a small, line-oriented
shape, so plain-text regex checks are the more honest tool for the job.

For the same reason — a lane that cannot even compile on CI is a lane that
never runs — ``TestLanesCompileUnderTheIcarusCiInstalls`` below also guards
``tb/sv/`` against the SystemVerilog loop-control statements the Icarus
version ``tb-sim.yml`` installs cannot build.
"""

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "tb-sim.yml"
RUN_TB_PATH = ROOT / "tb" / "run_tb.py"

# Lanes that are not RTL golden-vector comparisons: ``selftest`` proves the
# harness and ``anchor`` runs the format-true DUT. Both are wired into
# ``sim-selftest`` rather than ``sim-lanes``, so they are exempt from the
# LANES table but NOT from the "must run somewhere with Icarus" check.
NON_RTL_COMMANDS = frozenset({"selftest", "anchor"})

# lane -> tb/run_tb.py subcommand, matching the table in issue #187.
LANES = {
    "adsr": "adsr",  # issue #70
    "patch": "patch",  # issue #69
    "lfo": "lfo",  # issue #71
    "modmatrix": "modmatrix",  # issue #72
    "vco": "vco",  # issue #73
    "vco2": "vco2",  # issue #74
    "noise": "noise",  # issue #75
    "mix": "mix",  # issue #76
    "normreplay": "normreplay",  # issue #77
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


def _run_tb_commands() -> list:
    """Every ``tb/run_tb.py <command>`` argparse choice.

    Read with ``ast`` rather than by importing ``tb/run_tb.py``: this module
    runs in ``ci.yml``, which has neither Icarus nor the runner's simulation
    dependencies, and the file is the lane registry regardless of whether it
    is importable here.
    """
    tree = ast.parse(RUN_TB_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "command"):
            continue
        for keyword in node.keywords:
            if keyword.arg == "choices":
                return [ast.literal_eval(e) for e in keyword.value.elts]
    raise AssertionError(
        "could not find the `command` positional's choices= list in %s; "
        "this guard cannot tell which lanes exist" % RUN_TB_PATH
    )


def _lane_command_pattern(subcommand: str) -> re.Pattern:
    # \b on both sides so "vco" never matches inside "vco2" (and vice
    # versa): both are all-word-character tokens, so \b only lands at a
    # true token boundary here.
    return re.compile(
        r"tb/run_tb\.py(?:\s+--\S+\s+\S+)*\s+\b" + re.escape(subcommand) + r"\b"
    )


class TestEveryRunTbCommandIsWired(unittest.TestCase):
    """The lane registry, not a hand-kept list, decides what CI must run.

    ``LANES`` above is hand-maintained, so on its own it can only catch a
    lane being *removed* from the workflow — never a lane being *added* to
    ``tb/run_tb.py`` and never wired up. That is how ``mix``/``normreplay``
    (#76/#77) came to sit unwired and silently ``skipped`` while a guard
    test reported green. These two tests close the loop by deriving the
    expected set from ``tb/run_tb.py``'s own argparse choices.
    """

    def test_every_run_tb_command_runs_in_a_job_with_icarus(self):
        commands = _run_tb_commands()
        self.assertIn(
            "adsr",
            commands,
            "the choices= list was parsed but looks wrong (%r); this guard "
            "must not silently pass on an empty set" % (commands,),
        )
        jobs = _job_blocks(_read_workflow())
        for command in commands:
            with self.subTest(command=command):
                pattern = _lane_command_pattern(command)
                wired = [
                    job for job in jobs
                    if pattern.search(job) and "iverilog" in job
                ]
                self.assertTrue(
                    wired,
                    "tb/run_tb.py %s exists as a lane but is not run by any "
                    "Icarus-installing job in %s; its Icarus-gated tests "
                    "will keep reporting as `skipped` on every PR (issue "
                    "#187: a test that did not run must never be reported "
                    "as a pass)" % (command, WORKFLOW_PATH),
                )

    def test_lanes_table_lists_every_rtl_lane(self):
        expected = set(_run_tb_commands()) - set(NON_RTL_COMMANDS)
        self.assertEqual(
            expected - set(LANES),
            set(),
            "tb/run_tb.py has RTL lane(s) missing from this module's LANES "
            "table, so the per-step timeout / continue-on-error / "
            "Icarus-presence checks below never run for them",
        )
        self.assertEqual(
            set(LANES) - expected,
            set(),
            "this module's LANES table names lane(s) tb/run_tb.py does not "
            "have; the guard is asserting against a stale lane list",
        )


class TestEveryLaneWiredIntoCi(unittest.TestCase):
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


class TestLanesCompileUnderTheIcarusCiInstalls(unittest.TestCase):
    """The lanes must be buildable by the Icarus ``apt-get`` actually gives CI.

    ``tb-sim.yml`` installs Icarus with a bare
    ``apt-get install -y iverilog``, which on the ``ubuntu-2404`` runner is
    Icarus 12.0. Icarus 12 rejects the SystemVerilog loop-control
    statements ``break;``/``continue;`` outright
    ("sorry: break statements not supported") at *compile* time, so a
    single such statement anywhere in a lane's sources aborts that lane's
    step — and, because no lane step is ``continue-on-error``, every later
    lane in the job never runs at all. That is exactly how the ``patch``
    lane failed when issue #187 first wired these lanes in: a ``break;``
    in ``tb/sv/tb_patch_control.sv`` compiled fine under the locally
    installed Icarus 13 and not at all on CI.

    This is a text guard, not a compile: it runs in ``ci.yml``, which has
    no Icarus at all. It keeps the construct from being reintroduced
    without waiting on a multi-hour ``tb-sim.yml`` run to discover it.
    """

    # `break` / `continue` as whole-word statements. Identifiers containing
    # the words are not matched: a prefix form (`break_flag`) fails the
    # trailing `\s*;`, and a suffix form (`do_break;`) fails the `(?<![\w$])`
    # lookbehind. Prose inside a `//` comment is handled by
    # ``_offending_keyword`` below, not by this pattern.
    _UNSUPPORTED_STMT = re.compile(r"(?<![\w$])(break|continue)\s*;")

    @classmethod
    def _offending_keyword(cls, line: str):
        """Return the keyword ``line`` would trip the guard on, else ``None``.

        This is the guard's entire per-line decision, and both the scan below
        and the self-checks below it go through it. That sharing is the point
        (issue #206): the `//` stripping lives here rather than in
        ``_UNSUPPORTED_STMT``, so an ``assertNotRegex`` against the compiled
        pattern alone cannot reach it — only a test calling this helper
        exercises the same code path the scan actually uses.
        """
        code = line.split("//", 1)[0]
        match = cls._UNSUPPORTED_STMT.search(code)
        return match.group(1) if match else None

    def test_no_testbench_source_uses_break_or_continue(self):
        sv_dir = ROOT / "tb" / "sv"
        sources = sorted(sv_dir.glob("*.sv")) + sorted(sv_dir.glob("*.svh"))
        self.assertTrue(
            sources, "no SystemVerilog sources found under %s" % sv_dir
        )
        for source in sources:
            with self.subTest(source=source.name):
                offenders = []
                for lineno, line in enumerate(
                    source.read_text(encoding="utf-8").splitlines(), start=1
                ):
                    keyword = self._offending_keyword(line)
                    if keyword:
                        offenders.append((lineno, keyword))
                self.assertEqual(
                    offenders,
                    [],
                    "%s uses loop-control statement(s) Icarus 12 (the "
                    "version tb-sim.yml's apt-get installs) cannot compile: "
                    "%s. Restructure the loop with a sentinel flag instead "
                    "(see tb_patch_control.sv's stimulus read loop)."
                    % (
                        source.name,
                        ", ".join(
                            "line %d: %s;" % (n, kw) for n, kw in offenders
                        ),
                    ),
                )

    def test_guard_detects_a_reintroduced_break(self):
        # The guard must actually fire on the construct it claims to catch,
        # and must not fire on ordinary code or on an identifier that merely
        # contains the word.
        self.assertEqual(self._offending_keyword("            break;"),
                         "break")
        self.assertEqual(self._offending_keyword("        continue ;"),
                         "continue")
        self.assertIsNone(self._offending_keyword("reg break_flag;"))
        self.assertIsNone(
            self._offending_keyword("if (done) stim_done = 1'b1;")
        )

    def test_guard_ignores_identifiers_that_end_in_break_or_continue(self):
        # Pins the `(?<![\w$])` lookbehind specifically (issue #206). A
        # *prefix* identifier like `break_flag` is rejected by the trailing
        # `\s*;` alone, so it stays clean even with the lookbehind deleted;
        # only a suffix form distinguishes the two patterns. Dropping the
        # lookbehind would make this guard fail `sim-lanes` on lines that
        # compile fine.
        self.assertIsNone(self._offending_keyword("x = do_break;"))
        self.assertIsNone(self._offending_keyword("assign w = sig_continue;"))
        # `$` is legal inside (though not at the start of) a SystemVerilog
        # simple identifier, which is why the lookbehind excludes it too.
        self.assertIsNone(self._offending_keyword("assign y = tmp$continue;"))

    def test_guard_ignores_loop_control_words_inside_comments(self):
        # Pins the `line.split("//", 1)[0]` stripping in
        # ``_offending_keyword`` (issue #206). This lives outside
        # ``_UNSUPPORTED_STMT``, so it is only reachable by going through the
        # helper — an assertion against the compiled pattern cannot cover it,
        # and no comment under tb/sv/ trips it today, so the clean-tree scan
        # cannot either.
        self.assertIsNone(
            self._offending_keyword("// we break; out of the loop here")
        )
        self.assertIsNone(
            self._offending_keyword("    stim_done = 1'b1;  // then break;")
        )
        # ...but stripping must only drop the comment: real code preceding
        # one still has to fire, or the guard could be "fixed" by ignoring
        # every line that happens to contain a `//`.
        self.assertEqual(
            self._offending_keyword("            break;  // exit the loop"),
            "break",
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
