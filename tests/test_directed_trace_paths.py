"""Directed trace-path planner/verifier tests (issue #286); stdlib, synthetic only.

These exercise planning, deduplication, byte-integrity rehashing and every
negative control of ``torchsynth_voice.directed_trace_paths`` on synthetic
payloads. Synthetic payloads are never runtime evidence: no test here executes
the qualified release-era runtime, and the committed receipt is verified as
``UNRUN`` (not a pass) unless a measured, rehashable run has been retained.
"""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from array import array
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import directed_trace_paths as paths  # noqa: E402
from torchsynth_voice import trace_registry  # noqa: E402

REGISTRY = trace_registry.load_registry()
COVERAGE = json.loads(paths.COVERAGE_PATH.read_bytes())
DIRECTED = json.loads(paths.DIRECTED_PATH.read_bytes())
BY_NAME = {t["name"]: t for t in REGISTRY["traces"]}
PLAN = paths.build_plan(REGISTRY, COVERAGE, DIRECTED)
def _file_hash(name):
    return paths.sha256((ROOT / name).read_bytes())


HASHES = {
    "registry_sha256": paths.sha256(trace_registry.REGISTRY_PATH.read_bytes()),
    "coverage_sha256": paths.sha256(paths.COVERAGE_PATH.read_bytes()),
    "directed_sha256": paths.sha256(paths.DIRECTED_PATH.read_bytes()),
}
HASHES.update({key: _file_hash(name) for key, name in paths.INPUT_FILES.items()})
HASHES["existing_publications_sha256"] = {
    name: _file_hash(name) for name in paths.EXISTING_PUBLICATIONS
}
IDENTITY = {key: True for key in paths.IDENTITY_KEYS}
# Clearly synthetic but structurally valid records: they reuse the qualified
# identity fields so the provenance policy can be exercised, and are never
# runtime evidence.
EXECUTION = {
    "admitted": True,
    "outcome": "executed",
    "host": {"system": "SYNTHETIC", "machine": "synthetic"},
    "command": ["synthetic-fixture-not-a-run"],
    "exit_code": 0,
    "execution_id": "synthetic-fixture-execution",
}
QUALIFIED_RUNTIME, QUALIFIED_SOURCE = paths.qualified_worker_identity()
PROVENANCE_EXTRA = {
    "runtime": QUALIFIED_RUNTIME,
    "runtime_sha256": paths.sha256(paths.canonical_json_bytes(QUALIFIED_RUNTIME)),
    "source_sha256": QUALIFIED_SOURCE,
    "worker_producer_sha256": {n: _file_hash(n) for n in paths.PRODUCER_FILES},
}


def f32bytes(values):
    return array("f", values).tobytes()


def synthetic_payload(name, case_id):
    trace = BY_NAME[name]
    count = paths.expected_sample_count(trace)
    if name in paths.MIXER_SEAMS:
        return None
    if trace["kind"] == "scalar":
        return f32bytes([1.5])
    return f32bytes([0.25 + (i % 97) / 400.0 for i in range(count)])


def mixer_payloads(relation):
    count = 176400
    pre = [paths.f32(0.25 + (i % 89) / 200.0) for i in range(count)]
    peak = paths.f32(COVERAGE["normalization"][relation]["target_peak"])
    pre[7] = peak
    pre[11] = -peak if relation == "tie" else -0.5
    if relation == "above":
        gain = paths.f32(1.0 / peak)
        output = [paths.f32(v / peak) for v in pre]
    else:
        gain = 1.0
        output = list(pre)
    return {
        "mixer.pre_normalization": f32bytes(pre),
        "mixer.peak": f32bytes([peak]),
        "mixer.gain": f32bytes([gain]),
        "mixer.output": f32bytes(output),
    }


