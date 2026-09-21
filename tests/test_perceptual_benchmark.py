from __future__ import annotations

import math
import sys
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import perceptual_benchmark as pb  # noqa: E402


class PinTests(unittest.TestCase):
    def test_every_candidate_pin_is_complete(self):
        for identity, pin in pb.CANDIDATE_PINS.items():
            self.assertEqual(pin["identity"], identity)
            self.assertTrue(pin["class"])
            self.assertTrue(pin["sources"])
            for source in pin["sources"]:
                self.assertTrue(
                    "arXiv" in source
                    or "http" in source
                    or "ITU" in source
                    or "github" in source,
                    source,
                )

    def test_limitations_cover_every_candidate_and_the_cross_cutting_row(self):
        covered = {entry["candidate"] for entry in pb.CANDIDATE_LIMITATIONS}
        for identity in pb.CANDIDATE_PINS:
            self.assertIn(identity, covered)
        self.assertIn("all", covered)
        for entry in pb.CANDIDATE_LIMITATIONS:
            self.assertTrue(entry["limitation"])
            self.assertTrue(entry["source"])

    def test_visqol_pin_declares_the_48k_resampler(self):
        self.assertEqual(pb.VISQOL_PIN["required_rate_hz"], 48000.0)
        self.assertEqual(pb.VISQOL_PIN["resampler"], pb.RESAMPLER_PIN)

    def test_multires_pin_declares_fft_hops_windows_floors_and_phase(self):
        config = pb.MULTIRES_SPECTRAL_PIN["config"]
        self.assertEqual(config["fft_sizes"], [512, 1024, 2048])
        self.assertIn("Hann", config["window"])
        self.assertIn("log_floor", config)
        self.assertIn("phase discarded", config["spectrogram"])

    def test_doctrine_forbids_oracle_and_aggregate(self):
        self.assertIn("never an acceptance oracle", pb.DOCTRINE)
        self.assertIn("no aggregate score", pb.DOCTRINE)
        self.assertIn("no candidate becomes mandatory", pb.DOCTRINE)

    def test_human_comparison_row_is_explicit_no_verdict(self):
        row = pb.human_comparison_row()
        self.assertEqual(row["verdict"], "NO VERDICT")
        self.assertEqual(row["status"], "not_collected")
        self.assertIn("operator declination", row["reason"])
        self.assertIn("2026-09-20", row["reason"])
        self.assertIn("never merged", row["note"])
        fresh = pb.human_comparison_row()
        self.assertIsNot(fresh, row)
        self.assertEqual(fresh, row)


