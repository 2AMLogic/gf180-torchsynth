"""Golden-vector derivation and host mirror for the sine VCO lane (#73).

The bit-exactness target is the frozen fixed model's sine source lane
(vco_1): the per-sample loop in ``src/torchsynth_voice/fixed_voice.py``
(consumed through the frozen whole-voice receipt
``sim/reference/fixed-voice-golden-v1.json``). This module adds only:

- :func:`initial_phase_word` — the declared one-time initial-phase
  injection: ``half_even(turns * 2^32) mod 2^32`` with ``turns`` the
  binary64 physical value converted to an exact fraction exactly as the
  model does (``Fraction(float(...))``).
- :func:`mirror_sine_lane` — an exact integer re-walk of the sine lane
  (depth-mod product, ONE declared half-even narrowing to C4, C7
  saturation, the model's own MIDI clamp band, the declared binary64
  exp2 shadow site replayed host-side, the Q16.15 formation, the
  half-even K division, the C2 wrapping first-increment-first phase, the
  accepted hash-linked C5 table evaluation, and the S4 narrowing). It
  uses the model's own primitives and formats only; there is no
  separable model method to row-compare against, so the equality target
  is the receipt's frozen per-case ``vco_1.raw`` digests — asserted by
  the caller for every case.

The declared shadow sites are replayed host-side exactly as DR-0008
declares them open items: no RTL claim is made for them, and nothing
here claims synthesis, layout, signoff, or hardware anything. The RTL
engine (``tb/sv/sine_vco_engine.sv``) reproduces the same integer
dataflow bit-exactly.
"""

from __future__ import annotations

import hashlib
import json
import math
from fractions import Fraction
from typing import Dict, List, Tuple

from .float_interfaces import AUDIO_RATE_HZ
from .fixed_voice import entry_quantize, shadow_half_even
from .fixedpoint.counters import StickyCounters
from .fixedpoint.ops import OverflowPolicy, apply_policy, mul, rescale
from .fixedpoint.rounding import RoundingMode, div_round

__all__ = [
    "SineVcoGoldenError",
    "initial_phase_word",
    "mirror_sine_lane",
    "derive_case",
    "digest_words",
    "MIDI_CLAMP_MIN",
    "MIDI_CLAMP_MAX",
]

#: The model's own MIDI clamp band (a formatting clamp, in MIDI domain;
#: Nyquist clamps are forbidden per C7).
MIDI_CLAMP_MIN = 0
MIDI_CLAMP_MAX = 127

#: The single declared binary64 shadow site this lane owns.
EXP2_SHADOW_SITE = "vco.midi_to_hz.exp2.shadow"

_MIDI_A440 = 69.0
_SEMITONES_PER_OCTAVE = 12.0


class SineVcoGoldenError(ValueError):
    """Raised when the sine-lane mirror refuses its inputs."""


