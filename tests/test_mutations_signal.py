"""Family contract tests for the #33 signal-chain fault operators.

These tests pin the oscillator/gain/clipping/normalization family to the
landed mutation contract (spec/MUTATIONS.md): registration through the public
API with typed magnitudes and native units, fail-closed plan validation, the
non-writable normalization decision seam, directed-fixture exactness,
declared fault behavior with affected-sample reporting, clean-control
pairing, and the committed bounded publication.

The periodic oscillator property rows depend on the optional NumPy metrics
extra. On a stdlib-only host those rows are asserted as the declared
estimator unavailability recorded by the family runner
(numpy_unavailable_install_metrics_extra; unrun is never a PASS) and the
committed-publication rebuild refuses fail-closed naming exactly those
rows; the dedicated numerical CI job asserts the full trips with NumPy
installed and single-threaded BLAS.
"""

import importlib.util
import math
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import float_mix  # noqa: E402
from torchsynth_voice import mutations  # noqa: E402
from torchsynth_voice import mutation_runtime  # noqa: E402
from torchsynth_voice import mutations_signal as family  # noqa: E402
from torchsynth_voice import paired_metrics as pm  # noqa: E402
from torchsynth_voice import spectral_estimators as se  # noqa: E402

family.register_family()
CATALOG = mutations.load_seam_catalog(ROOT / mutations.SEAM_CATALOG_PATH)
BINDING = family.fixture_binding(family.fixture_identity())
FAMILY_OPERATORS = tuple(family.OPERATOR_DEFINITIONS)


def plan(*instances):
    return mutations.make_plan(BINDING, list(instances), CATALOG)


def norm_measurement(pre, post, applied, gain):
    peak, index = float_mix.peak_of_clip(pre)
    return se.normalization(
        pre,
        post,
        sample_rate_hz=family.SAMPLE_RATE_HZ,
        complete_samples=len(pre),
        reported_applied=applied,
        reported_peak=peak,
        reported_index=index,
        reported_gain=gain,
    )


class RegistrationTests(unittest.TestCase):
    def test_family_operators_registered_through_public_api(self):
        for operator_id in FAMILY_OPERATORS:
            with self.subTest(operator=operator_id):
                self.assertIn(operator_id, mutations.OPERATORS)
                definition = mutations.OPERATORS[operator_id]
                self.assertEqual(definition["version"], 1)
                self.assertTrue(definition["seam"] in CATALOG["seams"])
                self.assertTrue(CATALOG["seams"][definition["seam"]]["writable"])

    def test_family_operators_are_not_bridge_operators(self):
        for operator_id in FAMILY_OPERATORS:
            self.assertFalse(operator_id.startswith("bridge."))
        for operator_id in ("bridge.sham_slot", "bridge.scale_slot", "bridge.raise_slot", "bridge.scale_parameter"):
            self.assertIn("test-only", mutations.OPERATORS[operator_id]["summary"])

    def test_typed_magnitude_domains_use_native_units(self):
        expected_units = {
            "osc.tuning_shift": "semitone",
            "osc.phase_offset": "radian",
            "osc.shape_scale": "ratio",
            "gain.db": "dB",
            "gain.dc_offset": "amplitude",
            "clip.round_step": "amplitude_step",
            "clip.saturation_ceiling": "amplitude",
        }
        for operator_id, unit in expected_units.items():
            with self.subTest(operator=operator_id):
                self.assertEqual(mutations.OPERATORS[operator_id]["magnitude"]["unit"], unit)

    def test_out_of_domain_magnitude_refused(self):
        with self.assertRaises(mutations.MutationError):
            plan(family.instance("ms-x", "gain.db", "voice.post_module", 75.0, {"trace": "vco_1.post_vca", "slot": 0}))
        with self.assertRaises(mutations.MutationError):
            plan(family.instance("ms-x", "osc.tuning_shift", "voice.parameter_value", -30.0, {"parameter": "vco_1.tuning", "slot": 0}))

    def test_wrong_unit_refused(self):
        bad = family.instance("ms-x", "gain.db", "voice.post_module", 1.0, {"trace": "vco_1.post_vca", "slot": 0})
        bad["magnitude"] = {"value": 1.0, "unit": "ratio"}
        with self.assertRaises(mutations.MutationError):
            plan(bad)

    def test_trace_enum_enforced(self):
        with self.assertRaises(mutations.MutationError):
            plan(family.instance("ms-x", "gain.db", "voice.post_module", 1.0, {"trace": "keyboard.duration", "slot": 0}))
        with self.assertRaises(mutations.MutationError):
            plan(family.instance("ms-x", "norm.off", "voice.post_module", configuration={"trace": "vco_1.post_vca", "slot": 0}))

    def test_parameter_enum_enforced(self):
        with self.assertRaises(mutations.MutationError):
            plan(family.instance("ms-x", "osc.shape_scale", "voice.parameter_value", 2.0, {"parameter": "vco_1.tuning", "slot": 0}))

    def test_normalization_decision_seam_refuses_naming_producer_handoff(self):
        with self.assertRaises(mutations.MutationError) as caught:
            plan(
                family.instance(
                    "ms-norm",
                    "norm.always_on",
                    family.SEAM_NORMALIZATION_DECISION,
                    configuration={"trace": "mixer.output", "slot": 0},
                )
            )
        message = str(caught.exception)
        self.assertIn("not a writable injection seam", message)
        self.assertIn("Producer handoff required", message)

    def test_duplicate_registration_refused(self):
        with self.assertRaises(mutations.MutationError):
            mutations.register_operator("gain.db", dict(mutations.OPERATORS["gain.db"]))


