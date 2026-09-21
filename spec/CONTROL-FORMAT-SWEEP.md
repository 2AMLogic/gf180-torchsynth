# Control-path fixed-format sweep v1 (issue #50)

`tools/sweep_control_path_formats.py` sweeps preregistered candidate
fixed-format assignments for the control path through a candidate fixed
model — composed from the landed `torchsynth_voice.fixedpoint` primitives —
against the landed float reference (`torchsynth_voice.control_path`),
producing per-quantity error tables. The machine-readable evidence is
[`../sim/reference/control-format-sweep-v1.json`](../sim/reference/control-format-sweep-v1.json).

- Status: sweep v1, executed 2026-09-21 (worktree `feature/issue-50`).
- Numeric contract: `unbound:#53`; DR-0008 remains Proposed. **Every
  recommendation here is CANDIDATE-pending-#53-ratification, never
  accepted.** The frozen rubric (`spec/RUBRIC.md`,
  `reference/rubric-v0.json`) is the preregistered frame: the bands below
  are its landed float-control-path rows; the open `R-L3-M0…M7` limits are
  a #53 measure-then-preregister output and are **not** set by this sweep.
- Claims: none beyond measurement. No RTL, synthesis, layout, signoff,
  hardware-playback, or sound-fidelity claim is made or implied.

## Quantities and axes (preregistered)

One-factor-at-a-time around the composed baseline — the DR-0008 Section 12
selected-pending shapes (C4 MIDI `Q10.21`, C1-shape control word `Q2.21`,
C3-shaped pitch column `Q2.21`, C5 4096x24 table, C6 half-even, exact
rational upsample fraction, C2 u32 wrapping phase):

| Axis | Quantity (trace ownership) | Members |
| --- | --- | --- |
| `midi-domain` | S1 entry word of the MIDI-domain and unitless scalars (midi, depths, weights, sustain, alpha); stage **times** are seconds, not MIDI-domain quantities, and enter the declared Q16.30 length word directly (S2-class formation site — near-zero directed extremes like the one-ULP attack are representable there) | baseline; `Q12.19`, `Q8.23`, `Q7.16`, `Q9.6` |
| `control-signal` | control-rate word (ADSR/LFO/VCA/amp+noise columns) | baseline; `Q1.22`, `Q3.20`, `Q2.15`, `Q2.9`, `Q2.29` |
| `pitch-interface` | pitch-route column word at the control-to-VCO boundary (`mod_matrix.*_pitch`, `control_upsample.*_pitch`; C3's Hz-valued word formation is #41 scope) | baseline; `Q2.29`, `Q2.13`, `Q2.9` |
| `upsample-coordinate` | quantized interpolation fraction vs the exact rational; endpoint contract (blend rounding variants are covered by the rounding-mode axis: the model applies one mode globally) | baseline (uQ.31, half-even); `uQ.24`, `uQ.16` |
| `lfo-table` | quarter-wave table size for the LFO sine shape (C5 class; the 1K **quadratic** fallback is a different interpolator and is not instantiated — 1K-linear bounds the size axis) | baseline (4096x24); 8192x24, 2048x24, 1024x24 |
| `rounding-mode` | narrowing mode at every declared site (C6) | baseline (half-even); trunc |

19 candidates total. Every candidate identity is deterministic and recorded
in the evidence JSON. Grid amendment (declared before any recommendation
was recorded): the second full run measured that the LFO phase **integrates**
control-envelope quantization — fractional word bits bind, not integer
bits — so the 32-bit `Q2.29` control-word member was added to the
`control-signal` axis; the pathology receipt below records the finding.

## Candidate fixed model (declared composition)

Mirrors the float model stage for stage with declared fixed sites:

- **S1 entry:** every consumed physical scalar (68 names) quantized into
  its entry word through the canonical rounding scalar: MIDI-domain and
  unitless scalars (midi, depths, LFO weights, sustain, alpha) take the
  candidate's MIDI-domain word; stage-time parameters (attack, decay,
  release, duration — seconds) take the declared Q16.30 length word
  directly, per the DR-0008 vocabulary (C4 governs keyboard/tuning/depth
  arithmetic, not stage times).