class Fixture:
    """Writes synthetic payloads and builds a measured receipt from them."""

    def __init__(self, tmp):
        self.raw = Path(tmp)
        self.inventory = {}
        for case in PLAN["cases"]:
            relation = None
            if case["id"].startswith("normalization:"):
                relation = case["id"].split(":")[1]
                data = mixer_payloads(relation)
            else:
                data = {n: synthetic_payload(n, case["id"]) for n in case["traces"]}
            entries = []
            for name in case["traces"]:
                trace = BY_NAME[name]
                file_name = paths.payload_filename(case["id"], name)
                (self.raw / file_name).write_bytes(data[name])
                entries.append(
                    {
                        "name": name,
                        "file": file_name,
                        "sha256": paths.sha256(data[name]),
                        "size_bytes": len(data[name]),
                        "dtype": trace["dtype"],
                        "shape": list(trace["shape"]),
                    }
                )
            self.inventory[case["id"]] = entries

    def report(self):
        return {
            "cases": [
                {
                    "case": {"id": case_id},
                    "identity": dict(IDENTITY),
                    "capture_inventory": copy.deepcopy(entries),
                }
                for case_id, entries in self.inventory.items()
            ]
        }

    def receipt(self, report=None):
        rows, checks = paths.rows_from_worker(
            PLAN, REGISTRY, report or self.report(), self.raw
        )
        return paths.measured_receipt(
            PLAN,
            rows,
            checks,
            copy.deepcopy(HASHES),
            copy.deepcopy(EXECUTION),
            copy.deepcopy(PROVENANCE_EXTRA),
        )

    def verify(self, receipt):
        return paths.verify_receipt(receipt, REGISTRY, COVERAGE, DIRECTED, self.raw)


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fixture = Fixture(self.tmp.name)

    def reject(self, mutate, fragment):
        receipt = self.fixture.receipt()
        mutate(receipt)
        with self.assertRaises(ValueError) as caught:
            self.fixture.verify(receipt)
        self.assertIn(fragment, str(caught.exception))

    def test_synthetic_fixture_with_valid_provenance_passes(self):
        self.assertEqual(self.fixture.verify(self.fixture.receipt()), "PASS")

    def test_judge_reproduction_is_rejected(self):
        def mutate(r):
            r["execution"] = {
                "admitted": True,
                "outcome": "unrun",
                "host": {"system": "Linux", "machine": "aarch64"},
            }
            r["runtime"] = {"packages": {"torch": "wrong-version"}}
            for key in ("runtime_sha256", "source_sha256"):
                r[key] = "0" * 64
            r["worker_producer_sha256"] = {n: "0" * 64 for n in paths.PRODUCER_FILES}

        self.reject(mutate, "execution outcome")

    def test_contradictory_outcome_rejected(self):
        self.reject(
            lambda r: r["execution"].update(outcome="unrun"), "execution outcome"
        )

    def test_missing_execution_fields_rejected(self):
        for key in ("host", "command", "exit_code", "execution_id"):
            with self.subTest(key=key):
                self.reject(lambda r, k=key: r["execution"].pop(k), "execution")

    def test_nonzero_exit_rejected(self):
        self.reject(lambda r: r["execution"].update(exit_code=1), "exit code")

    def test_missing_provenance_records_rejected(self):
        for key in (
            "runtime",
            "runtime_sha256",
            "source_sha256",
            "worker_producer_sha256",
        ):
            with self.subTest(key=key):
                self.reject(lambda r, k=key: r.pop(k), "")

    def test_altered_runtime_rejected(self):
        self.reject(
            lambda r: r["runtime"].update(torch="9.9.9"), "qualified runtime: torch"
        )

    def test_runtime_digest_mismatch_rejected(self):
        self.reject(
            lambda r: r.update(runtime_sha256="0" * 64), "runtime digest"
        )

    def test_altered_source_rejected(self):
        def mutate(r):
            r["source_sha256"] = dict(r["source_sha256"])
            r["source_sha256"]["torchsynth/synth.py"] = "0" * 64

        self.reject(mutate, "source digests")

    def test_altered_or_missing_producer_hash_rejected(self):
        name = paths.PRODUCER_FILES[0]
        self.reject(
            lambda r: r["worker_producer_sha256"].update({name: "0" * 64}),
            "producer digest differs",
        )
        self.reject(lambda r: r["worker_producer_sha256"].pop(name), "producer digests")
        self.reject(
            lambda r: r["worker_producer_sha256"].update({name: "xyz"}),
            "malformed producer digest",
        )

    def test_altered_or_missing_input_hash_rejected(self):
        self.reject(
            lambda r: r["input_sha256"].update(worker_sha256="0" * 64),
            "input hash differs",
        )
        self.reject(lambda r: r["input_sha256"].pop("producer_sha256"), "input hash")
        self.reject(
            lambda r: r["input_sha256"].pop("existing_publications_sha256"),
            "existing-publication",
        )


