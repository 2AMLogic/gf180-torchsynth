"""Synthetic fixed-96 receipt audit controls; no TorchSynth or holdout data."""

from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from audit_development_corpus import (  # noqa: E402
    DEVELOPMENT,
    SCHEMA,
    audit_receipt,
)
from torchsynth_voice.artifact_renderer import (  # noqa: E402
    digest,
    json_bytes,
    render_artifact,
)
from torchsynth_voice.artifacts import ValidationError, loads  # noqa: E402
from torchsynth_voice.corpus import read_reference, run_corpus  # noqa: E402
from test_artifact_renderer import FakeBackend, template  # noqa: E402


def receipt_from(envelope, plan, index):
    return dict(
        schema=SCHEMA,
        schema_version=1,
        status=index["status"],
        profile=index["profile"],
        runtime_profile="synthetic-audit-profile",
        source_commit=plan["template"]["source"]["commit"],
        expectation=dict(
            development_indices=[
                case["sound_index"] for case in plan["selection"]
            ],
            holdout_indices_rendered=[],
        ),
        producer_git=plan["template"]["project_git"],
        manifest_sha256=plan["manifest"]["sha256"],
        run=dict(
            run_id=envelope["run_id"],
            envelope_ref=envelope["plan"]["ref"].replace(
                "plan.json", "summaries/000002.json"
            ),
            envelope_sha256=digest(json_bytes(envelope)),
            plan_sha256=envelope["plan"]["sha256"],
            index_sha256=envelope["index"]["sha256"],
        ),
        plan=plan,
        index=index,
    )


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve() / "store"
        self.manifest = ROOT / "spec/reference/corpus-v0.json"

    def build(self, indices=(0, 1)):
        envelope = run_corpus(
            self.root,
            self.manifest,
            template(),
            lambda request, store: render_artifact(request, store, FakeBackend()),
            indices=list(indices),
        )
        plan = loads(read_reference(self.root, envelope["plan"]))
        index = loads(read_reference(self.root, envelope["index"]))
        return receipt_from(envelope, plan, index)

    def reseal_index(self, receipt):
        receipt["run"]["index_sha256"] = digest(json_bytes(receipt["index"]))

    def test_valid_synthetic_receipt_passes_receipt_only_and_store_mode(self):
        receipt = self.build()
        self.assertEqual(audit_receipt(receipt, expected=[0, 1]), 2)
        self.assertEqual(
            audit_receipt(receipt, expected=[0, 1], store=self.root), 2
        )

    def test_recounted_two_case_index_cannot_pass_fixed_96_expectation(self):
        receipt = self.build()
        for kwargs in ({}, {"store": self.root}):
            with self.assertRaisesRegex(ValidationError, "fixed development set"):
                audit_receipt(receipt, **kwargs)

    def test_missing_case_identity_refused(self):
        receipt = self.build()
        receipt["index"]["cases"].pop()
        receipt["index"]["expected_case_count"] = 1
        receipt["index"]["observed_case_count"] = 1
        receipt["index"]["status"] = "failed"
        self.reseal_index(receipt)
        with self.assertRaisesRegex(ValidationError, "index identities"):
            audit_receipt(receipt, expected=[0, 1])

    def test_duplicate_case_identity_refused(self):
        receipt = self.build()
        receipt["index"]["cases"].append(copy.deepcopy(receipt["index"]["cases"][0]))
        receipt["index"]["observed_case_count"] = 3
        self.reseal_index(receipt)
        with self.assertRaisesRegex(ValidationError, "duplicate case identity"):
            audit_receipt(receipt, expected=[0, 1])

    def test_holdout_identity_refused_everywhere(self):
        receipt = self.build()
        receipt["expectation"]["holdout_indices_rendered"] = [96]
        with self.assertRaisesRegex(ValidationError, "holdout rendering"):
            audit_receipt(receipt, expected=[0, 1])
        receipt = self.build()
        receipt["plan"]["selection"][0]["sound_index"] = 96
        receipt["run"]["plan_sha256"] = digest(json_bytes(receipt["plan"]))
        with self.assertRaisesRegex(ValidationError, "plan selection"):
            audit_receipt(receipt, expected=[0, 1])

    def test_index_holdout_partition_claim_refused(self):
        receipt = self.build()
        receipt["index"]["cases"][0]["split"] = "holdout"
        self.reseal_index(receipt)
        with self.assertRaisesRegex(ValidationError, "non-development partition"):
            audit_receipt(receipt, expected=[0, 1])

    def test_inflated_observed_count_refused(self):
        receipt = self.build()
        receipt["index"]["observed_case_count"] += 1
        self.reseal_index(receipt)
        with self.assertRaisesRegex(ValidationError, "re-counted case records"):
            audit_receipt(receipt, expected=[0, 1])

    def test_complete_case_without_artifact_reference_refused(self):
        receipt = self.build()
        receipt["index"]["cases"][0]["artifact"] = None
        self.reseal_index(receipt)
        with self.assertRaisesRegex(ValidationError, "without artifact reference"):
            audit_receipt(receipt, expected=[0, 1])

    def test_tampered_index_or_envelope_digest_refused(self):
        receipt = self.build()
        receipt["run"]["index_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValidationError, "index digest"):
            audit_receipt(receipt, expected=[0, 1])
        receipt = self.build()
        receipt["run"]["envelope_sha256"] = "0" * 64
        with self.assertRaises(ValidationError):
            audit_receipt(receipt, expected=[0, 1], store=self.root)

    def test_store_mode_refuses_corrupt_audio(self):
        receipt = self.build()
        directory = self.root / "artifacts" / receipt["index"]["cases"][0][
            "artifact"
        ]["artifact_id"]
        audio = directory / "audio.f32le"
        original = audio.read_bytes()
        audio.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
        with self.assertRaises(ValidationError):
            audit_receipt(receipt, expected=[0, 1], store=self.root)
        audio.write_bytes(original[:-8])
        with self.assertRaises(ValidationError):
            audit_receipt(receipt, expected=[0, 1], store=self.root)

    def test_dirty_producer_identity_refused(self):
        receipt = self.build()
        receipt["producer_git"]["dirty"] = True
        with self.assertRaisesRegex(ValidationError, "frozen clean"):
            audit_receipt(receipt, expected=[0, 1])

    def test_fixed_default_denominator_is_96(self):
        self.assertEqual(DEVELOPMENT, list(range(96)))


if __name__ == "__main__":
    unittest.main()
