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

## Modulation matrix + endpoint-aligned upsample engines (issue #72)

`tb/run_tb.py modmatrix` runs the bit-exact RTL 4x5 modulation matrix and
the five endpoint-aligned control upsamplers against the frozen fixed
model's committed golden vectors (`sim/reference/mod-matrix-golden-v1/`,
generated by `tools/generate_mod_matrix_golden.py`):

- **Engine cores** — `tb/sv/mod_matrix_engine.sv` (the single upstream
  `mod_matrix` occurrence: five routes, each the exact integer weighted
  sum of the four Q2.21 source columns over twenty C4 Q10.21 depth words,
  ONE declared half-even narrowing through the canonical scalar per
  output sample, C7 saturation, **no clamp**) and
  `tb/sv/upsample_engine.sv` (five concurrent instances; the exact
  rational endpoint-aligned coordinate walk `j*1763/176399`, uQ.31
  half-even fraction words, the exact blend numerator with one declared
  half-even narrowing, exact endpoint copies of control indices 0 and
  1763) under `tb/sv/tb_mod_matrix_upsample.sv`.
- **Vectors** — six committed cases x five routes x 1,764 control words
  inline plus per-route audio evidence for all 176,400 samples: sha256
  over the exact integer word list, word-exact endpoint/jitter samples
  inline, and (for the receipt case) the #54-convention f32le sidecars
  under `sim/reference/mod-matrix-golden-v1-traces/`. Every expected
  word is produced by the frozen composition's own control path
  (`FixedControlPath._mod_matrix`/`._upsample`), with accepted-contract
  digests in provenance. Case families cover the frozen receipt
  (`boundary:lfo_1.mod_depth:center`, re-deriving #54's ten
  mod-matrix/upsample trace digests, refusing on drift), route extremes
  in both signs (beyond +-1.0: the no-clamp territory), zero depth,
  mixed signs with a distinct per-route depth signature, and an LFO
  shape-sweep column case.
