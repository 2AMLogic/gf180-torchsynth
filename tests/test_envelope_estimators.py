from __future__ import annotations

import math
import copy
import hashlib
import json
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.envelope_estimators import (  # noqa: E402
    DESTINATIONS,
    estimate_envelope,
    estimate_routes,
    broadband_amplitude,
    qualification_rows,
    tone_amplitude,
)
from torchsynth_voice.paired_metrics import Limit  # noqa: E402

sys.path.insert(0, str(ROOT / "tools"))
from qualify_envelope_estimators import (  # noqa: E402
    constructed_envelope,
    run_grid,
    validate_evidence,
)


class EnvelopeEstimatorTests(unittest.TestCase):
    def envelope(self, **changes):
        params = dict(
            attack=20.25,
            decay=25.5,
            release=30.75,
            sustain=0.4,
            alpha=1,
            note=90.5,
            count=150,
        )
        params.update(changes)
        samples, truth = constructed_envelope(**params)
        result = estimate_envelope(
            samples,
            rate_hz=441,
            note_on_seconds=params["note"] / 441,
            alpha=params["alpha"],
            origin_samples=params.get("origin", 0),
        )
        return samples, truth, result

    def test_linear_fractional_boundaries_are_measured(self):
        # Independent piecewise linear construction, epsilon=0.
        signal = []
        for t in range(100):
            if t < 10.25:
                y = t / 10.25
            elif t < 30.75:
                y = 1 - 0.6 * (t - 10.25) / 20.5
            elif t < 60.5:
                y = 0.4
            else:
                y = 0.4 * max(0, 1 - (t - 60.5) / 15.75)
            signal.append(y)
        result = estimate_envelope(
            signal, rate_hz=100, note_on_seconds=0.605, alpha=1, epsilon=0
        )
        for name, expected in (
            ("attack_end", 10.25),
            ("decay_end", 30.75),
            ("release_end", 76.25),
            ("sustain_amplitude", 0.4),
        ):
            self.assertAlmostEqual(result["metrics"][name]["value"], expected, places=8)

    def test_silence_refuses(self):
        result = estimate_envelope([0] * 100, rate_hz=100, note_on_seconds=0.5, alpha=1)
        self.assertIsNone(result["metrics"]["attack_end"]["value"])

    def test_route_swap_observes_destinations(self):
        source = [i / 32 for i in range(32)]
        outputs = {name: [0] * 32 for name in DESTINATIONS}
        outputs["noise_amp"] = source
        result = estimate_routes(source, outputs, rate_hz=441)
        self.assertAlmostEqual(result["metrics"]["gain.noise_amp"]["value"], 1)
        self.assertAlmostEqual(result["metrics"]["gain.vco_1_amp"]["value"], 0)

    def test_tone_phase_and_nonintegral_window(self):
        samples = [
            0.7 * math.sin(2 * math.pi * 733 * (i + 17) / 44100 + 0.43) + 0.1
            for i in range(777)
        ]
        result = tone_amplitude(
            samples, rate_hz=44100, frequency_hz=733, origin_samples=17
        )
        self.assertAlmostEqual(result["metrics"]["amplitude"]["value"], 0.7, places=10)

    def test_alpha_epsilon_and_amplitudes(self):
        for alpha in (0.1, 0.5, 1, 2, 6):
            _, truth, result = self.envelope(alpha=alpha)
            for name, expected in truth.items():
                with self.subTest(alpha=alpha, property=name):
                    self.assertAlmostEqual(
                        result["metrics"][name]["value"], expected, delta=2e-5
                    )

    def test_zero_inverse_release_stays_on(self):
        samples, _, result = self.envelope(release=0)
        self.assertGreater(samples[-1], 0.39)
        self.assertIsNone(result["metrics"]["release_end"]["value"])

    def test_zero_decay_ignores_sustain(self):
        samples, _, result = self.envelope(decay=0, sustain=0.2)
        self.assertGreater(samples[60], 0.99)
        self.assertIsNone(result["metrics"]["decay_end"]["value"])

    def test_noteoff_compresses_attack_and_decay(self):
        for changes in ({"attack": 120}, {"decay": 100}):
            samples, truth, result = self.envelope(**changes)
            self.assertEqual(truth["decay_end"], 90.5)
            self.assertGreater(samples[89], 0.3)
            self.assertIsNone(result["metrics"]["attack_end"]["value"])
            self.assertAlmostEqual(result["metrics"]["release_end"]["value"], 121.25)

    def test_fractional_sample_origin(self):
        _, truth, result = self.envelope(origin=0.375)
        self.assertAlmostEqual(
            result["metrics"]["attack_end"]["value"], truth["attack_end"]
        )
        self.assertIsInstance(result["metrics"]["observed_peak_index"]["value"], int)

    def test_delay_not_silently_aligned(self):
        samples, _, _ = self.envelope()
        result = estimate_envelope(
            [0] + samples[:-1], rate_hz=441, note_on_seconds=90.5 / 441, alpha=1
        )
        self.assertIsNone(result["metrics"]["attack_end"]["value"])
        self.assertIn("origin", result["metrics"]["attack_end"]["reason"])

    def test_release_outside_clip_refuses(self):
        _, _, result = self.envelope(release=200)
        self.assertEqual(
            result["metrics"]["release_end"]["reason"],
            "release_end_outside_observation",
        )

    def test_unobserved_or_flat_stages_refuse(self):
        for changes in ({"sustain": 1}, {"attack": 1}, {"decay": 1}, {"count": 10}):
            _, _, result = self.envelope(**changes)
            self.assertIsNone(result["metrics"]["attack_end"]["value"])

    def test_bad_metadata_and_samples_rejected(self):
        for samples in ([float("nan")], [True], [[1]], "audio"):
            with self.subTest(samples=samples), self.assertRaises(ValueError):
                estimate_envelope(samples, rate_hz=441, note_on_seconds=1, alpha=1)
        for changes in (
            {"rate_hz": 0},
            {"alpha": 0},
            {"alpha": 7},
            {"epsilon": -1},
            {"note_on_seconds": -1},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                estimate_envelope(
                    [0, 1],
                    **{"rate_hz": 441, "note_on_seconds": 1, "alpha": 1, **changes},
                )

    def test_forbidden_preparation_refuses_all_families(self):
        for operation in ("align", "normalize", "resample", "hilbert"):
            result = tone_amplitude(
                [0.5] * 8192, rate_hz=44100, frequency_hz=1000, preparation=operation
            )
            self.assertEqual(
                result["metrics"]["amplitude"]["reason"], "preparation_forbidden"
            )

    def test_tone_boundary_and_harmonic_refuse(self):
        for nonlinear in (False, True):
            samples = [
                math.sin(i / 10) * (0.5 if i > 512 else 1)
                if not nonlinear
                else math.sin(i / 10) + 0.1 * math.sin(i / 5)
                for i in range(1024)
            ]
            result = tone_amplitude(
                samples, rate_hz=1000, frequency_hz=1000 / (20 * math.pi)
            )
            self.assertIsNone(result["metrics"]["amplitude"]["value"])

    def test_tone_gain_phase_and_dc_are_separate(self):
        samples = [math.sin(i / 10 + 1.3) * 0.5 + 0.7 for i in range(1024)]
        result = tone_amplitude(
            samples, rate_hz=1000, frequency_hz=1000 / (20 * math.pi)
        )
        self.assertAlmostEqual(result["metrics"]["amplitude"]["value"], 0.5)
        self.assertAlmostEqual(result["diagnostics"]["coefficients"][2], 0.7)

    def test_minimum_tone_window_and_route_excitation_window(self):
        for count in (31, 32):
            samples = [0.5 * math.sin(2 * math.pi * i / 8 + 0.3) for i in range(count)]
            result = tone_amplitude(samples, rate_hz=8000, frequency_hz=1000)
            self.assertEqual(
                result["metrics"]["amplitude"]["value"] is not None, count == 32
            )
        for count in (7, 8):
            xs = [i / 8 for i in range(count)]
            result = estimate_routes(
                xs, {name: xs for name in DESTINATIONS}, rate_hz=441
            )
            self.assertEqual(
                result["metrics"]["gain.noise_amp"]["value"] is not None, count == 8
            )

    def test_broadband_realizations_and_floor(self):
        for seed in (10, 11, 12):
            rng = random.Random(seed)
            samples = [rng.gauss(0, 0.5) for _ in range(8192)]
            result = broadband_amplitude(
                samples, rate_hz=44100, source_law="iid_gaussian_unit_variance"
            )
            self.assertAlmostEqual(
                result["metrics"]["amplitude"]["value"], 0.5, delta=0.025
            )
        result = broadband_amplitude(
            [1e-9] * 8192, rate_hz=44100, source_law="iid_gaussian_unit_variance"
        )
        self.assertEqual(result["metrics"]["amplitude"]["reason"], "amplitude_floor")

    def test_broadband_law_short_window_and_boundary_refuse(self):
        for count, law in ((128, "iid_gaussian_unit_variance"), (8192, "colored")):
            result = broadband_amplitude([1] * count, rate_hz=44100, source_law=law)
            self.assertIsNone(result["metrics"]["amplitude"]["value"])
        result = broadband_amplitude(
            [0] * 4096 + [1, -1] * 2048,
            rate_hz=44100,
            source_law="iid_gaussian_unit_variance",
        )
        self.assertIn("nonstationary", result["metrics"]["amplitude"]["reason"])

    def test_signed_route_gain_and_physical_depth_measured_independently(self):
        source = [i / 32 for i in range(32)]
        outputs = {name: [-2 * x + 0.4 for x in source] for name in DESTINATIONS}
        physical = {name: [7 * x - 0.8 for x in source] for name in DESTINATIONS}
        result = estimate_routes(
            source, outputs, rate_hz=441, physical_outputs=physical
        )
        for name in DESTINATIONS:
            self.assertAlmostEqual(result["metrics"][f"gain.{name}"]["value"], -2)
            self.assertAlmostEqual(result["metrics"][f"depth.{name}"]["value"], 7)
        self.assertEqual(result["metrics"]["depth.vco_1_pitch"]["unit"], "semitone")

    def test_route_nonlinearity_and_no_excitation_refuse(self):
        source = [i / 32 for i in range(32)]
        for xs in (source, [0] * 32):
            outputs = {name: [x * x for x in source] for name in DESTINATIONS}
            result = estimate_routes(xs, outputs, rate_hz=441)
            self.assertTrue(all(m["value"] is None for m in result["metrics"].values()))

    def test_route_missing_destination_and_sample_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            estimate_routes([1] * 10, {"noise_amp": [1] * 10}, rate_hz=441)
        with self.assertRaises(ValueError):
            estimate_routes(
                [1] * 10, {name: [1] * 11 for name in DESTINATIONS}, rate_hz=441
            )

    def test_floor_and_unknown_limits_remain_null(self):
        _, truth, result = self.envelope()
        rows, data = qualification_rows(
            result,
            case_id="resolution",
            limits={
                "attack_end": Limit(
                    truth["attack_end"], 0.001, "control_sample", "test resolution"
                )
            },
        )
        self.assertTrue(
            all(r["observed"] is None and r["verdict"] == "NO VERDICT" for r in rows)
        )
        self.assertEqual(
            rows[0]["artifact"]["sha256"], hashlib.sha256(data).hexdigest()
        )


class QualificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.evidence = run_grid()

    def test_actual_grid_obligations(self):
        validate_evidence(self.evidence)
        self.assertGreaterEqual(len(self.evidence["records"]), 267)

    def test_all_twenty_named_routes_and_controls(self):
        routes = [
            record for record in self.evidence["records"] if record["domain"] == "route"
        ]
        self.assertEqual(len(routes), 100)
        sources = set()
        for record in routes:
            fixture = json.loads(record["diagnostic_utf8"])["comparison"]["fixture"]
            sources.add(fixture["case_id"])
            self.assertEqual(len(fixture["patch_sha256"]), 64)
            if fixture["fault"] == "destination":
                failures = [
                    r
                    for r in record["rows"]
                    if r["verdict"] == "FAIL" and r["property"].startswith("gain.")
                ]
                self.assertEqual(len(failures), 2)
        self.assertEqual(len(sources), 20)

    def test_breakpoint_controls_and_declared_blindspot(self):
        for stage in ("attack", "decay", "release"):
            record = next(
                r
                for r in self.evidence["records"]
                if r["case_id"] == f"mutation:{stage}:+2"
            )
            row = next(r for r in record["rows"] if r["property"] == stage + "_end")
            self.assertEqual(row["verdict"], "FAIL")
        record = next(
            r
            for r in self.evidence["records"]
            if r["case_id"] == "blindspot:attack:+0.005"
        )
        self.assertEqual(record["rows"][0]["verdict"], "PASS")

    def test_diagnostic_digest_tamper_rejected(self):
        evidence = copy.deepcopy(self.evidence)
        evidence["records"][0]["diagnostic_utf8"] += " "
        with self.assertRaisesRegex(ValueError, "digest"):
            validate_evidence(evidence)

    def test_plausible_row_tamper_rejected_against_diagnostic(self):
        evidence = copy.deepcopy(self.evidence)
        evidence["records"][0]["rows"][0]["observed"] += 0.001
        with self.assertRaisesRegex(ValueError, "derived rows"):
            validate_evidence(evidence)

    def test_fabricated_obligation_success_rejected(self):
        evidence = copy.deepcopy(self.evidence)
        evidence["obligations"][0]["expected_verdict"] = "FAIL"
        with self.assertRaisesRegex(ValueError, "obligations failed"):
            validate_evidence(evidence)

    def test_minimum_stage_lengths_and_separate_floors(self):
        for stage in ("attack", "decay", "release"):
            floor = self.evidence["floors"][f"direct.{stage}_end"]
            self.assertEqual(floor["minimum_tested_stage_samples"], 4)
            self.assertGreater(floor["refused"], 0)
        self.assertGreater(
            self.evidence["floors"]["broadband.amplitude"]["maximum_absolute_error"],
            1e-4,
        )
        self.assertLess(
            self.evidence["floors"]["tone.amplitude"]["maximum_absolute_error"], 1e-7
        )


if __name__ == "__main__":
    unittest.main()
