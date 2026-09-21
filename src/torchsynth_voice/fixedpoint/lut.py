"""Generator-emitted, hash-linked quarter-wave LUT evaluation.

Pattern per the DR-0008 Section 5 selection (C5, linear-interpolated
quarter-wave table): tables are emitted by a deterministic generator, carry a
SHA-256 over their canonical serialization, and refuse to load a payload whose
hash does not match (hash-linked data: any content change is detectable).

Generation uses exact ``decimal`` arithmetic (no float noise), so identical
parameters produce byte-identical tables on every platform and run. Evaluation
is pure integer arithmetic through the canonical rounding scalar, designed for
direct bit-exact correspondence with future RTL. This module makes no RTL
claim; table geometry enters only as explicit data per the DR-0008 Section 12
choice register (C5 — accepted by reviewed merge, 2026-09-21; see
:mod:`torchsynth_voice.fixedpoint.choices`).

The quarter table stores ``g(u) = cos((pi/2) * u)`` for ``u`` in ``[0, 1]``
(``u`` the quarter-turn fraction) in the entry format's integers. Full-circle
cosine is evaluated by quadrant selection and reflection; ``evaluate_sin``
reuses the same table via a quarter-turn phase offset ("sin reuse, cos reuse").
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from decimal import Decimal, localcontext
from fractions import Fraction
from typing import Any, Dict, Iterable, Tuple

from .formats import FixedFormat
from .rounding import DEFAULT_MODE, RoundingMode, div_round_reported

TABLE_SCHEMA = "gf180-torchsynth/quarter-wave-table-v1"
_HASH_DOMAIN = b"gf180-torchsynth/quarter-wave-table-v1"

# decimal working precision for generation; comfortably above any entry
# format's rounding threshold so emitted integers are exactly round(x * 2^f).
_GENERATION_PRECISION = 60


def _pi_decimal(prec: int) -> Decimal:
    """pi via Machin's formula with exact decimal determinism."""
    with localcontext() as ctx:
        ctx.prec = prec + 10
        threshold = Decimal(1).scaleb(-(prec + 5))

        def atan_inv(x: int) -> Decimal:
            base = Decimal(x)
            total = Decimal(0)
            term = Decimal(1) / base  # k = 0 term of atan(1/x)
            k = 0
            while True:
                total += term
                next_term = (
                    -term * Decimal(1) / (base * base)
                    * Decimal(2 * k + 1)
                    / Decimal(2 * k + 3)
                )
                if abs(next_term) < threshold:
                    break
                term = next_term
                k += 1
            return total

        return 16 * atan_inv(5) - 4 * atan_inv(239)


def _cos_series(x: Decimal, prec: int) -> Decimal:
    """cos(x) = sum_{k} (-1)^k x^(2k) / (2k)! with exact decimal arithmetic."""
    with localcontext() as ctx:
        ctx.prec = prec + 10
        total = Decimal(0)
        term = Decimal(1)  # k = 0 term
        k = 0
        threshold = Decimal(1).scaleb(-(prec + 5))
        while True:
            total += term
            next_term = -term * x * x / (Decimal((2 * k + 1) * (2 * k + 2)))
            if abs(next_term) < threshold:
                break
            term = next_term
            k += 1
        return total


@dataclass(frozen=True)
class QuarterWaveSpec:
    """Parameters of a quarter-wave table; widths/scales enter as data only."""

    n_entries: int
    entry_format: FixedFormat
    phase_bits: int

    def __post_init__(self) -> None:
        if self.n_entries < 1 or (self.n_entries & (self.n_entries - 1)) != 0:
            raise ValueError(f"n_entries must be a power of two, got {self.n_entries}")
        if self.entry_format.signed is False:
            raise ValueError(
                "entry format must be signed for full-circle quadrant evaluation"
            )
        if self.phase_bits < 2:
            raise ValueError("phase_bits must be at least 2 (quadrant field)")

    @property
    def index_bits(self) -> int:
        return self.n_entries.bit_length() - 1

    @property
    def interp_bits(self) -> int:
        return self.phase_bits - 2 - self.index_bits

    @property
    def quadrant_bits(self) -> int:
        return self.phase_bits - 2

    def to_json(self) -> Dict[str, Any]:
        return {
            "n_entries": int(self.n_entries),
            "entry_format": self.entry_format.to_json(),
            "phase_bits": int(self.phase_bits),
            "index_bits": int(self.index_bits),
            "interp_bits": int(self.interp_bits),
        }