class SpearmanTests(unittest.TestCase):
    def test_perfect_and_reversed_ranking(self):
        self.assertAlmostEqual(pb.spearman_rank([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)
        self.assertAlmostEqual(pb.spearman_rank([1, 2, 3, 4], [40, 30, 20, 10]), -1.0)

    def test_monotone_but_nonlinear_is_perfect(self):
        self.assertAlmostEqual(
            pb.spearman_rank([1, 2, 3, 4, 5], [1, 4, 9, 16, 25]), 1.0
        )

    def test_average_tie_ranks(self):
        self.assertAlmostEqual(
            pb.spearman_rank([1, 2, 2, 4], [1, 2, 3, 4]), math.sqrt(0.9)
        )

    def test_constant_input_is_undefined_not_zero(self):
        self.assertIsNone(pb.spearman_rank([1, 1, 1], [1, 2, 3]))

    def test_requires_equal_sizes(self):
        with self.assertRaises(pb.PerceptualBenchmarkError):
            pb.spearman_rank([1], [1, 2])


class BootstrapTests(unittest.TestCase):
    def test_deterministic_under_seed(self):
        x = [1.0, 2, 3, 4, 5, 6, 7, 8]
        y = [2.0, 1, 4, 3, 6, 5, 8, 7]
        first = pb.bootstrap_spearman_ci(x, y, trials=50, seed=7)
        second = pb.bootstrap_spearman_ci(x, y, trials=50, seed=7)
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "ok")

    def test_ci_brackets_point_for_noisy_monotone_data(self):
        x = [float(i) for i in range(30)]
        y = [float(i) + (i % 3) for i in range(30)]
        ci = pb.bootstrap_spearman_ci(x, y, trials=200, seed=11)
        self.assertLessEqual(ci["ci_low"], ci["point"])
        self.assertLessEqual(ci["point"], ci["ci_high"])

    def test_rejects_bad_trials_and_alpha(self):
        with self.assertRaises(pb.PerceptualBenchmarkError):
            pb.bootstrap_spearman_ci([1, 2], [1, 2], trials=0, seed=1)
        with self.assertRaises(pb.PerceptualBenchmarkError):
            pb.bootstrap_spearman_ci([1, 2], [1, 2], trials=5, seed=1, alpha=1.5)


class MonotonicityTests(unittest.TestCase):
    def test_increasing_ladder_is_valid(self):
        check = pb.ladder_monotonicity([1.0, 2.0, 2.0, 4.0, 8.0])
        self.assertTrue(check["monotone"])
        self.assertFalse(check["invalid_anchor"])
        self.assertEqual(check["violations"], [])

    def test_dip_is_flagged_not_smoothed(self):
        check = pb.ladder_monotonicity([1.0, 2.0, 1.5, 4.0, 8.0])
        self.assertFalse(check["monotone"])
        self.assertTrue(check["invalid_anchor"])
        self.assertEqual(len(check["violations"]), 1)
        self.assertEqual(check["violations"][0]["from_step"], 2)
        self.assertEqual(check["violations"][0]["to_step"], 3)

    def test_missing_steps_are_skipped_not_imputed(self):
        check = pb.ladder_monotonicity([1.0, None, 3.0])
        self.assertTrue(check["monotone"])
        self.assertEqual(check["evaluated_steps"], [1, 3])

    def test_reference_dependence_exactness(self):
        row = pb.reference_dependence_row("x", 0.0, identity_value=0.0)
        self.assertTrue(row["holds"])
        self.assertFalse(
            pb.reference_dependence_row("x", 1e-300, identity_value=0.0)["holds"]
        )
        self.assertFalse(
            pb.reference_dependence_row("x", None, identity_value=0.0)["holds"]
        )


class AvailabilityRefusalTests(unittest.TestCase):
    def setUp(self):
        patcher_module = mock.patch.object(pb, "_module_probe", lambda name: False)
        patcher_gst = mock.patch.object(
            pb, "_gst_element_probe", lambda element, inspector: False
        )
        patcher_which = mock.patch.object(pb.shutil, "which", lambda name: None)
        patcher_module.start()
        patcher_gst.start()
        patcher_which.start()
        self.addCleanup(patcher_module.stop)
        self.addCleanup(patcher_gst.stop)
        self.addCleanup(patcher_which.stop)

    def test_peaq_reports_every_probe_honestly(self):
        availability = pb.peaq_availability()
        self.assertFalse(availability["available"])
        self.assertEqual(len(availability["probes"]), 3)
        self.assertTrue(all(not probe["found"] for probe in availability["probes"]))
        self.assertIn("peaq_implementation_unavailable", availability["reason"])

    def test_visqol_refusal_names_the_missing_binary(self):
        availability = pb.visqol_availability()
        self.assertFalse(availability["available"])
        self.assertIn("visqol_binary_unavailable", availability["reason"])

    def test_cdpam_refusal_names_missing_modules(self):
        availability = pb.cdpam_availability()
        self.assertFalse(availability["available"])
        self.assertIn("cdpam_dependency_unavailable", availability["reason"])
        self.assertIn("torch", availability["reason"])

    def test_score_functions_raise_instead_of_skipping(self):
        for score in (pb.peaq_score, pb.visqol_score, pb.cdpam_score):
            with self.assertRaises(pb.PerceptualBenchmarkError) as caught:
                score([0.0, 1.0], [0.0, 1.0])
            self.assertIn("_unavailable", str(caught.exception))

    def test_refusal_precedes_any_measurement(self):
        with self.assertRaises(pb.PerceptualBenchmarkError):
            pb.peaq_score([0.0], [0.0, 0.0])


class InputValidationTests(unittest.TestCase):
    def test_pair_rejects_mismatched_counts(self):
        with self.assertRaises(pb.PerceptualBenchmarkError):
            pb._pair([0.0, 0.0], [0.0])

    def test_pair_rejects_nonfinite_and_bools(self):
        with self.assertRaises(pb.PerceptualBenchmarkError):
            pb._pair([0.0, float("nan")], [0.0, 0.0])
        with self.assertRaises(pb.PerceptualBenchmarkError):
            pb._pair([0.0, True], [0.0, 0.0])

    def test_pair_rejects_empty_inputs(self):
        with self.assertRaises(pb.PerceptualBenchmarkError):
            pb._pair([], [])

    def test_multires_refusal_names_numpy_when_absent(self):
        original = pb._require_numpy

        def absent():
            raise pb.PerceptualBenchmarkError("numpy_unavailable_install_metrics_extra")

        pb._require_numpy = absent
        try:
            with self.assertRaises(pb.PerceptualBenchmarkError) as caught:
                pb.multires_spectral_distance([0.0], [1.0])
            self.assertEqual(
                str(caught.exception), "numpy_unavailable_install_metrics_extra"
            )
        finally:
            pb._require_numpy = original

    def test_resampler_contribution_refusal_when_numpy_absent(self):
        with mock.patch.object(
            pb, "_module_probe", lambda name: name != "numpy" and False
        ):
            rows = pb.resampler_contribution_rows([0.0, 1.0])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "refused")
        self.assertIn("numpy_unavailable", rows[0]["reason"])


