"""Trace capture contract tests; stdlib-only with an in-process fake graph.

These tests exercise the production ``trace_capture`` module end to end on a
fake Torch/voice double: ordering, argument-identity association, selective
enablement, cleanup, conflict refusal, inventory validation and endpoint
checks. Byte-identity of real renders is qualified separately by the
release-era worker (tools/qualify_trace_capture.py); fakes here never count as
runtime evidence.
"""

import math
import sys
import unittest
from array import array
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import trace_capture  # noqa: E402
from torchsynth_voice import trace_registry  # noqa: E402

CONTROL_COUNT = 1764
AUDIO_COUNT = 176400
BATCH = 32


class FakeDevice:
    def __init__(self, kind):
        self.type = kind


class FakeTorch:
    float32 = "float32"

    @staticmethod
    def isfinite(value):
        return FakeFinite(value._finite)

    @staticmethod
    def ones_like(value):
        result = FakeTensor.scaled(1.0, value.shape)
        result.dtype = FakeTorch.float32
        return result

    @staticmethod
    def where(condition, left, right):
        data = array(
            "f",
            [
                left.data[i] if condition.data[i] else right.data[i]
                for i in range(len(condition.data))
            ],
        )
        return FakeTensor(data, left.shape)


class FakeFinite:
    def __init__(self, value):
        self._value = value

    def all(self):
        return self._value


class FakeTensor:
    def __init__(self, data, shape):
        self.data = data
        self.shape = shape
        self.dtype = FakeTorch.float32
        self.device = FakeDevice("cpu")
        self._finite = True

    @staticmethod
    def scaled(value, shape):
        data = array("f", [value]) * int(math.prod(shape))
        return FakeTensor(data, tuple(shape))

    @staticmethod
    def from_values(values, shape):
        return FakeTensor(array("f", values), tuple(shape))

    def __len__(self):
        return self.shape[0]

    def __getitem__(self, index):
        step = int(math.prod(self.shape[1:]))
        start = index * step
        return FakeTensor(self.data[start : start + step], self.shape[1:])

    def detach(self):
        return self

    def clone(self):
        return FakeTensor(array("f", self.data), self.shape)

    @property
    def ndim(self):
        return len(self.shape)

    def numpy(self):
        return self

    def contiguous(self):
        return self

    def tobytes(self):
        return self.data.tobytes()

    def reciprocal(self):
        return FakeTensor(
            array("f", [1.0 / x for x in self.data]), self.shape
        )

    def __gt__(self, threshold):
        result = self.clone()
        result.data = array("f", [1.0 if x > threshold else 0.0 for x in self.data])
        return result

    def abs_max(self):
        if len(self.shape) <= 1:
            return FakeTensor(array("f", [max(abs(x) for x in self.data)]), (1,))
        step = int(math.prod(self.shape[1:]))
        rows = self.shape[0]
        peaks = array(
            "f",
            [
                max(abs(x) for x in self.data[row * step : (row + 1) * step])
                for row in range(rows)
            ],
        )
        return FakeTensor(peaks, (rows, 1))

    def divide_by(self, divisor):
        return FakeTensor(
            array("f", [x / divisor.data[0] for x in self.data]), self.shape
        )


def fake_normalize(signal, max_sample=None):
    if max_sample is None:
        max_sample = signal.abs_max()
    if max_sample.data[0] > 1.0:
        signal = signal.divide_by(max_sample)
    return signal


class FakeHandle:
    def __init__(self, module, key):
        self.module = module
        self.key = key

    def remove(self):
        self.module._forward_hooks.pop(self.key, None)


class FakeModule:
    def __init__(self, name, function):
        self.name = name
        self.function = function
        self._forward_hooks = {}

    def register_forward_hook(self, hook):
        key = max(self._forward_hooks, default=-1) + 1
        self._forward_hooks[key] = hook
        return FakeHandle(self, key)

    def forward(self, *inputs):
        result = self.function(*inputs)
        for hook in list(self._forward_hooks.values()):
            hook(self, inputs, result)
        return result


