"""Timing/interpolation/envelope/modulation family contract tests (#32).

Stdlib-only, Torch-free apparatus tests over the landed #30 mutation
framework and the landed #120 property detectors. Every fault must trip
its landed detector refusal while the clean control passes the same
check; the ordinary/empty/sham controls stay byte-identical and the
declared-frame preflight refuses missing/duplicated samples before any
scoring. Actual-Voice runtime injection is not exercised here and is
never substituted by these proofs.

The periodic rows depend on the optional NumPy metrics extra. On a
stdlib-only host those rows are asserted as the declared detector
unavailability recorded by the family runner (unrun is never a PASS);
the dedicated numerical CI job asserts the full trips with NumPy
installed and single-threaded BLAS.
"""

import importlib.util
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import mutations  # noqa: E402
from torchsynth_voice import mutations_timing as mt  # noqa: E402
from torchsynth_voice import mutation_runtime  # noqa: E402
from torchsynth_voice import trace_capture  # noqa: E402

CATALOG = mutations.load_seam_catalog(ROOT / mutations.SEAM_CATALOG_PATH)
mt.register_family()


def make_harness(case="adsr-control"):
    return mt.TimingSignalHarness(ROOT, case)


def paired_verdict(expected, observed, rate, lane):
    rows, _ = mt.paired_rows(expected, observed, rate, "1",
                             "directed:mutation-timing-test", lane)
    return mt.verdict_of(rows, "exact_equal")


class RegistrationTests(unittest.TestCase):
    def test_family_operators_are_registered_through_the_public_api(self):
        self.assertEqual(
            set(mt.FAMILY_OPERATORS) <= set(mutations.OPERATORS),
            True,
        )
        self.assertEqual(len(mt.FAMILY_OPERATORS), 12)
        for operator_id, definition in mt.FAMILY_OPERATORS.items():
            with self.subTest(operator=operator_id):
                self.assertEqual(mutations.OPERATORS[operator_id], definition)
                self.assertEqual(definition["seam"], mt.FAMILY_SEAM)
                self.assertTrue(CATALOG["seams"][mt.FAMILY_SEAM]["writable"])

    def test_bridge_operators_are_untouched_and_test_only(self):
        for operator_id in ("bridge.scale_slot", "bridge.scale_parameter"):
            self.assertIn("test-only", mutations.OPERATORS[operator_id]["summary"])

    def test_reregistration_with_drift_refuses(self):
        drifted = dict(mt.FAMILY_OPERATORS["timing.drop_sample"])
        drifted["summary"] = "drifted summary"
        with self.assertRaises(mutations.MutationError):
            mutations.register_operator("timing.drop_sample", drifted)

    def test_magnitude_domains_are_typed_with_native_units(self):
        self.assertEqual(
            mutations.OPERATORS["envelope.breakpoint_shift"]["magnitude"],
            {"type": "number", "unit": "control_sample", "minimum": -8,
             "maximum": 8},
        )
        self.assertEqual(
            mutations.OPERATORS["modulation.lfo_rate_shift"]["magnitude"]["unit"],
            "hz",
        )
        self.assertEqual(
            mutations.OPERATORS["timing.drop_sample"]["magnitude"],
            {"type": "integer", "unit": "sample", "minimum": 1, "maximum": 1},
        )