class FixtureTests(unittest.TestCase):
    def test_directed_peaks_exactly_on_targets_and_unique(self):
        for target in (family.PEAK_ABOVE_ONE, family.PEAK_BELOW_ONE, family.PEAK_AT_ONE):
            with self.subTest(target=target):
                clip = family.directed_clip(target)
                peak, _ = float_mix.peak_of_clip(clip)
                self.assertEqual(peak, target)
                extremum_count = sum(1 for value in clip if value == peak or value == -peak)
                self.assertEqual(extremum_count, 1)

    def test_directed_clip_deterministic(self):
        self.assertEqual(
            family.directed_clip(family.PEAK_ABOVE_ONE),
            family.directed_clip(family.PEAK_ABOVE_ONE),
        )

    def test_lanes_are_full_length_and_deterministic_without_rng(self):
        state = random.getstate()
        for trace in ("vco_1.post_vca", "vco_2.post_vca", "noise.post_vca"):
            lane = family.render_lane(trace)
            with self.subTest(trace=trace):
                self.assertEqual(len(lane), float_mix.AUDIO_SAMPLES)
                self.assertEqual(lane, family.render_lane(trace))
        self.assertEqual(random.getstate(), state)

    def test_analytic_fixture_is_binary64_exact(self):
        lane = family.analytic_sine(family.ANALYTIC_HZ, 0.0)
        expected = [
            0.5 * math.sin(2.0 * math.pi * family.ANALYTIC_HZ * i / family.SAMPLE_RATE_HZ)
            for i in range(len(lane))
        ]
        self.assertEqual(lane, expected)


