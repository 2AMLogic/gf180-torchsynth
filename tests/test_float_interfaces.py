"""Offline float-interface contract tests with stdlib doubles (no Torch)."""

import copy
import hashlib
import json
import struct
import subprocess
import sys
import unittest
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from torchsynth_voice import float_interfaces as fi  # noqa: E402
from torchsynth_voice import trace_registry as registry  # noqa: E402
from torchsynth_voice.identity import SoundIdentity  # noqa: E402

MODULE_SET = {
    "keyboard",
    "lfo_1_rate_adsr",
    "lfo_2_rate_adsr",
    "lfo_1_amp_adsr",
    "lfo_2_amp_adsr",
    "lfo_1",
    "control_vca",
    "lfo_2",
    "adsr_1",
    "adsr_2",
    "mod_matrix",
    "control_upsample",
    "vco_1",
    "vca",
    "vco_2",
    "noise",
    "normalize_if_clipping",
    "mixer",
}


def f32(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


def noise_samples(fill=0.25):
    return struct.pack("<f", fill) * fi.AUDIO_SAMPLES


def make_noise(slot=0, fill=0.25):
    samples = noise_samples(fill)
    return {
        "seed": fi.NOISE_SEED,
        "slot": slot,
        "sample_count": fi.AUDIO_SAMPLES,
        "sha256": hashlib.sha256(samples).hexdigest(),
        "samples": samples,
    }


def make_normalized(value=0.5):
    encoded = f32(value)
    return {name: encoded for name in fi.inventory_names()}


def make_request(**overrides):
    arguments = {
        "sound_index": SoundIdentity(0),
        "normalized": make_normalized(),
        "noise": make_noise(slot=0),
        "execution_status": "canonical-batched",
    }
    arguments.update(overrides)
    return fi.ResolvedRequest(**arguments)


class CheckpointMapTests(unittest.TestCase):
    def setUp(self):
        self.document = fi.load_checkpoints()
        self.registry = registry.load_registry()

    def test_map_binds_landed_fingerprints(self):
        self.assertEqual(self.document["registry_token"], registry.registry_token())
        self.assertEqual(self.document["inventory_sha256"], fi.inventory_sha256())
        self.assertEqual(self.document["numeric_contract"], "unbound:#53")
        self.assertEqual(self.document["source_commit"], fi.SOURCE_COMMIT)

    def test_complete_module_and_shared_instance_coverage(self):
        interfaces = fi.module_interfaces(self.registry)
        self.assertEqual(len(interfaces), 27)
        self.assertEqual({interface.module for interface in interfaces}, MODULE_SET)
        occurrences = {}
        for interface in interfaces:
            occurrences.setdefault(interface.module, []).append(interface.occurrence)
        self.assertEqual(occurrences["control_vca"], [1, 2])
        self.assertEqual(occurrences["control_upsample"], [1, 2, 3, 4, 5])
        self.assertEqual(occurrences["vca"], [1, 2, 3])
        self.assertEqual(self.document["shared_instances"], fi.SHARED_INSTANCES)

    def test_matrix_and_mixer_argument_roles(self):
        by_module = {}
        for entry in self.document["checkpoints"]:
            by_module.setdefault(entry["module"], []).append(entry)
        matrix = by_module["mod_matrix"][0]
        self.assertEqual(
            [binding["source"] for binding in matrix["inputs"]],
            [
                "adsr_1.output",
                "adsr_2.output",
                "lfo_1.post_control_vca",
                "lfo_2.post_control_vca",
            ],
        )
        self.assertEqual(
            matrix["roles"],
            [
                "main-envelope-1",
                "main-envelope-2",
                "modulated-lfo-1",
                "modulated-lfo-2",
            ],
        )
        mixer = by_module["mixer"][0]
        self.assertEqual(
            [binding["source"] for binding in mixer["inputs"]],
            ["vco_1.post_vca", "vco_2.post_vca", "noise.post_vca"],
        )
        for entry in self.document["checkpoints"]:
            self.assertEqual(len(entry["roles"]), len(entry["inputs"]))

    def test_stale_fingerprints_fail(self):
        stale = copy.deepcopy(self.document)
        stale["registry_token"] = "tr1-" + "0" * 64
        with self.assertRaisesRegex(fi.InterfaceError, "stale checkpoint map"):
            fi.validate_checkpoints(stale)
        stale_inventory = copy.deepcopy(self.document)
        stale_inventory["inventory_sha256"] = "0" * 64
        with self.assertRaisesRegex(fi.InterfaceError, "inventory fingerprint"):
            fi.validate_checkpoints(stale_inventory)
        stale_schema = copy.deepcopy(self.document)
        stale_schema["schema_sha256"] = "0" * 64
        with self.assertRaisesRegex(fi.InterfaceError, "schema identity mismatch"):
            fi.validate_checkpoints(stale_schema)

    def test_changed_registry_content_fails_coverage(self):
        mutated = copy.deepcopy(self.registry)
        mutated["traces"][0]["name"] = "keyboard.midi_f1"
        with self.assertRaisesRegex(fi.InterfaceError, "one-to-one"):
            fi.validate_checkpoints(self.document, mutated)

    def test_swapped_checkpoint_entries_fail(self):
        swapped = copy.deepcopy(self.document)
        positions = [
            index
            for index, entry in enumerate(swapped["checkpoints"])
            if entry["module"] == "control_vca"
        ]
        swapped["checkpoints"][positions[0]], swapped["checkpoints"][positions[1]] = (
            swapped["checkpoints"][positions[1]],
            swapped["checkpoints"][positions[0]],
        )
        with self.assertRaisesRegex(fi.InterfaceError, "contiguous 1..27 sequence"):
            fi.validate_checkpoints(swapped)

    def test_bogus_occurrence_fails(self):
        bogus = copy.deepcopy(self.document)
        last_upsample = [
            entry
            for entry in bogus["checkpoints"]
            if entry["module"] == "control_upsample"
        ][-1]
        last_upsample["occurrence"] = 6
        with self.assertRaisesRegex(fi.InterfaceError, "occurrence mismatch"):
            fi.validate_checkpoints(bogus)

    def test_only_gain_is_derived(self):
        traces = {trace["name"]: trace for trace in self.registry["traces"]}
        for name, trace in traces.items():
            expected = "derived" if name == "mixer.gain" else "observed"
            self.assertEqual(trace["observation"], expected, name)
        gain = [
            entry
            for entry in self.document["checkpoints"]
            if entry["outputs"] == ["mixer.gain"]
        ][0]
        self.assertEqual(gain["mechanism"], "derived-from-observed-peak")


class ResolvedRequestTests(unittest.TestCase):
    def test_accepts_resolved_request(self):
        request = make_request(
            physical={name: f32(1.0) for name in fi.inventory_names()},
        )
        self.assertEqual(request.identity.sound_index, 0)
        self.assertEqual(request.noise["slot"], request.identity.noise_slot)
        self.assertIsNone(
            fi.ResolvedRequest(
                3,
                make_normalized(),
                make_noise(slot=3),
                execution_status="diagnostic-batch-1",
            ).physical
        )
        policy = fi.CalculationPolicy("binary32", "pinned-order", "1")
        self.assertEqual(policy.describe()["calculation_dtype"], "binary32")

    def test_positional_and_name_set_failures(self):
        names = fi.inventory_names()
        with self.assertRaisesRegex(fi.InterfaceError, "positional"):
            make_request(normalized=[0.5] * 78)
        missing = dict(make_normalized())
        del missing[names[0]]
        with self.assertRaisesRegex(fi.InterfaceError, "missing=" + names[0]):
            make_request(normalized=missing)
        extra = dict(make_normalized())
        extra["unknown.parameter"] = 0.5
        with self.assertRaisesRegex(fi.InterfaceError, "extra=unknown.parameter"):
            make_request(normalized=extra)
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            fi.loads(b'{"a": 1, "a": 2}')

    def test_value_domain_failures(self):
        cases = {
            "bool": True,
            "nonfinite-nan": float("nan"),
            "nonfinite-inf": float("inf"),
            "above-domain": 1.5,
            "below-domain": -0.25,
            "not-binary32": 1.0 / 3.0,
        }
        for label, value in cases.items():
            with self.assertRaises(fi.InterfaceError, msg=label):
                bad = make_normalized()
                bad["adsr_1.alpha"] = value
                make_request(normalized=bad)

    def test_noise_declaration_failures(self):
        with self.assertRaisesRegex(fi.InterfaceError, "seed 13"):
            make_request(noise=make_noise(slot=0) | {"seed": 7})
        with self.assertRaisesRegex(fi.InterfaceError, "slot"):
            make_request(
                sound_index=SoundIdentity(6),
                noise=make_noise(slot=0),
                execution_status="diagnostic-batch-1",
            )
        with self.assertRaisesRegex(fi.InterfaceError, "slot"):
            make_request(noise=make_noise(slot=32))
        wrong_count = make_noise(slot=0)
        wrong_count["sample_count"] = 176399
        with self.assertRaisesRegex(fi.InterfaceError, "176400"):
            make_request(noise=wrong_count)
        bad_digest = make_noise(slot=0)
        bad_digest["sha256"] = "0" * 64
        with self.assertRaisesRegex(fi.InterfaceError, "digest"):
            make_request(noise=bad_digest)
        short = make_noise(slot=0)
        short["samples"] = short["samples"][:-4]
        with self.assertRaisesRegex(fi.InterfaceError, "176400"):
            make_request(noise=short)
        nonfinite = struct.pack("<f", float("nan")) + noise_samples()[4:]
        corrupted = {
            "seed": 13,
            "slot": 0,
            "sample_count": fi.AUDIO_SAMPLES,
            "sha256": hashlib.sha256(nonfinite).hexdigest(),
            "samples": nonfinite,
        }
        with self.assertRaisesRegex(fi.InterfaceError, "finite binary32"):
            make_request(noise=corrupted)

    def test_provenance_failures(self):
        for field, value in (
            ("source_commit", "deadbeef"),
            ("profile", "drum-nebula"),
            ("runtime", "uncontrolled-baseline"),
            ("execution_status", None),
            ("execution_status", "batch-1"),
        ):
            with self.assertRaises(fi.InterfaceError, msg=field):
                overrides = {field: value}
                make_request(**overrides)

    def test_physical_map_preserved_separately(self):
        physical = {name: 63.5 for name in fi.inventory_names()}
        caller_normalized = make_normalized()
        request = make_request(normalized=caller_normalized, physical=dict(physical))
        self.assertEqual(request.physical["keyboard.midi_f0"], 63.5)
        self.assertIsNot(request.physical, physical)
        physical["keyboard.midi_f0"] = 0.0
        self.assertEqual(request.physical["keyboard.midi_f0"], 63.5)
        self.assertIsNot(request.normalized, caller_normalized)
        caller_normalized["adsr_1.alpha"] = 0.0
        self.assertEqual(request.normalized["adsr_1.alpha"], f32(0.5))
        short = dict(physical)
        del short["adsr_1.alpha"]
        with self.assertRaises(fi.InterfaceError):
            make_request(physical=short)
        with self.assertRaises(fi.InterfaceError):
            make_request(physical={"unknown.parameter": 1.0})

    def test_reset_replay_leaves_request_unchanged(self):
        request = make_request()
        snapshot = (
            dict(request.normalized),
            dict(request.noise),
            request.execution_status,
        )
        log_one = render_with_doubles(request)
        log_two = render_with_doubles(request)
        self.assertEqual(log_one, log_two)
        self.assertEqual(
            snapshot,
            (request.normalized, request.noise, request.execution_status),
        )


class BufferAndTimeTests(unittest.TestCase):
    def setUp(self):
        self.registry = registry.load_registry()

    def trace(self, name):
        return next(t for t in self.registry["traces"] if t["name"] == name)

    def test_scalar_facts_have_no_invented_rate(self):
        for name in ("keyboard.midi_f0", "keyboard.duration"):
            spec = fi.BufferSpec.from_trace(self.trace(name))
            self.assertIsNone(spec.rate_hz)
            self.assertIsNone(spec.sample_count)
            self.assertEqual(spec.shape, [])
        peak = fi.BufferSpec.from_trace(self.trace("mixer.peak"))
        self.assertEqual(peak.shape, [1])
        with self.assertRaisesRegex(fi.InterfaceError, "no invented sampling rate"):
            fi.BufferSpec("made.up", "scalar", "unit", 441, 1764, [1764], None)
        with self.assertRaisesRegex(fi.InterfaceError, "rate mismatch"):
            fi.BufferSpec(
                "adsr_1.output",
                "control",
                "linear-amplitude",
                44100,
                1764,
                [1764],
                self.trace("adsr_1.output")["time"],
            )

    def test_buffer_shape_and_time_grid_failures(self):
        adsr = self.trace("adsr_1.output")
        with self.assertRaisesRegex(fi.InterfaceError, "shape mismatch"):
            fi.BufferSpec(
                "adsr_1.output",
                "control",
                "linear-amplitude",
                441,
                1764,
                [176401],
                adsr["time"],
            )
        padded = copy.deepcopy(adsr)
        padded["time"]["last"] = [1764, 441]
        with self.assertRaisesRegex(fi.InterfaceError, "time grid"):
            fi.BufferSpec(
                "adsr_1.output",
                "control",
                "linear-amplitude",
                441,
                1764,
                [1764],
                padded["time"],
            )
        dropped = copy.deepcopy(adsr)
        dropped["time"]["last"] = [1762, 441]
        with self.assertRaisesRegex(fi.InterfaceError, "time grid"):
            fi.BufferSpec(
                "adsr_1.output",
                "control",
                "linear-amplitude",
                441,
                1764,
                [1764],
                dropped["time"],
            )

    def test_declared_dtype_and_encoding(self):
        spec = fi.BufferSpec.from_trace(self.trace("vco_1.raw"))
        described = spec.describe()
        self.assertEqual(described["dtype"], "float32")
        self.assertEqual(described["encoding"], "f32le")
        wrong = copy.deepcopy(self.trace("vco_1.raw"))
        wrong["dtype"] = "float64"
        with self.assertRaisesRegex(fi.InterfaceError, "float32"):
            fi.BufferSpec.from_trace(wrong)

    def test_time_origins_and_endpoints(self):
        self.assertEqual(fi.sample_time(0, 441), 0)
        self.assertEqual(fi.sample_time(1763, 441), Fraction(1763, 441))
        self.assertEqual(fi.sample_time(176399, 44100), Fraction(176399, 44100))
        self.assertLess(float(fi.sample_time(176399, 44100)), 4.0)
        for args in ((True, 441), (-1, 441), (0, 1), (0.5, 44100)):
            with self.assertRaises(fi.InterfaceError, msg=str(args)):
                fi.sample_time(*args)

    def test_endpoint_source_coordinates(self):
        self.assertEqual(fi.endpoint_source_coordinate(0), 0)
        self.assertEqual(fi.endpoint_source_coordinate(176399), 1763)
        self.assertEqual(fi.endpoint_source_coordinate(1), Fraction(1763, 176399))
        self.assertEqual(
            fi.endpoint_source_coordinate(880), Fraction(880 * 1763, 176399)
        )
        for bad in (True, -1, 176400, 0.5):
            with self.assertRaises(fi.InterfaceError, msg=str(bad)):
                fi.endpoint_source_coordinate(bad)


class CallLogDoubleTests(unittest.TestCase):
    def setUp(self):
        self.registry = registry.load_registry()
        self.document = fi.load_checkpoints()

    def ordered_calls(self, document):
        """Drive the 24 graph call sites from the map; identity-bound."""

        values = {}
        tracker = registry.CallTracker()
        for entry in document["checkpoints"]:
            if entry["module"] == "normalize_if_clipping":
                continue
            inputs = [values[binding["source"]] for binding in entry["inputs"]]
            outputs = [object() for _ in entry["outputs"]]
            tracker.observe(entry["module"], inputs, outputs)
            values.update(zip(entry["outputs"], outputs))
        tracker.finish()
        return values

    def test_full_ordered_call_log_with_identity_binding(self):
        values = self.ordered_calls(self.document)
        expected_traces = {
            t["name"]
            for t in self.registry["traces"]
            if not t["name"].startswith("mixer.") or t["name"] == "mixer.output"
        }
        self.assertEqual(set(values), expected_traces)
        normalization_orders = {
            entry["outputs"][0]: entry["order"]
            for entry in self.document["checkpoints"]
            if entry["module"] == "normalize_if_clipping"
        }
        self.assertEqual(
            set(normalization_orders),
            {"mixer.pre_normalization", "mixer.peak", "mixer.gain"},
        )
        vca_noise_order = [
            entry["order"]
            for entry in self.document["checkpoints"]
            if entry["outputs"] == ["noise.post_vca"]
        ][0]
        mixer_order = [
            entry["order"]
            for entry in self.document["checkpoints"]
            if entry["outputs"] == ["mixer.output"]
        ][0]
        for order in normalization_orders.values():
            self.assertGreater(order, vca_noise_order)
            self.assertLess(order, mixer_order)

    def test_swapped_equal_shaped_arguments_fail(self):
        swapped = copy.deepcopy(self.document)
        vca = [
            entry
            for entry in swapped["checkpoints"]
            if entry["module"] == "control_vca" and entry["occurrence"] == 1
        ][0]
        self.assertEqual(vca["inputs"][1]["source"], "lfo_1_amp_adsr.output")
        vca["inputs"][1]["source"] = "lfo_2_amp_adsr.output"
        with self.assertRaisesRegex(ValueError, "argument association mismatch"):
            self.ordered_calls(swapped)
        swapped = copy.deepcopy(self.document)
        matrix = [
            entry for entry in swapped["checkpoints"] if entry["module"] == "mod_matrix"
        ][0]
        matrix["inputs"][0]["source"], matrix["inputs"][1]["source"] = (
            matrix["inputs"][1]["source"],
            matrix["inputs"][0]["source"],
        )
        with self.assertRaisesRegex(ValueError, "argument association mismatch"):
            self.ordered_calls(swapped)

    def test_stateful_doubles_reset_and_leak_detection(self):
        class EnvelopeDouble:
            def __init__(self):
                self.calls = 0
                self.state = {}

            def forward(self, duration):
                self.calls += 1
                self.state["last_duration"] = duration
                return object()

            def reset(self):
                self.calls = 0
                self.state = {}

        request = make_request()
        duration = request.normalized["keyboard.duration"]
        reset_double = EnvelopeDouble()
        first = reset_double.forward(duration)
        self.assertEqual(reset_double.calls, 1)
        reset_double.reset()
        self.assertEqual(reset_double.calls, 0)
        self.assertEqual(reset_double.state, {})
        second = reset_double.forward(duration)
        self.assertIsNot(first, second)
        self.assertEqual(reset_double.calls, 1)
        unreset_double = EnvelopeDouble()
        unreset_double.forward(duration)
        unreset_double.forward(duration)
        self.assertEqual(unreset_double.calls, 2)
        self.assertEqual(reset_double.calls, 1)

    def test_noise_consumer_returns_resolved_bytes_only(self):
        request = make_request()
        noise = request.noise["samples"]
        request_two = make_request()
        self.assertEqual(noise, request_two.noise["samples"])
        self.assertEqual(hashlib.sha256(noise).hexdigest(), request.noise["sha256"])


class ClassificationTests(unittest.TestCase):
    def setUp(self):
        self.registry = registry.load_registry()
        self.document = fi.load_checkpoints()

    def test_every_boundary_has_a_class(self):
        for trace in self.registry["traces"]:
            expected = (
                "derived-diagnostic"
                if trace["name"] == "mixer.gain"
                else "declared-metrics"
            )
            self.assertEqual(
                fi.comparison_class(trace["name"], self.registry), expected
            )
        self.assertEqual(fi.comparison_class("input.normalized"), "exact-bytes")
        self.assertEqual(fi.comparison_class("input.noise"), "exact-bytes")
        self.assertEqual(
            fi.comparison_class("physical.parameters"), "measured-observation"
        )
        with self.assertRaises(fi.InterfaceError):
            fi.comparison_class("unknown.trace", self.registry)

    def test_division_versus_reciprocal_distinction(self):
        gain = next(
            trace for trace in self.registry["traces"] if trace["name"] == "mixer.gain"
        )
        self.assertEqual(gain["boundary"], "derived-reciprocal-not-applied")
        self.assertIn("divides", gain["meaning"])
        self.assertIn("peak > 1", gain["meaning"])
        consumers = gain["consumers"]
        self.assertEqual(consumers, ["diagnostics-only"])

    def test_numeric_contract_stays_unbound(self):
        self.assertEqual(self.document["numeric_contract"], "unbound:#53")
        sneaky = copy.deepcopy(self.document)
        sneaky["q_format"] = "Q2.21"
        with self.assertRaisesRegex(ValueError, "unexpected field"):
            fi.validate_checkpoints(sneaky)


class IndependenceTests(unittest.TestCase):
    def test_module_imports_and_validates_without_optional_packages(self):
        code = (
            "import sys, pathlib;"
            "sys.path.insert(0, str(pathlib.Path('src').resolve()));"
            "from torchsynth_voice import float_interfaces as fi;"
            "fi.load_checkpoints();"
            "fi.module_interfaces();"
            "assert 'numpy' not in sys.modules and 'torch' not in sys.modules"
        )
        subprocess.run([sys.executable, "-S", "-c", code], check=True, cwd=ROOT)

    def test_schema_and_api_parity(self):
        schema = json.loads(
            (ROOT / "spec/schemas/float-interface-v1.schema.json").read_text()
        )
        self.assertEqual(schema["properties"]["checkpoints"]["minItems"], 27)
        self.assertEqual(schema["properties"]["resolved_inputs"]["maxItems"], 3)
        shared = schema["properties"]["shared_instances"]["properties"]
        self.assertEqual(
            shared["control_vca"]["const"], fi.SHARED_INSTANCES["control_vca"]
        )
        self.assertEqual(
            shared["control_upsample"]["const"], fi.SHARED_INSTANCES["control_upsample"]
        )
        self.assertEqual(shared["vca"]["const"], fi.SHARED_INSTANCES["vca"])
        document = fi.load_checkpoints()
        by_name = {entry["name"]: entry for entry in document["resolved_inputs"]}
        self.assertEqual(by_name["input.normalized"]["count"], fi.PARAMETER_COUNT)
        self.assertEqual(by_name["input.noise"]["count"], fi.AUDIO_SAMPLES)
        self.assertEqual(len(fi.inventory_names()), fi.PARAMETER_COUNT)


def render_with_doubles(request):
    """Read-only render over interface doubles; returns an ordered call log."""

    log = []
    for interface in fi.module_interfaces():
        names = [port.name for port in interface.inputs]
        log.append((interface.module, interface.occurrence, tuple(names)))
    log.append(
        ("noise-bytes", hashlib.sha256(request.noise["samples"]).hexdigest(), ())
    )
    return log


if __name__ == "__main__":
    unittest.main()