class PlanValidationTests(unittest.TestCase):
    def setUp(self):
        self.harness = make_harness()

    def test_family_plans_validate_through_make_plan(self):
        plan = self.harness.fault("mti-t", "envelope.breakpoint_shift", 2.0,
                                  {"breakpoint": "attack"})
        self.assertTrue(plan["plan_id"].startswith("mp1-"))
        mutations.validate_plan(plan, CATALOG)

    def test_unknown_lane_configuration_refused(self):
        with self.assertRaises(mutations.MutationError) as caught:
            self.harness.fault("mti-t", "timing.drop_sample", 1,
                               {"lane": "analytic.not_a_lane", "index": 0})
        self.assertIn("configuration value not allowed", str(caught.exception))

    def test_cross_domain_lane_refused(self):
        with self.assertRaises(mutations.MutationError) as caught:
            self.harness.fault("mti-t", "timing.delay_audio_sample", 1,
                               {"lane": mt.LANE_ADSR})
        self.assertIn("configuration value not allowed", str(caught.exception))

    def test_unknown_breakpoint_refused(self):
        with self.assertRaises(mutations.MutationError):
            self.harness.fault("mti-t", "envelope.breakpoint_shift", 1.0,
                               {"breakpoint": "sustain"})

    def test_out_of_domain_magnitude_refused(self):
        with self.assertRaises(mutations.MutationError):
            self.harness.fault("mti-t", "envelope.breakpoint_shift", 9.0,
                               {"breakpoint": "attack"})
        with self.assertRaises(mutations.MutationError):
            self.harness.fault("mti-t", "timing.drop_sample", 2,
                               {"lane": mt.LANE_ADSR, "index": 0})

    def test_boolean_magnitude_refused(self):
        with self.assertRaises(mutations.MutationError) as caught:
            self.harness.fault("mti-t", "timing.drop_sample", True,
                               {"lane": mt.LANE_ADSR, "index": 0})
        self.assertIn("never bool", str(caught.exception))

    def test_family_operator_at_foreign_seam_refused(self):
        bad = mutation_runtime.instance(
            "mti-t", "timing.drop_sample", "apparatus.artifact_write", 1,
            {"lane": mt.LANE_ADSR, "index": 0})
        with self.assertRaises(mutations.MutationError) as caught:
            mutations.make_plan(self.harness.binding(), [bad], CATALOG)
        self.assertIn("does not support seam", str(caught.exception))

    def test_plan_identity_tracks_magnitude_and_configuration(self):
        ids = {
            self.harness.fault("mti-x", "envelope.breakpoint_shift", magnitude,
                               {"breakpoint": "attack"})["plan_id"]
            for magnitude in (0.5, 1.0)
        }
        self.assertEqual(len(ids), 2)
        other = self.harness.fault("mti-x", "envelope.breakpoint_shift", 0.5,
                                   {"breakpoint": "decay"})
        self.assertNotIn(other["plan_id"], ids)

    def test_undeclared_family_combination_refused(self):
        first = mutation_runtime.instance("mti-a", "interp.off_endpoint",
                                          mt.FAMILY_SEAM,
                                          configuration={
                                              "control_lane": mt.LANE_CONTROL_ENDPOINT,
                                              "upsampled_lane": mt.LANE_CONTROL_UPSAMPLE})
        second = mutation_runtime.instance("mti-b", "timing.delay_audio_sample",
                                           mt.FAMILY_SEAM, 1,
                                           {"lane": mt.LANE_CONTROL_UPSAMPLE})
        with self.assertRaises(mutations.MutationError) as caught:
            mutations.make_plan(self.harness.binding(), [first, second], CATALOG)
        self.assertIn("undeclared combination", str(caught.exception))


class FixtureTests(unittest.TestCase):
    def test_fixture_build_is_deterministic(self):
        first = mt.encode_fixture(mt.build_fixture("adsr-control"))
        second = mt.encode_fixture(mt.build_fixture("adsr-control"))
        self.assertEqual(first, second)

    def test_lanes_match_declared_frames(self):
        for case in mt.CASES:
            document = mt.build_fixture(case)
            for name, frame in mt.CASES[case]["lanes"].items():
                lane = document["lanes"][name]
                self.assertEqual(lane["rate_hz"], frame["rate_hz"], name)
                self.assertEqual(lane["sample_count"],
                                 len(mt.lane_samples(document, name)), name)

    def test_preflight_accepts_pristine_and_refuses_frame_faults(self):
        pristine = mt.decode_fixture(make_harness().payload)
        self.assertEqual(mt.frame_preflight(pristine, "adsr-control"), [])
        samples = mt.lane_samples(pristine, mt.LANE_ADSR)
        mt.write_lane(pristine, mt.LANE_ADSR, samples[:-1])
        refusals = mt.frame_preflight(pristine, "adsr-control")
        self.assertEqual(len(refusals), 1)
        self.assertIn("declared frame preflight refusal", refusals[0])
        self.assertIn(mt.LANE_ADSR, refusals[0])
        self.assertIn("239", refusals[0])


