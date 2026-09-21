# DR-0010: One-shot RTL microarchitecture and schedule

- Status: Accepted (reviewed merge; 2026-09-21) — completes and supersedes the
  2026-09-20 draft skeleton; P5 and the #82/#83 validations named below remain
  explicitly open inside the accepted record
- Date: 2026-09-20 (skeleton); amended 2026-09-21 (issue #63 ratification)
- Decision owners: 2AM Logic
- Scope: the one-shot RTL microarchitecture ratified by issue #63 — module
  interfaces and ownership, fixed formats (inherited, unchanged), the
  cycle/sample schedule with its feasibility bound, the normalization replay
  architecture and its replay-versus-buffering cost analysis, the memory
  strategy with its recorded basis, clip lifecycle (backpressure/error/reset),
  trace/debug visibility, and the host protocol binding shape. This record
  changes nothing about the numeric contract (DR-0008, Accepted), the host
  boundary and normalization semantics (DR-0003, Accepted by the same
  reviewed merge under the operator's standing delegation of 2026-09-20),
  substrate governance (DR-0005), the one-shot profile (DR-0002), or the
  canonical runtime (DR-0006). It makes no gf180mcu synthesis, layout,
  signoff, hardware playback, or sound-fidelity claim: area/fit/timing
  evidence belongs to #82/#83 (P5, still pending measurement), and no PPA or
  fit claim is made from the estimates here.

## Problem

#63 owed the ratified microarchitecture: every fixed-model operation and
state mapped to one RTL owner with a cycle/sample contract, a cycle-defined
noise/replay/reset and normalization schedule, worst-case resource and cycle
estimates, and a host binding — with the normalization architecture decision
carrying the replay-versus-buffering cost evidence DR-0003 required before
acceptance (`spec/decision-records/0003-host-boundary-and-normalization.md:38-44`).
The 2026-09-20 draft skeleton held the interface facts that were already
decidable and named P1–P5 as `pending measurement` with owning issues.

Every premise the skeleton waited on is now closed on `origin/main`:

- #50/#51 (control-path and audio-source sweeps) landed the calibrated bands;
- #52 landed the reciprocal-precision measurement (PR #150;
  `sim/reference/normalization-reciprocal-v1.json`), recommending — and
  DR-0008 §15 ratifying — reciprocal-multiply F=22, gain word U1.22
  (width 23), half-even at S5;
- #53 ratified DR-0008 (PR #154), making every register entry `accepted`;
- #54 froze the candidate fixed whole-voice model and bit-exact golden
  vectors (PR #156; `sim/reference/fixed-voice-golden-v1.json`);
- #62 landed protocol v2 with numeric wire formats bound to the accepted
  register (PR #155);
- #52's one residual — the replay-versus-buffering cost — was explicitly
  deferred to this record (DR-0008 §15,
  `spec/decision-records/0008-fixed-point-numeric-contract.md:519-529`;
  PR #150's tradeoff-ownership rows). This record delivers it (P4), and that
  delivery is the cost item DR-0003's acceptance required.

This record closes P1, P3 and P4 as decided (the schedule as a labeled
schedule-candidate, refutable per the stop-at-first-failed-premise rule),
closes P2 as proposed with a recorded basis, and leaves P5 `pending
measurement` (#82/#83) without blocking the schedule's refutable framing.

## Grounded facts (inherited, unchanged from the skeleton)

The skeleton's grounded section is preserved with one status correction: the
DR-0008 register is no longer pending — it reached Accepted by reviewed merge
(2026-09-21), so every A-row below is `grounded (accepted upstream)`. The
values are unchanged; only their contract strength moved.

| Quantity | Accepted format | Evidence |
| --- | --- | --- |
| Audio sample word (all audio-rate and control-rate signals) | 24-bit Q2.21, range [-4, +4), LSB 2^-21 | register C1 (`spec/reference/fixedpoint-choices-v1.json:5`); peak evidence 3.9478583336 (`spec/decision-records/0006-canonical-runtime.md:89`) |
| Phase accumulator (per oscillator) | unsigned 32-bit, modular wrapping, 2^32 units/turn, initial phase injected at sample 0 | register C2; `src/torchsynth_voice/fixedpoint/phase.py` |
| Frequency word (control→VCO interface) | 32-bit Q16.15 | register C3 |
| MIDI-domain control arithmetic | 32-bit Q10.21 | register C4 |
| Multiplier/accumulate internals | 24x24 → 48-bit products; 48-bit-class accumulators in VCA/mixer lanes; no intermediate rounding before a declared narrowing site | DR-0008 Section 2 |
| Rounding / saturation | half-even at every declared site S1–S5; saturation with sticky per-site counters; phase/frequency never saturate; Nyquist clamp forbidden | registers C6, C7 |
| Sine | 4096x24 quarter-wave LUT + integer linear interpolation (12 index + 18 interp bits on the 32-bit phase circle); 1K quadratic fallback pre-authorized | register C5; emission record (`spec/decision-records/0008-fixed-point-numeric-contract.md:429-442`) |
| Normalization gain | reciprocal-multiply, F=22 frac bits, gain word U1.22 (width 23), half-even at S5, strict `peak > 1` branch on the Q2.21 grid | register C9; measured receipt (`sim/reference/normalization-reciprocal-v1.json`, issue #52 / PR #150) |
| Noise | host-fed exact binary32 stream, slot `sound_index % 32`, seed 13 | register C8; `spec/VOICE-CONTRACT.md:75-77` |
| One-shot grid | 176,400 mono samples @ 44,100 Hz per trigger (4.000 s), 441 Hz control rate, exact 100x endpoint-aligned upsampling, one resolved sound per trigger | DR-0002 (`spec/decision-records/0002-one-shot-product-profile.md:27-35`); `spec/VOICE-CONTRACT.md:20-32`; DR-0008 Section 9 |

Consumers still refuse register values wherever a contract requires
re-verification against a digest (`src/torchsynth_voice/fixedpoint/README.md:37-49`
refusal-gate discipline); the RTL constants package
(`tb/sv/gf180_rtl_constants_pkg.sv`, SHA-256
`e6407c0af91d5e08cb49705f515e6d0d52852884c29e38616858338174107167`) is the
emitted instantiation of C1–C10 and `--check` fails on divergence.

**Composition and vectors.** The frozen model composition
(`sim/reference/fixed-voice-golden-v1.json:20`, identity `fixed-voice-v1`):
`FixedControlPath` (#50 certified baseline) → mod matrix → endpoint-aligned
uQ.31 upsample (`src/torchsynth_voice/fixed_voice.py:62`) → fixed sources
(u32 phase, Q16.15 frequency words, C5 LUT) → exact host-fed noise → three
VCAs → mixer → C9 normalization replay. The committed sentinel vector
(`sim/reference/golden-vector-fixed-anchor.json`) and the 34-case manifest
with per-trace digests are the RTL-facing bit-exactness targets; every
schedule and cost number in this record is derived from that composition's
operation counts, not from any RTL (none exists).

**Host binding shape.** #62's protocol v2 (PR #155) binds the wire: transport
separation, name-keyed patch load over the 78-name canonical inventory
(`spec/protocol/PATCH-LOAD.md:5-24`), session lifecycle/idempotency/error
semantics (`spec/protocol/SESSION.md:7-13`), and numeric wire formats bound
to the accepted register. The render trigger and noise-stream transport
commands are deliberately absent from that subset; the obligations this
record places on them are named in "Clip lifecycle" below and belong to the
#66 transport lane.

**Substrate governance.** DR-0005 (Accepted) governs all substrate reuse;
any memory model or flow tool this microarchitecture later adopts lands
under those gates.

## Choice register (machine-readable status)

Status vocabulary: `grounded (accepted upstream)` — an inherited accepted
fact this record changes never; `decided (schedule-candidate)` — decided
here from counted-operation evidence at declared candidate values, refutable:
the first failed premise stops downstream artifacts and forces amendment;
`proposed (recorded basis)` — a design selection with its basis recorded and
its validating measurement named; `pending measurement` — no decision
exists; the owning issue is named.

| ID | Choice | Status | Evidence | Owner / change trigger |
| --- | --- | --- | --- | --- |
| A1 | Audio word 24-bit Q2.21 [-4, +4), all audio-rate and control-rate paths | grounded (accepted upstream) | register C1 accepted 2026-09-21 | DR-0008 change path only |
| A2 | Phase accumulator u32 wrapping, 2^32 units/turn, initial phase injected at sample 0 | grounded (accepted upstream) | register C2 | DR-0008 change path only |
| A3 | Frequency word 32-bit Q16.15 at the control→VCO interface | grounded (accepted upstream) | register C3 | DR-0008 change path only |
| A4 | MIDI-domain control arithmetic 32-bit Q10.21 | grounded (accepted upstream) | register C4 | DR-0008 change path only |
| A5 | Sine: 4096x24 quarter-wave LUT + integer linear interp; 1K quadratic fallback pre-authorized | grounded (accepted upstream) | register C5; emitted constants (`tb/sv/gf180_rtl_constants_pkg.sv`) | DR-0008 change path; #82 memory evidence |
| A6 | Rounding half-even at every declared narrowing site (S1–S5) | grounded (accepted upstream) | register C6 | DR-0008 change path only |
| A7 | Saturation + sticky per-site counters; phase/frequency never saturate; Nyquist clamp forbidden | grounded (accepted upstream) | register C7 | DR-0008 change path only |
| A8 | 24x24 → 48-bit products; wide accumulators in VCA/mixer lanes; no intermediate rounding pre-narrowing | grounded (accepted upstream) | DR-0008 Section 2 | DR-0008 change path only |
| A9 | One-shot grid: 176,400 samples @ 44,100 Hz, 441 Hz control, mono, one resolved sound per trigger | grounded (accepted upstream) | DR-0002; `spec/VOICE-CONTRACT.md:20-32` | profile change requires its own DR |
| P1 | Per-sample cycle budget and clock | decided (schedule-candidate) | the budget equation and feasibility table below, derived from the frozen composition's counted ops (`sim/reference/fixed-voice-golden-v1.json`); candidate clocks 25/50/100 MHz declared, none selected | **#63 (this record)**; concrete clock selection amends on #82/#83 timing evidence |
| P2 | Memory architecture: macro SRAM vs distributed RAM for the sine LUT ROM, optional control-row retention, and per-module storage | proposed (recorded basis) | replay architecture (P4) eliminates the clip buffer; LUT is 12 KiB ROM; retention option costed below; macro-vs-distributed needs #82 | **#82** validates; amend on measurement |
| P3 | Time-multiplexing schedule across 2 VCOs + 6 ADSR envelopes + 2 LFOs + mod-matrix lanes | decided (schedule-candidate) | the module/owner/cycle-contract table and serialized schedule below, from the frozen composition's per-module op counts | **#63 (this record)**; refutable — stop at first failed premise |
| P4 | Normalization replay architecture: two-pass re-render vs render-to-buffer-then-scale vs recompute-and-apply | decided | **two-pass re-render** per DR-0003 (Accepted); cost analysis below is the acceptance evidence DR-0003:38-44 required, with #52's measured reciprocal (PR #150) fixing the S5 arithmetic | **#52 residual, closed by this record**; change only via a DR-0003 amendment |
| P5 | gf180 area/fit/PPA consequences of any datapath, memory or schedule row | pending measurement | #82 (synthesis/timing) and #83 (floorplan/place/route) | **#82/#83**; no PPA or fit claim from estimates (issue #63 AC) |

## Module interfaces, ownership, and cycle/sample contracts

Every fixed-model operation/state maps to exactly one RTL owner. Owner issue
numbers are the Epic #2 lanes; contracts are stated per sample (audio rate,
44,100 Hz) or per tick (control rate, 441 Hz; 1,764 ticks of 100 samples).

| Module (model source) | RTL owner | Cycle/sample contract |
| --- | --- | --- |
| Patch load, identity, reset, keyboard | #69 | control domain, once per trigger; S1 entry sites at trigger time |
| ADSR envelope engine (6 instances) | #70 | control rate: ≤ ~30 multiply-class ops + ≤ 18 declared `**alpha` shadow sites per tick across all 6 envelopes; duration received in advance (`spec/VOICE-CONTRACT.md:71`) |
| LFO (2) + control-rate VCA | #71 | control rate: ≤ ~14 multiply-class ops + 2 C5-class LUT interps + 2 phase steps per tick |
| Mod matrix (4x5) | #72 | control rate: 20 MACs + 5 declared narrowings per tick (`src/torchsynth_voice/format_sweep.py:563-598`) |
| Endpoint-aligned upsample (5 columns) | #72 | audio rate: 2 mults + 1 add + 1 half-even blend per column per sample; endpoints exact copies of control indices 0 and 1763 (`src/torchsynth_voice/format_sweep.py:600-687`, coordinate table `:207-228`); fraction word uQ.31 (`src/torchsynth_voice/fixed_voice.py:62`) |
| Sine VCO (vco_1 path) | #73 | audio rate: pitch row 3 mults / 4 adds / 2 narrows / 1 exp2 shadow + phase add + LUT interp 1 mult / 2 adds / 1 S4 narrow (rows below) |
| Square/saw VCO (vco_2 path) | #74 | audio rate: pitch row as vco_1 + shape row 6 mults / 5 adds / 5 narrows / 1 tanh shadow + second LUT interp (`src/torchsynth_voice/fixed_voice.py:390-426`); owns the declared exp2/tanh fixed-approximation items (the #74 declaration class, `src/torchsynth_voice/fixed_voice.py:15-27`) for both VCOs' pitch paths unless amended |
| Noise source | #75 | audio rate: streaming host-fed binary32 → Q2.21 convert (exponent shift + half-even round, no multiplier) + sticky counter; exact stream per C8 |
| Audio VCAs (3) + pre-normalization mixer | #76 | audio rate: 6 mults + 2 adds + 4 narrow sites; 48-bit-class accumulator; S4 rescale to the pre-normalization mix word |
| Normalization replay controller + one-shot top | #77 | two-pass schedule (P4): pass 1 peak tracking (1 compare + 1 abs-select folded into the mixer output), branch decision at sample 176,400, pass 2 replay with the U1.22 gain multiply + S5 narrow (normalized branch) or identity (unity branch) |
| Numeric/interface package + golden-vector harness | #68 | constants package digest-bound; per-trace digest comparison per `sim/reference/fixed-voice-golden-v1.json` |
| Module conformance / end-to-end bit identity | #78 / #79 | fixed-model→RTL sample-exact against the frozen vectors |

Control/audio rate crossing (issue #63 AC): the only crossing is the
five-column endpoint-aligned upsample; its contract — exact endpoints,
uQ.31 half-even blend, per-column declared narrowing — is the landed
model's, consumed unchanged. No zero-order hold exists anywhere in the
profile.

## Schedule (schedule-candidate — P1/P3)

**Counted per-audio-sample operations, pass 1** (derived from the frozen
composition's loop body, `src/torchsynth_voice/fixed_voice.py:371-450`, plus
the five upsample columns, `src/torchsynth_voice/format_sweep.py:600-687`;
"narrows" = declared rounding/narrowing sites incl. half-even blend):

| Block | Mults | Adds/compares | Narrows | Shadow sites |
| --- | --- | --- | --- | --- |
| vco_1 pitch path (depth-mod, clamp, MIDI→Hz, Q16.15 word, phase increment) | 3 | 6 | 3 | 1 exp2 |
| vco_1 phase + quarter-wave LUT + S4 | 1 | 2 | 1 | — |
| vco_2 pitch path | 3 | 6 | 3 | 1 exp2 |
| vco_2 shape path (2nd LUT interp, driven, tanh branch, left/right, product, S4) | 5 | 5 | 4 | 1 tanh |
| Noise convert (binary32 → Q2.21) | 0 | 0 | 1 | — |
| 3x audio VCA | 3 | 0 | 3 | — |
| Mixer (level mults, accumulate, S4) | 3 | 2 | 1 | — |
| 5-column endpoint-aligned upsample | 10 | 5 | 5 | — |
| **Pass-1 total per sample** | **28** | **26** | **21** | **3** |

Per control tick (amortized 1/100 per sample): ≤ ~80 multiply-class ops,
≤ ~24 declared shadow sites (the six `**alpha` powers dominate), ≤ ~30
adds/narrows — at every candidate clock the per-tick budget (100 × the
per-sample budget) exceeds this by more than an order of magnitude, so the
control path is never the binding constraint and the audio-rate loop below
is the whole feasibility question.

**Budget equation.** Serialized schedule candidate: one shared 24x24
multiplier, one shared round/narrow unit, one shared LUT read port.
Counted-arithmetic estimate C_counted ≈ 145 cycles/sample (28 mults x 3
pipeline slots + 26 adds + 21 narrows + compare folding + control
amortization). Per-sample budget:

    C = C_counted + T,   T = declared allowance for 2x exp2 + 1x tanh

Two passes always (P4: DR-0003 replays at unity too); pass 2 adds one
multiply + one narrow (≤ 4 cycles, folded into C). Clip budget asserted per
DR-0002/DR-0008 §9: **176,400 samples per clip = 352,800 sample-slots**:

    N_clip = 352,800 x C

**Feasibility at the declared candidate clocks (none selected; T = 100 and
the 1x real-time bound shown):**

| Candidate clock | Available cycles/sample | N_clip at T=100 (C=245) | Clip wall time | Clips/sec | Max T for ≤ 1x real-time (C ≤ f/88,200) |
| --- | --- | --- | --- | --- | --- |
| 25 MHz | 566.9 | 86.4 M cycles | 3.46 s (0.86x clip duration) | 0.29 | 138 |
| 50 MHz | 1,133.8 | 86.4 M cycles | 1.73 s (0.43x) | 0.58 | 422 |
| 100 MHz | 2,267.6 | 86.4 M cycles | 0.86 s (0.22x) | 1.16 | 989 |

The canonical profile is an offline one-shot clip (DR-0002), so no real-time
requirement exists to miss; the table states the margin, not a requirement.
The 1x-real-time column is the refutable bound: if the declared fixed exp2
and tanh approximations (the #74 declaration class; owners #73/#74, each
verified against the frozen M1 bands and golden vectors) cannot be built
within T ≤ 138 at the selected clock, the premise fails and the schedule
stops there — no downstream artifact may claim this budget. Rubric row
R-L3-M6 consumes a budget in exactly this form
(`spec/reference/rubric-v1.json:3490-3497`); binding it into a rubric
version is that document's own change control, not this amendment.

**Worst-case resource estimate from the same counts** (issue #63 AC —
estimates, no PPA/fit claim): 1 shared 24x24 multiplier + 1 narrow unit + 1
LUT port at the serialized candidate; parallelizing to k multipliers divides
the counted term approximately by k down to the transcendental floor. LUT
ROM 4096 x 24 b = 98,304 b (12.0 KiB). Noise ingest 705,600 B per pass
(1,411,200 B per clip over two passes) — at the slowest candidate and
T = 100 that is one byte every ~61 cycles; packed 24-bit output streams at
529,200 B per clip after the branch decision. Both are orders of magnitude
inside any plausible interface; the numbers are arithmetic on the declared
schedule, not interface claims.

## Normalization replay architecture (P4 — decided) and the DR-0003 cost evidence

**Decision: deterministic two-pass re-render** — DR-0003's semantics,
Accepted by the same reviewed merge as this record. Pass 1 renders the graph
once, tracking the peak of the pre-normalization Q2.21 mix (strict branch
`peak_int > 2^21`); at clip end the U1.22 gain word is formed once per clip
(`round(2^43 / peak)`, half-even) when — and only when — the branch is
taken; pass 2 deterministically replays the graph (unity or gain branch)
and applies the S5 narrowing. The three candidates:

| Candidate | Clip cycles (normalized) | Clip SRAM | Noise transport/clip | Energy class |
| --- | --- | --- | --- | --- |
| **two-pass re-render (selected)** | 352,800 x C (86.4 M at C=245) | live state only (< ~1 Kbit class: phase accumulators, envelope/LFO state, peak/gain words, sticky counters) | 2 x 705,600 B (host re-feeds the exact stream) | 2x pass energy |
| render-to-buffer-then-scale | 176,400 x C + 176,400 x 4 ≈ 43.9 M (C=245) | + 176,400 x 24 b = 4,233,600 b = **516.8 KiB** clip buffer | 1 x 705,600 B | 1x pass energy + ~8.5 Mbit buffer I/O |
| recompute-and-apply (single-pass running peak, retroactive gain) | — | — | — | **rejected**: samples cannot be emitted before the end-of-clip branch decision without a buffer or a semantic change; not bit-exact to DR-0003 |

At the 50 MHz candidate the replay costs +0.85 s per clip against buffering
and saves 516.8 KiB of SRAM — the dominant memory instance count on this
technology. Buffering the complete clip merely to normalize is excluded by
DR-0003 (`spec/decision-records/0003-host-boundary-and-normalization.md:21-23`),
so the decision follows from the accepted record; this analysis is the cost
quantification its acceptance required, delivered in the only honest form
available before RTL exists: computed from the frozen composition's counted
operations at declared candidate clocks (issue #63's AC explicitly admits
estimated cycle budgets and forbids only PPA/fit claims from estimates).
Absolute energy numbers are op-count classes here; measurement belongs to
#82 (P5).

**Replay determinism basis.** The frozen model's `render()` is a pure
function of the resolved request: phase accumulators inject their initial
phase at sample 0 (`src/torchsynth_voice/fixed_voice.py:335-341`), the
control path is a pure function of the physical map
(`src/torchsynth_voice/format_sweep.py:277`), and the noise stream is taken
from request bytes. PR #156's two byte-identical full regenerations
demonstrate total determinism; RTL replay determinism is the sample-exact
fixed-model→RTL contract checked against
`sim/reference/fixed-voice-golden-v1.json` (per-trace digests) by #78/#79.
The replay path stores no per-sample intermediate, so determinism cannot
drift through buffered state.

**Noise re-feed obligation (protocol implication, owner #66).** Pass 2
re-consumes the identical host-fed stream (C8): two passes x 705,600 B per
clip. The render-trigger and noise-stream transport commands are absent
from the landed protocol subset by design; when #66 defines them, they must
bind both passes of one trigger to one sound identity and one noise-stream
digest, with a pass-2 digest mismatch discarding the clip entire (see
"Clip lifecycle"). On-chip retention of the stream (binary32: 689 KiB; even
as dequantized Q2.21 words: 516.8 KiB) is rejected — it reintroduces the
clip-buffer cost P4 exists to avoid.

## Memory strategy (P2 — proposed with recorded basis)

- **No clip buffer** (consequence of P4): the only per-clip storage is live
  state — two 32-bit phase accumulators, LFO phase/state, six envelope ramp
  states, the peak and gain words, sticky counters — under ~1 Kbit.
- **Sine LUT: 12.0 KiB ROM** (4096 x 24 b), shared read port; the
  pre-authorized 1K quadratic fallback would be 3.0 KiB if #82 memory
  evidence favors it (register C5's own change trigger).
- **Control path re-rendered per pass** (zero retention). Recorded
  alternative, not selected: retain the five upsampled columns'
  control-rate sources (5 columns x 1,764 x ≤ 32 b = ≤ 34.5 KiB) to skip
  the control re-render in pass 2, trading ≤ 34.5 KiB RAM against ~2x
  control-path energy — the re-render is under 1% of the audio-loop budget,
  so the trade does not justify the RAM at any candidate clock.
- **Macro SRAM vs distributed RAM** for the LUT ROM (and any later retained
  structure): **pending #82** — this record proposes distributed-LUT-class
  storage for a 12 KiB ROM as the measured-fit default and defers the macro
  decision to #82's synthesis evidence, consistent with register C5's
  change trigger.

## Clip lifecycle: backpressure, error, reset, staleness (issue #63 AC)

- A trigger is bound to one sound identity + one noise-stream digest;
  pass 1 and pass 2 of that trigger and no others may write the output
  stream, and output release begins only after the branch decision — so no
  partially-normalized or mixed-clip output is representable.
- `RESET` (protocol `spec/protocol/SESSION.md`) discards all render state;
  the next trigger starts from the injected initial phases at sample 0.
  There is no resumable render: a pass aborted mid-clip leaves no state
  that a later trigger can silently continue.
- Backpressure is host-side only (`ERR_BUSY` semantics, landed subset); the
  core never blocks on a host mid-clip, and the noise re-feed of pass 2 is
  an idempotent re-send of the identical bytes under the same digest
  binding.
- A digest mismatch between the passes (or any `ERR_PATCH_HASH_MISMATCH`-
  class failure) discards the clip entire; the error taxonomy and recovery
  are the landed session semantics', unchanged.

## Trace/debug visibility (issue #63 AC)

RTL exposes, at minimum: every registry-named checkpoint trace the frozen
model emits (the 34-case manifest's trace set, in registry order), the
sticky per-site saturation/rounding counters, the shadow-site tallies, and
the normalization diagnostics (peak word, gain word, branch decision) —
the exact surfaces `sim/reference/fixed-voice-golden-v1.json` digests. A
bit mismatch localizes to the first diverging trace by per-trace digest
comparison (#68 harness); this is model/RTL-only observability within
DR-0008 §14.6's permitted class and adds no consumer-facing trace.

## Host protocol binding (issue #63 AC)

The binding is #62's landed protocol v2, consumed unchanged: frame
envelope, session lifecycle, name-keyed 78-parameter patch load with
hash-bound atomic commit, idempotent repeat/retry, `numeric_contract_version`
bound to the accepted register. This record adds the one architectural
obligation the subset deliberately deferred: the render trigger and
noise-stream transport commands (#66 lane) must carry the pass/digest
binding of "Clip lifecycle" above. Live-note semantics remain absent and
forbidden in this profile (`spec/protocol/SESSION.md:118-125`).

## Change control

- A-row changes: the DR-0008 change path first, then an amendment here.
- P4 (normalization architecture): change only via a DR-0003 amendment —
  normalization mechanics require their own decision record before any
  affected RTL (DR-0008 §13).
- P1/P3 (budget/schedule): amendment required before downstream artifacts
  if any premise fails — a declared approximation exceeding the applicable
  T bound, new peak evidence outside [-4, +4), a composition change in the
  frozen model, or #82/#83 timing evidence displacing the candidate clocks.
  The schedule stops at the first failed premise; downstream artifacts
  produced after a failed premise carry no valid claim.
- P2: amendment on #82's macro/distributed measurement.
- P5: closure requires committed #82/#83 evidence (synthesis/timing
  reports), never prose or estimates.
- This record authorizes no RTL commit, testbench, synthesis run, or
  workflow by itself; it is the contract the Epic #2 lanes (#68–#79)
  implement and verify against.

## Consequences

- Every fixed-model operation/state has exactly one RTL owner and a stated
  cycle/sample contract (issue #63 AC 1); the control/audio crossing keeps
  endpoint-aligned interpolation (AC 2); noise/replay/reset and both
  normalization passes are cycle-defined (AC 3); worst-case
  multipliers/adders/LUTs/RAM/bandwidth are estimated from counted
  operations (AC 4); clip staleness/mixing is structurally excluded (AC 5);
  trace visibility localizes bit mismatches per-trace (AC 6); the record
  cites the frozen golden vectors and the now-Accepted normalization
  decision (AC 7); and no PPA or fit claim is made from estimates (AC 8).
- Issue #52's residual — the replay-versus-buffering cost DR-0003 required
  — is delivered by P4, and DR-0003 reaches Accepted in the same reviewed
  merge; #52's acceptance criteria are thereby complete.
- Genuinely-open validation stays explicitly open and non-blocking: the
  concrete clock (#82/#83 timing), memory macro vs distributed (P2/#82),
  and all area/fit/PPA consequences (P5/#82/#83).
- Nothing numeric moved: the fixed vectors, constants package digest, and
  rubric files are untouched by this record; the frozen model remains the
  only executable definition of bit-exactness.

## Alternatives considered

- **Keep P1–P4 pending until #82 measures a real clock and memories.**
  Rejected: it inverts the dependency — #82's synthesis needs a ratified
  microarchitecture and schedule to synthesize; the schedule-candidate form
  with declared candidate clocks is exactly the refutable input #82 needs.
- **Select buffer-then-scale** (1x compute, 516.8 KiB SRAM). Rejected:
  DR-0003 excludes complete-clip buffering in the compatibility profile,
  and the SRAM cost is the dominant instance count on this technology; the
  replay premium is +0.85 s per offline clip at the 50 MHz candidate.
- **Single-pass running peak with retroactive gain.** Rejected: not
  bit-exact to the accepted normalization semantics; emitting samples
  before the branch decision requires either a buffer or a different
  (forbidden) normalization behavior.
- **Retain control-rate rows across passes.** Rejected (recorded above):
  ≤ 34.5 KiB RAM to save under 1% of the clip budget.
- **Decide the clock now.** Rejected: no timing evidence exists; P1 fixes
  the budget equation and three candidate clocks instead, with the
  selection trigger named.

## Ratification

Reviewed merge of the issue #63 ratification PR accepts this record in
full, including the P1/P3/P4 decisions and the P2 proposal, with P5 and
the #82/#83 validations explicitly open. The Builder does not approve its
own PR. The same merge performs the DR-0003 acceptance this chain deferred
(under the operator's standing delegation of 2026-09-20), citing #52's
measured reciprocal receipt and P4's cost analysis. Until that merge, this
record is Proposed and consumers must not instantiate RTL, testbenches or
flow runs against it.

## Citation basis

Repository citations use `path:line` form verified against `origin/main`
(head `1177061`, working tree identical, 2026-09-21). Upstream TorchSynth
claims are cited through this repository's records (DR-0002, DR-0003,
DR-0008, `spec/VOICE-CONTRACT.md`) and are not re-derived here. This record
makes **no** gf180mcu synthesis, layout, signoff, hardware playback, or
sound-fidelity claim, and no claim that any module, schedule or budget is
implementable at any stated cost beyond the counted-operation arithmetic
shown: area/fit/timing evidence belongs to #82/#83 (P5), the concrete clock
selection to #82/#83's timing evidence, and the fixed exp2/tanh
approximations to the #73/#74 lanes against the frozen M1 bands.
