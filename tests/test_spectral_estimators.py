"""Stdlib contract tests plus an explicitly requested numerical suite."""

from __future__ import annotations

import hashlib
import json
import math
import struct
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from torchsynth_voice import spectral_estimators as se
from torchsynth_voice.paired_metrics import Limit
from torchsynth_voice.scorecard import validate_row


class SpectralContractTests(unittest.TestCase):
    def test_noise_identity_is_original_bytes_and_selection(self):
        a = se.NoiseRecord(struct.pack("<4f", 0, 1, -1, 0), (4,), "<f4", 13, 0)
        self.assertEqual(
            se.noise_identity(a, a)["metrics"]["noise.identity"]["value"], 1
        )
        for b in (
            se.NoiseRecord(struct.pack("<4f", 1, -1, 0, 0), (4,), "<f4", 13, 0),
            se.NoiseRecord(a.data, (4,), "<f4", 29, 0),
            se.NoiseRecord(a.data, (4,), "<f4", 13, 1),
            se.NoiseRecord(a.data, (1, 4), "<f4", 13, 0),
        ):
            with self.subTest(b=b):
                result = se.noise_identity(a, b)
                self.assertEqual(result["metrics"]["noise.identity"]["value"], 0)

    def test_noise_statistics_use_declared_denominators(self):
        x = [-1.0, 1.0] * 32
        m = se.noise_statistics(x, sample_rate_hz=4096)["metrics"]
        self.assertEqual(m["noise.mean"]["value"], 0)
        self.assertEqual(m["noise.variance"]["value"], 1)
        self.assertEqual(m["noise.ac.1"]["value"], -63 / 64)
        self.assertEqual(m["noise.ac.2"]["value"], 62 / 64)

    def test_silent_and_short_correlation_refuse(self):
        for x in ([0.0] * 64, [1e-12, -1e-12] * 32, [1.0, -1.0]):
            m = se.noise_statistics(x, sample_rate_hz=4096)["metrics"]
            self.assertIsNone(m["noise.ac.1"]["value"])

    def test_normalization_strict_boundary_and_ties(self):
        for peak in (1 - 2**-24, 1.0, 1 + 2**-23, 2.0):
            x = [0.0, -peak, peak]
            y = x if peak <= 1 else [0.0, -1.0, 1.0]
            result = se.normalization(
                x,
                y,
                sample_rate_hz=44100,
                complete_samples=3,
                reported_applied=peak > 1,
                reported_peak=peak,
                reported_index=1,
                reported_gain=se.round_dtype(1 / peak if peak > 1 else 1, "float32"),
            )
            m = result["metrics"]
            self.assertEqual(m["normalization.peak_index"]["value"], 1)
            self.assertEqual(m["normalization.peak_value"]["value"], -peak)
            self.assertEqual(m["normalization.decision_match"]["value"], 1)
            self.assertEqual(m["normalization.rule_error_max"]["value"], 0)
            self.assertEqual(m["normalization.reciprocal_error"]["value"], 0)

    def test_normalization_seams_are_not_inferred(self):
        result = se.normalization(
            None, [0.0, 1.0], sample_rate_hz=44100, complete_samples=2
        )
        self.assertEqual(result["metrics"]["normalization.gain"]["status"], "missing")
        result = se.normalization(
            [0.0, 1.0], [0.0, 1.0], sample_rate_hz=44100, complete_samples=3
        )
        self.assertIsNone(result["metrics"]["normalization.peak_abs"]["value"])

    def test_normalization_decision_needs_metadata_even_at_one(self):
        result = se.normalization(
            [0.0, 1.0], [0.0, 1.0], sample_rate_hz=44100, complete_samples=2
        )
        self.assertIsNone(result["metrics"]["normalization.decision_match"]["value"])
        self.assertEqual(result["metrics"]["normalization.gain"]["value"], 1)

    def test_scorecard_keeps_raw_estimate_under_floor_refusal(self):
        measurement = se.noise_statistics([-1.0, 1.0] * 32, sample_rate_hz=4096)
        rows, data = se.scorecard_rows(
            measurement,
            case_id="test",
            trace="constructed",
            limits={"noise.mean": Limit(0, 1e-15, "amplitude", "independent zero")},
        )
        row = next(r for r in rows if r["property"] == "noise.mean")
        self.assertEqual(row["verdict"], "NO VERDICT")
        self.assertIsNone(row["observed"])
        self.assertEqual(row["artifact"]["sha256"], hashlib.sha256(data).hexdigest())
        self.assertIn("raw_metrics", json.loads(data)["comparison"])
        for row in rows:
            validate_row(row)

    def test_missing_numpy_never_passes_as_skip(self):
        probe = subprocess.run(
            [
                sys.executable,
                "-S",
                "-c",
                "import sys; sys.path.insert(0, 'src'); from torchsynth_voice.spectral_estimators import require_numpy; require_numpy()",
            ],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(probe.returncode, 0)
        self.assertIn("NumPy required", probe.stderr)

    def test_bad_input_rejected(self):
        for x in ([], [True], [math.nan], [[1.0]], [1e7]):
            with self.assertRaises(ValueError):
                se.noise_statistics(x, sample_rate_hz=4096)

    def test_metadata_and_exact_bytes_validation(self):
        for kwargs in (
            {"reported_applied": 1},
            {"reported_index": -1},
            {"reported_gain": math.inf},
        ):
            with self.assertRaises(ValueError):
                se.normalization(
                    [1], [1], sample_rate_hz=44100, complete_samples=1, **kwargs
                )
        with self.assertRaises(ValueError):
            se.NoiseRecord(b"short", (4,), "<f4", 13, 0)

    def test_production_and_failed_invariants_cannot_pass(self):
        result = se.noise_statistics([-1, 1] * 32, sample_rate_hz=4096)
        limit = {
            "noise.variance": Limit(1, 1e-12, "(amplitude)^2", "known construction")
        }
        for kwargs in ({"invariant_ok": False}, {"evidence_scope": "production"}):
            rows, _ = se.scorecard_rows(
                result,
                case_id="invalid-apparatus",
                trace="test",
                limits=limit,
                **kwargs,
            )
            row = next(r for r in rows if r["property"] == "noise.variance")
            self.assertEqual(row["verdict"], "NO VERDICT")
            self.assertIsNone(row["observed"])

    def test_no_limits_keep_explicit_family_rubric_and_nulls(self):
        rows, data = se.scorecard_rows(
            se.noise_identity(None, None), case_id="missing", trace="noise", limits={}
        )
        self.assertEqual(rows[0]["verdict"], "MISSING EVIDENCE")
        self.assertEqual(rows[0]["rubric"], se.RUBRIC)
        self.assertEqual(json.loads(data)["rubric"]["id"], se.RUBRIC["id"])

    def test_stored_evidence_and_tampering(self):
        import copy
        import importlib.util

        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location(
            "spectral_qualification", root / "tools/qualify_spectral_estimators.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        report = json.loads(module.REPORT.read_bytes())
        module.verify(report)
        self.assertEqual(len(report["cases"]), 150)
        self.assertEqual(len(report["mutations"]), 21)
        for field in ("observed", "sha256", "verdict"):
            changed = copy.deepcopy(report)
            row = next(
                r for r in changed["scorecard"]["rows"] if r["verdict"] == "PASS"
            )
            if field == "sha256":
                row["artifact"][field] = "0" * 64
            elif field == "verdict":
                row[field] = "FAIL"
            else:
                row[field] += 1
            with self.assertRaises(ValueError):
                module.verify(changed)

    def test_qualification_limits_are_independent_of_estimator_floor(self):
        import importlib.util
        from unittest.mock import patch

        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location(
            "spectral_qualification_limits",
            root / "tools/qualify_spectral_estimators.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = se.noise_statistics([-1, 1] * 32, sample_rate_hz=4096)
        with (
            patch.object(se, "resolution_floor", return_value=1.0),
            self.assertRaises(AssertionError),
        ):
            module.Trials().add(
                "floor-mutant", result, {"noise.mean": 0}, domain="rational-noise"
            )


def numerical_suite(preparation_source=None):
    np = se.require_numpy()

    class NumericalTests(unittest.TestCase):
        def test_parseval_and_actual_empty_bands(self):
            for n in (128, 129):
                x = np.zeros(n)
                x[-1] = 1
                result = se.band_statistics(x, sample_rate_hz=4096)
                powers = [
                    m["value"]
                    for k, m in result["metrics"].items()
                    if k.endswith(".power") and m["value"] is not None
                ]
                self.assertAlmostEqual(sum(powers), 1 / n, delta=1e-14)
                self.assertIsNone(result["metrics"]["band.20000_up.power"]["value"])
                self.assertEqual(
                    result["metrics"]["band.20000_up.power"]["reason"],
                    "no_fft_bins_in_band",
                )

        def test_log_floor_does_not_replace_zero(self):
            result = se.band_statistics([0.0] * 128, sample_rate_hz=4096)
            self.assertEqual(result["metrics"]["band.0_20.power"]["value"], 0)
            self.assertIsNone(result["metrics"]["band.0_20.log_power"]["value"])

        def test_missing_noise_keeps_requested_spectral_rows(self):
            result = se.noise_statistics(None, sample_rate_hz=4096, spectral=True)
            metric = result["metrics"]["band.20_200.power"]
            self.assertEqual(metric["status"], "missing")
            self.assertIsNone(metric["value"])

        def test_gain_invariant_ratios_keep_absolute_level(self):
            t = np.arange(1024) / 4096
            x = np.sin(2 * np.pi * 128 * t) + np.sin(2 * np.pi * 384 * t) / 3
            results = [
                se.spectrum(v * x, sample_rate_hz=4096, fundamental_band_hz=(112, 144))
                for v in (1, -2)
            ]
            self.assertAlmostEqual(
                results[0]["metrics"]["harmonic.h3_ratio"]["value"], 1 / 3, delta=1e-12
            )
            self.assertAlmostEqual(
                results[1]["metrics"]["harmonic.h3_ratio"]["value"], 1 / 3, delta=1e-12
            )
            self.assertAlmostEqual(
                results[1]["metrics"]["signal.rms"]["value"],
                2 * results[0]["metrics"]["signal.rms"]["value"],
                delta=1e-12,
            )

        def test_offbin_and_search_ambiguity_are_not_waveform_verdicts(self):
            for frequency in (128.5, 130):
                x = np.sin(2 * np.pi * frequency * np.arange(1024) / 4096)
                result = se.spectrum(
                    x, sample_rate_hz=4096, fundamental_band_hz=(112, 144)
                )
                self.assertIsNone(result["metrics"]["harmonic.h3_ratio"]["value"])
                self.assertIsNotNone(result["metrics"]["band.20_200.power"]["value"])

        def test_linear_mix_signed_gain_dc_and_clipping(self):
            a = np.sin(2 * np.pi * np.arange(256) / 16) + 0.125
            b = np.cos(2 * np.pi * np.arange(256) / 32) - 0.25
            result = se.mix(
                {"a": a, "b": b}, -0.5 * a + 0.25 * b + 0.125, sample_rate_hz=4096
            )
            self.assertAlmostEqual(
                result["metrics"]["mix.a.gain"]["value"], -0.5, delta=1e-12
            )
            self.assertAlmostEqual(
                result["metrics"]["mix.dc_offset"]["value"], 0.125, delta=1e-12
            )
            clipped = se.mix(
                {"a": a},
                np.clip(a, -0.4, 0.4),
                sample_rate_hz=4096,
                clip_bounds=(-0.4, 0.4),
            )
            self.assertGreater(clipped["metrics"]["mix.residual_rms"]["value"], 0.01)
            self.assertEqual(
                clipped["metrics"]["mix.boundary_count"]["value"],
                sum(abs(v) >= 0.4 for v in a),
            )

        def test_missing_and_rank_deficient_seams(self):
            x = np.sin(np.arange(256))
            result = se.mix({"a": x, "b": x}, x, sample_rate_hz=4096)
            self.assertIsNone(result["metrics"]["mix.a.gain"]["value"])
            self.assertEqual(
                result["metrics"]["mix.a.gain"]["reason"],
                "ambiguous_or_ill_conditioned_inputs",
            )
            result = se.mix({"a": None}, x, sample_rate_hz=4096)
            self.assertEqual(result["metrics"]["mix.a.gain"]["status"], "missing")

        def test_float64_and_late_peak(self):
            x = np.array([0, -1, 0, 2.0], dtype=np.float64)
            result = se.normalization(
                x, x / 2, sample_rate_hz=44100, complete_samples=4, dtype="float64"
            )
            self.assertEqual(result["metrics"]["normalization.peak_index"]["value"], 3)
            self.assertEqual(
                result["metrics"]["normalization.rule_error_max"]["value"], 0
            )

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(NumericalTests)
    if (
        preparation_source is not None
        or (
            Path(__file__).resolve().parents[1] / "src/torchsynth_voice/preparation.py"
        ).exists()
    ):

        class SharedPreparationTests(unittest.TestCase):
            def test_actual_read_only_adapter(self):
                x = [-1.0, 1.0] * 64
                result = se.estimate_prepared_pair(
                    x,
                    [2 * v for v in x],
                    estimator=se.noise_statistics,
                    sample_rate_hz=4096,
                    source=preparation_source,
                    window=(64, 128),
                )
                self.assertEqual(result["status"], "valid")
                self.assertEqual(
                    result["measurements"][0]["metrics"]["noise.variance"]["value"], 1
                )
                self.assertEqual(
                    result["measurements"][1]["metrics"]["noise.variance"]["value"], 4
                )
                refused = se.estimate_prepared_pair(
                    x,
                    x,
                    estimator=se.noise_statistics,
                    sample_rate_hz=4096,
                    source=preparation_source,
                    operations=("normalize",),
                )
                self.assertEqual(refused["status"], "refused")
                self.assertIsNone(refused["measurements"])

        suite.addTests(
            unittest.defaultTestLoader.loadTestsFromTestCase(SharedPreparationTests)
        )
    return suite


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--numerical", action="store_true")
    parser.add_argument("--preparation-source")
    args, remaining = parser.parse_known_args()
    if args.numerical:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(SpectralContractTests)
        suite.addTests(numerical_suite(args.preparation_source))
        outcome = unittest.TextTestRunner(verbosity=2 if "-v" in remaining else 1).run(
            suite
        )
        raise SystemExit(not outcome.wasSuccessful())
    unittest.main(argv=[sys.argv[0], *remaining])
