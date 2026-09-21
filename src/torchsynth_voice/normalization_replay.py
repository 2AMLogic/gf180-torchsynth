"""Fixed-path whole-clip normalization replay: the DR-0003 acceptance measurement.

Issue #52 delivers the measurement DR-0003 names before acceptance
(``spec/decision-records/0003-host-boundary-and-normalization.md:40-44``:
"division/reciprocal precision and output arithmetic") and the DR-0008
Section 8 / choice C9 reciprocal-precision measurement. Semantics are fixed
by DR-0003 and are not re-decided here: render once to find the peak, replay
at gain ``1 / peak`` if the peak exceeds one, otherwise unity. The limiter,
AGC and constant-headroom substitutes DR-0003 forbids appear only as
preregistered negative controls that must fail.

Numeric mechanics (DR-0008 Section 8, C6/C9 candidates):

- the peak is measured on the 24-bit Q2.21 pre-normalization mix;
- the strict branch condition is ``peak > 1`` in the Q2.21 domain, i.e.
  ``peak_int > 2^21`` (peak exactly one, silence, every peak at or below one
  bypass with the input bytes unchanged);
- site S5 is the declared reciprocal/gain application narrowing, rounding
  half-even (C6);
- two candidate application methods are measured: ``direct-division`` (each
  output sample is ``round(x * 2^21 / peak_int)`` -- one exact integer
  division, one declared rounding) and ``reciprocal-multiply`` (a
  declared-precision gain word ``round(2^F / peak_real)`` formed once per
  clip, then one multiply and one declared narrowing per sample), swept over
  candidate fractional widths ``F``;
- the float reference is the landed ``float-mix-v1`` normalization semantics
  (``torchsynth_voice.float_mix.normalize_if_clipping``) evaluated on the
  exact binary32 dequantization of the same Q2.21 clip. Every Q2.21 value is
  exactly representable in binary32, so the two grids share inputs exactly.

Comparison rows follow the primary paired rows of
``docs/MEASUREMENT-PLAN.md`` (sample count, first divergent sample, maximum
absolute error and index, mean error, RMSE, SNR with an explicit silent
result, reference/candidate peaks, saturation count, normalization gain).
Band-power rows are not emitted: the directed clips here are static level
fixtures, not pitched signals, so a preregistered frequency band is not
applicable to them; the banded M1 ladder stays owned by DR-0008 Section 10.

This module makes NO RTL claim and accepts nothing: DR-0003 and DR-0008 both
remain Proposed and the measured decision lands as a
candidate-pending-ratification evidence package. Cycle, SRAM and energy cost
of replay versus buffering belong to the architecture DR (issue #63) and are
not measured here.
"""

from __future__ import annotations

import hashlib
import json
import math
from fractions import Fraction
from typing import Dict, List, Optional, Sequence, Tuple

from . import float_mix as fm
from .fixedpoint import (
    FixedFormat,
    RoundingMode,
    StickyCounters,
    div_round,
    saturate,
)

# C1 candidate audio word: 24-bit Q2.21 [-4, +4), LSB 2^-21. Candidate
# instantiation data from spec/reference/fixedpoint-choices-v1.json; status
# "selected (operator ruling 2026-09-19); pending ratification", never
# accepted while DR-0008 is Proposed.
AUDIO_FORMAT = FixedFormat(signed=True, int_bits=2, frac_bits=21)
AUDIO_SCALE = AUDIO_FORMAT.scale  # 2^21
UNITY_INT = 1 << AUDIO_FORMAT.frac_bits  # the Q2.21 integer for exactly 1.0
HALF_OUTPUT_LSB = Fraction(1, 1 << (AUDIO_FORMAT.frac_bits + 1))  # 2^-22

# Declared rounding at the S5 reciprocal/gain application site (C6 candidate:
# half-even at S1-S5).
S5_SITE = "S5"
S5_MODE = RoundingMode.HALF_EVEN

# Candidate reciprocal fractional widths (gain word is unsigned U1.F: it must
# hold (0.25, 1.0], and rounding can push the minimal-divisor reciprocal up to
# exactly 2^F). The sweep measures the error knee; widths are data, not a
# decision.
RECIPROCAL_FRAC_WIDTHS = (12, 14, 16, 18, 20, 21, 22, 24)

