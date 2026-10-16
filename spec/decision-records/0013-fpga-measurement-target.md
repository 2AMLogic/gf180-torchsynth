# DR-0013: FPGA feasibility-measurement target, toolchain and wrapper boundary

- Status: Proposed
- Date: 2026-10-14
- Decision owners: 2AM Logic
- Issue: #80 (Epic #2, parent plan #58). Consumed by #81 (board control/audio
  path) and #374 (post-synthesis/post-route sentinel simulation, clean-checkout
  rebuild).
- Scope: which device, board, toolchain and wrapper boundary the Phase-3 FPGA
  measurement uses. This record changes **no** target semantics, arithmetic
  profile, noise policy, normalization, parameter order or clip timing
  (DR-0001, DR-0002, DR-0003, DR-0008 and DR-0010 are unchanged). It makes no
  gf180mcu, hardware-playback or sound-fidelity claim.

Ratification follows the project convention (DR-0006): reviewed merge
ratifies this record. Until then it is Proposed.

## Context

`docs/REUSE-AUDIT.md` (rows P09/Y06) and DR-0005 require the board to be
selected, with traced pin provenance, before any wrapper is written. No
record names one, and `tb/sv/one_shot_voice_top.sv` (the verified whole-voice
top, #79) is a verification top with thousands of port bits that cannot be
pinned out. #80 asks for resource, timing and cycle evidence on one supported
device without optimizing and without weakening the profile.

## Decisions

### D1. Device, board and toolchain

| Item | Choice |
| --- | --- |
| Device | Lattice ECP5 `LFE5U-85F`, package `CABGA381`, speed grade 6 (nextpnr `--85k --package CABGA381 --speed 6`) |
| Board | **Board not required for this stage.** ULX3S (85F, CABGA381) is the reference board for the device choice, because both sibling repos already target ECP5 boards (REUSE-AUDIT P09/Y06). No board is claimed to be on hand and none is programmed. |
| Flow | Yosys `synth_ecp5`, nextpnr-ecp5, Project Trellis `ecppack` (open flow, no vendor licence) |
| Front end | `read_slang` (yosys-slang, built into the YoWASP Yosys build). Yosys' native `read_verilog -sv` rejects the package (`localparam int`) and `sv2v` is not available on dispatch workers. |
| Pinned versions | `yowasp-yosys==0.70.0.0.post1259` (Yosys 0.70), `yowasp-nextpnr-ecp5==0.11.1.0.post826`, plus their pinned dependencies in `fpga/requirements.txt` |
| Provisioning | A throwaway venv at `fpga/build/venv` created by `fpga/build.sh` from `fpga/requirements.txt` (git-ignored). Nothing is installed on the host. The host's `yosys` 0.67 is not used for results. |
| Clock | One 25 MHz constraint (`--freq 25`, the lowest DR-0010 candidate and the ULX3S oscillator rate), plus an achieved-Fmax figure from nextpnr. 50 and 100 MHz are reported as achieved/not achieved, not re-run. |
| Pins | **None assigned.** I/O is left to nextpnr' automatic placement; no `.lpf` file is committed. Reusing sibling constraints is rejected by REUSE-AUDIT (P09/Y06). Pin assignment, with traced board-schematic provenance, is #81's first deliverable. |

### D2. Wrapper boundary (#80 vs #81)

`fpga/rtl/fpga_oneshot_top.sv` is a **measurement shim**, not a product
control path:

- it instantiates the unmodified `one_shot_voice_top` and drives its exact
  phase contract (control walk, two audio passes, noise priming, `in_done`,
  `mix_done`) from an internal sequencer;
- the static words (2,956 bits) arrive over a serial `cfg_in` chain and the
  per-tick/per-sample host streams (declared shadow words, noise bytes) come
  from a free-running pseudo-random register, because constant inputs would
  let synthesis fold away the arithmetic being measured;
- audio output is folded into a CRC and one parity pin so the sample path is
  not pruned;
- the declared host-replayed shadow tables (DR-0010 amendment 2026-09-22),
  the 4 B/sample noise stream and the host protocol receiver are **not**
  included, so their storage and transport cost is outside every number this
  stage reports;
- the stimulus is **not** the golden vector. The shim carries no numeric
  correctness claim; the exact-sentinel simulation of the synthesized netlist
  is #374.

The physical control path, audio capture and board pins remain #81. If #81
builds a different top, it must instantiate the same `one_shot_voice_top`
unmodified and may not edit the staged sources.

### D3. Source staging (synthesis-only transforms)

`tb/sv` is read-only. `fpga/scripts/prepare_sources.py` copies the reachable
sources into `fpga/build/src` and applies exactly five asserted edits:

1. `sine_vco_engine.sv`, `quarter_wave_lut.sv`: the `+lut=` plusarg `initial`
   block becomes `$readmemh("quarter_wave_audio.memh", rom)`.
2. `lfo_vca_engine.sv`: the `+ctl_lut=`/`+lut=` block becomes
   `$readmemh("quarter_wave_control.memh", rom)`.
3. `upsample_engine.sv`: the `+max_j=` block becomes `max_j = 0`, the
   plusarg's own default (full walk).
4. `adsr_engine.sv`: the restoring-divide loop header
   `for (i = den_msb + 60; i >= 0; ...)` becomes a constant-bound loop
   `for (i = 106; i >= 0; ...) if (i <= den_msb + 60)`. By construction it
   is intended to execute the same iterations in the same order (the guard
   reproduces the old start index; 106 is the maximum of `den_msb + 60`), so
   the arithmetic should be unchanged. What is actually verified is a
   bounded simulation check, not a formal proof:
   `fpga/scripts/check_adsr_div_equiv.py` extracts `div_half_even` from
   `tb/sv/adsr_engine.sv`, applies the same replacement, and compares the
   original and rewritten functions under Icarus on directed edges plus
   fixed-seed random vectors (100,290 vectors, 0 mismatches at the default
   size; a deliberately wrong bound of 105 is caught). Equivalence of the
   synthesized netlist is not established; that is #374. It is needed because the data-dependent start index could not
   be unrolled by the front end (it exhausted the 4 GiB WebAssembly memory
   before elaboration finished).

Both ROM images are regenerated by `fpga/scripts/gen_luts.py` with the same
accepted-register gate and table construction as `tb/run_voice.py`. Any
change to these rules is a change to this record.

### D4. What the results may claim

Resource, timing and cycle numbers are for **this device, this front end,
this tool version and this stimulus-driven shim**. A number is reported only if
the command that produced it ran. If the design does not fit or does not meet
timing, the bottleneck hierarchy is published and the profile is not
weakened. No pass/fail resource ceiling is imposed ("resource-generous" means
report utilisation without optimizing).

## Consequences

- #81 inherits the device and must still pick and trace a board and pins.
- #374 simulates the netlists emitted by `fpga/build.sh` against the golden
  sentinel and rebuilds from a clean checkout; this record does not claim
  either.
- A different device or toolchain pin requires amending this record.