class BehaviorTests(unittest.TestCase):
    def test_gain_db_applies_declared_ratio_and_reports_affected_samples(self):
        samples = [family.f32(0.5), family.f32(-0.25), 0.0]
        output, detail = family.apply_gain_db(samples, 0.0)
        self.assertEqual(output, samples)
        self.assertEqual(detail["affected_samples"], 0)
        output, detail = family.apply_gain_db(samples, 60.0)
        self.assertEqual(output[0], family.f32(0.5 * family.f32(10.0 ** 3.0)))
        self.assertEqual(detail["affected_samples"], 2)

    def test_polarity_is_exact_negation(self):
        samples = [family.f32(0.5), family.f32(-0.25)]
        output, detail = family.apply_polarity(samples)
        self.assertEqual(output, [family.f32(-0.5), family.f32(0.25)])
        self.assertEqual(detail["affected_samples"], 2)

    def test_dc_offset_adds_declared_amplitude(self):
        output, detail = family.apply_dc_offset([family.f32(0.5)], 0.05)
        self.assertEqual(output, [family.f32(0.55)])
        self.assertEqual(detail["offset_amplitude"], 0.05)

    def test_round_step_reports_affected_sample_count(self):
        samples = [family.f32(0.09), family.f32(0.1), family.f32(0.17)]
        output, detail = family.apply_round_step(samples, 0.1)
        self.assertEqual(output, [family.f32(0.1), family.f32(0.1), family.f32(0.2)])
        self.assertEqual(detail["affected_samples"], 2)
        self.assertEqual(detail["mode"], "round-to-nearest")

    def test_saturation_clamps_at_declared_ceiling(self):
        samples = [family.f32(0.5), family.f32(-0.9), family.f32(0.1)]
        output, detail = family.apply_saturation(samples, 0.75)
        self.assertEqual(output, [family.f32(0.5), family.f32(-0.75), family.f32(0.1)])
        self.assertEqual(detail["affected_samples"], 1)
        self.assertEqual(detail["ceiling_amplitude"], 0.75)

    def test_mode_substitute_swaps_waveform_family(self):
        lane = family.render_lane("vco_1.post_vca")
        replacement, detail = family.apply_mode_substitute("vco_1.post_vca", lane)
        self.assertEqual(detail["mode_from"], "sine")
        self.assertEqual(detail["mode_to"], "squaresaw")
        self.assertNotEqual(replacement, lane)

    def test_norm_mutation_set_maps_declared_operators(self):
        self.assertEqual(family.norm_mutation_set("norm.always_on"), frozenset({float_mix.MUTATION_ALWAYS}))
        self.assertEqual(family.norm_mutation_set("norm.off"), frozenset({float_mix.MUTATION_NEVER}))
        self.assertEqual(family.norm_mutation_set("norm.wrong_peak"), frozenset({float_mix.MUTATION_WRONG_PEAK}))
        with self.assertRaises(mutations.MutationError):
            family.norm_mutation_set("gain.db")

    def test_wrong_reciprocal_gain_doubles_diagnostic_on_normalize_branch(self):
        peak = family.PEAK_ABOVE_ONE
        self.assertEqual(family.wrong_reciprocal_gain(peak, True), family.f32(2.0 / peak))
        self.assertEqual(family.wrong_reciprocal_gain(peak, False), 1.0)


class SessionTests(unittest.TestCase):
    def test_empty_plan_records_no_events_and_stays_complete(self):
        outcome = family.SignalFixtureSession(plan()).run(slot=0)
        self.assertEqual(outcome["events"], [])
        self.assertTrue(outcome["events_summary"]["complete"])

    def test_sham_traverses_and_returns_original(self):
        outcome = family.SignalFixtureSession(
            plan(family.instance("ms-sham", "signal.sham", "voice.post_module", configuration={"trace": "vco_1.post_vca", "slot": 0}))
        ).run(slot=0)
        clean = family.SignalFixtureSession(plan()).run(slot=0)
        self.assertEqual(outcome["lanes"], clean["lanes"])
        event = outcome["events"][0]
        self.assertTrue(event["sham"])
        self.assertEqual(event["status"], "ineffective")

    def test_tuning_fault_localizes_to_source_lane_and_downstream_mix(self):
        tuning_plan = plan(
            family.instance("ms-tune", "osc.tuning_shift", "voice.parameter_value", 1.0, {"parameter": "vco_1.tuning", "slot": 0})
        )
        faulted = family.SignalFixtureSession(tuning_plan).run(slot=0)
        clean = family.SignalFixtureSession(plan()).run(slot=0)
        self.assertNotEqual(faulted["lanes"]["vco_1.post_vca"], clean["lanes"]["vco_1.post_vca"])
        self.assertEqual(faulted["lanes"]["vco_2.post_vca"], clean["lanes"]["vco_2.post_vca"])
        self.assertEqual(faulted["lanes"]["noise.post_vca"], clean["lanes"]["noise.post_vca"])
        self.assertNotEqual(faulted["rendered"]["mixer.output"], clean["rendered"]["mixer.output"])
        summary = mutations.validate_events(tuning_plan, faulted["events"])
        self.assertTrue(summary["complete"])
        self.assertEqual(summary["applied"], 1)

    def test_norm_stage_requires_directed_clip(self):
        with self.assertRaises(mutations.MutationError):
            family.SignalFixtureSession(
                plan(family.instance("ms-norm", "norm.off", "voice.post_module", configuration={"trace": "mixer.output", "slot": 0}))
            ).run(slot=0)

    def test_cross_family_undeclared_combination_refused_at_plan_time(self):
        with self.assertRaises(mutations.MutationError) as caught:
            plan(
                family.instance("ms-gain", "gain.db", "voice.post_module", 1.0, {"trace": "vco_1.post_vca", "slot": 0}),
                family.instance("ms-norm", "norm.off", "voice.post_module", configuration={"trace": "mixer.output", "slot": 0}),
            )
        self.assertIn("undeclared combination", str(caught.exception))

    def test_declared_composition_in_wrong_stage_order_refuses(self):
        reordered = plan(
            family.instance("ms-gain", "gain.db", "voice.post_module", 1.0, {"trace": "vco_1.post_vca", "slot": 0}),
            family.instance("ms-tune", "osc.tuning_shift", "voice.parameter_value", 1.0, {"parameter": "vco_1.tuning", "slot": 0}),
        )
        with self.assertRaises(mutations.MutationError) as caught:
            family.SignalFixtureSession(reordered).run(slot=0)
        self.assertIn("fixture graph causality", str(caught.exception))

    def test_declared_composition_in_graph_order_executes_and_completes(self):
        composed = plan(
            family.instance("ms-tune", "osc.tuning_shift", "voice.parameter_value", 1.0, {"parameter": "vco_1.tuning", "slot": 0}),
            family.instance("ms-gain", "gain.db", "voice.post_module", 1.0, {"trace": "vco_1.post_vca", "slot": 0}),
        )
        outcome = family.SignalFixtureSession(composed).run(slot=0)
        self.assertEqual([event["status"] for event in outcome["events"]], ["applied", "applied"])
        self.assertTrue(outcome["events_summary"]["complete"])

    def test_wrong_reciprocal_keeps_audio_and_doubles_reported_gain(self):
        clip = family.directed_clip(family.PEAK_ABOVE_ONE)
        outcome = family.SignalFixtureSession(
            plan(family.instance("ms-recip", "norm.wrong_reciprocal", "voice.post_module", configuration={"trace": "mixer.output", "slot": 0}))
        ).run(slot=0, norm_clip=clip)
        clean_output, _, clean_gain, _ = float_mix.normalize_if_clipping(clip)
        self.assertEqual(outcome["rendered"]["mixer.output"], clean_output)
        peak = float_mix.peak_of_clip(clip)[0]
        self.assertEqual(outcome["rendered"]["mixer.gain"], [family.f32(2.0 / peak)])
        self.assertNotEqual(outcome["rendered"]["mixer.gain"], [clean_gain])