# Release-era scale anchors (spec/decision-records/0006-canonical-runtime.md:
# 89), one per branch: quiet anchors the bypass branch, loud the divide
# branch.
ANCHOR_QUIET = "0.7895715833"
ANCHOR_LOUD = "3.9478583336"

# Draft DR-0008 Section 10 M1 strictest band (f0 <= 1 kHz): E <= 2^-13. The
# decision input reports the calibrated threshold per C10 (smallest power of
# two >= 2x the measured maximum); a draft band number is a comparison
# reference, never a ratified rubric threshold.
DRAFT_STRICTEST_BAND = Fraction(1, 1 << 13)

METHOD_DIRECT = "direct-division"
METHOD_RECIPROCAL = "reciprocal-multiply"

# Directed clips are full declared clips (176,400 samples at 44.1 kHz,
# DR-0008 Section 9): the float reference validates full-clip buffers and the
# whole-clip peak is a full-clip quantity.
CLIP_SAMPLES = fm.AUDIO_SAMPLES


class NormalizationReplayError(ValueError):
    """Raised on contract violations of the fixed replay measurement."""


def round_half_even(value: Fraction) -> int:
    """Round an exact rational to the nearest integer, halves to even.

    Local exact helper: the canonical fixedpoint rounding scalar operates on
    integers only, and anchor quantization starts from exact decimal
    rationals.
    """
    floor_value = value.numerator // value.denominator
    remainder = value - floor_value
    if remainder < Fraction(1, 2):
        return floor_value
    if remainder > Fraction(1, 2):
        return floor_value + 1
    return floor_value + (floor_value & 1)


def dequantize_q21(value: int) -> float:
    """Exact binary32 dequantization of a Q2.21 integer (always exact)."""
    if not AUDIO_FORMAT.contains(value):
        raise NormalizationReplayError(f"value {value} outside {AUDIO_FORMAT.identity}")
    return value / AUDIO_SCALE


def fixed_peak(signal: Sequence[int], tie: str = "earliest") -> Tuple[int, int]:
    """Whole-clip absolute maximum on the Q2.21 grid and its recorded index.

    Earliest maximal index (pinned ``torch.max`` semantics); ``tie="latest"``
    is a preregistered negative control only and must fail the index row.
    """
    peak = -1
    index = -1
    for position, value in enumerate(signal):
        magnitude = -value if value < 0 else value
        if magnitude > peak or (tie == "latest" and magnitude == peak):
            peak = magnitude
            index = position
    if peak < 0:
        raise NormalizationReplayError("peak scan requires a non-empty signal")
    return peak, index


def branch_decide(peak_int: int) -> bool:
    """Strict ``peak > 1`` on the Q2.21 measurement grid (DR-0003)."""
    return peak_int > UNITY_INT


def gain_format(frac_bits: int) -> FixedFormat:
    """The unsigned U1.F gain word format for a candidate precision."""
    return FixedFormat(signed=False, int_bits=1, frac_bits=frac_bits)


def reciprocal_word(peak_int: int, frac_bits: int) -> int:
    """Declared-precision reciprocal ``r = round(2^F / peak_real)`` (site S5).

    Exact integer form: ``round(2^(21+F) / peak_int)``, half-even. The word is
    interpreted in unsigned U1.F (value ``r / 2^F``), which holds the whole
    divide-branch range ``(0.25, 1.0]`` including the rounding edge case
    ``r = 2^F`` (exactly 1.0) at the minimal divisor.
    """
    if not branch_decide(peak_int):
        raise NormalizationReplayError(
            "reciprocal word is defined on the strict divide branch only"
        )
    return div_round(AUDIO_SCALE << frac_bits, peak_int, S5_MODE)


