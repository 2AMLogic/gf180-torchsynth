# Composed independent float Voice: end-to-end conformance v1

This document declares the composed whole-voice float model for issue #43 and
the conformance record it publishes. It consumes the interfaces and checkpoint
map of [FLOAT-INTERFACES.md](FLOAT-INTERFACES.md) unchanged and composes the
three landed independent float models without re-proving them: the control
path (#40, `src/torchsynth_voice/control_path.py`,
[sim/reference/control-path-float-v1.json](../sim/reference/control-path-float-v1.json)),
the sources (#41, `src/torchsynth_voice/float_sources.py`,
[sim/reference/float-sources-v1.json](../sim/reference/float-sources-v1.json)),
and the mix chain (#42, `src/torchsynth_voice/float_mix.py`,
[sim/reference/trace-capture.json](../sim/reference/trace-capture.json)).
The per-module matches are settled evidence; the conformance question owned
here is whether the composed chain — fed only a resolved request, with each
module consuming the previous module's outputs instead of captured reference
buffers — stays conformant at the declared inter-module seams.

## Ownership and policy

- Model module: `src/torchsynth_voice/float_voice.py`. Stdlib-only; imports
  no TorchSynth, Torch or NumPy; deterministic per render; no state across
  renders. The composition owns cross-module wiring only and re-derives no
  DSP: every stage calls a landed module unchanged.
- Composed calculation policy (`float-voice-v1`): the staged function
  composition of the landed per-module policies in evaluation order. The
  render walks the 27 declared call sites of the checkpoint map and produces
  all 32 registry traces in registry order plus the three resolved inputs,
  from exactly one resolved request (78 canonical normalized names, observed
  physical map, resolved seed-13 noise slot).
- Wiring self-check: with the matrix inputs wired as declared, the composed
  matrix and upsampler columns must reproduce the landed control-path
  model's own outputs byte-for-byte; any divergence is a composition defect,
  refused before comparison.

## Comparison classes (separate, never aggregated)

The record [`sim/reference/float-voice-v1.json`](../sim/reference/float-voice-v1.json)
keeps four row classes apart; summaries count rows but never mask one class
with another:

| Class | Content |
| --- | --- |
| `structural-identity` | Every checkpoint's registry shape, rate, dtype and binary32 encodability through the composed render. |
| `validity` | Exact-byte input identity (`noise.raw` is the resolved bytes), request immutability, derived-gain rule, branch/byte relations, exact upsample endpoints, mutation intact rows, reset/replay invariants. |
| `coverage` | All 32 checkpoints present in evaluation order, per case. |
| `numeric-error` | Paired declared-metrics and exact-digest rows; each row carries its own limit and provenance. Store-gated rows are retained receipts and are never decided here. |

A mismatch is never hidden by an aggregate, an alignment, a scaling, or a
dropped case: rows are per case and per checkpoint, comparisons are
time-locked, and a store-gated row says so explicitly instead of passing.

## Cases and declared comparisons

- **Pinned source fixtures** (`tests/fixtures/float-sources/`, 5 cases): the
  composed model renders from each case's committed observed maps and noise
  identity. `control_upsample.vco_{1,2}_pitch` are compared against the
  committed captured buffers under the landed control-path rubric limits;
  `vco_{1,2}.raw` against the landed per-case float-sources limits;
  `noise.raw` is exact-bytes. Raw artifact links (path, SHA-256, size) are
  retained in the record.
- **Pinned directed normalization cases** (`normalization:above`, `below`,
  `tie`): composed from the committed base map plus directed overrides. The
  keyboard scalars, `noise.raw`, `mixer.peak`, `mixer.gain` digests, the
  strict `peak > 1` branch decision, the bypass/division byte relations, and
  the exact directed binary32 peak targets must equal the release-era
  capture bindings in `sim/reference/trace-capture.json`. Measured composed
  peaks land exactly on the directed targets (delta 0 on all three).
