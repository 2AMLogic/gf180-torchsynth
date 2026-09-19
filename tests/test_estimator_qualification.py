from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import estimator_qualification as eq  # noqa: E402
from torchsynth_voice.scorecard import make_report  # noqa: E402

MODULE_BYTES = b"synthetic module bytes\n"
SYNTHETIC_PREP_DIGEST = hashlib.sha256(MODULE_BYTES).hexdigest()
RUBRIC = {"id": "synthetic-only", "version": "1"}


def row(verdict="PASS", case="synthetic-case", prop="amplitude"):
    statuses = {"PASS": "valid", "FAIL": "valid",
                "NO VERDICT": "insufficient", "MISSING EVIDENCE": "missing"}
    measured = verdict in ("PASS", "FAIL")
    return {
        "schema_version": 1,
        "case": {"id": case, "partition": "development"},
        "trace": "synthetic-trace",
        "property": prop,
        "estimator": {"name": "synthetic-estimator", "version": "1"},
        "rubric": RUBRIC,
        "unit": "1",
        "expected": {"value": 1, "source": "synthetic fixture"},
        "observed": (1 if verdict == "PASS" else 2) if measured else None,
        "tolerance": {"value": 1, "source": "synthetic rubric"},
        "validity": {"status": statuses[verdict], "reason": "synthetic " + verdict},
        "coverage": "complete" if measured else "none",
        "verdict": verdict,
        "artifact": {
            "identity": "synthetic-fixture",
            "sha256": hashlib.sha256(case.encode()).hexdigest(),
        },
    }


def obligation(case="synthetic-case", prop="amplitude", verdict="PASS"):
    return {
        "case_id": case,
        "property": prop,
        "expected_verdict": verdict,
        "observed_verdict": verdict,
        "satisfied": True,
    }


def synthetic_tree(directory: Path, *, prep_digest=SYNTHETIC_PREP_DIGEST):
    """Minimal structurally-valid producer artifacts; never measurements."""
    src = directory / "src" / "torchsynth_voice"
    src.mkdir(parents=True)
    for name in ("paired_metrics", "periodic_estimators", "envelope_estimators",
                 "spectral_estimators", "preparation"):
        (src / f"{name}.py").write_bytes(MODULE_BYTES)
    (directory / "spec").mkdir()
    (directory / "spec" / "SIGNAL-PREPARATION.md").write_text(
        f"prose ... remains SHA-256\n`{prep_digest}`. This note records.\n",
        encoding="utf-8",
    )
    sim = directory / "sim" / "qualification"
    sim.mkdir(parents=True)

    periodic = {
        "counts": {"cases": 1, "valid": 1, "refused": 0},
        "records": [{
            "measurement": {"status": "valid"},
            "rows": [row()],
        }],
        "range_cells": {
            "cell-key": {
                "algorithm": "periodic-v2",
                "cap_failures": [],
                "qualified": True,
            }
        },
        "paired_sentinels": [{"rows": [row(case="sentinel-a")]}],
    }
    envelope = {
        "records": [{"case_id": "synthetic-case"}],
        "report": make_report([row()], partition="development", rubric=RUBRIC),
        "obligations": [obligation()],
        "floors": {
            "direct.attack_end": {
                "maximum_absolute_error": 0.0,
                "mean_signed_error": 0.0,
                "minimum_tested_stage_samples": 1,
                "refused": 1,
                "unit": "1",
                "valid": 1,
            }
        },
        "shared_preparation": {
            "status": "executed",
            "cases": [{"operation": "identity"}],
            "source_sha256": prep_digest,
        },
    }
    spectral = {
        "cases": [{"id": "synthetic-case"}],
        "scorecard": make_report(
            [row(), row(verdict="MISSING EVIDENCE", case="synthetic-missing",
                        prop="noise_identity")],
            partition="development",
            rubric=RUBRIC,
        ),
        "refusals": {"reason-a": 1},
        "mutations": [{
            "case": "synthetic-mutation",
            "expected_detectors": ["d1"],
            "observed": {"d1": "FAIL"},
            "detected": True,
            "magnitude": "synthetic",
        }],
        "floors": [{
            "domain": "actual-frequency",
            "max_measured_absolute_error": 0.0,
            "preregistered_bound": 1e-09,
            "property": "harmonic.frequency",
            "trials": 1,
            "unit": "Hz",
        }],
        "finite_ensemble": {"seeds": [1]},
        "preparation": {
            "status": "measured",
            "checks": ["c1"],
            "source_sha256": prep_digest,
        },
        "version": "spectral-noise-mix-v1",
    }
    preparation = {
        "checks": [{"status": "pass"}],
        "status": "PASS",
        "schema": "preparation-qualification",
        "runtime_integration": {"status": "PENDING", "reason": "synthetic"},
        "actual_integrations": [{
            "evidence_status": "VALIDATED",
            "status": "NO_VERDICT",
        }],
        "implementation_sha256": {
            "src/torchsynth_voice/preparation.py": prep_digest,
        },
    }
    artifacts = {
        "periodic": periodic,
        "envelope": envelope,
        "spectral": spectral,
        "preparation": preparation,
    }
    for name, artifact in artifacts.items():
        relative = eq.ARTIFACT_PATHS[name]
        path = directory / relative
        path.write_text(json.dumps(artifact), encoding="utf-8")
    return artifacts


