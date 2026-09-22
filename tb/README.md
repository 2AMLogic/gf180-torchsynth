# Testbench

This directory compares RTL bit-for-bit with the frozen fixed-point model.
Floating TorchSynth comparisons and estimator qualification belong in the
model/measurement layers first. `tb/run_tb.py` is the PDK-free runner
convenience named in AGENTS.md; no step in this directory requires a PDK,
vendor tooling, or synthesis.

## Golden-vector harness (issue #68)

Landed today, per the issue's declared Startable Subset:

- **Schema + loader + first-mismatch reporter** —
  `src/torchsynth_voice/golden_vectors.py`. The JSON vector format follows
  #54's AC shape: name-keyed parameter inputs, expected trace/output
  values, cycle/sample mapping, and a content hash. Trace names are
  validated against `spec/reference/trace-registry-v1.json`, parameter
  names against the 78-name `spec/reference/parameter-inventory-v1.json`,
  and clip timing against the canonical 4 s / 44.1 kHz profile
  (176,400 @ 44,100 Hz audio; 1,764 @ 441 Hz control). Vector files carry
  the registry semantic version and inventory commit they were generated
  against; the loader refuses version mismatches. Comparison is exact
  (no tolerance): model-to-RTL is sample-exact per AGENTS.md.
- **Synthetic self-test** — `tb/run_tb.py selftest` proves both the pass
  path and the intentional-fail path of the reporter through a tiny
  synthetic DUT (`tb/sv/synth_dut.sv`) driven by a file-based testbench
  (`tb/sv/tb_synth_dut.sv`) under Icarus Verilog. The DUT's 8-bit width is
  harness-local and deliberately unrelated to the DR-0008 choice register.
- **Real golden-vector anchor run** — `tb/run_tb.py anchor` graduates the
  harness to the #54-frozen sentinel
  (`sim/reference/golden-vector-fixed-anchor.json`). The flow:
  1. loads the anchor through the landed schema/loader (schema, registry,
     inventory, timing, content-hash checks);
  2. verifies the accepted-contract hash binding
     (`golden_vectors.verify_accepted_contract`): the vector's provenance
     digests must match the live constants package
     (`tb/sv/gf180_rtl_constants_pkg.sv`), the live choice register
     (`spec/reference/fixedpoint-choices-v1.json`), and the accepted
     DR-0008 record (bound through the frozen receipt's `bindings`, since
     the anchor provenance carries the package/register digests and the
     receipt carries the record digest). Any mismatch **refuses the run**
     with a "regenerate, don't recompile" error — interface changes fail
     the contract check instead of silently recompiling;
  3. builds the accepted quarter-wave table through the DR-0008 refusal
     gate and matches its digest against the vector's `lut_sha256`;
  4. derives the DUT control words from the vector's own declared data —
     the phase-increment word via the declared binary64 shadow-site policy
     (an open DR-0008 approximation item, replayed host-side, **not** an
     RTL claim) and the S1-entry mixer-level word via the model's entry
     quantization;
  5. simulates the format-true DUT `tb/sv/lut_sine_dut.sv` — the C5 LUT
     sine path (u32 wrapping phase per C2, 4096x24 quarter-wave table +
     linear interpolation per C5, half-even per C6, saturation per C7)
     plus one Q2.21 mixer-level multiply — every constant imported from
     the generated package, over the full 176,400-sample clip;
  6. replays the integer dataflow host-side with the model's own
     primitives and requires the mirror to reproduce the frozen
     `vco_1.raw` trace exactly (vector truth);
  7. requires the RTL capture to match sample-exactly, on the vector
     trace and on the derived mixer-level product stream;
  8. plants a fault and requires the reporter to name the exact
      cycle/sample/trace/expected/actual row on the real vector; and
   9. asserts the ratified clip budget (DR-0010 Accepted schedule): the
      budget constants' internal consistency, the landed constants package
      against its live emission, and a measured-vs-budget headroom report
      over the testbench-counted cycles — printed as a PARTIAL-DUT
      MEASUREMENT (the LUT DUT retires one sample per cycle and is not the
      serialized schedule; no schedule-conformance, PPA, or fit claim).
