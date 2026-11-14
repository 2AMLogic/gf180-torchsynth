# Whole-voice synthesis feasibility: bottleneck attribution and check plan (issue #82)

**Label: synthesis feasibility, plan only.** This document changes no RTL,
spec, decision record, clock, schedule, evidence record or capability-graph
node, and it ran no tool. Nothing here is layout, signoff, silicon, hardware
playback or sound fidelity. It is built from three inputs only: the committed
record `asic/evidence/oneshot-synth-feasibility-v1.json`, `asic/README.md`
and a reading of `tb/sv/*.sv`. Figures quoted from the record are pinned to it
by `tests/test_synth_feasibility_evidence.py` (see "Pinned figures"). Anything
not measured is marked **inferred, not measured**.

## Status of every #82 acceptance criterion

The 2026-10-17 hold resolution keeps #82 open with its criteria unchanged.
This plan does not change any status below.

| #82 criterion | Status | Reason |
|---|---|---|
| Hashes and commands recorded | met by #376 (per-target and tool hashes in the record) | not touched here |
| Reports: hierarchy, memory, multiplier, unconstrained paths, slack, warnings, **achieved sample schedule** | **unmet** for the whole voice | no whole-voice netlist; `sample_schedule.achieved` is `null`; 4 of 10 engine modules have numbers |
| Latch / loop / width / X / unsupported-construct audit | met by #376 (pre-mapping front-end audit) | the derived view is not simulated (below) |
| Constraint completeness; no "timing met" with unconstrained logic | met for the 4 completed modules; **unmet** for the whole voice | whole-voice verdict is `unverifiable`, never `met` |
| Called synthesis feasibility, not layout/signoff/silicon | met | unchanged |
| Generic `klt` friction separated | met by #376 (`asic/klt-friction.md`) | appended to, not fixed here |
| P5 (DR-0010 area/fit/PPA) | **unmet** | needs a mapped whole voice and #83 |

The 25 MHz (40 ns) point is a declared **evaluation point** taken from the
DR-0010 schedule register. DR-0010 selects no clock, and neither does this
plan.

## Bottleneck table

"File:lines" refer to `tb/sv` as committed. (`README.md` and the record quote
`sine_vco_engine.sv:168-169`; those are line numbers in the derived synthesis
view, where the package import is expanded. In `tb/sv` the same `/` and `%`
are at lines 175-176.) "Evidence" cites a record field, or says inferred.
"Owner" is the DR-0010 row (`spec/decision-records/0010-one-shot-rtl-microarchitecture.md`)
that would own a remedy; P1 (budget and clock) is included where the remedy is
a clock rather than a structure.

| # | Construct | File:lines | Evidence | Owner |
|---|---|---|---|---|
| B1 | 64-stage unrolled restoring divider (once per clip) | `normalization_replay_engine.sv:172-194` | measured: `normalization_replay_engine` TT setup -170.29 ns, ABC critical path 208,476 ps, 290 flops, 11,050 cells | P3 (schedule), P5 |
| B2 | 49-stage unrolled restoring divider, one per upsample column (x5) | `upsample_engine.sv:142-170`, instanced `one_shot_voice_top.sv:400` | measured on one instance with B3: TT -69.36 ns; the divider's share of that path is **inferred, not measured** | P3, P5 |
| B3 | 5 x (1,764 x 24-bit) control-column memories lowered to flip-flops plus a read mux | `upsample_engine.sv:97` | measured: one column is 42,679 flops / 165,732 cells / 5,510,646 um^2; five columns 27.55 mm^2 is arithmetic on that instance, not a measurement | P2 |
| B4 | Two 96-bit `/` and `%` with a variable divisor (K site) | `sine_vco_engine.sv:169-183` (operators 175-176) | front-end audit: 2 `$div`/`$mod` cells; `sine_vco_engine` did not finish at 300 s, so **no timing number exists** (cost inferred) | P3, P1 |
| B5 | 107-stage (ADSR) and 92-stage (LFO) unrolled restoring dividers, three per ADSR (x6 instances) and per LFO (x2 instances) | `adsr_engine.sv:126-159` (loop 145); `lfo_vca_engine.sv:156-189` (loop 175); instanced `one_shot_voice_top.sv:261,310` | both engines timed out at 300 s in `abc`; **inferred, not measured**: same style as B1 but deeper | P3, P5 |
| B6 | 64-stage restoring divider in the square/saw VCO | `square_saw_vco_engine.sv:143-166` | timed out at 300 s in `abc`; **inferred, not measured** | P3, P5 |
| B7 | 76 of 93 `$mul` are 96x96->96 | sites include `sine_vco_engine.sv:189`, `253-254`; per-site list not in the record | measured count in the front-end audit; per-site delay **inferred, not measured**; mapped as `alumacc`/`maccmap` adder trees with no Booth | P3, P5 |
| B8 | Five copies of the 4097 x 24 sine ROM mapped as logic | `quarter_wave_lut.sv:24`; copies in `square_saw_vco_engine.sv:221-222`, `sine_vco_engine.sv:119`, `lfo_vca_engine.sv:222` | measured: `quarter_wave_lut` 223,693 um^2, TT -11.67 ns on a purely combinational in-to-out path with a 24 ns budget (the 40 ns period less the 8 ns input and 8 ns output delays); `memory_mapping: lowered_to_logic` | P2 |
| B9 | Mod-matrix 5 x 4 history registers and 4-lane blend | `mod_matrix_engine.sv:85-110` | did not finish even at 2,700 s; cause **inferred, not measured** | P3 |
| B10 | Single-cycle timing of every path (no multicycle exception in `klt sta`) | `one_shot_voice_top.sv` 4-cycle group | by construction pessimistic (friction F5); size of the pessimism **not measured** | P1, P3 |

