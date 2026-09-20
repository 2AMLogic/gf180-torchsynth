"""Independent float Voice source models: the two VCOs and the noise source.

Issue #41 deliverable. Stdlib-only and TorchSynth/Torch/NumPy import-free DSP
for the three source modules of the default Voice at their declared call sites
in ``spec/FLOAT-INTERFACES.md``: ``vco_1#1`` (SineVCO, checkpoint order 14),
``vco_2#1`` (SquareSawVCO, order 18) and ``noise#1`` (order 21). The control
upsamplers feeding the VCOs belong to #40 and are consumed here as declared
audio-rate inputs; the audio VCAs and mixer belong to #42.

The declared calculation policy (``CALCULATION_POLICY``) reproduces the pinned
upstream evaluation order at commit ``2b0964d4c6c3d472a2a0d54d91b408caaeffca6d``
(``torchsynth/module.py`` VCO/Noise regions, ``torchsynth/util.py``):

- elementwise arithmetic rounds to binary32 at every site, in upstream order:
  ``midi_f0 + tuning``, ``mod_depth * mod``, the modulated-path ``[0, 127]``
  MIDI clamp **before** ``440 * 2^((midi-69)/12)`` conversion, then
  ``(2*pi) * f / 44100`` per sample;
- phase is the unbounded running cumsum upstream actually executes on CPU:
  a binary64 accumulator storing a binary32 partial per sample (pinned
  ``aten/src/ATen/native/cpu/ReduceOpsKernel.cpp`` ``cumsum_cpu_kernel`` uses
  ``at::acc_type<float, false>`` = binary64). No wrap, no modulo; the
  first-sample phase is ``f32(first increment + initial_phase)``, never the
  bare initial phase. The fixed model's wrapping accumulator is a later,
  separately gated decision (DR-0008, Proposed) and is deliberately not
  implemented here;
- ``initial_phase`` is added once after the whole cumsum, exactly like the
  pinned ``cosine_argument += self.p("initial_phase")``;
- transcendentals (``exp2``, ``sin``, ``cos``, ``tanh``, ``log10``) evaluate
  in binary64 and round to binary32; upstream uses SLEEF binary32 kernels, so
  these sites can differ by roughly one ulp. VCO traces are therefore
  ``declared-metrics`` comparisons (DR-0007), never byte identity;
- SquareSawVCO ``partials`` uses the unmodulated-plus-maximum-depth pitch
  (``midi_f0 + tuning + max(mod_depth, 0)``), not the per-sample clamped
  pitch, and alias behavior above Nyquist is reproduced as captured, never
  repaired: upstream has no frequency clamp outside its debug-only assertion;
- the noise stream is bit-exact by identity: the pinned CPU generator is a
  standard MT19937 (``init_genrand`` seeding) and ``uniform_(-1, 1)`` on
  binary32 maps each draw to ``f32(f32(f32((raw & 0xFFFFFF) * 2^-24) * 2) - 1)``
  filling row-major over ``(32, 176400)`` precomputed slots with seed 13; the
  canonical slot for a sound is ``sound_index % 32``. ``noise#1`` consumes the
  resolved ``input.noise`` bytes verbatim and never regenerates them.

Numeric formats for a later fixed model stay open: the checkpoint map carries
``numeric_contract = "unbound:#53"`` and DR-0008 is Proposed, so nothing here
reads, depends on, or ratifies any selected fixed-format value.
"""

from __future__ import annotations

import math
import struct

from . import float_interfaces as fi

SOURCE_COMMIT = fi.SOURCE_COMMIT
AUDIO_SAMPLES = fi.AUDIO_SAMPLES
AUDIO_RATE_HZ = fi.AUDIO_RATE_HZ
NOISE_SEED = fi.NOISE_SEED
NOISE_STREAMS = fi.NOISE_STREAMS

PI_F32 = 3.1415927410125732
TWO_PI_F32 = 6.2831854820251465
AUDIO_RATE_F32 = float(AUDIO_RATE_HZ)
MIDI_A440 = 69.0
SEMITONES_PER_OCTAVE = 12.0
MIDI_CLAMP_MIN = 0.0
MIDI_CLAMP_MAX = 127.0
NOISE_UNIFORM_LOW = -1.0
NOISE_UNIFORM_HIGH = 1.0

CALCULATION_POLICY = fi.CalculationPolicy(
    calculation_dtype="float32",
    operation_policy=(
        "pinned-upstream-op-order-v1: elementwise binary32 at every site in "
        "upstream order; binary64 phase accumulator storing binary32 partials "
        "(pinned CPU cumsum acc_type); binary64 transcendentals rounded to "
        "binary32 (SLEEF vs libm sites ~1 ulp, declared-metrics comparison); "
        "noise stream bit-exact by identity"
    ),
    version="float-sources-v1",
)