- **Flow** — contract binding + per-case sample-exact RTL runs (matrix
  AND full-length audio per route), back-to-back trigger replay (no
  reset between runs; the second run must reproduce its solo golden
  capture byte-for-byte — depth words, column memories, and walk
  counters are per-trigger), exported op counters asserted against the
  model mirror's saturation counts and the DR-0010 #72 owner row
  (matrix: 20 MACs + 5 narrowings per control tick; upsample: blend
  mult/add/narrow at 1/1/1 within the 2/1/1 owner-row cap per column per
  interior audio sample, the exact incremental coordinate walk reported
  as a declared extra; the two exact-boundary endpoints are declared
  exact copies), the emitted
  schedule constants checked against their live emission, and eight
  planted mutations each required to be DETECTED — five RTL (the
  upstream +-1.0 matrix clamp, ZOH instead of endpoint-aligned
  interpolation, the align_corners=False off-endpoint scale, a selector
  instead of the weighted blend, a dropped route) and three vector-side
  with exact localization (route swap, depth sign flip, one-ULP depth —
  each must move exactly the expected route's traces).
- **Host mirror** — `src/torchsynth_voice/mod_matrix_golden.py`
  re-walks the matrix and upsample integer dataflows through the model's
  own primitives and asserts row-equality against `FixedControlPath`'s
  own rows, so the equivalence chain is model -> mirror -> RTL with no
  harness-parallel implementation anywhere. #72 has no declared binary64
  shadow: both stages are declared-exact integer arithmetic.
- **Tests** — `tests/test_mod_matrix_engine.py`: committed-vector/
  live-model equality, frozen-receipt digest binding, sidecar byte
  custody and lossless word round-trip, mirror-vs-model row equality,
  endpoint/jitter binding, generator determinism (`--check`), and the
  full tb flow (skipped where Icarus Verilog is absent).

## Sine VCO engine (issue #73)

`tb/run_tb.py vco` runs the bit-exact RTL sine VCO engine against the
frozen whole-voice receipt's `vco_1.raw` traces
(`sim/reference/fixed-voice-golden-v1.json`, the #54 receipt; the four
directed vco_1 cases additionally carry committed f32le sidecar bytes
under `sim/reference/fixed-voice-golden-v1-traces/`):

- **Engine core** — `tb/sv/sine_vco_engine.sv`: the frozen model's sine
  source lane as exact integer RTL — the C4 depth-mod product with one
  declared half-even narrowing, C7 saturation, the model's own MIDI
  clamp band [0, 127] (a formatting clamp; Nyquist clamps stay
  forbidden per C7), the half-even K division
  `K = round((f/fs)*2^32)` with `f` the Q16.15 shadow word, the C2 u32
  wrapping phase that takes the first increment BEFORE the LUT read
  (post-step phase; first-increment-first), the initial phase injected
  ONCE per trigger as a half-even turn word, the accepted hash-linked
  4096x24 quarter-wave C5 table with linear interpolation (12 index +
  18 interp bits), and the S4 narrowing into Q2.21. The declared
  binary64 `midi->Hz exp2` shadow site is replayed host-side exactly as
  DR-0008 declares it an open approximation item: the host mirror forms
  the per-sample Q16.15 word and the tb drives it in — the #158
  anchor-flow pattern. No exp2 is implemented in RTL and no RTL claim
  is made for the shadow site. The pitch-modulation input is the #72
  endpoint-aligned upsample engine's declared output, consumed
  unchanged (the blend lives upstream).
- **Vectors** — the #54 whole-voice receipt's 26 param-committed cases
  (the 8 development-corpus cases stay digest-custody only, per the
  receipt's custody policy). Every case's stimulus (keyboard word, S1
  entry words, initial-phase turn word, upsampled pitch column, mod
  matrix column) is regenerated through the frozen composition's own
  control path (`FixedControlPath.render_words`) and pinned to the
  receipt's per-trace digests; the expected output is the host mirror's
  sine-lane re-walk, pinned to the receipt's frozen `vco_1.raw` digest;
  the directed sidecar bytes must unpack word-exactly to the mirror.
  Coverage spans unmodulated 440 Hz, upper tuning, upper mod depth with
  67,370 measured MIDI clamps (the min/mid/max frequency sweep), upper
  initial phase, and the whole directed/calibration case set.
- **Flow** — contract binding (receipt DR-0008 status, hash-linked LUT
  digest, constants-package digest) + per-case sample-exact RTL runs
  over the full 176,400-sample clip (every `vco_1.raw` word AND
  post-step phase word), back-to-back trigger replay (no reset between
  runs; the second run must reproduce its solo golden capture
  byte-for-byte — the initial phase word and counters are
  per-trigger), exported op counters asserted against the DR-0010 #73
  owner rows (pitch path 3 mults / 6 adds / 3 narrows / 1 exp2 +
  phase+LUT 1/2/1: the engine declares 3/7/4 RTL ops per sample and the
  host shadow supplies the fourth mult; the complete clip schedule is
  asserted: walked samples == emitted samples_per_pass), the emitted
  schedule constants checked against their live emission, and five
  planted mutations each required to be DETECTED against the pristine
  frozen truth — wrong LUT address, dropped phase increment, and
  phase-wrap saturation (RTL, caught on trace rows), plus the
  un-clamped pitch (the model's MIDI clamp band dropped from the pitch
  formation) and the selector-vs-blend mod input (the blended pitch
  column replaced by the control-rate selector pick), both planted
  stimulus-side through the declared shadow: the mirror re-derives the
  Q16.15 words from the mutated pitch exactly as the integrated lane
  would consume them, so the fault reaches the trace the AC names.
- **Host mirror** — `src/torchsynth_voice/vco_golden.py` re-walks the
  sine lane through the model's own primitives (the exp2 shadow
  replayed host-side like the anchor flow); the frozen receipt's
  per-case digests are the row-equality target the tb asserts, so the
  equivalence chain is frozen model -> mirror -> RTL with no
  harness-parallel implementation anywhere.
- **Tests** — `tests/test_vco_engine.py`: receipt structure + bindings,
  sidecar byte custody, full/prefix mirror-vs-frozen-digest equality,
  the half-even initial-phase turn word, first-increment-first phase
  dataflow, and the full tb flow (skipped where Icarus Verilog is
  absent).

## Square/saw VCO engine (issue #74)

`tb/run_tb.py vco2` runs the bit-exact square/saw VCO engine against the
frozen fixed model's committed golden vectors
(`sim/reference/square-saw-vco-golden-v1/`, generated by
`tools/generate_square_saw_vco_golden.py`):

- **Engine core** — `tb/sv/square_saw_vco_engine.sv` (+ the factored C5
  evaluator `tb/sv/quarter_wave_lut.sv`, the #68 format-true path
  instantiated twice: cos at the post-step phase, sin a quarter turn
  behind) under `tb/sv/tb_square_saw_vco.sv`. The **declared shadow
  boundary** (DR-0008/DR-0010 open approximation items, the #74
  declaration class) is replayed host-side as deterministic words and
  stated in the module header: the per-sample midi->Hz `exp2` word
  (Q16.15), the per-clip `partials_constant` word (s14.17), and the
  per-sample `tanh` fanout words (`square_q`/`left_q`, Q2.21 — the
  model's declared dead-fanout site is replayed for boundary
  completeness and intentionally unread). **No RTL transcendental is
  implemented or claimed.** Everything else is exact integer RTL owned
  by the engine: the C4 depth-mod multiply and saturated pitch sum, the
  [0, 127<<21] MIDI clamp, the exact half-even phase-increment division
  by the constant `2^15 * 44100` denominator (restoring long division;
  no floats), the C2 u32 wrapping phase seeded by the S3-replayed
  initial-turn word, both C5 interpolations (C6 half-even), the `driven`
  multiply into s14.17, the right-branch narrowing
  `half_even(2^43 + shape*cos2, / 2^22)` (the exact rational form of the
  model's binary64 expression), the final `left_q * right_q` combine
  into C1, and the C7 saturations with sticky counters. The engine
  exports its own computed pitch word (`m2`) so the harness proves the
  replayed shadow words were derived from exactly the RTL's pitch state.
- **Vectors** — fifteen committed cases: five **frozen-binding** cases
  re-declare the fixed-voice-golden-v1 cases whose `vco_2.raw` words are
  retained as exact f32le sidecars (`waveform:vco_2:saw`,
  `boundary:vco_2.mod_depth:upper`, `boundary:vco_1.tuning:upper`,
  `boundary:vco_1.mod_depth:upper`, `boundary:vco_1.initial_phase:upper`)
  and bind the frozen trace digest + sidecar bytes, so the RTL is
  validated directly against the frozen word stream; ten **dedicated
  regime** cases are full-clip renders of `FixedVoiceModel` itself at
  the selector/extrema points the frozen selection does not carry
  (intermediate shape mixes 0.25/0.5/0.75, the half-even selector-word
  ties at 2^-22 and 3*2^-22, the least-significant shape step 2^-21,
  1-2^-21 just below the saw boundary, depth-max with an intermediate
  shape, and the phase corners (quarter turn, just-below-full-turn, and
  the binary32-2*pi wrap-past-the-top)). Every case's
  `vco_2.raw` digest — the model's own bits — must be reproduced by the
  host mirror at generation, load, and run time. Rubric honesty: a
  bit-exact engine trivially sits inside the calibrated M1 vco_2 bands
  (2^-5 / open / 2^-11); the open band and the SNR floors stay
  NO VERDICT, and nothing here forces a verdict on them.
- **Flow** — contract binding + per-case sample-exact RTL runs (pitch
  word, driven word, right word, and `vco_2.raw` vs the mirror; and vs
  the frozen sidecar words on frozen-binding cases), back-to-back
  trigger replay (static words, phase, and counters are per-trigger),
  exported op counters asserted against the DR-0010 #74 owner row (8
  multiply-class ops, 8 add/sub-class ops, 7 declared narrowings per
  sample; sats = the model mirror's sticky saturation total), the
  emitted schedule constants checked against their live emission with
  the PARTIAL-DUT headroom report, and three planted mutations each
  required to be DETECTED — the wrong (1-shape) square/saw mix (RTL), a
  one-ULP partials-constant error (stimulus; must localize to the
  driven stream alone), and a shadow-word corruption in the replayed
  tanh fanout (stimulus; must localize to `vco_2.raw` alone).
- **Host mirror** — `src/torchsynth_voice/vco_golden.py` re-walks the
  model's vco_2 lane with the model's own primitives
  (`entry_quantize`, `mul`, `apply_policy`, `div_round`,
  `PhaseAccumulator`, the accepted C5 table) and computes the declared
  shadow sites host-side exactly as the model does; its proof obligation
  is digest equality against the frozen `fixed-voice-golden-v1` cases
  (verified for every public frozen case at generation time), so the
  equivalence chain is model -> mirror -> RTL with no harness-parallel
  implementation anywhere.
- **Tests** — `tests/test_square_saw_vco_engine.py`: committed-vector
  set, shadow-boundary declaration on every vector, frozen sidecar
  custody, model-digest binding per case, selector-regime/tie coverage,
  the shape-0 `left_q == square_q` identity, phase-wrap reach, generator
  determinism (`--check`), and the full tb flow (skipped where Icarus
  Verilog is absent).

## Gated, not in this increment

The remaining audio-rate lanes (#75 noise/VCA,
#76 mixer/normalization), the shadow exp2/tanh sites (a resident RTL
approximation for either is the open DR-0008/DR-0010 item; #74 owns the
declaration class and replays them host-side until a declared
approximation is ratified), the serialized single-MAC
schedule pipeline itself (the RTL consumer of the `SCHED_*` constants),
the integration of the #70/#71/#72 engines into the whole-voice one-shot top
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
