# Fixed-path normalization replay measurement v1

This document records the normalization measurement delivered for issue
#52: the reciprocal-precision and output-arithmetic measurement DR-0003
names before acceptance
(`spec/decision-records/0003-host-boundary-and-normalization.md:40-44`) and
the DR-0008 Section 8 / choice C9 reciprocal-precision measurement. The
committed evidence receipt is
[`sim/reference/normalization-reciprocal-v1.json`](../sim/reference/normalization-reciprocal-v1.json)
(digest `66678504fe451df102ff2e456cfd44cce1a8077ecac5b833c7dca446f54b9320`,
byte-stable across regenerations with the declared timestamp excluded).
Model module: `src/torchsynth_voice/normalization_replay.py`; receipt tool:
`tools/measure_normalization_reciprocal.py`; tests:
`tests/test_normalization_replay.py`.

**Status: CANDIDATE-pending-ratification.** This package is the decision
*evidence input* for DR-0003 acceptance. It accepts nothing: DR-0003 and
DR-0008 both remain Proposed, and acceptance is the operator's later
action. This record makes no RTL, synthesis, layout, signoff, hardware
playback, or sound-fidelity claim.

## Fixed semantics (declared, not re-decided)

Normalization semantics are owned by DR-0003 and are consumed unchanged:
render once to find the peak; if the peak exceeds one, replay
deterministically at gain `1 / peak`, otherwise replay at unity gain. The
limiter, AGC, and constant-headroom substitutes DR-0003 forbids appear in
this package only as preregistered negative controls that must fail
(`tests/test_normalization_replay.py`, `ForbiddenSubstitutesMustFail`).

Numeric mechanics come from DR-0008 Section 8 and the C1/C6/C9 candidate
instantiations (all `selected (operator ruling 2026-09-19); pending
ratification`, never accepted while DR-0008 is Proposed):

- the peak is measured on the 24-bit Q2.21 pre-normalization mix
  (`C1`: `Q2.21`, range [-4, +4), LSB 2^-21);
- the strict branch condition is `peak > 1` on the Q2.21 grid, i.e.
  `peak_int > 2^21`; peak exactly one, silence, and every peak at or below
  one bypass with the input bytes unchanged;
- the reciprocal/gain application is declared narrowing site `S5`, rounding
  half-even (`C6`);
- the gain word is unsigned `U1.F` (one integer bit + F fractional bits),
  which holds the whole divide-branch range `(0.25, 1.0]` including the
  rounding edge case where the minimal divisor's reciprocal rounds up to
  exactly 1.0.

Every Q2.21 value is exactly representable in binary32, so the float
reference — the landed `float-mix-v1` normalization semantics
(`spec/FLOAT-MIX.md`, `float_mix.normalize_if_clipping`) — consumes the
exact binary32 dequantization of the *same* clip: the two grids share
inputs exactly and all differences below are application/rounding
differences, never input differences.

## Candidate application methods (exact behavioral definitions)

Two candidate methods are measured, both deterministic pure integer
functions of the Q2.21 clip and its integer peak `P`:

1. **`direct-division`** — each output sample is
   `round_half_even(x * 2^21 / P)`, one exact integer division per sample
   with a single declared rounding at site S5, then saturation into Q2.21.
   The effective per-sample gain is the exactly rounded real `1 / peak` at
   full output resolution; a hardware realization would need a per-sample
   divider (cost belongs to #63, not measured here).
2. **`reciprocal-multiply`** — a declared-precision gain word
   `r = round_half_even(2^(21+F) / P)` is formed once per clip and
   interpreted in `U1.F`; each output sample is the exact integer product
   `x * r` narrowed once to Q2.21 at site S5, half-even, with saturation.
   This is the replay shape a hardware reciprocal implies: one reciprocal
   per clip, one multiply per sample.

Candidate fractional widths swept: `F ∈ {12, 14, 16, 18, 20, 21, 22, 24}`.
The widths are sweep data, not a decision; the recommendation rule below is
fixed before the numbers and is mechanical.

## Directed case grid (below/at/above-one, ties, extrema)

All cases are full declared clips (176,400 samples at 44.1 kHz, DR-0008
Section 9), deterministically constructed on the Q2.21 grid. Measured
coverage (from the receipt `coverage` block):

| Case | Peak (Q2.21 int) | Peak real | Peak index | Branch |
| --- | --- | --- | --- | --- |
| `fixed:above-one-min` | 2,097,153 | 1.0000004768 | 21 | divide |
| `fixed:tie` | 2,097,152 | 1.0 exactly | 21 | bypass |
| `fixed:below-one-max` | 2,097,151 | 0.9999995232 | 21 | bypass |
| `fixed:extremum` | 8,388,607 | 3.9999995232 | 21 | divide |
| `fixed:silence` | 0 | 0.0 | 0 | bypass |
| `fixed:late-peak` | 2,109,497 | 1.0058865547 | 176,399 | divide |
| `fixed:tied-max` | 2,097,929 (x2) | 1.0003705025 | 9 (earliest) | divide |
| `fixed:anchor-quiet` | 1,655,852 | 0.7895717621 | 21 | bypass |
| `fixed:anchor-loud` | 8,279,259 | 3.9478583336 | 21 | divide |

The two release anchors (`spec/decision-records/0006-canonical-runtime.md:
89`) quantize onto the Q2.21 grid with exact reported offsets: the loud
anchor `3.9478583336` lands at `8279259/2^21 = 3.9478583335876465` (offset
`+253/20480000000000 ≈ +1.2e-11`), the quiet anchor `0.7895715833` at
`1655852/2^21 = 0.7895717620849609` (offset `−915379/5120000000000 ≈
−1.8e-7`). Both anchors keep their declared branches (quiet bypasses, loud
divides). Peak-index semantics reproduce the float rule: earliest maximal
index wins on the tie case, the late peak records index 176,399, and the
`latest`-tie control disagrees (must fail; tested).

Peak-grid consequence, reported rather than hidden: because the peak is
measured on the Q2.21 mix (DR-0008 Section 8), a float peak within half an
output LSB of unity quantizes onto exactly 1.0 and the fixed branch decides
on the Q2.21 grid. The directed float targets `1 + 2^-23` and `1 − 2^-24`
both land there; the fixed divide/bypass boundary is `2^21 + 1` (peak real
`1 + 2^-21`). This is declared behavior of the measurement grid, not a
mismatch.

## Measured reciprocal-precision sweep

Every divide-branch case is replayed by every candidate method/precision
and compared against the float reference per the primary paired rows of
`docs/MEASUREMENT-PLAN.md` (sample count, first divergent sample, max abs
error + index, mean error, RMSE, SNR with an explicit silent result,
reference/candidate peaks, saturation count, normalization gain); bypass
rows compare the untouched bytes and are byte-exact (`0/1` max error, no
divergent sample, on all four bypass cases). Band-power rows are not
emitted: these directed clips are static level fixtures, not pitched
signals; the banded M1 ladder stays owned by DR-0008 Section 10. Saturation
counters at S5 are zero on every row. Full tables live in the receipt
(`rows`, `gain_word_rows`); the worst case per candidate across all
divide-branch rows:

| Candidate | Worst max abs error (exact) | ≈ value | Worst case | C10 calibrated threshold | Meets draft 2^-13 band | Within 1/2 output LSB of floor |
| --- | --- | --- | --- | --- | --- | --- |
| `direct-division` | 524287/2199023255552 | 2.384e-7 (2^-22) | `fixed:extremum` | 2^-21 | yes | yes (is the floor) |
| `reciprocal-multiply F=12` | 961/2097152 | 4.582e-4 | `fixed:anchor-loud` | 2^-10 | **no** | no |
| `reciprocal-multiply F=14` | 25/1048576 | 2.384e-5 | `fixed:anchor-loud` | 2^-14 | yes | no |
| `reciprocal-multiply F=16` | 25/1048576 | 2.384e-5 | `fixed:anchor-loud` | 2^-14 | yes | no |
| `reciprocal-multiply F=18` | 7/1048576 | 6.676e-6 | `fixed:anchor-loud` | 2^-16 | yes | no |
| `reciprocal-multiply F=20` | 1/1048576 | 9.537e-7 | `fixed:anchor-loud` | 2^-19 | yes | no |
| `reciprocal-multiply F=21` | 1/1048576 | 9.537e-7 | `fixed:anchor-loud` | 2^-19 | yes | no |
| `reciprocal-multiply F=22` | 524289/2199023255552 | 2.384e-7 | `fixed:extremum` | 2^-20 | yes | **yes** |
| `reciprocal-multiply F=24` | 524287/2199023255552 | 2.384e-7 | `fixed:extremum` | 2^-21 | yes | **yes** |

Gain-word accuracy on the anchor-loud peak (exact rows in the receipt):
against the float `mixer.gain` diagnostic `f32(1/peak)` the `F=22` word
lands at `-1/2^25` and `F=24` at `+1/2^25` — both closer than half a
binary32 ulp at unity — and against the exact real reciprocal `F=24` lands
at `2.28e-8 ≈ 2^-25.4`. Gain-word error decreases monotonically
(non-increasing) with F.

## Measured decision input for DR-0003 acceptance

- **Band qualification.** Under the C10 calibration rule (smallest power of
  two ≥ 2x the measured maximum), every candidate from `F=14` up produces a
  calibrated threshold at or below the draft strictest DR-0008 Section 10
  M1 band (`E ≤ 2^-13`, f0 ≤ 1 kHz). `F=12` fails the band outright. Draft
  band numbers are references recorded in the DR text, not ratified
  thresholds.
- **Floor qualification (the knee).** The `direct-division` floor — the
  Q2.21 output-word quantization limit — is `524287/2199023255552 ≈ 2^-22`,
  reached at `fixed:extremum` (the largest divisor). `F=22` is the smallest
  swept width whose worst-case error is within half an output LSB of that
  floor; `F=24` becomes bit-indistinguishable from the floor.
- **Recommendation (rule fixed before the numbers):** the recommended
  reciprocal precision is the smallest swept width that both meets the
  draft band and reaches the floor — **`F=22`, gain word `U1.22` (23
  bits)**. Below the knee the gain word still dominates the output error —
  4x the floor at `F=20/21`, 100x at `F=14/16`, ~1900x at `F=12` — burning
  calibration headroom; above it a wider reciprocal buys nothing.
  `direct-division` remains the reference method and the measured error
  floor; choosing between one per-clip reciprocal plus per-sample multiply
  versus a per-sample divider is a cost question owned by the architecture
  DR (#63), not decided by this error evidence.

## Explicitly not measured here

Honest scope of this evidence package, per DR-0003's own pre-acceptance
measurement list:

- **Cycle, SRAM, and energy cost of replay versus buffering** — architecture
  DR (#63) deliverables; no PPA numbers appear in this package.
- **Replay determinism for stateful modules** — the fixed replay here is a
  pure integer function of the clip (byte-identical repeats are tested), and
  module-level replay determinism rides on the landed model determinism
  evidence (#43 composed conformance, repeatability rows); the RTL replay
  controller is #63/#74 work.
- **Exact noise stream requirements** — declared exact by DR-0008 Section 7
  (host-fed, slot `sound_index % 32`, seed 13); no error metric exists by
  declaration; policy work is #75's.
- **UI/transport cost of the 78-parameter resolved patch** — host boundary
  work, not this measurement.

## Mutation controls (must fail)

The DR-0003-forbidden substitutes are preregistered negative controls built
from the canonical primitives with the branch guard deliberately bypassed;
each must produce output that differs from the correct replay on its named
case, and the tests enforce it: a hard limiter at ±1 (fails the extremum
row), a constant-headroom scale (fails the anchor-loud row), an always-on
branch (fails the below-one row), and a runner-up wrong-peak divisor (fails
the above-one row). A control that does not fail is a defect of the control.

## Verification

Bounded, honest checks executed for this document:

| Executed check | Observed result |
| --- | --- |
| `timeout 900 python3 tools/measure_normalization_reciprocal.py --emit` | full sweep; receipt written; recommended `F=22 (unsigned U1.22 (width 23 bits))` |
| `timeout 900 python3 tools/measure_normalization_reciprocal.py --check` | receipt digest stable: `66678504fe451df102ff2e456cfd44cce1a8077ecac5b833c7dca446f54b9320` |
| `python3 -m compileall -q src tests tools` | clean |
| `timeout 900 python3 -m unittest discover -s tests -p test_normalization_replay.py` | 28 tests, all pass (~34 s) |