def digest_words(words) -> str:
    """The #54 trace-digest convention over an integer word list.

    The same canonical form the frozen receipt's per-trace digests use.
    """

    blob = json.dumps(
        list(words), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def initial_phase_word(
    physical_initial_phase: float,
    phase_width: int,
) -> int:
    """The one-time initial-phase injection as a C2 turn word.

    ``turns`` is the binary64 physical value converted exactly as the
    model converts it (``Fraction(float(...))``); the word is
    ``half_even(turns * 2^width)`` reduced modulo ``2^width``.
    """

    if phase_width <= 0:
        raise SineVcoGoldenError(
            "phase width must be positive, got %r" % (phase_width,)
        )
    turns = Fraction(float(physical_initial_phase)) / (2 * Fraction(math.pi))
    modulus = 1 << phase_width
    word = div_round(
        turns.numerator * modulus, turns.denominator, RoundingMode.HALF_EVEN
    )
    return word % modulus


def mirror_sine_lane(
    formats,
    midi_f0_word: int,
    tuning_word: int,
    depth_word: int,
    init_word: int,
    up_pitch: List[int],
    samples: int = None,
    clamp_pitch: bool = True,
) -> Tuple[Dict[str, List[int]], Dict[str, int]]:
    """Exact integer re-walk of the frozen model's sine source lane.

    Returns the ``vco`` (Q2.21 ``vco_1.raw`` words), ``phase`` (post-step
    C2 phase words), and ``fq`` (the per-sample Q16.15 shadow words the
    RTL consumes) streams plus the sticky counters (pitch-sum/depth/S4
    saturation and rounding events) and the measured MIDI clamp count.
    The declared exp2 shadow is replayed host-side (binary64 on the
    quantized control word), exactly the anchor-flow policy.

    ``samples`` caps the walk (a prefix); ``None`` walks the full clip.
    ``clamp_pitch=False`` drops the model's MIDI clamp band from the
    pitch formation — the tb's un-clamped-pitch mutation demonstration
    only: a committed run must never disable it.
    """

    audio = formats.audio
    midi_fmt = formats.midi
    entry_fmt = formats.table.spec.entry_format
    midi_scale = float(midi_fmt.scale)
    frequency_scale = int(formats.frequency.scale)
    freq_denominator = frequency_scale * AUDIO_RATE_HZ
    modulus = 1 << formats.phase_width
    clamp_max_word = MIDI_CLAMP_MAX << midi_fmt.frac_bits

    counters = StickyCounters()
    phase = init_word % modulus
    vco: List[int] = []
    phases: List[int] = []
    fqs: List[int] = []
    clamps = 0
    total = len(up_pitch) if samples is None else min(samples, len(up_pitch))
    for n in range(total):
        product = mul(
            depth_word, midi_fmt, up_pitch[n], audio, midi_fmt,
            formats.mode, OverflowPolicy.SATURATE, counters, "sine.depth_mod",
        )
        pitch_sum = apply_policy(
            midi_f0_word + tuning_word + product,
            midi_fmt, OverflowPolicy.SATURATE, counters, "sine.pitch_sum",
        )
        if clamp_pitch:
            if pitch_sum < MIDI_CLAMP_MIN:
                pitch_sum = MIDI_CLAMP_MIN
                clamps += 1
            elif pitch_sum > clamp_max_word:
                pitch_sum = clamp_max_word
                clamps += 1
        # Declared shadow site (binary64 on the quantized control word).
        hz = 440.0 * math.exp2(
            (pitch_sum / midi_scale - _MIDI_A440) / _SEMITONES_PER_OCTAVE
        )
        freq_word = shadow_half_even(hz * float(frequency_scale))
        increment = div_round(
            freq_word * modulus, freq_denominator, RoundingMode.HALF_EVEN
        )
        phase = (phase + increment) % modulus
        word = rescale(
            formats.table.evaluate(phase, formats.mode),
            entry_fmt, audio, formats.mode,
            OverflowPolicy.SATURATE, counters, "sine.S4",
        )
        vco.append(word)
        phases.append(phase)
        fqs.append(freq_word)

    tally = counters.as_json()
    return (
        {"vco": vco, "phase": phases, "fq": fqs},
        {"counters": tally, "clamps": clamps},
    )


def derive_case(formats, physical: Dict[str, float], samples: int = None):
    """Stimulus + mirror truth for one case from its physical parameters.

    The pitch column and the keyboard word come from the frozen
    composition's own control path (``FixedControlPath.render_words`` --
    the exact class ``FixedVoiceModel`` instantiates); the S1 entry
    words use the model's own entry sites; the lane re-walk is
    :func:`mirror_sine_lane`. Callers prove model-equality by digest
    against committed ``vco_1.raw`` evidence (the frozen receipt's
    per-case trace digests, or a fresh ``FixedVoiceModel`` render for
    the directed regime cases) -- this helper never *is* the truth.

    ``samples`` caps the walk (a prefix) for the fast checks.
    """

    from .format_sweep import FixedControlPath

    counters = StickyCounters()
    control = FixedControlPath(formats.control_spec)
    control_words = control.render_words(physical)
    words = {
        "keyboard.midi_f0": control_words["keyboard.midi_f0"][0],
        "vco_1.tuning": entry_quantize(
            float(physical["vco_1.tuning"]), formats.midi, counters,
            "s1.entry:vco_1.tuning",
        ),
        "vco_1.mod_depth": entry_quantize(
            float(physical["vco_1.mod_depth"]), formats.midi, counters,
            "s1.entry:vco_1.mod_depth",
        ),
    }
    init_word = initial_phase_word(
        physical["vco_1.initial_phase"], formats.phase_width
    )
    up_pitch = control_words["control_upsample.vco_1_pitch"]
    streams, aux = mirror_sine_lane(
        formats,
        words["keyboard.midi_f0"],
        words["vco_1.tuning"],
        words["vco_1.mod_depth"],
        init_word,
        up_pitch,
        samples=samples,
    )
    return {
        "words": words,
        "init_word": init_word,
        "up_pitch": up_pitch,
        "matrix_pitch": control_words["mod_matrix.vco_1_pitch"],
        "streams": streams,
        "clamps": aux["clamps"],
        "counters": aux["counters"],
    }