class ControlTests(unittest.TestCase):
    def test_ordinary_empty_and_sham_attempts_are_byte_identical(self):
        harness = make_harness()
        plain = harness.plain_attempt()
        empty = harness.attempt(harness.empty_plan())
        sham = harness.attempt(harness.sham_plan())
        for other in (empty, sham):
            self.assertEqual(plain.store, other.store)
        self.assertIsNone(empty.errored)
        self.assertIsNone(sham.errored)

    def test_sham_is_marked_and_never_effective(self):
        sham = make_harness().attempt(make_harness().sham_plan())
        self.assertEqual(len(sham.events), 1)
        self.assertTrue(sham.events[0]["sham"])
        self.assertEqual(sham.events[0]["status"], "ineffective")

    def test_global_rng_state_untouched_and_clean_rerun_after_crash(self):
        controls = mt.qualification_controls(ROOT)
        self.assertTrue(controls["global_rng_untouched_by_attempts"])
        self.assertTrue(controls["cleanup_after_producer_crash"])
        self.assertTrue(controls["plain_vs_empty_plan_bytes_identical"])
        self.assertTrue(controls["plain_vs_sham_bytes_identical"])
        self.assertTrue(controls["sibling_case_bytes_unchanged_by_faulted_case"])

    def test_family_fault_events_localize_to_the_declared_seam(self):
        harness = make_harness()
        attempt = harness.attempt(
            harness.fault("mti-t", "envelope.breakpoint_shift", 1.0,
                          {"breakpoint": "attack"}))
        self.assertEqual([event["seam"] for event in attempt.events],
                         [mt.FAMILY_SEAM])
        self.assertEqual(attempt.events[0]["status"], "applied")
        detail = attempt.events[0]["detail"]
        self.assertEqual(detail["breakpoint"], "attack")
        self.assertNotEqual(detail["original_lane_sha256"],
                            detail["replacement_lane_sha256"])

    def test_selector_mismatch_records_ineffective(self):
        harness = make_harness("upsample-audio")
        plan = harness.fault("mti-t", "timing.delay_control_sample", 1,
                             {"lane": mt.LANE_LFO})
        attempt = harness.attempt(plan)
        self.assertEqual([event["status"] for event in attempt.events],
                         ["ineffective"])
        self.assertEqual(attempt.events[0]["detail"]["reason"],
                         "selector did not match")
        self.assertEqual(attempt.store, harness.plain_attempt().store)


class TimingFaultTests(unittest.TestCase):
    def test_control_sample_delay_stays_visible_in_unaligned_primary_row(self):
        harness = make_harness()
        attempt = harness.attempt(
            harness.fault("mti-t", "timing.delay_control_sample", 1,
                          {"lane": mt.LANE_ADSR}))
        expected = mt.lane_samples(harness.document, mt.LANE_ADSR)
        self.assertEqual(
            paired_verdict(expected, expected, mt.CONTROL_RATE_HZ, mt.LANE_ADSR),
            "PASS")
        self.assertEqual(
            paired_verdict(expected,
                           mt.lane_samples(mt._attempt_document(attempt),
                                           mt.LANE_ADSR),
                           mt.CONTROL_RATE_HZ, mt.LANE_ADSR),
            "FAIL")

    def test_missing_and_duplicated_samples_fail_structural_preflight(self):
        harness = make_harness()
        for operator_id in ("timing.drop_sample", "timing.duplicate_sample"):
            with self.subTest(operator=operator_id):
                attempt = harness.attempt(
                    harness.fault("mti-t", operator_id, 1,
                                  {"lane": mt.LANE_ADSR, "index": 40}))
                document = mt._attempt_document(attempt)
                refusals = mt.frame_preflight(document, "adsr-control")
                self.assertTrue(refusals, operator_id)
                rows, comparison = mt.paired_rows(
                    mt.lane_samples(harness.document, mt.LANE_ADSR),
                    mt.lane_samples(document, mt.LANE_ADSR),
                    mt.CONTROL_RATE_HZ, "1", "test", mt.LANE_ADSR)
                self.assertNotEqual(comparison["pair_status"], "measured")
                self.assertEqual(comparison["metrics"]["framing_match"]["status"],
                                 "valid")
                self.assertEqual(comparison["metrics"]["framing_match"]["value"], 0)


