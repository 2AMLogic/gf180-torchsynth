"""Contract tests for the runtime family campaign preregistration (#333).

These tests validate a manifest, a schema and a non-writing validator. They do
not render, measure or qualify anything: a passing run says the preregistered
inventory is internally consistent and reconciled with the committed family
publications, never that any fault was detected in the actual Voice.
"""

import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import validate_runtime_family_campaign as campaign  # noqa: E402

MANIFEST = ROOT / campaign.MANIFEST_PATH


def load():
    return json.loads(MANIFEST.read_bytes())


def refreeze(manifest):
    """Recompute declared counts and digest so only the targeted rule can fail."""
    manifest["expected"] = campaign._expected_counts(manifest)
    manifest["frozen_inventory_digest"]["sha256"] = campaign.frozen_digest(manifest)
    return manifest


def entry(manifest, identifier):
    return next(e for e in manifest["inventory"] if e["id"] == identifier)


class CommittedManifestTests(unittest.TestCase):
    def test_committed_manifest_validates(self):
        self.assertEqual(campaign.validate_manifest(load()), [])

    def test_cli_is_non_writing_and_reports_unmeasured(self):
        before = sorted(p.name for p in (ROOT / "spec/reference").iterdir())
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools/validate_runtime_family_campaign.py")],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report["measured"])
        self.assertEqual(report["status"], "VALID_PREREGISTRATION")
        after = sorted(p.name for p in (ROOT / "spec/reference").iterdir())
        self.assertEqual(before, after)

    def test_target_and_holdout_preservation(self):
        manifest = load()
        target = manifest["target"]
        self.assertEqual(target["upstream_commit"], "2b0964d4c6c3d472a2a0d54d91b408caaeffca6d")
        self.assertEqual(target["nebula"], "default")
        self.assertEqual((target["duration_seconds"], target["sample_rate_hz"]), (4, 44100))
        self.assertEqual(target["noise_seed"], 13)
        self.assertFalse(target["semantics_changed"])
        self.assertTrue(manifest["holdout"]["sealed"])
        for case in manifest["cases"]:
            self.assertEqual(case["partition"], "development")
            self.assertFalse(96 <= case["global_sound_index"] <= 127)

    def test_every_publication_fault_row_is_reconciled_and_matrix_agrees(self):
        manifest = load()
        matrix = json.loads((ROOT / "sim/reference/mutation-matrix-v1.json").read_bytes())
        matrix_rows = sorted((r["family"], r["fault"]) for r in matrix["faults_to_tests"])
        inventory_rows = []
        for e in manifest["inventory"]:
            inventory_rows.extend([(e["family"], e["source"]["fault"])] * len(e["source"]["row_indices"]))
        self.assertEqual(sorted(inventory_rows), matrix_rows)
        self.assertEqual(manifest["expected"]["inventory_source_rows"], len(matrix["faults_to_tests"]))

    def test_no_nonexecutable_row_is_labeled_runtime_covered(self):
        manifest = load()
        for e in manifest["inventory"]:
            if e["disposition"] != "runtime_cell":
                self.assertFalse(e["counts_toward_runtime_total"], e["id"])
        by_disposition = {}
        for e in manifest["inventory"]:
            by_disposition.setdefault(e["disposition"], []).append(e["id"])
        self.assertEqual(len(by_disposition["deferred_unmeasured"]), 4)
        self.assertIn("signal:normalization-decision-replacement", by_disposition["refusal"])
        self.assertIn("timing:missing-sample", by_disposition["refusal"])
        self.assertIn("timing:dropped-endpoint-coordinate", by_disposition["runtime_cell"])

    def test_normalization_obligation_stays_open(self):
        manifest = load()
        self.assertEqual(manifest["normalization_handoff"]["disposition"], "deferred_unmeasured")
        self.assertTrue(any("normalization" in item for item in
                            manifest["completion"]["open_obligations_after_completion"]))
        for e in manifest["inventory"]:
            if any(op.startswith("norm.") for op in e["operators"]):
                self.assertIn(e["disposition"], ("deferred_unmeasured", "refusal"))
        self.assertEqual(manifest["host_admission"]["status"], "not_admitted")

    def test_aws_host_is_not_inferred_from_dr0006(self):
        admission = load()["host_admission"]
        self.assertIsNone(admission["sanctioned_aws_host"]["committed_qualification_evidence"])
        self.assertIn("Apple M5", admission["current_code_gate"])
        self.assertGreaterEqual(len(admission["qualification_predicate"]), 4)

    def test_align_corners_is_a_proposed_addition_outside_the_denominator(self):
        manifest = load()
        proposal = manifest["proposed_additions"][0]
        self.assertEqual(proposal["operator"], "interp.align_corners_false")
        self.assertFalse(proposal["counts_in_frozen_denominator"])
        self.assertNotIn(proposal["id"], [e["id"] for e in manifest["inventory"]])
        self.assertIn("align_corners=True", proposal["injection_site"])