class PairingTests(unittest.TestCase):
    EXACT_LIMITS = pm.Rubric(
        "signal-family-exactness",
        "1",
        {
            "framing_match": pm.Limit(1, 0, "1", "fixture"),
            "exact_equal": pm.Limit(1, 0, "1", "fixture"),
            "max_abs_error": pm.Limit(0, 1e-9, "amplitude", "fixture"),
            "mean_error": pm.Limit(0, 1e-9, "amplitude", "fixture"),
        },
    )
    NORM_LIMITS = {
        "normalization.decision_match": pm.Limit(1, 0, "1", "fixture"),
        "normalization.rule_error_max": pm.Limit(0, 1e-9, "amplitude", "fixture"),
        "normalization.reciprocal_error": pm.Limit(0, 1e-9, "1", "fixture"),
        "normalization.reported_peak_error": pm.Limit(0, 1e-9, "amplitude", "fixture"),
    }

    def verdicts(self, reference, candidate):
        comparison = pm.compare_paired(
            reference,
            candidate,
            reference_rate_hz=family.SAMPLE_RATE_HZ,
            candidate_rate_hz=family.SAMPLE_RATE_HZ,
            unit="amplitude",
        )
        rows, _ = pm.scorecard_rows(
            comparison,
            case_id="directed:signal-family-tests",
            partition="development",
            trace="vco_1.post_vca",
            rubric=self.EXACT_LIMITS,
        )
        return {
            row["property"]: row["verdict"]
            for row in rows
            if row["property"] in self.EXACT_LIMITS.limits
        }

    def norm_verdicts(self, measurement):
        rows, _ = se.scorecard_rows(
            measurement,
            case_id="directed:signal-family-tests",
            trace="mixer.output",
            limits=self.NORM_LIMITS,
        )
        return {row["property"]: row["verdict"] for row in rows if row["property"] in self.NORM_LIMITS}

    def test_gain_fault_fails_rows_its_clean_control_passes(self):
        clean_lane = family.render_lane("vco_1.post_vca")
        self.assertTrue(all(verdict == "PASS" for verdict in self.verdicts(clean_lane, clean_lane).values()))
        faulted, _ = family.apply_gain_db(clean_lane, 1.0)
        verdicts = self.verdicts(clean_lane, faulted)
        self.assertEqual(verdicts["max_abs_error"], "FAIL")
        self.assertEqual(verdicts["exact_equal"], "FAIL")

    def test_always_on_fails_below_one_bypass_rows_control_passes(self):
        clip = family.directed_clip(family.PEAK_BELOW_ONE)
        clean_output, _, clean_gain, clean_branch = float_mix.normalize_if_clipping(clip)
        control = self.norm_verdicts(norm_measurement(clip, clean_output, clean_branch, clean_gain))
        self.assertTrue(all(verdict == "PASS" for verdict in control.values()))
        fault_output, _, fault_gain, fault_branch = float_mix.normalize_if_clipping(
            clip, {float_mix.MUTATION_ALWAYS}
        )
        faulted = self.norm_verdicts(norm_measurement(clip, fault_output, fault_branch, fault_gain))
        self.assertEqual(faulted["normalization.decision_match"], "FAIL")
        self.assertEqual(faulted["normalization.rule_error_max"], "FAIL")

    def test_wrong_reciprocal_fails_only_the_gain_row(self):
        clip = family.directed_clip(family.PEAK_ABOVE_ONE)
        fault_output, peak, _, fault_branch = float_mix.normalize_if_clipping(clip)
        faulted = self.norm_verdicts(
            norm_measurement(clip, fault_output, fault_branch, family.wrong_reciprocal_gain(peak, fault_branch))
        )
        self.assertEqual(faulted["normalization.reciprocal_error"], "FAIL")
        self.assertEqual(faulted["normalization.rule_error_max"], "PASS")
        self.assertEqual(faulted["normalization.decision_match"], "PASS")

    def test_rng_state_untouched_by_sessions(self):
        before = random.getstate()
        family.SignalFixtureSession(plan()).run(slot=0)
        family.SignalFixtureSession(
            plan(family.instance("ms-sham", "signal.sham", "voice.post_module", configuration={"trace": "vco_1.post_vca", "slot": 0}))
        ).run(slot=0)
        self.assertEqual(random.getstate(), before)