- **Generated-constants tooling** —
  `src/torchsynth_voice/fixedpoint/codegen.py` +
  `tools/generate_rtl_constants.py`. Consumes
  `spec/reference/fixedpoint-choices-v1.json` strictly through
  `torchsynth_voice.fixedpoint.choices.require_accepted`, and the DR-0010
  schedule register `spec/reference/rtl-schedule-v1.json` through
  `torchsynth_voice.fixedpoint.schedule.require_accepted_schedule`. Since
  the issue #53/#63 ratifications (DR-0008 and DR-0010 Accepted by
  reviewed merges, 2026-09-21) the tool emits the C1-C10 numeric formats
  plus the ratified cycle-budget constants
  (`SCHED_*`: C_counted 145, C = 145 + T parameterization, 2 passes,
  352,800 clip sample-slots, pass-2 fold ≤ 4 cycles, the refutable
  T ≤ 138 @ 25 MHz ≤ 1x-real-time bound, candidate clocks "25,50,100" with
  none selected) into `tb/sv/gf180_rtl_constants_pkg.sv`; `--check` fails
  CI if that package goes stale against either register. Every emitted
  constant cites its record's Accepted section; widths and cycle numbers
  are only ever taken from an accepted register payload, never hardcoded.
  Emitting the schedule makes no implementability, PPA, fit, or hardware
  claim (schedule-candidate; T is an elaboration-time lane parameter, not
  a constant; #82/#83 own the measurements).
- **CI** — `.github/workflows/tb-sim.yml` installs Icarus Verilog and runs
  the self-test, the anchor run, and the test suites. PDK-free.

## ADSR envelope engine (issue #70)

`tb/run_tb.py adsr` runs the bit-exact RTL ADSR envelope engine against the
frozen fixed model's committed golden vectors
(`sim/reference/adsr-golden-v1/`, generated by
`tools/generate_adsr_golden.py`):

- **Engine core** — `tb/sv/adsr_engine.sv`: one envelope instance per
  module; the testbench instantiates the registry's six (adsr_1, adsr_2,
  the four LFO rate/amp ADSRs) concurrently
  (`tb/sv/tb_adsr_engine.sv`). Everything except the declared binary64
  `**alpha` shadow power is exact integer RTL: Q2.60 ramp domain with the
  host-replayed 1e-6 shadow epsilon, C6 half-even at the canonical
  division and the declared narrowings, C7 saturation, Q16.30 length
  words formed host-side (S1/S2) and received in advance with the
  exact-zero flags, exact Q4.60/Q6.90 combine products narrowed once into
  the C1 Q2.21 control word.
- **Vectors** — six committed cases x six envelope instances x 1,764
  control samples, every expected word produced by the frozen
  composition's own control path (`FixedControlPath._adsr`), with
  accepted-contract digests in provenance. The frozen-envelope-receipt
  case re-derives the #54 frozen receipt's six ADSR trace digests and
  refuses on drift.
- **Flow** — contract binding + per-case sample-exact RTL runs,
  back-to-back trigger replay (no reset between runs; the second run must
  reproduce its solo golden capture byte-for-byte), exported op counters
  asserted against the DR-0010 #70 owner row (24 multiply-class ops, 12
  declared narrowings, 18 `**alpha` shadow words per control tick across
  all six instances; 18 ramp divisions reported as a declared extra), the
  emitted schedule constants checked against their live emission, and
  three planted mutations (wrong sustain level, linear-vs-exponential
  curve confusion, off-by-one timing) each required to be DETECTED.
- **Host mirror** — `src/torchsynth_voice/adsr_golden.py` replays the
  shadow power host-side and asserts its rows equal the model's own
  `_ramp` rows, so the equivalence chain is model -> mirror -> RTL with no
  harness-parallel implementation anywhere.
- **Tests** — `tests/test_adsr_engine.py`: committed-vector/live-model
  equality, frozen-receipt digest binding, mirror-vs-model equality,
  generator determinism (`--check`), and the full tb flow (skipped where
  Icarus Verilog is absent).

## LFO + control-rate VCA engine (issue #71)

`tb/run_tb.py lfo` runs the bit-exact RTL LFO + control-rate VCA engine
against the frozen fixed model's committed golden vectors
(`sim/reference/lfo-vca-golden-v1/`, generated by
`tools/generate_lfo_vca_golden.py`):

- **Engine core** — `tb/sv/lfo_vca_engine.sv`: one logical LFO plus its
  control-rate VCA per module; the testbench instantiates the registry's
  two (`lfo_1`, `lfo_2`) concurrently
  (`tb/sv/tb_lfo_vca_engine.sv`). Everything except the declared binary64
  shape-weight shadow (`w ** 2.718281828` with `math.fsum` normalization,
  replayed host-side as five Q2.30 words) and the declared S3-style
  initial-turn word (formed host-side over the pinned binary64 `2*pi`,
  like #70's S1/S2 length words) is exact integer RTL: the rate
  formation narrowing into Q16.30 with the pre-accumulation zero clamp
  (`s4.lfo.rate_clamp`), the increment site `K = round((rate/441)*2^32)`,
  the C2 u32 wrapping phase that accumulates the first increment before
  the initial phase is added, the five-shape blend from the frozen
  control path's own C5-shape quarter-wave table (Q1.23 entries — the
  sweep table the model instantiates, distinct from the accepted C5
  audio-path table), exact Q4.60 accumulate with one declared narrowing,
  and the Q2.21 x Q2.21 control VCA with a single declared narrowing.
  The rate/amplitude envelope streams arrive as Q2.21 words — the #70
  engines' declared outputs.
