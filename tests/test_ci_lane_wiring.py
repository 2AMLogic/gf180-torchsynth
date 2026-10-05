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

And — a job that cannot fit its platform limit is a job that cannot run to a
verdict (issue #265) — ``TestWorkflowBudgetsFitTheHostedLimit``,
``TestLaneInventoryIsPinned`` and ``TestNoFailureMasking`` below check every
job in ``tb-sim.yml``: an explicit job cap safely under GitHub-hosted's
360-minute hard limit, an explicit budget on every step (setup and artifact
steps included) summing to comfortably under that cap, the original lane
inventory with its exact commands and per-lane timeouts, and no construct
that could turn a failed, timed-out or cancelled lane into a pass. Each guard
is a pure function of the workflow text, and each has negative fixtures
proving it rejects the defect it names.
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
# ``sim-selftest`` rather than a ``sim-lanes-*`` job, so they are exempt
# from the LANES table but NOT from the "must run somewhere with Icarus" check.
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


# --- Workflow-wide budget / inventory / masking guards (issue #265) ---------
#
# GitHub-hosted runners hard-stop every job at 360 minutes. A job whose
# declared cap reaches that limit can be cancelled by the platform before its
# own budget is spent, which surfaces as an infrastructure cancellation rather
# than as a lane verdict. The pre-#265 ``sim-lanes`` job declared a 400-minute
# cap over a 370-minute step sum.
HOSTED_JOB_LIMIT_MINUTES = 360
# Every job cap must leave at least this much room under the hosted limit.
PLATFORM_HEADROOM_MINUTES = 30
# Every job's step budgets must sum to at least this much under its cap, so a
# runaway step is stopped by its own (named, attributable) step timeout and
# not by the job cap. Setup/install and artifact-upload steps count too.
STEP_HEADROOM_MINUTES = 10

# The lane inventory and per-lane allowances as they stood before the #265
# split (``sim-lanes`` on main @ b43042b: 10+10+10+60+90+120+30+30+10 = 370),
# plus the two #79 one-shot regression commands. A split, regroup or rename
# must keep every command byte-identical (same arguments) and every timeout
# unchanged; a deliberate change to either belongs in the relevant spec/
# document *and* here, never silently in the workflow alone.
LANE_STEP_TIMEOUTS = {
    "adsr": 10,  # issue #70
    "patch": 10,  # issue #69
    "lfo": 10,  # issue #71
    "modmatrix": 60,  # issue #72
    "vco": 90,  # issue #73
    "vco2": 120,  # issue #74
    "noise": 30,  # issue #75
    "mix": 30,  # issue #76
    "normreplay": 10,  # issue #77
}
# Issue #264 (a deliberate, spec-recorded change -- spec/ONESHOT-E2E.md,
# "What is still open"): both one-shot lanes now tee their transcript next to
# their evidence record so the aggregate gate can consume both. Same profile,
# same harness, same timeout; the tee is only safe under `shell: bash`
# (`-eo pipefail`), which _inventory_violations requires of a piped lane.
ONESHOT_STEP_TIMEOUTS = {
    "python3 tb/run_oneshot.py --profile regression --workdir out/tail-chain"
    " 2>&1 | tee out/tail-chain/transcript.log": 150,
    "python3 tb/run_voice.py --profile regression --workdir out/whole-voice"
    " 2>&1 | tee out/whole-voice/transcript.log": 120,
}

_TIMEOUT_LINE = re.compile(r"^timeout-minutes:\s*(\S.*?)\s*(?:#.*)?$")
_JOB_NAME_LINE = re.compile(r"^  ([A-Za-z0-9_-]+):\s*(?:#.*)?$")
# `|| true`, `|| :` and `|| exit 0` all discard a failing command's status.
_STATUS_DISCARD = re.compile(r"\|\|\s*(?:true\b|:(?=\s|$)|exit\s+0\b)")
# Expressions that let a job or step run regardless of earlier failure or
# cancellation. Harmless on an artifact upload; a verdict-masking aggregator
# anywhere else.
_RUNS_REGARDLESS = re.compile(r"\b(?:always|cancelled|failure)\s*\(")
# A job's flow-style `needs: [a, b]` (or a single `needs: a`).
_NEEDS_LINE = re.compile(r"^needs:\s*(?:\[([^\]]*)\]|([A-Za-z0-9_-]+))\s*$")


def _expected_inventory() -> dict:
    """Pinned run command -> pinned step timeout, for every gated lane."""
    inventory = {
        "python3 tb/run_tb.py %s" % lane: minutes
        for lane, minutes in LANE_STEP_TIMEOUTS.items()
    }
    inventory.update(ONESHOT_STEP_TIMEOUTS)
    return inventory


def _parse_timeout(value: str):
    return int(value) if value.isdigit() else None


def _parse_jobs(text: str) -> dict:
    """Line-oriented parse of a workflow's ``jobs:`` section.

    Returns ``{job: {"cap", "keys", "steps"}}`` where ``cap`` is the
    job-level ``timeout-minutes`` (``None`` if absent or not a literal
    integer), ``keys`` the job's other 4-space-indent lines, and ``steps`` a
    list of ``{"label", "timeout", "lines"}`` dicts. Same deliberate stdlib-only
    shape as the rest of this module (``ci.yml`` carries no YAML parser); the
    layout it relies on (2-space job keys, 4-space job fields, 6-space step
    dashes, 8-space step fields) is the layout ``tb-sim.yml`` uses, and
    ``TestWorkflowBudgetsFitTheHostedLimit`` asserts the parse found the
    expected jobs and steps so a layout drift fails loudly rather than
    parsing to nothing.
    """
    jobs, job, step, in_jobs = {}, None, None, False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            in_jobs = stripped.startswith("jobs:")
            job = step = None
            continue
        if not in_jobs:
            continue
        if indent == 2:
            match = _JOB_NAME_LINE.match(line)
            job = {"cap": None, "keys": [], "steps": []}
            jobs[match.group(1) if match else stripped] = job
            step = None
            continue
        if job is None:
            continue
        if indent == 4:
            step = None
            job["keys"].append(stripped)
            match = _TIMEOUT_LINE.match(stripped)
            if match:
                job["cap"] = _parse_timeout(match.group(1))
            continue
        if indent == 6 and stripped.startswith("- "):
            body = stripped[2:]
            step = {"label": body, "timeout": None, "lines": [body]}
            job["steps"].append(step)
            match = _TIMEOUT_LINE.match(body)
            if match:
                step["timeout"] = _parse_timeout(match.group(1))
            continue
        if step is None:
            # Nested under a job-level key (strategy/matrix, env, ...).
            job["keys"].append(stripped)
            continue
        step["lines"].append(stripped)
        if indent == 8:
            if stripped.startswith("name:"):
                step["label"] = stripped
            match = _TIMEOUT_LINE.match(stripped)
            if match:
                step["timeout"] = _parse_timeout(match.group(1))
    return jobs


def _step_run(step: dict):
    """The step's single-line ``run:`` command, else ``None``."""
    for line in step["lines"]:
        if line.startswith("run:"):
            command = line[len("run:"):].strip()
            return None if command in ("|", ">", "|-", ">-") else command
    return None


def _step_field(step: dict, key: str):
    for line in step["lines"]:
        if line.startswith(key + ":"):
            return line[len(key) + 1:].strip()
    return None


def _budget_violations(text: str) -> list:
    """Every way a job's declared budget fails to fit the hosted limit."""
    violations = []
    jobs = _parse_jobs(text)
    if not jobs:
        violations.append("no jobs parsed from the workflow")
    for name, job in jobs.items():
        cap = job["cap"]
        if cap is None:
            violations.append(
                "%s: no explicit integer job-level timeout-minutes (the "
                "default is the platform's own 360-minute limit)" % name
            )
        elif cap >= HOSTED_JOB_LIMIT_MINUTES:
            violations.append(
                "%s: job cap %d >= GitHub-hosted's %d-minute hard job limit"
                % (name, cap, HOSTED_JOB_LIMIT_MINUTES)
            )
        elif cap > HOSTED_JOB_LIMIT_MINUTES - PLATFORM_HEADROOM_MINUTES:
            violations.append(
                "%s: job cap %d leaves under %d minutes of headroom below "
                "the %d-minute hosted limit"
                % (name, cap, PLATFORM_HEADROOM_MINUTES,
                   HOSTED_JOB_LIMIT_MINUTES)
            )
        if not job["steps"]:
            violations.append("%s: no steps parsed" % name)
        for step in job["steps"]:
            if step["timeout"] is None:
                violations.append(
                    "%s: step %r has no explicit integer timeout-minutes"
                    % (name, step["label"])
                )
        total = sum(s["timeout"] for s in job["steps"] if s["timeout"])
        if cap is not None and total + STEP_HEADROOM_MINUTES > cap:
            violations.append(
                "%s: step budgets sum to %d, which leaves under %d minutes "
                "of headroom below the %d-minute job cap"
                % (name, total, STEP_HEADROOM_MINUTES, cap)
            )
    return violations


def _inventory_violations(text: str) -> list:
    """Every pinned lane must run exactly once, unchanged, unconditionally."""
    violations = []
    found = {}
    for name, job in _parse_jobs(text).items():
        for step in job["steps"]:
            command = _step_run(step)
            if command in _expected_inventory():
                found.setdefault(command, []).append((name, job, step))
    for command, minutes in _expected_inventory().items():
        hits = found.get(command, [])
        if not hits:
            violations.append(
                "%r is not run by any step (a lane must not be silently "
                "dropped or have its arguments changed)" % command
            )
            continue
        if len(hits) > 1:
            violations.append(
                "%r is run by %d steps; each lane has exactly one gating "
                "step" % (command, len(hits))
            )
        for name, job, step in hits:
            if step["timeout"] != minutes:
                violations.append(
                    "%s: %r has timeout-minutes %r, pinned at %d (a lane's "
                    "allowance must not change silently)"
                    % (name, command, step["timeout"], minutes)
                )
            if _step_field(step, "if") is not None:
                violations.append(
                    "%s: %r is conditional; a skipped lane step reports as "
                    "success" % (name, command)
                )
            if any(key.startswith("if:") for key in job["keys"]):
                violations.append(
                    "%s: the job running %r is conditional; a skipped job "
                    "is not a lane verdict" % (name, command)
                )
            if "|" in command and _step_field(step, "shell") != "bash":
                # The default `run` shell is `bash -e` WITHOUT pipefail, so
                # `harness | tee log` would report tee's status, not the
                # harness's: a failing lane would pass. Only an explicit
                # `shell: bash` (`bash --noprofile --norc -eo pipefail`)
                # keeps the harness's failure.
                violations.append(
                    "%s: %r is piped without `shell: bash`; the default "
                    "shell has no pipefail, so the pipe discards the lane's "
                    "exit status" % (name, command)
                )
            if not any("iverilog" in line for s in job["steps"]
                       for line in s["lines"]):
                violations.append(
                    "%s: the job running %r never installs Icarus Verilog"
                    % (name, command)
                )
    return violations


def _masking_violations(text: str) -> list:
    """Constructs that can turn a failed or cancelled lane into a pass."""
    violations = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        code = line.split("#", 1)[0] if line.lstrip().startswith("#") else line
        if re.search(r"continue-on-error:\s*(?!false\b)\S", code):
            violations.append("line %d: continue-on-error" % lineno)
        if _STATUS_DISCARD.search(code):
            violations.append(
                "line %d: a command's failure status is discarded" % lineno
            )
    for name, job in _parse_jobs(text).items():
        for key in job["keys"]:
            if (key.startswith("if:") and _RUNS_REGARDLESS.search(key)
                    and not _forwards_every_producer_result(job)):
                violations.append(
                    "%s: job-level %r runs regardless of failed/cancelled "
                    "producers (an aggregation that can accept missing "
                    "results)" % (name, key)
                )
        for step in job["steps"]:
            condition = _step_field(step, "if")
            uses = _step_field(step, "uses") or ""
            if (condition and _RUNS_REGARDLESS.search(condition)
                    and not uses.startswith("actions/upload-artifact@")):
                violations.append(
                    "%s: step %r runs regardless of earlier failure; only "
                    "evidence upload may" % (name, step["label"])
                )
    return violations


def _job_needs(job: dict):
    """The job's ``needs`` list, ``[]`` if none, ``None`` if unparseable."""
    for key in job["keys"]:
        if key.startswith("needs:"):
            match = _NEEDS_LINE.match(key)
            if not match:
                return None
            names = match.group(1) if match.group(1) is not None else (
                match.group(2))
            return [n.strip() for n in names.split(",") if n.strip()]
    return []


def _forwards_every_producer_result(job: dict) -> bool:
    """Issue #264's one admitted job-level ``always()``.

    An aggregation job may run regardless of its producers' outcome -- so a
    failed or cancelled producer cannot turn it into a *skipped* (and so
    possibly green) required check -- only if it hands EVERY producer's
    result to a gate that fails closed on anything but ``success``:
    ``--producer-result <job>=${{ needs.<job>.result }}`` for each job it
    ``needs``, in a step it runs unconditionally. A job with no (or an
    unparseable) ``needs`` has no producer results to forward and is never
    admitted; nor is one that drops a single forwarding.
    """
    needs = _job_needs(job)
    if not needs:
        return False
    for producer in needs:
        forwarding = re.compile(
            r"--producer-result\s+%s=\$\{\{\s*needs\.%s\.result\s*\}\}"
            % (re.escape(producer), re.escape(producer))
        )
        if not any(
            _step_field(step, "if") is None
            and forwarding.search("\n".join(step["lines"]))
            for step in job["steps"]
        ):
            return False
    return True


def _mutate(test: unittest.TestCase, text: str, old: str, new: str) -> str:
    test.assertEqual(
        text.count(old), 1,
        "fixture anchor %r must occur exactly once in tb-sim.yml; this "
        "negative fixture is not exercising the path it claims to" % old,
    )
    return text.replace(old, new)


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
        # lookbehind would make this guard fail the `sim-lanes-*` jobs on
        # lines that compile fine.
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


class TestWorkflowBudgetsFitTheHostedLimit(unittest.TestCase):
    """Every tb-sim.yml job fits GitHub-hosted's 360-minute limit (#265)."""

    EXPECTED_JOBS = {
        "sim-selftest",
        "sim-lanes-vco",
        "sim-lanes-engines",
        "oneshot-tail-chain",
        "oneshot-whole-voice",
        "rtl-module-qualification",
    }

    @classmethod
    def setUpClass(cls):
        cls.text = _read_workflow()

    def test_parse_sees_every_job_and_their_steps(self):
        # The guard must not pass vacuously on a layout it cannot read.
        jobs = _parse_jobs(self.text)
        self.assertEqual(set(jobs), self.EXPECTED_JOBS)
        for name, job in jobs.items():
            with self.subTest(job=name):
                self.assertGreaterEqual(len(job["steps"]), 3)
        self.assertEqual(len(jobs["sim-lanes-vco"]["steps"]), 3 + 2)
        self.assertEqual(len(jobs["sim-lanes-engines"]["steps"]), 3 + 7)

    def test_every_job_and_step_budget_fits(self):
        self.assertEqual(_budget_violations(self.text), [])

    def test_no_job_is_named_like_the_unsplit_sim_lanes(self):
        # The over-limit job is gone, not merely re-capped.
        self.assertNotIn("sim-lanes", _parse_jobs(self.text))

    # --- negative fixtures -------------------------------------------------

    def test_rejects_a_cap_at_or_over_the_hosted_limit(self):
        for cap in ("360", "400"):
            with self.subTest(cap=cap):
                mutated = _mutate(
                    self, self.text,
                    "    timeout-minutes: 250\n",
                    "    timeout-minutes: %s\n" % cap,
                )
                self.assertTrue(any(
                    "sim-lanes-vco: job cap %s >=" % cap in v
                    for v in _budget_violations(mutated)
                ), _budget_violations(mutated))

    def test_rejects_a_cap_without_platform_headroom(self):
        mutated = _mutate(
            self, self.text,
            "    timeout-minutes: 250\n", "    timeout-minutes: 345\n",
        )
        self.assertTrue(any(
            "sim-lanes-vco: job cap 345 leaves under" in v
            for v in _budget_violations(mutated)
        ), _budget_violations(mutated))

    def test_rejects_an_absent_job_cap(self):
        mutated = _mutate(self, self.text, "    timeout-minutes: 250\n", "")
        self.assertTrue(any(
            v.startswith("sim-lanes-vco: no explicit integer job-level")
            for v in _budget_violations(mutated)
        ), _budget_violations(mutated))

    def test_rejects_a_non_literal_job_cap(self):
        mutated = _mutate(
            self, self.text,
            "    timeout-minutes: 250\n",
            "    timeout-minutes: ${{ inputs.cap }}\n",
        )
        self.assertTrue(any(
            v.startswith("sim-lanes-vco: no explicit integer job-level")
            for v in _budget_violations(mutated)
        ), _budget_violations(mutated))

    def test_rejects_step_totals_that_consume_the_job_cap(self):
        # sim-lanes-vco's steps sum to 230: a cap equal to the sum, and one
        # inside the required headroom, must both fail.
        for cap in ("230", "235"):
            with self.subTest(cap=cap):
                mutated = _mutate(
                    self, self.text,
                    "    timeout-minutes: 250\n",
                    "    timeout-minutes: %s\n" % cap,
                )
                self.assertIn(
                    "sim-lanes-vco: step budgets sum to 230, which leaves "
                    "under %d minutes of headroom below the %s-minute job "
                    "cap" % (STEP_HEADROOM_MINUTES, cap),
                    _budget_violations(mutated),
                )

    def test_rejects_the_pre_split_sim_lanes_shape(self):
        # One job carrying all nine lanes at the original 400-minute cap.
        mutated = _mutate(
            self, self.text,
            "    timeout-minutes: 200\n", "    timeout-minutes: 400\n",
        )
        self.assertTrue(any(
            "sim-lanes-engines: job cap 400 >=" in v
            for v in _budget_violations(mutated)
        ))

    def test_rejects_a_setup_step_without_a_budget(self):
        # The guard covers checkout/setup/install, not only lane steps.
        mutated = _mutate(
            self, self.text,
            "      - name: Install Icarus Verilog + Verilator (PDK-free)\n"
            "        timeout-minutes: 10\n",
            "      - name: Install Icarus Verilog + Verilator (PDK-free)\n",
        )
        self.assertTrue(any(
            v.startswith("rtl-module-qualification: step 'name: Install")
            and "no explicit integer timeout-minutes" in v
            for v in _budget_violations(mutated)
        ), _budget_violations(mutated))

    def test_rejects_an_artifact_step_without_a_budget(self):
        mutated = _mutate(
            self, self.text,
            "      - name: Upload qualification evidence\n"
            "        if: always()\n"
            "        timeout-minutes: 5\n",
            "      - name: Upload qualification evidence\n"
            "        if: always()\n",
        )
        self.assertTrue(any(
            "Upload qualification evidence" in v
            for v in _budget_violations(mutated)
        ))


class TestLaneInventoryIsPinned(unittest.TestCase):
    """The #265 split must not drop a lane or shrink its allowance."""

    @classmethod
    def setUpClass(cls):
        cls.text = _read_workflow()

    def test_pinned_lanes_match_the_lane_registry(self):
        self.assertEqual(set(LANE_STEP_TIMEOUTS), set(LANES))

    def test_pinned_lane_budgets_are_the_original_370_minutes(self):
        self.assertEqual(sum(LANE_STEP_TIMEOUTS.values()), 370)

    def test_every_lane_runs_once_unchanged_and_unconditionally(self):
        self.assertEqual(_inventory_violations(self.text), [])

    def test_vco_group_and_engine_group_split(self):
        jobs = _parse_jobs(self.text)

        def lanes_in(job):
            return sorted(
                _step_run(s) for s in jobs[job]["steps"]
                if (_step_run(s) or "").startswith("python3 tb/run_tb.py ")
            )

        self.assertEqual(
            lanes_in("sim-lanes-vco"),
            ["python3 tb/run_tb.py vco", "python3 tb/run_tb.py vco2"],
        )
        self.assertEqual(
            lanes_in("sim-lanes-engines"),
            sorted("python3 tb/run_tb.py %s" % lane for lane in (
                "adsr", "patch", "lfo", "modmatrix", "noise", "mix",
                "normreplay",
            )),
        )

    # --- negative fixtures -------------------------------------------------

    def test_rejects_a_missing_lane(self):
        mutated = _mutate(
            self, self.text,
            "        run: python3 tb/run_tb.py vco2\n", "",
        )
        self.assertIn(
            "'python3 tb/run_tb.py vco2' is not run by any step (a lane must "
            "not be silently dropped or have its arguments changed)",
            _inventory_violations(mutated),
        )

    def test_rejects_a_missing_oneshot_lane(self):
        mutated = _mutate(
            self, self.text,
            "        run: python3 tb/run_oneshot.py --profile regression "
            "--workdir out/tail-chain 2>&1 | tee out/tail-chain/transcript.log"
            "\n",
            "",
        )
        self.assertTrue(any(
            "run_oneshot.py --profile regression --workdir out/tail-chain "
            "2>&1 | tee out/tail-chain/transcript.log' is not run" in v
            for v in _inventory_violations(mutated)
        ))

    def test_rejects_changed_lane_arguments(self):
        mutated = _mutate(
            self, self.text,
            "run: python3 tb/run_voice.py --profile regression "
            "--workdir out/whole-voice 2>&1",
            "run: python3 tb/run_voice.py --profile directed "
            "--workdir out/whole-voice 2>&1",
        )
        self.assertTrue(any(
            "run_voice.py --profile regression --workdir out/whole-voice "
            "2>&1 | tee out/whole-voice/transcript.log' is not run" in v
            for v in _inventory_violations(mutated)
        ))

    def test_rejects_a_reduced_lane_allowance(self):
        mutated = _mutate(
            self, self.text,
            "        timeout-minutes: 120\n"
            "        run: python3 tb/run_tb.py vco2\n",
            "        timeout-minutes: 100\n"
            "        run: python3 tb/run_tb.py vco2\n",
        )
        self.assertIn(
            "sim-lanes-vco: 'python3 tb/run_tb.py vco2' has timeout-minutes "
            "100, pinned at 120 (a lane's allowance must not change "
            "silently)",
            _inventory_violations(mutated),
        )

    def test_rejects_a_duplicated_lane(self):
        mutated = _mutate(
            self, self.text,
            "        run: python3 tb/run_tb.py normreplay\n",
            "        run: python3 tb/run_tb.py normreplay\n"
            "      - name: duplicate\n"
            "        timeout-minutes: 10\n"
            "        run: python3 tb/run_tb.py normreplay\n",
        )
        self.assertTrue(any(
            "is run by 2 steps" in v for v in _inventory_violations(mutated)
        ))

    def test_rejects_a_piped_lane_without_pipefail(self):
        # Issue #264's tee: without `shell: bash` the default shell has no
        # pipefail, so tee's success would mask a failing harness.
        for lane in ("tail-chain", "whole-voice"):
            with self.subTest(lane=lane):
                anchor = (
                    "        shell: bash\n"
                    "        run: python3 tb/run_%s.py --profile regression "
                    "--workdir out/%s" % (
                        "oneshot" if lane == "tail-chain" else "voice", lane)
                )
                mutated = _mutate(
                    self, self.text, anchor,
                    anchor.replace("        shell: bash\n", ""),
                )
                self.assertTrue(any(
                    "--workdir out/%s" % lane in v
                    and "piped without `shell: bash`" in v
                    for v in _inventory_violations(mutated)
                ), _inventory_violations(mutated))

    def test_rejects_a_conditional_lane_job(self):
        mutated = _mutate(
            self, self.text,
            "  sim-lanes-vco:\n",
            "  sim-lanes-vco:\n    if: github.event_name == 'push'\n",
        )
        self.assertTrue(any(
            "sim-lanes-vco: the job running" in v and "is conditional" in v
            for v in _inventory_violations(mutated)
        ))


class TestNoFailureMasking(unittest.TestCase):
    """No construct may read a failed/timed-out/cancelled lane as a pass."""

    @classmethod
    def setUpClass(cls):
        cls.text = _read_workflow()

    def test_workflow_has_no_masking(self):
        self.assertEqual(_masking_violations(self.text), [])

    # --- negative fixtures -------------------------------------------------

    def test_rejects_continue_on_error(self):
        mutated = _mutate(
            self, self.text,
            "        timeout-minutes: 90\n",
            "        timeout-minutes: 90\n        continue-on-error: true\n",
        )
        self.assertTrue(any(
            "continue-on-error" in v for v in _masking_violations(mutated)
        ))

    def test_rejects_job_level_continue_on_error(self):
        mutated = _mutate(
            self, self.text,
            "    timeout-minutes: 200\n",
            "    timeout-minutes: 200\n    continue-on-error: true\n",
        )
        self.assertTrue(any(
            "continue-on-error" in v for v in _masking_violations(mutated)
        ))

    def test_rejects_a_discarded_exit_status(self):
        for suffix in (" || true", " || :", " || exit 0"):
            with self.subTest(suffix=suffix):
                mutated = _mutate(
                    self, self.text,
                    "run: python3 tb/run_tb.py modmatrix\n",
                    "run: python3 tb/run_tb.py modmatrix%s\n" % suffix,
                )
                self.assertTrue(any(
                    "failure status is discarded" in v
                    for v in _masking_violations(mutated)
                ))

    def test_rejects_an_aggregator_that_accepts_cancelled_producers(self):
        mutated = self.text + (
            "\n  lanes-summary:\n"
            "    needs: [sim-lanes-vco, sim-lanes-engines]\n"
            "    if: always()\n"
            "    runs-on: ubuntu-24.04\n"
            "    timeout-minutes: 5\n"
            "    steps:\n"
            "      - run: echo ok\n"
            "        timeout-minutes: 1\n"
        )
        self.assertTrue(any(
            v.startswith("lanes-summary: job-level 'if: always()'")
            for v in _masking_violations(mutated)
        ))

    def test_admits_the_aggregate_gate_only_with_every_result_forwarded(self):
        # Issue #264: rtl-module-qualification is `if: always()` over its
        # two producers and forwards both results to the fail-closed gate.
        jobs = _parse_jobs(self.text)
        job = jobs["rtl-module-qualification"]
        self.assertIn("if: always()", job["keys"])
        self.assertEqual(
            _job_needs(job), ["oneshot-tail-chain", "oneshot-whole-voice"])
        self.assertTrue(_forwards_every_producer_result(job))

    def test_rejects_the_aggregate_gate_dropping_a_forwarded_result(self):
        for producer in ("oneshot-tail-chain", "oneshot-whole-voice"):
            with self.subTest(producer=producer):
                line = (
                    "            --producer-result %s=${{ needs.%s.result }}"
                    " \\\n" % (producer, producer)
                )
                mutated = _mutate(self, self.text, line, "")
                self.assertTrue(any(
                    v.startswith(
                        "rtl-module-qualification: job-level 'if: always()'")
                    for v in _masking_violations(mutated)
                ), _masking_violations(mutated))

    def test_rejects_the_aggregate_gate_forwarding_a_constant(self):
        # A hard-coded `=success` is not a forwarded producer result.
        mutated = _mutate(
            self, self.text,
            "oneshot-whole-voice=${{ needs.oneshot-whole-voice.result }}",
            "oneshot-whole-voice=success",
        )
        self.assertTrue(any(
            v.startswith("rtl-module-qualification: job-level 'if: always()'")
            for v in _masking_violations(mutated)
        ))

    def test_rejects_the_aggregate_gate_with_an_unlisted_producer(self):
        # A new producer added to `needs` must be forwarded too.
        mutated = _mutate(
            self, self.text,
            "    needs: [oneshot-tail-chain, oneshot-whole-voice]\n",
            "    needs: [oneshot-tail-chain, oneshot-whole-voice, "
            "sim-lanes-vco]\n",
        )
        self.assertTrue(any(
            v.startswith("rtl-module-qualification: job-level 'if: always()'")
            for v in _masking_violations(mutated)
        ))

    def test_rejects_the_aggregate_gate_on_a_conditional_step(self):
        # The forwarding must sit in a step that runs unconditionally.
        mutated = _mutate(
            self, self.text,
            "        timeout-minutes: 25\n",
            "        if: github.event_name == 'push'\n"
            "        timeout-minutes: 25\n",
        )
        self.assertTrue(any(
            v.startswith("rtl-module-qualification: job-level 'if: always()'")
            for v in _masking_violations(mutated)
        ))

    def test_rejects_a_lane_step_that_runs_regardless(self):
        mutated = _mutate(
            self, self.text,
            "        timeout-minutes: 60\n"
            "        run: python3 tb/run_tb.py modmatrix\n",
            "        if: ${{ !cancelled() }}\n"
            "        timeout-minutes: 60\n"
            "        run: python3 tb/run_tb.py modmatrix\n",
        )
        self.assertTrue(any(
            "runs regardless of earlier failure" in v
            for v in _masking_violations(mutated)
        ))

    def test_artifact_upload_may_run_always(self):
        # Pins the one allowed exception, so the guard above is not
        # "fixed" by forbidding evidence upload after a failure.
        self.assertIn("if: always()", self.text)
        self.assertEqual(_masking_violations(self.text), [])


if __name__ == "__main__":
    unittest.main()