class AgreementRowTests(unittest.TestCase):
    @staticmethod
    def _points(metric_of_step=float, operator="clip.round_step"):
        points = []
        for step in range(1, 6):
            points.append(
                {
                    "operator": operator,
                    "ladder_step": step,
                    "magnitude": 10.0**step,
                    "objective": {
                        "snr_db": 90.0 - 10 * step,
                        "error_rms": 1e-4 * step,
                    },
                    "metrics": {"multires": metric_of_step(step)},
                }
            )
        return points

    def test_per_operator_rows_are_unit_coherent_and_rank_based(self):
        availability = {
            "multires": {"available": True, "reason": "declared pin ready"},
            "gated": {"available": False, "reason": "gated_absent"},
        }
        agreement = pb.build_agreement(
            {"signal": self._points()},
            ["multires", "gated"],
            availability,
            trials=20,
            seed=3,
        )
        operator_entry = agreement["signal"]["clip.round_step"]
        executed = operator_entry["multires"]
        self.assertEqual(executed["n_usable"], 5)
        self.assertAlmostEqual(executed["declared_step_rank"]["spearman"]["point"], 1.0)
        self.assertAlmostEqual(executed["snr_db"]["spearman"]["point"], -1.0)
        self.assertIn("never promotes", executed["declared_step_rank"]["note"])
        refused = operator_entry["gated"]
        self.assertEqual(refused["n_usable"], 0)
        self.assertEqual(refused["agreement"]["verdict"], "NO VERDICT")
        self.assertEqual(refused["agreement"]["reason"], "gated_absent")

    def test_declared_rank_ignores_numeric_sign_conventions(self):
        severity_rises_as_value_drops = self._points(
            metric_of_step=lambda step: float(step),
            operator="clip.saturation_ceiling",
        )
        for point, magnitude in zip(
            severity_rises_as_value_drops, [0.98, 0.9, 0.8, 0.6, 0.4]
        ):
            point["magnitude"] = magnitude
        availability = {"multires": {"available": True, "reason": "ready"}}
        agreement = pb.build_agreement(
            {"signal": severity_rises_as_value_drops},
            ["multires"],
            availability,
            trials=10,
            seed=1,
        )
        row = agreement["signal"]["clip.saturation_ceiling"]["multires"]
        self.assertAlmostEqual(row["declared_step_rank"]["spearman"]["point"], 1.0)

    def test_operators_never_mix_into_one_correlation(self):
        availability = {"multires": {"available": True, "reason": "ready"}}
        points = self._points(operator="gain.db") + self._points(
            operator="clip.round_step"
        )
        agreement = pb.build_agreement(
            {"signal": points},
            ["multires"],
            availability,
            trials=10,
            seed=1,
        )
        self.assertEqual(
            sorted(agreement["signal"]),
            ["clip.round_step", "gain.db"],
        )

    def test_fewer_than_two_pairs_is_no_verdict_not_zero(self):
        points = [
            {
                "operator": "gain.db",
                "ladder_step": 1,
                "magnitude": 1.0,
                "objective": {"snr_db": 40.0},
                "metrics": {"multires": 2.0},
            }
        ]
        agreement = pb.build_agreement(
            {"signal": points},
            ["multires"],
            {"multires": {"available": True, "reason": "ready"}},
            trials=10,
            seed=1,
        )
        row = agreement["signal"]["gain.db"]["multires"]["snr_db"]
        self.assertEqual(row["verdict"], "NO VERDICT")

    def test_agreement_carries_no_aggregate_key_anywhere(self):
        document = pb.build_agreement(
            {"signal": self._points()},
            ["multires"],
            {"multires": {"available": True, "reason": "ready"}},
            trials=10,
            seed=1,
        )
        blob = repr(document)
        self.assertNotIn("aggregate", blob.lower())
        self.assertNotIn("oracle", blob.lower())


