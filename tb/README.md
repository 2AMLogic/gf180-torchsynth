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
     cycle/sample/trace/expected/actual row on the real vector.
- **Generated-constants tooling** —
  `src/torchsynth_voice/fixedpoint/codegen.py` +
  `tools/generate_rtl_constants.py`. Consumes
  `spec/reference/fixedpoint-choices-v1.json` strictly through
  `torchsynth_voice.fixedpoint.choices.require_accepted`. Since the issue
  #53 ratification (DR-0008 Accepted by reviewed merge, 2026-09-21) the
  register admits C1-C10 and the tool emits
  `tb/sv/gf180_rtl_constants_pkg.sv`; `--check` fails CI if that package
  goes stale against the register. Widths are only ever taken from an
  accepted register payload, never hardcoded.
- **CI** — `.github/workflows/tb-sim.yml` installs Icarus Verilog and runs
  the self-test, the anchor run, and the two test suites. PDK-free.

## Gated, not in this increment

Cycle-budget constants (#63/#82 — both open; the schedule dimension of
this issue stays NO VERDICT until #63's microarchitecture schedule lands),
the remaining control-path/VCA/normalization RTL beyond the minimal LUT
sine block (the shadow exp2 site included), and any synthesis/PDK step.
Nothing landed here claims synthesis, layout, signoff, or hardware
conformance of any kind.

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
