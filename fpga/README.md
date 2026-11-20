# FPGA target (issue #80, DR-0017)

A resource-generous **feasibility measurement** of the passing one-shot RTL
(`tb/sv/one_shot_voice_top.sv`, #79) on one Lattice ECP5 device with the open
flow. It is evidence about that device, front end, tool version and stimulus
only. It is **not** evidence of gf180mcu feasibility, hardware playback or
sound fidelity, and it does not change the arithmetic profile.

Decision record: [`spec/decision-records/0017-fpga-measurement-target.md`](../spec/decision-records/0017-fpga-measurement-target.md)
(Proposed until a reviewed merge ratifies it).

> **Numbering note.** This record was DR-0013 until it was renumbered to
> DR-0017 on rebase (`main` landed a different 0013 first). The committed
> evidence in `reports/` — and `build.sh`, `requirements.txt` and
> `rtl/fpga_oneshot_top.sv`, whose SHA-256 that evidence binds — still read
> `DR-0013` and are deliberately left byte-identical, so the recorded hashes
> stay valid. `DR-0013` in those files means this record. See the DR's
> "Renumbering note".

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
| Distributed RAM (`TRELLIS_DPR16X4` 3,996 -> `TRELLIS_RAMW`) | 3,996 | 10,455 | 38% |

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
  **1,418,281** cycles from `start` (1,418,282 counting the `start` cycle, as simulated) (4 cycles/sample, 705,611 per audio pass,
  7,058 control-walk cycles). The second audio pass (the normalization
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

### DR-0010 assumptions vs the inferred mapping

- **P2/A5 (12 KiB sine LUT, macro vs distributed memory):** the 4097 x 24 ROM
  was **not** mapped to block RAM (`DP16KD` = 0). `sine_vco_engine.sv` reads
  `rom` combinationally through several read expressions (lines 240-258), so
  Yosys maps the ROM to LUT logic; a registered-read, one-port-per-ROM form
  would be needed for block RAM. This is a mismatch with the ROM assumption
  and the largest single area item; it is stated, not fixed (no RTL change).
- **Column memories** (5 x 1764 x 24 b, `upsample_engine`): inferred as
  distributed RAM (3,996 `TRELLIS_DPR16X4`), again 0 block RAM.
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
- Post-synthesis / post-route simulation and clean-checkout rebuild
  reproducibility: #374. `build.sh` was run once in the working tree
  (dirty state recorded in the report); nondeterminism is **not** quantified.
- The report's `git.head` (`4d650c1`) and `git.dirty: true` describe the tree
  the build actually ran in; the build was **not** re-run on later branch
  heads and the numbers were not re-measured. The report is bound to this
  tree by its committed hash manifest, not by that stale `git.head`:
  `reports/sources.json` records the SHA-256 of all 12 staged sources and
  still matches `tb/sv/*.sv` at the current head, and the report's
  `hashes_sha256` for the wrapper (`rtl/fpga_oneshot_top.sv`), `build.sh`,
  `requirements.txt` and the golden vector still match the committed files.
  The one input path that moved since the build — `write_lut_memh`, from
  `tb/run_tb.py` to `tb/tb_harness.py` (re-exported; `scripts/gen_luts.py`
  imports both helpers from `tb/run_tb.py`) — moved byte-identical, so the
  ROM-generation path that produced the committed images is unchanged.
  Re-verify those hashes before re-using any number in `reports/`.
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
| `build.sh` | `env stage synth engines pnr report` stages |
| `requirements.txt` | Pinned throwaway toolchain (installed only into `build/venv`) |
| `tb/tb_fpga_cycles.sv` | Shim schedule / cycle-count check (RTL level) |
| `reports/` | Committed evidence: JSON report, stats, pnr excerpts |
| `build/` | Git-ignored venv, staged sources, netlist, logs |

## Run

```
fpga/build.sh            # all stages; needs uv and network for the first `env`
fpga/build.sh synth      # flat synthesis only (~15 min, single job)
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
- klt is not used here (FPGA, not the ASIC flow); no klt friction observed.
