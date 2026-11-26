"""Issue #82: the committed gf180mcu synthesis-feasibility record must stay honest.

No synthesis tool, PDK or simulator is invoked here. This suite reads the
already-produced ``asic/evidence/oneshot-synth-feasibility-v1.json`` and
checks the properties that make it citeable as *synthesis feasibility* and
nothing stronger:

* the record is labelled and scoped as synthesis feasibility, with every
  layout / signoff / silicon / hardware-playback / sound-fidelity claim
  false;
* the RTL and derived synthesis-view hashes it cites are fresh against the
  tree (an edited engine must be re-synthesized, not inherit the record);
* the constraint-completeness rule: ``timing_verdict`` can only return
  ``met`` for a constrained, complete, non-negative-slack report at every
  required corner, and every stored verdict equals the verdict recomputed
  from the stored per-corner reports (so a hand-edited ``met`` fails);
* a target that did not finish carries no metrics and cannot claim timing;
* the Yosys budget travels in each request's public ``yosys_timeout_s`` field,
  an unsupported budget is refused before anything runs, and the recorded
  command is the ``klt`` argv actually run (subprocess intercepted, PDK-free);
* the record leaks no absolute host paths.

The discrimination checks (mutate a copy, expect refusal) prove the gates
bite; the synthesis-view generator's replace-exactly-once guard is checked
the same way.
"""

import contextlib
import copy
import io
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import synth_view  # noqa: E402
import synthesize_oneshot as so  # noqa: E402
import check_divider_depth as cdd  # noqa: E402
import synth_feasibility_ltp as sfl  # noqa: E402

RECORD = ROOT / "asic" / "evidence" / "oneshot-synth-feasibility-v1.json"
LTP_RECORD = ROOT / "asic" / "evidence" / "oneshot-synth-ltp-v1.json"
CORNERS = so.STA_CORNERS


def good_corner(**over):
    base = {
        "timing_status": "constrained",
        "worst_slack_ns": 1.0,
        "worst_hold_slack_ns": 0.5,
        "setup_violation_count": 0,
        "hold_violation_count": 0,
        "constraint_completeness": {"status": "complete"},
    }
    base.update(over)
    return base


def all_corners(**over):
    return {c: good_corner(**over) for c in CORNERS}


def check_record(record: dict, root: Path = ROOT) -> list:
    """Return a list of problems (empty == the record is citeable)."""
    problems = []
    if record.get("schema") != so.SCHEMA:
        problems.append("schema")
    if record.get("label") != "synthesis feasibility":
        problems.append("label is not 'synthesis feasibility'")
    claims = record.get("claims", {})
    for key in ("layout", "signoff", "silicon", "hardware_playback", "sound_fidelity"):
        if claims.get(key) is not False:
            problems.append("claims.%s must be false" % key)
    if claims.get("synthesis_feasibility") is not True:
        problems.append("claims.synthesis_feasibility")

    identity = record.get("identity", {})
    for name, digest in sorted(identity.get("rtl_sha256", {}).items()):
        path = root / "tb" / "sv" / name
        if not path.exists() or synth_view.sha256_file(path) != digest:
            problems.append("stale RTL hash: %s" % name)
    if identity.get("rtl_and_spec_inputs_match_git_head") is not True:
        problems.append("RTL/spec inputs did not match git_head when the record was made")
    if set(identity.get("rtl_sha256", {})) != set(synth_view.SOURCE_ORDER):
        problems.append("rtl_sha256 does not cover the whole-voice source set")

    targets = record.get("targets", {})
    if set(targets) != set(so.TARGET_ORDER):
        problems.append("targets do not match the declared target set")
    for name, result in sorted(targets.items()):
        status = result.get("synthesize", {}).get("status")
        verdict = result.get("sta", {}).get("verdict", {}).get("verdict")
        if status not in ("ok", "timeout", "error", "not_run"):
            problems.append("%s: bad synthesize status %r" % (name, status))
        if status != "ok":
            if verdict != "unverifiable":
                problems.append("%s: no netlist but timing verdict %r" % (name, verdict))
            if "summary" in result.get("synthesize", {}):
                problems.append("%s: metrics present for a target that did not finish" % name)
            continue
        corners = result.get("sta", {}).get("corners")
        if not corners:
            if verdict != "unverifiable":
                problems.append("%s: verdict %r without STA corners" % (name, verdict))
            continue
        recomputed = so.timing_verdict(corners)["verdict"]
        if recomputed != verdict:
            problems.append("%s: stored verdict %r != recomputed %r" % (name, verdict, recomputed))
        if verdict == "met":
            for corner, rep in corners.items():
                if (rep.get("constraint_completeness") or {}).get("status") != "complete":
                    problems.append("%s: 'met' with incomplete constraints at %s" % (name, corner))

    overall = record.get("overall", {})
    top = targets.get(so.TOP, {})
    if overall.get("whole_voice_synthesis") != top.get("synthesize", {}).get("status"):
        problems.append("overall.whole_voice_synthesis disagrees with the target")
    if overall.get("whole_voice_timing_verdict") != top.get("sta", {}).get("verdict", {}).get("verdict"):
        problems.append("overall.whole_voice_timing_verdict disagrees with the target")
    if overall.get("whole_voice_timing_verdict") == "met" and overall.get("whole_voice_synthesis") != "ok":
        problems.append("timing met without a whole-voice netlist")

    if re.search(r"/home/|/Users/|\\.ciel/|\\.volare/", json.dumps(record)):
        problems.append("absolute host path leaked")
    return problems


