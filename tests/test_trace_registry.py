"""Offline registry and fail-closed call-association controls (no Torch)."""

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from torchsynth_voice import trace_registry as registry  # noqa: E402


class TraceRegistryTests(unittest.TestCase):
    def setUp(self):
        self.document = registry.load_registry()

    def test_complete_directed_names_and_aliases(self):
        planned = json.loads(
            (ROOT / "spec/reference/directed-coverage-v1.json").read_text()
        )
        self.assertEqual(
            set(planned["traces"]), set(registry.requested_names(self.document))
        )
        aliases = self.document["aliases"]
        scalar = json.loads((ROOT / "sim/reference/scalar-execution.json").read_text())
        self.assertEqual(
            set(aliases["scalar-capture-v1"]),
            {t["name"] for t in scalar["capture_manifest"]},
        )
        matrix = json.loads(
            (ROOT / "env/release-era/repeatability-matrix.json").read_text()
        )
        self.assertEqual(set(aliases["repeatability-v1"]), set(matrix["artifacts"]))

    def test_content_identity_binds_registry_and_schema(self):
        original = registry.REGISTRY_PATH.read_bytes()
        expected = (
            "tr1-"
            + hashlib.sha256(b"torchsynth-trace-registry-v1\n" + original).hexdigest()
        )
        self.assertEqual(registry.registry_token(), expected)
        self.assertRegex(expected, r"^tr1-[0-9a-f]{64}$")
        self.assertEqual(
            self.document["schema_sha256"],
            hashlib.sha256(registry.SCHEMA_PATH.read_bytes()).hexdigest(),
        )
        changed = copy.deepcopy(self.document)
        changed["schema_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "schema identity mismatch"):
            registry.validate_registry(changed)

    def test_requested_order_is_graph_order_and_duplicates_refused(self):
        self.assertEqual(
            registry.requested_names(self.document, ["mixer.output", "adsr_1.output"]),
            ["adsr_1.output", "mixer.output"],
        )
        for names in (["unknown"], ["mixer.output", "mixer.output"]):
            with self.assertRaises(ValueError):
                registry.requested_names(self.document, names)
        names = registry.requested_names(self.document)
        self.assertLess(
            names.index("vco_1.raw"), names.index("control_upsample.vco_1_amp")
        )
        self.assertLess(
            names.index("noise.raw"), names.index("control_upsample.noise_amp")
        )

    def test_scalar_and_endpoint_metadata(self):
        by_name = {t["name"]: t for t in self.document["traces"]}
        for name in (
            "keyboard.midi_f0",
            "keyboard.duration",
            "mixer.peak",
            "mixer.gain",
        ):
            trace = by_name[name]
            self.assertEqual(trace["kind"], "scalar")
            self.assertIsNone(trace["rate_hz"])
            self.assertIsNone(trace["time"])
            self.assertIsNone(trace["sample_count"])
        self.assertEqual(by_name["mixer.peak"]["observation"], "observed")
        self.assertEqual(by_name["mixer.gain"]["observation"], "derived")
        for route in registry.ROUTES:
            trace = by_name["control_upsample." + route]
            self.assertEqual(
                trace["interpolation"]["input_coordinate"], "i*1763/176399"
            )
            self.assertEqual(trace["interpolation"]["input_last_time"], [1763, 441])
            self.assertEqual(trace["time"]["last"], [176399, 44100])
        self.assertEqual(
            sum(
                t["name"].endswith("adsr.output")
                or t["name"] in ("adsr_1.output", "adsr_2.output")
                for t in self.document["traces"]
            ),
            6,
        )

    def test_structural_negative_controls_retain_reasons(self):
        controls = registry.negative_controls(self.document)
        self.assertEqual(
            set(controls),
            {
                "missing-trace",
                "swapped-call-labels",
                "wrong-rate",
                "wrong-shape",
                "wrong-boundary",
            },
        )
        for result in controls.values():
            self.assertEqual(result["status"], "rejected")
            self.assertTrue(result["reason"])

    def test_call_tracker_accepts_exact_object_associations(self):
        tracker = registry.CallTracker()
        for module, inputs, outputs in registry.CALLS:
            args = tuple(tracker.values[n] for n in inputs)
            values = tuple(object() for _ in outputs)
            tracker.observe(module, args, values)
        tracker.finish()
        self.assertEqual(tracker.counts["control_vca"], 2)
        self.assertEqual(tracker.counts["control_upsample"], 5)
        self.assertEqual(tracker.counts["vca"], 3)

    def test_call_tracker_rejects_swapped_missing_extra_and_wrong_arguments(self):
        tracker = registry.CallTracker()
        with self.assertRaisesRegex(ValueError, "call order"):
            tracker.observe("vca", (), (object(),))
        with self.assertRaisesRegex(ValueError, "missing invocations"):
            tracker.finish()
        for module, inputs, outputs in registry.CALLS:
            args = tuple(tracker.values[n] for n in inputs)
            if module == "control_vca":
                with self.assertRaisesRegex(ValueError, "argument association"):
                    tracker.observe(module, tuple(reversed(args)), (object(),))
            tracker.observe(module, args, tuple(object() for _ in outputs))
        with self.assertRaisesRegex(ValueError, "extra invocation"):
            tracker.observe("mixer", (), (object(),))

    def test_duplicate_json_and_unknown_fields_refused(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            registry.loads('{"schema":1,"schema":2}')
        self.document["unknown"] = True
        with self.assertRaisesRegex(ValueError, "unexpected field"):
            registry.validate_registry(self.document)

    def test_observed_inventory_rejects_missing_and_wrong_shape(self):
        inventory = [
            {
                "name": t["name"],
                "shape": t["shape"],
                "batch_shape": [32 if x == "B" else x for x in t["batch_shape"]],
                "dtype": "float32",
                "rate_hz": t["rate_hz"],
                "boundary": t["boundary"],
                "sha256": "a" * 64,
            }
            for t in self.document["traces"]
        ]
        registry.validate_capture(self.document, inventory, 32)
        with self.assertRaisesRegex(ValueError, "capture inventory"):
            registry.validate_capture(self.document, inventory[:-1], 32)
        inventory[0]["shape"] = [1]
        with self.assertRaisesRegex(ValueError, "capture shape"):
            registry.validate_capture(self.document, inventory, 32)

    def test_standalone_without_site_packages(self):
        code = (
            "import importlib.util,sys; "
            f"s=importlib.util.spec_from_file_location('trace_registry',{str(registry.__file__)!r}); "
            "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
            "m.load_registry();assert not any(x in sys.modules for x in "
            "('torch','numpy','torchsynth','torchsynth_voice'))"
        )
        subprocess.run([sys.executable, "-S", "-c", code], check=True)

    def test_committed_prototype_provenance_and_inventory(self):
        """Check retained evidence bindings; this is not a fresh numerical run."""
        report = registry.loads(
            (ROOT / "sim/reference/trace-registry-prototype.json").read_bytes()
        )
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["registry_token"], registry.registry_token())
        self.assertEqual(report["schema_sha256"], self.document["schema_sha256"])
        self.assertEqual(report["runtime_profile"], "release-mkl-compatible-v1")
        self.assertEqual(
            report["runtime"]["math_environment"],
            {"MKL_CBWR": "COMPATIBLE", "ATEN_CPU_CAPABILITY": None},
        )
        self.assertEqual(report["runtime"]["threads"], 1)
        self.assertEqual(report["runtime"]["interop_threads"], 1)
        self.assertEqual(report["runtime"]["python"], "3.9.13")
        self.assertEqual(report["runtime"]["torch"], "1.12.1+cpu")
        self.assertFalse(report["project_git"]["dirty"])
        for field in ("runtime", "configuration"):
            encoded = json.dumps(
                report[field], sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
            self.assertEqual(
                report[field + "_sha256"], hashlib.sha256(encoded).hexdigest()
            )
        for path, sha in report["producer_sha256"].items():
            self.assertEqual(
                hashlib.sha256((ROOT / path).read_bytes()).hexdigest(), sha, path
            )
        registry.validate_capture(self.document, report["capture_inventory"], 32)
        self.assertEqual(
            report["negative_controls"], registry.negative_controls(self.document)
        )
        self.assertEqual(
            report["upsampling_endpoint_checks"], dict.fromkeys(registry.ROUTES, "PASS")
        )
        self.assertTrue(report["passive_capture_equal_bytes"])
        for module, count in (("control_vca", 2), ("control_upsample", 5), ("vca", 3)):
            self.assertEqual(report["invocation_counts"][module], count)

    def test_committed_parameter_bytes_and_independent_sentinel(self):
        report = registry.loads(
            (ROOT / "sim/reference/trace-registry-prototype.json").read_bytes()
        )
        sides = report["executions"]
        self.assertEqual(sides["captured"], sides["uncaptured"])
        for execution in sides.values():
            self.assertEqual(execution["before"], execution["after"])
            for kind in ("normalized", "physical"):
                parameters = execution["after"][kind]
                self.assertEqual(
                    sorted(parameters["values"]), report["parameter_names"]
                )
                self.assertEqual(len(parameters["values"]), 78)
                data = b""
                for name in report["parameter_names"]:
                    encoded = struct.pack("<f", parameters["values"][name])
                    self.assertEqual(
                        encoded.hex(), parameters["bytes_hex_by_name"][name]
                    )
                    data += encoded
                self.assertEqual(
                    hashlib.sha256(data).hexdigest(), parameters["selected_sha256"]
                )
        baseline = json.loads(
            (ROOT / "sim/reference/repeatability-runtime.json").read_text()
        )
        cell = next(
            c
            for c in baseline["cells"]
            if c["runtime"] == "release"
            and c["case"] == "global-0"
            and c["batch_size"] == 32
            and c["repeat"] == 1
        )
        traces = {t["name"]: t for t in report["capture_inventory"]}
        for alias, mapping in self.document["aliases"]["repeatability-v1"].items():
            observed = (
                sides["captured"]["after"][alias]["selected_sha256"]
                if mapping["kind"] == "input-checkpoint"
                else traces[mapping["target"]]["sha256"]
            )
            self.assertEqual(observed, cell["artifacts"][alias]["sha256"], alias)
            self.assertEqual(report["previous_sentinel_comparison"][alias], "PASS")


if __name__ == "__main__":
    unittest.main()
