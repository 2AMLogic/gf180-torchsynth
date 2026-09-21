"""Selectable rounding modes over exact integer arithmetic.

The single canonical rounding scalar is :func:`div_round`: integer division
of ``numer`` by a positive ``denominator`` with an explicit mode. Every
narrowing, interpolation and unit-conversion site in this package routes
through it, so equivalent operations share one rounding implementation.

``RoundingMode.DEFAULT`` is half-even, per the DR-0008 Section 6 rounding
choice (C6, accepted by reviewed merge, 2026-09-21 — see
:mod:`torchsynth_voice.fixedpoint.choices`). It is a library default, not a
hardcoded contract term: per-site exceptions stay expressible by passing
an explicit mode.

No floats participate: tie detection is exact integer comparison.
"""

from __future__ import annotations

import enum
from typing import Tuple


class RoundingMode(enum.Enum):
    """Round-to-nearest-even, half-away-from-zero, floor, and truncation."""

    HALF_EVEN = "half_even"
    HALF_AWAY_FROM_ZERO = "half_away_from_zero"
    FLOOR = "floor"
    TRUNC = "trunc"


# Library default per DR-0008 Section 6 (C6) -- selected-pending-ratification,
# not accepted; see the package docstring and spec/decision-records/0008.
DEFAULT_MODE = RoundingMode.HALF_EVEN


def div_round_reported(numer: int, denominator: int, mode: RoundingMode = DEFAULT_MODE) -> Tuple[int, bool]:
    """Divide and round with an explicit mode; report whether rounding occurred.

    ``denominator`` must be positive. Returns ``(quotient, rounded)`` where
    ``rounded`` is True iff the remainder was nonzero and affected the result.
    """
    if denominator <= 0:
        raise ValueError(f"denominator must be positive, got {denominator}")
    if mode is RoundingMode.FLOOR:
        quotient = numer // denominator
        return quotient, (numer % denominator) != 0
    negative = numer < 0
    magnitude = -numer if negative else numer
    quotient, remainder = divmod(magnitude, denominator)
    rounded = False
    if remainder:
        doubled = remainder * 2
        if mode is RoundingMode.TRUNC:
            pass
        elif doubled > denominator:
            quotient += 1
            rounded = True
        elif doubled == denominator:
            if mode is RoundingMode.HALF_AWAY_FROM_ZERO:
                quotient += 1
            elif mode is RoundingMode.HALF_EVEN:
                quotient += quotient & 1
            else:
                raise ValueError(f"unhandled rounding mode: {mode}")
            rounded = True
        else:
            rounded = True
    return (-quotient if negative else quotient), rounded


def div_round(numer: int, denominator: int, mode: RoundingMode = DEFAULT_MODE) -> int:
    """Round ``numer / denominator`` with the explicit mode (canonical scalar)."""
    return div_round_reported(numer, denominator, mode)[0]
