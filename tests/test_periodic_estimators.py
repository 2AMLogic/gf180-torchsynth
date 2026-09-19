"""Periodic analytic qualification; --numerical is required in dedicated CI."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from torchsynth_voice.periodic_estimators import (
    estimate_periodic,
    estimate_prepared_pair,
    identity_metadata,
    score_periodic,
    wrap_phase,
)
from torchsynth_voice.scorecard import validate_row


class ContractTests(unittest.TestCase):
    def test_wrap(self):
        self.assertEqual(wrap_phase(math.pi), -math.pi)
        self.assertAlmostEqual(wrap_phase(3 * math.pi), -math.pi)

    def test_identity_has_explicit_boundary(self):
        meta = identity_metadata(1764, 441, "1", time_origin=0.375)
        self.assertEqual(meta["window"], [0, 1764])
        self.assertEqual(meta["time_origin"], 0.375)
        self.assertEqual(meta["transforms"], [])
        self.assertEqual(meta["scope"], "analytic")

    def test_missing_numerical_dependency_refuses(self):
        with mock.patch.dict(sys.modules, {"numpy": None}):
            result = estimate_periodic(
                [0.0] * 20,
                sample_rate_hz=441,
                unit="1",
                family="lfo",
                reference_hz=4.37,
            )
        self.assertEqual(result["reason"], "numpy_unavailable_install_metrics_extra")
        self.assertTrue(all(value is None for value in result["estimates"].values()))

    def test_committed_record_digests_and_null_refusals(self):
        path = (
            Path(__file__).resolve().parents[1] / "sim/qualification/periodic-v1.json"
        )
        report = json.loads(path.read_text())
        self.assertEqual(report["qualification_failures"], [])
        self.assertEqual(report["counts"]["cases"], len(report["records"]))
        for record in report["records"] + report["paired_sentinels"]:
            sha = hashlib.sha256(record["diagnostic_json"].encode()).hexdigest()
            for row in record["rows"]:
                validate_row(row)
                self.assertEqual(row["artifact"]["sha256"], sha)
                if row["verdict"] == "NO VERDICT":
                    self.assertIsNone(row["observed"])


class NumericalTests(unittest.TestCase):
    def cosine(self, frequency=110.37, phase=-0.31, gain=1, rate=44100, n=44100):
        import numpy as np

        return gain * np.cos(2 * np.pi * frequency * np.arange(n) / rate + phase)

    def measure(self, samples, **kwargs):
        rate = kwargs.pop("sample_rate_hz", 44100)
        return estimate_periodic(
            samples,
            sample_rate_hz=rate,
            unit="1",
            family="oscillator",
            reference_hz=110.37,
            preparation=identity_metadata(len(samples), rate, "1"),
            fundamental_in_band=True,
            **kwargs,
        )

    def test_measures_frequency_instead_of_returning_reference(self):
        result = self.measure(self.cosine(frequency=113.17))
        self.assertAlmostEqual(result["estimates"]["frequency_hz"], 113.17, places=6)
        self.assertGreater(result["estimates"]["cents"], 40)

    def test_phase_and_one_sample_delay_stay_visible(self):
        result = self.measure(self.cosine(phase=0))
        delayed = self.measure(self.cosine(phase=-2 * math.pi * 110.37 / 44100))
        self.assertAlmostEqual(result["estimates"]["timing_samples"], 0, places=5)
        self.assertAlmostEqual(delayed["estimates"]["timing_samples"], 1, places=5)

    def test_silence_short_and_mixture_refuse(self):
        import numpy as np

        for signal in (
            np.zeros(44100),
            self.cosine(n=20),
            self.cosine() + self.cosine(frequency=137.4, gain=0.3),
        ):
            with self.subTest(length=len(signal)):
                self.assertNotEqual(self.measure(signal)["status"], "valid")

    def test_missing_floor_is_null_and_digest_is_real(self):
        result = self.measure(self.cosine())
        rows, record = score_periodic(result, case_id="floor-missing")
        for row in rows:
            validate_row(row)
            self.assertEqual(row["verdict"], "NO VERDICT")
            self.assertIsNone(row["observed"])
            self.assertEqual(
                row["artifact"]["sha256"], hashlib.sha256(record).hexdigest()
            )
        self.assertIsNotNone(
            json.loads(record)["measurement"]["estimates"]["frequency_hz"]
        )

    def test_absolute_depth_and_gain_are_not_fitted_away(self):
        original = self.measure(self.cosine())
        louder = self.measure(self.cosine(gain=10**0.05))
        self.assertAlmostEqual(
            louder["estimates"]["depth_peak_to_peak"]
            / original["estimates"]["depth_peak_to_peak"],
            10**0.05,
            places=9,
        )

    def test_phase_origin_and_wrap(self):
        rate, frequency, origin = 44100, 110.37, 0.375
        for phase in (-math.pi, -math.pi + 1e-4, math.pi - 1e-4):
            samples = self.cosine(phase=phase + math.tau * frequency * origin)
            result = estimate_periodic(
                samples,
                sample_rate_hz=rate,
                unit="1",
                family="oscillator",
                reference_hz=frequency,
                time_origin=origin,
                fundamental_in_band=True,
                preparation=identity_metadata(
                    len(samples), rate, "1", time_origin=origin
                ),
            )
            self.assertLess(
                abs(wrap_phase(result["estimates"]["phase_rad"] - phase)), 1e-7
            )

    def test_declared_preparation_and_alias_provenance_required(self):
        samples = self.cosine()
        for preparation, fundamental_in_band in (
            (None, True),
            (identity_metadata(len(samples), 44100, "1"), False),
        ):
            result = estimate_periodic(
                samples,
                sample_rate_hz=44100,
                unit="1",
                family="oscillator",
                reference_hz=110.37,
                preparation=preparation,
                fundamental_in_band=fundamental_in_band,
            )
            self.assertEqual(result["status"], "invalid")
        for field, value in (
            ("transforms", ["alignment"]),
            ("invariants_passed", False),
            ("unit", "volt"),
        ):
            preparation = identity_metadata(len(samples), 44100, "1")
            preparation[field] = value
            result = estimate_periodic(
                samples,
                sample_rate_hz=44100,
                unit="1",
                family="oscillator",
                reference_hz=110.37,
                preparation=preparation,
                fundamental_in_band=True,
            )
            self.assertEqual(result["status"], "invalid")

    def test_input_validation(self):
        import numpy as np

        for samples in (
            [True, 1],
            [[1, 2]],
            [math.nan],
            [math.inf],
            [2**53 + 1],
            [1j],
            np.array([1], dtype=object),
        ):
            with self.subTest(samples=str(samples)), self.assertRaises(ValueError):
                self.measure(samples)
        for reference in (0, -1, math.inf, True):
            with self.assertRaises(ValueError):
                estimate_periodic(
                    [0] * 20,
                    sample_rate_hz=441,
                    unit="1",
                    family="lfo",
                    reference_hz=reference,
                )
        outside = self.measure(self.cosine(frequency=1, n=176400))
        self.assertEqual(outside["reason"], "frequency_outside_declared_domain")

    def test_directed_pure_shapes_blends_and_square_uncertainty(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
        from qualify_periodic_estimators import fixture, grid

        for case in grid():
            if not case["id"].startswith("shape-") or case["frequency"] != 4.37:
                continue
            samples, rate, reference, truth = fixture(case)
            result = estimate_periodic(
                samples,
                sample_rate_hz=rate,
                unit="1",
                family="lfo",
                reference_hz=reference,
                weights=case["weights"],
                fundamental_in_band=True,
                preparation=identity_metadata(len(samples), rate, "1"),
            )
            if "cancel" in case["id"]:
                self.assertEqual(result["status"], "invalid")
            else:
                self.assertEqual(result["status"], "valid", case["id"])
                self.assertLess(
                    abs(result["estimates"]["frequency_hz"] - truth["frequency_hz"]),
                    0.001,
                )
                self.assertLess(
                    abs(
                        result["estimates"]["depth_peak_to_peak"]
                        - truth["depth_peak_to_peak"]
                    ),
                    1e-4,
                )
                if case["weights"][0] != 1:
                    self.assertIsNone(result["estimates"]["phase_rad"])
                if "sqr" in case["id"]:
                    lo, hi = result["diagnostics"]["frequency_interval_hz"]
                    self.assertLessEqual(lo, truth["frequency_hz"])
                    self.assertGreaterEqual(hi, truth["frequency_hz"])

    def test_floor_truth_limit_and_domain_fail_closed(self):
        measurement = self.measure(self.cosine())
        qualification = {
            "range_key": measurement["range_key"],
            "algorithm": measurement["algorithm"],
            "qualified": True,
            "guard_floors": {"frequency_hz": 1e-7},
        }
        good = {
            "expected": {"frequency_hz": 110.37},
            "limits": {"frequency_hz": 1e-4},
            "qualification": qualification,
            "truth_source": "analytic cosine",
            "limit_source": "test preregistration",
        }
        rows, _ = score_periodic(measurement, case_id="good", **good)
        self.assertEqual(rows[0]["verdict"], "PASS")
        for changes in (
            {"limits": {"frequency_hz": 1e-8}},
            {"expected": {}},
            {"limits": {}},
            {"qualification": dict(qualification, range_key="wrong")},
            {"qualification": dict(qualification, qualified=False)},
            {"qualification": dict(qualification, algorithm="superseded")},
        ):
            args = dict(good, **changes)
            rows, _ = score_periodic(measurement, case_id="refused", **args)
            self.assertEqual(rows[0]["verdict"], "NO VERDICT")
            self.assertIsNone(rows[0]["observed"])

    def test_lfo_modulation_without_metadata_hint_and_zero_weights_refuse(self):
        import numpy as np

        time = np.arange(1764) / 441
        raw = (1 - np.cos(math.tau * 4.37 * time)) / 2
        cases = (
            (
                (1 - np.cos(math.tau * 4.37 * time + 0.3 * np.sin(math.tau * time)))
                / 2,
                (1, 0, 0, 0, 0),
            ),
            (raw * np.linspace(0.1, 1, len(raw)), (1, 0, 0, 0, 0)),
            (raw, (0, 0, 0, 0, 0)),
        )
        for samples, weights in cases:
            result = estimate_periodic(
                samples,
                sample_rate_hz=441,
                unit="1",
                family="lfo",
                reference_hz=4.37,
                weights=weights,
                fundamental_in_band=True,
                preparation=identity_metadata(len(samples), 441, "1"),
            )
            self.assertEqual(result["status"], "invalid")

    def test_independent_faults_and_resolution_control(self):
        report = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "sim/qualification/periodic-v1.json"
            ).read_text()
        )
        for record in report["records"]:
            case = record["case"]
            if not case["id"].startswith("mutation-"):
                continue
            rows = {r["property"]: r for r in record["rows"]}
            for name in case["mandatory"]:
                self.assertEqual(rows[name]["verdict"], "FAIL")
            if "resolution_limit" in case:
                self.assertEqual(rows["frequency_hz"]["verdict"], "NO VERDICT")


def shared_preparation_probe(path):
    """Exercise the actual #86 source without installing or changing it."""
    module_name = "torchsynth_voice._periodic_preparation_probe"
    spec = importlib.util.spec_from_file_location(module_name, path)
    api = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = api
    spec.loader.exec_module(api)
    samples = NumericalTests().cosine(n=44100)
    reference = api.Signal(samples, 44100, "1")
    candidate = api.Signal(samples * 10**0.05, 44100, "1")
    pair = estimate_prepared_pair(
        reference,
        candidate,
        family="oscillator",
        reference_hz=110.37,
        analytic=True,
        fundamental_in_band=True,
        window=(441, 22050),
        preparation_api=api,
    )
    assert all(m["status"] == "valid" for m in pair["measurements"])
    a, b = (m["estimates"] for m in pair["measurements"])
    assert abs(b["depth_peak_to_peak"] / a["depth_peak_to_peak"] - 10**0.05) < 1e-8
    assert abs(wrap_phase(b["phase_rad"] - a["phase_rad"])) < 1e-7
    assert pair["preparation"][0]["window_bounds"] == [441, 22050]
    assert (
        pair["measurements"][0]["input_sha256"]
        == pair["preparation"][0]["prepared_sha256"]
    )
    refused = estimate_prepared_pair(
        reference,
        candidate,
        family="oscillator",
        reference_hz=110.37,
        fundamental_in_band=True,
        preparation_api=api,
    )
    assert all(m["status"] == "invalid" for m in refused["measurements"])
    return {
        "source_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        "analytic_window_gain_phase_hash_probe": "passed",
        "production": "refused_pending_runtime_qualification",
    }


if __name__ == "__main__":
    if "--preparation-source" in sys.argv:
        index = sys.argv.index("--preparation-source")
        source_path = sys.argv[index + 1]
        del sys.argv[index : index + 2]
        print(json.dumps(shared_preparation_probe(source_path), sort_keys=True))
    numerical = "--numerical" in sys.argv
    if numerical:
        sys.argv.remove("--numerical")
        import numpy  # noqa: F401 -- required, failure must not become a skip
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ContractTests)
    if numerical:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(NumericalTests))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())


def load_tests(loader, standard_tests, pattern):
    return loader.loadTestsFromTestCase(ContractTests)
