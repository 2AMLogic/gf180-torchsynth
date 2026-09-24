# DR-0008: Fixed-point numeric contract for the default Voice

- Status: Accepted (reviewed merge ratifies per §13; thresholds calibrated from the v0 sweeps; ratified 2026-09-21)
- Date: 2026-09-19
- Decision owners: 2AM Logic
- Scope: exactly the numeric-contract axes named in `AGENTS.md` — target,
  arithmetic profile, noise policy, normalization, parameter ordering, and
  clip timing. Microarchitecture, clocks/cycle budgets, memory macro choices,
  time-multiplexing, and host protocol binding belong to the architecture DR
  (issue #63) and are only cross-referenced here, never decided here.

This record is written against issue #53 ("Ratify the fixed arithmetic and
error contract") and consumes the verified upstream groundwork in
[#83 (comment 5743848538)](https://github.com/2AMLogic/gf180-torchsynth/issues/83#issuecomment-5743848538).
Per `spec/VOICE-CONTRACT.md:84-89`, numeric formats, function
approximations, rounding, saturation, and output word length are
**unratified** until measurement. The operator ruling of 2026-09-19 (see
"Operator ruling" below) accepted all nine open-question defaults, so the
Choice Register (Section 12) marks C1–C10 `selected (operator ruling
2026-09-19)`. The record remained Proposed — with every register entry
refused by the `require_accepted` gate — until the v0-sweep calibration
evidence landed (issue #50 via PR #152, issue #51 via PR #153, issue #52
via PR #150) and the record reached **Accepted** through the normal review
ladder (Proposed → Accepted, per `spec/decision-records/README.md`) by the
reviewed merge of the issue #53 ratification PR (2026-09-21). That reviewed
merge is the acceptance event Section 13 defines; Section 15 records what
it ratifies, per row, and what stays explicitly open. Consumers refuse
not-yet-accepted or stale values wherever the contract requires accepted
ones (issue #53, "Choices are data"): any future register state that does
not carry `dr_status: Accepted` plus per-entry `status: "accepted"` is
refused by `torchsynth_voice.fixedpoint.choices.require_accepted`. No RTL
implementation may be called conformant to this record on any basis other
than this accepted status plus the Section 10 calibrated thresholds.

All upstream claims below were verified at the pinned TorchSynth commit
`2b0964d4c6c3d472a2a0d54d91b408caaeffca6d` (declared at
`spec/reference/upstream.json:4`); the git-show extraction and SHA-256
hash checks establishing those upstream facts are documented in the #83
groundwork comment and are cited here, not re-derived. Upstream paths are
written `torchsynth/<file>:<line>` **at the pin**; they are not paths in
this repository.

## Operator ruling (2026-09-19)

On 2026-09-19 the Champion accepted, in session, all nine defaults of
Section 14 ("Open questions for the operator"):

- OQ1 — wrapping u32 phase accumulator (Section 4);
- OQ2 — 24-bit Q2.21 [-4, +4) internal audio word (Section 2);
- OQ3 — 4096x24 quarter-wave LUT + linear interpolation, with the
  1K-entry quadratic fallback pre-authorized (Section 5);
- OQ4 — round-half-even at all sites S1–S5 (Section 6);
- OQ5 — 2x-measured power-of-two thresholds preregistered before RTL
  freeze (Section 10);
- OQ6 — internal fixed-model observability ports permitted without a
  trace-registry schema change;
- OQ7 — host-fed exact noise stream (Section 7);
- OQ8 — float-reference fixtures generated under
  `release-mkl-compatible-v1` (Section 10);
- OQ9 — MIDI 151 required in intrinsic M2/M3 only (Section 10).

In Choice Register terms, the ruling selected C2, C5, C6, C8 and C10 (the
open-question defaults) and C1, C3, C4 and C9 (proposals with no open
question, never disputed). All ten register entries are therefore
`selected (operator ruling 2026-09-19)`. Landing this ruling is tracked
by issue #112. None of these choices is `accepted` until this record's
reviewed merge; the register below is the machine-readable state.

## 1. Target

The contract applies to one target: the default Voice graph
(`spec/VOICE-CONTRACT.md:48-56`) rendered as the canonical first product
profile — a deterministic one-shot, 4-second, 44.1 kHz default-nebula clip
(DR-0002) — under the host boundary of DR-0003: the host supplies a
resolved, name-keyed patch and the selected canonical noise stream
(`spec/decision-records/0003-host-boundary-and-normalization.md:14-19`).
Live note semantics and the drum nebula are later, separately named
profiles and are out of scope. Nothing here changes the target; the pin
stays `2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`.

## 2. Word formats (arithmetic profile selected by operator ruling 2026-09-19)

Notation: a signed two's-complement word is `Q<n>.<f>` with width
1 sign + n integer + f fractional bits.

| Quantity | Selected format | Rationale |
| --- | --- | --- |
| Audio sample word — all audio-rate signals and named traces (`vco_1.raw`, `vco_2.raw`, `noise.raw`, post-VCA, pre-normalization mix, final output) | 24-bit Q2.21, range [-4, +4), LSB 2^-21 ≈ 4.77e-7 | Measured pre-normalization peaks reach 3.9478583336 (`spec/decision-records/0006-canonical-runtime.md:89`); two integer bits give headroom to [-4, +4). LSB ≈ 8x finer than float32 quantization at unity, so float→fixed error is dominated by declared effects, not word width. |
| Phase accumulator (per oscillator) | unsigned 32-bit, modular (wrapping), 1 turn = 2^32 units | Resolution fs / 2^32 ≈ 1.03e-5 Hz, finer than float32 representability at f = 12,543.9 Hz (≈ 7.4e-4 Hz). Wrapping is semantically exact for cosine. |
| Frequency word (control→VCO interface) | 32-bit Q16.15, LSB 3.05e-5 Hz, range to ±65,536 Hz | Must represent f(MIDI 151) = 50,174 Hz without clamping: upstream does not clamp frequency outside its debug-only assertion (pinned `torchsynth/module.py:567-570`). Aliasing above Nyquist is upstream behavior to preserve, not fix. |
| MIDI-domain control arithmetic (keyboard/tuning/depth, pre-exp2) | 32-bit Q10.21, LSB 4.77e-7 MIDI | Pitch quantization ≪ 0.001 cents; far below float32; keeps the upstream MIDI clamp exact (pinned `torchsynth/module.py:600`). |
| Control-rate signals (441 Hz; LFO/ADSR/upsampled paths) | 24-bit Q2.21 shared with audio; the exact 100x endpoint-aligned upsample indexing is owned by #72 | Uniformity with the audio word; 44,100 / 441 = 100 exactly. |
| Multiplier/accumulate internals | 24x24 → 48-bit products; 48-bit accumulators in VCA/mixer lanes | No intermediate rounding before a declared narrowing site (Section 6). |

Internal fixed-model observability ports (raw phase, pre-narrowing
accumulator) are Open Question 6; the operator ruling of 2026-09-19
accepted its default — permitted as model/RTL-only observability without
a trace-registry schema change, provided upstream trace identity and
ordering are preserved.

## 3. Parameter ordering

Two orderings are binding and both are upstream-exact:

1. **Patch identity ordering.** The host delivers the 78 parameters as a
   resolved, name-keyed patch (DR-0003); canonical names and the
   normalized/physical duality follow `spec/PARAMETER-INVENTORY.md` and
   `spec/VOICE-CONTRACT.md:78-79`. Ordering is by canonical name; no
   positional parameter transport is allowed across the host boundary.
2. **Numeric application ordering** in the shared VCO pitch path
   (modulated branch — the only branch the default Voice exercises):
   `midi_f0 + tuning`, then `+ mod_depth * mod`, then the MIDI clamp to
   [0, 127], then MIDI→Hz `440 * 2^((midi-69)/12)` — upstream order per
   pinned `torchsynth/module.py:588-601` and `torchsynth/util.py:23`
   (verification: #83 groundwork comment). The clamp is exact integer
   saturation in the Q10.21 MIDI domain **before** exp2; reordering clamp
   after exp2 or folding it into the exp2 argument is a contract violation
   detected by M4.

## 4. Phase semantics (Open Question 1 — selected by operator ruling 2026-09-19)

Upstream accumulates phase as an unbounded float32 running cumsum
(pinned `torchsynth/module.py:611`, `initial_phase` added once at pinned
`torchsynth/module.py:573`): no wrap, no modulo; at f = 12,543.9 Hz the
reference's own accumulated phase reaches ≈ 3.15e5 rad by sample 176,399,
with float32 ULP ≈ 0.05 rad (amplitude error up to ≈ 0.05) at the top of
the band. That wander is **float-reference drift**, not fixed-model error,
and any float→fixed metric must account for it (#83 groundwork, Section 1).

**Selected semantics:** canonical fixed semantics = wrapping integer phase
accumulator, `phase[n+1] = (phase[n] + K[n]) mod 2^32`,
`K[n] = round((f[n] / fs) * 2^32)`; `initial_phase` is injected once at
sample 0 as `(initial_phase / (2*pi)) * 2^32`, rounded half-even, before
the first increment. Fixed-model phase is then near-exact (worst-case
increment-quantization drift bounded by M3), and the float reference's
cumsum drift is absorbed by the banded float→fixed metrics (Section 10).
The alternative — bit-exact float32-cumsum emulation in fixed model and
RTL — would force float32 addition semantics into hardware, forfeit the
near-exact phase bound, and still leave the reference's drift as the
dominant term of every band; it is recorded as the rejected-for-proposal
alternative in the Choice Register. The operator ruling of 2026-09-19
selected the wrapping accumulator. Consequence of this decision: it
propagates to every later trace comparison and to #79's bit-identity
claim, which is why it was settled here before any VCO RTL.

## 5. Waveform approximation: sine (Open Question 3 — selected by operator ruling 2026-09-19)

Comparison (intrinsic error vs float64 `math.cos`; cost order-of-magnitude
only — area evidence belongs to #82):

| Option | Rough cost | Max waveform error | Verifiability |
| --- | --- | --- | --- |
| Quarter-wave LUT, 4096 entries x 24 bit, linear interpolation on the next 18 phase bits (12 index + 18 interp of the 30 usable bits) | one 4Kx24 memory + one 24x18 multiply | interp ≤ (pi/2/4096)^2/8 = 1.84e-8; table quantization ≤ 2.4e-7; total ≤ 2.6e-7 | generator-emitted table, hash-linked to this DR; provable by exhaustive 2^32 sweep in simulation |
| CORDIC, 18 rotations, pipelined | no memory; ≈ 18 add/shift stages + gain-compensation multiply | ≈ 1 LSB at chosen coefficient width; gain constant folded exactly | bound derivable, but accumulation across rotations is subtler to prove sample-exact |
| Minimax polynomial over a quarter turn | ≈ 4-6 multiplies | design-dependent | coefficient provenance and range reduction must be proven; no memory |

**Selected:** quarter-wave LUT + linear interpolation, with the
1K-entry + quadratic-interpolation variant **pre-authorized now** as the
measured-fit fallback (it changes only the hashed table contents, never
the interface or the M2 gate). The table is generator-emitted and
hash-linked to this DR (issue #53 "Choices are data"); any table change
requires regenerating the hash and rerunning M2/M7 (Section 13). A future
switch to CORDIC for area would be gated by the same metrics unchanged.

## 6. Rounding, overflow, saturation

- **Rounding:** round-half-even at every declared narrowing site, matching
  IEEE/float32 reference rounding and avoiding DC bias under long
  accumulation. Declared sites, named for traceability:
  S1 host float→Q10.21 MIDI-domain entry; S2 frequency-word formation
  `K[n] = round((f[n]/fs) * 2^32)`; S3 `initial_phase` turn conversion;
  S4 48-bit product/accumulator → 24-bit Q2.21 narrowing at each module
  output; S5 reciprocal/gain application in the normalization replay.
  Any per-site exception (e.g. half-away-from-zero) must be named per
  site in this DR before acceptance (Open Question 4).
- **Saturation:** audio words saturate at the narrowing site; every module
  exports sticky per-module saturate/overflow counters as model and RTL
  traces (#76's AC already requires model/RTL counter agreement).
- **Never-saturate words:** phase and frequency wrap/modular by
  construction (Sections 2 and 4); saturating them is a violation.
- **Nyquist clamp is forbidden.** Upstream does not clamp frequency
  outside debug mode (pinned `torchsynth/module.py:567-570`);
  above-Nyquist aliasing (e.g. the MIDI 151 case at ≈ 50,174 Hz) is
  upstream behavior this contract preserves. The MIDI [0, 127] clamp
  (pinned `torchsynth/module.py:600`) is a *pitch-domain* clamp applied
  before exp2 (Section 3) and is not a frequency clamp.

## 7. Noise policy interface

The canonical noise stream is host- or testbench-fed **bit-exactly** per
DR-0003 (`spec/decision-records/0003-host-boundary-and-normalization.md:14-19`);
canonical slot is `sound_index % 32` with seed 13
(`spec/VOICE-CONTRACT.md:75-77`); 32-stream repetition for larger
reproducible batches is upstream behavior (pinned `torchsynth/config.py:19`;
verification: #83 groundwork comment). There is **no error metric** for
noise: exactness. An on-chip generator that reproduces the CPU `torch.rand`
bitstream would be a new noise policy and requires its own decision record
before any #75 RTL (Open Question 7).

## 8. Normalization interface

Normalization semantics are owned by DR-0003: render once to find the
peak; if peak > 1, replay deterministically at gain `1 / peak`, else unity.
This contract fixes only the numeric mechanics of that boundary:

- the peak is measured on the 24-bit Q2.21 pre-normalization mix;
- the replay gain `1 / peak` is a declared-precision reciprocal (site S5,
  rounding half-even); reciprocal precision **is measured**
  (`sim/reference/normalization-reciprocal-v1.json`, issue #52 / PR #150):
  the recommended mechanics are a reciprocal-multiply with a 22-fraction-bit
  reciprocal and a U1.22 (width-23) gain word — the smallest swept width
  whose calibrated threshold (2^-20, by the C10 rule) sits at or below the
  draft strictest M1 band (2^-13) and whose worst case (524289/2^41 ≈
  2.3842e-7) is within half an output LSB of the direct-division floor
  (524287/2^41); F=12 misses the draft band and is rejected. Choice C9
  carries these parameters; DR-0003 acceptance itself remains gated on its
  own text (see Section 15);
- no limiter, AGC, or constant headroom substitutes for conditional
  whole-clip normalization (DR-0003).

## 9. Clip timing

The clip is exactly 176,400 audio samples at 44,100 Hz (4.000 s) with a
441 Hz control rate and exact 100x endpoint-aligned upsampling (owned by
#72). All named traces carry the full 176,400 samples; comparisons are
time-locked and never aligned, trimmed, rescaled, or gain-fitted
(`spec/PAIRED-METRICS.md:3-4`). The per-sample cycle budget and schedule
are #63 deliverables; this record fixes only the sample grid and trace
lengths (metric M6 asserts the #63 budget when it exists).

## 10. Error metrics M0–M7

Ladder authority: fixed model→RTL is sample-exact; float→fixed uses
preregistered limits (`spec/decision-records/0004-verification-claims.md:16-18`,
`spec/decision-records/0007-single-sound-execution.md:163-164`). No VCO
RTL may start before its fixed model and these metrics are preregistered.
Float references are generated under `release-mkl-compatible-v1`
(`spec/decision-records/0006-canonical-runtime.md:17-18`). Every metric is
reported **per property/trace per fixture** with preregistered coverage —
never as a mean score (issue #53 AC). Reported rows follow
`docs/MEASUREMENT-PLAN.md:85-95` (first divergent sample, max abs error +
index, mean/RMSE, SNR, peaks, band error).

Coverage (fixtures): `source:vco_1` plus directed overrides
`vco_1.tuning`, `vco_1.mod_depth`, `vco_1.initial_phase`
(`spec/reference/directed-voice-v1.json`; 392 preregistered patches,
`spec/DIRECTED-FIXTURES.md:3-5`), covering unmodulated min/mid/max pitch;
depth extrema both signs; initial_phase at ±pi; pitch at MIDI 0, 69, 127;
phase-wrap crossing; zero-depth modulation. The no-modulation MIDI 151
case lives in intrinsic checks only (Open Question 9); the
`fundamental_in_band` provenance assertion applies to any above-Nyquist
case (`spec/PERIODIC-ESTIMATORS.md:20-24`).

- **M0 — trace identity.** Compare exactly the named traces
  `control_upsample.vco_1_pitch` (input) and `vco_1.raw` (output),
  176,400 samples each, per fixture (`spec/reference/trace-registry-v1.json:1091-1107`).
- **M1 — max abs sample error** `E = max_i |x_f32[i] - x_fix[i]|`, banded
  by fixture f0 (bands absorb the float reference's own cumsum drift,
  which grows with accumulated phase). **Calibrated 2026-09-21** per the
  C10 rule (smallest power of two ≥ 2x the measured value per band) over
  the sweep receipts, replacing the 2026-09-19 draft numbers pre-freeze;
  the draft numbers were measured UNREACHABLE: the float reference's own
  binary32 cumsum drift (increment-rounding bias + stored-partial wander,
  ≈ 7.78e-4 rad at 440 Hz over the full clip, up to 6.92e-3 rad at the
  depth fixture) exceeds the draft band-1/2 limits, so no candidate could
  separate on them at any word width (`sim/candidates/audio-sources-sweep-v1.json`
  policy notes and per-row `reference_wander_attribution`, issue #51 /
  PR #153). The recalibrated bands absorb that reference drift by
  attribution — they do not hide it: each band carries its measured floor
  and the drift attribution row that justifies it. Per oscillator class:
  - vco_1 (sine path, drift amplification 1.0):
    - f0 ≤ 1 kHz: `E ≤ 2^-9` (1.953e-3; measured max 8.857e-4 at the
      initial_phase fixture, drift floor 7.778e-4);
    - 1 kHz < f0 ≤ 5 kHz: `E ≤ 2^-7` (7.813e-3; measured max 3.016e-3 at
      the tuning fixture, drift floor 3.111e-3);
    - 5 kHz < f0 ≤ 12.6 kHz: `E ≤ 2^-6` (1.563e-2; measured max 6.844e-3
      at the depth fixture, drift floor 6.915e-3 — the draft 2^-4 rested
      on the conservative ≈ 0.05 rad wander estimate; the measurement
      binds tighter).
  - vco_2 (distortion path; the reference's stored-partial wander is
    amplified by the partials scale — amplification 16.21 at the 440 Hz
    saw/square fixtures, 0.03 at the high-frequency depth fixture):
    - f0 ≤ 1 kHz: `E ≤ 2^-5` (3.125e-2; measured max 1.423e-2, drift
      attribution 7.913e-3 square-term wander);
    - 1 kHz < f0 ≤ 5 kHz: **open / NO VERDICT** — no vco_2 fixture in
      this band was measured by the v0 sweeps; the band is defined but
      uncalibrated;
    - 5 kHz < f0 ≤ 12.6 kHz: `E ≤ 2^-11` (4.883e-4; measured max
      2.258e-4 at the high-frequency depth fixture).
  - SNR sub-limits: the draft `SNR ≥ 100 dB` (band 1) and `≥ 80 dB`
    (band 2) floors are unreachable at the reference drift floor
    (measured 70.7/60.7 dB worst) and the C10 power-of-two rule does not
    produce dB floors; they stay **open / NO VERDICT** pending the
    composed-model calibration. The E limits above govern M1.
  - Scope: calibrated from the five committed directed fixtures plus the
    normalization-stress anchor (the v0 sweeps' coverage); re-calibration
    over the full preregistered fixture suite precedes any RTL freeze
    (thresholds never increase after freeze).
- **M2 — intrinsic waveform accuracy** (generator-time, fixed model vs
  float64 `math.cos` over the full 2^32 phase circle). **Measured per
  geometry** on 2^19-point bounded sweeps against the analytic bound
  (`sim/candidates/audio-sources-sweep-v1.json`, `intrinsic.m2_lut_vs_cos`,
  issue #51 / PR #153):

  | Geometry | Measured max vs cos | Analytic bound | vs draft limit 2^-21 |
  | --- | --- | --- | --- |
  | 1024 x 24 linear | 5.057e-7 | 5.326e-7 | **FAIL** |
  | 1024 x 24 quadratic (pre-authorized fallback) | 2.531e-7 | 5.326e-7 | PASS |
  | 2048 x 24 linear | 3.018e-7 | 3.120e-7 | PASS |
  | 2048 x 24 quadratic | 2.497e-7 | 3.120e-7 | PASS |
  | **4096 x 24 linear (selected, C5)** | **2.456e-7** | 2.568e-7 | PASS |
  | 4096 x 24 quadratic | 2.628e-7 | 2.568e-7 | PASS |

  The selected geometry meets the declared measured target (≤ 2.6e-7).
  1K-linear fails, so the pre-authorized fallback remains the **1K
  quadratic** (measured 2.531e-7). Calibrated threshold (C10 rule,
  smallest power of two ≥ 2x the measured value of the selected
  geometry): `2^-20` (9.537e-7), replacing the draft `2^-21` pre-freeze
  (2x the measured 2.456e-7 is 4.912e-7 > 2^-21).
- **M3 — intrinsic phase accuracy** (constant f): fixed phase vs exact
  `f*n/fs` modulo 1 turn: `|dphi| ≤ 176400 * 2^-33 turns`
  (= 2.05e-5 turns = 1.29e-4 rad over the whole clip). **Measured
  2026-09-21** (`sim/candidates/audio-sources-sweep-v1.json`,
  `intrinsic.phase_resolution_m3_style`): the selected u32 width holds the
  declared bound at all three probe frequencies (worst measured
  1.6913e-5 turns at 27.5 Hz, vs bound 2.0536e-5); u28 (2.983e-5) and
  u24 (2.899e-3) exceed it, confirming C2's width is load-bearing. The
  declared bound stands (M3 is not a C10-calibrated band; the policy names
  M1/M2), with measurement margin 1.21x.
- **M4 — modulation crosswalk.** At MIDI + depth extremes the fixed output
  equals the analytic clamped-frequency prediction within the M1 band
  thresholds; zero samples fall outside the MIDI [0, 127] clamp.
- **M5 — pitch property.** Unmodulated fixtures: measured f0 (estimator
  grid of `spec/PERIODIC-ESTIMATORS.md:20-24`) within 1e-5 relative of
  requested; `fundamental_in_band` assertion for above-Nyquist cases.
- **M6 — schedule.** Complete-clip execution asserted over 176,400
  samples; the concrete per-sample budget is a #63 deliverable and is
  referenced, not invented, here.
- **M7 — negative controls.** Wrong LUT entry, dropped phase increment,
  un-clamped pitch, and ZOH-instead-of-endpoint-aligned input each **fail**
  M0–M5 under the selected model; a mutation set that does not fail is a
  metric defect, not a pass.

**Calibration policy (measure, then preregister):** after the candidate
fixed model exists, run all fixtures, then preregister final thresholds as
the smallest power of two ≥ 2x the measured M1/M2 value per band —
**before any RTL freeze**. **Applied 2026-09-21** over the v0 sweeps
(directed-fixture-plus-anchor coverage; the M1 scope note above states
what that coverage is): the M1 bands, the M2 threshold, and the C9
reciprocal threshold are that rule's outputs over
`sim/reference/control-format-sweep-v1.json`,
`sim/candidates/audio-sources-sweep-v1.json`, and
`sim/reference/normalization-reciprocal-v1.json`; the composed-model,
full-preregistered-suite run remains owed before any RTL freeze and may
recalibrate pre-freeze. Thresholds are never increased after RTL
freeze; a miss is a mismatch (`spec/PAIRED-METRICS.md:3-4`), never a
tolerance problem. Insufficient evidence is `NO VERDICT`, never a pass
(DR-0004).

Declarations #74 and #75 owe before their RTL (same ladder, same policy):
#74 — per-approximation intrinsic bounds for `tanh`, sin reuse, cos reuse
vs float64 over the phase circle; `partials_constant`
(`12000 / (max_f0 * log10(max_f0))`, pinned
`torchsynth/module.py:713-741`) as a declared-precision control-rate
scalar with rational bounds; shape boundary behavior at 0 and 1; banded
M1-style fixtures plus a directed spectral/alias negative control. #75 —
exactness per Section 7, no error metric.

**Amendment — the #74 transcendental declaration item is resolved by
decision (2026-09-22, issue #74 remainder, tracking issue #172; reviewed
merge is the ratification gate).** The approximation-selection item above
is closed by ratifying the **host-replay shadow boundary**, not by sweep:
the transcendental sub-expressions — the `exp2` of both VCOs' pitch
paths, `partials_constant`, and `tanh` (with its single-rounding fanout)
— are host-replayed deterministic words at the declared shadow boundary
(the #70/#71/#73 shadow-replay pattern, already landed and
digest-equality-proven against `fixed-voice-golden-v1` for the ADSR,
LFO/VCA, modulation-matrix, and sine-VCO lanes — PRs #161, #164, #166,
#169; the square/saw lane that raised the #74 item is likewise
landed, by merged PR #167). The replayed words are
the frozen model's
own words — bit-exact by construction — so no per-approximation
intrinsic bound is owed for the shadow replay itself; the sin-reuse and
cos-reuse bounds are likewise subsumed, because the RTL's LUT + linear
interp is the model's own integer C5 path, not an approximation of it
(the `intrinsic.m2_lut_vs_cos` measurement stays the recorded C5
intrinsic bound). The #74 sentence's non-approximation items (shape
boundary behavior at 0 and 1; banded M1-style fixtures; directed
spectral/alias negative control) are demonstrated by PR #167's committed
vectors (half-even tie/LSB/boundary-selector vectors, clamp-riding
alias-regime fixtures, detected mutations), now merged into main via
PR #167; the ratification gate for this record is this amendment's
own reviewed merge. The **recorded alternative**
— RTL-internal `exp2`/`tanh` approximation — stays **rejected-for-now**
on schedule/evidence grounds: no candidate approximation has M2-style
sweep evidence, while the shadow replay costs none. Any future adoption
(a later nebula/hardware iteration) requires its **own approximation
decision record plus fresh M2-style sweeps plus M1/M2 band recalibration
per §13 before any affected RTL change**. This amendment edits this
record's bytes only, so per §13 (as amended above) it triggers no
numeric gate; the procedural contract-digest rebinding it does trigger
is performed by this same change — every committed
`dr_0008_record_sha256` pin (the frozen receipt's bindings and the
per-case receipts, digest-field updates per the PR #159 precedent) is
re-bound to this amended record's digest. The #75 declaration is
unchanged.

## 11. Holdout

Sealed holdout cases are excluded from every fixture list in this record
and are not referenced by any threshold or coverage row here; ratification
of this DR neither reads nor unseals holdout evidence (issue #53 AC).

## 12. Choice register (machine-readable status)

Per issue #53, every choice is data: status is `proposed` / `selected` /
`accepted` / `rejected`. `selected (operator ruling 2026-09-19)` records
a choice fixed by the operator ruling of that date; it is still not
`accepted` — a choice becomes `accepted` only when this record's status
reaches Accepted by reviewed merge. **This record reached Accepted by the
reviewed merge of the issue #53 ratification PR (2026-09-21)**, so every
register entry is now `accepted (reviewed merge; 2026-09-21)` and the
machine-readable register
(`spec/reference/fixedpoint-choices-v1.json`) carries
`dr_status: "Accepted"` with per-entry `status: "accepted"`;
`torchsynth_voice.fixedpoint.choices.require_accepted` admits them.
Consumers must refuse `proposed`,
stale, and any not-yet-`accepted` value where the contract requires
`accepted`. Evidence artifact IDs are issue/comment or spec references;
affected traces are registry names.

| ID | Choice | Status | Evidence | Alternatives | Affected traces | Change trigger |
| --- | --- | --- | --- | --- | --- | --- |
| C1 | Audio word 24-bit Q2.21 [-4, +4) | accepted (reviewed merge; 2026-09-21) | `spec/decision-records/0006-canonical-runtime.md:89`; anchor saturation zero at Q2.21 (`sim/candidates/audio-sources-sweep-v1.json`) | 16-bit s1.15 (rejected for proposal: cannot hold measured peak 3.9478583336 without distortion; measured: 26,158 saturated samples at the anchor); 32-bit (deferred: cost) | all audio-rate traces | new peak evidence outside [-4, +4) |
| C2 | Phase: u32 wrapping, 2^32 units/turn | accepted (reviewed merge; 2026-09-21) | #83 comment 5743848538; DR-0004; measured M3-style rows: u32 within bound, u24/u28 exceed (`sim/candidates/audio-sources-sweep-v1.json`) | float32-cumsum emulation (rejected for proposal) | `vco_1.raw`, `vco_2.raw`, phase ports | new phase-semantics evidence |
| C3 | Frequency word Q16.15 | accepted (reviewed merge; 2026-09-21) | #83 groundwork Section 3.1 | narrower f-word (rejected: cannot hold 50,174 Hz) | `control_upsample.vco_1_pitch` consumers | upstream clamp evidence change |
| C4 | MIDI domain Q10.21 | accepted (reviewed merge; 2026-09-21) | pinned `torchsynth/module.py:600` via #83; midi-domain baseline member passes all sweep rows (`sim/reference/control-format-sweep-v1.json`) | float MIDI path (rejected: precision without bound) | keyboard/tuning/depth paths | fixture evidence of clamp mismatch |
| C5 | Sine: 4096x24 quarter-wave LUT + linear interp; 1K quadratic fallback pre-authorized | accepted (reviewed merge; 2026-09-21) | Section 5 table; measured M2 per geometry (Section 10; `sim/candidates/audio-sources-sweep-v1.json` `intrinsic.m2_lut_vs_cos`) | CORDIC; minimax polynomial; 1K-linear (measured M2 FAIL) | `vco_1.raw` | M2 sweep or #82 memory evidence |
| C6 | Rounding: half-even at S1–S5 | accepted (reviewed merge; 2026-09-21) | Section 6; rounding-mode axis (`sim/reference/control-format-sweep-v1.json`: truncation member loses to the half-even baseline) | per-site half-away-from-zero | all narrowing sites | directed fixture requiring an exception |
| C7 | Saturation + sticky counters; Nyquist clamp forbidden | accepted (reviewed merge; 2026-09-21) | #83 groundwork Section 1; per-site sticky counters measured (`sim/candidates/audio-sources-sweep-v1.json`) | clamp-to-Nyquist (rejected: not upstream) | saturate counters, `vco_*.raw` | upstream behavior change at pin |
| C8 | Noise: host-fed exact stream, slot `sound_index % 32`, seed 13 | accepted (reviewed merge; 2026-09-21) | `spec/VOICE-CONTRACT.md:75-77`; DR-0003; noise identity rows exact-bytes (`sim/candidates/audio-sources-sweep-v1.json`) | on-chip generator (needs its own DR) | `noise.raw` | new noise-policy decision record |
| C9 | Normalization replay gain `1/peak`, declared-precision reciprocal — measured: reciprocal-multiply, 22 frac bits, gain word U1.22 (width 23), half-even at S5 | accepted (reviewed merge; 2026-09-21) | DR-0003; `spec/decision-records/0003-host-boundary-and-normalization.md:40-44`; measured recommendation (`sim/reference/normalization-reciprocal-v1.json`, issue #52 / PR #150) | limiter/AGC (rejected by DR-0003); F=12 (measured: misses draft band); direct division (costlier, same floor) | final output, replay traces | reciprocal precision measurement |
| C10 | Thresholds: 2x-measured power-of-two, preregistered pre-freeze | accepted (reviewed merge; 2026-09-21) | Section 10; DR-0004; applied over the v0 sweeps (Section 10 as amended 2026-09-21) | fixed a-priori thresholds | M1/M2 bands | new calibration-policy evidence |

## 13. Change control

Which edits require what (issue #53 AC):

- Any change to a word format (C1, C3, C4), phase semantics (C2), or the
  approximation selection (C5 interface): a new/amended decision record
  **plus** regeneration of affected vectors (fixtures, LUT hash) and a
  fresh M2/M3 sweep **plus** recalibration of M1/M2 bands. Such edits are
  a new "profile" for trace-registry consumers. A decision that moves a
  transcendental sub-expression across the declared host/RTL boundary
  without changing any numeric value (the 2026-09-22 shadow-lane
  ratification, §10) does **not** trigger the numeric
  approximation-selection gates just stated: no vector value, LUT hash,
  or M1/M2 band changes, so every committed vector, LUT hash, and band
  applies verbatim, and re-entering them is the entry requirement of
  the recorded RTL-approximation alternative. It **does** trigger the
  procedural contract-digest rebinding: the committed receipts pin
  `dr_0008_record_sha256` to this file's bytes and refuse to validate
  after any edit here, so the amending record re-pins those digest
  fields to the amended record's digest — the machinery's own
  "regenerate" prescription, discharged by rebinding alone when (as in
  the §10 ratification) no frozen word changes.
- Any change to rounding sites or modes (C6), saturation policy (C7), or
  the threshold calibration rule (C10): amended DR + rerun of all
  directed fixtures' affected metrics (new vectors) + updated rubric rows
  in the measurement plan before RTL re-verification.
- Any change to the noise policy (C8) or normalization mechanics (C9):
  their own decision record (DR-0003 amendment or new DR) before any
  affected RTL changes.
- Table-content-only changes within the pre-authorized LUT fallback:
  regenerate the hash, rerun M2/M7, append to the Choice Register; no new
  DR if the interface and error gates are unchanged.
- DR status must reach Accepted (reviewed merge; the Builder does not
  approve its own PR) before any RTL implementation is called conformant
  to this contract.

**Emission record (2026-09-21).** With this record Accepted and every
register entry `status: "accepted"`, the refusal-gated constants emitter
(`tools/generate_rtl_constants.py` over
`src/torchsynth_voice/fixedpoint/codegen.py`) emitted the RTL constants
package for the first time:
`tb/sv/gf180_rtl_constants_pkg.sv`, SHA-256
`e6407c0af91d5e08cb49705f515e6d0d52852884c29e38616858338174107167`,
covering all of C1–C10 with no per-choice refusals (C8 and C10 are
interface/policy choices with no numeric parameters and correctly emit no
constants). The emitted widths were verified against the register: C1
width 24 (Q2.21), C2 width 32 (u32), C3 width 32 (Q16.15), C4 width 32
(Q10.21), C5 4096x24 with 12 index + 18 interp bits on a 32-bit phase
circle, C9 gain word width 23 (U1.22). `--check` mode now fails on any
register/package divergence.

## 14. Open questions for the operator (answered 2026-09-19 — see Operator ruling)

Each: recommended default, alternative, and consequence of the answer.

1. **Fixed-model phase semantics.** Default: wrapping u32 accumulator
   (Section 4). Alternative: bit-exact float32-cumsum emulation. Impact:
   propagates to every trace comparison and to #79's bit-identity claim;
   emulation forces float32 adders into RTL and makes the reference's own
   drift (≈ 0.05 rad at top-of-band) the dominant error everywhere.
2. **Internal audio word.** Default: 24-bit Q2.21 [-4, +4). Alternatives:
   16-bit (cannot hold measured peak 3.9478583336 without distortion);
   32-bit (area cost on gf180). Impact: must be settled before #63 freezes
   interfaces; 16-bit changes saturation evidence; 32-bit defers area
   measurement to #82.
3. **Sine approximation.** Default: 4096x24 quarter-wave LUT + linear
   interp, with the 1K-entry quadratic fallback pre-authorized.
   Alternatives: CORDIC (no memory, subtler proof); polynomial (provenance
   burden). Impact: M2 gate is unchanged for any choice; fallback changes
   only hashed table contents.
4. **Rounding mode.** Default: round-half-even at all sites S1–S5.
   Alternative: named per-site half-away-from-zero exceptions. Impact: any
   exception must be enumerated per site in this DR; silent exceptions are
   contract violations caught by M0/M7.
5. **Threshold calibration policy.** Default: measure the candidate fixed
   model, then preregister 2x-measured power-of-two thresholds before RTL
   freeze. Alternative: fixed a-priori thresholds. Impact: a-priori limits
   risk either looseness or guaranteed failure; the measured-preregister
   rule is the DR-0004-compliant mechanism; thresholds never increase
   after freeze.
6. **Internal fixed-model trace ports** beyond the 32 upstream traces
   (e.g. raw phase, pre-narrowing accumulator). Default: permitted as
   model/RTL-only observability without a trace-registry schema change,
   provided upstream trace identity and ordering are preserved.
   Alternative: require a registry schema extension. Impact: affects
   evidence granularity for M3/M7; no consumer-facing change either way.
7. **#75 noise policy.** Default: host-fed exact stream (DR-0003).
   Alternative: on-chip generator that must reproduce the CPU `torch.rand`
   bitstream for seed 13 exactly. Impact: a generator needs its own
   noise-policy DR before any #75 RTL; host-fed adds a transport
   obligation (#63), not a numeric one.
8. **Float-reference runtime.** Default: all float→fixed fixtures
   generated under `release-mkl-compatible-v1`
   (`spec/decision-records/0006-canonical-runtime.md:39-40`), pending the
   #88 / DR-0007 reconciliation. Impact: if reconciliation selects a
   different canonical runtime, fixtures are regenerated and M1/M2
   recalibrated once; thresholds do not silently carry over.
9. **Above-Nyquist fixtures.** Default: MIDI 151 (≈ 50,174 Hz) required in
   intrinsic M2/M3 only. Alternative: also required in float-vs-fixed M1.
   Impact: in M1 the float reference's own alias/cumsum behavior
   dominates; requiring it there needs a dedicated drift-dominated band or
   an extended M1 table.

## 15. Ratification record (2026-09-21)

This section records, per row, what the reviewed merge of the issue #53
ratification PR accepts and what stays explicitly open. The evidence base
is exactly the three v0 sweep receipts — `sim/reference/control-format-sweep-v1.json`
(issue #50, PR #152), `sim/candidates/audio-sources-sweep-v1.json`
(issue #51, PR #153), `sim/reference/normalization-reciprocal-v1.json`
(issue #52, PR #150) — and nothing beyond them is ratified. Per-row
honesty rule: ratify what the evidence supports; leave genuinely-open
rows open as `NO VERDICT`, never force-ratified.

| Row | Outcome | Evidence |
| --- | --- | --- |
| M1 (max abs sample error) | **Calibrated** E-limits per oscillator class and band (Section 10 as amended): vco_1 2^-9 / 2^-7 / 2^-6; vco_2 2^-5 / open / 2^-11. Bands absorb the measured reference cumsum drift by attribution (per-row `reference_wander_attribution`), not by hiding it. SNR floors and vco_2 band 2 stay **open / NO VERDICT**. | #153 drift-floor finding + attribution rows; calibrated by the C10 rule over the measured per-band maxima |
| M2 (intrinsic waveform) | **Calibrated** threshold 2^-20 (per-geometry measured table in Section 10; selected geometry passes the declared measured target). | #153 `intrinsic.m2_lut_vs_cos` |
| M3 (intrinsic phase) | **Confirmed** at the declared bound (176400 x 2^-33 turns) for the selected u32 width; u24/u28 measured exceeding it. | #153 `intrinsic.phase_resolution_m3_style` |
| M4 (modulation crosswalk) | **Open / NO VERDICT** — MIDI-domain fixed arithmetic was explicitly out of the swept budget; no measured crosswalk exists. | #153 policy note 1 |
| M5 (pitch property) | **Open / NO VERDICT** — the qualified periodic estimator grid has not run on fixed-model outputs. | sweep rows carry `estimator: null` |
| M6 (schedule) | **Open / NO VERDICT** — the per-sample budget is a #63 deliverable, referenced, not invented. | Section 9 |
| M7 (negative controls) | **Open / NO VERDICT** — 2 of the 4 preregistered mutation probes ran (wrong LUT entry 1128x, dropped phase increment 2258x, both detected); the un-clamped-pitch and ZOH probes and the full M0–M5 failure demonstration are still owed. | #153 `mutations` rows |
| M0 (trace identity) | **Open / NO VERDICT** — the sweeps are module-level candidate models; no composed fixed Voice emits the registry-named trace set per fixture yet. | #153 declared boundaries |
| C9 reciprocal precision | **Ratified** F=22 / U1.22 (width 23) with branch coverage below/at/above one, tie, late peak, extremum, silence. | #150 receipt `decision` block |

**DR-0003 remains Proposed.** Its own acceptance text
(`spec/decision-records/0003-host-boundary-and-normalization.md:38-44`)
requires, before acceptance: replay determinism for every stateful module;
cycle, SRAM, and energy cost of replay versus buffering; exact noise
stream requirements; division/reciprocal precision; and the UI/transport
cost of a 78-parameter resolved patch. The #52 receipt delivers the
reciprocal-precision item (and noise-exactness evidence), but it explicitly
defers replay-versus-buffering cost to #63 and makes no host-transport
measurement; its own disclaimers state that no DR-0003 acceptance is
performed and that the acceptance decision belongs to the operator.
Issue #52 therefore stays open on exactly that residual scope.

**Rubric binding.** `spec/reference/rubric-v0.json` remains frozen per its
own bump rule; the calibrated R-L3-M1/M2/M3 rows are bound as
`spec/reference/rubric-v1.json` (the bump target v0's `change_control`
names), whose assertions cite these receipts by digest and recompute.
R-L3-M0/M4/M5/M6/M7 stay `open` with `NO VERDICT` verdict rules. The
holdout seal is unaffected: no holdout artifact is read, referenced, or
unsealed by this ratification (Section 11).

## Citation basis

Repository citations use `path:line` form verified against `origin/main`
of this repository (working tree identical). Upstream citations use
`torchsynth/<file>:<line>` at pin `2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`,
verified via the git-show extraction and SHA-256 hash checks documented in
[#83, comment 5743848538](https://github.com/2AMLogic/gf180-torchsynth/issues/83#issuecomment-5743848538);
this record does not re-derive those hashes. This record makes **no**
gf180mcu synthesis, layout, signoff, hardware playback, or sound-fidelity
claim; area/fit evidence belongs to #82, timing to #83 (PnR lane), and
cycle budgets to #63.
