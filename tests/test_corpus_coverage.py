"""Synthetic strata/coverage audit controls for issue #21; no TorchSynth or holdout data."""

from __future__ import annotations

import math
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from audit_corpus_coverage import (  # noqa: E402
    AUDIO_BYTES,
    RULES_PATH,
    _cross_check_summaries,
    audio_facts,
    audit_corpus_coverage,
    classify_normalization,
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
EXPECTED = [0, 1, 2]


def rules_document():
    rules = loads((ROOT / RULES_PATH).read_bytes())
    return rules, digest(json_bytes(rules))


def receipt_from(envelope, root):
    plan = loads(read_reference(root, envelope["plan"]))
    index = loads(read_reference(root, envelope["index"]))
    attempts = envelope["attempts"]
    return dict(
        schema="torchsynth-development-corpus-evidence",
        schema_version=1,
        status=index["status"],
        profile=index["profile"],
        runtime_profile="synthetic-coverage-profile",
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
                "plan.json", "summaries/%06d.json" % len(attempts)
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


class CoverageAuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.manifest = ROOT / "spec/reference/corpus-v0.json"
        self.rules, self.rules_sha256 = rules_document()

    def render(self, name, indices=EXPECTED):
        root = self.base / name
        envelope = run_corpus(
            root,
            self.manifest,
            template(),
            lambda request, store: render_artifact(request, store, FakeBackend()),
            indices=list(indices),
        )
        return root, receipt_from(envelope, root)

    def audit(self, first, second, expected=EXPECTED):
        return audit_corpus_coverage(
            first[1],
            second[1],
            first_store=first[0],
            second_store=second[0],
            rules=self.rules,
            expected=list(expected),
            rules_sha256=self.rules_sha256,
        )

    def store_inventory(self, root):
        entries = []
        for path in sorted(Path(root).rglob("*")):
            if path.is_file():
                entries.append((str(path.relative_to(root)), path.stat().st_size))
        return entries

    def test_qualified_audit_reconciles_denominator_per_family(self):
        report = self.audit(self.render("first"), self.render("second"))
        self.assertEqual(report["verdict"], "QUALIFIED")
        self.assertEqual(report["denominator"]["observed_rows"], 3)
        self.assertEqual(report["denominator"]["holdout_identities_read"], 0)
        self.assertEqual(
            report["qualification"]["checks"]["per_case_byte_repeat"]["verdict"],
            "PASS",
        )
        for name, family in report["families"].items():
            if name == "continuous_regimes":
                continue
            rows = family["rows"]
            self.assertEqual(len(rows), 3, name)
            counts = family["reconciliation"]["counts"]
            self.assertEqual(sum(counts.values()), 3, name)
            self.assertEqual(
                sorted(row["case_id"] for row in rows),
                ["global-0", "global-1", "global-2"],
                name,
            )
        self.assertEqual(report["families"]["audio_facts"]["reconciliation"]["counts"],
                         {"measured": 3})
        self.assertEqual(
            report["families"]["pitch_stability"]["reconciliation"]["counts"],
            {"unqualified": 3},
        )
        self.assertEqual(
            report["families"]["noise_contribution_observed"]["reconciliation"][
                "counts"
            ],
            {"missing": 3},
        )
        self.assertEqual(
            report["families"]["envelope_stages_observed"]["reconciliation"][
                "counts"
            ],
            {"missing": 3},
        )
        self.assertEqual(
            report["families"]["frequency_ranges_observed"]["reconciliation"][
                "counts"
            ],
            {"unqualified": 3},
        )
        self.assertEqual(
            report["families"]["normalization_seam"]["reconciliation"]["counts"],
            {"measured": 3},
        )
        for row in report["families"]["pitch_stability"]["rows"]:
            self.assertEqual(row["requested_traces"], [])
            self.assertIn("analytic-development-only", row["reason"])
        for row in report["families"]["noise_contribution_observed"]["rows"]:
            self.assertIn("audio-only", row["reason"])
            self.assertEqual(row["intended_control_mixer_noise_normalized"], 0.5)
        for family in (
            "pitch_stability",
            "noise_contribution_observed",
            "envelope_stages_observed",
            "frequency_ranges_observed",
        ):
            self.assertEqual(
                report["families"][family]["attempted_estimators"], 0
            )
        self.assertEqual(
            {name: rows for name, rows in report["error_rows"].items() if rows},
            {},
        )

    def test_report_bytes_are_deterministic_and_path_independent(self):
        import shutil

        first = self.render("first")
        second = self.render("second")
        one = json_bytes(self.audit(first, second))
        two = json_bytes(self.audit(first, second))
        self.assertEqual(one, two)
        first_copy = self.base / "first-copy"
        second_copy = self.base / "second-copy"
        shutil.copytree(first[0], first_copy)
        shutil.copytree(second[0], second_copy)
        relocated = json_bytes(
            audit_corpus_coverage(
                first[1],
                second[1],
                first_store=first_copy,
                second_store=second_copy,
                rules=self.rules,
                expected=EXPECTED,
                rules_sha256=self.rules_sha256,
            )
        )
        self.assertEqual(one, relocated)
        self.assertNotIn(str(self.base).encode(), one)

    def test_missing_store_refused_without_directory_creation(self):
        first = self.render("first")
        second = self.render("second")
        missing = self.base / "absent-store"
        report = audit_corpus_coverage(
            first[1],
            second[1],
            first_store=missing,
            second_store=second[0],
            rules=self.rules,
            expected=EXPECTED,
            rules_sha256=self.rules_sha256,
        )
        self.assertEqual(report["verdict"], "REFUSED")
        self.assertIn("refusing to create", report["refusal"]["reason"])
        self.assertFalse(missing.exists())

    def test_holdout_identities_refused_before_payload_access(self):
        first = self.render("first")
        second = self.render("second")
        missing = self.base / "holdout-store"
        with self.assertRaisesRegex(ValidationError, "holdout"):
            self.audit(first, second, expected=[96])
        self.assertFalse(missing.exists())

    def test_short_repeat_refuses_qualification(self):
        first = self.render("first")
        second = self.render("second", indices=(0, 1))
        report = self.audit(first, second)
        self.assertEqual(report["verdict"], "REFUSED")
        self.assertEqual(
            report["refusal"]["check"], "repeat_receipt_audit"
        )

    def test_recounted_index_with_consistent_digest_still_refused(self):
        first = self.render("first")
        second = self.render("second")
        receipt = loads(json_bytes(second[1]))
        receipt["index"]["cases"] = [
            case
            for case in receipt["index"]["cases"]
            if case["case_id"] != "global-2"
        ]
        receipt["run"]["index_sha256"] = digest(json_bytes(receipt["index"]))
        report = audit_corpus_coverage(
            first[1],
            receipt,
            first_store=first[0],
            second_store=second[0],
            rules=self.rules,
            expected=EXPECTED,
            rules_sha256=self.rules_sha256,
        )
        self.assertEqual(report["verdict"], "REFUSED")
        self.assertEqual(
            report["refusal"]["check"], "repeat_receipt_audit"
        )

    def test_tampered_store_metadata_refuses_qualification(self):
        first = self.render("first")
        second_root, second_receipt = self.render("second")
        artifact = second_receipt["index"]["cases"][0]["artifact"]
        metadata_path = second_root / artifact["ref"]
        data = bytearray(metadata_path.read_bytes())
        data[50] ^= 1
        metadata_path.write_bytes(bytes(data))
        report = audit_corpus_coverage(
            first[1],
            second_receipt,
            first_store=first[0],
            second_store=second_root,
            rules=self.rules,
            expected=EXPECTED,
            rules_sha256=self.rules_sha256,
        )
        self.assertEqual(report["verdict"], "REFUSED")

    def test_audit_never_writes_into_either_store(self):
        first = self.render("first")
        second = self.render("second")
        before = (self.store_inventory(first[0]), self.store_inventory(second[0]))
        self.audit(first, second)
        after = (self.store_inventory(first[0]), self.store_inventory(second[0]))
        self.assertEqual(before, after)

    def test_parameter_strata_and_continuous_regimes_stay_continuous(self):
        report = self.audit(self.render("first"), self.render("second"))
        parameters = report["families"]["parameter_strata"]
        alpha = parameters["per_parameter"]["adsr_1.alpha"]
        self.assertEqual(
            alpha["bin_counts"],
            {
                "[0.00,0.25)": 0,
                "[0.25,0.50)": 0,
                "[0.50,0.75)": 3,
                "[0.75,1.00]": 0,
            },
        )
        self.assertEqual(alpha["observed"]["normalized_min"], 0.5)
        self.assertEqual(alpha["observed"]["normalized_max"], 0.5)
        self.assertEqual(len(parameters["per_parameter"]), 78)
        regimes = report["families"]["continuous_regimes"]
        self.assertEqual(
            regimes["dominant_weight_counts"]["lfo_1"],
            {"rsaw": 3},
        )
        self.assertEqual(
            regimes["vco_2_shape_bins"]["[0.50,0.75)"], 3
        )
        for row in report["families"]["envelope_stages_observed"]["rows"]:
            self.assertIsNone(row["value"])
            self.assertEqual(row["status"], "missing")
            self.assertEqual(
                row["requested_settings"]["attack_seconds"], 0.5
            )
            self.assertNotIn("observed_stage_lengths", row)
        raw = json_bytes(report)
        self.assertNotIn(b"selector_mode", raw)
        self.assertNotIn(b'"enum"', raw)

    def test_committed_report_diverges_when_a_row_is_dropped(self):
        first = self.render("first")
        second = self.render("second")
        original = json_bytes(self.audit(first, second))
        report = loads(original)
        dropped = report["families"]["pitch_stability"]["rows"].pop()
        report["families"]["pitch_stability"]["reconciliation"]["counts"][
            "unqualified"
        ] -= 1
        self.assertNotEqual(json_bytes(report), original)
        self.assertEqual(dropped["status"], "unqualified")


class AudioFactBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.rules, _ = rules_document()

    def facts(self, values):
        require_exact = AUDIO_BYTES
        data = struct.pack("<%df" % len(values), *values)
        self.assertEqual(len(data), require_exact or len(data))
        return audio_facts(data, self.rules)

    def silence(self, fill=0.0):
        return [fill] * SAMPLES

    def test_exact_silence_and_near_silence(self):
        facts = self.facts(self.silence())
        self.assertTrue(facts["exact_silence"])
        self.assertTrue(facts["near_silence"])
        self.assertEqual(facts["signed_zero_sample_count"], 0)
        self.assertEqual(facts["peak_abs"], 0.0)
        self.assertEqual(facts["peak_index"], 0)

    def test_signed_zero_is_exact_silence_and_bytes_are_reported(self):
        facts = self.facts(self.silence(fill=-0.0))
        self.assertTrue(facts["exact_silence"])
        self.assertTrue(facts["near_silence"])
        self.assertEqual(facts["signed_zero_sample_count"], SAMPLES)

    def test_tiny_nonzero_is_not_silence_but_is_near_silence(self):
        values = self.silence()
        values[5] = 8e-05
        facts = self.facts(values)
        self.assertFalse(facts["exact_silence"])
        self.assertTrue(facts["near_silence"])
        self.assertEqual(facts["peak_index"], 5)
        self.assertAlmostEqual(facts["rms"], 8e-05 / 420.0, delta=1e-12)

    def test_strict_rms_cutoff_boundary(self):
        values = self.silence()
        values[5] = 1e-03
        facts = self.facts(values)
        self.assertGreater(facts["rms"], 1e-06)
        self.assertFalse(facts["near_silence"])

    def test_strict_peak_cutoff_boundary(self):
        values = self.silence()
        values[7] = 2e-04
        facts = self.facts(values)
        self.assertLess(facts["rms"], 1e-06)
        self.assertGreaterEqual(facts["peak_abs"], 1e-04)
        self.assertFalse(facts["near_silence"])

    def test_full_scale_boundary_counts_and_clipping_rule(self):
        values = self.silence()
        values[0] = 1.0
        values[1] = -1.0
        values[2] = 1.5
        facts = self.facts(values)
        self.assertEqual(facts["beyond_full_scale_count"], 1)
        self.assertEqual(facts["at_full_scale_count"], 3)

    def test_peak_tie_first_index_wins(self):
        values = self.silence()
        values[10] = 0.7
        values[5000] = 0.7
        values[9000] = -0.7
        facts = self.facts(values)
        self.assertEqual(facts["peak_index"], 10)
        float32 = struct.unpack("<f", struct.pack("<f", 0.7))[0]
        self.assertEqual(facts["peak_signed"], float32)
        self.assertEqual(facts["peak_abs"], abs(float32))
        self.assertAlmostEqual(
            facts["peak_time_seconds"], 10 / 44100.0, delta=0.0
        )

    def test_nonfinite_and_wrong_framing_refuse(self):
        with self.assertRaisesRegex(ValidationError, "nonfinite"):
            self.facts(self.silence()[: SAMPLES - 1] + [float("nan")])
        with self.assertRaisesRegex(ValidationError, "byte count"):
            audio_facts(b"\x00" * 16, self.rules)

    def test_producer_summary_cross_check_refuses_false_rows(self):
        facts = audio_facts(
            struct.pack("<%df" % SAMPLES, *self.silence()), self.rules
        )
        recorded = dict(
            rms=0.25,
            dc_mean=0.0,
            peak_abs=0.0,
            peak_index=0,
            clipped_sample_count=0,
            normalization_gain=1.0,
        )
        with self.assertRaisesRegex(ValidationError, "summary rows disagree"):
            _cross_check_summaries(recorded, facts)
        recorded["rms"] = facts["rms"]
        _cross_check_summaries(recorded, facts)


class NormalizationSeamTests(unittest.TestCase):
    def test_below_threshold_is_not_applied_and_tie_unchanged(self):
        row = classify_normalization(
            dict(
                pre_normalization_peak=0.5,
                normalization_gain_derived=1.0,
                pre_normalization_sha256="a" * 64,
                richer_module_facts=dict(state="unavailable"),
            ),
            1.0,
        )
        self.assertEqual(row["classification"], "not_applied")
        self.assertIsNone(row["derived_pre_peak"])
        tie = classify_normalization(
            dict(
                pre_normalization_peak=1.0,
                normalization_gain_derived=1.0,
                pre_normalization_sha256="a" * 64,
                richer_module_facts=dict(state="unavailable"),
            ),
            1.0,
        )
        self.assertEqual(tie["classification"], "not_applied")

    def test_above_threshold_is_active_with_derived_pre_peak(self):
        row = classify_normalization(
            dict(
                pre_normalization_peak=2.0,
                normalization_gain_derived=0.5,
                pre_normalization_sha256="a" * 64,
                richer_module_facts=dict(state="unavailable"),
            ),
            0.5,
        )
        self.assertEqual(row["classification"], "active")
        self.assertEqual(row["derived_pre_peak"], 2.0)

    def test_missing_observations_stay_missing_not_bypass(self):
        row = classify_normalization(None, 1.0)
        self.assertEqual(row["status"], "missing")
        self.assertEqual(row["classification"], "missing")

    def test_gain_or_metadata_mismatch_refuse(self):
        observations = dict(
            pre_normalization_peak=2.0,
            normalization_gain_derived=0.4,
            pre_normalization_sha256="a" * 64,
            richer_module_facts=dict(state="unavailable"),
        )
        with self.assertRaisesRegex(ValidationError, "contract gain"):
            classify_normalization(observations, 0.4)
        observations["normalization_gain_derived"] = 0.5
        with self.assertRaisesRegex(ValidationError, "metadata"):
            classify_normalization(observations, 1.0)
        with self.assertRaisesRegex(ValidationError, "observation fields"):
            classify_normalization(dict(observations, extra=1), 0.5)


if __name__ == "__main__":
    unittest.main()