class RejectionTests(unittest.TestCase):
    def errors(self, manifest, check_sources=False):
        return campaign.validate_manifest(manifest, ROOT, check_sources)

    def assertRejects(self, manifest, fragment):
        errors = self.errors(manifest)
        self.assertTrue(errors, "expected a rejection mentioning %r" % fragment)
        self.assertTrue(any(fragment in e for e in errors), (fragment, errors[:5]))

    def test_duplicate_cell_id(self):
        m = load()
        m["inventory"].append(copy.deepcopy(m["inventory"][0]))
        self.assertRejects(refreeze(m), "duplicate inventory id")

    def test_unknown_operator(self):
        m = load()
        entry(m, "signal:gain.db")["operators"] = ["gain.unknown"]
        entry(m, "signal:gain.db")["magnitude"][0]["operator"] = "gain.unknown"
        self.assertRejects(refreeze(m), "unknown operator")

    def test_unknown_seam(self):
        m = load()
        entry(m, "signal:gain.db")["runtime_seam"] = "voice.not_a_seam"
        self.assertRejects(refreeze(m), "unknown seam")

    def test_non_writable_seam_cannot_host_an_executable_cell(self):
        m = load()
        entry(m, "signal:gain.db")["runtime_seam"] = "voice.normalization_decision"
        self.assertRejects(refreeze(m), "non-writable seam")

    def test_unknown_detector(self):
        m = load()
        entry(m, "signal:gain.db")["detector_ids"] = ["det-signal-99"]
        self.assertRejects(refreeze(m), "unknown detector")

    def test_missing_mandatory_bindings_fail_the_schema(self):
        for field in ("detector_ids", "runtime_seam", "development_cases", "expected_failure",
                      "repeats_required", "magnitude", "invariance_assertions"):
            m = load()
            del entry(m, "signal:gain.db")[field]
            errors = self.errors(m)
            self.assertTrue(any("missing required " + field in e for e in errors), (field, errors[:3]))

    def test_empty_detector_binding_is_rejected(self):
        m = load()
        entry(m, "signal:gain.db")["detector_ids"] = []
        self.assertTrue(self.errors(m))

    def test_missing_case_binding_is_rejected(self):
        m = load()
        entry(m, "signal:gain.db")["development_cases"] = ["global-0"]
        self.assertRejects(refreeze(m), "every campaign case")

    def test_missing_repeat_binding_is_rejected(self):
        m = load()
        entry(m, "signal:gain.db")["repeats_required"] = 1
        self.assertRejects(refreeze(m), "repeats_required")

    def test_holdout_requests_are_rejected(self):
        m = load()
        m["cases"].append({"id": "global-100", "global_sound_index": 100, "partition": "development",
                           "batch_size": 32, "batch_block": 3, "batch_slot": 4})
        self.assertRejects(refreeze(m), "sealed-holdout")
        m = load()
        m["cases"][0]["partition"] = "holdout"
        self.assertTrue(self.errors(m))
        m = load()
        entry(m, "signal:gain.db")["development_cases"] = ["global-0", "global-31", "global-100"]
        self.assertRejects(refreeze(m), "unknown or holdout case")

    def test_inconsistent_batch_slot_is_rejected(self):
        m = load()
        m["cases"][1]["batch_slot"] = 3
        self.assertRejects(refreeze(m), "batch_slot")

    def test_denominator_mismatch_is_rejected(self):
        m = load()
        m["expected"]["worker_attempts"] += 1
        self.assertRejects(m, "expected count mismatch: worker_attempts")
        m = load()
        m["expected"]["runtime_fault_cells"] -= 1
        self.assertRejects(m, "expected count mismatch: runtime_fault_cells")

    def test_silent_inventory_change_breaks_the_frozen_digest(self):
        m = load()
        entry(m, "signal:gain.db")["notes"] = "silently edited"
        errors = self.errors(m)
        self.assertTrue(any("frozen inventory digest" in e for e in errors), errors)

    def test_omitted_publication_fault_is_rejected(self):
        m = load()
        m["inventory"] = [e for e in m["inventory"] if e["id"] != "timing:route-sign-flip"]
        self.assertRejects(refreeze(m), "publication fault omitted from inventory")

    def test_mislabeled_publication_row_is_rejected(self):
        m = load()
        entry(m, "timing:route-sign-flip")["source"]["row_indices"] = [0]
        self.assertRejects(refreeze(m), "source row 0 is fault")

    def test_double_claimed_source_row_is_rejected(self):
        m = load()
        entry(m, "timing:route-sign-flip")["source"]["row_indices"] = [
            entry(m, "timing:route-depth-shift-plus25")["source"]["row_indices"][0]]
        self.assertTrue(self.errors(refreeze(m)))

    def test_deferred_normalization_cannot_become_a_runtime_cell(self):
        m = load()
        e = entry(m, "signal:norm.off")
        e["disposition"] = "runtime_cell"
        e["counts_toward_runtime_total"] = True
        e["obligation_open"] = False
        e["development_cases"] = [c["id"] for c in m["cases"]]
        e["repeats_required"] = 2
        e["expected_event"] = "applied"
        e["runtime_evidence_class"] = "graph_runtime"
        e["invariance_assertions"] = ["x"]
        errors = self.errors(refreeze(m))
        self.assertTrue(any("non-writable seam" in x for x in errors), errors)
        self.assertTrue(any("normalization fault must be deferred" in x for x in errors), errors)

    def test_refusal_cannot_count_as_a_kill(self):
        m = load()
        entry(m, "timing:missing-sample")["counts_toward_runtime_total"] = True
        self.assertRejects(refreeze(m), "counts_toward_runtime_total")

    def test_ineffective_normalization_cell_cannot_be_counted(self):
        m = load()
        cell = next(e for e in m["sensitivity_inventory"] if e["id"] == "signal:norm-cell:norm.off:below")
        cell["disposition"] = "deferred_unmeasured"
        self.assertTrue(self.errors(refreeze(m)))

    def test_sensitivity_entry_cannot_be_executed(self):
        m = load()
        m["sensitivity_inventory"][0]["executed_in_campaign"] = True
        self.assertTrue(self.errors(refreeze(m)))

    def test_proposed_addition_cannot_enter_the_denominator_silently(self):
        m = load()
        m["proposed_additions"][0]["counts_in_frozen_denominator"] = True
        self.assertTrue(self.errors(refreeze(m)))

    def test_preregistration_cannot_claim_a_measurement_or_admitted_host(self):
        m = load()
        m["measurement_status"]["runtime_family_measurement_performed"] = True
        self.assertTrue(self.errors(m))
        m = load()
        m["host_admission"]["status"] = "admitted"
        self.assertTrue(self.errors(m))

    def test_reliability_below_two_executions_is_rejected(self):
        m = load()
        m["reliability"]["min_independent_executions_per_cell"] = 1
        self.assertTrue(self.errors(m))

    def test_changed_bound_source_demands_a_revision(self):
        m = load()
        first = sorted(m["sources"])[0]
        m["sources"][first] = "0" * 64
        errors = self.errors(m, check_sources=True)
        self.assertTrue(any("declare a manifest revision" in e for e in errors), errors)