def apply_direct_division(
    signal: Sequence[int], peak_int: int, counters: Optional[StickyCounters] = None
) -> List[int]:
    """Method ``direct-division``: ``round(x * 2^21 / peak_int)`` per sample.

    One exact integer division per sample with a single declared rounding at
    site S5, then saturation into Q2.21 (a no-op on every well-formed
    divide-branch clip: the output magnitude never exceeds ``2^21``). The
    effective per-sample gain is the exactly rounded real ``1 / peak_real``
    at full Q2.21 output resolution.
    """
    if not branch_decide(peak_int):
        raise NormalizationReplayError("direct division requires peak > 1")
    return [
        saturate(
            div_round(value * AUDIO_SCALE, peak_int, S5_MODE),
            AUDIO_FORMAT,
            counters,
            S5_SITE,
        )
        for value in signal
    ]


def apply_reciprocal_multiply(
    signal: Sequence[int],
    peak_int: int,
    frac_bits: int,
    counters: Optional[StickyCounters] = None,
) -> List[int]:
    """Method ``reciprocal-multiply`` at fractional width ``F``.

    The gain word is formed once per clip (:func:`reciprocal_word`), then each
    output is the exact integer product narrowed once to Q2.21 at site S5,
    half-even, with saturation. This is the replay shape a hardware
    reciprocal implies; the per-clip reciprocal cost and any table
    organization belong to the architecture DR (issue #63).
    """
    gain = reciprocal_word(peak_int, frac_bits)
    return [
        saturate(
            div_round(value * gain, 1 << frac_bits, S5_MODE),
            AUDIO_FORMAT,
            counters,
            S5_SITE,
        )
        for value in signal
    ]


def replay_fixed(
    signal: Sequence[int],
    method: str = METHOD_DIRECT,
    frac_bits: Optional[int] = None,
) -> Dict:
    """Deterministic fixed-path normalization replay of one Q2.21 clip.

    Returns ``output`` (Q2.21 integers), the integer ``peak`` and its
    ``peak_index``, the ``branch`` decision, the ``gain_word`` and its format
    identity (unity on the bypass branch), the sticky saturation ``counters``
    at site S5, and the ``method``/``frac_bits`` provenance. A pure integer
    function of the input: a repeated request reproduces byte-identical
    outputs.
    """
    peak, index = fixed_peak(signal)
    divide = branch_decide(peak)
    if method == METHOD_DIRECT:
        if frac_bits is not None:
            raise NormalizationReplayError("direct division takes no gain width")
    elif method == METHOD_RECIPROCAL:
        if frac_bits is None:
            raise NormalizationReplayError("reciprocal multiply requires a gain width")
    else:
        raise NormalizationReplayError(f"unknown method: {method}")
    counters = StickyCounters()
    if divide:
        if method == METHOD_DIRECT:
            output = apply_direct_division(signal, peak, counters)
            gain_word = None
            gain_identity = None
        else:
            output = apply_reciprocal_multiply(signal, peak, frac_bits, counters)
            gain_word = reciprocal_word(peak, frac_bits)
            gain_identity = gain_format(frac_bits).identity
    else:
        output = list(signal)
        gain_word = None
        gain_identity = None
    return {
        "output": output,
        "peak": peak,
        "peak_index": index,
        "branch": "divide" if divide else "bypass",
        "gain_word": gain_word,
        "gain_format": gain_identity,
        "gain_real": (
            float(Fraction(gain_word, 1 << frac_bits))
            if gain_word is not None
            else 1.0
        ),
        "method": method,
        "frac_bits": frac_bits,
        "saturation_total": counters.total(),
    }


# ---------------------------------------------------------------------------
# Directed Q2.21 cases
# ---------------------------------------------------------------------------


def _body() -> List[int]:
    """A deterministic low-level body pattern that never approaches unity."""
    pattern = [3, -11, 42, -7, 19, -23, 8, -15, 31, -5, 12, -27]
    return [pattern[i % len(pattern)] for i in range(CLIP_SAMPLES)]


def clip_with_peak(peak_int: int, index: int = 21) -> List[int]:
    """A deterministic full-clip Q2.21 signal with unique maximum ``peak_int``.

    The tiled body stays three decades below unity so the inserted peak is
    unique; callers exercise ties explicitly with :func:`clip_tied_max`.
    """
    if peak_int < 0 or peak_int > AUDIO_FORMAT.max_int:
        raise NormalizationReplayError(f"peak {peak_int} outside Q2.21")
    clip = _body()
    clip[index % CLIP_SAMPLES] = peak_int
    return clip


