"""Explicit fixed-point word formats as data.

Every width and scale enters the library as an explicit parameter; no module
in this package hardcodes a candidate word format. Format descriptions
serialize deterministically (canonical identity strings and JSON dicts) so a
format can participate in artifact and model identities.

Semantics are independent of host integer overflow: values are plain Python
ints, always kept inside the declared representable range by explicit
two's-complement modular reduction, never by relying on host wrap behavior.

This module makes no RTL claim and implements no candidate instantiation;
word formats enter only as explicit data per the DR-0008 Section 12 choice
register (Accepted by reviewed merge, 2026-09-21; see
:mod:`torchsynth_voice.fixedpoint.choices`).
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Dict

FORMAT_SCHEMA = "gf180-torchsynth/fixed-format-v1"


@dataclass(frozen=True)
class FixedFormat:
    """A signed two's-complement or unsigned fixed-point word format.

    Signed ``Q<n>.<f>`` words have width ``1 + n + f`` per the DR-0008
    notation (one sign bit, ``n`` integer bits, ``f`` fractional bits).
    Unsigned ``U<n>.<f>`` words have width ``n + f``.

    ``modular=True`` declares a wrapping-by-construction word (for example a
    phase accumulator): saturating such a word is a contract violation and
    :func:`torchsynth_voice.fixedpoint.ops.saturate` refuses it.
    """

    signed: bool
    int_bits: int
    frac_bits: int
    modular: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.int_bits, int) or not isinstance(self.frac_bits, int):
            raise TypeError("int_bits and frac_bits must be ints")
        if self.int_bits < 0 or self.frac_bits < 0:
            raise ValueError("int_bits and frac_bits must be non-negative")
        if self.modular and self.signed:
            raise ValueError("modular words in this library are unsigned")

    @property
    def width(self) -> int:
        return self.int_bits + self.frac_bits + (1 if self.signed else 0)

    @property
    def scale(self) -> int:
        """Multiplicative factor between stored integer and real value."""
        return 1 << self.frac_bits

    @property
    def min_int(self) -> int:
        if not self.signed:
            return 0
        return -(1 << (self.width - 1))

    @property
    def max_int(self) -> int:
        if not self.signed:
            return (1 << self.width) - 1
        return (1 << (self.width - 1)) - 1

    @property
    def lsb(self) -> Fraction:
        """Exact least-significant-bit weight as a Fraction (never a float)."""
        return Fraction(1, self.scale)

    def enclose(self, value: int) -> int:
        """Reduce any integer into the representable two's-complement range.

        This is explicit modular reduction, not host overflow behavior.
        """
        value &= (1 << self.width) - 1
        if self.signed and value >= (1 << (self.width - 1)):
            value -= 1 << self.width
        return value

    def contains(self, value: int) -> bool:
        return self.min_int <= value <= self.max_int

    @property
    def identity(self) -> str:
        """Canonical identity string: ``Q<n>.<f>`` signed, ``U<n>.<f>`` unsigned."""
        prefix = "Q" if self.signed else "U"
        identity = f"{prefix}{self.int_bits}.{self.frac_bits}"
        if self.modular:
            identity += ":modular"
        return identity

    def to_json(self) -> Dict[str, Any]:
        """Deterministic JSON-serializable description (sorted, no floats)."""
        description = {
            "schema": FORMAT_SCHEMA,
            "signed": bool(self.signed),
            "int_bits": int(self.int_bits),
            "frac_bits": int(self.frac_bits),
            "width": int(self.width),
            "modular": bool(self.modular),
            "identity": self.identity,
        }
        return description

    @staticmethod
    def from_json(description: Dict[str, Any]) -> "FixedFormat":
        if description.get("schema") != FORMAT_SCHEMA:
            raise ValueError(f"unsupported format schema: {description.get('schema')!r}")
        fmt = FixedFormat(
            signed=description["signed"],
            int_bits=description["int_bits"],
            frac_bits=description["frac_bits"],
            modular=description.get("modular", False),
        )
        if description.get("width") != fmt.width:
            raise ValueError(
                f"declared width {description.get('width')} != derived width {fmt.width}"
            )
        if "identity" in description and description["identity"] != fmt.identity:
            raise ValueError(
                f"declared identity {description['identity']!r} != derived {fmt.identity!r}"
            )
        return fmt


def parse_identity(identity: str) -> FixedFormat:
    """Parse a canonical ``Q<n>.<f>`` / ``U<n>.<f>[:modular]`` identity string."""
    modular = False
    body = identity
    if body.endswith(":modular"):
        modular = True
        body = body[: -len(":modular")]
    prefix = body[:1]
    if prefix not in ("Q", "U"):
        raise ValueError(f"unrecognized format identity: {identity!r}")
    int_part, _, frac_part = body[1:].partition(".")
    if not int_part.isdigit() or not frac_part.isdigit():
        raise ValueError(f"unrecognized format identity: {identity!r}")
    return FixedFormat(
        signed=(prefix == "Q"),
        int_bits=int(int_part),
        frac_bits=int(frac_part),
        modular=modular,
    )