class FakeMixer(FakeModule):
    def output(self, signal):
        return fake_normalize(signal)

    def decoy(self, signal):
        return fake_normalize(signal)


def envelope(batch):
    return FakeTensor.scaled(0.5, (batch, CONTROL_COUNT))


def audio(batch):
    return FakeTensor.scaled(0.25, (batch, AUDIO_COUNT))


class FakeVoice:
    """The pinned 23-call graph in original order with shared modules."""

    def __init__(self, batch=BATCH, mixer=None):
        self.batch = batch
        self.mixer = mixer if mixer is not None else FakeMixer("mixer", self._mix)
        self.keyboard = FakeModule(
            "keyboard",
            lambda: (
                FakeTensor.scaled(60.0, (batch,)),
                FakeTensor.scaled(2.0, (batch,)),
            ),
        )
        self.lfo_1_rate_adsr = FakeModule("lfo_1_rate_adsr", lambda duration: envelope(batch))
        self.lfo_2_rate_adsr = FakeModule("lfo_2_rate_adsr", lambda duration: envelope(batch))
        self.lfo_1_amp_adsr = FakeModule("lfo_1_amp_adsr", lambda duration: envelope(batch))
        self.lfo_2_amp_adsr = FakeModule("lfo_2_amp_adsr", lambda duration: envelope(batch))
        self.lfo_1 = FakeModule("lfo_1", lambda rate: envelope(batch))
        self.lfo_2 = FakeModule("lfo_2", lambda rate: envelope(batch))
        self.control_vca = FakeModule(
            "control_vca", lambda raw, amp: FakeTensor.scaled(0.125, (batch, CONTROL_COUNT))
        )
        self.adsr_1 = FakeModule("adsr_1", lambda duration: envelope(batch))
        self.adsr_2 = FakeModule("adsr_2", lambda duration: envelope(batch))
        self.mod_matrix = FakeModule("mod_matrix", self._mod_matrix)
        self.control_upsample = FakeModule("control_upsample", self._upsample)
        self.vco_1 = FakeModule("vco_1", lambda midi, pitch: audio(batch))
        self.vco_2 = FakeModule("vco_2", lambda midi, pitch: audio(batch))
        self.noise = FakeModule("noise", lambda: audio(batch))
        self.vca = FakeModule("vca", self._vca)

    def _mod_matrix(self, a1, a2, l1, l2):
        return tuple(
            FakeTensor.scaled(1.0 + i, (self.batch, CONTROL_COUNT)) for i in range(5)
        )

    def _upsample(self, control):
        upsampled = FakeTensor.scaled(0.5, (self.batch, AUDIO_COUNT))
        for index in range(self.batch):
            source = index * CONTROL_COUNT
            target = index * AUDIO_COUNT
            upsampled.data[target] = control.data[source]
            upsampled.data[target + AUDIO_COUNT - 1] = control.data[
                source + CONTROL_COUNT - 1
            ]
        return upsampled

    def _vca(self, source, amp):
        return FakeTensor.scaled(0.5, (self.batch, AUDIO_COUNT))

    def _mix(self, one, two, three):
        # Below clipping: the fake graph exercises the bypass branch; the
        # division branch runs on real Torch in the release-era worker.
        return self.mixer.output(FakeTensor.scaled(0.75, (self.batch, AUDIO_COUNT)))

    def output(self, engaged=1):
        midi, duration = self.keyboard.forward()
        lfo_1_rate = self.lfo_1_rate_adsr.forward(duration)
        lfo_2_rate = self.lfo_2_rate_adsr.forward(duration)
        lfo_1_amp = self.lfo_1_amp_adsr.forward(duration)
        lfo_2_amp = self.lfo_2_amp_adsr.forward(duration)
        lfo_1_raw = self.lfo_1.forward(lfo_1_rate)
        lfo_1_vca = self.control_vca.forward(lfo_1_raw, lfo_1_amp)
        lfo_2_raw = self.lfo_2.forward(lfo_2_rate)
        lfo_2_vca = self.control_vca.forward(lfo_2_raw, lfo_2_amp)
        adsr_1 = self.adsr_1.forward(duration)
        adsr_2 = self.adsr_2.forward(duration)
        routes = self.mod_matrix.forward(adsr_1, adsr_2, lfo_1_vca, lfo_2_vca)
        vco_1_pitch = self.control_upsample.forward(routes[0])
        vco_1_raw = self.vco_1.forward(midi, vco_1_pitch)
        vco_1_amp = self.control_upsample.forward(routes[1])
        vco_1_out = self.vca.forward(vco_1_raw, vco_1_amp)
        vco_2_pitch = self.control_upsample.forward(routes[2])
        vco_2_raw = self.vco_2.forward(midi, vco_2_pitch)
        vco_2_amp = self.control_upsample.forward(routes[3])
        vco_2_out = self.vca.forward(vco_2_raw, vco_2_amp)
        noise_raw = self.noise.forward()
        noise_amp = self.control_upsample.forward(routes[4])
        noise_out = self.vca.forward(noise_raw, noise_amp)
        return self.mixer.forward(vco_1_out, vco_2_out, noise_out)