def clip_tied_max(peak_int: int) -> List[int]:
    """A clip with two equal absolute maxima (earliest index must win)."""
    clip = _body()
    clip[9] = peak_int
    clip[40000] = peak_int
    return clip


def anchor_peak_int(anchor_decimal: str) -> Tuple[int, Fraction]:
    """Nearest Q2.21 integer to a decimal release anchor, half-even, exact.

    Returns the integer and the exact quantization offset
    ``anchor - integer/2^21`` so the measurement reports the grid effect
    instead of hiding it.
    """
    exact = Fraction(anchor_decimal) * AUDIO_SCALE
    integer = round_half_even(exact)
    offset = Fraction(anchor_decimal) - Fraction(integer, AUDIO_SCALE)
    return integer, offset


DIRECTED_DECIMAL_ANCHORS = {
    "fixed:anchor-quiet": ANCHOR_QUIET,
    "fixed:anchor-loud": ANCHOR_LOUD,
}


def directed_cases() -> Dict[str, List[int]]:
    """The directed Q2.21 normalization case grid (below/at/above/extrema).

    Covers the smallest dividing peak, the unity tie, the largest bypassing
    peak, the Q2.21 extremum, silence, a late unique peak, tied maxima, and
    (in :func:`anchored_cases`) the two release anchors quantized onto the
    Q2.21 grid, one per branch.
    """
    return {
        # smallest peak that divides (exactly 1 + 2^-21)
        "fixed:above-one-min": clip_with_peak(UNITY_INT + 1),
        # exactly one: bypass
        "fixed:tie": clip_with_peak(UNITY_INT),
        # largest peak that bypasses (exactly 1 - 2^-21)
        "fixed:below-one-max": clip_with_peak(UNITY_INT - 1),
        # Q2.21 extremum: the largest divisor (just under 4)
        "fixed:extremum": clip_with_peak(AUDIO_FORMAT.max_int),
        # silence: bypass, unity gain, no division by zero
        "fixed:silence": [0] * CLIP_SAMPLES,
        # unique peak at the last sample: index semantics on the divide branch
        "fixed:late-peak": clip_with_peak(UNITY_INT + 12345, CLIP_SAMPLES - 1),        # two equal maxima: earliest index wins, branch decided once
        "fixed:tied-max": clip_tied_max(UNITY_INT + 777),
    }


def anchored_cases() -> Dict[str, Tuple[List[int], Fraction]]:
    """Anchor-scaled directed cases with their exact quantization offsets."""
    cases: Dict[str, Tuple[List[int], Fraction]] = {}
    for name, decimal in DIRECTED_DECIMAL_ANCHORS.items():
        integer, offset = anchor_peak_int(decimal)
        cases[name] = (clip_with_peak(integer), offset)
    return cases


# ---------------------------------------------------------------------------
# Float reference and paired comparison rows
# ---------------------------------------------------------------------------


def reference_float(signal: Sequence[int]) -> Dict:
    """The landed float-mix normalization semantics on the same clip.

    The Q2.21 clip dequantizes exactly into binary32, so the float reference
    consumes identical sample values: ``float_mix.normalize_if_clipping``
    supplies the whole-clip binary32 peak, the derived binary32 reciprocal
    diagnostic, the strict branch, and the elementwise binary32 division
    output.
    """
    floats = [dequantize_q21(value) for value in signal]
    output, peak, gain, branch = fm.normalize_if_clipping(floats)
    return {"output": output, "peak": peak, "gain": gain, "branch": branch}


def _rms(values: Sequence[float]) -> float:
    return math.sqrt(sum(value * value for value in values) / len(values))