class TestTimingVerdictCompleteness(unittest.TestCase):
    def test_clean_constrained_complete_is_met(self):
        self.assertEqual(so.timing_verdict(all_corners())["verdict"], "met")

    def test_unconstrained_is_never_met(self):
        verdict = so.timing_verdict(all_corners(timing_status="unconstrained",
                                                worst_slack_ns=1e39))
        self.assertEqual(verdict["verdict"], "unverifiable")

    def test_unclocked_or_unconstrained_endpoints_are_never_met(self):
        for status in ("incomplete", "unknown", None):
            with self.subTest(status=status):
                verdict = so.timing_verdict(all_corners(
                    constraint_completeness={"status": status}))
                self.assertNotEqual(verdict["verdict"], "met")

    def test_missing_completeness_block_is_never_met(self):
        report = good_corner()
        del report["constraint_completeness"]
        self.assertNotEqual(
            so.timing_verdict({c: dict(report) for c in CORNERS})["verdict"], "met")

    def test_missing_corner_is_never_met(self):
        corners = all_corners()
        del corners["ss_125C_4v50"]
        self.assertEqual(so.timing_verdict(corners)["verdict"], "unverifiable")

    def test_negative_slack_is_not_met_even_when_incomplete(self):
        corners = all_corners(worst_slack_ns=-0.1,
                              constraint_completeness={"status": "incomplete"})
        self.assertEqual(so.timing_verdict(corners)["verdict"], "not_met")

    def test_negative_hold_slack_is_not_met(self):
        self.assertEqual(so.timing_verdict(all_corners(worst_hold_slack_ns=-0.01))["verdict"],
                         "not_met")

    def test_violation_count_overrides_a_positive_wns(self):
        self.assertEqual(so.timing_verdict(all_corners(setup_violation_count=3))["verdict"],
                         "not_met")

    def test_one_failing_corner_fails_the_set(self):
        corners = all_corners()
        corners["ss_125C_4v50"] = good_corner(worst_slack_ns=-2.0)
        self.assertEqual(so.timing_verdict(corners)["verdict"], "not_met")

    def test_missing_slack_numbers_are_not_met(self):
        corners = all_corners()
        corners["tt_025C_5v00"] = good_corner(worst_slack_ns=None)
        self.assertNotEqual(so.timing_verdict(corners)["verdict"], "met")


class TestScheduleArithmetic(unittest.TestCase):
    def test_cycle_schedule_matches_the_documented_phase_contract(self):
        sched = so.clip_cycle_schedule()
        self.assertEqual(sched["control_cycles"], 1764 * 4)
        self.assertEqual(sched["audio_cycles"], 176400 * 4 * 2)
        self.assertEqual(sched["cycles_per_clip"], 7056 + 1411200)
        self.assertEqual(sched["dr0010_serialized_candidate"]["cycles_per_clip_at_T0"],
                         145 * 352800)

    def test_realtime_factor_refuses_missing_fmax(self):
        self.assertIsNone(so.realtime_factor(None))
        self.assertIsNone(so.realtime_factor(0))
        self.assertAlmostEqual(so.realtime_factor(1.0), 1418256 / 1e6 / 4.0, places=3)


class TestSynthesisViewGenerator(unittest.TestCase):
    def test_every_rewrite_must_match_exactly_once(self):
        text = (synth_view.SV / "adsr_engine.sv").read_text(encoding="utf-8")
        names = [n for n, _ in synth_view.package_ints(
            (synth_view.SV / synth_view.PKG).read_text(encoding="utf-8"))]
        derived = synth_view.transform("adsr_engine.sv", text, "x", names)
        self.assertIn("if (i <= den_msb + 60)", derived)
        self.assertNotIn("import gf180_rtl_constants", derived)
        with self.assertRaises(synth_view.SynthViewError):
            synth_view.transform("adsr_engine.sv",
                                 text.replace("den_msb + 60", "den_msb + 61"), "x", names)

    def test_lut_initial_block_loses_its_plusargs(self):
        text = (synth_view.SV / "quarter_wave_lut.sv").read_text(encoding="utf-8")
        names = [n for n, _ in synth_view.package_ints(
            (synth_view.SV / synth_view.PKG).read_text(encoding="utf-8"))]
        derived = synth_view.transform("quarter_wave_lut.sv", text, "asic/build/src", names)
        self.assertNotIn("$value$plusargs", derived)
        self.assertNotIn("$finish", derived)
        self.assertIn('$readmemh("asic/build/src/%s", rom)' % synth_view.AUDIO_MEMH, derived)

    def test_package_becomes_macros_and_uses_are_expanded(self):
        pkg = (synth_view.SV / synth_view.PKG).read_text(encoding="utf-8")
        view = synth_view._package_to_defines(pkg)
        self.assertIn("`define C1_WIDTH 24", view)
        self.assertNotIn("localparam", view)
        self.assertNotIn("C2_UNITS_PER_TURN", view)  # does not fit a 32-bit int
        out = synth_view._macro_ize(
            "import gf180_rtl_constants::*;\nwire [C1_WIDTH-1:0] a;\n", ["C1_WIDTH"])
        self.assertIn("`C1_WIDTH", out)
        self.assertNotIn("import", out)


