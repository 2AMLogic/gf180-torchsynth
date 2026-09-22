"""Golden-vector derivation and host mirror for the modulation matrix and
endpoint-aligned control upsamplers (#72).

The bit-exactness target is the frozen fixed model: every golden value here
is produced by the frozen composition's own control path
(``FixedControlPath``, the class the frozen whole-voice model instantiates;
``src/torchsynth_voice/format_sweep.py``) — never by a parallel
implementation. This module adds only:

- :func:`mirror_mod_matrix` — an exact integer re-walk of
  ``FixedControlPath._mod_matrix`` (the 4x5 matrix: four C4 Q10.21 depth
  words times four Q2.21 source columns accumulated into one exact
  numerator, ONE declared half-even narrowing through the canonical scalar
  ``entry_scale * ctrl_scale`` per output sample, C7 saturation) that is
  asserted row-equal to the model's own ``_mod_matrix`` rows, so the host
  mirror is provably the model, not a harness-parallel copy. There is no
  clamp at any matrix output: the upstream clamp is a registered negative
  mutation only.
- :func:`mirror_upsample` — an exact integer re-walk of
  ``FixedControlPath._upsample`` (endpoint-aligned coordinates from the
  model's own :func:`format_sweep.up_coordinate_table`: ``low``/remainder
  of ``j*1763/176399``, quantized uQ.31 fraction words, the exact blend
  numerator ``left*(2^31 - frac) + right*frac`` with one declared
  half-even narrowing, C7 saturation, exact endpoint copies) asserted
  row-equal to the model's own ``_upsample`` rows.
- :func:`pack_words_f32le` / :func:`unpack_words_f32le` — the #54 sidecar
  convention: Q2.21 words carried losslessly through division by 2^21
  (every word/2^21 with |word| < 2^23 is exactly representable in binary32);
  unpacking refuses any value that does not round-trip to the exact word.

Both mirrors are pure integer replays of declared-exact sites — #72 has NO
declared binary64 shadow — so the RTL consumes formed words plus the four
source columns and must reproduce every matrix and upsample word exactly.

The RTL engines (``tb/sv/mod_matrix_engine.sv``,
``tb/sv/upsample_engine.sv``) reproduce the same dataflow bit-exactly.

No RTL claim of any kind is made here.
"""

from __future__ import annotations

import struct
from typing import Dict, List, Tuple

from .float_interfaces import AUDIO_SAMPLES, CONTROL_SAMPLES
from .format_sweep import (
    MOD_MATRIX_INPUTS,
    MOD_MATRIX_OUTPUTS,
    PITCH_ROUTES,
    FixedControlPath,
    apply_saturation,
    up_coordinate_table,
)
from .fixedpoint.counters import ROUNDING, StickyCounters
from .fixedpoint.formats import FixedFormat
from .fixedpoint.rounding import div_round_reported

__all__ = [
    "MOD_ROUTES",
    "MOD_SOURCES",
    "PITCH_ROUTE_NAMES",
    "mirror_mod_matrix",
    "mirror_upsample",
    "pack_words_f32le",
    "unpack_words_f32le",
    "ModMatrixGoldenError",
]

#: The five registry routes this issue owns, in matrix output order.
MOD_ROUTES = MOD_MATRIX_OUTPUTS

#: The four matrix source columns, in the pinned input order: main ADSR1,
#: main ADSR2, post-VCA LFO1, post-VCA LFO2.
MOD_SOURCES = MOD_MATRIX_INPUTS

#: The two pitch-format routes (the remaining three take the C1 control
#: word format in the accepted instantiation).
PITCH_ROUTE_NAMES = PITCH_ROUTES


class ModMatrixGoldenError(ValueError):
    """Raised when a mirror refuses to match the frozen model."""


def route_format(fcp: FixedControlPath, route: str) -> FixedFormat:
    """The accepted output word format of one route."""

    return fcp.pitch_fmt if route in PITCH_ROUTES else fcp.ctrl_fmt


def depth_name(source: str, route: str) -> str:
    """The canonical consumed-parameter name of one matrix depth."""

    return "mod_matrix." + source + "->" + route


