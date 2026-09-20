"""Issue #41 float source models: identity, comparisons, semantics, mutations.

Offline stdlib tests (no Torch). The captured upstream fixtures and the
bounded evidence record come from the qualified release-era capture run
(``tools/capture_float_sources.py``); see
``sim/reference/float-sources-v1.json`` for provenance, measured values and
declared limits.
"""

import hashlib
import json
import math
import struct
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import float_interfaces as fi  # noqa: E402
from torchsynth_voice import float_sources as fs  # noqa: E402
from torchsynth_voice import paired_metrics as pm  # noqa: E402

RECORD_PATH = ROOT / "sim/reference/float-sources-v1.json"
FIXTURES = ROOT / "tests/fixtures/float-sources"
RECORD = json.loads(RECORD_PATH.read_bytes())


def f32(x):
    return fs.f32(x)


def load_case(case_id):
    directory = FIXTURES / case_id
    params = json.loads((directory / "params.json").read_bytes())
    physical = params["physical_by_name"]
    buffers = {}
    for name in RECORD["comparison_traces"]:
        buffers[name] = fs.f32le_values(
            (directory / (name + ".f32le")).read_bytes()
        )
    return params, physical, buffers


def paired_max_abs_error(reference, candidate):
    measurement = pm.compare_paired(
        reference,
        candidate,
        reference_rate_hz=fs.AUDIO_RATE_HZ,
        candidate_rate_hz=fs.AUDIO_RATE_HZ,
        unit=RECORD["comparison_unit"],
        spectral=False,
    )
    return measurement["metrics"]


def render_vco(vco, physical, control, **overrides):
    kwargs = dict(
        tuning=physical[vco + ".tuning"],
        mod_depth=physical[vco + ".mod_depth"],
        initial_phase=physical[vco + ".initial_phase"],
    )
    kwargs.update(overrides)
    model = fs.SquareSawVCO if vco == "vco_2" else fs.SineVCO
    if model is fs.SquareSawVCO:
        kwargs.setdefault("shape", physical[vco + ".shape"])
    return model(**kwargs).output(physical["keyboard.midi_f0"], control)


def small_noise(seed, sound_index, sample_count, streams=32):
    return fs.canonical_noise_slot_bytes(seed, sound_index, streams, sample_count)


class RecordAndFixtures(unittest.TestCase):
    def test_record_binds_model_and_policy(self):
        self.assertEqual(
            RECORD["model_module_sha256"],
            hashlib.sha256(
                (ROOT / "src/torchsynth_voice/float_sources.py").read_bytes()
            ).hexdigest(),
        )
        self.assertEqual(
            RECORD["calculation_policy"],
            fs.CALCULATION_POLICY.describe(),
        )
        self.assertEqual(RECORD["numeric_contract"], "unbound:#53")

    def test_fixture_digests_match_record(self):
        for case_id, entries in RECORD["fixtures"].items():
            directory = FIXTURES / case_id
            for name, entry in entries["files"].items():
                digest = hashlib.sha256(
                    (directory / (name + ".f32le")).read_bytes()
                ).hexdigest()
                self.assertEqual(digest, entry["sha256"], case_id + "/" + name)
            self.assertEqual(
                hashlib.sha256((directory / "params.json").read_bytes()).hexdigest(),
                entries["params_sha256"],
            )


