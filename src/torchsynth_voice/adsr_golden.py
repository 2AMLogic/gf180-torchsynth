"""Golden-vector derivation and host shadow replay for the ADSR engine (#70).

The bit-exactness target is the frozen fixed model: every golden value here
is produced by the frozen composition's own control path
(``FixedControlPath``, the class the frozen whole-voice model instantiates;
``src/torchsynth_voice/format_sweep.py``) — never by a parallel
implementation. This module adds only:

- :func:`derive_formation` — the stage-formation sites of ``_adsr``
  (S1/S2 entry words, exact rational lengths, quantized Q16.30 length
  words, exact-zero flags), exposed so the tb flow can drive the RTL with
  the model's own formed values (duration received in advance,
  ``spec/VOICE-CONTRACT.md:71``);
- :func:`mirror_ramp` — the declared binary64 shadow replay of the
  ``**alpha`` power: it re-walks ``_ramp``'s exact Q2.60 pre-power
  arithmetic, applies the model's own binary64 pow + half-even narrowing,
  and asserts row-equality against ``FixedControlPath._ramp`` so the host
  mirror is provably the model, not a harness-parallel copy;
- :func:`eps60_word` — the declared Q2.60 shadow epsilon word.

The RTL engine (``tb/sv/adsr_engine.sv``) consumes the formed words + flags
and the per-tick post-power shadow words, computes everything else in exact
integer arithmetic, and must reproduce ``FixedControlPath._adsr``'s output
words bit-exactly.

No RTL claim of any kind is made here; the binary64 shadow sites remain
open approximation items (the #74 declaration class).
"""

from __future__ import annotations

from fractions import Fraction
from typing import Dict, List, Tuple

from .float_interfaces import CONTROL_SAMPLES
from .format_sweep import (
    CONTROL_RATE_INT,
    EPS,
    SHAPE_FORMAT,
    FixedControlPath,
)
from .fixedpoint.counters import StickyCounters
from .fixedpoint.ops import OverflowPolicy, apply_policy
from .fixedpoint.rounding import RoundingMode, div_round_reported

__all__ = [
    "eps60_word",
    "derive_formation",
    "mirror_ramp",
    "quantize_entries",
    "combine_from_shadow",
    "ADSR_PREFIXES",
    "trace_name",
]

ADSR_PREFIXES = (
    "adsr_1.",
    "adsr_2.",
    "lfo_1_rate_adsr.",
    "lfo_2_rate_adsr.",
    "lfo_1_amp_adsr.",
    "lfo_2_amp_adsr.",
)


def quantize_entries(
    physical: Dict[str, float],
    fmt,
    mode: RoundingMode,
    counters: StickyCounters,
) -> Dict[str, int]:
    """S1 entry quantization over a name subset of the consumed scalars.

    Identical math to ``format_sweep.quantize_params`` (stage times into
    the Q16.30 length word, everything else into the MIDI-domain entry
    word), restricted to the names a golden vector carries.
    """

    from .format_sweep import apply_saturation, entry_format_for

    words = {}
    for name, value in physical.items():
        entry_fmt = entry_format_for(name, fmt)
        exact = Fraction(value) * entry_fmt.scale
        word, _rounded = div_round_reported(exact.numerator, exact.denominator, mode)
        if not entry_fmt.contains(word):
            word = apply_saturation(word, entry_fmt, "tb.entry:" + name, counters)
        words[name] = word
    return words


def combine_from_shadow(
    fcp: FixedControlPath,
    attack_row: List[int],
    decay_row: List[int],
    release_row: List[int],
    sustain_q: int,
) -> List[int]:
    """``_adsr``'s declared combine over externally supplied ramp rows.

    Mirrors the model's own loop (factor = (1 - sustain)*decay + sustain
    with one Q4.60 -> Q2.30 narrowing; envelope = attack * factor * release
    with one Q6.90 -> control-word narrowing) so the tb flow can build
    mutated-curve vectors from mutated shadow rows. Against the model's own
    rows this reproduces ``_adsr`` exactly (asserted by the tb flow).
    """

    from .format_sweep import SHAPE_ONE, apply_saturation

    counters = StickyCounters()
    envelope = []
    for attack_value, decay_value, release_value in zip(
        attack_row, decay_row, release_row
    ):
        factor = div_round_reported(
            (SHAPE_ONE - sustain_q) * decay_value + sustain_q * SHAPE_ONE,
            SHAPE_ONE,
            fcp.mode,
        )[0]
        if not SHAPE_FORMAT.contains(factor):
            factor = apply_saturation(
                factor, SHAPE_FORMAT, "tb.combine.factor", counters
            )
        product = attack_value * factor * release_value
        # _narrow_num: round(product * ctrl_fmt.scale / SHAPE.scale**3)
        word = div_round_reported(
            product * fcp.ctrl_fmt.scale, SHAPE_FORMAT.scale**3, fcp.mode
        )[0]
        if not fcp.ctrl_fmt.contains(word):
            word = apply_saturation(word, fcp.ctrl_fmt, "tb.combine.env", counters)
        envelope.append(word)
    return envelope


