# Fixed-point numeric primitives (generic, parameterized)

Deterministic fixed-point numeric primitives for the default Voice fixed
model. Every word width, scale, rounding mode, and overflow policy enters as
explicit data; no module here hardcodes a candidate instantiation. The
library is designed for direct bit-exact correspondence with future RTL and
makes **no RTL claim** — no synthesis, layout, signoff, hardware playback,
or sound-fidelity claim is made or implied. Nothing here is called
conformant to DR-0008 while that record's status is Proposed.

## Modules

- `formats` — explicit signed/unsigned fixed word formats as data, with
  deterministic identity strings and JSON descriptions for artifact/model
  identities. Values are plain integers constrained to the declared
  two's-complement range by explicit modular reduction; host integer
  overflow is never relied on.
- `rounding` — one canonical integer rounding scalar (`div_round`) with
  selectable modes: half-even (library default, the DR-0008 Section 6
  choice), half-away-from-zero, floor, truncation. Tie detection is exact
  integer comparison; no floats participate.
- `counters` — sticky, named per-site counters for narrowing/rounding and
  saturation events, with deterministic serialization.
- `ops` — single canonical implementations: `saturate` / `wrap` /
  `apply_policy`, `rescale` (narrowing and widening), `mul`, `add`, and a
  policy-driven `Accumulator`. Equivalent operations share one code path.
- `phase` — unsigned modular wrapping phase accumulators of explicit width:
  wrap-around is exact semantics (never an error event), initial-phase
  injection and per-increment formation round through the canonical scalar.
- `lut` — generator-emitted, hash-linked quarter-wave tables with integer
  linear interpolation, quadrant reflection, sine-by-cos reuse, analytic
  error bounds, and deterministic `decimal`-based generation (byte-identical
  tables across runs and platforms).
- `choices` — loads the machine-readable choice register and refuses
  not-yet-accepted values wherever the contract requires accepted ones.

## Instantiation status (hard rule)

Any C1–C10 instantiation ships only as data in the machine-readable choice
register `fixedpoint-choices-v1.json` under the spec reference directory.
Every entry carries

    status: accepted
    accepted_via: accepted (reviewed merge; 2026-09-21)

recording the DR-0008 Section 13 acceptance event (the reviewed merge of the
issue #53 ratification PR); `choices.require_accepted` admits these values
and structurally refuses anything else — a register edit that breaks the
invariant (an accepted entry in a non-Accepted record, a not-yet-accepted
status where accepted is required) is refused again. The vocabulary follows
the DR-0008 Section 12 choice register. No widths or scales beyond that
register appear anywhere in this package.

## Verification surface

- `test_fixedpoint_formats.py` — construction, validation, serialization
  round-trips and identity determinism.
- `test_fixedpoint_rounding.py` — rounding-mode vectors including exact
  ties-to-even cases on both signs.
- `test_fixedpoint_ops.py` — saturation and sticky counters, wrap
  semantics, accumulator growth, canonical-equivalence checks, host-overflow
  independence at extreme magnitudes.
- `test_fixedpoint_phase.py` — wrap-around crossings, injection ties, and
  exact (Fraction-based) drift bounds over a whole clip.
- `test_fixedpoint_lut.py` — generation determinism (including a
  fresh-interpreter hash check), serialization/hash stability and
  corruption refusal, known values, and a bounded dense sweep (2^20+ phase
  points) of the fixed evaluation against exact `math.cos` with the
  analytic error bound.
- `test_fixedpoint_choices.py` — status wording, vocabulary, and the
  refusal gate.