class InterpolationFaultTests(unittest.TestCase):
    def test_zoh_alters_control_derived_trace_and_not_source(self):
        harness = make_harness("upsample-audio")
        attempt = harness.attempt(
            harness.fault("mti-t", "interp.zoh_control",
                          configuration={"control_lane": mt.LANE_CONTROL_ENDPOINT,
                                         "upsampled_lane": mt.LANE_CONTROL_UPSAMPLE}))
        document = mt._attempt_document(attempt)
        self.assertNotEqual(
            mt._lane_digest(mt.lane_samples(document, mt.LANE_CONTROL_UPSAMPLE)),
            mt._lane_digest(mt.lane_samples(harness.document,
                                            mt.LANE_CONTROL_UPSAMPLE)))
        self.assertEqual(
            mt._lane_digest(mt.lane_samples(document, mt.LANE_CONTROL_ENDPOINT)),
            mt._lane_digest(mt.lane_samples(harness.document,
                                            mt.LANE_CONTROL_ENDPOINT)))
        self.assertEqual(document["parameters"], harness.document["parameters"])
        expected = mt.lane_samples(harness.document, mt.LANE_CONTROL_UPSAMPLE)
        self.assertEqual(
            paired_verdict(expected, expected, mt.AUDIO_RATE_HZ,
                           mt.LANE_CONTROL_UPSAMPLE),
            "PASS")
        self.assertEqual(
            paired_verdict(expected,
                           mt.lane_samples(document, mt.LANE_CONTROL_UPSAMPLE),
                           mt.AUDIO_RATE_HZ, mt.LANE_CONTROL_UPSAMPLE),
            "FAIL")

    def test_off_endpoint_fails_landed_endpoint_contract(self):
        harness = make_harness("upsample-audio")
        attempt = harness.attempt(
            harness.fault("mti-t", "interp.off_endpoint",
                          configuration={"control_lane": mt.LANE_CONTROL_ENDPOINT,
                                         "upsampled_lane": mt.LANE_CONTROL_UPSAMPLE}))
        document = mt._attempt_document(attempt)
        control = mt.lane_samples(document, mt.LANE_CONTROL_ENDPOINT)
        upsampled = mt.lane_samples(document, mt.LANE_CONTROL_UPSAMPLE)
        with self.assertRaises(ValueError) as caught:
            trace_capture.check_endpoint_bytes(
                struct.pack("<%df" % len(control), *control),
                struct.pack("<%df" % len(upsampled), *upsampled),
                input_count=int(document["parameters"]["control_count"]),
                output_count=int(document["parameters"]["output_count"]),
            )
        self.assertIn("endpoint loss", str(caught.exception))


