"""Golden-vector derivation and host mirror for the square/saw VCO (#74).

The bit-exactness target is the frozen fixed whole-voice model's vco_2 lane:
``FixedVoiceModel.render``'s square/saw oscillator block
(``src/torchsynth_voice/fixed_voice.py``, the ``m2``/``p2``/``v2`` dataflow).
Every exact-integer site is re-walked here with the model's own primitives
(``fixed_voice.entry_quantize``, ``fixedpoint.ops.mul``,
``fixedpoint.ops.apply_policy``, ``fixedpoint.rounding.div_round``,
``fixedpoint.phase.PhaseAccumulator``, the accepted C5 quarter-wave table)
so the mirror is provably the model, not a harness-parallel copy. The proof
obligation is digest equality against the frozen ``fixed-voice-golden-v1``
cases: sha256 over the exact ``vco_2.raw`` word lists in the #54
trace-digest convention.

Declared binary64 shadow sites (the #74 declaration class; DR-0008/DR-0010
open approximation items -- computed host-side here and replayed to the RTL
as deterministic words; NO RTL transcendental is implemented or claimed):

- ``vco.midi_to_hz.exp2.shadow``: ``440 * 2**((m/2**21 - 69)/12)`` on the
  quantized Q10.21 pitch word, narrowed once to the Q16.15 frequency word;
- ``vco_2.partials_constant.shadow``: ``12000/(f_max*log10(f_max))`` scaled
  by the pinned binary32 pi, narrowed once to the s14.17 partials word;
- ``vco_2.tanh.shadow`` and its S4a fanout: ``tanh((driven/scale)/2)`` on
  the quantized s14.17 ``driven`` word, narrowed to the Q2.21 ``square_q``
  and ``left_q`` words (the model's single-rounding binary64 products; the
  exact site of the future fixed-approximation DR decision).

Everything else is exact integer dataflow the RTL owns: the depth-mod
multiply and pitch sum (C4 Q10.21, C6 half-even, C7 saturate), the
[0, 127] MIDI clamp, the phase-increment division (half-even over the
constant ``freq_scale * fs`` denominator), the u32 wrapping phase
accumulator with the S3-replayed initial-turn word, both C5 LUT
interpolations (cos at the phase, sin a quarter turn behind), the
``driven`` multiply (s14.17), the ``right`` branch narrowing (exactly
``half_even(2**21 + shape*cos2 / 2**22)`` -- the exact rational form of the
model's binary64 expression for every accepted shape word), and the final
``left_q * right_q`` combine (C1).

No RTL claim of any kind is made here: no synthesis, layout, signoff,
hardware playback, or sound fidelity.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
from fractions import Fraction
from typing import Any, Dict, List, NamedTuple, Tuple

from .fixed_voice import (
    MIDI_A440,
    MIDI_CLAMP_MAX,
    MIDI_CLAMP_MIN,
    SEMITONES_PER_OCTAVE,
    AcceptedFormats,
    entry_quantize,
    shadow_half_even,
)
from .fixedpoint.counters import StickyCounters
from .fixedpoint.formats import FixedFormat
from .fixedpoint.ops import OverflowPolicy, apply_policy, mul
from .fixedpoint.phase import PhaseAccumulator
from .fixedpoint.rounding import div_round
from .float_interfaces import AUDIO_RATE_HZ
from .float_sources import PI_F32
from .format_sweep import FixedControlPath

__all__ = [
    "SHADOW_EXP2",
    "SHADOW_TANH",
    "SHADOW_PARTIALS",
    "PartialsConstant",
    "voice_digest",
    "initial_phase_word",
    "entry_words",
    "mirror_square_saw_vco",
    "derive_case",
    "unpack_words_f32le",
    "pack_words_f32le",
    "VcoGoldenError",
]

#: The declared shadow-site names (the model's own tallies).
SHADOW_EXP2 = "vco.midi_to_hz.exp2.shadow"
SHADOW_TANH = "vco_2.tanh.shadow"
SHADOW_PARTIALS = "vco_2.partials_constant.shadow"

#: The s14.17 format of the ``partials_constant`` word (the model's own).
PARTIALS_FMT = FixedFormat(signed=True, int_bits=14, frac_bits=17)


class VcoGoldenError(ValueError):
    """Raised when a mirror refuses to match the frozen model."""


class PartialsConstant(NamedTuple):
    """The per-clip partials word (the declared shadow's quantized output)."""

    word: int


def voice_digest(words: List[int]) -> str:
    """The #54 trace-digest convention over an integer word list."""

    blob = json.dumps(
        list(words), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def pack_words_f32le(words: List[int], frac_bits: int = 21) -> bytes:
    """Pack Q-format words as little-endian binary32 (the #54 convention).

    ``word / 2**frac_bits`` is exactly representable whenever the signed
    word fits in ``frac_bits + 23`` bits, so the packing is lossless; the
    unpacker refuses any drift anyway.
    """

    scale = float(1 << frac_bits)
    return struct.pack("<%df" % len(words), *[word / scale for word in words])


def unpack_words_f32le(payload: bytes, frac_bits: int = 21) -> List[int]:
    """Unpack binary32 payload back to exact words, refusing any drift."""

    scale = 1 << frac_bits
    values = struct.unpack("<%df" % (len(payload) // 4), payload)
    words: List[int] = []
    for value in values:
        scaled = value * scale
        rounded = int(round(scaled))
        if scaled != rounded:
            raise VcoGoldenError(
                "f32le payload value %r is not an exact Q%d word multiple"
                % (value, frac_bits)
            )
        words.append(rounded)
    return words


def initial_phase_word(formats: AcceptedFormats, physical: Any) -> int:
    """The declared S3-style initial-turn word for ``vco_2`` (host replay).

    ``turns = initial_phase / (2*pi)`` as exact Fractions, rounded half-even
    over the u32 phase circle and reduced modulo ``2**width`` -- the same
    computation the model performs through ``PhaseAccumulator.inject_turns``.
    """

    acc = PhaseAccumulator(formats.phase_width)
    turns = Fraction(float(physical)) / (2 * Fraction(math.pi))
    acc.inject_turns(turns, site="phase.initial.vco_2", mode=formats.mode)
    return acc.value


def entry_words(
    formats: AcceptedFormats, physical: Dict[str, Any], counters: StickyCounters
) -> Dict[str, int]:
    """The S1 entry words the vco_2 lane consumes (the model's own sites)."""

    return {
        "vco_2.tuning": entry_quantize(
            physical["vco_2.tuning"], formats.midi, counters, "s1.entry:vco_2.tuning"
        ),
        "vco_2.mod_depth": entry_quantize(
            physical["vco_2.mod_depth"], formats.midi, counters,
            "s1.entry:vco_2.mod_depth",
        ),
        "vco_2.shape": entry_quantize(
            physical["vco_2.shape"], formats.midi, counters, "s1.entry:vco_2.shape"
        ),
    }


def partials_constant(
    formats: AcceptedFormats,
    midi_f0_word: int,
    tuning_word: int,
    depth_word: int,
    counters: StickyCounters,
) -> PartialsConstant:
    """The declared ``partials_constant`` shadow site (host, binary64).

    Exact integer ``partials.sum`` saturation, then the model's own
    ``12000/(f_max*log10(f_max))`` real derivation scaled by the pinned
    binary32 pi and narrowed once to the s14.17 word
    (``src/torchsynth_voice/fixed_voice.py``, the ``partials.*`` sites).
    """

    max_pitch_word = apply_policy(
        midi_f0_word + tuning_word + max(depth_word, 0),
        formats.midi, OverflowPolicy.SATURATE, counters, "vco_2.partials.sum",
    )
    max_pitch_real = max_pitch_word / formats.midi.scale
    max_f0 = 440.0 * math.exp2((max_pitch_real - MIDI_A440) / SEMITONES_PER_OCTAVE)
    if max_f0 <= 0.0:
        raise VcoGoldenError("nonpositive maximum frequency has no log10")
    partials_real = 12000.0 / (max_f0 * math.log10(max_f0))
    partials_scale = float(PI_F32) * partials_real
    word = apply_policy(
        shadow_half_even(partials_scale * PARTIALS_FMT.scale),
        PARTIALS_FMT, OverflowPolicy.SATURATE, counters, "partials.q",
    )
    return PartialsConstant(word=word)


def mirror_square_saw_vco(
    formats: AcceptedFormats,
    words: Dict[str, int],
    partials: PartialsConstant,
    init_word: int,
    up_pitch: List[int],
) -> Dict[str, Any]:
    """Exact integer re-walk of the frozen model's vco_2 lane.

    Returns every lane stream (the pitch word, the per-sample shadow replay
    words, the LUT words, ``driven``, ``right_q``, and the ``vco_2.raw``
    output words) plus the sticky counters and shadow tallies. The exported
    ``square_q``/``left_q`` words are the declared tanh-shadow fanout the
    RTL consumes as replayed inputs; ``fq2`` is the declared exp2-shadow
    word feeding the RTL's own increment division.
    """

    counters = StickyCounters()
    audio = formats.audio
    midi_fmt = formats.midi
    table = formats.table
    entry_fmt = table.spec.entry_format
    lut_scale = float(entry_fmt.scale)
    phase_units = formats.phase_units_per_turn
    freq_scale = formats.frequency.scale
    midi_scale = float(midi_fmt.scale)
    clamp_max_word = MIDI_CLAMP_MAX << midi_fmt.frac_bits
    freq_denominator = freq_scale * AUDIO_RATE_HZ
    audio_scale_f = float(audio.scale)
    # The model's own tanh normalizer: the s14.17 FORMAT scale (the driven
    # word read as its fixed-point real value), not the partials real scale
    # (src/torchsynth_voice/fixed_voice.py:369).
    partials_scale_f = float(PARTIALS_FMT.scale)
    mode = formats.mode
    saturate = OverflowPolicy.SATURATE

    shape_word = words["vco_2.shape"]
    shape_real = shape_word / midi_scale
    one_minus_half_shape = 1.0 - shape_real / 2.0

    acc = PhaseAccumulator(formats.phase_width)
    acc.value = init_word % acc.modulus

    tally = {SHADOW_EXP2: 0, SHADOW_TANH: 0, SHADOW_PARTIALS: 1}
    m2_words: List[int] = []
    fq_words: List[int] = []
    k_words: List[int] = []
    phase_words: List[int] = []
    cos_words: List[int] = []
    sin_words: List[int] = []
    driven_words: List[int] = []
    square_words: List[int] = []
    left_words: List[int] = []
    right_words: List[int] = []
    v2_words: List[int] = []

    evaluate = table.evaluate
    exp2 = math.exp2
    tanh = math.tanh

    for n in range(len(up_pitch)):
        m2 = apply_policy(
            words["keyboard.midi_f0"] + words["vco_2.tuning"]
            + mul(words["vco_2.mod_depth"], midi_fmt, up_pitch[n], audio,
                  midi_fmt, mode, saturate, counters, "vco_2.depth_mod"),
            midi_fmt, saturate, counters, "vco_2.pitch_sum",
        )
        if m2 < MIDI_CLAMP_MIN:
            m2 = MIDI_CLAMP_MIN
        elif m2 > clamp_max_word:
            m2 = clamp_max_word
        tally[SHADOW_EXP2] += 1
        hz2 = 440.0 * exp2((m2 / midi_scale - MIDI_A440) / SEMITONES_PER_OCTAVE)
        fq2 = shadow_half_even(hz2 * freq_scale)
        k2 = div_round(fq2 * phase_units, freq_denominator, mode)
        p2 = acc.step(k2)
        cos2 = evaluate(p2, mode)
        sin2 = evaluate((p2 - (phase_units >> 2)) % phase_units, mode)
        driven = mul(partials.word, PARTIALS_FMT, sin2, entry_fmt,
                     PARTIALS_FMT, mode, saturate, counters, "vco_2.driven")
        tally[SHADOW_TANH] += 1
        square_real = tanh((driven / partials_scale_f) / 2.0)
        square_q = apply_policy(
            shadow_half_even(square_real * audio_scale_f), audio, saturate,
            counters, "vco_2.tanh_site.S4a",
        )
        left_q = apply_policy(
            shadow_half_even(one_minus_half_shape * square_real * audio_scale_f),
            audio, saturate, counters, "vco_2.left.S4a",
        )
        right_real = 1.0 + shape_real * (cos2 / lut_scale)
        right_q = apply_policy(
            shadow_half_even(right_real * audio_scale_f), audio, saturate,
            counters, "vco_2.right.S4a",
        )
        v2 = mul(left_q, audio, right_q, audio, audio, mode, saturate,
                 counters, "vco_2.S4")

        m2_words.append(m2)
        fq_words.append(fq2)
        k_words.append(k2)
        phase_words.append(p2)
        cos_words.append(cos2)
        sin_words.append(sin2)
        driven_words.append(driven)
        square_words.append(square_q)
        left_words.append(left_q)
        right_words.append(right_q)
        v2_words.append(v2)

    return {
        "m2": m2_words,
        "fq": fq_words,
        "k": k_words,
        "phase": phase_words,
        "cos": cos_words,
        "sin": sin_words,
        "driven": driven_words,
        "square_q": square_words,
        "left_q": left_words,
        "right_q": right_words,
        "v2": v2_words,
        "counters": counters.as_json(),
        "shadow_tally": tally,
    }


def derive_case(
    formats: AcceptedFormats, physical: Dict[str, Any]
) -> Dict[str, Any]:
    """Derive one case's stimulus and mirror truth from physical parameters.

    The pitch column comes from the frozen composition's own control path
    (``FixedControlPath.render_words`` -- the exact class the frozen
    whole-voice model instantiates); the S1 entry words, the declared
    shadow sites, and the mirror re-walk come from this module. Callers
    prove model-equality by digest against the committed ``vco_2.raw``
    evidence (the frozen golden's per-case trace digests or a fresh
    ``FixedVoiceModel`` render for dedicated cases).
    """

    counters = StickyCounters()
    fcp = FixedControlPath(formats.control_spec)
    control_words = fcp.render_words(physical)
    up_pitch = control_words["control_upsample.vco_2_pitch"]
    words = entry_words(formats, physical, counters)
    words["keyboard.midi_f0"] = control_words["keyboard.midi_f0"][0]
    partials = partials_constant(
        formats, words["keyboard.midi_f0"], words["vco_2.tuning"],
        words["vco_2.mod_depth"], counters,
    )
    init_word = initial_phase_word(formats, physical["vco_2.initial_phase"])
    streams = mirror_square_saw_vco(formats, words, partials, init_word, up_pitch)
    return {
        "words": words,
        "partials": partials,
        "init_word": init_word,
        "up_pitch": up_pitch,
        "streams": streams,
    }
