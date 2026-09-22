"""Golden-vector derivation and host mirror for the LFO + control-VCA engine (#71).

The bit-exactness target is the frozen fixed model: every golden value here
is produced by the frozen composition's own control path
(``FixedControlPath``, the class the frozen whole-voice model instantiates;
``src/torchsynth_voice/format_sweep.py``) — never by a parallel
implementation. This module adds only:

- :func:`weight_shadow` — the declared binary64 shadow replay of the LFO
  shape weights (``w ** 2.718281828`` with ``math.fsum`` normalization and
  the all-zero refusal), narrowed into the Q2.30 shape domain exactly like
  ``FixedControlPath._lfo``'s own weight block;
- :func:`init_word` — the declared S3-style initial-turn formation
  (initial-phase entry over the pinned binary64 ``2 * PINNED_PI``, half-
  even u32 word), formed host-side and received in advance by the RTL,
  like #70's S1/S2 length words;
- :func:`mirror_lfo` — an exact integer re-walk of ``_lfo``'s per-tick
  loop (rate formation, zero clamp, increment site, u32 wrapping phase,
  five-shape blend, declared narrowing) that asserts row-equality against
  ``FixedControlPath._lfo``, so the host mirror is provably the model, not
  a harness-parallel copy; it also returns the sticky rate-clamp count
  the RTL's exported ``op_clamps`` counter must reproduce;
- :func:`mirror_vca` — the control-rate VCA through the model's own
  :func:`torchsynth_voice.fixedpoint.ops.mul` primitive, asserted equal
  to ``FixedControlPath._control_vca``.

The RTL engine (``tb/sv/lfo_vca_engine.sv``) consumes the formed words,
the five weight words, and the per-tick envelope streams, computes
everything else in exact integer arithmetic, and must reproduce the
model's ``lfo_<n>.raw`` and ``lfo_<n>.post_control_vca`` traces
bit-exactly.

No RTL claim of any kind is made here; the binary64 shadow site remains
an open approximation item (the #74 declaration class).
"""

from __future__ import annotations

import math
from fractions import Fraction
from typing import Dict, List, Tuple

from .control_path import LFO_EXPONENT, LFO_SHAPES, PINNED_TWO_PI
from .float_interfaces import CONTROL_SAMPLES
from .format_sweep import (
    CONTROL_RATE_INT,
    LENGTH_FORMAT,
    LUT_PHASE_BITS,
    SHAPE_FORMAT,
    SHAPE_ONE,
    FixedControlPath,
    UndefinedControlState,
    apply_saturation,
)
from .fixedpoint.counters import StickyCounters
from .fixedpoint.ops import OverflowPolicy, mul
from .fixedpoint.rounding import RoundingMode, div_round_reported

__all__ = [
    "LFO_SIDES",
    "raw_trace",
    "vca_trace",
    "weight_shadow",
    "init_word",
    "mirror_lfo",
    "mirror_vca",
    "mirror_blend",
    "phase_modulus",
    "UndefinedLFOState",
]

#: The two registry LFO instances this issue owns (rate/amp ADSR outputs
#: feeding them are the #70 engines' declared outputs).
LFO_SIDES = ("lfo_1.", "lfo_2.")

#: The model's own undefined-state refusal, re-exported for the refusal test.
UndefinedLFOState = UndefinedControlState


def raw_trace(side: str) -> str:
    """The registry trace name of one LFO's raw blend output."""

    return side + "raw"


def vca_trace(side: str) -> str:
    """The registry trace name of one LFO's post-control-VCA output."""

    return side + "post_control_vca"


def phase_modulus() -> int:
    """The C2 u32 phase circle (units per turn)."""

    return 1 << LUT_PHASE_BITS


