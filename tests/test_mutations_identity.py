"""Identity/parameter/noise family contract tests (#31).

Stdlib-only, Torch-free apparatus tests over the landed #30 mutation
framework and the landed identity/conversion/noise contracts. Every fault
must trip its landed detector refusal while the clean control passes the
same check; provenance and parameter refusals happen before any perceptual
row is computed; wrong-slot and wrong-seed noise fail the exact identity
record while the statistical rows stay plausible and the blast radius stays
inside the declared noise lanes. Actual-Voice runtime injection is not
exercised here and is never substituted by these proofs; the ``bridge.*``
operators stay #30-owned test-only proofs.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import directed  # noqa: E402
from torchsynth_voice import float_sources  # noqa: E402
from torchsynth_voice import mutations  # noqa: E402
from torchsynth_voice import mutations_identity as mi  # noqa: E402
from torchsynth_voice import mutation_runtime  # noqa: E402
from torchsynth_voice.identity import SoundIdentity  # noqa: E402

CATALOG = mutations.load_seam_catalog(ROOT / mutations.SEAM_CATALOG_PATH)
mi.register_family()
# 2051 differs in both render_batch_index and render_batch_size under a
# 128-batch misprojection, so the provenance refusal names the coordinate.
REDUCED_CASE = str(mi.REPRESENTATIVE_INDICES[1])


def make_harness(case=REDUCED_CASE):
    return mi.IdentityFixtureHarness(ROOT, case)


class RegistrationTests(unittest.TestCase):
    def test_family_operators_are_registered_through_the_public_api(self):
        self.assertEqual(
            set(mi.FAMILY_OPERATORS) <= set(mutations.OPERATORS),
            True,
        )
        self.assertEqual(len(mi.FAMILY_OPERATORS), 6)
        for operator_id, definition in mi.FAMILY_OPERATORS.items():
            with self.subTest(operator=operator_id):
                self.assertEqual(mutations.OPERATORS[operator_id], definition)
                self.assertEqual(definition["seam"], mi.FAMILY_SEAM)
                self.assertTrue(CATALOG["seams"][mi.FAMILY_SEAM]["writable"])

    def test_bridge_operators_are_untouched_and_test_only(self):
        for operator_id in ("bridge.scale_slot", "bridge.scale_parameter"):
            self.assertIn("test-only", mutations.OPERATORS[operator_id]["summary"])

    def test_reregistration_with_drift_refuses(self):
        drifted = dict(mi.FAMILY_OPERATORS["noise.slot_shift"])
        drifted["summary"] = "drifted summary"
        with self.assertRaises(mutations.MutationError):
            mutations.register_operator("noise.slot_shift", drifted)

    def test_magnitude_domains_are_typed_with_native_units(self):
        self.assertEqual(
            mutations.OPERATORS["identity.batch_shift"]["magnitude"],
            {"type": "integer", "unit": "batch_size", "minimum": 32,
             "maximum": 4096},
        )
        self.assertEqual(
            mutations.OPERATORS["noise.slot_shift"]["magnitude"]["unit"], "slot"
        )
        self.assertEqual(
            mutations.OPERATORS["noise.seed_shift"]["magnitude"]["unit"],
            "seed_offset",
        )
        for operator_id in ("identity.train_test_flip", "param.positional_shuffle",
                            "param.conversion_substitute"):
            self.assertEqual(
                mutations.OPERATORS[operator_id]["magnitude"],
                {"type": "null", "unit": "none"},
            )


class PlanValidationTests(unittest.TestCase):
    def setUp(self):
        self.harness = make_harness()

    def test_family_plans_validate_through_make_plan(self):
        plan = self.harness.fault("mii-t", "noise.slot_shift", 5)
        self.assertTrue(plan["plan_id"].startswith("mp1-"))
        mutations.validate_plan(plan, CATALOG)

    def test_out_of_domain_magnitude_refused(self):
        with self.assertRaises(mutations.MutationError):
            self.harness.fault("mii-t", "noise.slot_shift", 0)
        with self.assertRaises(mutations.MutationError):
            self.harness.fault("mii-t", "noise.slot_shift", 32)
        with self.assertRaises(mutations.MutationError):
            self.harness.fault("mii-t", "noise.seed_shift", 20)
        with self.assertRaises(mutations.MutationError):
            self.harness.fault("mii-t", "identity.batch_shift", 16)

    def test_boolean_magnitude_refused(self):
        with self.assertRaises(mutations.MutationError) as caught:
            self.harness.fault("mii-t", "noise.slot_shift", True)
        self.assertIn("never bool", str(caught.exception))

    def test_family_operator_at_foreign_seam_refused(self):
        bad = mutation_runtime.instance(
            "mii-foreign", "noise.slot_shift", "voice.post_module", 5
        )
        with self.assertRaises(mutations.MutationError) as caught:
            mutations.make_plan(self.harness.binding(), [bad], self.harness.catalog)
        self.assertIn("does not support seam", str(caught.exception))

    def test_declared_noise_composition_allowed_and_foreign_refused(self):
        composed = mutations.make_plan(
            self.harness.binding(),
            [
                mutation_runtime.instance("mii-c1", "noise.slot_shift",
                                          mi.FAMILY_SEAM, 5),
                mutation_runtime.instance("mii-c2", "noise.seed_shift",
                                          mi.FAMILY_SEAM, 1),
            ],
            self.harness.catalog,
        )
        self.assertEqual(len(composed["mutations"]), 2)
        with self.assertRaises(mutations.MutationError) as caught:
            mutations.make_plan(
                self.harness.binding(),
                [
                    mutation_runtime.instance("mii-x1", "noise.slot_shift",
                                              mi.FAMILY_SEAM, 5),
                    mutation_runtime.instance("mii-x2", "identity.batch_shift",
                                              mi.FAMILY_SEAM, 128),
                ],
                self.harness.catalog,
            )
        self.assertIn("undeclared combination", str(caught.exception))


class DetectorOrderingTests(unittest.TestCase):
    """AC1/AC2: provenance and parameter refusals precede perceptual rows."""

    def setUp(self):
        self.harness = make_harness()

    def test_clean_control_passes_every_gate_and_reaches_perceptual_rows(self):
        rows, gate = mi.detector_pipeline(
            self.harness.document, self.harness.document,
            "directed:mutation-identity-test-control",
        )
        self.assertTrue(gate["perceptual_rows_reached"])
        for name in ("identity.provenance_match", "identity.train_test_match",
                     "parameter.first_wrong_position", "noise.identity",
                     "blast.exact_match"):
            self.assertEqual(mi._verdict_of(rows, name), "PASS", name)

    def test_batch_identity_fault_fails_provenance_before_perceptual_rows(self):
        attempt = self.harness.attempt(
            self.harness.fault("mii-batch", "identity.batch_shift", 128)
        )
        rows, gate = mi.detector_pipeline(
            mi.decode_fixture(attempt.store[mi.PAYLOAD_PATH]),
            self.harness.document,
            "directed:mutation-identity-test-batch",
        )
        self.assertEqual(mi._verdict_of(rows, "identity.provenance_match"), "FAIL")
        self.assertEqual(
            gate["provenance"]["first_wrong_identity_field"], "render_batch_index"
        )
        self.assertFalse(gate["perceptual_rows_reached"])
        self.assertIn("provenance refusal at render_batch_index", gate["gate_reason"])
        self.assertFalse(
            any(row["property"] == "noise.identity" for row in rows)
        )

    def test_train_test_flip_fails_its_named_detector_before_perceptual_rows(self):
        attempt = self.harness.attempt(
            self.harness.fault("mii-flip", "identity.train_test_flip")
        )
        rows, gate = mi.detector_pipeline(
            mi.decode_fixture(attempt.store[mi.PAYLOAD_PATH]),
            self.harness.document,
            "directed:mutation-identity-test-flip",
        )
        self.assertEqual(mi._verdict_of(rows, "identity.train_test_match"), "FAIL")
        self.assertFalse(gate["perceptual_rows_reached"])
        self.assertIn("is_train", gate["gate_reason"])

    def test_parameter_faults_name_the_first_wrong_canonical_parameter(self):
        names = mi.canonical_names()
        attempt = self.harness.attempt(
            self.harness.fault(
                "mii-shuffle",
                "param.positional_shuffle",
                configuration={"first": names[0], "second": names[1]},
            )
        )
        _, gate = mi.detector_pipeline(
            mi.decode_fixture(attempt.store[mi.PAYLOAD_PATH]),
            self.harness.document,
            "directed:mutation-identity-test-shuffle",
        )
        self.assertEqual(gate["parameter"]["first_wrong_position"], 0)
        self.assertEqual(
            gate["parameter"]["first_wrong_canonical_parameter"], names[0]
        )
        self.assertFalse(gate["perceptual_rows_reached"])

        modes = {}
        for position, name in enumerate(names):
            row = mi.inventory_rows()[name]
            if row["curve"] != 1 and "linear" not in modes:
                modes["linear"] = (position, name)
        attempt = self.harness.attempt(
            self.harness.fault(
                "mii-conversion",
                "param.conversion_substitute",
                configuration={"mode": "linear-where-curved"},
            )
        )
        _, gate = mi.detector_pipeline(
            mi.decode_fixture(attempt.store[mi.PAYLOAD_PATH]),
            self.harness.document,
            "directed:mutation-identity-test-conversion",
        )
        self.assertEqual(gate["parameter"]["first_wrong_position"],
                         modes["linear"][0])
        self.assertEqual(gate["parameter"]["first_wrong_canonical_parameter"],
                         modes["linear"][1])


class NoiseFaultTests(unittest.TestCase):
    """AC3/AC4: exact identity fails, statistics stay plausible, blast confined."""

    def setUp(self):
        self.harness = make_harness()

    def noise_fault_rows(self, operator_id, magnitude):
        attempt = self.harness.attempt(
            self.harness.fault("mii-noise", operator_id, magnitude)
        )
        document = mi.decode_fixture(attempt.store[mi.PAYLOAD_PATH])
        rows, gate = mi.detector_pipeline(
            document, self.harness.document,
            "directed:mutation-identity-test-noise",
            mi.EXPECTED_BLAST[operator_id],
        )
        return attempt, document, rows, gate

    def test_wrong_slot_fails_identity_while_statistics_stay_plausible(self):
        attempt, document, rows, gate = self.noise_fault_rows("noise.slot_shift", 5)
        events = attempt.events
        self.assertEqual([event["status"] for event in events], ["applied"])
        self.assertEqual(document["noise"]["slot"],
                         (SoundIdentity(self.harness.document["sound_index"]
                                        ).noise_slot + 5) % 32)
        self.assertEqual(mi._verdict_of(rows, "noise.identity"), "FAIL")
        for lag in mi.NOISE_LAGS:
            self.assertEqual(mi._verdict_of(rows, "noise.ac.%d" % lag), "PASS", lag)
        self.assertTrue(gate["perceptual_rows_reached"])
        self.assertEqual(set(gate["diverged_lanes"]), set(mi.NOISE_TRUTH_LANES))
        self.assertEqual(mi._verdict_of(rows, "blast.exact_match"), "PASS")

    def test_wrong_seed_fails_identity_and_landed_declared_seed_refusal(self):
        attempt, document, rows, gate = self.noise_fault_rows("noise.seed_shift", 1)
        self.assertEqual(document["noise"]["seed"], mi.NOISE_SEED + 1)
        self.assertEqual(mi._verdict_of(rows, "noise.identity"), "FAIL")
        for lag in mi.NOISE_LAGS:
            self.assertEqual(mi._verdict_of(rows, "noise.ac.%d" % lag), "PASS", lag)
        self.assertEqual(set(gate["diverged_lanes"]), set(mi.NOISE_TRUTH_LANES))
        witness = mi._resolved_request_witness(document)
        self.assertFalse(witness["accepted"])
        self.assertIn("seed 13", witness["refusal"])

    def test_ineffective_selector_records_ineffective_event(self):
        names = mi.canonical_names()
        attempt = self.harness.attempt(
            self.harness.fault(
                "mii-same",
                "param.positional_shuffle",
                configuration={"first": names[0], "second": names[0]},
            )
        )
        self.assertEqual(
            [event["status"] for event in attempt.events], ["ineffective"]
        )
        self.assertIsNone(attempt.errored)


class DirectedWitnessTests(unittest.TestCase):
    """The landed ResolvedRequest verdicts on full-length directed fixtures."""

    def test_resolved_request_accepts_clean_and_refuses_wrong_noise(self):
        harness = make_harness("directed-train")
        witness = mi._resolved_request_witness(harness.document)
        self.assertTrue(witness["accepted"], witness)

        attempt = harness.attempt(harness.fault("mii-slot", "noise.slot_shift", 5))
        witness = mi._resolved_request_witness(
            mi.decode_fixture(attempt.store[mi.PAYLOAD_PATH])
        )
        self.assertFalse(witness["accepted"])
        self.assertIn("must copy the resolved noise slot exactly", witness["refusal"])

        attempt = harness.attempt(harness.fault("mii-seed", "noise.seed_shift", 1))
        witness = mi._resolved_request_witness(
            mi.decode_fixture(attempt.store[mi.PAYLOAD_PATH])
        )
        self.assertFalse(witness["accepted"])
        self.assertIn("seed must be the declared seed 13", witness["refusal"])

    def test_name_keyed_interface_accepts_shuffle_witness(self):
        harness = make_harness("directed-test")
        names = mi.canonical_names()
        attempt = harness.attempt(
            harness.fault(
                "mii-witness-shuffle",
                "param.positional_shuffle",
                configuration={"first": names[2], "second": names[3]},
            )
        )
        witness = mi._resolved_request_witness(
            mi.decode_fixture(attempt.store[mi.PAYLOAD_PATH])
        )
        self.assertTrue(witness["accepted"])

    def test_truth_slice_matches_canonical_noise_resolve(self):
        sound_index = mi.DIRECTED_CASES["directed-train"]
        full = float_sources.NoiseSource.resolve(sound_index)
        truth = mi.slot_bytes(mi.NOISE_SEED, SoundIdentity(sound_index).noise_slot,
                              mi.SLICE_REDUCED)
        self.assertEqual(full[: len(truth)], truth)
        self.assertEqual(len(full), mi.SLICE_FULL * 4)


class ControlsAndCoverageTests(unittest.TestCase):
    def test_family_controls_hold(self):
        controls = mi.qualification_controls(ROOT)
        failed = [name for name, okay in controls.items() if not okay]
        self.assertEqual(failed, [])

    def test_every_fault_trips_on_every_representative_case(self):
        receipts = mi.representative_random_receipts(ROOT)
        self.assertEqual(len(receipts), 6 * len(mi.REPRESENTATIVE_INDICES))
        by_case = {}
        for receipt in receipts:
            by_case.setdefault(receipt["case"], set()).add(receipt["operator"])
            self.assertTrue(receipt["tripped"],
                            (receipt["case"], receipt["operator"]))
            self.assertTrue(receipt["control_accepted"])
        self.assertEqual(len(by_case), len(mi.REPRESENTATIVE_INDICES))
        designation = {
            str(index): SoundIdentity(index).is_train
            for index in mi.REPRESENTATIVE_INDICES
        }
        self.assertEqual(len(set(designation.values())), 2)
        slots = {
            SoundIdentity(index).noise_slot for index in mi.REPRESENTATIVE_INDICES
        }
        self.assertGreaterEqual(len(slots), 6)


if __name__ == "__main__":
    unittest.main()