def f32(x):
    """Round one real to the declared binary32 calculation dtype."""

    return struct.unpack("<f", struct.pack("<f", x))[0]


def f32le_bytes(samples):
    """Encode binary32 samples as the declared little-endian buffer bytes."""

    return struct.pack("<%df" % len(samples), *samples)


def f32le_values(data):
    """Decode declared little-endian binary32 buffer bytes."""

    if type(data) is not bytes:
        raise ValueError("buffer bytes required")
    if len(data) % 4:
        raise ValueError("truncated binary32 buffer")
    return struct.unpack("<%df" % (len(data) // 4), data)


def checkpoint_bindings():
    """The declared call sites this module implements, from the landed map."""

    document = fi.load_checkpoints()
    wanted = {"vco_1": SineVCO, "vco_2": SquareSawVCO, "noise": NoiseSource}
    bindings = {}
    for entry in document["checkpoints"]:
        module = entry["module"]
        if module in wanted:
            bindings[module] = {
                "order": entry["order"],
                "occurrence": entry["occurrence"],
                "inputs": [binding["source"] for binding in entry["inputs"]],
                "roles": list(entry["roles"]),
                "outputs": list(entry["outputs"]),
                "model": wanted[module].__name__,
            }
    if set(bindings) != set(wanted):
        raise RuntimeError("source checkpoints missing from the declared map")
    return bindings


class MT19937:
    """The pinned CPU generator: standard MT19937, ``init_genrand`` seeding."""

    _N = 624

    def __init__(self, seed):
        seed = int(seed) & 0xFFFFFFFF
        state = [0] * self._N
        state[0] = seed
        for index in range(1, self._N):
            previous = state[index - 1]
            state[index] = (
                1812433253 * (previous ^ (previous >> 30)) + index
            ) & 0xFFFFFFFF
        self._state = state
        self._index = self._N

    def next_u32(self):
        if self._index >= self._N:
            state = self._state
            for index in range(self._N):
                mixed = (state[index] & 0x80000000) + (
                    state[(index + 1) % self._N] & 0x7FFFFFFF
                )
                value = state[(index + 397) % self._N] ^ (mixed >> 1)
                if mixed & 1:
                    value ^= 0x9908B0DF
                state[index] = value
            self._index = 0
        value = self._state[self._index]
        self._index += 1
        value ^= value >> 11
        value ^= (value << 7) & 0x9D2C5680
        value ^= (value << 15) & 0xEFC60000
        value ^= value >> 18
        return value


_NOISE_SCALE_24 = f32(2.0**-24)
_PACK_F32 = struct.Struct("<f").pack


def _uniform_minus1_1(raw_u32):
    """Pinned CPU ``uniform_(-1, 1)`` for binary32, verified bit-exact.

    ``x * (to - from) + from`` with ``to - from = 2`` and ``from = -1``, both
    exactly representable, against the verified 24-bit unit derivation.
    """

    unit = f32((raw_u32 & 0xFFFFFF) * _NOISE_SCALE_24)
    return f32(f32(unit * 2.0) - 1.0)


def canonical_noise_slot_bytes(seed, sound_index, streams, sample_count):
    """One slot of the pinned precomputed noise buffer, bit-exact.

    The pinned ``Noise`` module fills ``(32, buffer_size)`` row-major from one
    seeded generator, so slot ``s`` starts after ``s * buffer_size`` draws.
    Larger reproducible batches repeat these streams; the canonical slot for a
    sound is ``sound_index % 32``.
    """

    if type(seed) is not int or seed < 0:
        raise ValueError("noise seed must be a non-negative int")
    if type(sound_index) is not int or sound_index < 0:
        raise ValueError("sound_index must be a non-negative int")
    generator = MT19937(seed)
    for _ in range((sound_index % streams) * sample_count):
        generator.next_u32()
    out = bytearray()
    for _ in range(sample_count):
        out += _PACK_F32(_uniform_minus1_1(generator.next_u32()))
    return bytes(out)


class NoiseSource:
    """Checkpoint ``noise#1`` (order 21): consume resolved bytes verbatim.

    ``noise.raw`` is a later graph observation of the resolved ``input.noise``
    stream. This module never regenerates the stream from a fresh batch-1
    seed and never invokes upstream randomization; production of the resolved
    stream belongs to the request resolver, which uses
    :func:`canonical_noise_slot_bytes` with the pinned seed and slot rule.
    """

    def __init__(self, seed=NOISE_SEED):
        if seed != NOISE_SEED:
            raise ValueError("the canonical noise seed is 13")
        self.seed = seed

    @staticmethod
    def resolve(sound_index, seed=NOISE_SEED):
        """The resolved ``input.noise`` bytes for one global sound index."""

        return canonical_noise_slot_bytes(
            seed, sound_index, NOISE_STREAMS, AUDIO_SAMPLES
        )

    @staticmethod
    def output(resolved_noise):
        if type(resolved_noise) is not bytes:
            raise ValueError("resolved noise must be the declared bytes")
        if len(resolved_noise) != AUDIO_SAMPLES * 4:
            raise ValueError(
                "resolved noise must contain exactly %d binary32 samples"
                % AUDIO_SAMPLES
            )
        return bytes(resolved_noise)


def pitch_phases(midi_f0, tuning, mod_depth, mod_signal):
    """Unbounded phase cumsum of the shared pinned VCO pitch path.

    Returns the binary32 stored partials of the running cumsum over
    ``(2*pi) * f / 44100`` where ``f`` applies the modulated-path
    ``[0, 127]`` MIDI clamp before the ``440 * 2^((midi-69)/12)`` conversion.
    The accumulator is binary64, faithful to the pinned CPU cumsum; the
    stored partial is binary32, faithful to the declared buffer dtype.
    """

    midi = f32(f32(midi_f0) + f32(tuning))
    depth = f32(mod_depth)
    phases = []
    append = phases.append
    accumulator = 0.0
    exp2 = math.exp2
    for sample in mod_signal:
        control = f32(midi + f32(depth * f32(sample)))
        if control < MIDI_CLAMP_MIN:
            control = MIDI_CLAMP_MIN
        elif control > MIDI_CLAMP_MAX:
            control = MIDI_CLAMP_MAX
        exponent = f32(f32(control - MIDI_A440) / SEMITONES_PER_OCTAVE)
        hz = f32(440.0 * f32(exp2(exponent)))
        increment = f32(f32(TWO_PI_F32 * hz) / AUDIO_RATE_F32)
        accumulator += increment
        append(f32(accumulator))
    return phases


class SineVCO:
    """Checkpoint ``vco_1#1`` (order 14): ``torch.cos`` of the phase argument.

    Constructed from observed physical parameter values; ``output`` consumes
    the physical keyboard scalar ``keyboard.midi_f0`` (pitch-base) and the
    declared audio-rate pitch-modulation buffer from #40's upsampler.
    """

    def __init__(self, tuning, mod_depth, initial_phase):
        self.tuning = f32(tuning)
        self.mod_depth = f32(mod_depth)
        self.initial_phase = f32(initial_phase)

    def output(self, midi_f0, mod_signal):
        base = f32(midi_f0)
        initial = self.initial_phase
        cos = math.cos
        return [
            f32(cos(f32(phase + initial)))
            for phase in pitch_phases(
                base, self.tuning, self.mod_depth, mod_signal
            )
        ]


class SquareSawVCO:
    """Checkpoint ``vco_2#1`` (order 18): the distortion-synthesis oscillator.

    Shape sweeps square (0) to saw (1). ``partials`` uses
    ``12000 / (max_f0 * log10(max_f0))`` with
    ``max_f0 = midi_to_hz(midi_f0 + tuning + max(mod_depth, 0))`` — the
    unmodulated pitch plus full upward depth, recomputed exactly like the
    pinned ``partials_constant``, never the per-sample clamped pitch.
    Above-Nyquist aliasing in the result is captured upstream behavior.
    """

    def __init__(self, tuning, mod_depth, initial_phase, shape):
        self.tuning = f32(tuning)
        self.mod_depth = f32(mod_depth)
        self.initial_phase = f32(initial_phase)
        self.shape = f32(shape)

    def partials(self, midi_f0):
        max_pitch = f32(
            f32(f32(midi_f0) + self.tuning) + max(self.mod_depth, 0.0)
        )
        exponent = f32(f32(max_pitch - MIDI_A440) / SEMITONES_PER_OCTAVE)
        max_f0 = f32(440.0 * f32(math.exp2(exponent)))
        if max_f0 <= 0.0:
            raise ValueError("nonpositive maximum frequency has no log10")
        return f32(12000.0 / f32(max_f0 * f32(math.log10(max_f0))))

    def output(self, midi_f0, mod_signal):
        base = f32(midi_f0)
        initial = self.initial_phase
        partials_scale = f32(PI_F32 * self.partials(base))
        shape = self.shape
        one_minus_half_shape = f32(1.0 - f32(shape / 2.0))
        sin = math.sin
        cos = math.cos
        tanh = math.tanh
        out = []
        append = out.append
        for phase in pitch_phases(
            base, self.tuning, self.mod_depth, mod_signal
        ):
            argument = f32(phase + initial)
            driven = f32(partials_scale * f32(sin(argument)))
            square = f32(tanh(f32(driven / 2.0)))
            left = f32(one_minus_half_shape * square)
            right = f32(1.0 + f32(shape * f32(cos(argument))))
            append(f32(left * right))
        return out