class NoiseBitIdentity(unittest.TestCase):
    def test_all_32_slots_reproduce_torch_digests(self):
        document = json.loads((FIXTURES / "noise-streams.json").read_bytes())
        generator = fs.MT19937(document["seed"])
        for slot in range(document["streams"]):
            chunks = bytearray()
            pack = struct.Struct("<f").pack
            for _ in range(document["sample_count"]):
                chunks += pack(fs._uniform_minus1_1(generator.next_u32()))
            digest = hashlib.sha256(bytes(chunks)).hexdigest()
            self.assertEqual(
                digest,
                document["slots"][str(slot)]["sha256"],
                "noise slot %d diverged from the pinned torch stream" % slot,
            )

    def test_noise_module_consumes_resolved_bytes_verbatim(self):
        resolved = fs.NoiseSource.resolve(0)
        self.assertEqual(fs.NoiseSource.output(resolved), resolved)
        with self.assertRaises(ValueError):
            fs.NoiseSource.output(resolved[:-4])
        with self.assertRaises(ValueError):
            fs.NoiseSource.output(memoryview(resolved))
        with self.assertRaises(ValueError):
            fs.NoiseSource(seed=14)

    def test_slot_rule_and_identity_failures(self):
        tiny = dict(sample_count=64, streams=8)
        self.assertEqual(
            small_noise(13, 37, **tiny),
            small_noise(13, 5, **tiny),
            "slot must be sound_index % streams",
        )
        self.assertNotEqual(small_noise(13, 0, **tiny), small_noise(13, 1, **tiny))
        self.assertNotEqual(small_noise(13, 0, **tiny), small_noise(14, 0, **tiny))

    def test_resolved_noise_matches_record_slot0(self):
        self.assertEqual(
            hashlib.sha256(fs.NoiseSource.resolve(0)).hexdigest(),
            RECORD["noise"]["declared_slot0_sha256"],
        )


class CheckpointBinding(unittest.TestCase):
    def test_declared_call_sites_match_the_model(self):
        bindings = fs.checkpoint_bindings()
        self.assertEqual(bindings["vco_1"]["order"], 14)
        self.assertEqual(bindings["vco_2"]["order"], 18)
        self.assertEqual(bindings["noise"]["order"], 21)
        self.assertEqual(
            bindings["vco_1"]["inputs"],
            ["keyboard.midi_f0", "control_upsample.vco_1_pitch"],
        )
        self.assertEqual(
            bindings["vco_2"]["inputs"],
            ["keyboard.midi_f0", "control_upsample.vco_2_pitch"],
        )
        self.assertEqual(bindings["noise"]["inputs"], [])
        self.assertEqual(bindings["vco_1"]["roles"], ["pitch-base", "pitch-modulation"])
        self.assertEqual(bindings["vco_1"]["model"], "SineVCO")
        self.assertEqual(bindings["vco_2"]["model"], "SquareSawVCO")
        self.assertEqual(bindings["noise"]["model"], "NoiseSource")


class VcoCheckpointComparisons(unittest.TestCase):
    """Declared-metrics match against the captured pinned Voice traces."""

    def test_every_captured_case_matches_under_declared_limits(self):
        for case_id in RECORD["capture"]["cases"]:
            limit = RECORD["limits"][case_id]
            with self.subTest(case=case_id):
                params, physical, buffers = load_case(case_id)
                for vco in ("vco_1", "vco_2"):
                    rendered = render_vco(
                        vco, physical, buffers["control_upsample." + vco + "_pitch"]
                    )
                    metrics = paired_max_abs_error(
                        buffers[vco + ".raw"], rendered
                    )
                    self.assertEqual(metrics["framing_match"]["value"], 1)
                    self.assertLessEqual(
                        metrics["max_abs_error"]["value"],
                        limit,
                        "%s %s exceeded the declared limit" % (case_id, vco),
                    )


