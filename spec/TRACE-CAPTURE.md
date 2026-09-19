# Voice trace capture v1

## Scope and ownership

This is the sole production passive-capture implementation for the pinned
Voice (`torchsynth/torchsynth` at `2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`).
It consumes #22's versioned registry (`trace-registry-v1`) and validator; it
does not fork the name/schema table, does not make any seam writable, and does
not store artifacts (#24) or render public artifacts (#15). Canonical
numerical evidence requires DR-0006's measured `release-mkl-compatible-v1`
host/profile; DR-0007 scalar execution remains diagnostic.

Owned files: `src/torchsynth_voice/trace_capture.py`,
`tests/test_trace_capture.py`, `tools/qualify_trace_capture.py`,
`env/release-era/capture_traces.py`, this document, the bounded publication
`sim/reference/trace-capture.json`, and `.github/workflows/trace-capture.yml`.

## Non-perturbation contract

- Every observer returns `None`; graph code is never replaced. Captured and
  uncaptured executions must produce byte-identical final audio, name-keyed
  normalized and actual-physical parameter bytes, selected noise bytes and RNG
  state within the same runtime and execution.
- No observer mutates inputs, outputs, parameters or buffers, and no observer
  draws random values. Selected original values are snapshotted (cloned)
  before later mutation can alias them.
- Per-render invocation bookkeeping is independent of selection: every
  registered graph call is tracked in original order with original argument
  object identity through #22's `CallTracker` — the reused `control_vca`
  (2 calls), `control_upsample` (5 calls) and `vca` (3 calls) occurrences are
  all counted even when only one occurrence's output is requested. Missing,
  extra, out-of-order or wrongly associated calls are rejected, never assigned
  by a plausible occurrence number; numerically equal but distinct input
  objects are rejected.
- `mixer.pre_normalization` observes the original `normalize_if_clipping`
  call input; `mixer.peak` observes the return-frame `max_sample` local. The
  profiler is bound to the intended Voice/mixer invocation: a normalization
  call from any other site is rejected as a wrong clamp site. The weighted mix
  is never reconstructed and the peak reduction is never rerun.
- `mixer.gain` is an explicitly derived binary32 diagnostic (one when the
  observed peak is at most one, otherwise its reciprocal). It is never applied
  to audio, never substitutes the original division, and is marked
  `observation: derived` in the registry.
- Observers and the normalization profiler are removed after success, render
  or capture errors, and partial setup failure. A conflicting active profiler
  refuses capture instead of destroying another caller's observer, and a
  refused or failed session leaves the Voice untouched.

## Production API

`src/torchsynth_voice/trace_capture.py` imports only the stdlib and the #22
registry module; callers inject the Torch module, the Voice, and the original
`torchsynth.util.normalize_if_clipping`, so the Python 3.9 release worker can
import the file directly.

```python
from torchsynth_voice.trace_capture import TraceCapture

session = TraceCapture(voice, registry_document, torch, normalize_if_clipping,
                       names=["adsr_1.output", "mixer.peak"], slot=0, batch_size=32)
with session:
    audio, forward, labels = voice(0)
session.inventory        # validated capture descriptors, graph-ordered subset
session.values           # selected-sound snapshots for requested traces only
session.tracker.counts   # complete invocation bookkeeping, selection-independent
```

`names=None` keeps all 32 registry traces; an empty selection keeps pure
invocation bookkeeping. Requests are validated (unknown names, duplicates,
unsupported configuration) before any observer is attached.

## Qualified-runtime protocol

`tools/qualify_trace_capture.py` (default mode) is host-gated to DR-0006's
measured Apple M5 / macOS 26.5.1 / Docker 29.7.2 (server linux/arm64) scope.
It rebuilds the unchanged release image for linux/amd64, launches the Python
3.9 worker `env/release-era/capture_traces.py` offline with one CPU, 6 GiB
memory, all thread counts one, `MKL_CBWR=COMPATIBLE` and
`ATEN_CPU_CAPABILITY` unset, then verifies every raw artifact after the worker
exits and writes the bounded publication. Output directories are never reused.
The worker revalidates the pinned source hashes before importing upstream,
reuses the read-only release runtime gates (#12/#88 producers, not their
capture code), and refuses environments outside `release-mkl-compatible-v1`.

Preregistered bounded development set (no corpus or holdout render is
authorized): corpus draws `global-0` and `global-6` plus directed
`normalization:above`, `normalization:below` and `normalization:tie` — the
three original normalization branches, including the tie-at-boundary
convention. Each case renders uncaptured, with the preregistered partial
selection (`adsr_1.output`, `control_upsample.vco_1_pitch`, `mixer.peak`,
`mixer.output`) and with full capture. `global-0` additionally compares all 32
full-capture trace hashes, selected audio and selected noise against the
committed #22 prototype publication, and runs the passive-cleanup controls: a
capture failure injected mid-render must clean up and leave later renders
byte-identical, and a conflicting profiler must be refused.

For every case the worker checks complete upsample endpoint pairs (first and
last binary32 samples of each `control_upsample.*` against its
`mod_matrix.*` source; truncated, padded or wrong-rate traces are rejected,
never repaired), records the actually observed normalization branch and
verifies `mixer.output` equals the original division branch of the observed
`pre_normalization` and `peak` bytes, exercises the original
`normalize_if_clipping` on synthetic below/at/above-one, silence, tied and
late-peak inputs, and reruns registry- and capture-level negative controls
(missing, swapped, wrong-rate/shape/boundary/clamp-site, malformed digest).

## Costs

For each case and mode the worker records wall-clock render seconds, monotonic
process peak RSS after the render, the original batch allocation (32 × 176400
binary32), and retained selected-sound capture bytes for partial and full
selection. Measured costs are reported separately from derived arithmetic
projections such as a 96-case storage volume; no projected number is a
measurement.

## Publication and CI

A qualified run writes `sim/reference/trace-capture.json` (identities, case
outcomes, per-trace hashes, endpoint checks, branch evidence, controls, costs,
command, host and git provenance, warnings, limitations). The publication is
hash-level; raw audio/traces stay under ignored `out/`. Inspection of the
committed record is not a fresh numerical run.

CI (`.github/workflows/trace-capture.yml`) runs the stdlib contract tests and
`tools/qualify_trace_capture.py --check-inputs`, which re-verifies the
preregistered input pins, selection plan, directed case existence and negative
controls on ordinary hosts without Torch or Docker.
`tools/qualify_trace_capture.py --check-publication` strictly validates a
committed publication against the current registry identity and fails when the
evidence is stale or absent; absence is reported as absent, never as a pass.

Reproduce the qualified publication:

```sh
python3 tools/qualify_trace_capture.py --output out/trace-capture
python3 -m unittest discover -s tests -p test_trace_capture.py -v
python3 tools/qualify_trace_capture.py --check-inputs
python3 tools/qualify_trace_capture.py --check-publication
```

## Boundaries

This capture observes the default Voice graph only; it establishes no scalar
substitution, mutation, corpus/holdout render, independent-model DSP, RTL,
synthesis, layout, signoff, playback or sound-fidelity claim. Analytic range
metadata is not measured activation coverage. Registry evolution publishes a
new explicit version; consumers must not invent a parallel name registry.