def build_synthetic(**kwargs) -> tuple[Path, dict]:
    directory = Path(tempfile.mkdtemp(prefix="estimator-qual-"))
    synthetic_tree(directory, **kwargs)
    return directory, eq.build_ledger(directory)


class SyntheticTreeTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory, cls.ledger = build_synthetic()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.directory, ignore_errors=True)

    def inventory(self, ledger=None):
        ledger = copy.deepcopy(ledger or self.ledger)
        return {
            "schema_version": 1,
            "inventory": "estimator-obligations",
            "families": {
                name: eq._ledger_inventory_view(ledger, name)
                for name in eq.FAMILIES
            },
            "artifacts": ledger["artifacts"],
        }

    def test_round_trip_and_gate_qualified(self):
        restored = eq.ledger_from_json(eq.ledger_to_json(self.ledger))
        self.assertEqual(restored, self.ledger)
        publication = eq.gate_dependent_families(self.ledger)
        self.assertEqual(publication["preparation_status"], "consistent")
        for name in eq.FAMILIES:
            self.assertEqual(publication["families"][name]["publication"],
                             "qualified")

    def test_preparation_binding_is_consistent(self):
        block = self.ledger["preparation"]
        self.assertEqual(block["status"], "consistent")
        self.assertEqual(block["recomputed_sha256"], SYNTHETIC_PREP_DIGEST)
        self.assertEqual(
            set(block["declared"]),
            {"envelope_shared_preparation", "preparation_artifact",
             "spectral_probe"},
        )

    def test_omitted_row_with_case_present_refuses(self):
        directory = Path(tempfile.mkdtemp(prefix="estimator-qual-"))
        try:
            synthetic_tree(directory)
            envelope_path = directory / eq.ARTIFACT_PATHS["envelope"]
            artifact = json.loads(envelope_path.read_text(encoding="utf-8"))
            # Delete the required row, keep the case present, and repair the
            # local summary so the tamper is internally consistent. The
            # independent obligation inventory still exposes the omission.
            artifact["report"] = make_report(
                [], partition="development", rubric=RUBRIC
            )
            envelope_path.write_text(json.dumps(artifact), encoding="utf-8")
            with self.assertRaisesRegex(
                eq.EstimatorQualificationError, "obligations lack any report row"
            ):
                eq.build_ledger(directory)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_duplicate_obligation_refuses(self):
        directory = Path(tempfile.mkdtemp(prefix="estimator-qual-"))
        try:
            synthetic_tree(directory)
            path = directory / eq.ARTIFACT_PATHS["envelope"]
            artifact = json.loads(path.read_text(encoding="utf-8"))
            artifact["obligations"].append(obligation())
            path.write_text(json.dumps(artifact), encoding="utf-8")
            with self.assertRaisesRegex(
                eq.EstimatorQualificationError, "duplicate obligation"
            ):
                eq.build_ledger(directory)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_cleared_obligations_refuse(self):
        directory = Path(tempfile.mkdtemp(prefix="estimator-qual-"))
        try:
            synthetic_tree(directory)
            path = directory / eq.ARTIFACT_PATHS["envelope"]
            artifact = json.loads(path.read_text(encoding="utf-8"))
            artifact["obligations"] = []
            path.write_text(json.dumps(artifact), encoding="utf-8")
            with self.assertRaisesRegex(
                eq.EstimatorQualificationError, "obligations must be nonempty"
            ):
                eq.build_ledger(directory)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_substituted_case_key_refuses(self):
        directory = Path(tempfile.mkdtemp(prefix="estimator-qual-"))
        try:
            synthetic_tree(directory)
            path = directory / eq.ARTIFACT_PATHS["envelope"]
            artifact = json.loads(path.read_text(encoding="utf-8"))
            artifact["obligations"][0]["case_id"] = "other-case"
            path.write_text(json.dumps(artifact), encoding="utf-8")
            with self.assertRaisesRegex(
                eq.EstimatorQualificationError, "lack any report row"
            ):
                eq.build_ledger(directory)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_empty_periodic_cells_refuse(self):
        directory = Path(tempfile.mkdtemp(prefix="estimator-qual-"))
        try:
            synthetic_tree(directory)
            path = directory / eq.ARTIFACT_PATHS["periodic"]
            artifact = json.loads(path.read_text(encoding="utf-8"))
            artifact["range_cells"] = {}
            path.write_text(json.dumps(artifact), encoding="utf-8")
            with self.assertRaisesRegex(
                eq.EstimatorQualificationError, "range_cells must be nonempty"
            ):
                eq.build_ledger(directory)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_count_mismatch_refuses(self):
        directory = Path(tempfile.mkdtemp(prefix="estimator-qual-"))
        try:
            synthetic_tree(directory)
            path = directory / eq.ARTIFACT_PATHS["periodic"]
            artifact = json.loads(path.read_text(encoding="utf-8"))
            artifact["counts"]["valid"] = 0
            path.write_text(json.dumps(artifact), encoding="utf-8")
            with self.assertRaisesRegex(
                eq.EstimatorQualificationError, "recomputed"
            ):
                eq.build_ledger(directory)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_false_detection_flag_refuses(self):
        directory = Path(tempfile.mkdtemp(prefix="estimator-qual-"))
        try:
            synthetic_tree(directory)
            path = directory / eq.ARTIFACT_PATHS["spectral"]
            artifact = json.loads(path.read_text(encoding="utf-8"))
            artifact["mutations"][0]["detected"] = False
            path.write_text(json.dumps(artifact), encoding="utf-8")
            with self.assertRaisesRegex(
                eq.EstimatorQualificationError, "hides an assigned detector FAIL"
            ):
                eq.build_ledger(directory)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_detection_claim_without_detector_fail_refuses(self):
        directory = Path(tempfile.mkdtemp(prefix="estimator-qual-"))
        try:
            synthetic_tree(directory)
            path = directory / eq.ARTIFACT_PATHS["spectral"]
            artifact = json.loads(path.read_text(encoding="utf-8"))
            artifact["mutations"][0]["observed"] = {"d1": "NO VERDICT"}
            path.write_text(json.dumps(artifact), encoding="utf-8")
            with self.assertRaisesRegex(
                eq.EstimatorQualificationError, "without an assigned detector"
            ):
                eq.build_ledger(directory)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_falsified_floor_and_repaired_digest_still_detected(self):
        directory = Path(tempfile.mkdtemp(prefix="estimator-qual-"))
        try:
            synthetic_tree(directory)
            spectral_path = directory / eq.ARTIFACT_PATHS["spectral"]
            artifact = json.loads(spectral_path.read_text(encoding="utf-8"))
            # Falsify a floor value in the artifact data.
            artifact["floors"][0]["max_measured_absolute_error"] = 1.0
            spectral_path.write_text(json.dumps(artifact), encoding="utf-8")
            fresh = eq.build_ledger(directory)
            beyond = fresh["families"]["spectral"]["floors"][
                "exceeding_preregistered_bound"
            ]
            self.assertEqual(beyond, 1)
            # Internally-consistent edit: also refresh the pinned artifact
            # digest, as a self-consistent rewrite would. The reviewed
            # inventory still exposes the drift.
            inventory = self.inventory()
            inventory["artifacts"]["spectral"] = fresh["artifacts"]["spectral"]
            with self.assertRaisesRegex(
                eq.EstimatorQualificationError, "inventory mismatch"
            ):
                eq.check_inventory(fresh, inventory)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_stale_preparation_pin_gates_dependents(self):
        directory, ledger = build_synthetic(
            prep_digest="b" * 64,
        )
        try:
            # One declared pin kept the stale digest: inconsistent binding.
            block = ledger["preparation"]
            self.assertEqual(block["status"], "inconsistent")
            publication = eq.gate_dependent_families(ledger)
            for name in eq.PREPARATION_DEPENDENT:
                view = publication["families"][name]
                self.assertEqual(view["publication"], "NO VERDICT")
                self.assertTrue(view["reason"])
            self.assertEqual(
                publication["families"]["paired"]["publication"], "unaffected"
            )
            self.assertEqual(
                publication["families"]["preparation"]["publication"],
                "unaffected",
            )
            # Raw rows are preserved untouched by the gate.
            self.assertEqual(
                ledger["families"]["envelope"]["verdicts"]["PASS"], 1
            )
            with self.assertRaisesRegex(
                eq.EstimatorQualificationError, "artifact digests do not match"
            ):
                eq.check_inventory(
                    ledger,
                    self.inventory(copy.deepcopy(self.ledger)),
                )
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_gate_override_reason(self):
        publication = eq.gate_dependent_families(
            self.ledger, override_reason="injected asymmetric preparation"
        )
        for name in eq.PREPARATION_DEPENDENT:
            self.assertEqual(
                publication["families"][name]["reason"],
                "injected asymmetric preparation",
            )

    def test_inventory_extra_recomputed_key_refuses(self):
        inventory = self.inventory()
        ledger = copy.deepcopy(self.ledger)
        ledger["families"]["paired"]["grid"]["extra"] = 1
        with self.assertRaisesRegex(
            eq.EstimatorQualificationError, "inventory mismatch"
        ):
            eq.check_inventory(ledger, inventory)

    def test_inventory_unreviewed_view_key_refuses(self):
        inventory = self.inventory()
        # A future module revision adding a census field without a reviewed
        # inventory refresh must refuse, never silently widen the policy.
        original = eq._ledger_inventory_view

        def padded(ledger, name):
            view = original(ledger, name)
            if name == "periodic":
                view["surprise"] = 1
            return view

        with unittest.mock.patch.object(
            eq, "_ledger_inventory_view", side_effect=padded
        ):
            with self.assertRaisesRegex(
                eq.EstimatorQualificationError, "unreviewed keys"
            ):
                eq.check_inventory(self.ledger, inventory)

    def test_inventory_reviewed_value_drift_refuses(self):
        inventory = self.inventory()
        inventory["families"]["periodic"]["verdicts"]["PASS"] += 1
        with self.assertRaisesRegex(
            eq.EstimatorQualificationError, "inventory mismatch"
        ):
            eq.check_inventory(self.ledger, inventory)

    def test_inventory_must_cover_mandatory_families(self):
        inventory = self.inventory()
        del inventory["families"]["spectral"]
        with self.assertRaisesRegex(
            eq.EstimatorQualificationError, "mandatory set"
        ):
            eq.check_inventory(self.ledger, inventory)

    def test_validate_ledger_rejects_malformed_documents(self):
        with self.assertRaises(eq.EstimatorQualificationError):
            eq.ledger_from_json("{\"schema_version\": 2}")
        ledger = copy.deepcopy(self.ledger)
        ledger["families"].pop("paired")
        with self.assertRaisesRegex(
            eq.EstimatorQualificationError, "exactly"
        ):
            eq.validate_ledger(ledger)
        ledger = copy.deepcopy(self.ledger)
        ledger["totals"]["verdicts"]["PASS"] += 5
        with self.assertRaisesRegex(
            eq.EstimatorQualificationError, "totals.verdicts"
        ):
            eq.validate_ledger(ledger)
        ledger = copy.deepcopy(self.ledger)
        ledger["families"]["spectral"]["estimator"]["module_sha256"] = "xyz"
        with self.assertRaisesRegex(
            eq.EstimatorQualificationError, "64 hex digits"
        ):
            eq.validate_ledger(ledger)

    def test_nonfinite_and_duplicate_json_refuse(self):
        with self.assertRaises(eq.EstimatorQualificationError):
            eq.ledger_from_json("{\"schema_version\": NaN}")


