"""Canonical fixed-point operations on explicitly formatted integer words.

Single canonical implementations: saturation/wrap, narrowing/widening
rescale, multiply and add all route through the same primitives, and every
equivalent operation shares one code path (issue #49 acceptance criterion).

All arithmetic is exact integer arithmetic with explicit policy application:
results never depend on host integer overflow. Words are plain Python ints
constrained to their format's representable range by explicit modular
reduction.

Policies:
- ``SATURATE`` clamps into the representable range and notes a sticky
  ``saturation`` counter at the named site.
- ``WRAP`` reduces modulo 2^width (two's-complement modular semantics); it is
  the required semantics for never-saturate phase-class words (DR-0008
  Sections 2, 4 and 6). Wrapping notes no error counters: it is semantics,
  not an event.

Saturating a format declared ``modular`` raises: never-saturate words must
not be routed through saturation (contract violation guard).

Every narrowing site can emit named trace/counter events via an optional
:class:`StickyCounters`; passing counters requires naming the site.

This module makes no RTL claim.
"""

from __future__ import annotations

import enum
from typing import Optional

from .counters import ROUNDING, SATURATION, StickyCounters
from .formats import FixedFormat
from .rounding import DEFAULT_MODE, RoundingMode, div_round_reported


class OverflowPolicy(enum.Enum):
    SATURATE = "saturate"
    WRAP = "wrap"


DEFAULT_POLICY = OverflowPolicy.SATURATE


def _require_site(counters: Optional[StickyCounters], site: Optional[str]) -> None:
    if counters is not None and not site:
        raise ValueError("a named site is required whenever counters are provided")


def apply_policy(
    value: int,
    fmt: FixedFormat,
    policy: OverflowPolicy = DEFAULT_POLICY,
    counters: Optional[StickyCounters] = None,
    site: Optional[str] = None,
) -> int:
    """Bring one integer into ``fmt``'s range under an explicit policy."""
    _require_site(counters, site)
    if fmt.contains(value):
        return value
    if policy is OverflowPolicy.WRAP:
        return fmt.enclose(value)
    if policy is OverflowPolicy.SATURATE:
        if fmt.modular:
            raise ValueError(
                f"refusing to saturate modular (never-saturate) format {fmt.identity}"
            )
        clamped = fmt.max_int if value > 0 else fmt.min_int
        if counters is not None:
            counters.note(site, SATURATION)
        return clamped
    raise ValueError(f"unhandled overflow policy: {policy}")


def saturate(
    value: int,
    fmt: FixedFormat,
    counters: Optional[StickyCounters] = None,
    site: Optional[str] = None,
) -> int:
    """Clamp into ``fmt``'s representable range, noting a sticky counter."""
    return apply_policy(value, fmt, OverflowPolicy.SATURATE, counters, site)


def wrap(value: int, fmt: FixedFormat) -> int:
    """Explicit modular reduction into ``fmt``'s range (no error counters)."""
    return apply_policy(value, fmt, OverflowPolicy.WRAP, None, None)


def _rescale_reported(
    value: int,
    src_frac: int,
    dst_frac: int,
    mode: RoundingMode,
) -> tuple:
    """Convert an integer between fractional scales exactly; report rounding."""
    if dst_frac >= src_frac:
        return value << (dst_frac - src_frac), False
    return div_round_reported(value, 1 << (src_frac - dst_frac), mode)


def rescale(
    value: int,
    src: FixedFormat,
    dst: FixedFormat,
    mode: RoundingMode = DEFAULT_MODE,
    policy: OverflowPolicy = DEFAULT_POLICY,
    counters: Optional[StickyCounters] = None,
    site: Optional[str] = None,
) -> int:
    """Narrow or widen an integer word from format ``src`` to format ``dst``.

    The value is interpreted in ``src`` (i.e. it must be inside ``src``'s
    range); the result is the exact rational rescale, rounded per ``mode``,
    then brought into ``dst`` under ``policy``. Widening is exact and rounds
    nothing.
    """
    _require_site(counters, site)
    if not src.contains(value):
        raise ValueError(f"value {value} outside source format {src.identity}")
    scaled, rounded = _rescale_reported(value, src.frac_bits, dst.frac_bits, mode)
    if rounded and counters is not None:
        counters.note(site, ROUNDING)
    return apply_policy(scaled, dst, policy, counters, site)


def mul(
    a: int,
    fmt_a: FixedFormat,
    b: int,
    fmt_b: FixedFormat,
    out: FixedFormat,
    mode: RoundingMode = DEFAULT_MODE,
    policy: OverflowPolicy = DEFAULT_POLICY,
    counters: Optional[StickyCounters] = None,
    site: Optional[str] = None,
) -> int:
    """Exact integer product of two formatted words, narrowed once to ``out``.

    No intermediate rounding: the full-precision product is formed exactly
    and a single declared narrowing site applies rounding and policy.
    """
    _require_site(counters, site)
    if not fmt_a.contains(a):
        raise ValueError(f"operand {a} outside format {fmt_a.identity}")
    if not fmt_b.contains(b):
        raise ValueError(f"operand {b} outside format {fmt_b.identity}")
    product_frac = fmt_a.frac_bits + fmt_b.frac_bits
    scaled, rounded = _rescale_reported(a * b, product_frac, out.frac_bits, mode)
    if rounded and counters is not None:
        counters.note(site, ROUNDING)
    return apply_policy(scaled, out, policy, counters, site)


def add(
    a: int,
    b: int,
    fmt: FixedFormat,
    policy: OverflowPolicy = DEFAULT_POLICY,
    counters: Optional[StickyCounters] = None,
    site: Optional[str] = None,
) -> int:
    """Same-format addition with explicit policy application."""
    _require_site(counters, site)
    if not fmt.contains(a) or not fmt.contains(b):
        raise ValueError(f"operand outside format {fmt.identity}")
    return apply_policy(a + b, fmt, policy, counters, site)


class Accumulator:
    """A formatted accumulating register with explicit policy and counters.

    Growth is exact until the declared policy event (saturation or wrap), so
    accumulator behavior is fully determined by data, never by host overflow.
    """

    def __init__(
        self,
        fmt: FixedFormat,
        policy: OverflowPolicy = DEFAULT_POLICY,
        counters: Optional[StickyCounters] = None,
    ) -> None:
        if policy is OverflowPolicy.SATURATE and fmt.modular:
            raise ValueError(
                f"refusing saturating accumulator on modular format {fmt.identity}"
            )
        self.fmt = fmt
        self.policy = policy
        self.counters = counters
        self.value = 0

    def add(self, value: int, site: str) -> int:
        if not self.fmt.contains(value):
            raise ValueError(f"operand {value} outside format {self.fmt.identity}")
        self.value = apply_policy(
            self.value + value,
            self.fmt,
            self.policy,
            self.counters,
            site,
        )
        return self.value

    def reset(self) -> None:
        self.value = 0
