# T1 block spec table (issue #387)

Status: **Ratification pending; ratified on operator approval of the PR
carrying DR-0016** (`spec/decision-records/0016-ratify-t1-spec.md`). This is
the block-level spec table that klayout-tools T1 items 5, 6, 8 and 10 grade
against (tracker #386; klayout-tools `docs/design-evidence-tiers.md`). It
selects no clock, changes no RTL or evidence record, and ran no tool. DR-0016
states exactly what is ratified: the table as written, with pending rows
staying pending. Until the operator approves that PR, every verdict graded
against this table is provisional.

It states claims the block *will* make at T1. It is not evidence that any of
them holds: no row below is a synthesis, layout, signoff, hardware-playback or
sound-fidelity result (`CLAUDE.md`). Existing records are cited, not restated.

## Table

Corner sets are the ones `asic/README.md` ("Named library, corner,
constraints") already uses: PDK `gf180mcuD`, library
`gf180mcu_fd_sc_mcu7t5v0`; STA corners `tt_025C_5v00`, `ss_125C_4v50`
(setup-critical), `ff_n40C_5v50` (hold-critical). Functional simulation has no
PVT corner (RTL, zero-delay).

| id | metric | bound | corner set | grading verb / record | row class | item-6 statement |
|---|---|---|---|---|---|---|
| F1 | Sample-exact RTL output vs the frozen fixed-point model over the declared vector set | 0 differing samples; `float_tolerance: null`; all 31 whole-voice cases (`full` profile) | none (zero-delay RTL simulation) | `tb/run_voice.py` / `tb/run_oneshot.py`, verified by `tools/verify_oneshot_evidence.py`; records `sim/evidence/oneshot-whole-voice-full-v1.json` and `oneshot-tail-chain-full-v1.json` (#79, #261); the `klt functional-verification` mapping is **pending** (not yet run through that verb) | deterministic | Not statistical: the claim is exact equality on a finite declared vector set, so there is no accuracy, offset, matching, yield, sigma or Cpk quantity to bound. |
| F2 | Mutation sensitivity of the F1 harness | 13 of 13 all-RTL mutations detected | none | same whole-voice `full` record (`mutations` section) | deterministic | Not statistical: a count of killed mutants out of a fixed list, not a distribution. It grades the harness behind F1, not the silicon. |
| C1 | Clip-render bound: cycles per sample `C` for a 176,400-sample (352,800 sample-slot) clip, `N_clip = 352,800 x C` | **pending, citing DR-0010 P1** (`spec/decision-records/0010-one-shot-rtl-microarchitecture.md`, P1 "decided (schedule-candidate)"). Candidates 25/50/100 MHz are declared there; **none is selected here**. Selection amends on #82/#83 timing evidence | `tt_025C_5v00`, `ss_125C_4v50`, `ff_n40C_5v50` once a clock exists | `klt sta` (setup/hold, 3 corners) plus the achieved sample schedule in the #82 record (`sample_schedule.achieved`, currently `null`); pending #82/#83 | deterministic (cycle count) given a selected clock; the clock bound itself is a timing-closure check | Not statistical: cycles per sample is a schedule property of the RTL, and clock closure is a per-corner slack sign check, not a spread. |
| T1 | Setup and hold slack of the whole voice at the (not yet selected) clock | WNS >= 0 and hold WNS >= 0 with complete constraints at every corner; **pending #82** (no whole-voice netlist exists; the whole-voice verdict is `unverifiable`, never `met`) | the three STA corners above | `klt sta` `timing_verdict`; `asic/evidence/oneshot-synth-feasibility-v1.json` | deterministic | Not statistical: per-corner static timing is a worst-case check, not a yield or sigma bound. Statistical OCV/derating, if later adopted, would need its own row. |
| A1 | Mapped standard-cell area of the whole voice | **pending #82**: no whole-voice netlist (4 of 10 engine modules have numbers; the top did not finish synthesis), so any number now would be invented. Memory macro vs distributed storage (DR-0010 P2) also open | synthesis corner `tt_025C_5v00` | `klt synthesize` area report in the #82 record | deterministic | Not statistical: area of a fixed mapped netlist is a single deterministic number; no matching or yield quantity. |
| PW1 | Power of the whole voice | **pending #83**: no power number, no bound. It will be stated at an explicit corner (`tt_025C_5v00` nominal for dynamic power, plus `ff_125C_5v50` for leakage-critical: fast process, 125 C, 5.50 V is where subthreshold leakage peaks), supply, the declared clock, and a stated switching-activity source (a named fixed-model vector from F1, not a default toggle rate) | `tt_025C_5v00`, `ff_125C_5v50` | pending #83 power report; no verb or record exists yet | deterministic given the named activity vector | Not statistical as defined: a single-vector estimate, not a spread. Vector-to-vector variation is not bounded here and, if wanted, is a separate row. |

## Item-6 account

Item 6 asks, for every row, whether it is statistical (accuracy, offset,
matching, yield, sigma or Cpk bound). The table above covers every row id: F1,
F2, C1, T1, A1 and PW1 each carry a per-row reason in its last column. **No row
is statistical**, so item 6 can be recorded `not_applicable` against the
ratified table once it is ratified, with those reasons as the basis. If
ratification adds a statistical row (for example derated timing or a power
distribution), that row needs its own bound and the `not_applicable` claim must
be revisited.

## What is not a row

The perceptual and population metrics in [`RUBRIC.md`](RUBRIC.md) (and the
families it composes) are measurement apparatus for judging the model and
corpus, not silicon spec rows. They carry no bound the block makes at T1 and are
deliberately absent here. Sound fidelity is likewise not claimed by any row.

## Pending items

| Pending | Gate | Rows |
|---|---|---|
| Clock selection | #82/#83 timing evidence (DR-0010 P1) | C1, T1, PW1 |
| Whole-voice mapped netlist | #82 | T1, A1 |
| Power activity and corner report | #83 | PW1 |
| `klt functional-verification` run over F1 | not yet run | F1 |
| Ratification of this table | operator approval of the DR-0016 PR (EE key and market key reviews as input) | all |