def compare_rows(
    case_id: str,
    fixed_out: Sequence[int],
    reference: Dict,
    replay: Dict,
) -> Dict:
    """Primary paired rows (``docs/MEASUREMENT-PLAN.md``) for one comparison.

    Errors are exact rationals: the fixed sample is ``int / 2^21`` and the
    reference is a binary32 float, so ``Fraction`` subtraction never rounds.
    A silent reference reports the explicit ``silent`` SNR result the plan
    requires instead of a division by zero.
    """
    if len(fixed_out) != len(reference["output"]):
        raise NormalizationReplayError("length mismatch against the float reference")
    errors = [
        Fraction(fixed_value, AUDIO_SCALE) - Fraction(reference_value)
        for fixed_value, reference_value in zip(fixed_out, reference["output"])
    ]
    abs_errors = [abs(error) for error in errors]
    max_abs = max(abs_errors)
    max_index = abs_errors.index(max_abs)
    first_divergent = next(
        (position for position, error in enumerate(errors) if error != 0),
        None,
    )
    mean_error = sum(errors, Fraction(0)) / len(errors)
    error_rms = _rms([float(error) for error in errors])
    signal_rms = _rms([float(value) for value in reference["output"]])
    if signal_rms == 0.0:
        snr = "silent"
    elif error_rms == 0.0:
        snr = "infinite"
    else:
        snr = 20.0 * math.log10(signal_rms / error_rms)
    reference_peak, reference_index = fm.peak_of_clip(reference["output"])
    candidate_peak, candidate_index = fixed_peak(fixed_out)
    return {
        "case": case_id,
        "sample_count": len(fixed_out),
        "first_divergent_sample": first_divergent,
        "max_abs_error": float(max_abs),
        "max_abs_error_exact": f"{max_abs.numerator}/{max_abs.denominator}",
        "max_abs_error_index": max_index,
        "mean_error": float(mean_error),
        "rmse": error_rms,
        "signal_rms": signal_rms,
        "snr_db": snr,
        "reference_peak": reference_peak,
        "reference_peak_index": reference_index,
        "candidate_peak": dequantize_q21(candidate_peak),
        "candidate_peak_index": candidate_index,
        "normalization_gain": replay["gain_real"],
        "branch": replay["branch"],
        "reference_branch": "divide" if reference["branch"] else "bypass",
        "saturation_total": replay["saturation_total"],
    }


def gain_word_rows(peak_int: int, frac_bits: int) -> Dict:
    """Exact reciprocal-word error rows for one divide-branch grid point.

    Reports the gain word's distance to the exact real reciprocal and to the
    float ``mixer.gain`` diagnostic ``f32(1/peak)`` -- the DR-0008 C9 trace
    relation -- both as exact rationals.
    """
    peak_real = Fraction(peak_int, AUDIO_SCALE)
    gain = reciprocal_word(peak_int, frac_bits)
    gain_real = Fraction(gain, 1 << frac_bits)
    exact_reciprocal = 1 / peak_real
    float_gain = fm.derived_gain(dequantize_q21(peak_int))
    error_exact = gain_real - exact_reciprocal
    error_float = gain_real - Fraction(float_gain)
    return {
        "peak_int": peak_int,
        "peak_real": float(peak_real),
        "frac_bits": frac_bits,
        "gain_word": gain,
        "gain_real": float(gain_real),
        "exact_reciprocal": float(exact_reciprocal),
        "float_gain_f32": float_gain,
        "error_vs_exact": float(error_exact),
        "error_vs_exact_exact": f"{error_exact.numerator}/{error_exact.denominator}",
        "error_vs_float_gain": float(error_float),
        "error_vs_float_gain_exact": f"{error_float.numerator}/{error_float.denominator}",
    }


# ---------------------------------------------------------------------------
# Sweep and decision rows
# ---------------------------------------------------------------------------


def _calibrated_threshold(measured: Fraction) -> Fraction:
    """C10 rule: smallest power of two >= 2x the measured maximum."""
    target = measured * 2
    power = 0
    while Fraction(1, 1 << power) > target:
        power += 1
    return Fraction(1, 1 << power)