class FakeFailingVoice(FakeVoice):
    """A voice whose last module refuses hook registration (partial setup)."""

    def __init__(self, batch=BATCH):
        super().__init__(batch=batch)
        original = self.noise.register_forward_hook

        def refusing(hook):
            raise ValueError("registration refused")

        self.noise.register_forward_hook = refusing


DOCUMENT = trace_registry.load_registry()
SUBSET = ["adsr_1.output", "control_upsample.vco_1_pitch", "mixer.peak", "mixer.output"]


def hooks_attached(voice):
    return any(
        getattr(voice, name)._forward_hooks
        for name in dict.fromkeys(call[0] for call in trace_registry.CALLS)
    )


def render(voice, session=None):
    if session is None:
        return voice.output()
    with session:
        return voice.output()


class RegistrySelectionTests(unittest.TestCase):
    def test_unknown_name_rejected_before_any_observer(self):
        voice = FakeVoice()
        with self.assertRaises(ValueError):
            trace_capture.TraceCapture(
                voice, DOCUMENT, FakeTorch, fake_normalize, names=["not.a.trace"]
            )
        self.assertEqual(len(voice.keyboard._forward_hooks), 0)

    def test_duplicate_name_rejected(self):
        with self.assertRaises(ValueError):
            trace_capture.TraceCapture(
                FakeVoice(), DOCUMENT, FakeTorch, fake_normalize, names=["adsr_1.output", "adsr_1.output"]
            )

    def test_empty_selection_keeps_pure_bookkeeping(self):
        voice = FakeVoice()
        session = trace_capture.TraceCapture(
            voice, DOCUMENT, FakeTorch, fake_normalize, names=[], batch_size=BATCH
        )
        render(voice, session)
        self.assertEqual(session.inventory, [])
        self.assertEqual(
            session.tracker.counts,
            {"keyboard": 1, "control_vca": 2, "control_upsample": 5, "vca": 3}
            | {"lfo_1_rate_adsr": 1, "lfo_2_rate_adsr": 1, "lfo_1_amp_adsr": 1,
               "lfo_2_amp_adsr": 1, "lfo_1": 1, "lfo_2": 1, "adsr_1": 1,
               "adsr_2": 1, "mod_matrix": 1, "vco_1": 1, "vco_2": 1,
               "noise": 1, "mixer": 1},
        )

    def test_requested_plan_follows_graph_order(self):
        plan = trace_capture.requested_plan(
            DOCUMENT, ["mixer.output", "adsr_1.output"]
        )
        self.assertEqual(
            [trace["name"] for trace in plan], ["adsr_1.output", "mixer.output"]
        )