def generate_quarter_cos(spec: QuarterWaveSpec) -> "QuarterWaveTable":
    """Emit the deterministic quarter-cosine table for ``spec``.

    Entry ``i`` is the exact rational ``cos((pi/2) * i / n)`` scaled by the
    entry format's LSB weight, rounded through the canonical half-even scalar.
    The endpoint (quarter-turn value, ``i = n``) is emitted the same way.
    """
    frac = spec.entry_format.frac_bits
    scale = spec.entry_format.scale
    pi = _pi_decimal(_GENERATION_PRECISION)
    entries = []
    for i in range(spec.n_entries):
        angle = pi * Decimal(i) / (Decimal(2) * Decimal(spec.n_entries))
        value = _cos_series(angle, _GENERATION_PRECISION)
        exact = Fraction(value) * scale
        entry, _ = div_round_reported(exact.numerator, exact.denominator, DEFAULT_MODE)
        entries.append(entry)
    endpoint_angle = pi / Decimal(2)
    endpoint_exact = Fraction(_cos_series(endpoint_angle, _GENERATION_PRECISION)) * scale
    endpoint, _ = div_round_reported(
        endpoint_exact.numerator, endpoint_exact.denominator, DEFAULT_MODE
    )
    table = QuarterWaveTable(spec=spec, entries=tuple(entries), endpoint=endpoint)
    table.validate()
    return table


