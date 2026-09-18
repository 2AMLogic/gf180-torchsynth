from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
import time
import unittest
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.paired_metrics import (  # noqa: E402
    Limit,
    MetricsError,
    Rubric,
    analytic_exactness_rubric,
    artifact_reference,
    compare_paired,
    scorecard_rows,
)
from torchsynth_voice.scorecard import (  # noqa: E402
    make_report,
    report_from_json,
    report_to_json,
)


def compare(reference, candidate, **kwargs):
    settings = {"reference_rate_hz": 8, "candidate_rate_hz": 8, "unit": "V"}
    settings.update(kwargs)
    return compare_paired(reference, candidate, **settings)


def value(result, name):
    return result["metrics"][name]["value"]


def rows(result, rubric=None):
    return scorecard_rows(
        result, case_id="analytic", partition="development", trace="test", rubric=rubric
    )


class PairedMetricsTests(unittest.TestCase):
    def test_equality_and_peak_ties(self):
        result = compare([0, -2, 2, 0], [0, -2, 2, 0])
        for metric in ("max_abs_error", "mean_error", "mean_abs_error", "error_rms"):
            self.assertEqual(value(result, metric), 0)
        self.assertEqual(value(result, "exact_equal"), 1)
        self.assertEqual(value(result, "mismatch_count"), 0)
        self.assertIsNone(value(result, "first_divergence_index"))
        self.assertEqual(value(result, "reference.peak_index"), 1)
        self.assertEqual(value(result, "reference.peak_value"), -2)
        self.assertAlmostEqual(value(result, "reference.rms"), math.sqrt(2))

    def test_one_sample_delay_is_not_aligned(self):
        reference, candidate = [0, 1, 0, 0], [0, 0, 1, 0]
        result = compare(reference, candidate)
        self.assertEqual(value(result, "first_divergence_index"), 1)
        self.assertEqual(value(result, "max_abs_error_index"), 1)
        self.assertEqual(value(result, "mismatch_count"), 2)
        self.assertAlmostEqual(value(result, "error_rms"), math.sqrt(0.5))
        self.assertAlmostEqual(value(result, "snr_db"), -10 * math.log10(2))
        self.assertEqual((reference, candidate), ([0, 1, 0, 0], [0, 0, 1, 0]))

    def test_plus_minus_one_db_is_not_rescaled(self):
        for db in (-1, 1):
            gain = 10 ** (db / 20)
            with self.subTest(db=db):
                result = compare([1, -1, 1, -1], [gain, -gain, gain, -gain])
                self.assertAlmostEqual(value(result, "error_rms"), abs(gain - 1))
                self.assertAlmostEqual(
                    value(result, "snr_db"), -20 * math.log10(abs(gain - 1))
                )
                self.assertEqual(value(result, "mismatch_count"), 4)

    def test_polarity_and_dc(self):
        inverted = compare([1, -1], [-1, 1])
        self.assertEqual(value(inverted, "error_rms"), 2)
        self.assertEqual(value(inverted, "mean_error"), 0)
        dc = compare([1, -1], [1.25, -0.75])
        self.assertEqual(value(dc, "mean_error"), 0.25)
        self.assertEqual(value(dc, "candidate.dc"), 0.25)
        self.assertEqual(value(dc, "error_rms"), 0.25)

    def test_window_impulse_and_short_tail(self):
        result = compare([0] * 5, [0, 0, 2, 0, 3], window_samples=2)
        self.assertEqual(
            [(w["start"], w["stop"]) for w in result["windows"]],
            [(0, 2), (2, 4), (4, 5)],
        )
        self.assertEqual(value(result, "window.0.error_rms"), 0)
        self.assertAlmostEqual(value(result, "window.1.error_rms"), math.sqrt(2))
        self.assertEqual(value(result, "window.2.error_rms"), 3)
        self.assertEqual(value(result, "window.2.max_abs_error_index"), 4)
        self.assertAlmostEqual(value(result, "error_rms"), math.sqrt(13 / 5))

    def test_snr_null_cases_and_near_silence(self):
        for reference, candidate, reason in (
            ([0, 0], [0, 0], "undefined_both_silent"),
            ([0, 0], [0, 1], "negative_infinity_silent_reference"),
            ([1, 0], [1, 0], "positive_infinity_zero_error"),
        ):
            with self.subTest(reason=reason):
                metric = compare(reference, candidate)["metrics"]["snr_db"]
                self.assertIsNone(metric["value"])
                self.assertEqual(metric["reason"], reason)
        tiny = compare([1e-300, -1e-300], [0, 0])
        self.assertEqual(value(tiny, "reference.rms"), 1e-300)
        self.assertEqual(value(tiny, "snr_db"), 0)

    def test_missing_extra_and_truncated_samples_are_framing_failures(self):
        for candidate in ([1], [1, 2, 3], []):
            result = compare([1, 2], candidate)
            self.assertEqual(value(result, "sample_count_delta"), len(candidate) - 2)
            self.assertEqual(value(result, "framing_match"), 0)
            self.assertIsNone(value(result, "error_rms"))
            self.assertIsNone(value(result, "first_divergence_index"))
            self.assertEqual(result["windows"], [])
            produced, _ = rows(result, analytic_exactness_rubric())
            self.assertEqual(
                next(r for r in produced if r["property"] == "framing_match")[
                    "verdict"
                ],
                "FAIL",
            )

    def test_empty_missing_and_rate_mismatch_are_not_success(self):
        for a, b, settings in (
            ([], [], {}),
            ([1], None, {}),
            ([1], [1], {"candidate_rate_hz": 9}),
        ):
            result = compare(a, b, **settings)
            self.assertIsNone(value(result, "error_rms"))
            produced, _ = rows(result, analytic_exactness_rubric())
            exact = next(r for r in produced if r["property"] == "exact_equal")
            self.assertNotEqual(exact["verdict"], "PASS")
            self.assertIsNone(exact["observed"])

    def test_quantization_and_clipping(self):
        result = compare([0.25, 0.75, -0.25, -0.75], [0, 0.5, 0, -0.5])
        self.assertEqual(value(result, "error_rms"), 0.25)
        result = compare([-2, -1, 0, 1, 2], [-1, -1, 0, 1, 1], clip_bounds=(-1, 1))
        self.assertEqual(value(result, "reference.clip_count"), 4)
        self.assertEqual(value(result, "candidate.clip_count"), 4)
        self.assertAlmostEqual(value(result, "error_rms"), math.sqrt(2 / 5))

    def test_bad_values_shapes_and_settings_are_rejected(self):
        class UnsupportedDtype(list):
            dtype = "unsupported"

        for a in (
            [[1]],
            [True],
            ["1"],
            [1j],
            [math.nan],
            [math.inf],
            [2**53 + 1],
            "1",
            {0: 1},
            [Fraction(1, 3)],
            UnsupportedDtype([1]),
        ):
            with self.subTest(a=a), self.assertRaises(MetricsError):
                compare(a, [1])
        for settings in (
            {"reference_rate_hz": 0},
            {"candidate_rate_hz": math.nan},
            {"window_samples": 0},
            {"window_samples": True},
            {"unit": " "},
            {"clip_bounds": (1, -1)},
        ):
            with self.subTest(settings=settings), self.assertRaises(MetricsError):
                compare([1], [1], **settings)

    def test_large_and_subnormal_values_do_not_fabricate_finite_results(self):
        result = compare([1e308, -1e308], [1e308, -1e308])
        self.assertEqual(value(result, "reference.rms"), 1e308)
        self.assertEqual(value(result, "reference.dc"), 0)
        overflow = compare([-1e308], [1e308])
        self.assertIsNone(value(overflow, "error_rms"))
        self.assertEqual(value(overflow, "mismatch_count"), 1)
        json.dumps(overflow, allow_nan=False)
        tiny = compare([5e-324], [0])
        self.assertEqual(value(tiny, "error_rms"), 5e-324)
        self.assertEqual(value(tiny, "snr_db"), 0)

    def test_no_implicit_rubric_and_raw_diagnostics_are_hashed(self):
        result = compare([1, 0], [0, 0])
        produced, data = rows(result)
        record = json.loads(data)
        self.assertEqual(record["comparison"], result)
        self.assertEqual(value(record["comparison"], "max_abs_error"), 1)
        for row in produced:
            self.assertEqual(row["verdict"], "NO VERDICT")
            self.assertIsNone(row["observed"])
            self.assertEqual(
                row["artifact"]["sha256"], hashlib.sha256(data).hexdigest()
            )
        report = make_report(
            produced, partition="development", rubric=produced[0]["rubric"]
        )
        self.assertEqual(report_from_json(report_to_json(report)), report)

    def test_explicit_rubric_pass_fail_and_undefined_refusal(self):
        rubric = Rubric(
            "test-only",
            "1",
            {
                "max_abs_error": Limit(0, 0.25, "V", "analytic fixture"),
                "snr_db": Limit(20, 1, "dB", "analytic fixture"),
            },
        )
        for candidate, verdict in (([1.25], "PASS"), ([1.5], "FAIL")):
            produced, data = rows(compare([1], candidate), rubric)
            row = next(r for r in produced if r["property"] == "max_abs_error")
            self.assertEqual(row["verdict"], verdict)
            self.assertEqual(
                json.loads(data)["rubric"]["limits"]["max_abs_error"]["tolerance"], 0.25
            )
            report = make_report(
                produced, partition="development", rubric=produced[0]["rubric"]
            )
            self.assertEqual(report_from_json(report_to_json(report)), report)
        produced, _ = rows(compare([1], [1]), rubric)
        self.assertIsNone(
            next(r for r in produced if r["property"] == "snr_db")["observed"]
        )

    def test_artifact_id_adapter_hashes_actual_bytes(self):
        self.assertEqual(
            artifact_reference(artifact_id="ra1-test", record_bytes=b"record"),
            {"identity": "ra1-test", "sha256": hashlib.sha256(b"record").hexdigest()},
        )

    def test_stdlib_path_does_not_import_optional_libraries(self):
        code = f"import sys; sys.path.insert(0, {str(ROOT / 'src')!r}); " + (
            "from torchsynth_voice.paired_metrics import compare_paired; "
            "compare_paired([1], [1], reference_rate_hz=1, candidate_rate_hz=1, unit='V'); "
            "assert not {'numpy', 'torch', 'torchsynth', 'lightning'} & sys.modules.keys()"
        )
        subprocess.run([sys.executable, "-S", "-c", code], check=True)

    def test_missing_numpy_is_an_explicit_refusal(self):
        code = f"import sys; sys.path.insert(0, {str(ROOT / 'src')!r}); " + (
            "from torchsynth_voice.paired_metrics import compare_paired; "
            "r = compare_paired([1], [1], reference_rate_hz=1, candidate_rate_hz=1, unit='V', spectral=True); "
            "m = r['metrics']['band.0_20.error_rms']; "
            "assert m['value'] is None and m['reason'] == 'numpy_unavailable_install_metrics_extra'"
        )
        subprocess.run([sys.executable, "-S", "-c", code], check=True)

    def test_rubric_rejects_missing_sources_nonfinite_limits_and_wrong_units(self):
        for args in (
            (0, -1, "V", "test"),
            (math.inf, 0, "V", "test"),
            (0, math.nan, "V", "test"),
            (0, 0, "V", ""),
            (True, 0, "V", "test"),
        ):
            with self.subTest(args=args), self.assertRaises(MetricsError):
                Limit(*args)
        for name, unit in (("error_rms", "Hz"), ("typo", "V")):
            with self.subTest(name=name), self.assertRaises(MetricsError):
                rows(
                    compare([1], [1]),
                    Rubric("test", "1", {name: Limit(0, 0, unit, "test")}),
                )

    def test_exact_rubric_and_missing_evidence_round_trip(self):
        for candidate, expected in (
            ([1, 2], "PASS"),
            ([1, -2], "FAIL"),
            (None, "MISSING EVIDENCE"),
        ):
            produced, _ = rows(compare([1, 2], candidate), analytic_exactness_rubric())
            exact = next(r for r in produced if r["property"] == "exact_equal")
            self.assertEqual(exact["verdict"], expected)
            report = make_report(
                produced, partition="development", rubric=exact["rubric"]
            )
            self.assertEqual(report_from_json(report_to_json(report)), report)

    def test_settings_and_rubrics_change_record_identity(self):
        a, data_a = rows(compare([1], [1]))
        b, data_b = rows(compare([1], [1], window_samples=2))
        self.assertNotEqual(a[0]["estimator"], b[0]["estimator"])
        self.assertNotEqual(a[0]["artifact"], b[0]["artifact"])
        self.assertNotEqual(data_a, data_b)
        c, _ = rows(compare([1], [1]), analytic_exactness_rubric())
        self.assertNotEqual(a[0]["artifact"], c[0]["artifact"])

    def test_signed_zero_and_exact_float_equality(self):
        self.assertEqual(value(compare([-0.0], [0.0]), "exact_equal"), 1)
        result = compare([1.0], [math.nextafter(1.0, 2.0)])
        self.assertEqual(value(result, "mismatch_count"), 1)
        self.assertEqual(value(result, "max_abs_error"), 2**-52)


