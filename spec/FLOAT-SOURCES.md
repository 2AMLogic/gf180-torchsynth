# Independent float source models v1

This document declares the landed independent float implementations of the
three source modules of the default Voice — `vco_1#1` (SineVCO), `vco_2#1`
(SquareSawVCO) and `noise#1` — delivered for issue #41. It consumes the
interfaces and checkpoint map of
[FLOAT-INTERFACES.md](FLOAT-INTERFACES.md) unchanged and owns no boundary
types. The control upsamplers feeding the VCOs belong to #40 and enter as
declared audio-rate inputs; the audio VCAs, normalization and mixer belong to
#42; end-to-end conformance belongs to #43.

## Ownership and policy

- Model module: `src/torchsynth_voice/float_sources.py`. Stdlib-only;
  imports no TorchSynth, Torch or NumPy; deterministic per render.
- Declared calculation policy: pinned-upstream op order at commit
  `2b0964d4c6c3d472a2a0d54d91b408caaeffca6d` (`float-sources-v1`,
  `CalculationPolicy(calculation_dtype="float32", ...)`). Elementwise
  arithmetic rounds to binary32 at every site in upstream order:
  `midi_f0 + tuning`, `mod_depth * mod`, the modulated-path `[0, 127]` MIDI
  clamp **before** the `440 * 2^((midi-69)/12)` conversion, then
  `(2*pi) * f / 44100` per sample.
- Phase semantics: the unbounded running cumsum the pinned CPU actually
  executes — a binary64 accumulator storing a binary32 partial per sample
  (pinned `cumsum_cpu_kernel` accumulates in `at::acc_type<float, false>`);
  no wrap, no modulo; `initial_phase` is added once after the whole cumsum,
  so sample zero is `f32(first increment + initial_phase)`, never the bare
  initial phase. The fixed model's wrapping accumulator is a later,
  separately gated decision (DR-0008, Proposed) and is deliberately not
  implemented here.
- SquareSawVCO `partials` uses `12000 / (max_f0 * log10(max_f0))` with
  `max_f0 = midi_to_hz(midi_f0 + tuning + max(mod_depth, 0))` — the
  unmodulated pitch plus full upward depth, never the per-sample clamped
  pitch. Above-Nyquist aliasing is reproduced as captured, never repaired:
  upstream has no frequency clamp outside its debug-only assertion.
- Noise: bit-exact by identity. The pinned CPU generator is a standard
  MT19937 with `init_genrand` seeding and seed 13;
  `uniform_(-1, 1)` on binary32 maps each draw to
  `f32(f32(f32((raw & 0xFFFFFF) * 2^-24) * 2) - 1)`, filling row-major over
  `(32, 176400)` precomputed slots; the canonical slot is
  `sound_index % 32`. `noise#1` consumes the resolved `input.noise` bytes
  verbatim and never regenerates them.

## Comparison classes and declared limits

Captured evidence and fixtures come from the qualified release-era
environment via `tools/capture_float_sources.py`; the bounded record is
[`sim/reference/float-sources-v1.json`](../sim/reference/float-sources-v1.json)
and the per-case buffers live under `tests/fixtures/float-sources/`.

| Boundary | Class | Declared comparison |
| --- | --- | --- |
| `noise.raw` / `input.noise` | `exact-bytes` | All 32 slot digests reproduce the pinned torch streams bit-exactly. No tolerance. |
| `vco_1.raw`, `vco_2.raw` | `declared-metrics` | `paired_metrics` at 44.1 kHz, per-case `max_abs_error` limits in the record. No byte identity (DR-0007). |

Measured max abs error at the declared checkpoints: constant-frequency cases
stay within ~1.8e-7 (one to two binary32 ulp of the waveform); the pitch-swing
cases measure up to 4.2e-3 against a 5e-2 limit, dominated by a characterized
drift mechanism — a reachable pitch input where the model's binary64 libm
`exp2` rounds differently from torch's SLEEF binary32 `exp2` (7 of 7675
measured inputs, one ulp each) shifts the increment of every sample visiting
it, and an input on the ADSR sustain plateau accumulates ~3.5e-3 rad over the
~33k plateau samples. The limits cover several such plateaus plus
cross-platform libm variance.

Fault localization is required, not optional: mutations that ignore tuning,
clamp frequency inside the band ("repair" aliasing), wrap the phase
accumulator, assume the bare-initial-phase convention, force the square
shape, or select a wrong noise slot/seed must fail their comparisons. A
Nyquist (22050 Hz) clamp is bit-identically ineffective inside the pinned
clamped-MIDI domain (maximum ~12.54 kHz) and is recorded as a no-op, not a
passing "fix".

## Explicitly deferred (numeric formats stay open)

Fixed word widths, Q formats, LUT organization, rounding and saturation
sites, approximation budgets, and float/fixed tolerances are **not**
specified here; the checkpoint map carries `numeric_contract = "unbound:#53"`
and DR-0008 remains Proposed. Nothing in this document reads, depends on, or
ratifies any selected DR-0008 value. No runtime, fixed-model, hardware, or
sound-fidelity claim is established by these models or records.

## Verification

Bounded, honest checks executed for this document:

| Executed check | Observed result |
| --- | --- |
| `timeout 120 python3 -m compileall -q src tests tools` | recorded in the PR body |
| `timeout 600 python3 -m unittest discover -s tests -p test_float_sources.py -v` | 21 tests, all pass, recorded in the PR body |
| `python3 tools/capture_float_sources.py --check` | fixtures and record consistent |
| capture run inside the qualified image | five directed cases rendered; exp2 agreement and torch-side increment digests recorded |