class CaptureLifecycleTests(unittest.TestCase):
    def capture(self, voice, names=None):
        return trace_capture.TraceCapture(
            voice, DOCUMENT, FakeTorch, fake_normalize, names=names, batch_size=BATCH
        )

    def test_full_capture_validates_and_matches_untraced_bytes(self):
        voice = FakeVoice()
        untraced = render(voice).tobytes()
        voice = FakeVoice()
        session = self.capture(voice)
        traced = render(voice, session).tobytes()
        self.assertEqual(traced, untraced)
        self.assertEqual(len(session.inventory), 32)
        trace_capture.validate_selected_capture(
            DOCUMENT, None, session.inventory, BATCH
        )
        self.assertEqual(
            [item["name"] for item in session.inventory],
            [trace["name"] for trace in DOCUMENT["traces"]],
        )

    def test_selective_subset_records_only_requested_traces(self):
        voice = FakeVoice()
        session = self.capture(voice, names=SUBSET)
        render(voice, session)
        self.assertEqual(
            [item["name"] for item in session.inventory], SUBSET
        )
        trace_capture.validate_selected_capture(
            DOCUMENT, SUBSET, session.inventory, BATCH
        )

    def test_observers_removed_after_success(self):
        voice = FakeVoice()
        session = self.capture(voice)
        render(voice, session)
        self.assertFalse(hooks_attached(voice))
        self.assertIsNone(sys.getprofile())

    def test_capture_exception_removes_all_observers(self):
        voice = FakeVoice()
        session = self.capture(voice)
        original_add = session.add

        def failing_add(name, value):
            if name == "mixer.output":
                raise ValueError("injected capture failure")
            original_add(name, value)

        session.add = failing_add
        with self.assertRaises(ValueError):
            render(voice, session)
        self.assertFalse(session.active)
        self.assertFalse(hooks_attached(voice))
        self.assertIsNone(sys.getprofile())
        # The voice itself must be reusable and unaffected for later sessions.
        followup = self.capture(voice, names=["mixer.output"])
        render(voice, followup)
        self.assertEqual(len(followup.inventory), 1)

    def test_partial_setup_failure_rolls_back(self):
        voice = FakeFailingVoice()
        session = self.capture(voice)
        with self.assertRaises(ValueError):
            session.__enter__()
        self.assertFalse(session.active)
        self.assertFalse(hooks_attached(voice))
        self.assertIsNone(sys.getprofile())

    def test_conflicting_profiler_refused_without_side_effects(self):
        voice = FakeVoice()

        def busy(*args):
            return None

        sys.setprofile(busy)
        try:
            session = self.capture(voice)
            with self.assertRaises(ValueError):
                session.__enter__()
        finally:
            sys.setprofile(None)
        self.assertFalse(session.active)
        self.assertFalse(hooks_attached(voice))

    def test_double_activation_refused(self):
        voice = FakeVoice()
        session = self.capture(voice)
        session.__enter__()
        try:
            with self.assertRaises(ValueError):
                session.__enter__()
        finally:
            # Simulate an aborted body: finish() must not be required.
            session.__exit__(ValueError, ValueError("aborted"), None)
        self.assertFalse(session.active)
        self.assertFalse(hooks_attached(voice))
        self.assertIsNone(sys.getprofile())

    def test_repeat_sessions_are_independent(self):
        first_bytes = None
        for _ in range(2):
            voice = FakeVoice()
            session = self.capture(voice)
            data = render(voice, session).tobytes()
            if first_bytes is None:
                first_bytes = data
            self.assertEqual(data, first_bytes)
            self.assertEqual(len(session.inventory), 32)