class TestSynthesizeInvocation(unittest.TestCase):
    """The Yosys budget rides in the request; ``klt`` is invoked directly.

    PDK-free (issue #563): the request is really generated and the subprocess
    call is intercepted, so these check the request document and the exact
    ``klt`` argv without ``klt``, Yosys, a liberty or a PDK store. They
    exercise request generation and subprocess arguments only -- they are
    not a synthesis result.
    """

    FAILED = subprocess.CompletedProcess([], 1, stdout="", stderr="boom")

    def setUp(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory(dir=ROOT / "asic"))
        self.workdir = Path(tmp)

    def intercepted_klt(self, request, timeout_s):
        """``run_klt_synthesize`` with the subprocess intercepted -> (outcome, call)."""
        with mock.patch.object(so, "sh", return_value=self.FAILED) as run, \
                mock.patch.object(so, "reap_orphan_abc", return_value=[]) as reap:
            outcome = so.run_klt_synthesize(request, timeout_s)
        run.assert_called_once()
        reap.assert_called_once_with()
        return outcome, run.call_args

    def intercepted_target(self, target, timeout_s):
        """``run_target`` with the subprocess intercepted -> (result, call)."""
        with mock.patch.object(so, "sh", return_value=self.FAILED) as run, \
                mock.patch.object(so, "reap_orphan_abc", return_value=[]) as reap:
            result = so.run_target(self.workdir, target, timeout_s, force=False)
        run.assert_called_once()
        reap.assert_called_once_with()
        return result, run.call_args

    def assert_ran_from_repo_root(self, call):
        """klt is given repo-relative paths and the repo root as its cwd."""
        self.assertEqual(call.kwargs["cwd"], so.ROOT)

    def test_default_budget_is_the_request_field(self):
        request = so.write_synth_request(self.workdir, "noise_stream_dut", "r1")
        document = json.loads(request.read_text(encoding="utf-8"))
        self.assertEqual(document["yosys_timeout_s"], so.KLT_DEFAULT_YOSYS_TIMEOUT_S)
        self.assertEqual(so.KLT_DEFAULT_YOSYS_TIMEOUT_S, 300.0)
        self.assertNotIn("yosys_timeout_s", document["constraints"])

    def test_per_target_override_is_the_request_field(self):
        request = so.write_synth_request(self.workdir, "upsample_engine", "r1", 2700)
        document = json.loads(request.read_text(encoding="utf-8"))
        self.assertEqual(document["yosys_timeout_s"], 2700.0)
        self.assertNotIn("yosys_timeout_s", document["constraints"])

    def test_the_ceiling_itself_is_accepted(self):
        self.assertEqual(so.KLT_YOSYS_TIMEOUT_MAX_S, 3600.0)
        request = so.write_synth_request(self.workdir, "upsample_engine", "r1", 3600)
        self.assertEqual(json.loads(request.read_text(encoding="utf-8"))["yosys_timeout_s"], 3600.0)

    def test_unsupported_budget_is_refused_before_anything_is_written(self):
        for bad in (so.KLT_YOSYS_TIMEOUT_MAX_S + 1, 7200, 0, -5, float("nan"),
                    float("inf"), True, "300", None):
            with self.subTest(budget=bad):
                shutil.rmtree(self.workdir)
                self.workdir.mkdir()
                with self.assertRaises(ValueError) as caught:
                    so.write_synth_request(self.workdir, "upsample_engine", "r1", bad)
                message = str(caught.exception)
                self.assertIn("upsample_engine", message)
                self.assertIn("yosys_timeout_s", message)
                self.assertIn("3600", message)
                self.assertEqual(list(self.workdir.iterdir()), [])

    def test_extended_run_is_a_direct_klt_invocation(self):
        request = so.write_synth_request(self.workdir, "upsample_engine", "r1", 2700)
        outcome, call = self.intercepted_klt(request, 2700)
        self.assertEqual(call.args[0], [
            "klt", "synthesize", so.rel(request),
            "--pdk", so.PDK_VARIANT, "--pdk-root", str(so.PDK_ROOT), "--format", "json"])
        self.assert_ran_from_repo_root(call)
        self.assertEqual(call.kwargs["timeout"], 2700 + 180)
        self.assertEqual(outcome["status"], "error")
        self.assertEqual(outcome["message"], "boom")
        self.assertEqual(outcome["yosys_timeout_s"], 2700.0)

    def test_recorded_command_is_the_invocation_with_only_the_pdk_root_elided(self):
        for timeout_s in (so.KLT_DEFAULT_YOSYS_TIMEOUT_S, 2700.0):
            with self.subTest(timeout_s=timeout_s):
                request = so.write_synth_request(self.workdir, "upsample_engine", "r1", timeout_s)
                outcome, call = self.intercepted_klt(request, timeout_s)
                self.assert_ran_from_repo_root(call)
                self.assertEqual(call.kwargs["timeout"], timeout_s + 180)
                argv = call.args[0]
                self.assertEqual(argv.count(str(so.PDK_ROOT)), 1)
                self.assertEqual(outcome["command"], " ".join(
                    "<ciel store>" if tok == str(so.PDK_ROOT) else tok for tok in argv))
                self.assertEqual(
                    outcome["command"],
                    "klt synthesize %s --pdk %s --pdk-root <ciel store> --format json"
                    % (so.rel(request), so.PDK_VARIANT))
                self.assertEqual(outcome["yosys_timeout_s"], timeout_s)

    def test_target_result_records_the_request_and_command_it_ran(self):
        for target, timeout_s in (("noise_stream_dut", so.KLT_DEFAULT_YOSYS_TIMEOUT_S),
                                  ("upsample_engine", 2700.0)):
            request_path = self.workdir / "req" / ("synth_%s.json" % target)
            with self.subTest(target=target):
                result, call = self.intercepted_target(target, timeout_s)
                self.assert_ran_from_repo_root(call)
                self.assertEqual(call.args[0][:3], ["klt", "synthesize", so.rel(request_path)])
                recorded = json.loads(request_path.read_text(encoding="utf-8"))
                self.assertEqual(result["request"], recorded)
                self.assertEqual(result["request"]["yosys_timeout_s"], timeout_s)
                self.assertEqual(result["request"]["hdl_toplevel"], target)
                self.assertEqual(result["synthesize"]["yosys_timeout_s"], timeout_s)
                self.assertIn(so.rel(request_path), result["synthesize"]["command"])
                self.assertNotIn("__kwdefaults__", json.dumps(result))
                stored = json.loads((self.workdir / "results" / ("%s.json" % target))
                                    .read_text(encoding="utf-8"))
                self.assertEqual(stored, result)

    def test_missing_klt_writes_target_error_and_skips_sta(self):
        missing = FileNotFoundError(2, "No such file or directory", "klt")
        with mock.patch.object(subprocess, "run", side_effect=missing) as run, \
                mock.patch.object(so, "reap_orphan_abc", return_value=[]) as reap:
            so.run_target(self.workdir, "noise_stream_dut", 2700, force=False)

        request = self.workdir / "req" / "synth_noise_stream_dut.json"
        run.assert_called_once_with(
            ["klt", "synthesize", so.rel(request), "--pdk", so.PDK_VARIANT,
             "--pdk-root", str(so.PDK_ROOT), "--format", "json"],
            capture_output=True, text=True, cwd=so.ROOT, timeout=2880)
        reap.assert_not_called()
        stored = json.loads((self.workdir / "results" / "noise_stream_dut.json").read_text())
        synth = stored["synthesize"]
        self.assertEqual(synth["status"], "error")
        self.assertEqual(synth["message"], str(missing))
        self.assertEqual(synth["reaped_orphan_abc"], 0)
        self.assertIsNone(synth["exit_code"])
        self.assertEqual(synth["command"], so.display_command(run.call_args.args[0]))
        self.assertEqual(synth["yosys_timeout_s"], 2700)
        self.assertEqual(stored["request"]["yosys_timeout_s"], 2700)
        self.assertEqual(stored["sta"]["status"], "not_run")

    def test_missing_klt_without_procfs_keeps_launch_diagnostic_for_every_target(self):
        missing = FileNotFoundError(2, "No such file or directory", "klt")
        no_proc = FileNotFoundError(2, "No such file or directory", "/proc")
        real_iterdir = Path.iterdir

        def iterdir(path):
            if str(path) == "/proc":
                raise no_proc
            return real_iterdir(path)

        with mock.patch.object(subprocess, "run", side_effect=missing), \
                mock.patch.object(Path, "iterdir", iterdir):
            for target, budget in (("noise_stream_dut", 300), ("upsample_engine", 2700)):
                so.run_target(self.workdir, target, budget, force=False)

        for target, budget in (("noise_stream_dut", 300), ("upsample_engine", 2700)):
            stored = json.loads((self.workdir / "results" / ("%s.json" % target)).read_text())
            synth = stored["synthesize"]
            self.assertEqual(synth["status"], "error")
            self.assertEqual(synth["message"], str(missing))
            self.assertIsNone(synth["exit_code"])
            self.assertEqual(synth["yosys_timeout_s"], budget)
            self.assertEqual(stored["sta"]["status"], "not_run")

    def run_cli(self, argv):
        result = {"target": "x", "synthesize": {"status": "error"},
                  "sta": {"verdict": {"verdict": "unverifiable"}}}
        out = io.StringIO()
        with mock.patch.object(so.synth_view, "build") as build, \
                mock.patch.object(so, "run_target", return_value=result) as run_target, \
                contextlib.redirect_stdout(out):
            rc = so.main(["run", "--workdir", str(self.workdir)] + argv)
        return rc, out.getvalue(), build, run_target

    def test_cli_passes_each_target_its_resolved_budget(self):
        rc, _, build, run_target = self.run_cli(
            ["--targets", "noise_stream_dut,upsample_engine,quarter_wave_lut",
             "--timeout", "1800", "--timeouts", "upsample_engine=2700"])
        self.assertEqual(rc, 0)
        build.assert_called_once_with(self.workdir / "src")
        self.assertEqual([(call.args[1], call.args[2]) for call in run_target.call_args_list],
                         [("noise_stream_dut", 1800.0), ("upsample_engine", 2700.0),
                          ("quarter_wave_lut", 1800.0)])

    def test_cli_refuses_an_unsupported_budget_before_anything_runs(self):
        cases = {
            "per-target": (["--targets", "noise_stream_dut,upsample_engine",
                            "--timeouts", "noise_stream_dut=300,upsample_engine=3601"],
                           "upsample_engine"),
            "global": (["--targets", "noise_stream_dut,upsample_engine", "--timeout", "0"],
                       "every target"),
        }
        for name, (argv, named) in cases.items():
            with self.subTest(budget=name):
                rc, out, build, run_target = self.run_cli(argv)
                self.assertEqual(rc, 2)
                build.assert_not_called()
                run_target.assert_not_called()
                self.assertIn("yosys_timeout_s for %s" % named, out)
                self.assertIn("<= 3600", out)

    def test_cli_help_names_the_request_field_and_its_ceiling(self):
        with contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit):
            so.main(["run", "--help"])
        self.assertIn("yosys_timeout_s", out.getvalue())
        self.assertIn("3600", out.getvalue())