def trace_name(prefix: str) -> str:
    """The registry trace name of one envelope instance's output."""

    return prefix + "output"


def eps60_word(mode: RoundingMode) -> int:
    """The declared Q2.60 shadow-domain word of the 1e-6 ramp epsilon."""

    scaled = Fraction(EPS) * (1 << 60)
    return div_round_reported(scaled.numerator, scaled.denominator, mode)[0]


def derive_formation(
    fcp: FixedControlPath, words: Dict[str, int], prefix: str
) -> Dict[str, object]:
    """The stage-formation sites of ``_adsr`` for one envelope instance.

    Every value is the model's own computation: entries decode through
    ``FixedControlPath._entry`` (Q16.30 for stage times, C4 Q10.21 for
    sustain/alpha), lengths form as exact rationals with the pinned
    attack/decay cut, and the Q16.30 length words quantize through
    ``_quantized_samples``. The exact-zero flags decide ``_ramp``'s
    zero-length branch on the pre-quantization exact length, exactly like
    the model.
    """

    duration_seconds = fcp._entry(words, "keyboard.duration")
    attack_seconds = fcp._entry(words, prefix + "attack")
    duration_exact = duration_seconds * Fraction(CONTROL_RATE_INT)
    attack_exact = min(attack_seconds, duration_seconds) * Fraction(CONTROL_RATE_INT)
    decay_seconds = max(duration_seconds - attack_seconds, Fraction(0))
    new_decay_exact = (
        min(decay_seconds, fcp._entry(words, prefix + "decay"))
        * Fraction(CONTROL_RATE_INT)
    )
    release_exact = fcp._entry(words, prefix + "release") * Fraction(CONTROL_RATE_INT)
    return {
        "duration_q": fcp._quantized_samples(duration_exact),
        "attack_q": fcp._quantized_samples(attack_exact),
        "decay_q": fcp._quantized_samples(new_decay_exact),
        "release_q": fcp._quantized_samples(release_exact),
        "duration_exact": duration_exact,
        "attack_exact": attack_exact,
        "decay_exact": new_decay_exact,
        "release_exact": release_exact,
        "duration_zero": duration_exact == 0,
        "attack_zero": attack_exact == 0,
        "decay_zero": new_decay_exact == 0,
        "release_zero": release_exact == 0,
        "sustain_q": fcp._to_shape(words[prefix + "sustain"], fcp.entry_fmt),
        "alpha": float(fcp._entry(words, prefix + "alpha")),
    }


def mirror_ramp(
    fcp: FixedControlPath,
    length_q: int,
    length_exact: Fraction,
    start_q: int,
    inverse: bool,
    alpha: float,
) -> Tuple[List[int], List[int]]:
    """One shadow-replayed ramp row: ``(value60_row, shape_word_row)``.

    Re-walks ``FixedControlPath._ramp``'s exact Q2.60 arithmetic (tilt,
    clamp, canonical half-even division, epsilon add, clamp at one,
    conditional inversion, zero/degenerate branches on the exact length),
    then the declared binary64 ``**alpha`` shadow power and the model's own
    half-even narrowing + saturation into the Q2.30 shape domain. The
    result is asserted equal to ``fcp._ramp`` — the host mirror is the
    model, and any drift anywhere fails loudly here.
    """

    mode = fcp.mode
    eps60 = eps60_word(mode)
    start60 = start_q << 30
    len60 = length_q << 30
    exact_zero = length_exact == 0
    degenerate = (not exact_zero) and length_q == 0
    counters = StickyCounters()
    value_row: List[int] = []
    word_row: List[int] = []
    for index in range(CONTROL_SAMPLES):
        x = (index << 60) - start60
        if x < 0:
            x = 0
        if exact_zero or degenerate:
            value60 = 1 << 60
            if degenerate and inverse:
                value60 = 0
        else:
            value60 = div_round_reported((x + eps60) << 60, len60, mode)[0]
            value60 += eps60
            if value60 > (1 << 60):
                value60 = 1 << 60
            elif value60 < 0:
                value60 = 0
            if inverse:
                value60 = (1 << 60) - value60
        powered = (float(value60) * 2.0**-60) ** alpha
        scaled = Fraction(powered) * SHAPE_FORMAT.scale
        word, _rounded = div_round_reported(scaled.numerator, scaled.denominator, mode)
        if not SHAPE_FORMAT.contains(word):
            word = apply_policy(
                word, SHAPE_FORMAT, OverflowPolicy.SATURATE, counters, "tb.shadow.pow"
            )
        value_row.append(value60)
        word_row.append(word)
    model_row = fcp._ramp(length_q, length_exact, start_q, inverse, alpha)
    if word_row != model_row:
        raise AssertionError(
            "host shadow replay drifted from the model's _ramp row "
            "(prefix-length %d/%s): mirror %r vs model %r"
            % (length_q, str(length_exact), word_row[:4], model_row[:4])
        )
    return value_row, word_row