- **Vectors** — thirteen committed cases x two instances x 1,764 control
  samples x two traces (`lfo_<n>.raw`, `lfo_<n>.post_control_vca`),
  every expected word produced by the frozen composition's own control
  path (`FixedControlPath._lfo`/`._control_vca`), with accepted-contract
  digests in provenance. Case families cover single shapes and
  continuous blends (never selectors), weight boundaries (tiny-weight
  normalization, equal-weight ties), mod-depth extremes including the
  negative-rate zero clamp, frequency/phase extremes with multi-turn
  wrapping, rate/amplitude modulation drives, and min/max/degenerate
  states. The frozen-lfo-receipt case re-derives the #54 frozen
  receipt's four LFO/VCA trace digests and refuses on drift. All-zero
  shape weights are the model's declared undefined state: generation
  and the host mirror refuse, never repair.
- **Flow** — contract binding + per-case sample-exact RTL runs,
  back-to-back trigger replay (no reset between runs; the second run
  must reproduce its solo golden capture byte-for-byte — no cross-run
  or cross-instance phase/weight state survives a trigger), exported op
  counters asserted against the model mirror's measured clamp count and
  the DR-0010 #71 owner row (12 multiply-class ops <= ~14, 2 C5-class
  LUT interps, 2 phase steps per control tick across both instances; 20
  declared narrowings reported as a declared extra), the emitted
  schedule constants checked against their live emission, and five
  planted mutations (selector-instead-of-blend, wrong shape table,
  depth modulation dropped, phase first-increment skipped, rate-clamp
  removal — all RTL) each required to be DETECTED.
- **Host mirror** — `src/torchsynth_voice/lfo_golden.py` replays the
  shadow weights and initial-turn word host-side and asserts the integer
  mirror equals the model's own `_lfo`/`_control_vca` rows (including
  the sticky rate-clamp count the RTL counter must reproduce), so the
  equivalence chain is model -> mirror -> RTL with no harness-parallel
  implementation anywhere.
- **Tests** — `tests/test_lfo_vca_engine.py`: committed-vector/live-model
  equality, frozen-receipt digest binding, mirror-vs-model equality, the
  undefined-weights refusal, generator determinism (`--check`), and the
  full tb flow (skipped where Icarus Verilog is absent).

## Gated, not in this increment

The remaining control-path RTL beyond the LUT sine block and the
#71 LFO/control-VCA engine (the mod matrix and endpoint-aligned
upsamples, #72), the audio-rate sources and VCAs (#73-#76),
the shadow exp2/tanh sites, the serialized single-MAC
schedule pipeline itself (the RTL consumer of the `SCHED_*` constants),
the integration of the #70/#71 engines into the whole-voice one-shot top
(the #78/#79 conformance lanes consume them there), a resident RTL
implementation of the `**alpha` binary64 shadow (open DR-0008/DR-0010
item; replayed host-side until a declared approximation is ratified),
the concrete clock selection (#82/#83 timing evidence amends DR-0010), the
#82/#83 area/fit/PPA validations, and any synthesis/PDK step. Nothing
landed here claims synthesis, layout, signoff, or hardware conformance of
any kind.

## Vector file shape (v1)

```json
{
  "schema": "gf180-torchsynth/golden-vector-v1",
  "schema_version": 1,
  "trace_registry_version": "voice-traces-v1",
  "parameter_inventory_commit": "<40-hex pinned commit>",
  "clip": {
    "profile": "canonical-default-nebula-4s | synthetic-bringup",
    "sample_rate_hz": 44100,
    "sample_count": 176400,
    "control_rate_hz": 441,
    "control_count": 1764
  },
  "parameters": {"adsr_1.alpha": 0.5},
  "traces": [
    {
      "name": "mixer.output",
      "kind": "audio",
      "encoding": "f32le | synthetic-int",
      "cycle_start": 0,
      "values": ["..."]
    }
  ],
  "content_hash": "<sha256 of the canonical JSON of everything above>"
}
```

The reporter's first-mismatch line always carries the five mandated
columns: `cycle | sample | trace | expected | actual`.