class MissingWholeCaseTests(unittest.TestCase):
    def test_missing_whole_case_is_verifiable_fail_not_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(tmp)
            report = fixture.report()
            dropped = report["cases"].pop(0)["case"]["id"]
            receipt = fixture.receipt(report)
            self.assertEqual(receipt["status"], "FAIL")
            self.assertEqual(fixture.verify(receipt), "FAIL")
            entry = receipt["case_checks"][dropped]
            self.assertIs(entry["worker_result_present"], False)
            self.assertFalse(any(entry[k] for k in paths.IDENTITY_KEYS))
            self.assertFalse(receipt["summary"]["complete_path_coverage"])
            rows = [r for r in receipt["rows"] if r["case"] == dropped]
            self.assertTrue(rows and all(r["status"] == "missing" for r in rows))

    def test_forged_pass_for_missing_case_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(tmp)
            report = fixture.report()
            report["cases"].pop(0)
            receipt = fixture.receipt(report)
            receipt["status"] = "PASS"
            with self.assertRaises(ValueError):
                fixture.verify(receipt)

    def test_absent_case_cannot_claim_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(tmp)
            report = fixture.report()
            dropped = report["cases"].pop(0)["case"]["id"]
            receipt = fixture.receipt(report)
            receipt["case_checks"][dropped] = dict(
                IDENTITY, worker_result_present=False
            )
            with self.assertRaises(ValueError):
                fixture.verify(receipt)