class AccountingTests(unittest.TestCase):
    """The accounting rule is exercised with hypothetical outcomes only."""

    def test_only_double_detected_runtime_cells_are_kills(self):
        m = load()
        cases = [c["id"] for c in m["cases"]]
        outcomes = {}
        for e in m["inventory"]:
            for case in cases:
                for repeat in (1, 2):
                    outcomes[(e["id"], case, repeat)] = "detected"
        totals = campaign.runtime_kill_totals(m, outcomes)
        self.assertEqual(totals["kills"], m["expected"]["runtime_fault_cells"])
        self.assertEqual(totals["open"], 0)
        # refusals, compositions, deferred and second-detector entries never add to kills
        self.assertEqual(totals["kills"], 25 * 3)

    def test_ineffective_or_single_repeat_cells_stay_open(self):
        m = load()
        outcomes = {("signal:gain.db", "global-0", 1): "detected",
                    ("signal:gain.db", "global-0", 2): "ineffective"}
        totals = campaign.runtime_kill_totals(m, outcomes)
        self.assertEqual(totals["kills"], 0)
        self.assertEqual(totals["open"], m["expected"]["runtime_fault_cells"])


class SchemaTests(unittest.TestCase):
    def test_declared_schema_with_a_full_jsonschema_implementation(self):
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest("jsonschema is not installed; the stdlib subset validator ran instead")
        schema = json.loads((ROOT / campaign.SCHEMA_PATH).read_bytes())
        Draft202012Validator.check_schema(schema)
        errors = sorted(Draft202012Validator(schema).iter_errors(load()), key=str)
        self.assertEqual([], [e.message for e in errors])


class DocumentationLinkTests(unittest.TestCase):
    def test_decision_record_exists_and_is_indexed(self):
        manifest = load()
        record = ROOT / manifest["decision_record"]
        self.assertTrue(record.is_file())
        text = record.read_text()
        for needle in ("2b0964d4c6c3d472a2a0d54d91b408caaeffca6d", "AQ-1", "AQ-2",
                       "deferred", "i-018841ef4169207ba", "#314", "#257"):
            self.assertIn(needle, text)
        index = (ROOT / "spec/decision-records/README.md").read_text()
        self.assertIn(record.name, index)

    def test_mutations_spec_and_audit_link_the_contract(self):
        for relative in ("spec/MUTATIONS.md", "docs/MUTATION-COVERAGE-AUDIT.md"):
            text = (ROOT / relative).read_text()
            self.assertIn("runtime-family-campaign-v1.json", text, relative)
        audit = (ROOT / "docs/MUTATION-COVERAGE-AUDIT.md").read_text()
        self.assertIn("preregistration", audit.lower())


if __name__ == "__main__":
    unittest.main()
