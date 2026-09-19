"""Analytic apparatus controls only; no TorchSynth runtime claims."""

import copy
import hashlib
import importlib.util
import json
import math
import struct
import tempfile
import unittest
import uuid
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from torchsynth_voice.identity import SoundIdentity
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

    def test_historical_runner_is_explicit_and_repeatability_only(self):
        result = self.tool.integration_report(
            Path.cwd(),
            "a" * 40,
            "scalar",
            Path.cwd(),
            historical_runner="2182bc9524016476f9a538d11fe2e3035fe0ae45",
        )
        self.assertEqual(result["evidence_status"], "REFUSED")
        self.assertIn("repeatability only", result["reason"])

    def test_repeatability_historical_selection_reaches_same_public_gate(self):
        revision = "2182bc9524016476f9a538d11fe2e3035fe0ae45"
        historical_hash = "b" * 64
        plan = {
            "schema_version": 2,
            "profile": "release-mkl-compatible-v1",
            "profile_environment": {
                "release": self.tool.SCALAR_MATH_PROFILE,
                "current": {"ATEN_CPU_CAPABILITY": None, "MKL_CBWR": None},
            },
            "sample_count": 176400,
            "control_sample_count": 1764,
            "nebula": "default",
            "noise_seed": 13,
            "configuration": {"reproducible": True},
            "source_commit": "synthetic",
        }
        native = {
            "status": "PASS",
            "source_commit": "synthetic",
            "plan_sha256": "plan",
            "provenance": {"runner_sha256": historical_hash},
            "cells": [],
        }
        data = self.tool.record_bytes(native)
        report = dict(native, raw_report_sha256=self.tool.sha(data))
        runner = SimpleNamespace(
            validate_plan=Mock(),
            plan_hash=Mock(return_value="plan"),
            historical_runner_hash=Mock(return_value=historical_hash),
            validate_results=Mock(side_effect=ValueError("public gate reached")),
        )
        with patch.object(self.tool, "safe_read", return_value=data):
            with self.assertRaisesRegex(ValueError, "stale producer implementation"):
                self.tool.repeatability_projection(
                    report,
                    plan,
                    runner,
                    "a" * 64,
                    Path.cwd(),
                    {},
                    {},
                )
            runner.historical_runner_hash.assert_not_called()
            runner.validate_results.assert_not_called()
            with self.assertRaisesRegex(ValueError, "public gate reached"):
                self.tool.repeatability_projection(
                    report,
                    plan,
                    runner,
                    "a" * 64,
                    Path.cwd(),
                    {},
                    {},
                    historical_runner=revision,
                )
        runner.historical_runner_hash.assert_called_once_with(revision)
        runner.validate_results.assert_called_once_with(
            plan,
            [],
            Path.cwd(),
            historical_runner=revision,
        )
        runner.validate_results.side_effect = None
        runner.summarize = Mock(side_effect=ValueError("comparison gate reached"))
        values = {
            "repeatability-runtime.json": data,
            "provenance.json": self.tool.record_bytes(native["provenance"]),
            "preregistered-plan.json": self.tool.record_bytes(plan),
        }
        with (
            patch.object(
                self.tool, "safe_read", side_effect=lambda _, name: values[name]
            ),
            self.assertRaisesRegex(ValueError, "comparison gate reached"),
        ):
            self.tool.repeatability_projection(
                report,
                plan,
                runner,
                "a" * 64,
                Path.cwd(),
                {},
                {},
                historical_runner=revision,
            )
        runner.summarize.assert_called_once_with(
            plan,
            [],
            Path.cwd(),
            historical_runner=revision,
        )

    def test_scalar_refresh_binding_cannot_fall_back_to_historical_campaign(self):
        data = b'{"status":"PASS"}'
        report = {
            "status": "PASS",
            "profile_validation": {"local_full_report_sha256": "old"},
            "evidence_refresh": {"local_full_report_sha256": self.tool.sha(data)},
        }
        with patch.object(self.tool, "safe_read", return_value=data):
            self.assertEqual(self.tool.scalar_aggregate_bytes(report, Path.cwd()), data)
            report["profile_validation"]["local_full_report_sha256"] = self.tool.sha(
                data
            )
            for refresh in ({"local_full_report_sha256": "stale"}, {}, None):
                with self.subTest(refresh=refresh):
                    report["evidence_refresh"] = refresh
                    with self.assertRaises((ValueError, KeyError, TypeError)):
                        self.tool.scalar_aggregate_bytes(report, Path.cwd())
            del report["evidence_refresh"]
            self.assertEqual(self.tool.scalar_aggregate_bytes(report, Path.cwd()), data)
            report["status"] = "FAIL"
            with self.assertRaisesRegex(
                ValueError, "committed/current aggregate mismatch"
            ):
                self.tool.scalar_aggregate_bytes(report, Path.cwd())

    def test_cli_forwards_explicit_historical_runner(self):
        revision = "2182bc9524016476f9a538d11fe2e3035fe0ae45"
        result = {"status": "NO_VERDICT"}
        with (
            patch.object(
                self.tool, "integration_report", return_value=result
            ) as integrate,
            patch.object(self.tool, "integration_receipt", return_value={}),
            patch("builtins.print"),
        ):
            self.assertEqual(
                self.tool.main(
                    [
                        "integrate",
                        "--kind",
                        "repeatability",
                        "--producer-root",
                        ".",
                        "--producer-commit",
                        "a" * 40,
                        "--raw-root",
                        ".",
                        "--historical-runner",
                        revision,
                    ]
                ),
                2,
            )
        integrate.assert_called_once_with(
            Path.cwd(),
            "a" * 40,
            "repeatability",
            Path.cwd(),
            historical_runner=revision,
        )

    def test_historical_object_refusal_is_not_a_hash_fallback(self):
        runner = SimpleNamespace(historical_runner_hash=Mock())
        for reason in (
            "unreviewed historical runner revision",
            "historical runner Git object unavailable; explicitly acquire it",
            "historical runner Git-object hash mismatch",
        ):
            runner.historical_runner_hash.side_effect = ValueError(reason)
            with (
                self.subTest(reason=reason),
                patch.object(
                    self.tool, "committed_bytes", return_value=b'{"schema_version":1}'
                ),
                patch.object(
                    self.tool, "load_producer", return_value=(runner, "a" * 64)
                ),
            ):
                result = self.tool.integration_report(
                    Path.cwd(),
                    "a" * 40,
                    "repeatability",
                    Path.cwd(),
                    historical_runner="2182bc9524016476f9a538d11fe2e3035fe0ae45",
                )
                self.assertEqual(result["evidence_status"], "REFUSED")
                self.assertEqual(result["reason"], reason)

    def test_repeatability_old_or_mismatched_profile_refuses_before_replay(self):
        runner = SimpleNamespace(validate_plan=Mock())
        for plan in (
            {},
            {
                "schema_version": 2,
                "profile": "release-mkl-compatible-v1",
                "profile_environment": {"release": {"MKL_CBWR": None}},
            },
        ):
            with (
                self.subTest(plan=plan),
                self.assertRaisesRegex(ValueError, "math profile"),
            ):
                self.tool.repeatability_projection(
                    {}, plan, runner, "a" * 64, Path.cwd(), {}, {}
                )
        runner.validate_plan.assert_not_called()

    def test_producer_loader_keeps_runner_path_after_input_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner_path = root / "env/release-era/qualify_repeatability.py"
            runner_path.parent.mkdir(parents=True)
            runner_path.write_text("MARKER = 'synthetic runner'\n")

            def committed(_root, _commit, name):
                if name.startswith("spec/"):
                    return (self.tool.ROOT / name).read_bytes()
                return (
                    runner_path.read_bytes()
                    if name.endswith("qualify_repeatability.py")
                    else b""
                )

            with patch.object(self.tool, "committed_bytes", side_effect=committed):
                runner, digest = self.tool.load_producer(
                    root, "a" * 40, "repeatability"
                )
            self.assertEqual(runner.MARKER, "synthetic runner")
            self.assertEqual(digest, self.tool.sha(runner_path.read_bytes()))

    def test_dirty_producer_source_refuses_before_module_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "env/release-era/qualify_scalar.py"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"uncommitted runner bytes")
            with (
                patch.object(
                    self.tool.subprocess,
                    "run",
                    return_value=SimpleNamespace(
                        returncode=0, stdout=b"committed runner bytes"
                    ),
                ),
                patch.object(
                    self.tool.importlib.util, "spec_from_file_location"
                ) as loader,
            ):
                with self.assertRaisesRegex(ValueError, "uncommitted producer file"):
                    self.tool.load_producer(root, "a" * 40, "scalar")
                loader.assert_not_called()

    def test_diagnostic_receipt_keeps_evidence_and_production_verdict_separate(self):
        result = {
            "status": "NO_VERDICT",
            "evidence_status": "VALIDATED",
            "producer": {"commit": "a" * 40},
            "closure": "root reconciliation pending",
            "control": {
                "evidence_category": "actual",
                "normative_oracle": False,
                "reason": "diagnostic oracle",
                "diagnostics": {
                    "cases_per_run": 12,
                    "seams_per_case": 36,
                    "byte_equivalence": "PASS",
                    "math_environment": self.tool.SCALAR_MATH_PROFILE,
                },
            },
        }
        receipt = self.tool.integration_receipt(result)
        self.assertEqual(receipt["evidence_status"], "VALIDATED")
        self.assertEqual(receipt["status"], "NO_VERDICT")
        self.assertEqual(receipt["byte_equivalence"], "PASS")
        self.assertFalse(receipt["normative_oracle"])