def weight_shadow(
    fcp: FixedControlPath, words: Dict[str, int], side: str
) -> List[int]:
    """The five normalized Q2.30 shape-weight words, in model order.

    Re-walks ``FixedControlPath._lfo``'s declared binary64 shadow block
    (``format_sweep.py:459-471``) operation for operation: the entry
    decode, ``w ** LFO_EXPONENT`` (the literal 2.718281828), ``math.fsum``
    normalization with the all-zero ``UndefinedControlState`` refusal, and
    the model's own ``_narrow`` into the Q2.30 shape domain. Fed to the
    RTL as per-trigger words; any drift anywhere fails the end-to-end
    vector match and the :func:`mirror_lfo` row assertion.
    """

    weights_binary64 = []
    for shape in LFO_SHAPES:
        weight_value = float(fcp._entry(words, side + shape))
        weights_binary64.append(weight_value**LFO_EXPONENT)
    total = math.fsum(weights_binary64)
    if total == 0.0:
        raise UndefinedControlState(
            "undefined LFO shape state: all-zero shape weights for " + side
        )
    return [
        fcp._narrow(Fraction(w / total), SHAPE_FORMAT, "s4.lfo.weight." + side)
        for w in weights_binary64
    ]


def init_word(fcp: FixedControlPath, words: Dict[str, int], side: str) -> int:
    """The declared S3-style initial-turn word (host-formed site).

    Mirrors ``format_sweep.py:473-479`` exactly: the initial-phase entry
    over the pinned binary64 ``2 * PINNED_PI`` in exact rational turns,
    half-even rounding onto the u32 phase circle, modular reduction.
    """

    initial_turns = fcp._entry(words, side + "initial_phase") / Fraction(
        PINNED_TWO_PI
    )
    word, _rounded = div_round_reported(
        initial_turns.numerator * (1 << LUT_PHASE_BITS),
        initial_turns.denominator,
        fcp.mode,
    )
    return word % phase_modulus()


def mirror_blend(fcp: FixedControlPath, phase: int, weight_q: List[int]) -> int:
    """``_lfo_blend``'s five pinned shapes at one u32 phase argument.

    Exact integer re-walk of ``format_sweep.py:518-541``: the C5
    quarter-wave table evaluation, the exact Q1.23 -> Q2.30 widening, the
    sign from the table word, the four derived shapes through the
    canonical half-even scalar, the exact Q4.60 weight/shape accumulate,
    and the single declared narrowing.
    """

    mode = fcp.mode
    cos_q23 = fcp.table.evaluate(phase, mode)  # Q1.23
    cos_q30 = cos_q23 << 7  # exact widen Q1.23 -> Q2.30
    sign = (cos_q23 > 0) - (cos_q23 < 0)
    # sin shape = (cos(x + pi) + 1) / 2 = (1 - cos x) / 2.
    sin_q30 = div_round_reported(SHAPE_ONE - cos_q30, 2, mode)[0]
    # saw = (x mod 2pi) / 2pi exactly: the u32 phase over 2^32.
    saw_q30 = div_round_reported(phase << 30, 1 << 32, mode)[0]
    rsaw_q30 = SHAPE_ONE - saw_q30
    # tri = 2*saw, reflected to 2 - 2*saw above the mid turn; t = 1 kept.
    if phase > (1 << 31):
        tri_q30 = div_round_reported((1 << 32) - phase, 2, mode)[0]
    else:
        tri_q30 = div_round_reported(phase, 2, mode)[0]
    # sqr = (1 - sign(cos x)) / 2: 1 where cos < 0, 0.5 at exact zero.
    sqr_q30 = div_round_reported(SHAPE_ONE - sign * SHAPE_ONE, 2, mode)[0]
    shapes = (sin_q30, tri_q30, saw_q30, rsaw_q30, sqr_q30)
    merged = 0
    for weight, shape in zip(weight_q, shapes):
        merged += weight * shape
    # Continuous blend, exact Q4.60 accumulate, one declared narrowing.
    return div_round_reported(merged, SHAPE_ONE, mode)[0]


