"""Analytic apparatus controls only; no TorchSynth runtime claims."""

import copy
import hashlib
import importlib.util
import json
import math
import struct
from pathlib import Path
import unittest
from dataclasses import replace

from torchsynth_voice.paired_metrics import analytic_exactness_rubric, scorecard_rows
from torchsynth_voice.preparation import (
    TRANSFORMS,
    Preparation,
    Signal,
    check_invariant,
    compare_exact,
    gate_rows,
    invariant_trial,
    prepare,
    prepare_pair,
    resample_diagnostic,
    runtime_evidence_control,
    symmetry_control,
)
from torchsynth_voice.scorecard import validate_row
from torchsynth_voice.identity import SoundIdentity


def ratio(pair):
    """Toy consumer, not a qualified production estimator."""
    return [sum(pair[1]["prepared_samples"]) / sum(pair[0]["prepared_samples"])]


def onset_spec():
    return Preparation(
        estimator="synthetic-ratio",
        estimator_version="1",
        path="property",
        unit="amplitude",
        time_origin="onset",
        window=(0, 3),
        allowed=("window",),
        forbidden=tuple(t for t in TRANSFORMS if t != "window"),
        invariances=("onset_shift", "silence_padding", "common_gain"),
        measure_kind="ratio",
        gain_domain=(0.25, 4.0),
    )


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.spec = Preparation("paired", "1", "exact", "amplitude")
        self.ref = Signal([1.0, -0.0, 0.5], 44100, "amplitude")

    def test_identity_and_hashes_preserve_signed_zero(self):
        result = prepare(self.ref, self.spec)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["raw_samples"], result["prepared_samples"])
        self.assertEqual(result["raw_sha256"], result["prepared_sha256"])
        self.assertEqual(result, prepare(self.ref, self.spec))
        self.assertEqual(math.copysign(1, result["prepared_samples"][1]), -1)
        self.assertEqual(self.ref.samples, [1.0, -0.0, 0.5])

    def test_every_transform_refused_on_exact_path(self):
        for operation in TRANSFORMS + ("invented",):
            with self.subTest(operation=operation):
                result = prepare(self.ref, self.spec, operations=(operation,))
                self.assertEqual(result["status"], "refused")
                self.assertIsNone(result["prepared_samples"])
                self.assertIn(operation, result["reason"])
        self.assertEqual(
            prepare(self.ref, replace(self.spec, window=(0, 2)))["status"], "refused"
        )

    def test_invalid_signal_and_metadata_refuse(self):
        for samples in (
            [],
            None,
            [True],
            [[1]],
            [float("nan")],
            [float("inf")],
            [2**53 + 1],
        ):
            with self.subTest(samples=samples):
                self.assertEqual(
                    prepare(replace(self.ref, samples=samples), self.spec)["status"],
                    "refused",
                )
        for signal in (
            replace(self.ref, sample_rate_hz=0),
            replace(self.ref, unit="volts"),
            replace(self.ref, time_origin_samples=0.5),
        ):
            self.assertEqual(prepare(signal, self.spec)["status"], "refused")

    def test_declaration_is_exhaustive_and_fail_closed(self):
        for change in (
            {"forbidden": ()},
            {"allowed": ("window",)},
            {"boundary": "zero-pad"},
            {"refusal_conditions": ()},
            {"estimator_version": ""},
        ):
            self.assertEqual(
                prepare(self.ref, replace(self.spec, **change))["status"], "refused"
            )

    def test_pair_symmetry_and_mutant(self):
        other = replace(self.ref, samples=[2, 0, 1])
        forward = prepare_pair(self.ref, other, self.spec)
        backward = prepare_pair(other, self.ref, self.spec)
        self.assertEqual(symmetry_control(forward, backward)["status"], "pass")
        mutant = copy.deepcopy(forward)
        mutant[1]["prepared_samples"].reverse()  # Sum/mean is still plausible.
        self.assertEqual(symmetry_control(mutant, backward)["status"], "fail")
        self.assertEqual(symmetry_control((), ())["status"], "fail")

    def test_exact_retains_timing_gain_and_frame_errors(self):
        for samples in ([0, 1, 0.5], [2, 0, 1], [1, 0]):
            result = compare_exact(
                self.ref, replace(self.ref, samples=samples), self.spec
            )
            comparison = result["comparison"]
            self.assertNotEqual(comparison["metrics"]["exact_equal"]["value"], 1)
        result = compare_exact(
            self.ref, replace(self.ref, time_origin_samples=1), self.spec
        )
        self.assertEqual(result["status"], "refused")
        self.assertIsNone(result["comparison"])
        self.assertEqual(
            compare_exact(self.ref, self.ref, onset_spec())["status"], "refused"
        )

    def test_window_boundaries_and_no_extension(self):
        for amplitude in (1.0, 1e-14):
            for onset in (0, 2):
                signal = Signal(
                    [0.0] * onset + [amplitude, amplitude / 2, 0],
                    44100,
                    "amplitude",
                    onset_sample=onset,
                )
                result = prepare(signal, onset_spec())
                self.assertEqual(
                    result["prepared_samples"], [amplitude, amplitude / 2, 0]
                )
                self.assertEqual(result["window_bounds"], [onset, onset + 3])
                tail = prepare(signal, replace(onset_spec(), window=(2, 3)))
                self.assertEqual(tail["prepared_samples"], [0])
                self.assertEqual(
                    prepare(signal, replace(onset_spec(), window=(0, 4)))["status"],
                    "refused",
                )

    def test_shift_padding_gain_and_applicability(self):
        ref = Signal([0, 1, 2, 1, 0], 44100, "amplitude", onset_sample=1)
        cand = replace(ref, samples=[0, 2, 4, 2, 0])
        for kind, value in (
            ("onset_shift", 4),
            ("onset_shift", -1),
            ("silence_padding", 5),
            ("common_gain", 0.25),
            ("common_gain", 4),
        ):
            trial = invariant_trial(
                ref, cand, onset_spec(), ratio, kind=kind, amount=value
            )
            self.assertEqual(trial["status"], "pass", trial)
        for spec, kind, value in (
            (self.spec, "onset_shift", 1),
            (replace(onset_spec(), measure_kind="absolute"), "common_gain", 2),
            (onset_spec(), "common_gain", 0),
            (onset_spec(), "common_gain", -1),
            (onset_spec(), "onset_shift", -2),
        ):
            self.assertEqual(
                invariant_trial(ref, cand, spec, ratio, kind=kind, amount=value)[
                    "status"
                ],
                "not_applicable",
            )

    def test_time_varying_errors_do_not_collapse(self):
        result = compare_exact(
            Signal([0] * 8, 44100, "amplitude"),
            Signal([1] * 4 + [-1] * 4, 44100, "amplitude"),
            self.spec,
            window_samples=4,
        )
        metrics = result["comparison"]["metrics"]
        self.assertEqual(metrics["mean_error"]["value"], 0)
        self.assertEqual(metrics["window.0.mean_error"]["value"], 1)
        self.assertEqual(metrics["window.1.mean_error"]["value"], -1)
        self.assertEqual(check_invariant("windows", [1, -1], [-1, 1])["status"], "fail")

    def test_gate_preserves_rejected_values_and_validates_rows(self):
        comparison = compare_exact(self.ref, self.ref, self.spec)["comparison"]
        rows, raw = scorecard_rows(
            comparison,
            case_id="synthetic",
            partition="development",
            trace="output",
            rubric=analytic_exactness_rubric(),
        )
        control = check_invariant("asymmetric-mutant", [1, 2], [2, 1])
        gated, diagnostic = gate_rows(
            rows,
            controls=[control],
            preparation=prepare_pair(self.ref, self.ref, self.spec),
            raw_diagnostic=raw,
        )
        self.assertTrue(all(row["verdict"] == "NO VERDICT" for row in gated))
        for row in gated:
            validate_row(row)
            self.assertIsNone(row["observed"])
            self.assertEqual(
                row["artifact"]["sha256"], hashlib.sha256(diagnostic).hexdigest()
            )
        record = json.loads(diagnostic)
        self.assertTrue(any(row["observed"] == 1 for row in record["raw_rows"]))
        self.assertEqual(record["raw_diagnostic_hex"], raw.hex())
        for controls in (
            [],
            [{"status": "pass"}],
            [check_invariant("n/a", [1], [1], applicable=False, reason="wrong domain")],
        ):
            refused, _ = gate_rows(
                rows,
                controls=controls,
                preparation=prepare_pair(self.ref, self.ref, self.spec),
                raw_diagnostic=raw,
            )
            self.assertTrue(all(row["verdict"] == "NO VERDICT" for row in refused))

    def test_resampling_bound_and_exact_exclusion(self):
        for rate in (8000, 44100, 48000):
            frequency = rate / 32
            samples = [math.cos(2 * math.pi * frequency * i / rate) for i in range(65)]
            result = resample_diagnostic(
                Signal(samples, rate, "amplitude"),
                target_rate_hz=rate * 2,
                max_frequency_hz=frequency,
                amplitude_bound=1,
            )
            self.assertEqual(result["status"], "valid")
            errors = [
                abs(x - math.cos(2 * math.pi * frequency * i / (rate * 2)))
                for i, x in enumerate(result["prepared_samples"])
            ]
            self.assertLessEqual(max(errors), result["error_bound"])
            self.assertEqual(result["prepared_samples"][-1], samples[-1])
            self.assertTrue(result["diagnostic_only"])
        for rate, frequency in ((22050, 100), (88200, 2000), (200000, 100)):
            result = resample_diagnostic(
                self.ref,
                target_rate_hz=rate,
                max_frequency_hz=frequency,
                amplitude_bound=1,
            )
            self.assertEqual(result["status"], "refused")

    def test_gate_rejects_mutated_bytes_and_diagnostic_acceptance(self):
        comparison = compare_exact(self.ref, self.ref, self.spec)["comparison"]
        rows, raw = scorecard_rows(
            comparison,
            case_id="synthetic",
            partition="development",
            trace="output",
            rubric=analytic_exactness_rubric(),
        )
        pair = prepare_pair(self.ref, self.ref, self.spec)
        controls = [symmetry_control(pair, pair)]
        good, _ = gate_rows(
            rows, controls=controls, preparation=pair, raw_diagnostic=raw
        )
        self.assertEqual([r["verdict"] for r in good], [r["verdict"] for r in rows])
        with self.assertRaisesRegex(ValueError, "digest"):
            gate_rows(
                rows, controls=controls, preparation=pair, raw_diagnostic=raw + b" "
            )
        mutated = copy.deepcopy(pair)
        mutated[0]["prepared_samples"][0] = 0
        refused, _ = gate_rows(
            rows, controls=controls, preparation=mutated, raw_diagnostic=raw
        )
        self.assertTrue(all(r["verdict"] == "NO VERDICT" for r in refused))
        diagnostic = resample_diagnostic(
            self.ref, target_rate_hz=88200, max_frequency_hz=100, amplitude_bound=1
        )
        refused, _ = gate_rows(
            rows,
            controls=controls,
            preparation=(diagnostic, diagnostic),
            raw_diagnostic=raw,
        )
        self.assertTrue(all(r["verdict"] == "NO VERDICT" for r in refused))