class AssociationEnforcementTests(unittest.TestCase):
    def capture(self, voice):
        return trace_capture.TraceCapture(
            voice, DOCUMENT, FakeTorch, fake_normalize, batch_size=BATCH
        )

    def test_out_of_order_call_rejected(self):
        voice = FakeVoice()
        session = self.capture(voice)
        with self.assertRaises(ValueError):
            with session:
                midi, duration = voice.keyboard.forward()
                voice.lfo_2_rate_adsr.forward(duration)
                voice.lfo_1_rate_adsr.forward(duration)

    def test_numerically_equal_but_distinct_argument_rejected(self):
        voice = FakeVoice()
        session = self.capture(voice)
        with self.assertRaises(ValueError):
            with session:
                midi, duration = voice.keyboard.forward()
                impersonator = duration.clone()
                voice.lfo_1_rate_adsr.forward(impersonator)

    def test_missing_call_rejected_at_finish(self):
        voice = FakeVoice()
        session = self.capture(voice)
        with self.assertRaises(ValueError):
            with session:
                midi, duration = voice.keyboard.forward()
                voice.adsr_1.forward(duration)
                voice.adsr_2.forward(duration)

    def test_extra_call_rejected(self):
        voice = FakeVoice()
        session = self.capture(voice)
        with self.assertRaises(ValueError):
            with session:
                midi, duration = voice.keyboard.forward()
                voice.keyboard.forward()

    def test_wrong_output_arity_rejected(self):
        voice = FakeVoice()
        voice.keyboard.function = lambda: FakeTensor.scaled(1.0, (BATCH,))
        session = self.capture(voice)
        with self.assertRaises(ValueError):
            with session:
                voice.keyboard.forward()

    def test_normalization_from_wrong_caller_is_the_wrong_clamp_site(self):
        voice = FakeVoice()
        session = self.capture(voice)
        with self.assertRaises(ValueError):
            with session:
                voice.mixer.decoy(FakeTensor.scaled(0.5, (BATCH, AUDIO_COUNT)))


class InventoryValidationTests(unittest.TestCase):
    def inventory(self):
        return [
            {
                "name": trace["name"],
                "shape": trace["shape"],
                "batch_shape": [BATCH if n == "B" else n for n in trace["batch_shape"]],
                "dtype": trace["dtype"],
                "rate_hz": trace["rate_hz"],
                "boundary": trace["boundary"],
                "sha256": "0" * 64,
            }
            for trace in DOCUMENT["traces"]
        ]

    def expect_reject(self, inventory, requested=None, batch=BATCH):
        with self.assertRaises(ValueError):
            trace_capture.validate_selected_capture(
                DOCUMENT, requested, inventory, batch
            )

    def test_complete_inventory_accepted(self):
        trace_capture.validate_selected_capture(DOCUMENT, None, self.inventory(), BATCH)

    def test_missing_capture_rejected(self):
        inventory = self.inventory()
        inventory.pop(0)
        self.expect_reject(inventory)

    def test_swapped_equal_shaped_names_rejected(self):
        inventory = self.inventory()
        left = next(
            i for i, item in enumerate(inventory)
            if item["name"] == "lfo_1.post_control_vca"
        )
        right = next(
            i for i, item in enumerate(inventory)
            if item["name"] == "lfo_2.post_control_vca"
        )
        inventory[left]["name"], inventory[right]["name"] = (
            inventory[right]["name"],
            inventory[left]["name"],
        )
        self.expect_reject(inventory)

    def test_wrong_rate_rejected(self):
        inventory = self.inventory()
        for item in inventory:
            if item["name"] == "adsr_1.output":
                item["rate_hz"] = 44100
        self.expect_reject(inventory)

    def test_wrong_shape_rejected(self):
        inventory = self.inventory()
        for item in inventory:
            if item["name"] == "adsr_2.output":
                item["shape"] = [AUDIO_COUNT]
        self.expect_reject(inventory)

    def test_wrong_boundary_rejected(self):
        inventory = self.inventory()
        for item in inventory:
            if item["name"] == "control_upsample.vco_1_pitch":
                item["boundary"] = "post-midi-clamp"
        self.expect_reject(inventory)

    def test_wrong_clamp_site_rejected(self):
        inventory = self.inventory()
        for item in inventory:
            if item["name"] == "mixer.pre_normalization":
                item["boundary"] = "post-conditional-normalization"
        self.expect_reject(inventory)

    def test_malformed_digest_rejected(self):
        inventory = self.inventory()
        inventory[0]["sha256"] = "deadbeef"
        self.expect_reject(inventory)

    def test_unsupported_batch_rejected(self):
        self.expect_reject(self.inventory(), batch=31)


