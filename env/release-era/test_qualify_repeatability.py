"""Stdlib integrity tests; actual rendering is exercised separately by the sentinel."""

import copy
import struct
import tempfile
import unittest
from pathlib import Path

import qualify_repeatability as q


class RepeatabilityTests(unittest.TestCase):
    def test_coordinates(self):
        for index in (0, 31, 32, 9215, 9216, 39942):
            for size in (32, 64, 128, 256):
                value = q.coordinates(index, size)
                self.assertEqual(value["batch"] * size + value["slot"], index)
                self.assertEqual(value["noise_slot"], index % 32)
                self.assertEqual(value["is_train"], index in (0, 31, 32, 9215))
        with self.assertRaises(ValueError):
            q.coordinates(0, 1)

    def test_complete_matrix(self):
        plan = q.load_plan()
        self.assertEqual(len(q.expected_cells(plan)), 128)
        for key in ("batch_sizes", "repeats", "cases"):
            changed = copy.deepcopy(plan)
            changed[key].pop()
            with self.subTest(key=key), self.assertRaises(ValueError):
                q.validate_plan(changed)

    def test_byte_exact_signed_zero(self):
        result = q.compare_bytes(struct.pack("<f", 0), struct.pack("<f", -0.0))
        self.assertFalse(result["equal_bytes"])
        self.assertEqual(result["first_different_sample"], 0)
        self.assertEqual(result["max_abs_difference"], 0)

    def test_difference_metrics(self):
        result = q.compare_bytes(struct.pack("<2f", 1, 2), struct.pack("<2f", 1, 4))
        self.assertEqual(result["first_different_sample"], 1)
        self.assertEqual(result["max_abs_difference"], 2)
        self.assertEqual(result["mean_abs_difference"], 1)

    def test_shape_finite_and_count_errors(self):
        for a, b in (
            (b"", b""),
            (b"abc", b"abc"),
            (b"1234", b"12345678"),
            (struct.pack("<f", float("nan")), struct.pack("<f", 0)),
        ):
            with self.subTest(a=a), self.assertRaises(ValueError):
                q.compare_bytes(a, b)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = struct.pack("<f", 1)
            (root / "a").write_bytes(data)
            record = {"file": "a", "sha256": q.sha256(data), "samples": 2}
            with self.assertRaisesRegex(ValueError, "count"):
                q.read_artifact(root, record)
            record["samples"] = 1
            record["sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "hash"):
                q.read_artifact(root, record)

    def test_missing_stale_duplicate_results(self):
        plan = q.load_plan()
        with self.assertRaisesRegex(ValueError, "missing"):
            q.validate_results(plan, [])
        cells = [
            dict(
                cell,
                status="NO_VERDICT",
                error="resource refusal",
                plan_sha256=q.plan_hash(plan),
            )
            for cell in q.expected_cells(plan)
        ]
        q.validate_results(plan, cells)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            q.validate_results(plan, cells + [cells[0]])
        cells[0]["plan_sha256"] = "stale"
        with self.assertRaisesRegex(ValueError, "stale"):
            q.validate_results(plan, cells)

    def test_refusal_is_not_a_pass(self):
        self.assertEqual(q.verdict(["PASS", "NO_VERDICT"]), "NO_VERDICT")
        self.assertEqual(q.verdict(["FAIL", "NO_VERDICT"]), "FAIL")
        self.assertEqual(q.verdict([]), "NO_VERDICT")
        self.assertEqual(
            q.pair({"status": "NO_VERDICT"}, {"status": "PASS"}, Path("."))["status"],
            "NO_VERDICT",
        )

    def test_sentinel_rejects_expected_input_audio_and_label_changes(self):
        cell = {
            "status": "PASS",
            "input_sha256": "1" * 64,
            "plan_sha256": "2" * 64,
            "artifacts": {"audio": {"sha256": "3" * 64}},
            "label_byte_hex": "01",
            "passive_capture": {"equal_bytes": True},
        }
        expected = {
            "input_sha256": cell["input_sha256"],
            "plan_sha256": cell["plan_sha256"],
            "artifact_sha256": {"audio": "3" * 64},
            "label_byte_hex": "01",
        }
        q.check_sentinel(cell, expected)
        self.assertEqual(len(q.sentinel_controls(cell, expected)), 2)
        for field, message in (
            ("plan_sha256", "preregistration"),
            ("label_byte_hex", "label"),
        ):
            mutated = dict(expected, **{field: "wrong"})
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, message):
                q.check_sentinel(cell, mutated)
        cell["status"] = "NO_VERDICT"
        with self.assertRaisesRegex(ValueError, "unavailable"):
            q.check_sentinel(cell, expected)

    def test_changed_audio_bytes_rejected_on_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = struct.pack("<2f", 0.25, -0.5)
            record = {"file": "audio", "sha256": q.sha256(original), "samples": 2}
            (root / "audio").write_bytes(struct.pack("<2f", 0.5, -0.5))
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                q.read_artifact(root, record)

    def test_missing_executable_is_explicit_refusal(self):
        result = q.invoke(["/nonexistent/issue-12-runtime"])
        self.assertEqual(result.returncode, -1)
        self.assertIn("FileNotFoundError", result.stderr)


if __name__ == "__main__":
    unittest.main()
