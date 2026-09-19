"""Stdlib controls for the scalar probe; real renders live in qualify_scalar.sh."""

import copy
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import qualify_scalar as probe


class ScalarProtocolTests(unittest.TestCase):
    def test_source_gate_refuses_before_import(self):
        plan, _ = probe.load_plan()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "torchsynth").mkdir()
            (root / "torchsynth/config.py").write_text(
                "raise RuntimeError('never import')"
            )
            with (
                patch.dict("sys.modules", {"torch": None, "torchsynth": None}),
                self.assertRaisesRegex(ValueError, "source hash mismatch"),
            ):
                probe.provenance(root, plan)

    def test_sentinel_rejects_changed_expected_bytes(self):
        runtime = dict.fromkeys(
            (
                "name",
                "python",
                "machine",
                "packages",
                "torch_build",
                "device",
                "dtype",
                "threads",
                "interop_threads",
            ),
            "fixture",
        )
        side = {
            "normalized_by_name": {"a": 0.5},
            "traces": [
                {"name": n, "shape": s, "sha256": "a" * 64}
                for n, s in probe.capture_manifest()
            ],
        }
        report = {
            "status": "PASS",
            "fresh_process_repeats_equal": True,
            "runtime": runtime,
            "provenance": {"fixture": "same"},
            "comparisons": [
                {"id": "unit", "canonical": side, "scalar": copy.deepcopy(side)}
            ],
        }
        probe.verify_expected(report, copy.deepcopy(report), ["unit"])
        changed = copy.deepcopy(report)
        changed["comparisons"][0]["scalar"]["traces"][-1]["sha256"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "sentinel drift"):
            probe.verify_expected(report, changed, ["unit"])
        with self.assertRaisesRegex(ValueError, "missing or duplicate"):
            probe.verify_expected(report, report, ["absent"])

    def test_parameter_names_are_a_set_not_positional(self):
        expected = {"a": 0.25, "b": 0.75}
        probe.require_named(expected, {"b": 0.75, "a": 0.25})
        for changed in ({"a": 0.25}, dict(expected, c=0.5), {"a": 0.75, "b": 0.25}):
            with (
                self.subTest(changed=changed),
                self.assertRaisesRegex(ValueError, "input.normalized"),
            ):
                probe.require_named(expected, changed)

    def test_binary32_and_signed_zero_are_checked(self):
        for bad in (float("nan"), 1.01, -0.1, 0.1, True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                probe.require_named({"a": bad}, {"a": bad})
        with self.assertRaisesRegex(ValueError, "input.normalized"):
            probe.require_named({"a": 0.0}, {"a": -0.0})

    def test_byte_metrics_do_not_align_or_rescale(self):
        metric = probe.differences(
            struct.pack("<3f", 0, 1, -1), struct.pack("<3f", 0, 2, -1)
        )
        self.assertFalse(metric["equal_bytes"])
        self.assertEqual(metric["first_different_sample"], 1)
        self.assertEqual(metric["max_abs_difference"], 1)
        self.assertAlmostEqual(metric["mean_abs_difference"], 1 / 3)
        self.assertAlmostEqual(metric["rms_difference"], (1 / 3) ** 0.5)
        metric = probe.differences(struct.pack("<f", 0), struct.pack("<f", -0.0))
        self.assertFalse(metric["equal_bytes"])
        self.assertEqual(metric["max_abs_difference"], 0)

    def test_bad_samples_refuse(self):
        for left, right in (
            (b"", b""),
            (b"x", b"x"),
            (b"1234", b""),
            (struct.pack("<f", float("nan")), b"1234"),
        ):
            with self.subTest(left=left), self.assertRaises(ValueError):
                probe.differences(left, right)

    def test_noise_requires_selected_slot_and_exact_bytes(self):
        data = struct.pack("<2f", 0.25, -0.5)
        probe.require_noise(data, data, 6, 6, count=2)
        for actual, slot in ((data, 0), (data[::-1], 6), (data[:4], 6)):
            with (
                self.subTest(slot=slot),
                self.assertRaisesRegex(ValueError, "input.noise"),
            ):
                probe.require_noise(data, actual, 6, slot, count=2)

    def test_missing_extra_reordered_trace_and_bad_shape_refuse(self):
        expected = probe.capture_manifest()
        trace = [{"name": name, "shape": shape} for name, shape in expected]
        probe.require_trace(trace)
        mutations = [trace[:-1], trace + trace[:1], list(reversed(trace))]
        bad_shape = copy.deepcopy(trace)
        bad_shape[-1]["shape"] = [1]
        mutations.append(bad_shape)
        for changed in mutations:
            with self.assertRaisesRegex(ValueError, "trace manifest"):
                probe.require_trace(changed)

    def test_stale_provenance_refuses(self):
        with self.assertRaisesRegex(ValueError, "provenance"):
            probe.require_provenance({"runner": "old"}, {"runner": "new"})

    def test_artifact_path_hash_and_count_are_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = struct.pack("<2f", 1, 2)
            (root / "trace.f32le").write_bytes(data)
            record = {"file": "trace.f32le", "sha256": probe.sha256(data), "shape": [2]}
            self.assertEqual(probe.read_artifact(root, record), data)
            for key, value in (
                ("file", "../trace.f32le"),
                ("sha256", "0" * 64),
                ("shape", [3]),
            ):
                with self.subTest(key=key), self.assertRaises(ValueError):
                    probe.read_artifact(root, dict(record, **{key: value}))

    def test_preregistered_cases_include_nonzero_noise_and_controls(self):
        config = json.loads(probe.HERE.joinpath("scalar-cases.json").read_text())
        self.assertTrue(
            {0, 6, 31, 39942}.issubset({c["sound_index"] for c in config["cases"]})
        )
        self.assertEqual(
            config["controls"]["wrong-parameter"]["expected_seam"], "input.normalized"
        )
        self.assertEqual(
            config["controls"]["wrong-noise"]["expected_seam"], "input.noise"
        )


if __name__ == "__main__":
    unittest.main()
