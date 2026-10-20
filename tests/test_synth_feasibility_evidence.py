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
* the record leaks no absolute host paths.

The discrimination checks (mutate a copy, expect refusal) prove the gates
bite; the synthesis-view generator's replace-exactly-once guard is checked
the same way.
"""

import copy
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import synth_view  # noqa: E402
import synthesize_oneshot as so  # noqa: E402

RECORD = ROOT / "asic" / "evidence" / "oneshot-synth-feasibility-v1.json"
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
            decimals = len(expected.partition(".")[2])
            if round(actual, decimals) != round(float(expected), decimals):
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


if __name__ == "__main__":
    unittest.main()