class RuntimeConsumerTests(unittest.TestCase):
    """Synthetic projection controls; never actual runtime qualification."""

    def setUp(self):
        raw = struct.pack("<2f", 0.25, 0.5)
        digest = hashlib.sha256(raw).hexdigest()
        self.files = {"synthetic.f32le": raw}
        artifacts = {
            name: {"locator": "synthetic.f32le", "sha256": digest}
            for name in ("normalized", "physical", "noise", "audio")
        }
        self.expected = {
            "producer": {"commit": "a" * 40, "report_sha256": "b" * 64},
            "source": {"commit": "c" * 40, "files": {"synth.py": "d" * 64}},
            "configuration_sha256": "e" * 64,
            "parameter_names": ["a", "b"],
            "artifacts": {name: 2 for name in artifacts},
            "equal_artifacts": list(artifacts),
            "cells": [],
            "repeat_groups": [["repeat-1", "repeat-2"]],
            "equality_groups": [["repeat-1", "repeat-2"]],
        }
        cells = []
        for repeat in (1, 2):
            request = {
                "key": f"repeat-{repeat}",
                "runtime": {"lock_sha256": "f" * 64},
                "execution_width": 32,
                "reproducible": True,
                "case_definition": {"id": "synthetic"},
                "sound_index": 39942,
                "identity_batch_size": 32,
            }
            self.expected["cells"].append(request)
            cells.append(
                {
                    **request,
                    "status": "PASS",
                    "identity": SoundIdentity(39942).to_dict(32),
                    "normalized": {"a": 0.25, "b": 0.5},
                    "physical": {"a": 0.25, "b": 0.5},
                    "artifacts": copy.deepcopy(artifacts),
                    "process_identity": f"synthetic-process-{repeat}",
                }
            )
        self.evidence = {
            k: copy.deepcopy(self.expected[k])
            for k in ("producer", "source", "configuration_sha256")
        }
        self.evidence.update(
            schema_version=1,
            evidence_category="synthetic",
            cells=cells,
            oracle_status="normative",
            diagnostics={"fixture": "synthetic only"},
        )

    def consume(self):
        return runtime_evidence_control(
            self.evidence, self.expected, self.files.__getitem__
        )

    def test_complete_synthetic_cannot_be_normative_evidence(self):
        result = self.consume()
        self.assertEqual(result["status"], "pass", result)
        self.assertFalse(result["normative_oracle"])
        self.assertEqual(result["evidence_category"], "synthetic")
        signal = Signal([1], 44100, "amplitude")
        spec = Preparation("synthetic-runtime-dependent", "1", "exact", "amplitude")
        comparison = compare_exact(signal, signal, spec)["comparison"]
        rows, raw = scorecard_rows(
            comparison,
            case_id="synthetic",
            partition="development",
            trace="output",
            rubric=analytic_exactness_rubric(),
        )
        gated, _ = gate_rows(
            rows,
            controls=[result],
            preparation=prepare_pair(signal, signal, spec),
            raw_diagnostic=raw,
        )
        self.assertTrue(all(row["verdict"] == "NO VERDICT" for row in gated))

    def test_missing_and_failed_cases_refuse(self):
        self.assertEqual(
            runtime_evidence_control(None, self.expected, self.files.__getitem__)[
                "status"
            ],
            "refused",
        )
        self.evidence["cells"].pop()
        self.assertEqual(self.consume()["status"], "refused")
        self.setUp()
        self.evidence["cells"][0].update(
            status="NO_VERDICT", reason="explicit resource refusal"
        )
        result = self.consume()
        self.assertEqual(result["status"], "refused")
        self.assertIn("resource refusal", result["reason"])
        self.evidence["cells"] = []
        self.expected["cells"] = []
        self.assertIn("vacuous", self.consume()["reason"])

    def test_stale_report_config_and_source_refuse(self):
        for field in ("source", "producer", "configuration_sha256"):
            with self.subTest(field=field):
                self.setUp()
                self.evidence[field] = "stale"
                self.assertEqual(self.consume()["status"], "refused")

    def test_wrong_identity_named_patch_noise_or_runtime_refuse(self):
        for field in (
            "runtime",
            "identity",
            "normalized",
            "physical",
            "case_definition",
        ):
            with self.subTest(field=field):
                self.setUp()
                self.evidence["cells"][0][field] = {}
                self.assertEqual(self.consume()["status"], "refused")
        self.setUp()
        self.files["synthetic.f32le"] = struct.pack("<2f", 0.5, 0.25)
        self.assertIn("hash/count mismatch", self.consume()["reason"])

    def test_reused_process_or_divergent_repeat_refuse(self):
        self.evidence["cells"][1]["process_identity"] = "synthetic-process-1"
        self.assertIn("reused a process", self.consume()["reason"])
        self.setUp()
        raw = struct.pack("<2f", 0.5, 0.25)
        self.files["changed.f32le"] = raw
        self.evidence["cells"][1]["artifacts"]["audio"] = {
            "locator": "changed.f32le",
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        result = self.consume()
        self.assertEqual(result["status"], "refused")
        self.assertIn("divergence", result["reason"])

    def test_scalar_decision_is_enforced(self):
        for status in ("diagnostic", "rejected"):
            self.evidence["oracle_status"] = status
            result = self.consume()
            self.assertEqual(result["status"], "refused")
            self.assertIn(status, result["reason"])
            self.assertFalse(result["normative_oracle"])


class QualificationCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / "tools/qualify_preparation.py"
        spec = importlib.util.spec_from_file_location("qualify_preparation", path)
        cls.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.tool)

    def test_analytic_report_has_reproducible_actual_record_hashes(self):
        report = self.tool.analytic_report()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["runtime_integration"]["status"], "PENDING")
        self.assertEqual(
            self.tool.record_bytes(report),
            self.tool.record_bytes(self.tool.analytic_report()),
        )
        for digest, artifact in report["artifacts"].items():
            self.assertEqual(
                hashlib.sha256(self.tool.record_bytes(artifact)).hexdigest(), digest
            )

    def test_strict_runtime_json_and_safe_paths(self):
        for data in (b'{"a": 1, "a": 2}', b'{"a": NaN}', b'{"a": 1e999}'):
            with self.assertRaises(ValueError):
                self.tool.strict_json(data)
        with self.assertRaisesRegex(ValueError, "escapes"):
            self.tool.safe_read(Path.cwd(), "../outside")

    def test_integration_requires_exact_producer_commit(self):
        result = self.tool.integration_report(Path.cwd(), "HEAD", "scalar", Path.cwd())
        self.assertEqual(result["status"], "NO_VERDICT")
        self.assertIn("exact 40-digit SHA", result["reason"])
        self.assertIn("pending", result["closure"])


if __name__ == "__main__":
    unittest.main()