def numpy_suite():
    """Explicitly numpy-gated suite; fails to start when numpy is absent."""

    import numpy as np

    class NumpyGatedTests(unittest.TestCase):
        def test_resampler_same_rate_is_identity(self):
            signal = [math.sin(2 * math.pi * 440.0 * i / 44100.0) for i in range(1000)]
            self.assertEqual(
                pb.resample_samples(signal, src_rate_hz=44100.0, dst_rate_hz=44100.0),
                signal,
            )

        def test_resampler_declared_output_lengths(self):
            signal = [math.sin(2 * math.pi * 440.0 * i / 44100.0) for i in range(4410)]
            up = pb.resample_samples(signal, src_rate_hz=44100.0, dst_rate_hz=48000.0)
            self.assertEqual(len(up), math.ceil(4410 * 48000 / 44100))
            down = pb.resample_samples(up, src_rate_hz=48000.0, dst_rate_hz=44100.0)
            self.assertEqual(len(down), len(signal))

        def test_kaiser_center_is_one(self):
            offsets = np.asarray([0.0, 1.0, -1.0])
            window = pb._kaiser_continuous(offsets, 32, 16.0, np)
            self.assertAlmostEqual(float(window[0]), 1.0, places=12)

        def test_multires_distance_identity_is_exactly_zero(self):
            signal = [math.sin(2 * math.pi * 220.0 * i / 44100.0) for i in range(4096)]
            outcome = pb.multires_spectral_distance(signal, signal)
            self.assertEqual(outcome["value"], 0.0)
            self.assertTrue(
                pb.reference_dependence_row(
                    "multires-log-spectral-v1",
                    outcome["value"],
                    identity_value=outcome["identity_value"],
                )["holds"]
            )

        def test_multires_distance_rises_with_noise(self):
            signal = [math.sin(2 * math.pi * 220.0 * i / 44100.0) for i in range(8192)]
            import random

            rng = random.Random(5)
            values = []
            for amplitude in (0.001, 0.01, 0.1):
                degraded = [s + amplitude * rng.gauss(0.0, 1.0) for s in signal]
                values.append(pb.multires_spectral_distance(signal, degraded)["value"])
            self.assertLess(values[0], values[1])
            self.assertLess(values[1], values[2])

        def test_multires_rejects_framing_mismatch(self):
            with self.assertRaises(pb.PerceptualBenchmarkError):
                pb.multires_spectral_distance([0.0, 0.0], [0.0])

        def test_resampler_contribution_rows_are_primary_plus_diagnostic(self):
            signal = [math.sin(2 * math.pi * 220.0 * i / 44100.0) for i in range(8820)]
            rows = pb.resampler_contribution_rows(signal)
            self.assertEqual(len(rows), 2)
            primary = rows[0]
            self.assertTrue(primary["primary"])
            self.assertEqual(primary["alignment"], "none (repo contract)")
            self.assertEqual(primary["framing_match"], 1)
            self.assertLessEqual(abs(primary["snr_db"]), 1e9)
            diagnostic = rows[1]
            self.assertFalse(diagnostic["primary"])
            self.assertTrue(diagnostic["diagnostic_only"])
            self.assertIn("estimated_delay_samples", diagnostic)

    return unittest.defaultTestLoader.loadTestsFromTestCase(NumpyGatedTests)


if __name__ == "__main__":
    if "--numpy" in sys.argv:
        sys.argv.remove("--numpy")
        try:
            import numpy  # noqa: F401
        except ImportError:
            raise SystemExit(
                "the --numpy suite requires NumPy: install the locked metrics extra"
            )
        suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        suite.addTests(numpy_suite())
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        sys.exit(not result.wasSuccessful())
    unittest.main()