class QuarterWaveTable:
    """An immutable, hash-linked quarter-wave table payload."""

    def __init__(
        self,
        spec: QuarterWaveSpec,
        entries: Tuple[int, ...],
        endpoint: int,
    ) -> None:
        self.spec = spec
        self.entries = tuple(int(e) for e in entries)
        self.endpoint = int(endpoint)

    def validate(self) -> None:
        fmt = self.spec.entry_format
        if len(self.entries) != self.spec.n_entries:
            raise ValueError(
                f"expected {self.spec.n_entries} entries, got {len(self.entries)}"
            )
        for i, entry in enumerate(self.entries):
            if not fmt.contains(entry):
                raise ValueError(f"entry {i} value {entry} outside {fmt.identity}")
        if not fmt.contains(self.endpoint):
            raise ValueError(f"endpoint value {self.endpoint} outside {fmt.identity}")

    def canonical_bytes(self) -> bytes:
        """Deterministic byte serialization: hash input and transport form."""
        parts = [_HASH_DOMAIN, b"\x00"]
        parts.append(
            json.dumps(self.spec.to_json(), sort_keys=True, separators=(",", ":")).encode(
                "ascii"
            )
        )
        parts.append(b"\x00")
        parts.append(",".join(str(e) for e in self.entries).encode("ascii"))
        parts.append(b"\x00")
        parts.append(str(self.endpoint).encode("ascii"))
        return b"".join(parts)

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def to_json(self) -> Dict[str, Any]:
        """Full payload with hash; :meth:`from_json` refuses hash mismatches."""
        return {
            "schema": TABLE_SCHEMA,
            "spec": self.spec.to_json(),
            "entries": list(self.entries),
            "endpoint": int(self.endpoint),
            "sha256": self.sha256(),
        }

    @staticmethod
    def from_json(payload: Dict[str, Any]) -> "QuarterWaveTable":
        if payload.get("schema") != TABLE_SCHEMA:
            raise ValueError(f"unsupported table schema: {payload.get('schema')!r}")
        spec = QuarterWaveSpec(
            n_entries=payload["spec"]["n_entries"],
            entry_format=FixedFormat.from_json(payload["spec"]["entry_format"]),
            phase_bits=payload["spec"]["phase_bits"],
        )
        table = QuarterWaveTable(
            spec=spec,
            entries=tuple(payload["entries"]),
            endpoint=payload["endpoint"],
        )
        table.validate()
        actual = table.sha256()
        if payload.get("sha256") != actual:
            raise ValueError(
                "table hash mismatch: payload is not the hash-linked content "
                f"(declared {payload.get('sha256')!r}, computed {actual!r})"
            )
        return table

    def evaluate(self, phase: int, mode: RoundingMode = DEFAULT_MODE) -> int:
        """Full-circle fixed-point evaluation at an unsigned ``phase_bits`` phase.

        Integer-exact linear interpolation between adjacent entries; the
        quarter-turn endpoint covers ``i = n - 1``'s right neighbor and the
        reflected ``r = 0`` boundary. Returns the signed entry-format integer.
        """
        if phase < 0 or phase >= (1 << self.spec.phase_bits):
            raise ValueError(f"phase {phase} outside {self.spec.phase_bits} bits")
        if self.spec.interp_bits < 0:
            raise ValueError("interp_bits underflow: phase circle finer than table grid")
        quadrant = phase >> self.spec.quadrant_bits
        r = phase & ((1 << self.spec.quadrant_bits) - 1)
        # cos((pi/2)(q + u)) = +g(u) q=0 | -g(1-u) q=1 | -g(u) q=2 | +g(1-u) q=3
        negate = quadrant in (1, 2)
        reflect = quadrant in (1, 3)
        if reflect:
            r = (1 << self.spec.quadrant_bits) - r
        t = r & ((1 << self.spec.interp_bits) - 1)
        i = r >> self.spec.interp_bits
        if i >= self.spec.n_entries:
            # r == 0 reflected: exactly the quarter-turn endpoint.
            value = self.endpoint
        else:
            a = self.entries[i]
            b = self.endpoint if i == self.spec.n_entries - 1 else self.entries[i + 1]
            scaled, _ = div_round_reported(
                a * (1 << self.spec.interp_bits) + (b - a) * t,
                1 << self.spec.interp_bits,
                mode,
            )
            value = scaled
        return -value if negate else value

    def evaluate_float(self, phase: int, mode: RoundingMode = DEFAULT_MODE) -> float:
        """Fixed-point evaluation converted to float (measurement only)."""
        return self.evaluate(phase, mode) / self.spec.entry_format.scale

    def error_vs_cos(self, phase: int, mode: RoundingMode = DEFAULT_MODE) -> float:
        """Absolute error of the fixed evaluation vs exact float64 cos."""
        turns = phase / float(1 << self.spec.phase_bits)
        return abs(self.evaluate_float(phase, mode) - math.cos(2.0 * math.pi * turns))

    def analytic_error_bound(self) -> float:
        """Bound: table rounding + result rounding + linear-interp curvature.

        ``<= LSB + (pi/2 / n)^2 / 8`` -- the same arithmetic as the DR-0008
        Section 5 comparison (half-LSB node quantization plus half-LSB result
        rounding, plus the linear-interpolation h^2/8 term).
        """
        lsb = 1.0 / self.spec.entry_format.scale
        h = math.pi / 2.0 / self.spec.n_entries
        return lsb + (h * h) / 8.0


def evaluate_sin(table: QuarterWaveTable, phase: int, mode: RoundingMode = DEFAULT_MODE) -> int:
    """Sine by cos reuse: ``sin(x) = cos(x - pi/2)``, same hash-linked table."""
    return table.evaluate((phase - (1 << (table.spec.phase_bits - 2))) % (1 << table.spec.phase_bits), mode)


def max_error_vs_cos(
    table: QuarterWaveTable,
    phases: Iterable[int],
    mode: RoundingMode = DEFAULT_MODE,
) -> Tuple[float, int]:
    """Dense-sweep helper: max |fixed - cos| over ``phases`` and its argmax."""
    worst = -1.0
    worst_phase = -1
    for phase in phases:
        err = table.error_vs_cos(phase, mode)
        if err > worst:
            worst = err
            worst_phase = phase
    return worst, worst_phase
