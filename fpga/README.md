# FPGA target (issues #80 and #374, DR-0017)

A resource-generous **feasibility measurement** of the passing one-shot RTL
(`tb/sv/one_shot_voice_top.sv`, #79) on one Lattice ECP5 device with the open
flow. It is evidence about that device, front end, tool version and stimulus
only. It is **not** evidence of gf180mcu feasibility, hardware playback or
sound fidelity, and it does not change the arithmetic profile.

Decision record: [`spec/decision-records/0017-fpga-measurement-target.md`](../spec/decision-records/0017-fpga-measurement-target.md)
(Proposed until a reviewed merge ratifies it).

> **Numbering note.** This record was DR-0013 until it was renumbered to
> DR-0017 on rebase (`main` landed a different 0013 first). #80's evidence
> deliberately kept the `DR-0013` spelling so its recorded hashes stayed
> valid; #374 completed the rebind — `build.sh`, `requirements.txt` and
> `rtl/fpga_oneshot_top.sv` now say `DR-0017`, and the clean-checkout
> rebuild regenerated `reports/fpga-build-report.json`'s hash bindings in
> the same change (see "Clean-checkout rebuild" below). The synthesized
> netlist is byte-identical across the rebind; only the three edited files'
> hashes moved.

## Result (summary; details in `reports/fpga-build-report.json`)

**Outcome: the unmodified-arithmetic core does not fit an LFE5U-85F.
Placement/routing was not completed and no timing figure exists.** This is a
published negative result, not a pass.

| Resource (yosys 0.70 `synth_ecp5`, flat, `abc -lut 4:7`) | Used | LFE5U-85F | % |
| --- | --- | --- | --- |
| Total LUT4 (logic 196,764 + carry 108,458 + RAM 15,984 + RAMW 7,992 = 329,198; packed `TRELLIS_COMB` 335,198) | 329,198 | 83,640 | 393% (COMB 400%) |
| `TRELLIS_FF` | 4,754 | 83,640 | 5% |
| `MULT18X18D` (DSP) | 324 | 156 | 207% |
| `DP16KD` (block RAM) | 0 | 208 | 0% |
| Distributed RAM cells (`TRELLIS_DPR16X4` -> `TRELLIS_RAMW`, after packing) | 3,996 | 10,455 | 38% |
| Distributed RAM read LUT4s ("RAM LUTs", before packing; write "RAMW LUTs" 7,992/20,910 = 38%) | 15,984 | 10,455 | 152% |

- Distributed-RAM units (corrected per the EE review of #375): both rows are
  nextpnr's own, in the unit it printed (`reports/pnr_tail.log`). The 38% row
  counts **packed cells** — 3,996 `TRELLIS_RAMW` against the device's 10,455
  (= 83,640/8) — so numerator and denominator are both cell counts. The same
  memory in LUT4 units *also* overflows the device: 15,984 read ("RAM") LUT4s
  are 152% of the 10,455 LUT4s usable as RAM (write "RAMW" LUT4s
  7,992/20,910 = 38%). Both LUT4 counts are already inside the 329,198 LUT4
  total above.

- nextpnr-ecp5 0.11.1 packed the design, printed the utilisation above and
  then aborted with an uncaught WebAssembly exception (exit 1) with no
  "ERROR" text; the committed `reports/pnr_stderr_excerpt.txt` has the
  evidence. **No placement, no routing, no Fmax or slack** is claimed at
  25, 50 or 100 MHz. The 25 MHz constraint was set but never evaluated.
- Bottleneck hierarchy (standalone per-engine synthesis, instances from
  `one_shot_voice_top`; indicative, not additive to the flat total because
  there is no cross-boundary constant propagation): the sine ROM
  (`sine_vco_engine`, ~93 k LUT4 standalone) and the two LFO engines
  (~61 k LUT4 for 2 instances) dominate, then `square_saw_vco_engine`
  (~34 k), `upsample_engine` x5 (~40 k, plus all the distributed RAM),
  `normalization_replay_engine` (~22 k) and `adsr_engine` x6 (~13 k LUT4,
  108 `MULT18X18D`). Multipliers: the flat design needs 324 against 156
  available (ADSR 108, LFO 88, matrix 80, mixer 45, VCOs 33, upsample 20,
  replay 4; the standalone sum is 378, flat is 324).
- What the synthesized default observes (`OBSERVE_ALL=0`): the signature pins
  carry `audio_out` (CRC-folded), `error_code`, `noise_error`, `done` and
  `error` only. The `obs_bit` enumeration (`rtl/fpga_oneshot_top.sv:281-287`)
  folds into `ops_all`, which is held at zero when `OBSERVE_ALL==0`, so that
  whole cone is pruned by synthesis and reaches no pin. The area its
  observability cones would add is **not measured**; the pruning is
  conservative for the does-not-fit conclusion (unobserved cones can only
  make the total smaller).
- Cycles per clip (shim schedule, confirmed by simulation, see below):
  **1,418,282** cycles counting the `start` cycle — the one convention used
  in this summary, matching `reports/cycle_check.txt` and the report's
  `sim_cycle_check` field (the report JSON's `clip_cycles_start_to_done` is
  the exclusive count, one cycle fewer) (4 cycles/sample, 705,611 per audio
  pass, 7,058 control-walk cycles). The second audio pass (the normalization
  replay re-render) costs **705,611** cycles. At the DR-0010 candidate clocks
  this *would be* 56.7 ms / 28.4 ms / 14.2 ms (25/50/100 MHz) **if** the
  design fit and met those clocks, which was not shown. DR-0010's serialized
  single-multiplier candidate is ~86.4 M cycles per clip; this top is the
  opposite end of that trade (fully parallel, unserialized) and is the reason
  it needs 324 multipliers.
- Simulation check (`tb/tb_fpga_cycles.sv`, Icarus, RTL level, **not** a
  post-synthesis or post-route simulation): with an all-zero static
  configuration and noise stream the shim reaches its final state in the
  predicted cycle count with the core's `done` set and no `error`. The
  stimulus is not the golden vector: **no numeric-correctness claim.**
  With that all-zero stimulus the noise DUT accepts no bytes
  (`reports/cycle_check.txt` reports `bytes=0`): `noise_byte_valid` is driven
  throughout `S_PRIME`/`S_GROUP`, but the accept path itself is not
  exercised — this check validates the shim schedule, not noise ingestion.
  The exact-sentinel simulation of the netlist is #374.

## #374: post-synthesis sentinel attempt and clean-checkout rebuild

**Outcome: the exact-sentinel simulation of the synthesized netlist is
REFUSED with a measured reason — the pinned open flow ships no simulation
model for the DSP primitive — and the clean-checkout rebuild reproduced
every #80 number with zero drift.** Details and commands:
`reports/post-synth-sim.json` (the refusal record) and
`reports/fpga-build-report.json` (`clean_checkout_rebuild`).

### The refusal (primitive-model gap)

- A **post-synthesis netlist of the unmodified `one_shot_voice_top`** (same
  staged sources and flow as the #80 measurement, no shim — the shim's
  pseudo-random host streams cannot carry golden vectors) was built by the
  new `synth_voice` stage: 376,656 cells — LUT4 223,603, CCU2C 61,661,
  PFUMX 51,467, L6MUX21 26,763, TRELLIS_FF 6,124, TRELLIS_DPR16X4 6,660,
  **MULT18X18D 378** (plus 18 `$specify2` cells that `write_verilog`
  renders as `specify` constructs). 556 MB of Verilog / 491 MB of JSON,
  both hash-bound in the report.
- The sentinel would have been the committed fixed-model golden vector's
  param-committed regression case `normalization:below`, derived and
  written by `tb/run_voice.py`'s own `voice_write_case`, compared
  sample-exactly with `voice_rows`/`voice_expected` (integer equality, no
  tolerance) — exactly the RTL lane's check. The stimulus was prepared and
  hashed; the simulation could not run:
- **Icarus Verilog 13.0**: parsed the entire 556 MB netlist in **14.8 s /
  3.0 GB peak RSS**, then failed elaboration with `Unknown module type:
  MULT18X18D` at every one of the 378 DSP instances (rc 123).
- **Verilator 5.050**: same failure independently —
  `Cannot find file containing module: 'MULT18X18D'` (rc 1, **81.7 s /
  9.2 GB peak RSS**, error limit 50).
- The gap is the toolchain's, not a missing file: the pinned
  yowasp-yosys 0.70 `share/ecp5` simulation library provides behavioral
  models for every other cell the netlist uses (LUT4, CCU2C, PFUMX,
  L6MUX21, TRELLIS_FF, TRELLIS_DPR16X4) but only a `(* blackbox *)`
  declaration for `MULT18X18D` (`cells_bb.v`). No design was shrunk,
  serialized or re-mapped to fit: per the issue, a committed refusal is a
  complete outcome.
- Consequence for DR-0017 D3.4: the **ADSR constant-bound loop rewrite's
  synthesized-netlist equivalence remains open**; the bounded simulation
  check (`fpga/scripts/check_adsr_div_equiv.py`, 100,290 vectors, 0
  mismatches) is still the only evidence for that rewrite.

### Clean-checkout rebuild (reproducibility)

A clean detached checkout of the rebinding commit (no working-tree
changes) ran `fpga/build.sh env stage synth engines pnr synth_voice`,
the RTL cycle check, `vsim` and `report`:

- Every compared field is **identical** to the #80 report (verdict
  `identical`, zero drift): all flat cell counts, all per-engine counts,
  `engines_failed`, device, yosys/nextpnr banners, venv package list, and
  the pnr exit (`nextpnr exit code: 1`, same wasm abort at TRELLIS_COMB
  400%). The **synthesized netlist JSON is byte-identical** to #80's
  despite the three rebound files' comment edits, as is
  `reports/cycle_check.txt`. Nondeterminism sources: none observed in any
  reported number (the toolchain is pinned by `fpga/requirements.txt`);
  the only run-to-run textual variance found is two cosmetic lines in
  nextpnr's stderr (banner placement and one zero-count `DTR` row),
  recorded by the differing `pnr_stderr_excerpt.txt`.
- `fpga/scripts/rebuild_compare.py --check` re-runs the same comparison
  between two committed reports and fails on any drift; the DR-0013 ->
  DR-0017 rebind regenerated the report's hash bindings in the same
  change (`build.sh`, `requirements.txt`, wrapper hashes moved; the
  netlist and all staged `tb/sv` sources did not).
- `reports/stat_voice_top.txt` / the report's `synthesis_voice_top` block
  carry the new netlist's own stat.

### DR-0010 assumptions vs the inferred mapping

- **P2/A5 (12 KiB sine LUT, macro vs distributed memory):** the 4097 x 24 ROM
  was **not** mapped to block RAM (`DP16KD` = 0). `sine_vco_engine.sv` reads
  `rom` combinationally through several read expressions (lines 240-258), so
  Yosys maps the ROM to LUT logic; a registered-read, one-port-per-ROM form
  would be needed for block RAM. This is a mismatch with the ROM assumption
  and the largest single area item; it is stated, not fixed (no RTL change).
- **Column memories** (5 x 1764 x 24 b, `upsample_engine`): inferred as
  distributed RAM (3,996 `TRELLIS_DPR16X4`, the summary table's distributed-RAM
  rows), again 0 block RAM.
- **P3/A8 (24x24 products, one serialized shared multiplier):** the integrated
  top is unserialized. `MULT18X18D` count 324 (a 24x24 signed product needs
  several 18x18 DSPs); the DR-0010 schedule candidate is not what was built.
- **A1-A4, A6-A9** are untouched: no arithmetic, width, rounding or clip-timing
  change. One loop header in `adsr_engine.sv` is rewritten to a constant-bound
  guarded loop in the *staged copy* only (DR-0017 D3); `tb/sv` is unchanged.
  Equivalence of that rewrite is checked by bounded simulation only
  (`python3 fpga/scripts/check_adsr_div_equiv.py`: function-level, Icarus,
  100,290 directed + fixed-seed random vectors, 0 mismatches); it is not a
  formal proof and does not cover the synthesized netlist (#374).

## Not run / not established

- Placement, routing, Fmax, slack, bitstream (`ecppack` not run): no timing.
- **Gate-level (post-synthesis/post-route) simulation**: REFUSED with a
  measured reason (#374, see above) — the pinned flow has no behavioral
  model for `MULT18X18D`; no post-route netlist exists at all because the
  design does not fit. The RTL-level checks (`tb/run_voice.py`,
  `tb/tb_fpga_cycles.sv`) are unchanged and remain the correctness
  evidence. The ADSR loop-rewrite synthesized-netlist equivalence caveat
  (DR-0017 D3.4) is **still open**.
- The #80 build ran once in a dirty working tree; #374's clean-checkout
  rebuild (see above) reproduced every reported number with zero drift
  and is bound into the regenerated `reports/fpga-build-report.json`
  (`clean_checkout_rebuild`).
- The report's `git.head` fields describe the tree each build actually ran
  in: #80's `4d650c1` (dirty, on `feature/issue-80`) and the rebuild's
  detached clean checkout of the #374 rebinding commit. Each report is
  bound to its inputs by its committed hash manifest
  (`reports/sources.json` and `hashes_sha256`); re-verify those hashes
  before re-using any number in `reports/`.
- Any board work, pin assignment (DR-0017 D1: none), programming, audio.
- Any ABC9 result: `abc9` did not finish in 590 s, so `abc -lut 4:7` was used.
- Whole-design `-noflatten` hierarchy: killed (4 GiB wasm limit); replaced by
  the standalone per-engine runs.

## Layout

| Path | Role |
| --- | --- |
| `rtl/fpga_oneshot_top.sv` | Measurement shim (sequencer + serial config + LFSR stimulus + CRC observer) around the unmodified core |
| `scripts/prepare_sources.py` | Stages `tb/sv` into `build/src` with five asserted synthesis-only edits (DR-0017 D3) |
| `scripts/gen_luts.py` | Emits the two `$readmemh` images (same gate as `tb/run_voice.py`) |
| `scripts/yowasp_bigstack.py` | Runs YoWASP tools with a bigger wasm stack (workaround, see friction) |
| `scripts/make_report.py` | Parses build outputs into `reports/` |
| `scripts/run_postsynth_sentinel.py` | #374: netlist/model inventories + the named exact-sentinel attempt; writes `reports/post-synth-sim.json` (verdict or committed refusal) |
| `scripts/rebuild_compare.py` | #374: report-vs-report comparison; `--check` fails on any resource-count drift |
| `build.sh` | `env stage synth engines pnr synth_voice vsim report` stages |
| `requirements.txt` | Pinned throwaway toolchain (installed only into `build/venv`) |
| `tb/tb_fpga_cycles.sv` | Shim schedule / cycle-count check (RTL level) |
| `reports/` | Committed evidence: JSON report, stats, pnr excerpts, post-synth-sim record |
| `build/` | Git-ignored venv, staged sources, netlists, logs |

## Run

```
fpga/build.sh            # all stages; needs uv and network for the first `env`
fpga/build.sh synth      # flat synthesis only (~15 min, single job; host-dependent)
fpga/build.sh synth_voice  # whole-voice post-synthesis netlist (#374)
python3 fpga/scripts/rebuild_compare.py \
  fpga/reports/fpga-build-report.json fpga/reports/fpga-build-report.json \
  --check   # the drift gate (identical reports -> exit 0)
```

Single-threaded by design (one tool at a time). The pinned toolchain installs
only into the git-ignored `fpga/build/venv`; nothing lands in the provisioned
host-tool paths. The first `env` stage may fetch a uv-managed CPython 3.14
(cached under `~/.local/share/uv/python`, outside the repo) when no suitable
interpreter is already present. The iverilog cycle check:

```
cd fpga/build/src && iverilog -g2012 -o ../tbc -P tb_fpga_cycles.WALK=176400 \
  ../../tb/tb_fpga_cycles.sv <staged sources> && vvp ../tbc
```

## Generic toolchain friction (for upstream reporting)

- yosys-slang in YoWASP Yosys 0.70: default 2 MiB wasm stack is exhausted
  ("wasm trap: call stack exhausted") on this RTL; `-j` threads unsupported
  (`thread constructor failed`) so `-j 1` is required. YoWASP exposes no
  stack-size option; `yowasp_bigstack.py` calls the exported
  `wasmtime_config_async_stack_size_set` through ctypes.
- yosys-slang cannot unroll a loop whose start is data dependent (`for (i =
  den_msb + 60; ...)`): it hit `unroll limit` and, raised, exceeded the
  4 GiB wasm memory.
- yosys-slang emits 136 "asynchronous load value missing" warnings for
  function-local variables inside `always @(posedge clk or posedge rst)`.
- `synth_ecp5` 0.70 has no way to skip ABC9; replacing `map_luts` with
  `abc -lut 4:7` via `-run` labels was needed. `-noflatten` on the full top
  exhausted memory.
- nextpnr-ecp5 (YoWASP) aborts with an uncaught wasm exception and no error
  text when the design exceeds device capacity.
- yowasp-yosys 0.70's `share/ecp5/cells_sim.v` includes `cells_ff.vh` and
  `cells_io.vh` twice (once via `common_sim.vh`, once itself), so no
  compiler accepts it as-is (`read_verilog +/ecp5/cells_sim.v` fails in
  Yosys itself with "Re-definition of module"; Icarus likewise).
  `run_postsynth_sentinel.py` flattens the include chain, inlining each
  header once (module texts untouched).
- yowasp-yosys 0.70 ships **no behavioral simulation model for
  `MULT18X18D`** — `cells_sim.v` covers every other ECP5 primitive the
  flow emits, but the DSP multiplier exists only as a `(* blackbox *)`
  declaration in `cells_bb.v`. This alone makes gate-level simulation of
  the synthesized netlist impossible in the open flow (#374 refusal).
- klt is not used here (FPGA, not the ASIC flow); no klt friction observed.