- **Development receipts** (`global-0`, `global-6`): the corpus resolver owns
  their physical maps and the raw whole-voice buffers remain in the
  operator's store; the record retains them as receipt links and enumerates
  store-gated rows, never verdicts. The 96 development cases and 392
  directed fixtures remain covered by the landed #130 capture and its
  committed rubric, bound here by digest — composed, not re-proved.

## Fault localization

Every numeric and validity row names its case and checkpoint. On a
divergence, `float_voice.diverged_boundary` walks the checkpoints in
evaluation order against whatever pinned references a case retains and
names the first checkpoint — and so the module boundary — that introduced
the divergence. Store-gated rows and digest-divergent declared-metrics
buffers are recorded with their raw artifact links so any later paired
comparison can localize numerically.

## Composed mutation controls (must fail)

Three preregistered cross-module wiring faults are injected through the
composed model. Each must fail its named composed rows on its gate case,
preserve its intact rows, and localize; a mutation that does not fail is a
defect of the control, not a pass:

| Mutation | Gate case | Must fail on | Localizes to |
| --- | --- | --- | --- |
| `swapped-vco-pitch` | `boundary:vco_1.mod_depth:upper` | `vco_1.raw` and every downstream row while the upsampled columns still match the pinned buffers | `control_upsample->vco` pitch input wiring |
| `lfo-adsr-control-swap` | `boundary:vco_1.mod_depth:upper` | `mod_matrix.*`, `control_upsample.*` and downstream while all four envelope traces stay intact | mod-matrix control-rate input wiring |
| `normalize-before-mix` | `normalization:above` | `mixer.peak`, `mixer.gain`, `mixer.output` and the captured branch/byte relations while the oscillator traces stay intact | normalization placement around the mixer |

`normalize-before-mix` is expectedly ineffective at or below the branch
(no division is applied either way); the per-case effectiveness table is
recorded, never silently dropped. These composed wiring faults extend — and
reuse the semantics of — the per-module negative controls of
[CONTROL-PATH.md](CONTROL-PATH.md), [FLOAT-SOURCES.md](FLOAT-SOURCES.md)
and [FLOAT-MIX.md](FLOAT-MIX.md), which remain in force at their own layers.

## CI regression subset

`tests/test_float_voice.py` runs the composed model, the class separation,
the exact classes, the bounded pinned numeric rows, the three mutations and
the committed record's static bindings in CI with no TorchSynth dependency
(stdlib only). `tools/compare_float_voice.py --check` statically re-verifies
the committed record's input digests, fixture links, mutation outcomes and
render invariants; the tool's default mode re-renders every decided row and
regenerates the record. Raw whole-voice buffers stay in the operator's
store: a row that has not run there is recorded `STORE-GATED`, never
reported as a pass.

## Explicitly deferred (numeric formats stay open)

Fixed word widths, Q formats, LUT organization, rounding and saturation
sites, approximation budgets, and float/fixed tolerances are **not**
specified here; the checkpoint map carries `numeric_contract =
"unbound:#53"` and DR-0008 remains Proposed — none of its selected values
is read, depended on, or ratified by this document, the composed model, or
the record. No fixed-model, RTL, hardware, synthesis, layout, signoff, or
sound-fidelity claim is established by this work.

## Verification

Bounded, honest checks executed for this document:

| Executed check | Observed result |
| --- | --- |
| `timeout 120 python3 -m compileall -q src tests tools` | recorded in the PR body |
| `timeout 900 python3 -m unittest discover -s tests -p test_float_voice.py -v` | 25 tests, all pass, recorded in the PR body |
| `timeout 900 python3 tools/compare_float_voice.py --record sim/reference/float-voice-v1.json` | 634 decided rows PASS, 0 FAIL, 81 store-gated receipts; 3/3 mutations fail and localize; record regenerated |
| `timeout 300 python3 tools/compare_float_voice.py --check sim/reference/float-voice-v1.json` | static record check OK |
