"""Wrapping modular phase-accumulator semantics (phase-class words).

Canonical fixed phase semantics per the DR-0008 Section 4 selection: an
unsigned modular accumulator where ``phase[n+1] = (phase[n] + K[n]) mod
2^width`` and one turn is exactly ``2^width`` units. Wrapping is semantically
exact for quadrature waves and is never an error event: phase-class words are
never-saturate words, and saturating them is a contract violation.

Increments and injected initial turns are converted through the canonical
half-even rounding scalar so the model and future RTL can correspond
bit-exactly. Exact ``Fraction`` accessors make drift measurable without
float error.

The width is an explicit parameter of every instance: this module embeds no
candidate width. DR-0008's Section 12 phase-semantics choice (the wrapping
u32 accumulator, C2) is `accepted` as of the record's reviewed merge
(2026-09-21); see :mod:`torchsynth_voice.fixedpoint.choices`. No RTL claim
is made here.
"""

from __future__ import annotations

from fractions import Fraction
from typing import List

from .rounding import DEFAULT_MODE, RoundingMode, div_round_reported


class PhaseAccumulator:
    """Unsigned modular phase word: ``2^width`` units per turn."""

    def __init__(self, width: int, initial_value: int = 0) -> None:
        if not isinstance(width, int) or width <= 0:
            raise ValueError(f"width must be a positive int, got {width!r}")
        self.width = width
        self.modulus = 1 << width
        self.units_per_turn = self.modulus
        self.value = initial_value % self.modulus

    @property
    def turns(self) -> Fraction:
        """Exact accumulated phase in turns."""
        return Fraction(self.value, self.modulus)

    def inject_turns(
        self,
        turns: Fraction,
        site: str = "phase.initial",
        mode: RoundingMode = DEFAULT_MODE,
    ) -> int:
        """Inject an initial phase given in exact turns (declared site S3-style).

        Rounds ``turns * 2^width`` half-even (or per explicit mode) and
        reduces modulo 2^width. Returns the stored value.
        """
        if not isinstance(turns, Fraction):
            turns = Fraction(turns)
        scaled, _rounded = div_round_reported(
            turns.numerator * self.modulus, turns.denominator, mode
        )
        self.value = scaled % self.modulus
        return self.value

    def step(
        self,
        increment: int,
        site: str = "phase.increment",
        mode: RoundingMode = DEFAULT_MODE,
    ) -> int:
        """Advance by one (optionally fractional-exact) increment, wrapping.

        ``increment`` may be any integer; reduction is explicit modular
        arithmetic, never host overflow. Returns the new stored value.
        """
        self.value = (self.value + increment) % self.modulus
        return self.value

    def increment_from_frequency(
        self,
        frequency_over_fs: Fraction,
        site: str = "phase.K",
        mode: RoundingMode = DEFAULT_MODE,
    ) -> int:
        """Declared increment site S2-style: ``K = round((f/fs) * 2^width)``.

        ``frequency_over_fs`` is the exact rational ``f / fs``; the rounding
        mode defaults to the canonical half-even. Returns the increment; it
        is not applied automatically.
        """
        if not isinstance(frequency_over_fs, Fraction):
            frequency_over_fs = Fraction(frequency_over_fs)
        k, _rounded = div_round_reported(
            frequency_over_fs.numerator * self.modulus,
            frequency_over_fs.denominator,
            mode,
        )
        return k

    def run_constant_frequency(
        self,
        frequency_over_fs: Fraction,
        steps: int,
        mode: RoundingMode = DEFAULT_MODE,
    ) -> List[int]:
        """Step ``steps`` times at a constant exact ``f/fs``; return the trace."""
        k = self.increment_from_frequency(frequency_over_fs, mode=mode)
        return [self.step(k, mode=mode) for _ in range(steps)]