class LandedTreeTestCase(unittest.TestCase):
    """Stored-evidence regression against the committed artifacts."""

    @classmethod
    def setUpClass(cls):
        cls.ledger = eq.build_ledger(ROOT)

    def test_preparation_provenance_is_consistent(self):
        block = self.ledger["preparation"]
        self.assertEqual(block["status"], "consistent")
        self.assertEqual(
            block["recomputed_sha256"],
            "0f8e9e6ee2b31dd6b2b2f5118e81a90d08849d013da13ff04d0c3100c660d297",
        )
        self.assertEqual(
            block["spec_described_sha256"], block["recomputed_sha256"]
        )

    def test_committed_ledger_and_inventory_reconcile(self):
        committed = eq.ledger_from_json(
            (ROOT / "sim/qualification/estimators-v1.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(committed, self.ledger)
        inventory = eq.load_artifact(
            ROOT / "spec/reference/estimator-obligations-v1.json"
        )
        eq.check_inventory(self.ledger, inventory)

    def test_publication_totals(self):
        totals = self.ledger["totals"]
        self.assertEqual(totals["rows"], 6988)
        self.assertEqual(
            totals["verdicts"],
            {
                "PASS": 3620,
                "FAIL": 205,
                "NO VERDICT": 3137,
                "MISSING EVIDENCE": 26,
            },
        )
        # Coverage and floor-limited cases are counted separately and never
        # merged into pass/fail (or each other); refusal denominators stay
        # per family (cases, rows, or obligations) and are never summed.
        self.assertEqual(totals["floor_limited"], 1788)
        families = self.ledger["families"]
        self.assertEqual(families["periodic"]["coverage"]["refused"], 119)
        self.assertEqual(families["envelope"]["coverage"]["refused"], 79)
        self.assertEqual(families["spectral"]["coverage"]["refused"], 1484)
        self.assertEqual(families["spectral"]["coverage"]["missing"], 26)
        self.assertEqual(families["paired"]["coverage"]["refused"], 506)

    def test_every_mandatory_family_has_floors_or_explicit_absence(self):
        for name, family in self.ledger["families"].items():
            self.assertTrue(family["validity_predicate"].strip())
            self.assertTrue(family["limitations"])
            if name in ("envelope", "spectral"):
                self.assertGreater(family["floors"]["count"], 0)
            else:
                self.assertEqual(family["floors"], [])


if __name__ == "__main__":
    unittest.main()
