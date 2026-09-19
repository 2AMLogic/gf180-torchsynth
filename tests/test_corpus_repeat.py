"""Synthetic repeat-comparison controls for issue #20; no TorchSynth or holdout data."""

from __future__ import annotations

import copy
import math
import os
import shutil
import struct
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from compare_development_corpus import (  # noqa: E402
    compare_development_corpus,
    paired_audio_metrics,
)
from torchsynth_voice.artifact_renderer import (  # noqa: E402
    digest,
    json_bytes,
    render_artifact,
)
from torchsynth_voice.artifacts import ValidationError, loads  # noqa: E402
from torchsynth_voice.corpus import read_reference, run_corpus  # noqa: E402
from test_artifact_renderer import FakeBackend, template  # noqa: E402

SAMPLES = 176400


class ReceiptShapedBackend(FakeBackend):
    """FakeBackend with a full v1 worker-receipt shape for envelope classification."""

    def __call__(self, request, *, capture_provider=None):
        observation = super().__call__(request, capture_provider=capture_provider)
        inner = observation["receipt"]
        observation["receipt"] = dict(
            schema="torchsynth-render-receipt",
            schema_version=1,
            request_sha256="0" * 64,
            worker_sha256="1" * 64,
            source_sha256="2" * 64,
            source_validated_before_import=True,
            runtime=dict(synthetic_runtime=True),
            rng_sentinel="3" * 64,
            runtime_profile="synthetic-repeat-profile",
            image="synthetic-image",
            exit_code=0,
            warning_categories=inner.get("warning_categories", []),
            execution_id=str(uuid.uuid4()),
            process_id=os.getpid(),
            started_utc=datetime.now(timezone.utc).isoformat(),
            command=["synthetic", str(uuid.uuid4())],
            host=dict(synthetic_host=True),
            stdout_sha256="4" * 64,
            stderr_sha256="5" * 64,
        )
        return observation


def receipt_from(envelope, root):
    plan = loads(read_reference(root, envelope["plan"]))
    index = loads(read_reference(root, envelope["index"]))
    return dict(
        schema="torchsynth-development-corpus-evidence",
        schema_version=1,
        status=index["status"],
        profile=index["profile"],
        runtime_profile="synthetic-repeat-profile",
        source_commit=plan["template"]["source"]["commit"],
        expectation=dict(
            development_indices=[case["sound_index"] for case in plan["selection"]],
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
            envelope_size_bytes=envelope["index"]["size_bytes"],
            index_ref=envelope["index"]["ref"],
            index_sha256=envelope["index"]["sha256"],
            plan_sha256=envelope["plan"]["sha256"],
        ),
        plan=plan,
        index=index,
    )


class RepeatComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.manifest = ROOT / "spec/reference/corpus-v0.json"

    def render(self, name, mutate=None, indices=(0, 1)):
        root = self.base / name
        backend = ReceiptShapedBackend()
        if mutate is not None:
            backend.mutate = mutate
        envelope = run_corpus(
            root,
            self.manifest,
            template(),
            lambda request, store: render_artifact(request, store, backend),
            indices=list(indices),
        )
        return root, receipt_from(envelope, root)

    @staticmethod
    def bump_sample(observation, index, value):
        buffer = bytearray(observation["audio"])
        struct.pack_into("<f", buffer, index * 4, value)
        observation["audio"] = bytes(buffer)

    def compare(self, first, second, expected=(0, 1)):
        return compare_development_corpus(
            first[1],
            second[1],
            first_store=first[0],
            second_store=second[0],
            expected=list(expected),
        )

    def test_complete_synthetic_repeat_with_recorded_telemetry(self):
        first = self.render("first")
        second = self.render("second")
        report = self.compare(first, second)
        self.assertEqual(report["verdict"], "COMPLETE-REPEAT")
        self.assertEqual(report["summary"]["complete_rows"], 2)
        self.assertEqual(report["index_bytes"]["verdict"], "EQUAL")
        self.assertEqual(report["envelope"]["verdict"], "IMMUTABLE_EQUAL")
        self.assertNotEqual(first[1]["run"]["run_id"], second[1]["run"]["run_id"])
        self.assertIn("run_id", report["envelope"]["volatile_fields_differing"])
        for row in report["cases"]:
            self.assertEqual(row["audio"], "EQUAL")
            self.assertEqual(row["metadata"], "EQUAL")
            self.assertEqual(row["traces"], "NOT_REQUESTED")
            self.assertEqual(row["classification"], "EQUAL")
        self.assertIsNone(report["cases"][0].get("numeric"))

    def test_report_bytes_are_deterministic_for_identical_inputs(self):
        first = self.render("first")
        second = self.render("second")
        one = json_bytes(self.compare(first, second))
        two = json_bytes(self.compare(first, second))
        self.assertEqual(one, two)

    def test_audio_mismatch_numeric_rows_with_independent_expectations(self):
        first = self.render("first")
        second = self.render(
            "second", mutate=lambda o: self.bump_sample(o, 50, 0.5)
        )
        report = self.compare(first, second)
        self.assertEqual(report["verdict"], "INCOMPLETE")
        row = report["cases"][0]
        self.assertEqual(row["audio"], "MISMATCH")
        self.assertEqual(row["classification"], "AUDIO-OR-INPUT-IDENTITY")
        numeric = row["numeric"]
        self.assertEqual(numeric["verdict"], "MISMATCH")
        diffs = [0.0] * SAMPLES
        diffs[50] = 0.5
        self.assertEqual(numeric["mean_error"], math.fsum(diffs) / SAMPLES)
        self.assertEqual(
            numeric["mean_absolute_error"],
            math.fsum(abs(d) for d in diffs) / SAMPLES,
        )
        self.assertEqual(
            numeric["rms_error"],
            math.sqrt(math.fsum(d * d for d in diffs) / SAMPLES),
        )
        self.assertEqual(numeric["maximum_absolute_error"], 0.5)
        self.assertEqual(numeric["maximum_absolute_error_sample"], 50)
        self.assertEqual(numeric["differing_samples"], 1)
        self.assertEqual(numeric["first_divergent_byte"], 203)
        self.assertEqual(numeric["first_divergent_sample"], 50)
        self.assertEqual(numeric["first_divergent_sample_first_value"], 0.0)
        self.assertEqual(numeric["first_divergent_sample_second_value"], 0.5)

    def test_signed_zero_byte_mismatch_stays_visible_with_zero_error(self):
        first = self.render("first")

        def signed_zero(observation):
            buffer = bytearray(observation["audio"])
            struct.pack_into("<f", buffer, 20, -0.0)
            observation["audio"] = bytes(buffer)

        second = self.render("second", mutate=signed_zero)
        report = self.compare(first, second)
        row = report["cases"][0]
        self.assertEqual(row["audio"], "MISMATCH")
        self.assertNotEqual(
            row["audio_sha256"]["first"], row["audio_sha256"]["second"]
        )
        numeric = row["numeric"]
        self.assertEqual(numeric["mean_error"], 0.0)
        self.assertEqual(numeric["rms_error"], 0.0)
        self.assertEqual(numeric["differing_samples"], 1)
        self.assertEqual(numeric["first_divergent_byte"], 23)
        self.assertEqual(numeric["first_divergent_sample"], 5)
        self.assertEqual(report["verdict"], "INCOMPLETE")

    def test_short_second_run_refuses_repeat_with_denominator_recorded(self):
        first = self.render("first")
        second = self.render("second", indices=(0,))
        report = self.compare(first, second)
        self.assertEqual(report["verdict"], "INCOMPLETE")
        self.assertEqual(report["second"]["receipt_audit"], "REFUSED")
        self.assertIn("fixed development set", report["second"]["refusal"])

    def test_tampered_store_yields_structured_side_refusal(self):
        first = self.render("first")
        second_root, second_receipt = self.render("second")
        audio = second_root / "artifacts" / second_receipt["index"]["cases"][0][
            "artifact"
        ]["artifact_id"] / "audio.f32le"
        data = bytearray(audio.read_bytes())
        data[100] ^= 1
        audio.write_bytes(bytes(data))
        report = self.compare(first, (second_root, second_receipt))
        self.assertEqual(report["verdict"], "INCOMPLETE")
        self.assertEqual(report["second"]["receipt_audit"], "REFUSED")
        self.assertIn("refusal", report["second"])

    def test_falsified_envelope_counter_refuses_the_side(self):
        first = self.render("first")
        second_root, second_receipt = self.render("second")
        envelope_path = second_root / second_receipt["run"]["envelope_ref"]
        envelope = loads(envelope_path.read_bytes())
        envelope["counts"]["success"] += 1
        envelope_path.write_bytes(json_bytes(envelope))
        report = self.compare(first, (second_root, second_receipt))
        self.assertEqual(report["verdict"], "INCOMPLETE")
        self.assertEqual(report["second"]["receipt_audit"], "REFUSED")

    def test_same_root_and_symlink_and_hardlink_alias_refused(self):
        first = self.render("first")
        second = self.render("second")
        with self.assertRaisesRegex(ValidationError, "same store root"):
            self.compare(first, first)
        link = self.base / "link-to-first"
        link.symlink_to(first[0])
        with self.assertRaisesRegex(ValidationError, "same store root"):
            self.compare(first, (link, first[1]))
        clone = self.base / "hardlinked-first"
        shutil.copytree(first[0], clone, copy_function=os.link)
        report = self.compare(first, (clone, first[1]))
        self.assertEqual(report["verdict"], "INCOMPLETE")
        self.assertEqual(report["second"]["receipt_audit"], "REFUSED")
        self.assertIn("unaliased", report["second"]["refusal"])

    def test_holdout_identity_refused_before_data_access(self):
        first = self.render("first")
        second = self.render("second")
        with self.assertRaisesRegex(ValidationError, "holdout"):
            self.compare(first, second, expected=(0, 96))

    def test_missing_store_tree_refused_and_never_created(self):
        first = self.render("first")
        second = self.render("second")
        absent = self.base / "absent-store"
        report = compare_development_corpus(
            first[1],
            second[1],
            first_store=first[0],
            second_store=absent,
            expected=[0, 1],
        )
        self.assertEqual(report["verdict"], "INCOMPLETE")
        self.assertEqual(report["second"]["receipt_audit"], "REFUSED")
        self.assertIn("missing", report["second"]["refusal"])
        self.assertFalse(absent.exists())

    def test_comparison_neither_store_nor_missing_inputs_are_written(self):
        first = self.render("first")
        second = self.render("second")

        def snapshot(root):
            state = {}
            for path in sorted(Path(root).rglob("*")):
                if path.is_file():
                    state[str(path)] = (path.read_bytes(), path.stat().st_mtime_ns)
                else:
                    state[str(path)] = path.stat().st_mtime_ns
            return state

        before = {str(first[0]): snapshot(first[0]), str(second[0]): snapshot(second[0])}
        absent = self.base / "also-absent"
        self.compare(first, second)
        compare_development_corpus(
            first[1],
            second[1],
            first_store=first[0],
            second_store=absent,
            expected=[0, 1],
        )
        after = {str(first[0]): snapshot(first[0]), str(second[0]): snapshot(second[0])}
        self.assertEqual(before, after)
        self.assertFalse(absent.exists())

    def test_second_render_never_reads_first_store_measured_outputs(self):
        first = self.render("first")
        second_root, _ = self.render("second")
        marker = str(first[0]).encode()
        for path in second_root.rglob("*"):
            if path.is_file():
                self.assertNotIn(marker, path.read_bytes())

    def test_tampered_receipt_index_without_reseal_refuses_the_side(self):
        first = self.render("first")
        second_root, second_receipt = self.render("second")
        receipt = copy.deepcopy(second_receipt)
        receipt["index"]["cases"][0]["fixture"]["sound_index"] = 7
        report = self.compare(first, (second_root, receipt))
        self.assertEqual(report["verdict"], "INCOMPLETE")
        self.assertEqual(report["second"]["receipt_audit"], "REFUSED")
        report = self.compare(first, (second_root, second_receipt))
        self.assertEqual(report["cases"][0]["index_record"], "EQUAL")

    def test_paired_metrics_refuse_invalid_inputs(self):
        short = bytes(SAMPLES * 4 - 4)
        result = paired_audio_metrics(short, short)
        self.assertEqual(result["verdict"], "REFUSED")
        self.assertIn("byte count", result["reason"])
        nan = struct.pack("<f", float("nan")) * SAMPLES
        result = paired_audio_metrics(nan, nan)
        self.assertEqual(result["verdict"], "REFUSED")
        self.assertIn("nonfinite", result["reason"])


if __name__ == "__main__":
    unittest.main()