class ScalarBindingTests(unittest.TestCase):
    """Synthetic protocol fixtures, independent of producer implementations."""

    setUpClass = classmethod(QualificationCommandTests.setUpClass.__func__)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.names = ["keyboard.midi_f0", "other.parameter"]
        self.case = {"id": "synthetic", "sound_index": 6}
        self.plan = {
            "capture_version": "synthetic-v1",
            "configuration": {"sample_rate": 44100},
            "cases": [self.case],
            "controls": {
                name: {"case": "synthetic", "expected_seam": seam}
                for name, seam in (
                    ("wrong-parameter", "input.normalized"),
                    ("wrong-noise", "input.noise"),
                    ("fresh-randomization", "input.normalized"),
                )
            },
        }
        self.plan["cases"].append({"id": "synthetic-slot0", "sound_index": 0})
        self.report = {
            "campaign_id": str(uuid.uuid4()),
            "runtime": {
                "math_environment": {
                    "ATEN_CPU_CAPABILITY": None,
                    "MKL_CBWR": "COMPATIBLE",
                }
            },
            "provenance": {"source_commit": "synthetic"},
            "capture_manifest": [
                {"name": name, "shape": [2]}
                for name in (
                    "input.normalized",
                    "input.noise",
                    "physical.parameters",
                    "audio.final",
                )
            ],
            "execution_records": [],
            "run_report_sha256": {},
            "negative_controls": {},
        }
        (self.root / "campaign.id").write_text(self.report["campaign_id"])
        self.runs = {}
        for repeat in (1, 2):
            for side in ("canonical", "scalar"):
                directory = f"run-{repeat}/{side}"
                path = self.root / directory
                path.mkdir(parents=True)
                artifacts = []
                for item in self.report["capture_manifest"]:
                    raw = struct.pack("<2f", 0.25, 0.5)
                    filename = item["name"] + ".f32le"
                    (path / filename).write_bytes(raw)
                    artifacts.append(
                        dict(item, file=filename, sha256=self.tool.sha(raw))
                    )
                run = {
                    "status": "PASS",
                    "side": side,
                    "mutation": None,
                    "execution": self.execution(repeat),
                    "runtime": copy.deepcopy(self.report["runtime"]),
                    "provenance": copy.deepcopy(self.report["provenance"]),
                    "capture_version": self.plan["capture_version"],
                    "rng_sentinel": "PASS",
                    "source_unchanged_after_render": True,
                    "cases": [
                        {
                            "id": "synthetic",
                            "case_definition": self.case,
                            "configuration": self.plan["configuration"],
                            "corpus_coordinates": SoundIdentity(6).to_dict(32),
                            "execution_width": 32 if side == "canonical" else 1,
                            "reproducible": side == "canonical",
                            "parameter_order": self.names,
                            "normalized_by_name": dict(zip(self.names, (0.25, 0.5))),
                            "traces": artifacts,
                        }
                    ],
                }
                if side == "scalar":
                    self.associate(run, repeat)
                run["cases"][0].update(
                    passive_capture_invariant=True,
                    no_hook_audio_sha256=artifacts[-1]["sha256"],
                )
                slot0 = copy.deepcopy(run["cases"][0])
                slot0.update(
                    id="synthetic-slot0",
                    case_definition=self.plan["cases"][1],
                    corpus_coordinates=SoundIdentity(0).to_dict(32),
                )
                noise = slot0["traces"][1]
                noise["file"] = "slot0-noise.f32le"
                slot0_bytes = struct.pack("<2f", 0.75, 0.5)
                (path / noise["file"]).write_bytes(slot0_bytes)
                noise["sha256"] = self.tool.sha(slot0_bytes)
                run["cases"].append(slot0)
                self.runs[directory] = run
                self.write_run(directory)
        for name, definition in self.plan["controls"].items():
            directory = "controls/" + name
            path = self.root / directory
            path.mkdir(parents=True)
            expected = dict(zip(self.names, (0.25, 0.5)))
            actual = dict(expected)
            if name == "wrong-parameter":
                actual[self.names[0]] = 0.0
            elif name == "fresh-randomization":
                actual = dict.fromkeys(self.names, 0.75)
            raw = struct.pack("<2f", 0.75 if name == "wrong-noise" else 0.25, 0.5)
            (path / "noise.f32le").write_bytes(raw)
            control = {
                "side": "scalar",
                "mutation": name,
                "status": "NO_VERDICT",
                "error": "ValueError: " + definition["expected_seam"] + ": synthetic",
                "execution": self.execution(1),
                "control_observation": {
                    "case": "synthetic",
                    "runtime": copy.deepcopy(self.report["runtime"]),
                    "provenance": copy.deepcopy(self.report["provenance"]),
                    "expected_normalized": expected,
                    "actual_normalized": actual,
                    "expected_noise_slot": 6,
                    "actual_noise_slot": 0 if name == "wrong-noise" else 6,
                    "expected_noise_sha256": self.tool.sha(
                        struct.pack("<2f", 0.25, 0.5)
                    ),
                    "actual_noise_sha256": self.tool.sha(raw),
                    "actual_noise": {
                        "file": "noise.f32le",
                        "sha256": self.tool.sha(raw),
                        "shape": [2],
                    },
                },
            }
            self.associate(control, 1)
            self.runs[directory] = control
            self.write_run(directory)
        self.refresh_records()

    def execution(self, repeat):
        return {
            "uuid": str(uuid.uuid4()),
            "campaign_id": self.report["campaign_id"],
            "repeat": repeat,
            "pid": 1,
            "started_utc": "2026-09-19T00:00:00+00:00",
        }

    def associate(self, run, repeat):
        directory = f"run-{repeat}/canonical"
        run.update(
            canonical_execution_uuid=self.runs[directory]["execution"]["uuid"],
            canonical_report_sha256=self.tool.sha(
                (self.root / directory / "report.json").read_bytes()
            ),
        )

    def write_run(self, directory):
        data = self.tool.record_bytes(self.runs[directory])
        (self.root / directory / "report.json").write_bytes(data)
        if directory.startswith("run-"):
            self.report["run_report_sha256"][directory + "/report.json"] = (
                self.tool.sha(data)
            )
        else:
            self.report["negative_controls"][directory.split("/")[1]] = {
                "observed": copy.deepcopy(self.runs[directory]),
                "detected": True,
            }

    def refresh_records(self):
        self.report["execution_records"] = [
            dict(
                run["execution"],
                path=path,
                side=run["side"],
                **({"mutation": run["mutation"]} if run["mutation"] else {}),
            )
            for path, run in self.runs.items()
        ]

    def validate(self):
        return self.tool.validate_scalar_records(
            self.report, self.plan, self.root, self.names
        )

    def test_complete_diagnostic_records_have_seven_distinct_processes(self):
        result = self.validate()
        self.assertEqual(len(result["execution_records"]), 7)
        self.assertEqual(len(result["raw_report_sha256"]), 7)

    def test_reused_or_canonical_as_scalar_refuses(self):
        run = self.runs["run-2/scalar"]
        run["execution"]["uuid"] = self.runs["run-1/scalar"]["execution"]["uuid"]
        self.write_run("run-2/scalar")
        self.refresh_records()
        with self.assertRaisesRegex(ValueError, "reused"):
            self.validate()
        run["execution"]["uuid"] = str(uuid.uuid4())
        run["side"] = "canonical"
        self.write_run("run-2/scalar")
        with self.assertRaisesRegex(ValueError, "role"):
            self.validate()

    def test_self_consistent_old_math_profile_refuses(self):
        self.report["runtime"]["math_environment"]["MKL_CBWR"] = None
        with self.assertRaisesRegex(ValueError, "math profile"):
            self.validate()

    def test_stale_control_missing_inputs_and_unobserved_mutation_refuse(self):
        directory = "controls/wrong-parameter"
        original = copy.deepcopy(self.runs[directory])
        for field, value, reason in (
            ("provenance", {"source_commit": "stale"}, "provenance"),
            ("runtime", {}, "runtime"),
            ("actual_normalized", {}, "named"),
            (
                "actual_normalized",
                original["control_observation"]["expected_normalized"],
                "mutation",
            ),
        ):
            with self.subTest(field=field, value=value):
                self.runs[directory] = copy.deepcopy(original)
                self.runs[directory]["control_observation"][field] = value
                self.write_run(directory)
                with self.assertRaisesRegex(ValueError, reason):
                    self.validate()

    def test_association_case_configuration_and_source_refuse(self):
        directory = "run-2/scalar"
        original = copy.deepcopy(self.runs[directory])
        for field, value in (
            ("canonical_report_sha256", "0" * 64),
            ("provenance", {}),
            ("runtime", {}),
            ("cases", []),
        ):
            with self.subTest(field=field):
                self.runs[directory] = copy.deepcopy(original)
                self.runs[directory][field] = value
                self.write_run(directory)
                with self.assertRaises(ValueError):
                    self.validate()

    def test_numeric_physical_difference_is_not_source_or_input_mismatch(self):
        directory = "run-2/scalar"
        trace = self.runs[directory]["cases"][0]["traces"][2]
        raw = struct.pack("<2f", 0.25000003, 0.5)
        trace["file"] = "changed-physical.f32le"
        (self.root / directory / trace["file"]).write_bytes(raw)
        trace["sha256"] = self.tool.sha(raw)
        self.write_run(directory)
        self.validate()  # Physical conversion is an observed seam, not an input gate.

    def test_control_uuid_missing_noise_and_escaped_artifacts_refuse(self):
        directory = "controls/wrong-noise"
        control = self.runs[directory]
        control["execution"]["uuid"] = self.runs["run-1/canonical"]["execution"]["uuid"]
        self.write_run(directory)
        with self.assertRaisesRegex(ValueError, "reused"):
            self.validate()
        control["execution"]["uuid"] = str(uuid.uuid4())
        control["control_observation"]["actual_noise"]["file"] = "../../../outside"
        self.write_run(directory)
        with self.assertRaisesRegex(ValueError, "escapes"):
            self.validate()
        control["control_observation"]["actual_noise"]["file"] = "missing.f32le"
        self.write_run(directory)
        with self.assertRaises(FileNotFoundError):
            self.validate()

    def test_wrong_noise_requires_the_actual_preregistered_slot_zero(self):
        directory = "controls/wrong-noise"
        observation = self.runs[directory]["control_observation"]
        raw = struct.pack(
            "<2f", 0.875, 0.5
        )  # Another full-length stream mislabeled slot zero.
        (self.root / directory / "noise.f32le").write_bytes(raw)
        observation["actual_noise"]["sha256"] = observation["actual_noise_sha256"] = (
            self.tool.sha(raw)
        )
        self.write_run(directory)
        with self.assertRaisesRegex(ValueError, "slot-zero"):
            self.validate()

    def test_failed_or_missing_passive_capture_refuses_independently(self):
        directory = "run-2/scalar"
        case = self.runs[directory]["cases"][0]
        for value in (False, None, 1):
            case["passive_capture_invariant"] = value
            self.write_run(directory)
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(ValueError, "passive"),
            ):
                self.validate()

    def test_wrong_execution_configuration_and_case_refuse(self):
        directory = "run-2/scalar"
        original = copy.deepcopy(self.runs[directory])
        for field, value in (
            ("execution_width", 32),
            ("reproducible", True),
            ("configuration", {}),
            ("case_definition", {}),
            ("corpus_coordinates", {}),
            ("traces", []),
        ):
            with self.subTest(field=field):
                self.runs[directory] = copy.deepcopy(original)
                self.runs[directory]["cases"][0][field] = value
                self.write_run(directory)
                with self.assertRaises(ValueError):
                    self.validate()

    def test_aggregate_replay_is_mandatory_and_does_not_overwrite_raw_report(self):
        raw_report = self.root / "scalar-execution.json"
        raw_report.write_bytes(b"original measurement")

        def aggregate(stage, sentinel):
            self.assertFalse(sentinel)
            self.assertNotEqual(stage, self.root)
            self.assertEqual(
                (stage / "campaign.id").read_bytes(),
                (self.root / "campaign.id").read_bytes(),
            )
            (stage / "scalar-execution.json").write_bytes(
                self.tool.record_bytes(self.report)
            )

        runner = SimpleNamespace(aggregate=Mock(side_effect=aggregate))
        self.assertEqual(
            self.tool.replay_scalar_aggregate(runner, self.root, self.report)["status"],
            "PASS",
        )
        runner.aggregate.assert_called_once()
        self.assertEqual(raw_report.read_bytes(), b"original measurement")
        runner.aggregate.side_effect = ValueError("stale native control")
        with self.assertRaisesRegex(ValueError, "stale native control"):
            self.tool.replay_scalar_aggregate(runner, self.root, self.report)


if __name__ == "__main__":
    unittest.main()