def run_sweep() -> Dict:
    """The full directed reciprocal-precision sweep (deterministic).

    Every directed case is replayed by every candidate method/precision and
    compared against the float reference; bypass rows compare the untouched
    bytes, divide rows quantify the application error. Bypass comparisons are
    byte-exact by construction and still reported as rows so the coverage
    claim is checkable. Deterministic: identical inputs produce identical
    rows.
    """
    rows: List[Dict] = []
    gain_rows: List[Dict] = []

    def emit(case_id: str, clip: Sequence[int]) -> None:
        reference = reference_float(clip)
        peak, _ = fixed_peak(clip)
        if branch_decide(peak):
            rows.append(
                compare_rows(
                    f"{case_id}[direct-division]",
                    apply_direct_division(clip, peak),
                    reference,
                    replay_fixed(clip, METHOD_DIRECT),
                )
            )
            for frac_bits in RECIPROCAL_FRAC_WIDTHS:
                replay = replay_fixed(clip, METHOD_RECIPROCAL, frac_bits)
                rows.append(
                    compare_rows(
                        f"{case_id}[reciprocal-multiply F={frac_bits}]",
                        replay["output"],
                        reference,
                        replay,
                    )
                )
                gain_rows.append(gain_word_rows(peak, frac_bits))
        else:
            replay = replay_fixed(clip, METHOD_DIRECT)
            rows.append(
                compare_rows(f"{case_id}[bypass]", replay["output"], reference, replay)
            )

    for case_id, clip in sorted(directed_cases().items()):
        emit(case_id, clip)
    for case_id, (clip, offset) in sorted(anchored_cases().items()):
        peak, _ = fixed_peak(clip)
        reference = reference_float(clip)
        rows.append(
            {
                "case": f"{case_id}[anchor-quantization]",
                "anchor_decimal": DIRECTED_DECIMAL_ANCHORS[case_id],
                "anchor_peak_int": peak,
                "anchor_peak_fixed_real": dequantize_q21(peak),
                "anchor_quantization_offset": float(offset),
                "anchor_quantization_offset_exact": f"{offset.numerator}/{offset.denominator}",
                "branch": "divide" if branch_decide(peak) else "bypass",
                "reference_peak": reference["peak"],
                "float_gain_f32": reference["gain"],
            }
        )
        emit(case_id, clip)
    return {"rows": rows, "gain_word_rows": gain_rows}


def _method_label(case_id: str) -> Optional[str]:
    if "[direct-division]" in case_id:
        return "direct-division"
    if "[reciprocal-multiply F=" in case_id:
        return "reciprocal-multiply F=" + case_id.split("F=")[1].rstrip("]")
    return None


def decision_rows(sweep: Dict) -> Dict:
    """The measured decision input for DR-0003 acceptance.

    Aggregates the sweep into one worst-case measured output error per
    candidate method/precision across every divide-branch row, applies the
    C10 calibration rule, and compares each calibrated threshold against the
    draft strictest M1 band (2^-13) as the margin reference.

    The recommendation rule, fixed before the numbers were read: the
    recommended precision is the smallest swept width satisfying BOTH
    (i) its C10 calibrated threshold sits at or below the draft strictest
    M1 band, and (ii) its worst-case measured output error is within half an
    output LSB (2^-22) of the direct-division floor -- the point where the
    Q2.21 output word, not the gain word, dominates the error and a wider
    reciprocal stops improving the output.
    """
    worst: Dict[str, Tuple[Fraction, str]] = {}
    for row in sweep["rows"]:
        label = _method_label(row["case"])
        if label is None:
            continue
        measured = Fraction(row["max_abs_error_exact"])
        current = worst.get(label)
        if current is None or measured > current[0]:
            worst[label] = (measured, row["case"])
    floor_label = "direct-division"
    floor, floor_case = worst[floor_label]
    band_check = {}
    for label, (measured, case_id) in sorted(worst.items()):
        threshold = _calibrated_threshold(measured)
        band_check[label] = {
            "measured_worst_case": float(measured),
            "measured_worst_case_exact": f"{measured.numerator}/{measured.denominator}",
            "worst_case_at": case_id,
            "calibrated_threshold": float(threshold),
            "calibrated_threshold_exact": f"{threshold.numerator}/{threshold.denominator}",
            "meets_draft_band_with_margin": threshold <= DRAFT_STRICTEST_BAND,
            "within_half_lsb_of_floor": measured <= floor + HALF_OUTPUT_LSB,
        }
    qualifying = [
        frac_bits
        for frac_bits in RECIPROCAL_FRAC_WIDTHS
        if band_check.get(f"reciprocal-multiply F={frac_bits}", {}).get(
            "meets_draft_band_with_margin"
        )
        and band_check[f"reciprocal-multiply F={frac_bits}"][
            "within_half_lsb_of_floor"
        ]
    ]
    recommended = qualifying[0] if qualifying else None
    return {
        "status": "candidate-pending-ratification",
        "draft_band_reference": (
            "DR-0008 Section 10 M1 strictest draft band E <= 2^-13; a draft "
            "number recorded in the DR text, never a ratified threshold"
        ),
        "calibration_rule": "C10: smallest power of two >= 2x the measured maximum",
        "direct_division_floor": float(floor),
        "direct_division_floor_exact": f"{floor.numerator}/{floor.denominator}",
        "direct_division_floor_at": floor_case,
        "recommendation_rule": (
            "smallest swept width with calibrated threshold at or below the "
            "draft strictest M1 band AND worst-case error within half an "
            "output LSB (2^-22) of the direct-division floor"
        ),
        "recommended_reciprocal_frac_bits": recommended,
        "recommended_gain_word": (
            f"unsigned U1.{recommended} (width {recommended + 1} bits)"
            if recommended is not None
            else None
        ),
        "band_check": band_check,
    }