class DetectorFaultTests(unittest.TestCase):
    def test_envelope_breakpoint_shift_is_identified_against_declared_truth(self):
        harness = make_harness()
        params = mt.CASES["adsr-control"]
        control_rows, _ = mt.envelope_rows(
            mt.lane_samples(harness.document, mt.LANE_ADSR), params, "test-control")
        self.assertEqual(mt.verdict_of(control_rows, "attack_end"), "PASS")
        attempt = harness.attempt(
            harness.fault("mti-t", "envelope.breakpoint_shift", 2.0,
                          {"breakpoint": "attack"}))
        fault_rows, _ = mt.envelope_rows(
            mt.lane_samples(mt._attempt_document(attempt), mt.LANE_ADSR),
            params, "test-fault")
        self.assertEqual(mt.verdict_of(fault_rows, "attack_end"), "FAIL")
        self.assertEqual(mt.verdict_of(fault_rows, "decay_end"), "FAIL")

    def test_route_sign_and_depth_defects_are_identified(self):
        harness = make_harness("route-control")
        params = mt.CASES["route-control"]
        control_rows, _ = mt.route_rows(harness.document, params, "test-control")
        for name in params["gains"]:
            self.assertEqual(mt.verdict_of(control_rows, "gain." + name), "PASS")
        sign_attempt = harness.attempt(
            harness.fault("mti-t", "modulation.route_sign_flip",
                          configuration={"destination": "vco_1_pitch"}))
        sign_rows, _ = mt.route_rows(mt._attempt_document(sign_attempt), params,
                                     "test-fault")
        self.assertEqual(mt.verdict_of(sign_rows, "gain.vco_1_pitch"), "FAIL")
        depth_attempt = harness.attempt(
            harness.fault("mti-t2", "modulation.route_depth_shift", 0.25,
                          {"destination": "vco_1_amp"}))
        depth_rows, _ = mt.route_rows(mt._attempt_document(depth_attempt), params,
                                      "test-fault2")
        self.assertEqual(mt.verdict_of(depth_rows, "gain.vco_1_amp"), "FAIL")
        self.assertEqual(mt.verdict_of(depth_rows, "gain.noise_amp"), "PASS")

    def test_route_swap_moves_measured_gain_between_destinations(self):
        harness = make_harness("route-control")
        params = mt.CASES["route-control"]
        attempt = harness.attempt(
            harness.fault("mti-t", "modulation.route_swap",
                          configuration={"left": "vco_2_pitch",
                                         "right": "vco_2_amp"}))
        rows, _ = mt.route_rows(mt._attempt_document(attempt), params, "test")
        self.assertEqual(mt.verdict_of(rows, "gain.vco_2_pitch"), "FAIL")
        self.assertEqual(mt.verdict_of(rows, "gain.vco_2_amp"), "FAIL")

    def test_lfo_rate_and_depth_defects_are_identified(self):
        harness = make_harness("lfo-control")
        params = mt.CASES["lfo-control"]
        control_rows, control_measurement = mt.lfo_rows(
            mt.lane_samples(harness.document, mt.LANE_LFO), params,
            "test-control", ROOT)
        if control_measurement["status"] != "valid":
            self.assertIn("numpy", control_measurement["reason"])
            return
        self.assertEqual(mt.verdict_of(control_rows, "frequency_hz"), "PASS")
        self.assertEqual(mt.verdict_of(control_rows, "depth_peak_to_peak"),
                         "PASS")
        rate_attempt = harness.attempt(
            harness.fault("mti-t", "modulation.lfo_rate_shift", 0.5))
        rate_rows, _ = mt.lfo_rows(
            mt.lane_samples(mt._attempt_document(rate_attempt), mt.LANE_LFO),
            params, "test-fault", ROOT)
        self.assertEqual(mt.verdict_of(rate_rows, "frequency_hz"), "FAIL")
        depth_attempt = harness.attempt(
            harness.fault("mti-t2", "modulation.lfo_depth_shift", 0.1))
        depth_rows, _ = mt.lfo_rows(
            mt.lane_samples(mt._attempt_document(depth_attempt), mt.LANE_LFO),
            params, "test-fault2", ROOT)
        self.assertEqual(mt.verdict_of(depth_rows, "depth_peak_to_peak"), "FAIL")


class FloorAndCoverageTests(unittest.TestCase):
    def test_small_mutation_demonstrates_measured_sensitivity_floor(self):
        probes = mt.sensitivity_floor(ROOT)
        by_name = {probe["probe"]: probe for probe in probes}
        detected = by_name["attack-breakpoint-shift-0.03"]
        blind = by_name["attack-breakpoint-shift-0.005"]
        self.assertTrue(detected["detected"])
        self.assertGreater(detected["measured_error"], mt.ENVELOPE_COORD_BOUND)
        self.assertFalse(blind["detected"])
        self.assertLess(blind["measured_error"], mt.ENVELOPE_COORD_BOUND)
        for probe in probes:
            self.assertEqual(probe["detected"], probe["expected_detected"])

    def test_degenerate_envelope_and_high_rate_coverage(self):
        coverage = mt.coverage_cases(ROOT)
        rows = coverage["envelope_and_high_rate"]
        by_case = {row["coverage_case"]: row for row in rows}
        self.assertEqual(by_case["attack-one-sample"]["verdict"], "NO VERDICT")
        self.assertEqual(by_case["silent-sustain"]["verdict"], "NO VERDICT")
        self.assertEqual(by_case["flat-sustain"]["verdict"], "NO VERDICT")
        self.assertEqual(by_case["zero-release"]["verdict"], "NO VERDICT")
        self.assertIn("zero_release", by_case["zero-release"]["reason"])
        high_rate = by_case["high-rate-lfo-20hz"]
        if importlib.util.find_spec("numpy") is None:
            self.assertEqual(high_rate["verdict"], "NO VERDICT")
            self.assertNotEqual(high_rate["reason"], "valid")
        else:
            self.assertEqual(high_rate["verdict"], "PASS")
            self.assertEqual(high_rate["reason"], "valid")
        self.assertEqual(
            by_case["one-audio-sample-delay-visibility"]["verdict"], "FAIL")


