# ASIC flow: gf180mcu synthesis feasibility (issue #82)

**Label: synthesis feasibility. Nothing here is layout, signoff, silicon,
hardware playback or sound fidelity.** Use `klt` for ASIC flow work (repository
rule); `tb/run_tb.py` and `tb/run_voice.py` remain PDK-free conveniences.

## Result in one paragraph

The whole-voice RTL that issue #79 proved bit-exact in simulation
(`tb/sv/one_shot_voice_top.sv`) **did not synthesize to gf180mcu cells**
through `klt synthesize` (Yosys 0.67 + ABC): the whole top was still in Yosys'
`opt` of the per-instance modules when `klt` killed Yosys at its fixed 300 s
bound. Of the 10 engine modules, 4 completed (`noise_stream_dut` and
`quarter_wave_lut` inside 300 s; `upsample_engine` in 1,802 s and
`normalization_replay_engine` in 1,052 s only after the harness raised the
Yosys bound to 2,700 s) and 6 did not (5 at 300 s; `mod_matrix_engine` also not
at 2,700 s). **All four completed modules miss timing at the 25 MHz evaluation
clock** (worst: `normalization_replay_engine`, -170 ns TT setup, i.e. a
~4.8 MHz clock; `upsample_engine`, -69 ns, ~9.1 MHz). So the honest answer to
"does this RTL, as written, synthesize and close timing on gf180mcu" is
**no: not for the whole voice, and no module that did complete closes at 25 MHz
(only `noise_stream_dut` passes at TT; it fails at the slow corner)**. The
first-order causes are structural (combinational restoring dividers, 96-bit
`/` `%` operators, and 5 x 1,764 x 24 bits of control-column storage that
becomes ~213 k flip-flops and a read mux) and are architecture facts for the
DR-0010 schedule work, not tool glitches. The record is
`asic/evidence/oneshot-synth-feasibility-v1.json`.