- **ADSR:** lengths in a Q16.30 length word (`seconds x 441`, declared
  formation site); ramps in the Q2.30 shape domain — tilt/clamp/epsilon
  exact integer work, the division through the canonical scalar, the `**
  alpha` power in **binary64 shadow on the quantized operand** (an open
  #74-class approximation item, not a swept axis); `attack * factor *
  release` an exact Q6.90 product with one narrowing to the control word.
- **LFO:** C2 wrapping u32 phase accumulator; per-sample increment the
  declared `K = round((rate/441) * 2^32)` site from the Q16.30 rate word
  (rate clamps at zero with a saturation counter); sine shape from the
  hash-linked quarter-wave table (C5; entry Q1.23, 32-bit phase); saw,
  rsaw, tri and sqr derived from the exact phase word; the `w ** 2.718281828`
  weights in binary64 shadow on quantized weights (declared open
  approximation); the blend an exact Q4.60 accumulate with one narrowing.
- **Control VCA / mod matrix:** exact multiplies and a 48-bit-class exact
  accumulator, one declared narrowing per output sample (site S4), depth
  words from the S1 entry format.
- **Upsample:** exact rational `low`/`remainder`; the fraction quantized to
  the candidate's `uQ.<bits>` word; the blend an exact integer weighted
  average with one declared rounding; endpoints exact copies by
  construction and asserted.
- **Overflow:** declared saturation policy with sticky per-site counters
  (C7 shape); the phase word wraps by construction (never-saturate).

## Fixtures (preregistered; bounded)

- **Directed extremes:** every 4th case of `reference/directed-voice-v1.json`
  in file order — 98 of 392 cases — driven by the base physical map plus
  each case's physical overrides (host-reproducible, exact).
- **Corpus-style draws:** 32 deterministic draws, `random.Random((4001+i)*16+attempt)`,
  uniform over the per-parameter min/max bounds observed across all 392
  directed effective maps (bounds recorded in the evidence JSON), **rejected
  and redrawn** when the float control chain leaves the declared ±3.9
  amplitude envelope (at most 12 attempts per draw; every rejection is
  recorded in the evidence JSON). Rationale (declared amendment, made
  before any recommendation was recorded — see the pathology receipt
  below): per-parameter marginal extrema composed simultaneously are not a
  calibrated upstream draw, so out-of-envelope co-extreme inputs answer a
  range question, not a resolution question; the range question itself is
  delegated to the committed-corpus leg (#53) and to the recorded
  saturation counters. These draws stand in for the committed 96-case
  development corpus, whose physical maps are digest-custody
  (operator-retained, never committed) and were **not read**
  (`committed_corpus_cases_read: 0`); the committed-corpus leg belongs to
  the #53 ratification evidence.
- **Holdout:** untouched (`holdout_reads: 0`); no holdout artifact is
  referenced by any part of this sweep.
- Total: 130 fixtures. Both sides consume identical physical maps, so the
  tables measure format error only — this sweep makes no #40
  pinned-match claim, and the resolved-request normalized maps are
  validation-only at the seam.

### Pathology receipt (superseded first full run)

The first full run (superseded evidence, 2026-09-21) drew without the
amplitude envelope and recorded a genuine **saturation pathology**, kept
here as the rejection evidence the envelope rule exists for:

- On synthetic co-extreme draw `draw:seed-4013`, the float mod-matrix
  columns exceeded the Q2.21-class range [-4, +4): every candidate
  saturated (baseline sticky saturation total 1423) with max-abs errors up
  to 1.80e-1 (`mod_matrix.vco_1_amp`) — a range finding, not a resolution
  finding: no candidate's fractional resolution was the limiting factor.
- The same run exposed two real fixed-model defects (both fixed before
  the rerun): (a) a ramp length that is nonzero but below the length
  word's resolution rounded to the all-one branch, flipping the
  conditional inversion and producing ~1.0 envelope errors on
  `*_attack:near-lower` directed extremes — the zero-length branch is now
  decided on the exact pre-quantization length; (b) the ramp epsilon word
  in Q2.30 carried a 2.4e-4 relative error that dominated
  degenerate-length ramps (`*_release:near-lower`), producing up to 6.6e-5
  LFO-raw error (1.08x the landed tolerance, every candidate) through the
  LFO phase's integration of envelope error — the ramp epsilon and
  intermediate now carry Q2.60 shadow words (a modeling-granularity
  amendment, not a candidate format axis), which reduced that residual to
  2.1e-6 (3x under the tolerance).

## Bands and verdicts (preregistered)

- **Buffer traces (20):** the landed frozen float-frame per-trace
  tolerances of `reference/control-path-rubric-v1.json` — the same
  power-of-two frame rubric v0 composes for the `R-L2` float-control-path
  rows. A candidate row is `PASS` iff its max-abs over all fixtures is at
  or under the tolerance.
- **Keyboard scalars (2):** consumed-identity rows — the reference is the
  binary64 physical value, so the row measures the pure S1 entry
  quantization error, judged against the candidate's own half-LSB. The
  landed zero tolerances on these traces encode the float
  verbatim-consumption seam, not a word-width bound.
- **Verdict rule:** per-trace conjunction; no aggregate score selects a
  candidate; per-module failures stay visible per row. RMSE and worst-case
  SNR are reported per row as diagnostics and never gate.
- **Screening vs certification:** non-baseline candidates report the five
  upsampled traces on the declared screen grid (stride 100 plus exact
  endpoints; control-rate traces always full length). Full-length
  certification covers the baseline plus, per axis, the smallest-footprint
  member (word-width sum, fractional-bit sum, table entries, up-fraction
  bits — a format-size proxy, not a PPA claim) whose every screen row meets
  its band.

## Results (measured, 130 fixtures)

Executed: `timeout 7200 python3 tools/sweep_control_path_formats.py
--directed-stride 4 --draws 32` — see
[`../sim/reference/control-format-sweep-v1.json`](../sim/reference/control-format-sweep-v1.json)
for every per-trace row (max abs, argmax fixture, pooled RMSE, worst SNR,
saturation/rounding counter totals, per-row verdict).

**Outcome: 11 of 19 candidates pass every row; the composed baseline — the
DR-0008 selected-pending shapes — passes all 22 rows.** Tightest baseline
margins: the `keyboard.midi_f0` consumed-identity row sits exactly at its
structural half-LSB bound (by construction of round-to-nearest), and the
tightest buffer row is `lfo_*.post_control_vca` at 1.72x under the landed
tolerance; every upsampled row is at least 2.15x under.

Passing candidates: baseline; midi `Q12.19`, `Q8.23`; ctrl `Q3.20`,
`Q2.29`; pitch `Q2.29`; up-fraction `uQ.24`, `uQ.16`; LUT 8192x24,
2048x24, 1024x24.

Executed: `timeout 7200 python3 tools/sweep_control_path_formats.py
--directed-stride 4 --draws 32` — 130 fixtures (98 directed stride-4 +
32 corpus-style draws), 19 candidates, elapsed 5274 s.

Per-trace rows (max abs, argmax fixture, pooled RMSE, worst SNR,
saturation/rounding counter totals, per-row verdict) are in the evidence
JSON. Summary:

| Axis | Candidate | Worst-row max abs | Rows PASS | All rows | Certified full |
| --- | --- | --- | --- | --- | --- |
| `baseline` | `midi=Q10.21 ... mode=half_even` | 2.837e-05 | 22/22 | PASS | yes |
| `control-signal` | `midi=Q10.21 ctrl=Q1.22 pitch=Q2.21 upfrac=uQ.31 lut=4096x24 mode=half_even` | 1.784e+00 | 16/22 | **FAIL** | grid |
| `control-signal` | `midi=Q10.21 ctrl=Q3.20 pitch=Q2.21 upfrac=uQ.31 lut=4096x24 mode=half_even` | 2.837e-05 | 22/22 | PASS | yes |
| `control-signal` | `midi=Q10.21 ctrl=Q2.15 pitch=Q2.21 upfrac=uQ.31 lut=4096x24 mode=half_even` | 2.563e-01 | 6/22 | **FAIL** | grid |
| `control-signal` | `midi=Q10.21 ctrl=Q2.9 pitch=Q2.21 upfrac=uQ.31 lut=4096x24 mode=half_even` | 8.251e-01 | 2/22 | **FAIL** | grid |
| `control-signal` | `midi=Q10.21 ctrl=Q2.29 pitch=Q2.21 upfrac=uQ.31 lut=4096x24 mode=half_even` | 9.406e-06 | 22/22 | PASS | grid |
| `lfo-table` | `midi=Q10.21 ctrl=Q2.21 pitch=Q2.21 upfrac=uQ.31 lut=8192x24 mode=half_even` | 2.813e-05 | 22/22 | PASS | grid |
| `lfo-table` | `midi=Q10.21 ctrl=Q2.21 pitch=Q2.21 upfrac=uQ.31 lut=2048x24 mode=half_even` | 2.813e-05 | 22/22 | PASS | grid |
| `lfo-table` | `midi=Q10.21 ctrl=Q2.21 pitch=Q2.21 upfrac=uQ.31 lut=1024x24 mode=half_even` | 2.837e-05 | 22/22 | PASS | yes |
| `midi-domain` | `midi=Q12.19 ... mode=half_even` | 3.365e-05 | 22/22 | PASS | yes |
| `midi-domain` | `midi=Q8.23 ... mode=half_even` | 2.813e-05 | 22/22 | PASS | grid |
| `midi-domain` | `midi=Q7.16 ... mode=half_even` | 7.101e-01 | 7/22 | **FAIL** | grid |
| `midi-domain` | `midi=Q9.6 ... mode=half_even` | 9.256e-01 | 2/22 | **FAIL** | grid |
| `pitch-interface` | `midi=Q10.21 ctrl=Q2.21 pitch=Q2.29 upfrac=uQ.31 lut=4096x24 mode=half_even` | 2.837e-05 | 22/22 | PASS | yes |
| `pitch-interface` | `midi=Q10.21 ctrl=Q2.21 pitch=Q2.13 upfrac=uQ.31 lut=4096x24 mode=half_even` | 1.242e-04 | 18/22 | **FAIL** | grid |
| `pitch-interface` | `midi=Q10.21 ctrl=Q2.21 pitch=Q2.9 upfrac=uQ.31 lut=4096x24 mode=half_even` | 1.916e-03 | 18/22 | **FAIL** | grid |
| `rounding-mode` | `midi=Q10.21 ... mode=trunc` | 4.584e-05 | 20/22 | **FAIL** | grid |
| `upsample-coordinate` | `midi=Q10.21 ctrl=Q2.21 pitch=Q2.21 upfrac=uQ.24 lut=4096x24 mode=half_even` | 2.813e-05 | 22/22 | PASS | grid |
| `upsample-coordinate` | `midi=Q10.21 ctrl=Q2.21 pitch=Q2.21 upfrac=uQ.16 lut=4096x24 mode=half_even` | 2.980e-05 | 22/22 | PASS | yes |

Axis winners (smallest-footprint band-passing member per axis):

| Axis | Baseline passes (all 22 rows) | Smallest passing member |
| --- | --- | --- |
| `control-signal` | yes | `midi=Q10.21, ctrl=Q3.20, pitch=Q2.21, up=uQ.31, lut=4096x24, mode=half_even` |
| `lfo-table` | yes | `midi=Q10.21, ctrl=Q2.21, pitch=Q2.21, up=uQ.31, lut=1024x24, mode=half_even` |
| `midi-domain` | yes | `midi=Q12.19, ctrl=Q2.21, pitch=Q2.21, up=uQ.31, lut=4096x24, mode=half_even` |
| `pitch-interface` | yes | `midi=Q10.21, ctrl=Q2.21, pitch=Q2.29, up=uQ.31, lut=4096x24, mode=half_even` |
| `rounding-mode` | yes | `none passing` |
| `upsample-coordinate` | yes | `midi=Q10.21, ctrl=Q2.21, pitch=Q2.21, up=uQ.16, lut=4096x24, mode=half_even` |

Recommendation: `midi=Q10.21 ctrl=Q2.21 pitch=Q2.21 upfrac=uQ.31 lut=4096x24 mode=half_even` composed baseline — all rows PASS; every recommendation is
**CANDIDATE-pending-#53-ratification, never accepted**; refusals recorded: 0; holdout reads: 0; committed corpus cases read: 0.


Rejected candidates, with their binding rows (artifact links to the
evidence JSON's per-candidate blocks):

| Rejected | Binding evidence |
| --- | --- |
| midi `Q7.16` | `adsr_1.output` 2.13e-05 vs 1.53e-05; upsampled amp columns 7.7e-02 on a draw fixture (coarse depth/sustain/alpha entry words) |
| midi `Q9.6` | envelope rows 2.3e-02 vs 1.5e-05..2.4e-04 (2.3e-02-level envelope distortion; 20 rows fail) |
| ctrl `Q1.22` | range [-2,+2) saturates (1764 sticky events on `directed:special:stress`): amp columns 1.78e+00 vs 6.1e-05 |
| ctrl `Q2.15` | `adsr_1.output` 1.54e-05 vs 1.53e-05 (marginal); upsampled amp columns 7.4e-03 |
| ctrl `Q2.9` | envelope rows 9.8e-04; 20 rows fail |
| pitch `Q2.13` | `control_upsample.*_pitch` 1.24e-04 vs 1.22e-04 (marginal, 1.02x over) |
| pitch `Q2.9` | `control_upsample.*_pitch` 1.9e-03 vs 1.2e-04 (16x over) |
| rounding `trunc` | the consumed-identity rows exceed their structural half-LSB bounds (truncation rounds up to a full LSB: `keyboard.midi_f0` 4.47e-07 vs 2.38e-07, `keyboard.duration` 9.27e-10 vs 4.66e-10) — rounding-bias evidence for C6 half-even; 20/22 rows pass |

Mechanism notes carried forward as measured findings for #53:

- **The LFO phase integrates control-envelope error.** Envelope-word
  quantization accumulates in the phase integrator: `lfo_1.raw` error
  drops 2.5x (2.37e-05 to 9.4e-06) when the control word widens from
  Q2.21 to Q2.29. At the band level this is harmless for every passing
  candidate, but any future control-word narrowing must be judged with
  the integrator in evidence, not per-sample alone.
- **Synthetic co-extreme draws are a range instrument, not a resolution
  instrument.** The superseded first run's ±4-exceeding draws produced
  candidate-independent saturation errors (up to 1.80e-01); the declared
  ±3.9 amplitude envelope now redirects that evidence to the saturation
  counters (baseline 1145 sticky saturation events across 130 fixtures,
  from in-range clamp sites such as the LFO rate clamp at zero), and the
  real-corpus column range remains a committed-corpus measurement for
  #53.

## Recommendations

**Recommendation (CANDIDATE-pending-#53-ratification, never accepted):**
the composed baseline — `midi=Q10.21` (C4), `ctrl=Q2.21` (C1 shape),
`pitch=Q2.21`, exact-rational-fraction `uQ.31` upsampling, 4096x24
quarter-wave table (C5), half-even rounding (C6) — passes every band on
every fixture and is the recommended control-path format assignment.

Measured alternatives for #53's Pareto consideration, each passing every
row with the recorded margins: control word `Q3.20` (24-bit, tightest
row at its own structural bound) and `Q2.29` (32-bit, 2.5x lower LFO-integrator
error); pitch column `Q2.29`; up-fraction `uQ.24` (still endpoint-exact);
LUT sizes 2048x24 and 1024x24 (bounded by linear-interpolation error
2.9e-07 at 1K, still 100x under the tightest band). The `midi=Q12.19`
word passes footprint-tied with C4's `Q10.21`; C4 remains the
selected-pending default. Nothing here is accepted: ratification, final
`R-L3` limit calibration (measure, then preregister per DR-0008 Section
10), and the committed-corpus leg are #53 deliverables.

## Explicitly out of scope

The committed-corpus leg (digest-custody maps; #53 ratification evidence);
the ADSR `** alpha` and LFO weight-exponent approximations (#74-class
declarations); the Hz-valued frequency word formation and all VCO-side
quantities (#41); RTL and any timing/cycle claim (#63); area, storage,
power, and any synthesized-PPA claim (#82); sound fidelity, hardware
playback, synthesis, layout, and signoff claims of any kind.