class PhaseSemantics(unittest.TestCase):
    def test_first_sample_includes_first_increment(self):
        model = fs.SineVCO(0.0, 0.0, math.pi)
        rendered = model.output(69.0, [0.0])
        increment = f32(f32(fs.TWO_PI_F32 * f32(440.0)) / fs.AUDIO_RATE_F32)
        expected = f32(math.cos(f32(increment + f32(math.pi))))
        self.assertEqual(struct.pack("<f", rendered[0]), struct.pack("<f", expected))
        # The wrong convention (bare initial phase at sample zero) is
        # distinguishable by far more than rounding at this increment.
        self.assertGreater(abs(rendered[0] - math.cos(math.pi)), 0.001)

    def test_phase_accumulator_is_binary64_with_binary32_partials(self):
        hz = f32(440.0 * f32(2.0 ** f32(f32(f32(93.0 - 69.0) / 12.0))))
        increment = f32(f32(fs.TWO_PI_F32 * hz) / fs.AUDIO_RATE_F32)
        model = fs.SineVCO(24.0, 0.0, 0.0)
        rendered = model.output(69.0, [0.0] * 4000)
        accumulator = 0.0
        for index, value in enumerate(rendered):
            accumulator += increment
            argument = f32(accumulator)
            self.assertEqual(
                struct.pack("<f", value), struct.pack("<f", f32(math.cos(argument)))
            )

    def test_modulated_path_clamps_before_conversion(self):
        depth = 200.0
        for control, clamped_midi in ((1.0, 127.0), (-1.0, 0.0)):
            model = fs.SineVCO(0.0, depth, 0.0)
            rendered = model.output(69.0, [control])
            expected_hz = f32(
                440.0 * f32(2.0 ** f32(f32(clamped_midi - 69.0) / 12.0))
            )
            increment = f32(f32(fs.TWO_PI_F32 * expected_hz) / fs.AUDIO_RATE_F32)
            expected = f32(math.cos(increment))
            self.assertEqual(
                struct.pack("<f", rendered[0]), struct.pack("<f", expected)
            )

    def test_square_saw_partials_use_unmodulated_pitch_plus_depth(self):
        model = fs.SquareSawVCO(3.0, 24.0, 0.0, 1.0)
        max_pitch = f32(f32(f32(69.0) + 3.0) + 24.0)
        max_f0 = f32(440.0 * f32(2.0 ** f32(f32(max_pitch - 69.0) / 12.0)))
        expected = f32(12000.0 / f32(max_f0 * f32(math.log10(max_f0))))
        self.assertEqual(struct.pack("<f", model.partials(69.0)), struct.pack("<f", expected))