The capability graph is deliberately unchanged: the `synthesized` node
(`gf180-synthesis-qualification-v1`, "gf180mcu synthesis with committed
mapped-flow evidence") stays planned / BLOCKED / NOT RUN, because this record
establishes a *failure to synthesize the whole voice*, not mapped-flow
evidence for it, and no runnable qualification check is registered for it.

## What was run (and what was not)

| Step | Status |
|---|---|
| Derive the Yosys-readable synthesis view of the #79 RTL (`tools/synth_view.py`) | run; 4 rewrite classes, all hashed |
| `klt synthesize` per engine + whole voice, `gf180mcu_fd_sc_mcu7t5v0` / `tt_025C_5v00`, `gf180mcuD` (open_pdks `f6eeac7d`) | run on 11 targets: **4 completed, 7 did not** (the whole voice and 6 engines; see record). Two completed only with the Yosys bound raised to 2,700 s; `audio_mix_engine`, `adsr_engine`, `lfo_vca_engine`, `sine_vco_engine`, `square_saw_vco_engine` and the top were **not** retried above 300 s |
| `klt sta` (OpenSTA, `verilog` mode) at 3 corners with explicit clock/I-O delays | run for the 4 completed targets; **not run** for the 7 with no netlist (recorded `unverifiable`, never a pass) |
| Yosys front-end audit of the whole voice (`proc`, `check` hier + flat, coarse inventory) | run, completes in ~2 min |
| `verilator --lint-only -Wall` of the original RTL | run |
| `klt equiv` / `--verify-equivalence` | **not run**: sequential designs are out of `klt equiv`'s combinational scope |
| `klt synthesize --restructure-timing`, `--check --rerun` | **not run** |
| Gate-level simulation of the mapped netlist | **not run**; synthesis view is not itself simulated (see Limitations) |
| Floorplan / place / route / CTS / parasitics | **not run** (issue #83, not this one) |

## Named library, corner, constraints

* PDK `gf180mcuD`, open_pdks `f6eeac7dad085ffcc829ccfd721f7b4ce39edcf7` (ciel
  store), library `gf180mcu_fd_sc_mcu7t5v0` (7-track, 5 V-class cells),
  synthesis corner `tt_025C_5v00`; liberty SHA-256 in the record. STA corners:
  `tt_025C_5v00`, `ss_125C_4v50` (setup-critical), `ff_n40C_5v50`
  (hold-critical).
* Clock `clk`, 40.0 ns (25 MHz, the DR-0010 schedule register's bound clock;
  DR-0010 selects no clock, so this is a declared evaluation point, not a
  selection). Input and output delays 8.0 ns (20 %) on every non-clock port.
* Reset: `rst` is an asynchronous active-high input constrained like any other
  input; recovery/removal are therefore timed; no reset synchronizer exists or
  is assumed.
* Generated clocks / enables: none exist. Phase enables are host-sequenced
  synchronous inputs; with no multicycle exception expressible in `klt sta`
  every path is timed single-cycle (pessimistic by construction; friction F5).
* Memory: no SRAM macro, no blackbox; ROMs and column memories are left to Yosys
  memory inference (flip-flops / logic). The record's `memory_mapping` and
  `ff_bits_pre_mapping` say what happened.
* Wire load: none (netlist-only STA, no placement or parasitics); every slack
  here is optimistic with respect to any routed result.

## Findings

Numbers are from the committed record; re-derive them with the commands below.

* **Completed modules** (TT synthesis, then STA at the 25 MHz / 40 ns clock;
  slack in ns, `klt sta` verdict from `timing_verdict`):

  | Module | Cells | Area (um^2) | Flops | ABC crit. path (ps) | Setup WNS TT / SS / FF | Min hold WNS | Verdict |
  |---|---|---|---|---|---|---|---|
  | `noise_stream_dut` | 1,489 | 31,565 | 168 | 19,789 | +12.60 / -5.38 / +20.40 | +0.48 | not_met (SS) |
  | `quarter_wave_lut` (one 4097 x 24 ROM + interpolator, purely combinational: in-to-out budget 24 ns) | 13,245 | 223,693 | 0 | 36,857 | -11.67 / -43.55 / +2.19 | +16.20 | not_met |
  | `normalization_replay_engine` (64-stage unrolled divider) | 11,050 | 258,393 | 290 | 208,476 | -170.29 / -358.32 / -88.99 | +0.48 | not_met |
  | `upsample_engine` (one of five columns; 1,764 x 24 memory) | 165,732 | 5,510,646 | 42,679 | 107,886 | -69.36 / -167.06 / -27.06 | +0.47 | not_met |

  The ROM is **not** mapped to a macro: Yosys lowered the 98,328-bit array to
  logic (`memory_mapping: lowered_to_logic`). The voice instantiates five such
  ROM copies (two `quarter_wave_lut` in `square_saw_vco_engine`, one inline in
  `sine_vco_engine`, one in each of the two `lfo_vca_engine`): arithmetic only,
  ~1.12 mm^2 as logic. One `upsample_engine` column memory became 42,336
  `dffq_1` plus 42,382 `mux2_1` (2.72 mm^2 of flip-flop area alone); five columns
  are ~27.6 mm^2 by multiplication of the measured instance (not a whole-design
  measurement, `derived_arithmetic_not_measured` in the record). Note klt's
  `memory_mapping` reports `none_detected` for that array because Yosys'
  front end had already replaced it with registers (friction F10).
  Multipliers in the completed modules were mapped by Yosys' default
  `alumacc` -> `$macc` -> `maccmap` adder trees (no Booth), per the retained
  Yosys log counts in the record.
* **Did not complete**: `adsr_engine`, `lfo_vca_engine`, `mod_matrix_engine`,
  `sine_vco_engine`, `square_saw_vco_engine`, `audio_mix_engine` and the whole
  top. All but the top were inside Yosys' generic `abc` step (pass 4.22;
  5.22 for `square_saw_vco_engine`; before the liberty mapping) when stopped
  (`mod_matrix_engine` after 2,700 s); the top
  had not left `opt` of the derived per-instance modules. No area, count or
  timing number exists for any of them, and none is estimated here.
* **Why (front-end audit, whole voice, pre-mapping)**: 93 `$mul` operators, 76
  of them 96 x 96 -> 96; two **96-bit `/` and `%` operators with a variable
  divisor** in `sine_vco_engine` (lines 168-169); 218,002 flip-flop bits of
  which 211,680 are the five `upsample_engine` control-column memories
  (5 x 1,764 x 24), replaced by registers by the front end; and
  restoring-division functions unrolled to 60-107 stages inside `adsr_engine`
  x6 (three dividers each), `lfo_vca_engine` x2, `normalization_replay_engine`
  and `square_saw_vco_engine`. The two completed divider modules show what that costs: ABC's own
  critical-path estimate for `normalization_replay_engine` is 208 ns and
  `klt sta` puts its TT fmax at 4.76 MHz. The 4-cycle audio group in
  `one_shot_voice_top.sv` clocks each engine once per cycle and so asks that
  path to fit one clock period. The schedule the RTL actually has is
  4 cycles/sample x 2 passes (1,418,256 cycles per clip, >= 0.355 MHz for 1x
  real time) -- but it has no achieved clock, so **the achieved sample
  schedule is "none": unmeasured, not met**.
* **Audits** (all in the record's `audit` block): no inferred latches
  (`proc`), no combinational loops, multiple drivers or undriven nets
  (`check`, hierarchical and flattened, 0 problems); Verilator `-Wall` reports
  51 `WIDTHEXPAND` and 15 `WIDTHTRUNC` (the same classes the repo's lint ratchet
  `tb/rtl-lint-baseline.json` already waives by count; none was fixed or newly
  judged here) and no latch/loop/`COMBDLY` class; the RTL has
  no `x` literals or `casex`; its simulation-only constructs (plusarg + 
  `$readmemh` ROM loads in three modules, the `+max_j` cap in
  `upsample_engine`, `$display`/`$finish`) cannot be synthesized as written
  and are handled by the synthesis view (below). One `SYNCASYNCNET` warning
  in the top is recorded, not resolved.
* **Constraint completeness**: `klt sta`'s `constraint_completeness` was
  `complete` (no unconstrained endpoints, no unclocked registers) for the four
  completed modules at all three corners. `tools/synthesize_oneshot.py`
  `timing_verdict` returns `met` only if every corner is `constrained` and
  `complete` with non-negative setup and hold slack; the whole voice has no
  netlist, so its verdict is `unverifiable`, never `met`.
  `tests/test_synth_feasibility_evidence.py` pins this, including mutation
  checks that a forged `met` or an unconstrained report is refused.

## The synthesis view (a derived source -- read this)

`klt synthesize` runs Yosys' native `read_verilog` without `-sv`, so the
packaged SystemVerilog RTL cannot be read as-is, and four constructs are
simulation-only or unelaboratable (details in `tools/synth_view.py`):

1. the constants package -> `` `define `` macros (same integer values);
2. `$value$plusargs` + `$readmemh` ROM loads -> a fixed `$readmemh` of the
   same hash-linked `.memh` payload the simulation loaded;
3. the `upsample_engine` `+max_j` cap -> the constant `0` (the full walk the
   committed top always uses);
4. the data-dependent division loop start `den_msb + 60` ->
   a constant start with an `if (i <= den_msb + 60)` guard, arithmetically
   identical but a **structural source edit**.

`tb/sv/*.sv` is untouched. The record hashes the original sources, the derived
view and the ROM payloads. **The derived view is not itself simulated**: the
bit-exact evidence of #79 covers `tb/sv`, not this view, so this record does
not claim the mapped design computes the same samples. Closing that gap
(re-running the #79 lane on the view, or a gate-level run) is follow-up work.

## Reproduce

```sh
python3 tools/synthesize_oneshot.py run                 # klt synthesize + klt sta, per target
python3 tools/synthesize_oneshot.py run --targets upsample_engine --timeouts upsample_engine=2700 --force
python3 tools/synthesize_oneshot.py record              # rewrites the evidence record
python3 -m unittest tests.test_synth_feasibility_evidence -v
```

`run` writes the derived view and per-target results under the ignored
`asic/build/`, plus the commit-safe generated Yosys scripts under
`asic/evidence/scripts/`. A `--timeout` above 300 patches `klt`'s fixed Yosys
timeout (friction F2) and the record names which targets used it. Install
nothing on the host: the PDK, `klt`, `yosys`, `openroad` and `verilator` are
the worker's pinned tools.

## Files

* `asic/evidence/oneshot-synth-feasibility-v1.json` -- the record (hashes,
  tool versions, constraints, exact commands, per-target results, audits).
* `asic/evidence/scripts/` -- `klt`-generated Yosys scripts (commit-safe).
* `asic/klt-friction.md` -- generic `klt` friction, separated for upstream
  filing in `2AMLogic/klayout-tools` (F1-F12).
* `tools/synth_view.py`, `tools/synthesize_oneshot.py`,
  `tests/test_synth_feasibility_evidence.py`.

## Open follow-ups this record does not close

* A synthesizable (iterative or serialized) divider/arithmetic structure is a
  DR-0010 schedule decision, not something this issue may change.
* A larger or differently-scripted ABC budget (friction F2, F4) might yield
  mapped numbers for the remaining divider-bearing engines and the top (the
  two completed divider modules took 17-30 minutes each); none exist today.
* Re-running the #79 simulation lane on the synthesis view, or a gate-level
  simulation, to tie the mapped logic to the sample-exact evidence.
* The ranked bottleneck table, the sample-identity gap analysis and the
  ordered cheaper-check list for these follow-ups are in
  [`whole-voice-feasibility-plan.md`](whole-voice-feasibility-plan.md)
  (plan only; it changes no RTL, record or clock).
* Sine-VCO `/` `%` and the control-column memories (flip-flops) are the first
  things a macro-vs-distributed memory decision (DR-0010 P2) must address.