def mirror_mod_matrix(
    fcp: FixedControlPath,
    words: Dict[str, int],
    signals: List[List[int]],
) -> Tuple[Dict[str, List[int]], Dict[str, int]]:
    """Exact integer re-walk of ``_mod_matrix``, asserted equal to the model.

    Returns the per-route output words plus the sticky counters the RTL's
    exported op counters must reproduce (rounding and saturation events per
    declared site). Any row disagreement with ``FixedControlPath._mod_matrix``
    raises: the mirror is a proof obligation, not an alternative model.
    """

    counters = StickyCounters()
    common = fcp.entry_fmt.scale * fcp.ctrl_fmt.scale
    outputs: Dict[str, List[int]] = {}
    for route in MOD_ROUTES:
        fmt = route_format(fcp, route)
        site = "s4.mod_matrix." + route
        d0, d1, d2, d3 = (words[depth_name(source, route)] for source in MOD_SOURCES)
        c0, c1, c2, c3 = signals
        merged: List[int] = []
        for index in range(CONTROL_SAMPLES):
            numer = d0 * c0[index] + d1 * c1[index] + d2 * c2[index] + d3 * c3[index]
            word, rounded = div_round_reported(numer * fmt.scale, common, fcp.mode)
            if rounded:
                counters.note(site, ROUNDING)
            if not fmt.contains(word):
                word = apply_saturation(word, fmt, site, counters)
            merged.append(word)
        outputs[route] = merged
    model = fcp._mod_matrix(words, signals)
    if outputs != model:
        for route in MOD_ROUTES:
            if outputs[route] != model[route]:
                for index, (mine, theirs) in enumerate(
                    zip(outputs[route], model[route])
                ):
                    if mine != theirs:
                        raise ModMatrixGoldenError(
                            "mod-matrix mirror diverges from the frozen model "
                            "at %s[%d]: mirror %d model %d"
                            % (route, index, mine, theirs)
                        )
    return outputs, counters.as_json()


def mirror_upsample(
    fcp: FixedControlPath,
    column: List[int],
    route: str,
) -> Tuple[List[int], Dict[str, int]]:
    """Exact integer re-walk of ``_upsample``, asserted equal to the model.

    Uses the model's own coordinate table (exact ``j*1763/176399`` floors
    and remainders, uQ.31 half-even fraction words, zero at exact
    boundaries) and the exact blend numerator; one declared half-even
    narrowing per interior sample; exact endpoint copies of control indices
    0 and 1763. Returns the full 176,400-word audio stream plus the sticky
    counters (blend roundings, fraction-word formations, saturations).
    """

    counters = StickyCounters()
    fmt = route_format(fcp, route)
    frac_bits = fcp.spec.up_frac_bits
    frac_scale = 1 << frac_bits
    site = "s4.upsample"
    coordinates = up_coordinate_table(frac_bits, fcp.mode)
    if len(coordinates) != AUDIO_SAMPLES:
        raise ModMatrixGoldenError(
            "coordinate table carries %d rows, expected %d"
            % (len(coordinates), AUDIO_SAMPLES)
        )
    output: List[int] = []
    for low, frac_word in coordinates:
        if frac_word == 0:
            output.append(column[low])
            continue
        left = column[low]
        right = column[low + 1]
        numer = left * (frac_scale - frac_word) + right * frac_word
        quotient, rounded = div_round_reported(numer, frac_scale, fcp.mode)
        if rounded:
            counters.note(site + ".blend", ROUNDING)
        if not fmt.contains(quotient):
            quotient = apply_saturation(quotient, fmt, site, counters)
        output.append(quotient)
    model = fcp._upsample(column, fmt)
    if output != model:
        for index, (mine, theirs) in enumerate(zip(output, model)):
            if mine != theirs:
                raise ModMatrixGoldenError(
                    "upsample mirror diverges from the frozen model at %s[%d]:"
                    " mirror %d model %d" % (route, index, mine, theirs)
                )
    return output, counters.as_json()


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
            raise ModMatrixGoldenError(
                "f32le payload value %r is not an exact Q%d word multiple"
                % (value, frac_bits)
            )
        words.append(rounded)
    return words