class MutationLocalization(unittest.TestCase):
    """Faulted model variants must fail their captured comparisons."""

    def test_ignored_tuning_localizes(self):
        case_id = "boundary:vco_1.tuning:upper"
        limit = RECORD["limits"][case_id]
        params, physical, buffers = load_case(case_id)
        rendered = render_vco("vco_1", physical, buffers["control_upsample.vco_1_pitch"])
        self.assertLessEqual(
            paired_max_abs_error(buffers["vco_1.raw"], rendered)["max_abs_error"][
                "value"
            ],
            limit,
        )
        unturned = render_vco(
            "vco_1",
            physical,
            buffers["control_upsample.vco_1_pitch"],
            tuning=0.0,
        )
        self.assertGreater(
            paired_max_abs_error(buffers["vco_1.raw"], unturned)["max_abs_error"][
                "value"
            ],
            limit,
            "an ignored tuning fault was not localized",
        )

    def test_alias_repair_frequency_clamp_localizes(self):
        case_id = "boundary:vco_1.mod_depth:upper"
        limit = RECORD["limits"][case_id]

        class FrequencyClampedSine(fs.SineVCO):
            def output(self, midi_f0, mod_signal):
                phases = []
                accumulator = 0.0
                midi = f32(f32(midi_f0) + self.tuning)
                exp2 = math.exp2
                for sample in mod_signal:
                    control = f32(midi + f32(self.mod_depth * f32(sample)))
                    control = min(max(control, 0.0), 127.0)
                    hz = f32(
                        440.0 * f32(exp2(f32(f32(control - 69.0) / 12.0)))
                    )
                    hz = min(hz, 8000.0)
                    accumulator += f32(f32(fs.TWO_PI_F32 * hz) / fs.AUDIO_RATE_F32)
                    phases.append(f32(accumulator))
                init = self.initial_phase
                return [f32(math.cos(f32(p + init))) for p in phases]

        params, physical, buffers = load_case(case_id)
        control = buffers["control_upsample.vco_1_pitch"]
        faithful = fs.SineVCO(
            physical["vco_1.tuning"],
            physical["vco_1.mod_depth"],
            physical["vco_1.initial_phase"],
        ).output(physical["keyboard.midi_f0"], control)
        self.assertLessEqual(
            paired_max_abs_error(buffers["vco_1.raw"], faithful)["max_abs_error"][
                "value"
            ],
            limit,
        )
        mutated = FrequencyClampedSine(
            physical["vco_1.tuning"],
            physical["vco_1.mod_depth"],
            physical["vco_1.initial_phase"],
        ).output(physical["keyboard.midi_f0"], control)
        self.assertGreater(
            paired_max_abs_error(buffers["vco_1.raw"], mutated)["max_abs_error"][
                "value"
            ],
            limit,
            "alias-repair frequency clamping was not localized",
        )

    def test_nyquist_clamp_is_ineffective_inside_the_pinned_domain(self):
        case_id = "boundary:vco_1.mod_depth:upper"

        class NyquistClampedSine(fs.SineVCO):
            def output(self, midi_f0, mod_signal):
                phases = []
                accumulator = 0.0
                midi = f32(f32(midi_f0) + self.tuning)
                exp2 = math.exp2
                for sample in mod_signal:
                    control = f32(midi + f32(self.mod_depth * f32(sample)))
                    control = min(max(control, 0.0), 127.0)
                    hz = f32(
                        440.0 * f32(exp2(f32(f32(control - 69.0) / 12.0)))
                    )
                    hz = min(hz, fs.AUDIO_RATE_F32 / 2.0)
                    accumulator += f32(f32(fs.TWO_PI_F32 * hz) / fs.AUDIO_RATE_F32)
                    phases.append(f32(accumulator))
                init = self.initial_phase
                return [f32(math.cos(f32(p + init))) for p in phases]

        params, physical, buffers = load_case(case_id)
        control = buffers["control_upsample.vco_1_pitch"]
        faithful = fs.SineVCO(
            physical["vco_1.tuning"],
            physical["vco_1.mod_depth"],
            physical["vco_1.initial_phase"],
        ).output(physical["keyboard.midi_f0"], control)
        mutated = NyquistClampedSine(
            physical["vco_1.tuning"],
            physical["vco_1.mod_depth"],
            physical["vco_1.initial_phase"],
        ).output(physical["keyboard.midi_f0"], control)
        # Upstream never exceeds Nyquist inside the clamped [0, 127] MIDI
        # domain (the pinned maximum is ~12.54 kHz), so a Nyquist clamp is a
        # bit-identical no-op here: there is no aliasing to "repair" at
        # 22050 Hz. This documents the no-op honestly; the in-band 8 kHz
        # clamp mutation above is the one that localizes.
        self.assertEqual(
            fs.f32le_bytes(faithful), fs.f32le_bytes(mutated)
        )

    def test_wrapped_phase_localizes(self):
        case_id = "boundary:vco_1.tuning:upper"
        limit = RECORD["limits"][case_id]

        class PhaseWrappedSine(fs.SineVCO):
            def output(self, midi_f0, mod_signal):
                phases = fs.pitch_phases(
                    midi_f0, self.tuning, self.mod_depth, mod_signal
                )
                init = self.initial_phase
                return [
                    f32(math.cos(f32(f32(phase % fs.TWO_PI_F32) + init)))
                    for phase in phases
                ]

        params, physical, buffers = load_case(case_id)
        control = buffers["control_upsample.vco_1_pitch"]
        faithful = fs.SineVCO(
            physical["vco_1.tuning"],
            physical["vco_1.mod_depth"],
            physical["vco_1.initial_phase"],
        ).output(physical["keyboard.midi_f0"], control)
        mutated = PhaseWrappedSine(
            physical["vco_1.tuning"],
            physical["vco_1.mod_depth"],
            physical["vco_1.initial_phase"],
        ).output(physical["keyboard.midi_f0"], control)
        packed_faithful = [struct.pack("<f", v) for v in faithful]
        packed_mutated = [struct.pack("<f", v) for v in mutated]
        differing = sum(
            1
            for a, b in zip(packed_faithful, packed_mutated)
            if a != b
        )
        self.assertGreater(
            differing,
            len(faithful) // 2,
            "wrapped phase did not diverge from the unbounded accumulator",
        )
        metrics = paired_max_abs_error(buffers["vco_1.raw"], mutated)
        self.assertGreater(
            metrics["max_abs_error"]["value"],
            limit,
            "wrapped phase was not localized against the captured trace",
        )

    def test_bare_initial_phase_localizes(self):
        case_id = "boundary:vco_1.tuning:upper"
        limit = RECORD["limits"][case_id]

        class BareInitialPhaseSine(fs.SineVCO):
            def output(self, midi_f0, mod_signal):
                phases = fs.pitch_phases(
                    midi_f0, self.tuning, self.mod_depth, mod_signal
                )
                # Wrong convention: sample zero is the bare initial phase
                # (the first increment is dropped from the first argument).
                rendered = [
                    f32(math.cos(f32(phase + self.initial_phase)))
                    for phase in phases
                ]
                rendered[0] = f32(math.cos(self.initial_phase))
                return rendered

        params, physical, buffers = load_case(case_id)
        mutated = BareInitialPhaseSine(
            physical["vco_1.tuning"],
            physical["vco_1.mod_depth"],
            physical["vco_1.initial_phase"],
        ).output(
            physical["keyboard.midi_f0"], buffers["control_upsample.vco_1_pitch"]
        )
        self.assertGreater(
            paired_max_abs_error(buffers["vco_1.raw"], mutated)["max_abs_error"][
                "value"
            ],
            limit,
            "the bare-initial-phase fault was not localized",
        )

    def test_forced_square_shape_localizes(self):
        case_id = "waveform:vco_2:saw"
        limit = RECORD["limits"][case_id]
        params, physical, buffers = load_case(case_id)
        control = buffers["control_upsample.vco_2_pitch"]
        faithful = render_vco("vco_2", physical, control)
        self.assertLessEqual(
            paired_max_abs_error(buffers["vco_2.raw"], faithful)["max_abs_error"][
                "value"
            ],
            limit,
        )
        mutated = render_vco(
            "vco_2", physical, control, shape=0.0
        )
        self.assertGreater(
            paired_max_abs_error(buffers["vco_2.raw"], mutated)["max_abs_error"][
                "value"
            ],
            limit,
            "a forced-square shape fault was not localized",
        )

    def test_wrong_noise_stream_localizes(self):
        tiny = dict(sample_count=256, streams=32)
        declared = small_noise(13, 0, **tiny)
        self.assertNotEqual(declared, small_noise(13, 1, **tiny))
        self.assertNotEqual(declared, small_noise(14, 0, **tiny))
        self.assertNotEqual(declared, small_noise(13, 6, **tiny))


class DeterminismAndImportFreedom(unittest.TestCase):
    def test_repeated_renders_are_identical(self):
        _, physical, buffers = load_case("waveform:vco_2:saw")
        control = buffers["control_upsample.vco_2_pitch"]
        first = fs.f32le_bytes(render_vco("vco_2", physical, control))
        second = fs.f32le_bytes(render_vco("vco_2", physical, control))
        self.assertEqual(first, second)

    def test_module_imports_no_torchsynth_torch_or_numpy(self):
        code = (
            "import sys; sys.path.insert(0, %r); "
            "import torchsynth_voice.float_sources; "
            "print(sorted(set(sys.modules) & {'torch', 'numpy', 'torchsynth'}))"
            % str(ROOT / "src")
        )
        observed = subprocess.check_output(
            [sys.executable, "-S", "-c", code], text=True
        ).strip()
        self.assertEqual(observed, "[]")
        source = (ROOT / "src/torchsynth_voice/float_sources.py").read_text()
        for forbidden in ("import torch", "import numpy", "from torch", "from numpy"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