def mirror_lfo(
    fcp: FixedControlPath,
    words: Dict[str, int],
    side: str,
    rate_env: List[int],
    weight_q: List[int],
) -> Tuple[List[int], int]:
    """``_lfo``'s per-tick loop over externally supplied weight words.

    Re-walks ``format_sweep.py:481-516`` exactly: the rate formation
    narrowing into the Q16.30 length word, the zero clamp with its sticky
    saturation count, the declared increment site, the u32 wrapping phase
    that accumulates the first increment before the initial phase is
    added, the five-shape blend, and the declared narrowing into the C1
    control word. Returns ``(values, clamp_count)``; the values are
    asserted equal to ``fcp._lfo`` — the host mirror is the model, and
    any drift anywhere fails loudly here.
    """

    counters = StickyCounters()
    freq_word = words[side + "frequency"]
    depth_word = words[side + "mod_depth"]
    common = fcp.entry_fmt.scale * fcp.ctrl_fmt.scale
    init = init_word(fcp, words, side)
    modulus = phase_modulus()
    phase = 0
    values: List[int] = []
    clamps = 0
    for index in range(CONTROL_SAMPLES):
        # rate = frequency + mod_depth * rate_envelope: exact integer
        # numerator over `common`, one declared narrowing to Q16.30.
        rate_word = div_round_reported(
            (freq_word * fcp.ctrl_fmt.scale + depth_word * rate_env[index])
            * LENGTH_FORMAT.scale,
            common,
            fcp.mode,
        )[0]
        if not LENGTH_FORMAT.contains(rate_word):
            rate_word = apply_saturation(
                rate_word, LENGTH_FORMAT, "tb.mirror.lfo.rate", counters
            )
        if rate_word < 0:
            # The modulated rate clamps at zero before accumulation.
            rate_word = 0
            clamps += 1
        # Declared increment site: K = round((rate / 441) * 2^32).
        increment = div_round_reported(
            rate_word << (LUT_PHASE_BITS - LENGTH_FORMAT.frac_bits),
            CONTROL_RATE_INT,
            fcp.mode,
        )[0]
        phase = (phase + increment) % modulus
        argument = (phase + init) % modulus
        blended = mirror_blend(fcp, argument, weight_q)
        word = div_round_reported(
            blended * fcp.ctrl_fmt.scale, SHAPE_FORMAT.scale, fcp.mode
        )[0]
        if not fcp.ctrl_fmt.contains(word):
            word = apply_saturation(
                word, fcp.ctrl_fmt, "tb.mirror.lfo." + side, counters
            )
        values.append(word)
    model = fcp._lfo(words, side, rate_env)
    if values != model:
        first = next(
            (i for i, (a, b) in enumerate(zip(values, model)) if a != b), -1
        )
        raise AssertionError(
            "host LFO mirror drifted from the model's _lfo (%s index %d): "
            "mirror %r vs model %r"
            % (side, first, values[first], model[first])
        )
    return values, clamps


def mirror_vca(
    fcp: FixedControlPath, waveform: List[int], gain: List[int]
) -> List[int]:
    """``_control_vca`` through the model's own ``mul`` primitive.

    Asserted equal to ``FixedControlPath._control_vca`` — the same
    single-narrowing product with C6 half-even and C7 saturation.
    """

    counters = StickyCounters()
    values = [
        mul(
            wave,
            fcp.ctrl_fmt,
            gain_value,
            fcp.ctrl_fmt,
            fcp.ctrl_fmt,
            fcp.mode,
            OverflowPolicy.SATURATE,
            counters,
            "tb.mirror.control_vca",
        )
        for wave, gain_value in zip(waveform, gain)
    ]
    model = fcp._control_vca(waveform, gain)
    if values != model:
        first = next(
            (i for i, (a, b) in enumerate(zip(values, model)) if a != b), -1
        )
        raise AssertionError(
            "host control-VCA mirror drifted from the model's "
            "_control_vca (index %d): mirror %r vs model %r"
            % (first, values[first], model[first])
        )
    return values