# Explicit opt-in suite: normal discovery runs only stdlib tests, with no skips.
# The dedicated CI command requests these tests and fails if NumPy is absent.
def spectral_suite():
    import numpy as np

    class SpectralTests(unittest.TestCase):
        def spectral(self, reference, candidate, rate=48000, **kwargs):
            return compare(
                reference,
                candidate,
                reference_rate_hz=rate,
                candidate_rate_hz=rate,
                spectral=True,
                **kwargs,
            )

        def test_bin_centered_tones_and_band_edges(self):
            n, rate = 48000, 48000
            t = np.arange(n) / rate
            for frequency, band in (
                (20, "20_200"),
                (200, "200_2000"),
                (2000, "2000_20000"),
                (20000, "20000_up"),
            ):
                with self.subTest(frequency=frequency):
                    signal = np.sin(2 * np.pi * frequency * t)
                    result = self.spectral(np.zeros(n), signal, rate)
                    self.assertAlmostEqual(
                        value(result, f"band.{band}.error_rms"),
                        math.sqrt(0.5),
                        places=12,
                    )
                    self.assertAlmostEqual(
                        value(result, f"band.{band}.error_power"), 0.5, places=12
                    )
                    for other in (
                        "0_20",
                        "20_200",
                        "200_2000",
                        "2000_20000",
                        "20000_up",
                    ):
                        if other != band:
                            self.assertLess(
                                value(result, f"band.{other}.error_power"), 1e-20
                            )

        def test_dc_and_even_nyquist_weights(self):
            for signal, band in (
                (np.ones(48), "0_20"),
                ((-1.0) ** np.arange(48), "20000_up"),
            ):
                result = self.spectral(np.zeros(48), signal)
                self.assertEqual(value(result, f"band.{band}.error_power"), 1)
                self.assertEqual(value(result, "error_rms"), 1)

        def test_impulse_parseval_for_even_and_odd_lengths(self):
            for n in (47, 48):
                impulse = np.zeros(n)
                impulse[3] = 2
                result = self.spectral(np.zeros(n), impulse)
                total = sum(
                    metric["value"]
                    for name, metric in result["metrics"].items()
                    if name.startswith("band.")
                    and name.endswith("error_power")
                    and metric["value"] is not None
                )
                self.assertAlmostEqual(total, 4 / n, places=14)
                self.assertAlmostEqual(
                    value(result, "error_rms"), 2 / math.sqrt(n), places=14
                )
                # Flat impulse spectrum: DC has weight 1; odd final bin has 2.
                top_bins = [k for k in range(n // 2 + 1) if k * 48000 / n >= 20000]
                weights = sum(1 if n % 2 == 0 and k == n // 2 else 2 for k in top_bins)
                self.assertAlmostEqual(
                    value(result, "band.20000_up.error_power"),
                    4 * weights / n**2,
                    places=14,
                )

        def test_spectral_error_retains_phase_and_gain_faults(self):
            signal = np.sin(2 * np.pi * 1000 * np.arange(480) / 48000)
            for candidate, rms in (
                (-signal, math.sqrt(2)),
                (2 * signal, math.sqrt(0.5)),
            ):
                result = self.spectral(signal, candidate)
                self.assertAlmostEqual(
                    value(result, "band.200_2000.error_rms"), rms, places=13
                )

        def test_single_sample_empty_bands_and_low_trace_rate(self):
            result = self.spectral([0], [2], rate=100)
            self.assertEqual(value(result, "band.0_20.error_power"), 4)
            self.assertIsNone(value(result, "band.20_200.error_power"))
            self.assertEqual(
                result["metrics"]["band.20_200.error_power"]["reason"],
                "no_fft_bins_in_band",
            )
            result = self.spectral([0, 0], [1, -1], rate=40)
            self.assertEqual(value(result, "band.20_200.error_power"), 1)
            self.assertEqual(value(result, "band.0_20.error_power"), 0)

        def test_float32_and_integer_conversion_before_subtraction(self):
            ref = np.array([0.1, -0.1], dtype=np.float32)
            candidate = ref.astype(np.float64)
            result = self.spectral(ref, candidate)
            self.assertEqual(value(result, "exact_equal"), 1)
            self.assertEqual(result["inputs"]["reference"]["dtype"], "float32")
            result = self.spectral(
                np.array([255], dtype=np.uint8), np.array([0], dtype=np.uint8)
            )
            self.assertEqual(value(result, "mean_error"), -255)
            for invalid in (
                np.ones((1, 2)),
                np.array([True]),
                np.array([1 + 1j]),
                np.array([1], dtype=object),
                np.array([2**53 + 1], dtype=np.uint64),
            ):
                with self.subTest(dtype=invalid.dtype), self.assertRaises(MetricsError):
                    self.spectral(invalid, [1])

        def test_spectral_range_failures_do_not_serialize_infinity_or_zero(self):
            for amplitude in (1e308, 1e-300):
                result = self.spectral([0], [amplitude])
                self.assertEqual(value(result, "band.0_20.error_rms"), amplitude)
                self.assertIsNone(value(result, "band.0_20.error_power"))
                json.dumps(result, allow_nan=False)
            result = self.spectral([-1e308], [1e308])
            self.assertIsNone(value(result, "band.0_20.error_rms"))

        def test_spectral_scorecard_round_trip(self):
            result = self.spectral([0, 0], [1, -1])
            rubric = Rubric(
                "analytic-nyquist",
                "1",
                {
                    "band.20000_up.error_power": Limit(
                        1, 0, "(V)^2", "alternating unit samples"
                    )
                },
            )
            produced, data = rows(result, rubric)
            row = next(
                r for r in produced if r["property"] == "band.20000_up.error_power"
            )
            self.assertEqual(row["verdict"], "PASS")
            self.assertEqual(
                row["artifact"]["sha256"], hashlib.sha256(data).hexdigest()
            )
            report = make_report(
                produced, partition="development", rubric=row["rubric"]
            )
            self.assertEqual(report_from_json(report_to_json(report)), report)

        def test_full_clip_timing_and_resource_smoke(self):
            import resource

            n = 176400
            reference = np.zeros(n, dtype=np.float32)
            candidate = reference.copy()
            candidate[-1] = 1
            started = time.perf_counter()
            result = self.spectral(reference, candidate, rate=44100)
            elapsed = time.perf_counter() - started
            self.assertEqual(value(result, "candidate.sample_count"), n)
            self.assertEqual(value(result, "first_divergence_index"), n - 1)
            self.assertAlmostEqual(value(result, "error_rms"), 1 / 420, places=15)
            power = sum(
                m["value"]
                for name, m in result["metrics"].items()
                if name.startswith("band.") and name.endswith("error_power")
            )
            self.assertAlmostEqual(power, 1 / n, places=18)
            # A generous smoke ceiling catches a full-clip quadratic transform.
            self.assertLess(elapsed, 30)
            self.assertNotIn("torch", sys.modules)
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            rss_mib = rss / (1024**2 if sys.platform == "darwin" else 1024)
            print(
                f"\nFull clip: N={n}, elapsed={elapsed:.3f}s, process_peak_RSS={rss_mib:.1f}MiB, NumPy={np.__version__}"
            )

    return unittest.defaultTestLoader.loadTestsFromTestCase(SpectralTests)


if __name__ == "__main__":
    if "--spectral" in sys.argv:
        sys.argv.remove("--spectral")
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(PairedMetricsTests)
        suite.addTests(spectral_suite())
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        sys.exit(not result.wasSuccessful())
    unittest.main()
