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

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
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


def load_runner():
    spec = importlib.util.spec_from_file_location(
        "qualify_mutations_matrix_under_test",
        ROOT / "tools/qualify_mutations_matrix.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CiSubsetSentinelTests(unittest.TestCase):
    """The ``--ci-subset`` sentinel (issue #299): exactly 20 rows, fail-visible."""

    def setUp(self):
        self.runner = load_runner()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)

    def run_quiet(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            try:
                rows = self.runner.select_ci_subset()
                code = 0
            except SystemExit as exit_:
                rows, code = None, exit_.code
        return rows, code, out.getvalue()

    def point_at(self, matrix, families=None):
        path = self.tmp_path / "matrix.json"
        path.write_text(json.dumps(matrix))
        self.runner.PUBLICATION_PATH = path
        if families is not None:
            self.runner.FAMILY_PUBLICATIONS = families

    def test_selects_exactly_the_published_20_rows(self):
        rows, code, _ = self.run_quiet()
        self.assertEqual(code, 0)
        committed = load_matrix()["ci_subset"]["rows"]
        self.assertEqual(len(rows), self.runner.EXPECTED_CI_SUBSET_ROWS)
        self.assertEqual(self.runner.EXPECTED_CI_SUBSET_ROWS, 20)
        self.assertEqual(rows, committed)

    def test_cli_flag_prints_pass_and_rows(self):
        completed = subprocess.run(
            [sys.executable, str(ROOT / "tools/qualify_mutations_matrix.py"),
             "--ci-subset"],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["ci_subset_rows"], 20)
        self.assertEqual(report["rows"], load_matrix()["ci_subset"]["rows"])

    def test_committed_row_list_drift_fails_visibly(self):
        matrix = load_matrix()
        matrix["ci_subset"]["rows"] = matrix["ci_subset"]["rows"][:-1]
        self.point_at(matrix)
        _, code, out = self.run_quiet()
        self.assertEqual(code, 1)
        self.assertIn("ci_subset", out)

    def test_row_count_other_than_20_fails_visibly(self):
        matrix = load_matrix()
        matrix["faults_to_tests"] = [
            row for row in matrix["faults_to_tests"]
            if row["fault"] != "gain.db"
        ]
        matrix["ci_subset"] = self.runner.build_ci_subset(
            matrix["faults_to_tests"]
        )
        self.point_at(matrix)
        _, code, out = self.run_quiet()
        self.assertEqual(code, 1)
        self.assertIn("expected 20", out)

    def test_untripped_family_row_fails_visibly(self):
        matrix = load_matrix()
        families = {}
        for family, path in self.runner.FAMILY_PUBLICATIONS.items():
            publication = json.loads((ROOT / path).read_bytes())
            if family == "signal":
                for entry in publication["fault_matrix"]:
                    if entry["fault"] == "gain.db":
                        entry["tripped"] = False
            target = self.tmp_path / (family + ".json")
            target.write_text(json.dumps(publication))
            families[family] = str(target)
        self.point_at(matrix, families)
        _, code, out = self.run_quiet()
        self.assertEqual(code, 1)
        self.assertIn("gain.db", out)

    def test_absent_publication_is_exit_2_never_a_pass(self):
        self.runner.PUBLICATION_PATH = self.tmp_path / "missing.json"
        _, code, out = self.run_quiet()
        self.assertEqual(code, 2)
        self.assertIn("ABSENT", out)

    def test_ci_subset_and_check_are_mutually_exclusive(self):
        completed = subprocess.run(
            [sys.executable, str(ROOT / "tools/qualify_mutations_matrix.py"),
             "--ci-subset", "--check"],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        self.assertEqual(completed.returncode, 2)

    def test_workflow_wires_identity_checks_and_sentinel(self):
        text = (ROOT / ".github/workflows/mutations.yml").read_text()
        for command in (
            "python -m unittest discover -s tests -p test_mutations_identity.py",
            "python tools/qualify_mutations_identity.py --check",
            "python tools/qualify_mutations_matrix.py --ci-subset",
        ):
            with self.subTest(command=command):
                self.assertEqual(text.count(command), 1)
        sentinel = text.split("  ci-subset-sentinel:", 1)[1].split("\n  timing-numerical:", 1)[0]
        self.assertIn("--ci-subset", sentinel)


class ContractWrongDemonstrationTests(unittest.TestCase):
    """Issue #298: contract-wrong-but-perceptually-unverified demonstrations.

    These tests run against the committed family publications (regenerated by
    their owning runners) through the matrix composition functions; they do
    not read the committed matrix, whose regeneration is owned by #314.
    """

    @classmethod
    def setUpClass(cls):
        import copy

        cls.copy = copy
        cls.runner = load_runner()
        cls.publications = {
            family: json.loads((ROOT / path).read_bytes())
            for family, path in cls.runner.FAMILY_PUBLICATIONS.items()
        }

    def fresh(self):
        return self.copy.deepcopy(self.publications)

    def violations(self, family, mutate=None):
        publications = self.fresh()
        record = publications[family]["contract_wrong_demonstrations"][0]
        if mutate is not None:
            mutate(record, publications[family])
        return self.runner.validate_contract_wrong_demonstration(
            record, publications[family]
        )

    def test_both_cases_validate_and_are_retained(self):
        for family in ("signal", "timing"):
            with self.subTest(family=family):
                self.assertEqual(self.violations(family), [])
        records = self.runner.build_contract_wrong_demonstrations(self.fresh())
        self.assertEqual(
            sorted(record["demonstration_id"] for record in records),
            [
                "contract-wrong/gain.db+0.01dB",
                "contract-wrong/timing.delay_audio_sample+1sample",
            ],
        )

    def test_forward_and_reverse_associations_are_preserved(self):
        publications = self.fresh()
        catalog = mutations.load_seam_catalog(ROOT / mutations.SEAM_CATALOG_PATH)
        families = self.runner.register_families()
        faults = self.runner.build_faults_to_tests(families, publications, catalog)
        guards = self.runner.false_positive_guards(publications)
        tests = self.runner.build_tests_to_faults(faults, guards)
        records = self.runner.build_contract_wrong_demonstrations(publications)
        self.runner.attach_contract_wrong(faults, tests, records)
        for record in records:
            with self.subTest(demonstration=record["demonstration_id"]):
                owner = next(
                    row
                    for row in faults
                    if row["family"] == record["family"]
                    and row["fault"] == record["owning_fault_row"]
                )
                self.assertIn(
                    record["demonstration_id"],
                    owner["links"]["contract_wrong_demonstrations"],
                )
                detector = next(
                    row
                    for row in tests
                    if row["family"] == record["family"]
                    and row["detector"] == owner["links"]["downstream"]
                )
                self.assertIn(
                    record["demonstration_id"],
                    detector["contract_wrong_demonstrations"],
                )
                self.assertIn(record["owning_fault_row"], detector["expected_faults"])
                self.assertTrue(owner["false_positives"]["clean_control_accepted"])

    def test_missing_mandatory_failure_is_rejected(self):
        def mutate(record, _):
            record["mandatory_rows"]["fault"]["max_abs_error"]["verdict"] = "PASS"

        self.assertTrue(self.violations("signal", mutate))
        self.assertTrue(self.violations("timing", mutate))

    def test_missing_or_nonpassing_clean_control_is_rejected(self):
        def nonpassing(record, _):
            record["mandatory_rows"]["clean_control"]["exact_equal"]["verdict"] = "FAIL"

        def missing(record, _):
            del record["mandatory_rows"]["clean_control"]

        for family in ("signal", "timing"):
            with self.subTest(family=family):
                self.assertTrue(self.violations(family, nonpassing))
                self.assertTrue(self.violations(family, missing))

    def test_loosened_tolerance_is_rejected(self):
        def mutate(record, _):
            record["mandatory_rows"]["fault"]["max_abs_error"]["tolerance"] = 1.0

        for family in ("signal", "timing"):
            self.assertTrue(self.violations(family, mutate))

    def test_optional_acceptance_counted_as_contract_pass_is_rejected(self):
        def counted(record, _):
            record["optional_counts_as_contract_pass"] = True

        def outcome(record, _):
            record["mandatory_outcome"] = "PASS"

        for family in ("signal", "timing"):
            with self.subTest(family=family):
                self.assertTrue(self.violations(family, counted))
                self.assertTrue(self.violations(family, outcome))

    def test_perceptual_similarity_pass_without_evidence_is_rejected(self):
        def claim(record, _):
            record["perceptual_similarity"]["status"] = "PASS"

        def scope(record, _):
            record["perceptual_similarity"]["evidence_scope"] = "listening test"

        def record_scope(record, _):
            record["evidence_scope"] = "actual Voice"

        for family in ("signal", "timing"):
            with self.subTest(family=family):
                self.assertTrue(self.violations(family, claim))
                self.assertTrue(self.violations(family, scope))
                self.assertTrue(self.violations(family, record_scope))

    def test_wrong_magnitude_or_unlinked_owner_is_rejected(self):
        def magnitude(record, _):
            record["magnitude"]["value"] = 1.0 if record["family"] == "signal" else 2

        def owner(record, _):
            record["owning_fault_row"] = "no-such-row"

        for family in ("signal", "timing"):
            with self.subTest(family=family):
                self.assertTrue(self.violations(family, magnitude))
                self.assertTrue(self.violations(family, owner))

    def test_builder_fails_closed_on_malformed_or_missing_demonstration(self):
        publications = self.fresh()
        publications["signal"]["contract_wrong_demonstrations"][0][
            "mandatory_rows"
        ]["fault"]["exact_equal"]["verdict"] = "PASS"
        with self.assertRaises(SystemExit):
            self.runner.build_contract_wrong_demonstrations(publications)
        publications = self.fresh()
        publications["timing"]["contract_wrong_demonstrations"] = []
        with self.assertRaises(SystemExit):
            self.runner.build_contract_wrong_demonstrations(publications)

    def test_demonstrations_do_not_change_the_published_ci_subset_size(self):
        self.assertEqual(self.runner.EXPECTED_CI_SUBSET_ROWS, 20)


if __name__ == "__main__":
    unittest.main()
