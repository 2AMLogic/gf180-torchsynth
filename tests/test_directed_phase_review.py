from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from review_directed_phase import (  # noqa: E402
    BASIS_CODES,
    BASIS_PATH,
    REPORT_PATH,
    ReviewError,
    audible_sources,
    build_review,
    coverage_case_ids,
    encode,
    independence_criterion,
    manifest_criterion,
    normalization_criterion,
    parameter_criterion,
    scorecard_criterion,
    trace_criterion,
    truth_classification,
)
from torchsynth_voice import directed  # noqa: E402
from torchsynth_voice.inventory import load_json  # noqa: E402
from torchsynth_voice.trace_registry import load_registry  # noqa: E402

REVIEW_DOCUMENT = ROOT / "spec/DIRECTED-PHASE-REVIEW.md"


class PhaseReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = build_review()
        cls.manifest = load_json(ROOT / "spec/reference/directed-voice-v1.json")
        cls.coverage = load_json(ROOT / "spec/reference/directed-coverage-v1.json")
        cls.basis = load_json(ROOT / BASIS_PATH)
        cls.registry = load_registry()
        cls.rows = directed.inventory_rows()
        cls.capture = load_json(ROOT / "sim/reference/trace-capture.json")

    def test_committed_report_matches_a_fresh_derivation(self):
        self.assertEqual(encode(self.report), (ROOT / REPORT_PATH).read_bytes())
        self.assertEqual(encode(build_review()), encode(self.report))
        statuses = {entry["id"]: entry["status"] for entry in self.report["criteria"]}
        self.assertEqual(
            statuses,
            {
                "AC1": "SATISFIED",
                "AC2": "SATISFIED-AS-PREPARATION",
                "AC3": "SATISFIED",
                "AC4": "SATISFIED",
                "AC5": "SATISFIED",
                "AC6": "SATISFIED",
                "AC7": "SATISFIED",
            },
        )

    def test_cli_check_is_read_only_and_detects_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "review.json"
            write = subprocess.run(
                [sys.executable, "tools/review_directed_phase.py", "--report", str(target)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(write.returncode, 0, write.stderr)
            self.assertEqual(target.read_bytes(), (ROOT / REPORT_PATH).read_bytes())
            good = subprocess.run(
                [
                    sys.executable,
                    "tools/review_directed_phase.py",
                    "--check",
                    "--report",
                    str(target),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(good.returncode, 0, good.stderr)
            original = target.read_bytes()
            target.write_bytes(original + b" ")
            drift = subprocess.run(
                [
                    sys.executable,
                    "tools/review_directed_phase.py",
                    "--check",
                    "--report",
                    str(target),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(drift.returncode, 1, drift.stdout)
            self.assertEqual(target.read_bytes(), original + b" ")

    def test_committed_report_is_current_against_the_tree(self):
        check = subprocess.run(
            [sys.executable, "tools/review_directed_phase.py", "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)

    def test_every_parameter_and_fixture_is_reachable_from_a_coverage_intent(self):
        self.assertEqual(len(self.rows), 78)
        self.assertEqual(set(self.coverage["parameters"]), set(self.rows))
        self.assertEqual(
            coverage_case_ids(self.coverage),
            {case["id"] for case in self.manifest["cases"]},
        )
        self.assertEqual(self.report["corpus"]["directed_cases"], 392)
        self.assertEqual(self.coverage["discrete_modes"]["count"], 0)
        self.assertTrue(self.coverage["discrete_modes"]["reason"].strip())

    def test_planned_traces_are_the_canonical_registry_without_analytic_collisions(self):
        canonical = {trace["name"] for trace in self.registry["traces"]}
        self.assertEqual(len(canonical), 32)
        self.assertEqual(set(self.coverage["traces"]), canonical)
        self.assertFalse([name for name in canonical if name.startswith("analytic.")])
        limited = self.report["criteria"][1]["observed"][
            "traces_declaring_a_graph_limitation"
        ]
        self.assertTrue(set(limited) < canonical)
        self.assertIn("mixer.peak", limited)

    def test_normalization_targets_are_exact_binary32_neighbours_with_a_tie_policy(self):
        targets = self.coverage["normalization"]
        self.assertEqual(
            {relation: entry["target_peak"] for relation, entry in targets.items()},
            {"below": 1 - 2**-24, "tie": 1.0, "above": 1 + 2**-23},
        )
        for entry in targets.values():
            self.assertIn("tie retains input", entry["render_obligation"])
            self.assertEqual(entry["peak_status"], "unmeasured")
        branches = self.report["criteria"][2]["observed"]["captured_peak_greater_than_one"]
        self.assertEqual(
            branches,
            {
                "normalization:above": True,
                "normalization:below": False,
                "normalization:tie": False,
            },
        )

    def test_truth_basis_covers_every_canonical_trace_and_partitions_the_fixtures(self):
        canonical = {trace["name"] for trace in self.registry["traces"]}
        self.assertEqual(set(self.basis["traces"]), canonical)
        for name, entry in self.basis["traces"].items():
            self.assertIn(entry["basis"], BASIS_CODES, name)
            self.assertTrue(entry["condition"].strip(), name)
            self.assertTrue(entry["analytic_properties"], name)
        truth = self.report["truth"]
        self.assertEqual(sum(truth["traces_by_basis"].values()), 32)
        self.assertEqual(sum(truth["audible_source_combinations"].values()), 392)
        silent = truth["fixtures_with_analytically_silent_output"]["count"]
        self.assertEqual(
            silent + truth["fixtures_whose_peak_must_be_measured"]["count"], 392
        )
        self.assertEqual(
            silent, truth["audible_source_combinations"]["none"]
        )
        noise = truth["fixtures_without_analytic_final_output"]["ids"]
        self.assertEqual(len(noise), 21)
        self.assertIn("source:noise", noise)
        self.assertIn("special:stress", noise)
        for case_id in truth["fixtures_with_analytically_silent_output"]["ids"]:
            self.assertNotIn(case_id, noise)

    def test_activation_rule_matches_the_declared_family_rules(self):
        cases = {case["id"]: case for case in self.manifest["cases"]}
        silent = directed.resolve_patch(self.manifest, cases["special:silence"])
        self.assertEqual(audible_sources(silent), ())
        stress = directed.resolve_patch(self.manifest, cases["special:stress"])
        self.assertEqual(audible_sources(stress), ("noise", "vco_1", "vco_2"))
        noise_only = directed.resolve_patch(self.manifest, cases["source:noise"])
        self.assertEqual(audible_sources(noise_only), ("noise",))

    def test_scorecard_negative_controls_run_live_and_all_refuse(self):
        criterion = scorecard_criterion()
        probes = criterion["observed"]["negative_controls"]
        self.assertEqual(
            [probe["probe"] for probe in probes],
            [
                "missing unit",
                "missing validity",
                "NaN as a result value",
                "aggregate of incompatible properties",
            ],
        )
        for probe in probes:
            self.assertTrue(probe["refused"], probe)
            self.assertTrue(probe["reason"].strip(), probe)

    def test_coverage_counts_do_not_move_with_the_verdict(self):
        summaries = independence_criterion(
            load_json(ROOT / "docs/scorecard.json")
        )["observed"]["control_summaries"]
        self.assertEqual(
            summaries["pass_and_fail"]["coverage"],
            summaries["pass_and_no_verdict"]["coverage"],
        )
        self.assertNotEqual(
            summaries["pass_and_fail"]["verdicts"],
            summaries["pass_and_no_verdict"]["verdicts"],
        )
        development = self.report["criteria"][5]["observed"]
        self.assertEqual(development["board_development_expected_rows"], 33672)
        self.assertEqual(sum(development["board_development_raw_row_counts"].values()), 0)

    def test_review_refuses_a_parameter_gap_or_a_drifted_normalization_target(self):
        coverage = copy.deepcopy(self.coverage)
        del coverage["parameters"]["mixer.noise"]
        with self.assertRaisesRegex(ReviewError, "no planned fixture"):
            parameter_criterion(coverage, self.rows, {case["id"] for case in self.manifest["cases"]})

        coverage = copy.deepcopy(self.coverage)
        coverage["normalization"]["tie"]["target_peak"] = 1 + 2**-30
        with self.assertRaisesRegex(ReviewError, "target peak drifted"):
            normalization_criterion(coverage, self.capture)

        coverage = copy.deepcopy(self.coverage)
        coverage["traces"].pop("mixer.peak")
        with self.assertRaisesRegex(ReviewError, "canonical registry disagree"):
            trace_criterion(coverage, self.registry, self.capture)

    def test_review_refuses_a_positional_patch_or_an_unpinned_manifest(self):
        manifest = copy.deepcopy(self.manifest)
        first = manifest["cases"][0]
        first["overrides"] = [[name, value] for name, value in first["overrides"].items()]
        with self.assertRaises(ValueError):
            manifest_criterion(manifest, self.rows, load_json(ROOT / "spec/reference/case-registry-v1.json"))

        registry = load_json(ROOT / "spec/reference/case-registry-v1.json")
        for source in registry["sources"]:
            if source["family"] == "directed":
                source["manifest"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ReviewError, "directed pin"):
            manifest_criterion(copy.deepcopy(self.manifest), self.rows, registry)

    def test_truth_classification_refuses_an_incomplete_basis(self):
        basis = copy.deepcopy(self.basis)
        basis["traces"].pop("noise.raw")
        with self.assertRaisesRegex(ReviewError, "canonical traces"):
            truth_classification(self.manifest, basis, self.registry)

        basis = copy.deepcopy(self.basis)
        basis["traces"]["noise.raw"]["basis"] = "measured-somehow"
        with self.assertRaisesRegex(ReviewError, "unknown truth basis"):
            truth_classification(self.manifest, basis, self.registry)

    def test_markdown_accounting_table_matches_the_generated_report(self):
        text = REVIEW_DOCUMENT.read_text(encoding="utf-8")
        rows = [
            line
            for line in text.splitlines()
            if line.startswith("| `") and line.count("|") == 3
        ]
        self.assertGreaterEqual(len(rows), 15)
        for line in rows:
            key, value = (cell.strip() for cell in line.strip("|").split("|"))
            resolved = self.report
            for part in key.strip("`").split("."):
                resolved = resolved[part]
            self.assertEqual(json.loads(value), resolved, key)

    def test_no_torch_import_in_a_fresh_interpreter(self):
        code = (
            "import sys; sys.path.insert(0, 'tools'); sys.path.insert(0, 'src');"
            "import review_directed_phase as review; review.build_review();"
            "assert 'torch' not in sys.modules"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