Reading of the table: the measured failures (B1, B3, B8) are all structural,
not a tool glitch, and each is sized in nanoseconds or square millimetres. The
unmeasured rows (B4-B7, B9) are the ones the 300 s bound hid. The plan below
treats them as hypotheses to test cheaply, not as findings.

## Sample-identity gap

The #79 evidence is sample-exact on `tb/sv`. The synthesis view is a derived
source (`tools/synth_view.py`) and is **not** covered by that evidence, so a
mapped netlist could not be tied to the sample-exact result today. The four
rewrite classes, and whether each could change a sample:

| Rewrite class | Could it change a sample? | Argument | Cheapest falsifiable check |
|---|---|---|---|
| 1. Constants package -> `` `define `` macros | Not by value; **by width or sign it could** | Same integer values by construction, but a macro is untyped text where a `localparam` is not. `synth_view.py` also leaves constants outside the 32-bit range undefined (`C2_UNITS_PER_TURN`), so a future use fails loudly rather than silently truncating | Diff the expanded view against the package: assert every macro value equals the `localparam` value (static, no simulator) |
| 2. `$value$plusargs`/`$readmemh` ROM load -> fixed `$readmemh` | Not if the file is the same bytes | The record hashes the payload (`rom_payload_sha256`); identity needs the simulation lane to have loaded the same hash | Assert the payload hash equals the one the #79 lane writes (`write_lut_memh` in `tb/run_voice.py:829-830`): static hash comparison |
| 3. `+max_j` plusarg -> constant 0 | No for the committed top | `max_j == 0` means the full walk (`upsample_engine.sv:178-179`); the cap term `max_j != 0 && ...` at line 256 is then constant false. The caveat: this is only identity with a #79 run that did not pass `+max_j` | Grep the #79 lane's invocations for `max_j`; none should set it |
| 4. Division loop start `den_msb + 60` -> constant start with an `if (i <= den_msb + 60)` guard | No, provided the constant start is at least `den_msb_max + 60` | Iterations above `den_msb + 60` are skipped by the guard, so the executed iteration set is identical. The constants are 91 (LFO, 32-bit denominator, `den_msb <= 31`) and 106 (ADSR, 47-bit denominator, `den_msb <= 46`) in `tools/synth_view.py:144,151`, matching the loop bound arithmetic | An exhaustive or randomized comparison of the two loop forms over the denominator range in a software model, plus the replace-exactly-once guard already tested |

Not one of the four classes, and **unchecked**: Yosys' native `read_verilog`
(without `-sv`) and `iverilog -g2012` can differ on signedness, `integer`
loop bounds and constant-expression widths. The rewrites above do not rule
that out, so the only complete tie is dynamic: run the #79 whole-voice lane
against the derived view. That lane hard-codes its sources
(`tb/run_voice.py:76-77`, `tb/sv`), so pointing it at `asic/build/src` needs a
source-directory override that does not exist. Adding one is a harness change
and is out of scope here.

## Cheaper falsifiable checks, ordered by cost

| # | Claim it would falsify | Command | Expected pass / fail signal | Resource |
|---|---|---|---|---|
| C0 | "A longer Yosys bound would change the whole-voice verdict" | none (reasoning from the record, below) | falsified if a completed top-level component met 25 MHz; none did | the committed record |
| C1 | Rewrite classes 1-2 are value-preserving | Python comparing the view's macro values to the package, and the payload hash to the lane's | pass: zero differences; fail: any differing value or hash | local CPU, seconds |
| C2 | Rewrite class 4 is identity | software model of both loop forms over `den_msb` in 0..46 | pass: equal quotients and remainders for all tested denominators | local CPU, seconds |
| C3 | The B-row ordering (which construct dominates the path) | record per-engine ABC critical paths for the engines that do complete, and compare to depth proportional to stage count | pass: B1 (64 stages) and B2 (49 stages) paths scale with stage count; fail: they do not, and the divider is not the dominant term | the committed record; no run |
| C4 | The unmeasured rows B4-B7 cost what B1 suggests | one Yosys `synth -run :fine` plus `abc -D` per engine with a *scoped* script, or a Yosys `ltp`/`stat` of the unmapped design (single engine, one process) | pass: a path-depth number appears inside minutes for the engines that timed out; fail: still no result | one `yosys` process, local, minutes; **not run here**, needs operator sign-off because it is a new tool run on the shared worker |
| C5 | The derived view computes the same samples | the #79 lane against the view, after a source-directory override exists | pass: sample-exact on every case; fail: first mismatch localized by the lane | simulator CPU; **not run**; harness change plus a campaign, so needs its own issue |
| C6 | A restructured divider (iterative or serialized) would close 25 MHz | a DR-governed experiment on one engine (`normalization_replay_engine`, once per clip) | pass: TT setup slack >= 0 with `complete` constraints; fail: still negative | `klt synthesize` plus `klt sta` on one engine; needs a decision record first |

### C0: the longer-bound claim, reasoned from the record

* The 4 completed modules took 1.8 s to 1,802 s (`yosys_timeout_s` 300 or
  2,700). Completion changed what is *known*, not what is *true*: three of the
  four still fail setup timing at TT, and the fourth fails at the slow corner.
* `normalization_replay_engine` is a component of the top
  (`one_shot_voice_top.sv:555`). Its engine-level STA constrains ports with an 8 ns
  input and output delay, but the failing path is register to register inside
  the divider, so the same path exists in the top. A mapped top cannot be
  faster than that path unless flattening restructures the divider by roughly
  a factor of five, which is **inferred, not measured** and not what ABC
  usually does for a restoring-divider chain.
* Therefore, for the whole voice: the timing verdict at 25 MHz would remain
  `not_met` even if the top mapped. A longer bound could produce numbers for
  the unmeasured rows, which is C4's job at lower cost, but it cannot turn the
  completed-module verdicts around.
* `mod_matrix_engine` not finishing at 2,700 s shows time is not a monotone
  fix: more minutes did not produce a result for it.

## Decision records that must change before any structural edit

No such change is made here. Before any of these edits, a decision record (or
an amendment to DR-0010) has to exist and be accepted:

* **P3 (time-multiplexing schedule)**: any serialized or iterative divider
  (B1, B2, B4-B6) or any change to the 4-cycle group in
  `one_shot_voice_top.sv`. The DR-0010 serialized candidate (145 counted
  cycles per sample) is declared as a candidate and is not implemented by this
  RTL.
* **P2 (memory architecture)**: any change to the control-column memories
  (B3) or the ROM copies (B8), including a macro-versus-distributed choice or a
  shared ROM.
* **P1 (budget and clock)**: any selection of a clock other than the 25 MHz
  evaluation point.
* **P5 (area/fit/PPA)** stays *pending measurement*. It closes only on a
  committed mapped whole-voice record plus the #83 flow, not on an estimate.
* Arithmetic, normalization or parameter-order changes need the existing
  DR-0008 and DR-0003 change paths (`CLAUDE.md`: `spec/` owns behavior).

## Out of scope for this plan

No new `klt synthesize` or `klt sta` run, no larger timeout, no lower or new
clock, no simulation campaign, Monte Carlo, spending or AWS box use, no
change to the `synthesized` capability node or to the evidence record, and no
closure or split of #82. Floorplan, place and route stay with #83.

## Pinned figures

Each line below is `record.path = value`. The test resolves the dotted path in
the committed record and refuses any drift. Add a line here when the prose
above quotes a new record number.

```pinned-figures
targets.normalization_replay_engine.sta.corners.tt_025C_5v00.worst_slack_ns = -170.29
targets.upsample_engine.sta.corners.tt_025C_5v00.worst_slack_ns = -69.36
targets.quarter_wave_lut.sta.corners.tt_025C_5v00.worst_slack_ns = -11.67
targets.noise_stream_dut.sta.corners.tt_025C_5v00.worst_slack_ns = 12.60
targets.noise_stream_dut.sta.corners.ss_125C_4v50.worst_slack_ns = -5.38
targets.normalization_replay_engine.synthesize.hierarchy.0.cells = 11050
targets.upsample_engine.synthesize.hierarchy.0.cells = 165732
targets.upsample_engine.synthesize.hierarchy.0.area_um2 = 5510646
targets.quarter_wave_lut.synthesize.hierarchy.0.area_um2 = 223693
targets.mod_matrix_engine.synthesize.yosys_timeout_s = 2700
targets.sine_vco_engine.synthesize.yosys_timeout_s = 300
overall.whole_voice_synthesis = timeout
overall.whole_voice_timing_verdict = unverifiable
sample_schedule.achieved = None
sample_schedule.cycles_per_clip = 1418256
sample_schedule.dr0010_serialized_candidate.counted_cycles_per_sample = 145
audit.yosys_frontend_whole_voice.multiplier_operator_cells = 93
audit.yosys_frontend_whole_voice.multiplier_widths_a_x_b_to_y.96x96->96 = 76
audit.yosys_frontend_whole_voice.div_mod_operator_cells = 2
audit.yosys_frontend_whole_voice.ff_bits_pre_mapping = 218002
derived_arithmetic_not_measured.five_upsample_column_instances.flip_flop_cells = 213395
```

Not pinned (they are plan-side readings, not record numbers): line ranges
into `tb/sv`, stage counts (49, 64, 92, 107), instance counts, and the
cycle-budget remark in B8. The test pins the files they cite by hash through
the record's `rtl_sha256` check, so an RTL edit already forces a refresh of
the record and this plan.
