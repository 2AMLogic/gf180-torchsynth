"""Stdlib integrity tests; actual rendering is exercised separately by the sentinel."""

import copy
import json
import struct
import tempfile
import unittest
import uuid
from pathlib import Path

import qualify_repeatability as q


class RepeatabilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.plan = q.load_plan()

    def cell(self):
        """Synthetic complete observations, never qualification evidence."""
        plan = self.plan
        cell = dict(
            q.expected_cells(plan)[0],
            status="PASS",
            plan_sha256=q.plan_hash(plan),
            run_id="test-run",
            execution_id=str(uuid.uuid4()),
            directory="release-32-1",
            identity=q.coordinates(0, 32),
            inputs=q.case_inputs(plan, plan["cases"][0]),
            parameter_names=plan["parameter_names"],
            label_byte_hex="01",
            passive_capture={"equal_bytes": True},
            normalization_applied=False,
        )
        cell["input_sha256"] = q.sha256(q.json_bytes(cell["inputs"]))
        cell["artifacts"] = {}
        directory = self.root / cell["directory"]
        directory.mkdir(exist_ok=True)
        for name, spec in plan["artifacts"].items():
            count = spec["shape"][0]
            data = struct.pack("<f", 0.25) * count
            filename = cell["case"] + "." + name + ".f32le"
            (directory / filename).write_bytes(data)
            cell["artifacts"][name] = dict(
                spec, file=filename, sha256=q.sha256(data), samples=count
            )
        for kind in ("normalized", "physical"):
            cell[kind] = dict.fromkeys(plan["parameter_names"], 0.25)
        self.receipt(cell)
        return cell

    def receipt(self, cell):
        q.write_json(
            self.root / "provenance.json",
            {
                "run_id": cell["run_id"],
                "plan_sha256": q.plan_hash(self.plan),
                "runner_sha256": q.sha256(Path(q.__file__).read_bytes()),
            },
        )
        worker = {
            "run_id": cell["run_id"],
            "execution_id": cell["execution_id"],
            "plan_sha256": q.plan_hash(self.plan),
            "source_sha256": q.expected_source_hashes(),
            "source_validated_before_import": True,
            "rng_sentinel": "PASS",
            "runner_sha256": q.sha256(Path(q.__file__).read_bytes()),
            "runtime": dict(
                self.plan["runtime_definitions"]["release"],
                threads=1,
                interop_threads=1,
                thread_environment=q.THREAD_ENV,
                math_environment=self.plan["profile_environment"]["release"],
            ),
            "cells": [{k: v for k, v in cell.items() if k != "directory"}],
        }
        directory = self.root / cell["directory"]
        q.write_json(directory / "result.json", worker)
        q.write_json(directory / "stdout.json", worker)
        q.write_json(
            directory / "execution.json",
            {
                "exit_code": 0,
                "directory": cell["directory"],
                "stdout_sha256": q.sha256((directory / "stdout.json").read_bytes()),
            },
        )

    def expected(self, cell):
        return {
            "input_sha256": cell["input_sha256"],
            "plan_sha256": cell["plan_sha256"],
            "artifact_sha256": {k: v["sha256"] for k, v in cell["artifacts"].items()},
            "label_byte_hex": cell["label_byte_hex"],
        }

    def refused_matrix(self):
        return [
            dict(
                c,
                status="NO_VERDICT",
                error="not observed",
                plan_sha256=q.plan_hash(self.plan),
            )
            for c in q.expected_cells(self.plan)
        ]

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
        cell = self.cell()
        expected = self.expected(cell)
        q.check_sentinel(cell, expected, self.root)
        self.assertEqual(len(q.sentinel_controls(cell, expected, self.root)), 4)
        for field, message in (
            ("plan_sha256", "preregistration"),
            ("label_byte_hex", "label"),
        ):
            mutated = dict(expected, **{field: "wrong"})
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, message):
                q.check_sentinel(cell, mutated, self.root)
        cell["status"] = "NO_VERDICT"
        with self.assertRaisesRegex(ValueError, "unavailable"):
            q.check_sentinel(cell, expected, self.root)

    def test_empty_artifacts_refused_at_all_public_boundaries(self):
        cell = self.cell()
        cell["artifacts"] = {}
        cells = self.refused_matrix()
        cells[0] = cell
        for call in (
            lambda: q.summarize(self.plan, cells, self.root / "nonexistent"),
            lambda: q.pair(cell, cell, self.root),
            lambda: q.check_sentinel(cell, self.expected(cell), self.root),
        ):
            with self.assertRaisesRegex(ValueError, "required artifact set"):
                call()

    def test_wrong_audio_counts_names_and_dtypes_refused(self):
        original = self.cell()
        for field, value in (
            ("samples", 78),
            ("shape", [78]),
            ("dtype", "<f8"),
            ("file", original["artifacts"]["normalized"]["file"]),
        ):
            for side in (0, 1):
                pair = [copy.deepcopy(original), copy.deepcopy(original)]
                pair[side]["artifacts"]["audio"][field] = value
                with (
                    self.subTest(field=field, side=side),
                    self.assertRaisesRegex(ValueError, "count/shape/dtype/name"),
                ):
                    q.pair(*pair, self.root)
        cell = copy.deepcopy(original)
        cell["artifacts"]["audio"] = cell["artifacts"]["normalized"]
        with self.assertRaisesRegex(ValueError, "count/shape/dtype/name"):
            q.check_sentinel(cell, self.expected(cell), self.root)

    def test_coordinate_configuration_label_and_parameter_map_binding(self):
        original = self.cell()
        mutations = [
            ("identity", "index", 999999),
            ("identity", "slot", 1),
            ("identity", "noise_slot", 1),
            ("identity", "is_train", False),
            ("inputs", "nebula", "drum"),
            ("normalized", self.plan["parameter_names"][0], 0.5),
        ]
        for section, key, value in mutations:
            cell = copy.deepcopy(original)
            cell[section][key] = value
            self.receipt(cell)
            cells = self.refused_matrix()
            cells[0] = cell
            with self.subTest(section=section, key=key), self.assertRaises(ValueError):
                q.summarize(self.plan, cells, self.root)
        cell = copy.deepcopy(original)
        cell["label_byte_hex"] = "00"
        with self.assertRaisesRegex(ValueError, "label"):
            q.pair(cell, original, self.root)

    def test_missing_raw_and_worker_bindings_cannot_pass(self):
        cell = self.cell()
        self.assertEqual(q.pair(cell, cell, self.root)["status"], "PASS")
        directory = self.root / cell["directory"]
        path = directory / "result.json"
        original = json.loads(path.read_text())
        for key, value in (
            ("source_sha256", {}),
            ("execution_id", "wrong"),
            ("plan_sha256", "wrong"),
            ("runner_sha256", "wrong"),
        ):
            changed = dict(original, **{key: value})
            q.write_json(path, changed)
            with self.subTest(key=key), self.assertRaises(ValueError):
                q.check_sentinel(cell, self.expected(cell), self.root)
        self.receipt(cell)
        (directory / cell["artifacts"]["audio"]["file"]).unlink()
        with self.assertRaises(FileNotFoundError):
            q.pair(cell, cell, self.root)

    def test_synchronized_receipts_still_require_source_and_profile_bindings(self):
        cell = self.cell()
        directory = self.root / cell["directory"]
        for field in (
            "source_sha256",
            "runner_sha256",
            "math_environment",
            "thread_environment",
        ):
            self.receipt(cell)
            worker = json.loads((directory / "result.json").read_text())
            if field in ("math_environment", "thread_environment"):
                worker["runtime"][field] = {}
            else:
                worker[field] = "wrong"
            q.write_json(directory / "result.json", worker)
            q.write_json(directory / "stdout.json", worker)
            receipt = json.loads((directory / "execution.json").read_text())
            receipt["stdout_sha256"] = q.sha256(
                (directory / "stdout.json").read_bytes()
            )
            q.write_json(directory / "execution.json", receipt)
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ValueError, "source/runtime/process binding"),
            ):
                q.check_sentinel(cell, self.expected(cell), self.root)

    def test_real_short_audio_cannot_hide_behind_declared_count(self):
        cell = self.cell()
        data = struct.pack("<78f", *([0.25] * 78))
        cell["artifacts"]["audio"]["sha256"] = q.sha256(data)
        (
            self.root / cell["directory"] / cell["artifacts"]["audio"]["file"]
        ).write_bytes(data)
        self.receipt(cell)
        with self.assertRaisesRegex(ValueError, "sample count mismatch"):
            q.check_sentinel(cell, self.expected(cell), self.root)

    def test_absent_cells_keep_comparison_denominator_without_pass(self):
        comparisons = q.summarize(
            self.plan, self.refused_matrix(), self.root / "absent"
        )
        self.assertEqual(len(comparisons), 144)
        self.assertEqual({c["status"] for c in comparisons}, {"NO_VERDICT"})

    def test_single_missing_seam_refused_on_either_side(self):
        original = self.cell()
        for name in self.plan["artifacts"]:
            cell = copy.deepcopy(original)
            del cell["artifacts"][name]
            for left, right in ((cell, original), (original, cell)):
                with (
                    self.subTest(name=name),
                    self.assertRaisesRegex(ValueError, "required artifact set"),
                ):
                    q.pair(left, right, self.root)

    def test_repeat_cannot_reuse_execution_identity(self):
        cells = self.refused_matrix()
        first = self.cell()
        second = copy.deepcopy(first)
        second["repeat"] = 2
        second["directory"] = "release-32-2"
        cells[0], cells[8] = first, second
        with self.assertRaisesRegex(ValueError, "reused an execution ID"):
            q.validate_results(self.plan, cells)

    def test_sentinel_cannot_skip_raw_verification(self):
        cell = self.cell()
        with self.assertRaisesRegex(ValueError, "requires verified raw files"):
            q.check_sentinel(cell, self.expected(cell), None)

    def test_profile_is_explicit_before_worker_launch(self):
        from argparse import Namespace
        from unittest.mock import patch

        args = Namespace(
            mode="matrix",
            image="sha256:test",
            current_python="python",
            source_root=Path("source"),
        )
        command = q.command_for(args, "release", 32, 1, self.root, "test")
        self.assertIn("MKL_CBWR=COMPATIBLE", command)
        self.assertEqual(
            command[command.index("sha256:test") + 1 :][:3],
            ["-u", "ATEN_CPU_CAPABILITY", "python"],
        )
        with (
            patch.dict(
                q.os.environ,
                {"MKL_CBWR": "surprise", "ATEN_CPU_CAPABILITY": "surprise"},
            ),
            patch.object(q.subprocess, "run") as run,
        ):
            q.invoke(["python"], profile=self.plan["profile_environment"]["current"])
            environment = run.call_args.kwargs["env"]
            self.assertNotIn("MKL_CBWR", environment)
            self.assertNotIn("ATEN_CPU_CAPABILITY", environment)

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