class MatrixAndEvidenceTests(unittest.TestCase):
    matrix = None
    records = None

    @classmethod
    def setUpClass(cls):
        cls.matrix, cls.records = mt.fault_matrix(ROOT)

    def test_every_family_fault_trips_with_a_passing_control(self):
        self.assertGreaterEqual(len(self.matrix), 12)
        unavailable = 0
        for entry in self.matrix:
            with self.subTest(fault=entry["fault"]):
                if (entry["observed_refusal"] or "").startswith(
                        "periodic detector unavailable"):
                    unavailable += 1
                    self.assertFalse(entry["control_accepted"], entry["fault"])
                    self.assertFalse(entry["tripped"], entry["fault"])
                    self.assertIn("numpy", entry["observed_refusal"])
                else:
                    self.assertTrue(entry["control_accepted"], entry["fault"])
                    self.assertTrue(entry["tripped"], entry["fault"])
        self.assertLessEqual(unavailable, 2)

    def test_composed_plan_runs_in_declared_order(self):
        harness = make_harness("upsample-audio")
        plan = mutations.make_plan(
            harness.binding(),
            [
                mutation_runtime.instance(
                    "mti-zoh", "interp.zoh_control", mt.FAMILY_SEAM,
                    configuration={"control_lane": mt.LANE_CONTROL_ENDPOINT,
                                   "upsampled_lane": mt.LANE_CONTROL_UPSAMPLE}),
                mutation_runtime.instance("mti-delay", "timing.delay_audio_sample",
                                          mt.FAMILY_SEAM, 1,
                                          {"lane": mt.LANE_CONTROL_UPSAMPLE}),
            ],
            CATALOG,
        )
        attempt = harness.attempt(plan)
        self.assertEqual([event["status"] for event in attempt.events],
                         ["applied", "applied"])
        summary = mutations.validate_events(plan, attempt.events)
        self.assertTrue(summary["complete"])

    def test_family_evidence_envelope_binds_plan_events_and_artifacts(self):
        harness = make_harness()
        plan = harness.fault("mti-t", "envelope.breakpoint_shift", 1.0,
                             {"breakpoint": "attack"})
        attempt = harness.attempt(plan)
        envelope = mutations.make_envelope(
            plan=plan,
            events_summary=attempt.events_summary,
            artifacts=[
                {"path": path, "sha256": mutation_runtime.sha256(data),
                 "size_bytes": len(data)}
                for path, data in sorted(attempt.store.items())
            ],
            controls={"family_controls_fixture": True},
            fault_matrix=self.matrix[:1],
            runtime_scope="stdlib-apparatus-analytic-fixtures",
            not_run=["actual-Voice runtime injection of family operators"],
        )
        self.assertTrue(envelope["envelope_id"].startswith("mu1-"))
        self.assertEqual(mutations.validate_envelope(envelope),
                         envelope["envelope_id"])

    def test_publication_rebuild_is_deterministic(self):
        controls_a = mt.qualification_controls(ROOT)
        controls_b = mt.qualification_controls(ROOT)
        self.assertEqual(controls_a, controls_b)
        self.assertTrue(all(controls_a.values()))


if __name__ == "__main__":
    unittest.main()
