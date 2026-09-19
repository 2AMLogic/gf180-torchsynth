# Independent float-model interfaces and trace checkpoints v1

This document declares the interfaces between the pinned TorchSynth Voice
modules and the candidate independent float-model layers: input/output names,
kinds, shapes, dtypes, encodings, units, ranges, rates, time origins and
endpoints, argument roles, state and reset ownership, and the ordered
checkpoint map onto the landed trace registry. It is arithmetic-independent on
the fixed side: numeric formats for the later fixed model stay open for issue
[#53](https://github.com/2AMLogic/gf180-torchsynth/issues/53) and DR-0008,
which is **Proposed** — none of its selected values is accepted here, and
consumers of this document must refuse to treat any fixed-point width,
scaling, rounding, or approximation choice as accepted.

The module `torchsynth_voice.float_interfaces` owns the boundary types and
validation only. It imports no TorchSynth, Torch, or NumPy, implements no DSP,
executes no render, and qualifies no runtime or fidelity. Actual independent
algorithms belong to #40/#41/#42; end-to-end conformance belongs to #43.

## Sources of truth (landed, read-only)

| Input | Identity used here |
| --- | --- |
| [`reference/trace-registry-v1.json`](reference/trace-registry-v1.json) | Sole trace registry (#22). Content identity `registry_token()` = `tr1-` + SHA-256 of the exact file; the checkpoint map embeds and re-verifies it. Its trace-array order is the evaluation order. |
| [`reference/parameter-inventory-v1.json`](reference/parameter-inventory-v1.json) | The exact 78 canonical parameter names (#16); the checkpoint map embeds and re-verifies its SHA-256. |
| DR-0006 (Accepted) | Canonical float runtime `release-mkl-compatible-v1`; provenance field of every resolved request. |
| DR-0007 (#88) | Batch-1 scalar execution is a **diagnostic oracle** under the measured release-era runtime, not a normative bridge. Canonical batched rows remain the downstream reference. Matching inputs never imply scalar or cross-host byte equivalence. |
| DR-0008 (#53) | **Proposed** fixed-point contract. Referenced only to enumerate what stays open; its Choice Register values are not accepted. |

A changed registry fingerprint must fail stale checkpoint mappings; the
validation in `float_interfaces.load_checkpoints` enforces exactly that.

## Declared module interfaces

Every default-Voice module call site, in evaluation order, with argument
roles bound. Shared module instances appear as distinct occurrences with the
counts upstream actually exercises: **2** control-VCA, **5** upsampler and
**3** audio-VCA call sites. The map is one-to-one with the registry's 32
named traces plus the three input checkpoints; `mixer.gain` is the only
derived trace, justified below.

| # | Call site | Inputs (role) | Outputs | Boundary notes |
| --- | --- | --- | --- | --- |
| 1 | `keyboard#1` | — | `keyboard.midi_f0`, `keyboard.duration` | Physical scalars; continuous MIDI, no note rounding |
| 2–5 | `lfo_1_rate_adsr#1`, `lfo_2_rate_adsr#1`, `lfo_1_amp_adsr#1`, `lfo_2_amp_adsr#1` | `keyboard.duration` (note-duration) | one envelope each | Post-envelope-ramp-limits |
| 6 | `lfo_1#1` | `lfo_1_rate_adsr.output` (frequency-envelope) | `lfo_1.raw` | Modulation affects Hz before the nonnegative-frequency clamp |
| 7 | `control_vca#1` | `lfo_1.raw` (waveform), `lfo_1_amp_adsr.output` (gain-envelope) | `lfo_1.post_control_vca` | Shared instance 1 of 2 |
| 8–9 | `lfo_2#1`, `control_vca#2` | as 6–7 for LFO 2 | `lfo_2.raw`, `lfo_2.post_control_vca` | Shared instance 2 of 2 |
| 10–11 | `adsr_1#1`, `adsr_2#1` | `keyboard.duration` (note-duration) | `adsr_1.output`, `adsr_2.output` | Note-off known in advance |
| 12 | `mod_matrix#1` | `adsr_1.output`, `adsr_2.output`, `lfo_1.post_control_vca`, `lfo_2.post_control_vca` | `mod_matrix.{vco_1_pitch, vco_1_amp, vco_2_pitch, vco_2_amp, noise_amp}` | 4-by-5 matrix; input order is main ADSR1, main ADSR2, post-VCA LFO1, post-VCA LFO2; **no matrix clamp** |
| 13 | `control_upsample#1` | `mod_matrix.vco_1_pitch` | `control_upsample.vco_1_pitch` | Endpoint-aligned; shared instance 1 of 5 |
| 14 | `vco_1#1` | `keyboard.midi_f0` (pitch-base), `control_upsample.vco_1_pitch` (pitch-modulation) | `vco_1.raw` | Modulation affects MIDI pitch before the modulated-path [0, 127] clamp and conversion |
| 15–16 | `control_upsample#2`, `vca#1` | `mod_matrix.vco_1_amp`; `vco_1.raw` + upsampled amp | `control_upsample.vco_1_amp`, `vco_1.post_vca` | Upsampler 2 of 5; VCA 1 of 3 |
| 17–20 | as 13–16 for VCO 2 | | | Upsamplers 3–4 of 5; VCA 2 of 3 |
| 21 | `noise#1` | — | `noise.raw` | Consumes `input.noise` bytes exactly; never regenerates them |
| 22–23 | `control_upsample#5`, `vca#3` | `mod_matrix.noise_amp`; `noise.raw` + upsampled amp | `control_upsample.noise_amp`, `noise.post_vca` | Upsampler 5 of 5; VCA 3 of 3 |
| 24 | `normalize_if_clipping` (entry) | weighted mix | `mixer.pre_normalization` | Observed at the Python call; original input, not reconstructed |
| 25 | `normalize_if_clipping` (reduction) | `mixer.pre_normalization` | `mixer.peak` | Whole-clip absolute maximum; original return-frame local |
| 26 | `normalize_if_clipping` (decision) | `mixer.peak` | `mixer.gain` | **Derived** diagnostic; upstream divides by the peak directly on the strict `peak > 1` branch |
| 27 | `mixer#1` (return) | `vco_1.post_vca`, `vco_2.post_vca`, `noise.post_vca` (weighted sources 1–3) | `mixer.output` | Mixer order is VCO1, VCO2, noise; hook fires at the post-normalization return; aliases the final `Voice.output` |

This is a recording of source evaluation order, not registration or
alphabetical order, traced at the pinned commit (`synth.py:475-510`,
`module.py` buffer/clamp/noise/matrix/mix regions; see the issue's verified
corrections). Contract doubles must fail when equal-shaped arguments or
checkpoint labels are swapped; invocation order and argument identity are
checked, not numerical fidelity.

## Buffer, rate, unit, dtype, and time semantics

- **Kinds and grids.** Control traces: 441 Hz, 1764 samples, shape `[1764]`.
  Audio traces: 44100 Hz, 176400 samples, shape `[176400]`. Scalar facts
  (`keyboard.midi_f0`, `keyboard.duration`, `mixer.peak`, `mixer.gain`):
  `rate_hz = null`, `sample_count = null`; a scalar keyboard, peak, or
  decision fact has **no invented sampling rate**. A scalar's shape is `[]`
  for keyboard outputs and `[1]` for whole-clip facts, per the registry.
- **Dtype and encoding.** All named buffers are `float32` `f32le`, matching
  the pinned upstream reference dtype and DR-0007's binary32 input rule. The
  candidate model's *calculation* dtype and operation order are a separate,
  explicitly declared and versioned `CalculationPolicy`; an implicit Python
  binary64 computation presented as original binary32 is a violation.
- **Time origin and endpoints.** Every sampled trace is trigger-relative:
  control index `i` is at `i/441` s and audio index `j` at `j/44100` s. The
  first sample is index 0 (time `0/1`) and the last is index `count - 1`;
  dropped endpoints, silent padding, or alignment are contract failures.
- **Upsampling coordinate contract.** Output index `j` reads source
  coordinate `j*(1764-1)/(176400-1)` (exact rational; endpoints coincide
  with control indices 0 and 1763). This is a coordinate contract only; it
  does not mandate changing any floating evaluation order.
- **First-sample phase convention.** The pinned LFO/VCO phase accumulations
  include the first frequency increment before the initial phase is added;
  sample zero is not the bare initial phase. Request doubles must not assume
  otherwise.
- **Fractional timing.** ADSR stage durations may be fractional control
  samples; duration arrives in seconds and note-off is known in advance.
  Physical (and normalized) values are never rounded to indices or integer
  notes.
- **`output` versus `forward` sizing.** The contract binds the declared
  observable output buffers above. Internal `forward` intermediates may be
  sized however an implementation likes; only declared outputs are
  checkpoints.

Continuous LFO shape weights are a blend, not a discrete selector; undefined
numeric states (for example all-zero shape weights) are refused or recorded
as undefined — never silently repaired.

## Resolved request contract

One render consumes exactly one resolved sound:

- **Normalized parameters.** A name-keyed map over the exact 78 canonical
  inventory names. Positional, missing, extra, or duplicate name
  interchange is refused. Each value is finite, inside the declared
  normalized domain `[0, 1]`, and exactly representable as binary32; bools
  are refused. Valid normalized bytes are preserved separately from
  observed physical values and from any model-produced conversion results.
- **Selected noise.** Declared with seed 13, slot `sound_index % 32`,
  exactly 176400 finite binary32 samples, and a SHA-256 of those bytes.
  The model renders the resolved stream verbatim (`noise.raw` is its later
  graph observation); it never invokes upstream randomization and never
  regenerates a nonzero slot from a fresh batch-1 seed.
- **Observed physical conversion.** `physical.parameters` is the actual
  name-keyed runtime conversion of the accepted normalized inputs — the
  earliest numerical comparison seam. Analytic fixture values must never be
  substituted for it, and batched physical values must never be silently
  substituted to suppress scalar drift.
- **Identity.** `SoundIdentity` coordinates, noise slot, and train
  designation are reused. Different directed overrides that share a global
  index are not the same resolved patch; directed case identity is carried
  by the request, not by the index alone.
- **Provenance.** Source commit `2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`,
  profile `torchsynth-1-voice-default`, runtime
  `release-mkl-compatible-v1` (DR-0006, Accepted), and an explicit
  execution status of `canonical-batched` (normative reference) or
  `diagnostic-batch-1` (diagnostic oracle, DR-0007, which must copy the
  resolved noise slot exactly). Unknown values are refused.
- **Parameter/noise resolution and runtime admission** belong to their
  producers; this contract validates a resolved request, it does not
  generate one.

## Exact versus measured boundaries

| Boundary | Class | Meaning |
| --- | --- | --- |
| `input.normalized`, `input.noise` | `exact-bytes` | The model consumes these bytes exactly. |
| `physical.parameters` | `measured-observation` | Captured runtime conversion; compared at this seam, never replaced analytically. |
| All 31 graph traces | `declared-metrics` | Observed upstream buffers, compared under declared metrics. No cross-width or cross-host byte identity is assumed (DR-0007). |
| `mixer.gain` | `derived-diagnostic` | Justified derived trace: a reciprocal computed from the observed peak. Upstream applies division directly; a multiply-by-reciprocal implementation is a later numerical choice and is not proof of byte-identical division. |

Normalization branch behavior to preserve in any consumer: bypass at peak
1, silence, earliest-index peak ties, late peaks, and the strict `peak > 1`
condition over the complete clip.

## Reset, replay, and state ownership

State is per module and per clip: phase/sample position, envelope state and
temporary coefficients, the exact noise cursor, trace bookkeeping, and any
cached buffers. After reset, a repeated request must produce the same call
log and outputs, must not inherit prior call state, and must not mutate the
resolved request. Changed-request-then-restoration sequences must leave no
residue. This is an offline whole-clip software interface: no live-note
semantics, and no ratification of DR-0003's proposed hardware
replay/buffering architecture.

## Explicitly deferred (numeric formats stay open)

Fixed word widths, Q formats, LUT organization, rounding and saturation
sites, approximation budgets, hardware timing, and float/fixed tolerances
are **not** specified here. They belong to #53 / DR-0008, whose status is
Proposed; the checkpoint map carries `numeric_contract = "unbound:#53"` and
consumers must refuse to read any accepted numeric contract out of this
document. Contract doubles prove invocation, validation, association, and
reset behavior — never numerical fidelity.

## Downstream handoffs

- Verified #24 trace artifacts are consumed through the documented artifact
  adapter chain; no second capture, store, or renderer is introduced here.
- #87's case registry models sample captures as f32le/f64le with positive
  rates; scalar summaries in this contract acquire no fictitious Hz to fit
  it. Direct mappings are the sampled traces; the three input checkpoints
  and `mixer.gain` need an explicit consumer extension or refusal, routed to
  their owners before implementation.
- #40/#41/#42 own the algorithms behind these interfaces; #43 owns
  end-to-end conformance.

## Verification

Bounded, honest checks executed for this document (stdlib only, optional
packages absent):

| Executed check | Observed result |
| --- | --- |
| `timeout 120 python3 -m compileall -q src tests tools` | recorded in the PR body |
| `python3 -m unittest discover -s tests -v` (focused new file reported there) | recorded in the PR body |
| Checkpoint map/schema validation against the landed registry and inventory fingerprints | enforced by `float_interfaces.load_checkpoints()` in the new tests |

No runtime, independent-model-conformance, hardware, or scorecard PASS
record is generated from these doubles.