class PublicationTests(unittest.TestCase):
    def test_committed_publication_matches_fresh_rebuild(self):
        import json

        sys.path.insert(0, str(ROOT / "tools"))
        try:
            import qualify_mutations_signal

            committed = json.loads(qualify_mutations_signal.PUBLICATION_PATH.read_bytes())
            if importlib.util.find_spec("numpy") is None:
                refused = qualify_mutations_signal.analytic_periodic(
                    family.analytic_sine(family.ANALYTIC_HZ, 0.0), family.ANALYTIC_HZ
                )
                self.assertEqual(refused["status"], "invalid")
                self.assertEqual(
                    refused["reason"], "numpy_unavailable_install_metrics_extra"
                )
                with self.assertRaises(SystemExit) as caught:
                    qualify_mutations_signal.build_publication()
                message = caught.exception.code
                self.assertIn("family faults did not trip their rows", message)
                untripped = message.split("did not trip their rows: ", 1)[1].split(", ")
                self.assertEqual(
                    untripped,
                    ["osc.tuning_shift (property)", "osc.phase_offset (property)"],
                )
                return
            fresh = qualify_mutations_signal.build_publication()
            for field in qualify_mutations_signal.COMPARABLE_FIELDS:
                if field == "optional_observations":
                    # The band rms values are NumPy-FFT demonstration
                    # observations (declared tolerant, never a perceptual
                    # qualification); their last-ulp rendering is platform
                    # dependent while the declared verdict structure is not.
                    self.assertEqual(sorted(committed[field]), sorted(fresh[field]))
                    for fault, row in fresh[field].items():
                        with self.subTest(fault=fault):
                            declared = committed[field][fault]
                            self.assertEqual(row["metric"], declared["metric"])
                            self.assertEqual(row["tolerance"], declared["tolerance"])
                            self.assertEqual(row["optional"], declared["optional"])
                            self.assertEqual(row["tolerant"], declared["tolerant"])
                            self.assertTrue(math.isfinite(row["observed"]))
                            self.assertLessEqual(row["observed"], row["tolerance"])
                else:
                    self.assertEqual(committed.get(field), fresh[field], field)
        finally:
            sys.path.remove(str(ROOT / "tools"))
            sys.modules.pop("qualify_mutations_signal", None)


if __name__ == "__main__":
    unittest.main()
