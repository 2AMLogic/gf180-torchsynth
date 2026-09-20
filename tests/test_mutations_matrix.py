"""Bidirectional mutation-coverage matrix contract tests; stdlib-only.

These tests bind the committed #34 matrix publication
(``sim/reference/mutation-matrix-v1.json``) to the landed operator registry:
every faults-to-tests row must name registered operators of exactly one
family with a matching declared seam topology, every mandatory detector must
name at least one expected fault with wrong-then-right counts, the
deterministic CI subset must cover identity, delay, interpolation, gain and
normalization, below-floor and out-of-applicability evidence must stay
labeled ``sensitivity-NO VERDICT`` (never passes), and no scalar mutation
score may replace the rows. The full rerun (the family ``--check``
executions the matrix composes) is re-established by
``tools/qualify_mutations_matrix.py`` in CI; absence of the committed
publication is a failure here, never a pass.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import mutations  # noqa: E402
from torchsynth_voice import mutations_identity  # noqa: E402
from torchsynth_voice import mutations_signal  # noqa: E402
from torchsynth_voice import mutations_timing  # noqa: E402

MATRIX_PATH = ROOT / "sim/reference/mutation-matrix-v1.json"
CI_CATEGORIES = ("identity", "delay", "interpolation", "gain", "normalization")


def load_matrix():
    if not MATRIX_PATH.exists():
        raise AssertionError(
            "matrix publication is ABSENT — requires "
            "tools/qualify_mutations_matrix.py; absence is never a pass"
        )
    return json.loads(MATRIX_PATH.read_bytes())


def register_families():
    mutations_identity.register_family()
    mutations_timing.register_family()
    mutations_signal.register_family()


class MatrixPublicationTests(unittest.TestCase):
    def test_committed_matrix_is_present_and_passing(self):
        matrix = load_matrix()
        self.assertEqual(matrix["kind"], "mutation-matrix-v1")
        self.assertEqual(matrix["schema_version"], 1)
        self.assertEqual(matrix["status"], "PASS")
        self.assertEqual(matrix["mutation_contract"], "mutation-v1")

    def test_every_fault_row_names_registered_single_family_operators(self):
        matrix = load_matrix()
        register_families()
        for row in matrix["faults_to_tests"]:
            with self.subTest(fault=row["fault"]):
                self.assertTrue(row["operators"])
                for operator_id in row["operators"]:
                    self.assertIn(operator_id, mutations.OPERATORS)
                self.assertIn(row["family"], ("identity", "timing", "signal"))
                self.assertEqual(row["validity"], "detected")
                self.assertEqual(row["missed_faults"], [])
                applicability = row["applicability"]
                self.assertTrue(
                    applicability["directed_cases"]
                    or applicability.get("note", "").strip()
                )
                self.assertTrue(
                    (ROOT / row["links"]["publication"]).exists(),
                    "row links a missing family publication",
                )
                if applicability["directed_cases"]:
                    self.assertTrue(row["links"]["envelope_ids"])

    def test_family_applicability_surfaces_are_populated(self):
        matrix = load_matrix()
        surfaces = matrix["applicability_surfaces"]
        self.assertEqual(set(surfaces), {"identity", "timing", "signal"})
        self.assertGreaterEqual(surfaces["identity"]["directed_cases"], 2)
        self.assertGreaterEqual(
            surfaces["identity"]["representative_random_cases"], 1
        )
        self.assertGreaterEqual(surfaces["timing"]["coverage_cases"], 1)
        self.assertGreaterEqual(surfaces["timing"]["floor_probes"], 2)
        self.assertTrue(surfaces["signal"]["directed_fixture"])
        self.assertEqual(
            set(surfaces["signal"]["normalization_classes"]),
            {"above", "at", "below"},
        )

    def test_row_seam_topology_matches_registry_or_plan_time_refusal(self):
        matrix = load_matrix()
        register_families()
        catalog = mutations.load_seam_catalog(ROOT / mutations.SEAM_CATALOG_PATH)
        for row in matrix["faults_to_tests"]:
            with self.subTest(fault=row["fault"]):
                if "fail-closed at plan time" in row["links"]["downstream"]:
                    self.assertFalse(catalog["seams"][row["seam"]]["writable"])
                else:
                    seams = {
                        mutations.OPERATORS[operator_id]["seam"]
                        for operator_id in row["operators"]
                    }
                    self.assertEqual(seams, {row["seam"]})

    def test_every_mandatory_detector_names_faults_with_counts(self):
        matrix = load_matrix()
        faults = {row["fault"] for row in matrix["faults_to_tests"]}
        self.assertTrue(matrix["tests_to_faults"])
        named = set()
        for detector in matrix["tests_to_faults"]:
            with self.subTest(detector=detector["detector"][:60]):
                self.assertTrue(detector["expected_faults"])
                self.assertTrue(set(detector["expected_faults"]) <= faults)
                named |= set(detector["expected_faults"])
                counts = detector["wrong_then_right"]
                self.assertEqual(
                    counts["wrong_observed"], len(detector["expected_faults"])
                )
                self.assertEqual(counts["right_observed"], counts["wrong_observed"])
                self.assertTrue(detector["baseline"].strip())
        self.assertEqual(named, faults)

    def test_deterministic_ci_subset_covers_required_categories(self):
        matrix = load_matrix()
        subset = matrix["ci_subset"]
        self.assertEqual(
            set(subset["categories"]), set(CI_CATEGORIES)
        )
        faults = {row["fault"]: row for row in matrix["faults_to_tests"]}
        for category in CI_CATEGORIES:
            rows = subset["categories"][category]
            with self.subTest(category=category):
                self.assertTrue(rows, "empty CI subset category")
                for fault in rows:
                    self.assertIn(fault, faults)
                    self.assertEqual(faults[fault]["validity"], "detected")
        self.assertTrue(subset["all_detected"])
        self.assertTrue(subset["rows"])
        self.assertTrue(set(subset["rows"]) <= set(faults))

    def test_below_floor_and_out_of_applicability_stay_no_verdict(self):
        matrix = load_matrix()
        sensitivity = matrix["sensitivity"]
        self.assertIn("sensitivity-NO VERDICT", sensitivity["policy"])
        labelled = (
            sensitivity["timing_floor_probes"]
            + sensitivity["timing_coverage_rows"]
            + sensitivity["signal_normalization_cells"]
        )
        self.assertTrue(labelled)
        for entry in labelled:
            self.assertIn(
                entry["validity"], ("detected", "sensitivity-NO VERDICT")
            )
        no_verdict = [
            entry
            for entry in labelled
            if entry["validity"] == "sensitivity-NO VERDICT"
        ]
        self.assertTrue(
            no_verdict,
            "the matrix must carry below-floor sensitivity rows",
        )
        below_floor = [
            probe
            for probe in sensitivity["timing_floor_probes"]
            if not probe["detected"]
        ]
        self.assertTrue(below_floor)
        for probe in below_floor:
            self.assertEqual(probe["validity"], "sensitivity-NO VERDICT")
            self.assertLess(
                probe["magnitude_control_samples"],
                probe["qualified_resolution_control_samples"],
            )
        self.assertEqual(matrix["counts"]["missed_faults"], 0)

    def test_composition_gate_proofs_record_wrong_then_right(self):
        matrix = load_matrix()
        composition = matrix["composition_coverage"]
        self.assertEqual(composition["cross_family_declared_pairs"], [])
        proofs = composition["gate_proofs"]
        self.assertGreaterEqual(len(proofs["wrong_refused"]), 3)
        for proof in proofs["wrong_refused"]:
            self.assertIn("undeclared combination", proof["refusal"])
            self.assertNotEqual(
                proof["left"].split(".")[0], proof["right"].split(".")[0]
            )
        accepted = proofs["right_accepted"]
        self.assertTrue(accepted["plan_id"].startswith("mp1-"))
        executed = composition["executed_composed_rows"]
        self.assertTrue(executed)
        for row in executed:
            self.assertEqual(row["validity"], "detected")
            self.assertGreaterEqual(len(row["operators"]), 2)

    def test_localization_matches_injection_topology(self):
        matrix = load_matrix()
        localization = matrix["localization_topology"]
        self.assertTrue(localization["signal_trace_records"])
        for fault, record in localization["signal_trace_records"].items():
            with self.subTest(fault=fault):
                self.assertTrue(record["topology_match"])
                self.assertEqual(len(record["source_traces_changed"]), 1)
                self.assertTrue(record["downstream_mix_changed"])
        for family, okay in localization["sibling_invariance_controls"].items():
            self.assertTrue(okay, family + " sibling invariance control")
        self.assertTrue(localization["row_seam_matches_registry"])

    def test_registry_counts_match_the_landed_modules(self):
        matrix = load_matrix()
        register_families()
        registry = matrix["registry"]
        self.assertEqual(
            registry["total_registered_operators"], len(mutations.OPERATORS)
        )
        family_operators = sum(
            len(operators)
            for family, operators in registry["families"].items()
            if family != "framework"
        )
        self.assertEqual(registry["family_operators"], family_operators)
        for family, operators in registry["families"].items():
            for operator_id in operators:
                self.assertIn(operator_id, mutations.OPERATORS)

    def test_no_scalar_mutation_score_replaces_rows(self):
        matrix = load_matrix()
        self.assertIn("no scalar mutation score", matrix["score_policy"])
        for key in matrix:
            self.assertNotIn("score", key.replace("score_policy", ""))

    def test_holdout_and_downstream_consumers_stay_not_run(self):
        matrix = load_matrix()
        not_run = "\n".join(matrix["not_run"])
        self.assertIn("holdout", not_run)
        self.assertIn("actual-Voice runtime", not_run)
        consumers = matrix["consumers"]
        self.assertEqual(consumers["issue_44"]["status"], "not run here")
        self.assertEqual(consumers["issue_48"]["status"], "not run here")
        self.assertIn("blind-listening", consumers["issue_44"]["consumes"])

    def test_family_reruns_are_recorded_live(self):
        matrix = load_matrix()
        reruns = matrix["family_reruns"]
        for name in ("framework", "runtime-bridge", "identity", "timing", "signal"):
            self.assertIn(name, reruns)
            self.assertEqual(reruns[name]["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