class EndpointTests(unittest.TestCase):
    def test_endpoint_pairs_cover_five_routes(self):
        pairs = trace_capture.endpoint_pairs(DOCUMENT)
        self.assertEqual(len(pairs), 5)
        for control, upsampled in pairs:
            self.assertTrue(control.startswith("mod_matrix."))
            self.assertTrue(upsampled.startswith("control_upsample."))

    def test_exact_endpoints_accepted(self):
        control = FakeTensor.scaled(0.5, (CONTROL_COUNT,)).tobytes()
        upsampled = bytearray(FakeTensor.scaled(0.5, (AUDIO_COUNT,)).tobytes())
        upsampled[0:4] = control[0:4]
        upsampled[-4:] = control[-4:]
        result = trace_capture.check_endpoint_bytes(control, bytes(upsampled))
        self.assertEqual(result["status"], "PASS")

    def test_endpoint_loss_rejected(self):
        control = FakeTensor.scaled(0.5, (CONTROL_COUNT,)).tobytes()
        upsampled = bytearray(FakeTensor.scaled(0.5, (AUDIO_COUNT,)).tobytes())
        upsampled[0:4] = control[0:4]
        upsampled[-4:] = control[-4:]
        upsampled[0:4] = b"\x00\x00\x00\x00"
        with self.assertRaises(ValueError):
            trace_capture.check_endpoint_bytes(control, bytes(upsampled))

    def test_truncated_trace_not_repaired(self):
        control = FakeTensor.scaled(0.5, (CONTROL_COUNT,)).tobytes()
        with self.assertRaises(ValueError):
            trace_capture.check_endpoint_bytes(
                control, FakeTensor.scaled(0.5, (AUDIO_COUNT - 1,)).tobytes()
            )

    def test_padded_trace_not_repaired(self):
        control = FakeTensor.scaled(0.5, (CONTROL_COUNT,)).tobytes()
        padded = FakeTensor.scaled(0.5, (AUDIO_COUNT,)).tobytes() + b"\x00" * 4
        with self.assertRaises(ValueError):
            trace_capture.check_endpoint_bytes(control, padded)

    def test_wrong_rate_control_not_repaired(self):
        control = FakeTensor.scaled(0.5, (CONTROL_COUNT - 1,)).tobytes()
        upsampled = FakeTensor.scaled(0.5, (AUDIO_COUNT,)).tobytes()
        with self.assertRaises(ValueError):
            trace_capture.check_endpoint_bytes(control, upsampled)


class NormalizationDiagnosticsTests(unittest.TestCase):
    def test_derived_gain_rule(self):
        above = FakeTensor.from_values([2.0], (1,))
        gain = trace_capture.derive_gain(above, FakeTorch)
        self.assertEqual(gain.data[0], 0.5)
        at = FakeTensor.from_values([1.0], (1,))
        self.assertEqual(trace_capture.derive_gain(at, FakeTorch).data[0], 1.0)
        below = FakeTensor.from_values([0.5], (1,))
        self.assertEqual(trace_capture.derive_gain(below, FakeTorch).data[0], 1.0)

    def test_gain_is_declared_derived_not_observed(self):
        trace = next(
            t for t in DOCUMENT["traces"] if t["name"] == "mixer.gain"
        )
        self.assertEqual(trace["observation"], "derived")
        self.assertEqual(trace["boundary"], "derived-reciprocal-not-applied")

    def test_observed_peak_is_the_return_frame_local(self):
        trace = next(
            t for t in DOCUMENT["traces"] if t["name"] == "mixer.peak"
        )
        self.assertEqual(trace["producer"]["mechanism"], "python-return-local")
        self.assertEqual(trace["observation"], "observed")


class NegativeControlTests(unittest.TestCase):
    def test_registry_and_capture_controls_all_rejected(self):
        controls = trace_capture.negative_controls(DOCUMENT)
        self.assertGreaterEqual(len(controls), 10)
        for name, entry in controls.items():
            self.assertEqual(entry["status"], "rejected", name)
            self.assertTrue(entry["reason"], name)

    def test_mixer_output_alias_is_the_voice_return(self):
        final = next(
            t for t in DOCUMENT["traces"] if t["name"] == "mixer.output"
        )
        self.assertEqual(final["consumers"], ["Voice.output.return"])


if __name__ == "__main__":
    unittest.main()