class PlanTests(unittest.TestCase):
    def test_every_registry_trace_has_a_permitted_manifest_case(self):
        directed_ids = {c["id"] for c in DIRECTED["cases"]}
        self.assertEqual(
            {r["trace"] for r in PLAN["rows"]}, {t["name"] for t in REGISTRY["traces"]}
        )
        for row in PLAN["rows"]:
            self.assertIn(row["case"], COVERAGE["traces"][row["trace"]]["cases"])
            self.assertIn(row["case"], directed_ids)
            self.assertTrue(row["limitation"])

    def test_plan_is_deterministic_and_deduplicates_shared_cases(self):
        self.assertEqual(PLAN, paths.build_plan(REGISTRY, COVERAGE, DIRECTED))
        ids = [c["id"] for c in PLAN["cases"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertLess(len(ids), len(PLAN["rows"]))
        shared = next(c for c in PLAN["cases"] if c["id"].startswith("route:"))
        self.assertGreater(len(shared["traces"]), 1)

    def test_envelopes_avoid_deactivating_zero_stage_case(self):
        for row in PLAN["rows"]:
            if row["trace"].endswith("_adsr.output") or row["trace"].startswith("adsr_"):
                self.assertNotIn("zero-stages", row["case"])

    def test_normalization_seams_take_all_cases_with_declared_relations(self):
        rows = [r for r in PLAN["rows"] if r["trace"] in paths.MIXER_SEAMS]
        self.assertEqual(
            {r["case"] for r in rows},
            {"normalization:above", "normalization:below", "normalization:tie"},
        )
        for row in rows:
            self.assertEqual(row["expectation"]["kind"], "normalization")

    def test_scalars_have_range_expectation_not_nonconstant(self):
        for row in PLAN["rows"]:
            if row["trace"].startswith("keyboard."):
                self.assertEqual(row["expectation"]["kind"], "scalar_in_range")


class VerifierTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fixture = Fixture(self.tmp.name)

    def reject(self, receipt, fragment=None):
        with self.assertRaises(ValueError) as caught:
            self.fixture.verify(receipt)
        if fragment:
            self.assertIn(fragment, str(caught.exception))

    def test_synthetic_receipt_verifies(self):
        receipt = self.fixture.receipt()
        self.assertEqual(receipt["status"], "PASS")
        self.assertTrue(receipt["summary"]["complete_path_coverage"])
        self.assertEqual(self.fixture.verify(receipt), "PASS")

    def test_receipt_only_inspection_is_refused(self):
        with self.assertRaises(ValueError):
            paths.verify_receipt(self.fixture.receipt(), REGISTRY, COVERAGE, DIRECTED)

    def test_mismatched_case_mapping(self):
        receipt = self.fixture.receipt()
        row = next(r for r in receipt["rows"] if r["trace"] == "adsr_1.output")
        row["case"] = "envelope:adsr_1:zero-stages"
        self.reject(receipt, "row set differs")

    def test_case_not_permitted_for_trace(self):
        receipt = self.fixture.receipt()
        row = next(r for r in receipt["rows"] if r["trace"] == "adsr_1.output")
        row["case"] = "source:noise"
        self.reject(receipt)

    def test_missing_registry_name(self):
        receipt = self.fixture.receipt()
        receipt["rows"] = [r for r in receipt["rows"] if r["trace"] != "noise.raw"]
        self.reject(receipt)

    def test_duplicate_row(self):
        receipt = self.fixture.receipt()
        receipt["rows"].append(copy.deepcopy(receipt["rows"][0]))
        self.reject(receipt, "duplicate row")

    def test_payload_byte_corruption(self):
        receipt = self.fixture.receipt()
        row = next(r for r in receipt["rows"] if r["trace"] == "lfo_1.raw")
        path = self.fixture.raw / row["payload"]["file"]
        data = bytearray(path.read_bytes())
        data[0] ^= 0xFF
        path.write_bytes(bytes(data))
        self.reject(receipt, "payload hash mismatch")

    def test_payload_hash_claim_mismatch(self):
        receipt = self.fixture.receipt()
        row = next(r for r in receipt["rows"] if r["trace"] == "lfo_1.raw")
        row["payload"]["sha256"] = "0" * 64
        self.reject(receipt, "payload hash mismatch")

    def test_payload_shape_corruption_with_consistent_hash(self):
        receipt = self.fixture.receipt()
        row = next(r for r in receipt["rows"] if r["trace"] == "lfo_1.raw")
        path = self.fixture.raw / row["payload"]["file"]
        data = path.read_bytes()[:-4]
        path.write_bytes(data)
        row["payload"]["sha256"] = paths.sha256(data)
        row["payload"]["size_bytes"] = len(data)
        self.reject(receipt, "dtype/shape/count mismatch")

    def test_payload_path_traversal_rejected(self):
        receipt = self.fixture.receipt()
        row = receipt["rows"][0]
        row["payload"]["file"] = "../" + row["payload"]["file"]
        self.reject(receipt)

    def test_nonfinite_payload_becomes_failed_row_and_blocks_pass(self):
        trace_name = "vco_1.raw"
        case_id = next(r["case"] for r in PLAN["rows"] if r["trace"] == trace_name)
        data = array("f", [0.5] * 176400)
        data[5] = float("nan")
        entry = next(
            e for e in self.fixture.inventory[case_id] if e["name"] == trace_name
        )
        (self.fixture.raw / entry["file"]).write_bytes(data.tobytes())
        entry["sha256"] = paths.sha256(data.tobytes())
        receipt = self.fixture.receipt()
        row = next(r for r in receipt["rows"] if r["trace"] == trace_name)
        self.assertEqual(row["status"], "failed")
        self.assertIn("nonfinite", row["reason"])
        self.assertEqual(receipt["status"], "FAIL")
        self.assertFalse(receipt["summary"]["complete_path_coverage"])
        self.assertEqual(self.fixture.verify(receipt), "FAIL")
        row["status"] = "captured"
        self.reject(receipt)

    def test_nonfinite_claimed_as_captured_is_rejected(self):
        receipt = self.fixture.receipt()
        row = next(r for r in receipt["rows"] if r["trace"] == "noise.raw")
        path = self.fixture.raw / row["payload"]["file"]
        data = array("f", [0.5] * 176400)
        data[0] = float("inf")
        path.write_bytes(data.tobytes())
        row["payload"]["sha256"] = paths.sha256(data.tobytes())
        self.reject(receipt, "nonfinite")

    def test_constant_series_cannot_claim_activation(self):
        receipt = self.fixture.receipt()
        row = next(r for r in receipt["rows"] if r["trace"] == "adsr_1.output")
        path = self.fixture.raw / row["payload"]["file"]
        data = f32bytes([0.5] * 1764)
        path.write_bytes(data)
        row["payload"]["sha256"] = paths.sha256(data)
        self.reject(receipt)  # stale measurements/claims
        row["measurements"] = paths.evaluate(
            row["expectation"], BY_NAME["adsr_1.output"], {"adsr_1.output": data}
        )[0]
        self.reject(receipt, "unsupported activation claim")

    def test_all_zero_series_is_not_active(self):
        measured, checks = paths.evaluate(
            {"kind": "series_active"},
            BY_NAME["noise.raw"],
            {"noise.raw": f32bytes([0.0] * 176400)},
        )
        self.assertFalse(checks["nonzero"])
        self.assertFalse(paths.activation_record({"kind": "series_active"}, checks)["passed"])

    def test_scalar_out_of_declared_range_fails(self):
        expectation = PLAN_EXPECT("keyboard.midi_f0")
        _, checks = paths.evaluate(
            expectation, BY_NAME["keyboard.midi_f0"], {"keyboard.midi_f0": f32bytes([200.0])}
        )
        self.assertFalse(checks["within_declared_range"])

    def test_normalization_relation_enforced(self):
        receipt = self.fixture.receipt()
        self.assertTrue(
            all(
                r["activation"]["passed"]
                for r in receipt["rows"]
                if r["trace"] in paths.MIXER_SEAMS
            )
        )
        wrong = mixer_payloads("above")
        row = next(
            r
            for r in PLAN["rows"]
            if r["trace"] == "mixer.peak" and r["case"] == "normalization:below"
        )
        _, checks = paths.evaluate(row["expectation"], BY_NAME["mixer.peak"], wrong)
        self.assertFalse(paths.activation_record(row["expectation"], checks)["passed"])

    def test_altered_expectation_rejected(self):
        receipt = self.fixture.receipt()
        row = next(r for r in receipt["rows"] if r["trace"] == "keyboard.duration")
        row["expectation"] = {"kind": "series_active"}
        self.reject(receipt, "expectation")

    def test_limitation_must_be_retained(self):
        receipt = self.fixture.receipt()
        receipt["rows"][0]["limitation"] = ""
        self.reject(receipt, "limitation")

    def test_missing_row_prevents_complete_claim(self):
        report = self.fixture.report()
        report["cases"][0]["capture_inventory"].pop()
        receipt = self.fixture.receipt(report)
        self.assertEqual(receipt["status"], "FAIL")
        self.assertGreaterEqual(receipt["summary"]["missing"], 1)
        self.assertFalse(receipt["summary"]["complete_path_coverage"])
        self.assertEqual(self.fixture.verify(receipt), "FAIL")
        receipt["status"] = "PASS"
        self.reject(receipt)

    def test_identity_failure_prevents_pass(self):
        report = self.fixture.report()
        report["cases"][0]["identity"]["audio_identical"] = False
        receipt = self.fixture.receipt(report)
        self.assertEqual(receipt["status"], "FAIL")

    def test_stale_registry_identity_rejected(self):
        receipt = self.fixture.receipt()
        receipt["input_sha256"]["registry_sha256"] = "0" * 64
        self.reject(receipt, "stale")

    def test_wrong_pinned_commit_rejected(self):
        receipt = self.fixture.receipt()
        receipt["pinned_torchsynth_commit"] = "0" * 40
        self.reject(receipt, "TorchSynth")


def PLAN_EXPECT(name):
    return next(r["expectation"] for r in PLAN["rows"] if r["trace"] == name)


class UnrunTests(unittest.TestCase):
    def receipt(self):
        return paths.unrun_receipt(
            PLAN, REGISTRY, HASHES, "host not admitted", {"system": "Linux"}, ["cmd"]
        )

    def test_unrun_is_reported_as_unrun_never_pass(self):
        receipt = self.receipt()
        status = paths.verify_receipt(receipt, REGISTRY, COVERAGE, DIRECTED)
        self.assertEqual(status, "UNRUN")
        self.assertFalse(receipt["summary"]["complete_path_coverage"])

    def test_unrun_cannot_claim_admitted_execution(self):
        receipt = self.receipt()
        receipt["execution"]["admitted"] = True
        with self.assertRaises(ValueError):
            paths.verify_receipt(receipt, REGISTRY, COVERAGE, DIRECTED)

    def test_unrun_row_cannot_carry_evidence(self):
        receipt = self.receipt()
        receipt["rows"][0]["activation"] = {"passed": True}
        with self.assertRaises(ValueError):
            paths.verify_receipt(receipt, REGISTRY, COVERAGE, DIRECTED)

    def test_unrun_row_without_reason_rejected(self):
        receipt = self.receipt()
        del receipt["rows"][0]["reason"]
        with self.assertRaises(ValueError):
            paths.verify_receipt(receipt, REGISTRY, COVERAGE, DIRECTED)

    def test_status_pass_without_admission_rejected(self):
        receipt = self.receipt()
        receipt["status"] = "PASS"
        with self.assertRaises(ValueError):
            paths.verify_receipt(receipt, REGISTRY, COVERAGE, DIRECTED, "/nonexistent")


class CommittedReceiptTests(unittest.TestCase):
    def test_committed_receipt_is_honest_and_not_a_pass_without_raw_bytes(self):
        publication = ROOT / "sim/reference/directed-trace-paths-v1.json"
        if not publication.exists():
            self.skipTest("no committed receipt")
        receipt = json.loads(publication.read_bytes())
        if receipt["status"] == "UNRUN":
            self.assertEqual(
                paths.verify_receipt(receipt, REGISTRY, COVERAGE, DIRECTED), "UNRUN"
            )
            result = subprocess.run(
                [sys.executable, str(ROOT / "tools/qualify_directed_trace_paths.py"),
                 "--check-receipt"],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        else:
            self.skipTest("measured receipt needs retained raw payloads to verify")

    def test_existing_publications_untouched_by_inputs_check(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools/qualify_directed_trace_paths.py"),
             "--check-inputs"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["traces"], 32)


if __name__ == "__main__":
    unittest.main()
