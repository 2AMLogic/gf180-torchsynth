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
- **Generated-constants tooling** —
  `src/torchsynth_voice/fixedpoint/codegen.py` +
  `tools/generate_rtl_constants.py`. Consumes
  `spec/reference/fixedpoint-choices-v1.json` strictly through
  `torchsynth_voice.fixedpoint.choices.require_accepted`, which refuses
  every C1-C10 entry while DR-0008 is Proposed — so today the tool
  emits nothing but the refusal report. That refusal is the deliverable's
  honest state; widths are only ever taken from an accepted register
  payload, never hardcoded.
- **CI** — `.github/workflows/tb-sim.yml` installs Icarus Verilog and runs
  the self-test plus the two test suites. PDK-free.

## Gated, not in this increment

Real default-nebula golden vectors (#54), accepted-contract hash-linking
(needs DR-0008 Accepted), cycle-budget constants (#63/#82), and any
synthesis/PDK step. Nothing landed here claims conformance to DR-0008.

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
