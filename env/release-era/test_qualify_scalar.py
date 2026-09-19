"""Stdlib controls for the scalar probe; real renders live in qualify_scalar.sh."""

import copy
import json
import struct
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import qualify_scalar as probe


class AggregateRefusalTests(unittest.TestCase):
    """Exercise the public aggregator with complete, synthetic raw artifacts."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plan, self.cases = probe.load_plan(True)
        self.campaign = str(uuid.uuid4())
        (self.root / "campaign.id").write_text(self.campaign)
        for name in ("image.id", "docker-server.txt", "build.log"):
            (self.root / name).write_text("synthetic unit fixture")
        manifest = [("input.normalized", [78]), ("input.noise", [176400])]
        self.addCleanup(patch.stopall)
        patch.object(probe, "capture_manifest", return_value=manifest).start()
        names = sorted(
            p["name"]
            for p in json.loads(
                (probe.REPO / "spec/reference/parameter-inventory-v1.json").read_text()
            )["parameters"]
        )
        values = dict.fromkeys(names, 0.25)
        for repeat in (1, 2):
            for side in ("canonical", "scalar"):
                path = self.root / f"run-{repeat}" / side
                path.mkdir(parents=True)
                execution = {
                    "uuid": str(uuid.uuid4()),
                    "pid": 1,
                    "started_utc": "2026-09-19T00:00:00+00:00",
                    "campaign_id": self.campaign,
                    "repeat": repeat,
                }
                report = {
                    "status": "PASS",
                    "side": side,
                    "mutation": None,
                    "execution": execution,
                    "provenance": {"definition_sha256": {}},
                    "runtime": {"name": "synthetic"},
                    "cases": [],
                    "capture_version": self.plan["capture_version"],
                    "rng_sentinel": "PASS",
                    "source_unchanged_after_render": True,
                    "warnings": [],
                    "stdout": "",
                    "stderr": "",
                }
                if side == "scalar":
                    canonical = path.parent / "canonical" / "report.json"
                    report["canonical_execution_uuid"] = json.loads(
                        canonical.read_text()
                    )["execution"]["uuid"]
                    report["canonical_report_sha256"] = probe.sha256(
                        canonical.read_bytes()
                    )
                for case in self.cases:
                    records = []
                    for name, shape in manifest:
                        data = struct.pack("<f", 0.25) * shape[0]
                        filename = case["id"] + "." + name + ".f32le"
                        (path / filename).write_bytes(data)
                        records.append(
                            {
                                "name": name,
                                "shape": shape,
                                "file": filename,
                                "sha256": probe.sha256(data),
                            }
                        )
                    report["cases"].append(
                        {
                            "id": case["id"],
                            "case_definition": case,
                            "corpus_coordinates": probe.identity(case["sound_index"]),
                            "configuration": self.plan["configuration"],
                            "execution_width": 32 if side == "canonical" else 1,
                            "reproducible": side == "canonical",
                            "normalized_by_name": values,
                            "parameter_order": names,
                            "traces": records,
                        }
                    )
                self.write(path / "report.json", report)
        canonical_path = self.root / "run-1/canonical/report.json"
        canonical = json.loads(canonical_path.read_text())
        case = next(c for c in canonical["cases"] if c["id"] == "global-6")
        for mutation in self.plan["controls"]:
            path = self.root / "controls" / mutation
            path.mkdir(parents=True)
            actual = dict(values)
            if mutation == "wrong-parameter":
                actual["keyboard.midi_f0"] = 0.0
            elif mutation == "fresh-randomization":
                actual = dict.fromkeys(names, 0.5)
            noise = (
                struct.pack("<f", 0.5 if mutation == "wrong-noise" else 0.25) * 176400
            )
            (path / "actual-noise.f32le").write_bytes(noise)
            report = {
                "status": "NO_VERDICT",
                "side": "scalar",
                "mutation": mutation,
                "error": "ValueError: "
                + self.plan["controls"][mutation]["expected_seam"]
                + ": synthetic refusal",
                "execution": dict(canonical["execution"], uuid=str(uuid.uuid4())),
                "canonical_execution_uuid": canonical["execution"]["uuid"],
                "canonical_report_sha256": probe.sha256(canonical_path.read_bytes()),
                "control_observation": {
                    "case": "global-6",
                    "runtime": canonical["runtime"],
                    "provenance": canonical["provenance"],
                    "expected_normalized": values,
                    "actual_normalized": actual,
                    "expected_noise_sha256": case["traces"][1]["sha256"],
                    "actual_noise_sha256": probe.sha256(noise),
                    "actual_noise": {
                        "file": "actual-noise.f32le",
                        "shape": [176400],
                        "sha256": probe.sha256(noise),
                    },
                    "expected_noise_slot": 6,
                    "actual_noise_slot": 0 if mutation == "wrong-noise" else 6,
                },
            }
            self.write(path / "report.json", report)

    def write(self, path, report):
        path.write_text(json.dumps(report))

    def mutate(self, relative, change):
        path = self.root / relative / "report.json"
        report = json.loads(path.read_text())
        change(report)
        self.write(path, report)

    def test_complete_fixture_aggregates(self):
        probe.aggregate(self.root, True)

    def test_canonical_cannot_impersonate_scalar(self):
        path = self.root / "run-1/canonical"
        with self.assertRaisesRegex(ValueError, "role"):
            probe.compare_run(path, path)

    def test_wrong_width_and_reproducibility_refuse_through_aggregate(self):
        for key, value in (("execution_width", 32), ("reproducible", True)):
            path = self.root / "run-1/scalar/report.json"
            original = json.loads(path.read_text())
            self.mutate("run-1/scalar", lambda r: r["cases"][0].update({key: value}))
            with self.assertRaisesRegex(ValueError, "configuration"):
                probe.aggregate(self.root, True)
            self.write(path, original)

    def test_reused_pair_refuses_through_aggregate(self):
        for side in ("canonical", "scalar"):
            self.write(
                self.root / "run-2" / side / "report.json",
                json.loads((self.root / "run-1" / side / "report.json").read_text()),
            )
        with self.assertRaisesRegex(ValueError, "repeat|reused"):
            probe.aggregate(self.root, True)

    def test_reused_identity_refuses_even_with_distinct_repeat(self):
        first = json.loads((self.root / "run-1/canonical/report.json").read_text())
        self.mutate(
            "run-2/scalar",
            lambda r: r["execution"].update(uuid=first["execution"]["uuid"]),
        )
        with self.assertRaisesRegex(ValueError, "reused"):
            probe.aggregate(self.root, True)

    def test_unassociated_scalar_refuses(self):
        self.mutate(
            "run-1/scalar",
            lambda r: r.update(canonical_execution_uuid=str(uuid.uuid4())),
        )
        with self.assertRaisesRegex(ValueError, "association"):
            probe.aggregate(self.root, True)

    def test_missing_identity_refuses(self):
        self.mutate("run-1/canonical", lambda r: r.pop("execution"))
        with self.assertRaisesRegex(ValueError, "execution identity"):
            probe.aggregate(self.root, True)

    def test_reused_control_process_refuses(self):
        first = json.loads((self.root / "run-1/scalar/report.json").read_text())
        self.mutate(
            "controls/wrong-parameter", lambda r: r.update(execution=first["execution"])
        )
        with self.assertRaisesRegex(ValueError, "reused control"):
            probe.aggregate(self.root, True)

    def test_controls_cannot_swap_independent_faults(self):
        for name in ("wrong-noise", "fresh-randomization"):
            path = self.root / "controls" / name / "report.json"
            original = json.loads(path.read_text())
            wrong = json.loads(
                (self.root / "controls/wrong-parameter/report.json").read_text()
            )
            self.mutate(
                "controls/" + name,
                lambda r: r.update(control_observation=wrong["control_observation"]),
            )
            with self.assertRaises(ValueError):
                probe.aggregate(self.root, True)
            self.write(path, original)

    def test_controls_must_retain_provenance_runtime_and_fault(self):
        relative = "controls/wrong-parameter"
        path = self.root / relative / "report.json"
        original = json.loads(path.read_text())
        mutations = [
            lambda r: r["control_observation"].update(
                provenance={"source_commit": "stale"}
            ),
            lambda r: r["control_observation"].update(
                runtime={"name": "unrun-runtime"}
            ),
            lambda r: r["control_observation"].update(case="global-0"),
            lambda r: r.update(mutation="wrong-noise"),
            lambda r: r.update(canonical_report_sha256="0" * 64),
            lambda r: r["execution"].update(repeat=2),
            lambda r: r["control_observation"].update(
                actual_normalized=r["control_observation"]["expected_normalized"]
            ),
        ]
        for change in mutations:
            self.mutate(relative, change)
            with self.assertRaises(ValueError):
                probe.aggregate(self.root, True)
            self.write(path, original)

    def test_noise_control_requires_observed_mutation_artifact(self):
        self.mutate(
            "controls/wrong-noise",
            lambda r: r["control_observation"].update(
                actual_noise_sha256=r["control_observation"]["expected_noise_sha256"]
            ),
        )
        with self.assertRaisesRegex(ValueError, "noise"):
            probe.aggregate(self.root, True)


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
                "math_environment",
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
        changed = copy.deepcopy(report)
        changed["runtime"]["math_environment"] = {
            "MKL_CBWR": "COMPATIBLE",
            "ATEN_CPU_CAPABILITY": None,
        }
        with self.assertRaisesRegex(ValueError, "provenance"):
            probe.verify_expected(report, changed, ["unit"])

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