@unittest.skipUnless(RECORD.exists(), "no committed synthesis-feasibility record yet")
class TestCommittedRecord(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.record = json.loads(RECORD.read_text(encoding="utf-8"))

    def test_record_is_committable(self):
        self.assertEqual(check_record(self.record), [])

    def test_constraints_and_library_are_named(self):
        constraints = self.record["constraints"]
        for key in ("clock_period_ns", "input_delay_ns", "output_delay_ns",
                    "reset_assumption", "generated_clocks_and_enables", "memory_treatment"):
            self.assertIn(key, constraints)
        library = self.record["library"]
        self.assertEqual(library["cell_library"], so.CELL_LIBRARY)
        self.assertTrue(library["open_pdks_version"])
        self.assertTrue(library["liberty_sha256"])

    def test_derived_synthesis_view_still_reproduces_the_recorded_hashes(self):
        """Regenerate the view (and ROM payloads) and compare with the record.

        Catches a changed transform, a changed ROM table, or an RTL edit that
        the plain RTL hash check would not tie to the derived sources.
        """
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ledger = synth_view.build(Path(tmp) / "src")
        identity = self.record["identity"]
        self.assertEqual(ledger["synthesis_view_sha256"], identity["synthesis_view_sha256"])
        self.assertEqual(ledger["memh_sha256"], identity["rom_payload_sha256"])
        self.assertEqual(ledger["original_sha256"], identity["rtl_sha256"])

    def test_every_completed_target_names_its_yosys_bound_and_script(self):
        for name, result in self.record["targets"].items():
            synth = result["synthesize"]
            if synth["status"] in ("ok", "timeout", "error"):
                self.assertIn("yosys_timeout_s", synth, name)
                self.assertTrue((ROOT / "asic" / "evidence" / "scripts"
                                 / ("synth_%s.ys" % name)).exists(), name)

    def test_schedule_is_the_derived_arithmetic(self):
        schedule = self.record["sample_schedule"]
        self.assertEqual(schedule["cycles_per_clip"], so.clip_cycle_schedule()["cycles_per_clip"])

    # ---- discrimination: the gates must refuse planted faults ----------
    def test_a_stale_rtl_hash_is_refused(self):
        bad = copy.deepcopy(self.record)
        name = next(iter(bad["identity"]["rtl_sha256"]))
        bad["identity"]["rtl_sha256"][name] = "0" * 64
        self.assertTrue(any("stale RTL hash" in p for p in check_record(bad)))

    def test_a_claim_upgrade_is_refused(self):
        for key in ("layout", "signoff", "silicon", "hardware_playback", "sound_fidelity"):
            bad = copy.deepcopy(self.record)
            bad["claims"][key] = True
            self.assertTrue(check_record(bad), key)

    def test_a_forged_met_verdict_is_refused(self):
        bad = copy.deepcopy(self.record)
        for result in bad["targets"].values():
            sta = result.get("sta", {})
            if sta.get("corners"):
                sta["verdict"]["verdict"] = "met"
                break
        else:
            # No target has STA corners: forge a met verdict on a target
            # without a netlist instead -- also refused.
            name = so.TOP
            bad["targets"][name]["sta"]["verdict"]["verdict"] = "met"
            bad["overall"]["whole_voice_timing_verdict"] = "met"
        if bad != self.record:
            self.assertTrue(check_record(bad))

    def test_metrics_on_an_unfinished_target_are_refused(self):
        bad = copy.deepcopy(self.record)
        for result in bad["targets"].values():
            if result["synthesize"]["status"] != "ok":
                result["synthesize"]["summary"] = {"area_um2": 1.0}
                self.assertTrue(check_record(bad))
                return
        self.skipTest("every target finished; nothing to plant on")

    def test_a_host_path_leak_is_refused(self):
        bad = copy.deepcopy(self.record)
        bad["scope"] += " /home/someone/.ciel/x"
        self.assertTrue(check_record(bad))


PLAN = ROOT / "asic" / "whole-voice-feasibility-plan.md"
README = ROOT / "asic" / "README.md"


def pinned_figures(plan_text: str) -> list:
    """Return ``[(dotted_path, expected_text)]`` from the plan's pinned block."""
    match = re.search(r"```pinned-figures\n(.*?)\n```", plan_text, re.S)
    if not match:
        return []
    pins = []
    for line in match.group(1).splitlines():
        if line.strip():
            path, _, expected = line.partition(" = ")
            pins.append((path.strip(), expected.strip()))
    return pins


def resolve(record, dotted: str):
    node = record
    for part in dotted.split("."):
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node


def check_plan_pins(record: dict, plan_text: str) -> list:
    """Return problems where a pinned plan figure disagrees with the record."""
    problems = []
    pins = pinned_figures(plan_text)
    if not pins:
        problems.append("plan has no pinned figures")
    for path, expected in pins:
        try:
            actual = resolve(record, path)
        except (KeyError, IndexError, ValueError, TypeError):
            problems.append("%s: not in the record" % path)
            continue
        if isinstance(actual, float):
            # Relative tolerance, not a decimals-from-plan rule: the plan
            # legitimately states a rounded form of a full-precision record
            # value, so the comparison precision must never come from the
            # plan string. rel_tol=1e-3 is the measured knee: it refuses
            # the small-magnitude truncations (worst_slack_ns -5.38 -> -5)
            # while passing every committed pin.
            if not math.isclose(actual, float(expected), rel_tol=1e-3):
                problems.append("%s: plan %s != record %r" % (path, expected, actual))
        elif str(actual) != expected:
            problems.append("%s: plan %s != record %r" % (path, expected, actual))
    return problems


@unittest.skipUnless(RECORD.exists() and PLAN.exists(), "no record or plan")
class TestWholeVoicePlan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.record = json.loads(RECORD.read_text(encoding="utf-8"))
        cls.plan = PLAN.read_text(encoding="utf-8")

    def test_every_pinned_figure_matches_the_record(self):
        self.assertEqual(check_plan_pins(self.record, self.plan), [])

    def test_an_altered_pinned_figure_is_refused(self):
        pins = pinned_figures(self.plan)
        self.assertGreater(len(pins), 10)
        path, expected = pins[0]
        bad = self.plan.replace("%s = %s" % (path, expected), "%s = 1" % path)
        self.assertNotEqual(bad, self.plan)
        self.assertTrue(check_plan_pins(self.record, bad))

    def test_a_truncated_pinned_figure_is_refused(self):
        # Small-magnitude slack pins restated with fewer decimals: the old
        # plan-derived-precision rule accepted these (e.g. -5 against a
        # record of -5.38319, a 7.1% distortion).
        for old, new in (
            ("targets.noise_stream_dut.sta.corners.ss_125C_4v50"
             ".worst_slack_ns = -5.38",
             "targets.noise_stream_dut.sta.corners.ss_125C_4v50"
             ".worst_slack_ns = -5"),
            ("targets.normalization_replay_engine.sta.corners.tt_025C_5v00"
             ".worst_slack_ns = -170.29",
             "targets.normalization_replay_engine.sta.corners.tt_025C_5v00"
             ".worst_slack_ns = -170"),
        ):
            with self.subTest(pin_line=old):
                bad = self.plan.replace(old, new)
                self.assertNotEqual(bad, self.plan)
                self.assertTrue(check_plan_pins(self.record, bad))

    def test_a_drifted_record_is_refused(self):
        bad = copy.deepcopy(self.record)
        bad["overall"]["whole_voice_synthesis"] = "ok"
        self.assertTrue(check_plan_pins(bad, self.plan))

    def test_plan_keeps_the_unmet_status_and_scope_wording(self):
        lowered = self.plan.lower()
        self.assertIn("synthesis feasibility", lowered)
        self.assertIn("**unmet**", self.plan)
        self.assertIn("evaluation point", lowered)
        self.assertIn("inferred, not measured", lowered)
        for phrase in ("signoff achieved", "closes #82", "closed #82"):
            self.assertNotIn(phrase, lowered)

    def test_readme_links_the_plan_and_record_still_claims_nothing_more(self):
        self.assertIn("whole-voice-feasibility-plan.md", README.read_text(encoding="utf-8"))
        self.assertEqual(check_record(self.record), [])


@unittest.skipUnless(RECORD.exists() and PLAN.exists(), "no record or plan")
class TestDividerDepthCheck(unittest.TestCase):
    """Plan check C3: divider-depth proportionality, arithmetic on the record.

    The tool derives everything from the committed record; the plan's
    ``c3-divider-depth`` block is the *recorded outcome* this suite pins.
    The test asserts the recorded outcome, never that the two per-stage
    rates agree: a test asserting agreement would have to be tuned to pass.
    """

    @classmethod
    def setUpClass(cls):
        cls.record = json.loads(RECORD.read_text(encoding="utf-8"))
        cls.plan = PLAN.read_text(encoding="utf-8")

    def test_c3_verdict_is_disagree_outside_the_declared_tolerance(self):
        result = cdd.analyze(self.record)
        self.assertEqual(result["verdict"], "disagree")
        self.assertGreater(result["relative_difference"], cdd.AGREEMENT_TOLERANCE)
        self.assertEqual(result["tolerance"], cdd.AGREEMENT_TOLERANCE)

    def test_derived_figures_match_the_recorded_outcome(self):
        result = cdd.analyze(self.record)
        b1, b2 = result["rows"][0], result["rows"][1]
        self.assertEqual(b1["name"], "normalization_replay_engine")
        self.assertEqual(b1["stages"], 64)
        self.assertEqual(round(b1["ps_per_stage"], 2), 3257.44)
        self.assertEqual(b2["name"], "upsample_engine")
        self.assertEqual(b2["stages"], 49)
        self.assertEqual(round(b2["ps_per_stage"], 2), 2201.76)
        for row in result["rows"][2:]:
            self.assertIsNone(row["ps_per_stage"], row["name"])
        self.assertEqual(round(result["relative_difference"], 3), 0.387)
        self.assertEqual(round(result["intercept_ps"], 2), -220707.30)

    def test_plan_c3_block_matches_the_record_derived_figures(self):
        self.assertEqual(cdd.check_plan(self.record, self.plan), [])

    def test_an_altered_c3_block_is_refused(self):
        truncations = (
            # Each conclusion-bearing figure restated with fewer decimals
            # than the tool declares: the old plan-derived-precision rule
            # accepted every one of these, asserting the opposite of the
            # recorded C3 conclusion.
            ("tolerance_relative = 0.10", "tolerance_relative = 0"),
            ("ratio_b1_over_b2 = 1.4795", "ratio_b1_over_b2 = 1"),
            ("relative_difference = 0.387", "relative_difference = 0"),
            ("intercept_ps = -220707.30", "intercept_ps = -220707"),
            ("normalization_replay_engine.ps_per_stage = 3257.44",
             "normalization_replay_engine.ps_per_stage = 3257"),
        )
        for old, new in (("verdict = disagree", "verdict = agree"),
                         ("ps_per_stage = 3257.44", "ps_per_stage = 1")) \
                + truncations:
            with self.subTest(block_line=old):
                bad = self.plan.replace(old, new)
                self.assertNotEqual(bad, self.plan)
                self.assertTrue(cdd.check_plan(self.record, bad))

    def test_a_c3_figure_stated_with_extra_decimals_is_refused(self):
        # Deliberate, not accidental: the block records the outcome at the
        # precision the tool declares, so any other spelling of a figure --
        # more precision included -- is refused. The comparison precision
        # never comes from the plan string.
        bad = self.plan.replace("relative_difference = 0.387",
                                "relative_difference = 0.3868")
        self.assertNotEqual(bad, self.plan)
        self.assertTrue(cdd.check_plan(self.record, bad))

    def test_stage_figures_compare_as_exact_integer_text(self):
        figures = cdd.block_figures(cdd.analyze(self.record))
        for name, stages in cdd.MODULE_STAGES:
            with self.subTest(module=name):
                self.assertEqual(figures[name + ".stages"], str(stages))
                self.assertNotIn(".", figures[name + ".stages"])

    def test_zero_stage_controls_are_reported_not_divided(self):
        result = cdd.analyze(self.record)
        names = [row["name"] for row in result["rows"]]
        self.assertEqual(names, ["normalization_replay_engine", "upsample_engine",
                                 "quarter_wave_lut", "noise_stream_dut"])
        for row in result["rows"]:
            if row["stages"] == 0:
                self.assertIsNone(row["ps_per_stage"], row["name"])
            else:
                self.assertAlmostEqual(
                    row["critical_path_ps"] / row["stages"], row["ps_per_stage"])


def rebuild_view_ledger(record: dict) -> dict:
    """Regenerate the synthesis view in a temp dir with the record's memh text.

    The ``$readmemh`` directory text is part of the view's bytes, so a
    rebuild with a different text would invent drift.
    """
    memh_dir_text = record["identity"].get("memh_dir_text", "asic/build/src")
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        return synth_view.build(Path(tmp) / "src", memh_dir_text=memh_dir_text)


@unittest.skipUnless(LTP_RECORD.exists() and PLAN.exists(),
                     "no ltp record or plan")
class TestLtpDepthEvidence(unittest.TestCase):
    """Plan check C4: the committed unmapped-depth record must stay honest.

    Same discipline as ``TestCommittedRecord``/``TestWholeVoicePlan``, for
    the second record: depths are cell levels of an unmapped netlist and can
    never grow a timing field; every figure the plan quotes is pinned; the
    hashes are fresh against the working tree. The discrimination checks
    prove the gates bite.
    """

    @classmethod
    def setUpClass(cls):
        cls.record = json.loads(LTP_RECORD.read_text(encoding="utf-8"))
        cls.plan = PLAN.read_text(encoding="utf-8")
        cls.ledger = rebuild_view_ledger(cls.record)

    def test_record_is_citeable_depth_evidence(self):
        self.assertEqual(sfl.check_record(self.record, self.ledger), [])

    def test_record_hashes_are_fresh_against_the_tree(self):
        identity = self.record["identity"]
        self.assertEqual(self.ledger["synthesis_view_sha256"],
                         identity["synthesis_view_sha256"])
        self.assertEqual(self.ledger["memh_sha256"], identity["memh_sha256"])
        self.assertEqual(self.ledger["original_sha256"], identity["rtl_sha256"])

    def test_engines_match_c4s_declared_set(self):
        self.assertEqual(set(self.record["engines"]),
                         {"sine_vco_engine", "adsr_engine", "lfo_vca_engine",
                          "square_saw_vco_engine", "mod_matrix_engine"})

    def test_no_timing_like_key_exists_anywhere(self):
        def keys(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    yield key
                    yield from keys(value)
            elif isinstance(node, list):
                for value in node:
                    yield from keys(value)
        for key in keys(self.record):
            self.assertIsNone(sfl.FORBIDDEN_KEY_RE.search(str(key)), key)

    def test_units_say_cell_levels(self):
        self.assertIn("cell level", self.record["units"])
        self.assertIn("not nanoseconds",
                      self.record["engines"]["adsr_engine"]["note"])

    def test_every_engine_ran_inside_its_declared_bound(self):
        for name, engine in self.record["engines"].items():
            with self.subTest(engine=name):
                self.assertEqual(engine["timeout_bound_s"],
                                 self.record["timeout_s_per_engine"])
                self.assertLessEqual(engine["wall_time_s"],
                                     engine["timeout_bound_s"])

    def test_ok_engines_carry_depth_and_counts(self):
        for name, engine in self.record["engines"].items():
            if engine["status"] != "ok":
                continue
            with self.subTest(engine=name):
                self.assertIsInstance(engine["ltp"]["depth_cells"], int)
                self.assertIsInstance(engine["ltp"]["path_start"], str)
                self.assertIsInstance(engine["ltp"]["path_end"], str)
                self.assertIsInstance(engine["stat_cell_counts"], dict)

    def test_sine_vco_div_mod_widths_match_the_frontend_audit(self):
        # B4's front-end audit counted 2 $div/$mod cells; the C4 record
        # names them with widths -- the two records agree.
        arith = self.record["engines"]["sine_vco_engine"]["arith_operator_widths"]
        self.assertEqual(arith,
                         {"$div": {"96x31->96": 1}, "$mod": {"96x31->96": 1}})

    def test_a_timeout_and_an_empty_ltp_are_results_not_crashes(self):
        # The negative outcomes C4 accepts: the parsers yield None/{}/nothing
        # to record, and the gates accept a timeout engine that carries no
        # depth -- recorded, never retried.
        self.assertIsNone(sfl.parse_ltp("no longest path here"))
        self.assertEqual(sfl.parse_stat("nothing"), {})
        self.assertEqual(sfl.parse_arith(Path("/nonexistent/arith.json")), {})
        bad = copy.deepcopy(self.record)
        bad["engines"]["lfo_vca_engine"].update({"status": "timeout",
                                                 "exit_code": None,
                                                 "ltp": None,
                                                 "stat_cell_counts": None,
                                                 "arith_operator_widths": None})
        self.assertEqual(
            [p for p in sfl.check_record(bad, self.ledger)
             if p.startswith("lfo_vca_engine")], [])

    def test_plan_pins_match_the_record(self):
        self.assertEqual(sfl.check_plan(self.record, self.plan), [])

    def test_an_altered_pin_is_refused(self):
        bad = self.plan.replace("engines.adsr_engine.ltp.depth_cells = 32",
                                "engines.adsr_engine.ltp.depth_cells = 33")
        self.assertNotEqual(bad, self.plan)
        self.assertTrue(sfl.check_plan(self.record, bad))

    def test_a_drifted_record_is_refused(self):
        bad = copy.deepcopy(self.record)
        bad["engines"]["mod_matrix_engine"]["ltp"]["depth_cells"] = 999
        self.assertTrue(sfl.check_plan(bad, self.plan))

    def test_a_timing_like_key_is_refused(self):
        bad = copy.deepcopy(self.record)
        bad["engines"]["adsr_engine"]["worst_slack_ns"] = -1.0
        self.assertTrue(sfl.check_record(bad, self.ledger))

    def test_an_absolute_host_path_is_refused(self):
        bad = copy.deepcopy(self.record)
        script = bad["engines"]["adsr_engine"]["yosys_script"]
        bad["engines"]["adsr_engine"]["yosys_script"] = script.replace(
            "read_verilog asic/build/ltp/src",
            "read_verilog /home/builder/gf180-torchsynth/asic/build/ltp/src",
            1)
        self.assertNotEqual(script,
                            bad["engines"]["adsr_engine"]["yosys_script"])
        self.assertTrue(sfl.check_record(bad, self.ledger))

    def test_depth_on_a_timed_out_engine_is_refused(self):
        bad = copy.deepcopy(self.record)
        bad["engines"]["mod_matrix_engine"]["status"] = "timeout"
        self.assertTrue(sfl.check_record(bad, self.ledger))

    def test_b_row_cells_quote_the_recorded_depths(self):
        row_depth = {"| B4 |": "sine_vco_engine",
                     "| B6 |": "square_saw_vco_engine",
                     "| B9 |": "mod_matrix_engine"}
        for prefix, engine in row_depth.items():
            line = next(l for l in self.plan.splitlines()
                        if l.startswith(prefix))
            depth = self.record["engines"][engine]["ltp"]["depth_cells"]
            self.assertIn(str(depth), line, engine)
        b5 = next(l for l in self.plan.splitlines() if l.startswith("| B5 |"))
        self.assertIn("32", b5)
        self.assertIn("396", b5)

    def test_c4_row_reports_the_outcome(self):
        row = next(l for l in self.plan.splitlines() if l.startswith("| C4 |"))
        self.assertIn("tools/synth_feasibility_ltp.py", row)
        self.assertIn("2026-11-18", row)
        self.assertNotIn("**not run here**", row)

    def test_tool_check_mode_passes(self):
        self.assertEqual(sfl.main(["--check"]), 0)


if __name__ == "__main__":
    unittest.main()
