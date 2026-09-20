"""Deterministic fixed-point numeric primitives (generic, parameterized).

The library in this package implements explicit, format-parameterized
fixed-point semantics designed for direct bit-exact correspondence with
future RTL. It makes NO RTL claim of any kind: no synthesis, layout,
signoff, hardware playback, or sound-fidelity claim is made or implied, and
nothing here is called conformant to DR-0008 while that record's status is
Proposed.

Design invariants (issue #49):

- Word widths and scales enter only as explicit data parameters; no module
  hardcodes a candidate instantiation. Format descriptions serialize
  deterministically into artifact/model identities.
- One canonical implementation per operation: every narrowing, rounding,
  unit conversion, and interpolation routes through the same rounding scalar
  (:mod:`torchsynth_voice.fixedpoint.rounding`).
- Rounding modes are selectable with half-even as the library default (the
  DR-0008 Section 6 choice, C6) and per-site exceptions stay expressible.
- Saturation applies with sticky named per-site counters (C7 pattern);
  phase-class words wrap modularly and refuse saturation (C2 pattern).
- LUT tables are generator-emitted, deterministically reproducible, and
  hash-linked to their canonical serialization (C5 pattern).
- Candidate C1-C10 instantiations live only in
  ``spec/reference/fixedpoint-choices-v1.json`` with status
  ``selected (operator ruling 2026-09-19); pending ratification`` -- never
  ``accepted`` -- and :mod:`torchsynth_voice.fixedpoint.choices` refuses
  them wherever the contract requires accepted values.

All arithmetic is exact integer arithmetic plus explicit policies; results
never depend on host integer overflow.
"""

from .formats import FORMAT_SCHEMA, FixedFormat, parse_identity
from .rounding import (
    DEFAULT_MODE,
    RoundingMode,
    div_round,
    div_round_reported,
)
from .counters import OVERFLOW, ROUNDING, SATURATION, StickyCounters
from .ops import (
    DEFAULT_POLICY,
    OverflowPolicy,
    Accumulator,
    add,
    apply_policy,
    mul,
    rescale,
    saturate,
    wrap,
)
from .phase import PhaseAccumulator
from .lut import (
    TABLE_SCHEMA,
    QuarterWaveSpec,
    QuarterWaveTable,
    evaluate_sin,
    generate_quarter_cos,
    max_error_vs_cos,
)
from .choices import (
    CHOICES_PATH,
    DR_ACCEPTED_STATUS,
    ACCEPTED_STATUS,
    ChoiceNotAccepted,
    accepted_choices,
    get,
    load_choices,
    require_accepted,
    validate_choices,
)

__all__ = [
    "FORMAT_SCHEMA",
    "FixedFormat",
    "parse_identity",
    "DEFAULT_MODE",
    "RoundingMode",
    "div_round",
    "div_round_reported",
    "OVERFLOW",
    "ROUNDING",
    "SATURATION",
    "StickyCounters",
    "DEFAULT_POLICY",
    "OverflowPolicy",
    "Accumulator",
    "add",
    "apply_policy",
    "mul",
    "rescale",
    "saturate",
    "wrap",
    "PhaseAccumulator",
    "TABLE_SCHEMA",
    "QuarterWaveSpec",
    "QuarterWaveTable",
    "evaluate_sin",
    "generate_quarter_cos",
    "max_error_vs_cos",
    "CHOICES_PATH",
    "DR_ACCEPTED_STATUS",
    "ACCEPTED_STATUS",
    "ChoiceNotAccepted",
    "accepted_choices",
    "get",
    "load_choices",
    "require_accepted",
    "validate_choices",
]