def build_receipt(generated_utc: str) -> Dict:
    """Assemble the committed evidence receipt for the sweep."""
    sweep = run_sweep()
    decision = decision_rows(sweep)
    directed = directed_cases()
    anchored = anchored_cases()
    coverage = {}
    for case_id, clip in sorted({**directed, **{k: v[0] for k, v in anchored.items()}}.items()):
        peak, index = fixed_peak(clip)
        coverage[case_id] = {
            "peak_int": peak,
            "peak_real": dequantize_q21(peak),
            "peak_index": index,
            "branch": "divide" if branch_decide(peak) else "bypass",
        }
    return {
        "schema": "gf180-torchsynth/normalization-reciprocal-v1",
        "schema_version": 1,
        "status": "candidate-pending-ratification",
        "issue": 52,
        "generated_utc": generated_utc,
        "producer": {
            "module": "torchsynth_voice.normalization_replay",
            "tool": "tools/measure_normalization_reciprocal.py",
            "float_reference_policy": "float-mix-v1 (spec/FLOAT-MIX.md)",
            "dr_0003_status": "Proposed",
            "dr_0008_status": "Proposed",
        },
        "provenance": {
            "audio_format": {
                "identity": AUDIO_FORMAT.identity,
                "choice": "C1 candidate (selected operator ruling 2026-09-19; pending ratification)",
            },
            "rounding": {
                "mode": S5_MODE.value,
                "site": S5_SITE,
                "choice": "C6 candidate (selected operator ruling 2026-09-19; pending ratification)",
            },
            "unity_int": UNITY_INT,
            "reciprocal_frac_widths": list(RECIPROCAL_FRAC_WIDTHS),
            "gain_word_format": "unsigned U1.F (one integer bit + F fractional bits)",
            "anchor_decimals": dict(DIRECTED_DECIMAL_ANCHORS),
            "float_reference": (
                "float_mix.normalize_if_clipping on the exact binary32 "
                "dequantization of the same Q2.21 clip (every Q2.21 value is "
                "exactly representable in binary32)"
            ),
            "peak_grid_note": (
                "the peak is measured on the Q2.21 pre-normalization mix per "
                "DR-0008 Section 8; float peaks within half an output LSB of "
                "unity quantize onto exactly 1.0, so the fixed strict > 1 "
                "branch decides on the Q2.21 grid by declaration"
            ),
        },
        "disclaimers": [
            "No DR-0003 acceptance and no DR-0008 ratification is performed or implied.",
            "No RTL, synthesis, layout, signoff, hardware playback or sound-fidelity claim is made.",
            "Cycle, SRAM, energy and PPA costs of replay versus buffering are issue #63 deliverables, not measured here.",
            "This receipt is measurement evidence input; the acceptance decision belongs to the operator.",
        ],
        "coverage": coverage,
        "rows": sweep["rows"],
        "gain_word_rows": sweep["gain_word_rows"],
        "decision": decision,
    }


def receipt_sha256(receipt: Dict) -> str:
    """Deterministic digest of the receipt body (``generated_utc`` excluded)."""
    body = {key: value for key, value in receipt.items() if key != "generated_utc"}
    payload = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
