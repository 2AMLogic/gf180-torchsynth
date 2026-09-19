# Voice trace registry v1

## Capture seam decisions, recorded before implementation

The selected Voice graph and arithmetic are unchanged. This registry describes
passive observations; it does not make these seams writable mutation points.
The 32 planned names in directed-coverage-v1 become canonical names. The final
Voice return aliases `mixer.output`; no second tensor is invented for
`audio.final`. Input normalized parameters, actual physical parameters and
selected noise are separate input checkpoints. Scalar probe aliases remain
historical observations, not production capture evidence.

Observe the original `normalize_if_clipping` call input for
`mixer.pre_normalization`, and its return-frame `max_sample` for `mixer.peak`.
Never reconstruct the weighted mix or rerun its peak reduction. `mixer.gain`
is an explicitly derived binary32 diagnostic: one when the observed peak is at
most one, otherwise its reciprocal. Upstream divides by the peak; this
diagnostic is never used to generate audio. Peak location and normalization
decision are not additional registry traces in v1.

Shared modules are identified by ordered call occurrence **and original input
object identity**. The five upsamplers interleave with raw source generation:
pitch upsample, raw VCO, amplitude upsample, VCA for each oscillator, then raw
noise, amplitude upsample, VCA. Observe all six ADSRs including `adsr_1` and
`adsr_2` directly. Missing, extra, reordered or incorrectly associated calls
refuse the prototype rather than guessing a label.

Scalar facts have no sample rate or time grid. Control sample i is labelled
i/441 and audio sample i is labelled i/44100, both beginning at trigger zero.
Oscillator index zero already includes its first cumulative phase increment;
grid labels do not imply an initial-phase sample. Endpoint-aligned upsampling
maps audio index i to control coordinate i*1763/176399: the final audio point
176399/44100 seconds maps to control point 1763/441 seconds. It is not an
integer factor-of-100 time mapping.

This is naming/observation design under the existing contract, with no changed
target, arithmetic, noise, normalization or timing policy. DR-0006 permits only
its measured release-mkl-compatible-v1 host/profile for canonical evidence.
DR-0007 scalar execution remains diagnostic. Production selective capture and
resource qualification belong to #23; artifact trace storage belongs to #24.

## Registry and consumer API

The machine-readable contract is [trace-registry-v1.json](reference/trace-registry-v1.json),
with the closed [JSON Schema](schemas/trace-registry-v1.schema.json). Each trace
states producer occurrence/output position, consumers, meaning, units, analytic
bounds, observation versus derivation, shape, dtype/encoding, rate, sample count,
time grid, interpolation and clamp/VCA/normalization boundary. `B` is the
supported reproducible batch width; the prototype uses 32. Keyboard scalars
select to shape `[]`; the peak reduction's retained dimension selects to `[1]`.
Both are scalar facts with `sample_count`, `rate_hz` and `time` explicitly null.
All sampled traces select to a single mono vector, with no added channel axis.

Bounds are source-parameter limits or analytical conservative bounds for valid
finite inputs, not empirically measured activation or a numerical tolerance.
The square/saw analytical bound is conservatively 1.25, each modulation column
at most 4, and the weighted mixed bound 13. Internal LFO nonnegative-frequency
and VCO MIDI clamps precede raw waveforms; the observed upsampled pitch still
precedes VCO depth/tuning/clamp. Zero waveform-weight denominators remain
invalid upstream inputs, not a request to repair or normalize a patch.

```python
from torchsynth_voice.trace_registry import load_registry, registry_token, requested_names

registry = load_registry()
token = registry_token()
names = requested_names(registry, ["mixer.output", "adsr_1.output"])
# names == ["adsr_1.output", "mixer.output"]
```

The externally computed token is `tr1-` followed by lowercase SHA-256 of the
ASCII domain `torchsynth-trace-registry-v1\n` (one LF), then the **exact registry
file bytes**, including its final newline. The registry includes its semantic
version, schema version, and exact schema-file SHA-256. The token itself stays
outside the registry, avoiding a self-hash cycle. Formatting changes therefore
change identity. Use this token in render-v1's existing `trace_registry_version`
field and `requested_names`' unique graph-ordered subset in `requested_traces`.
Unknown names and duplicate requests are rejected; empty requests remain empty.
Neither helper rewrites existing artifact IDs. Registry evolution publishes a
new explicit version; consumers must not invent a parallel name registry.

`probe-aliases-v1` explicitly maps the scalar probe's 36 observations and the
repeatability probe's eleven artifact names. Three scalar observations are
input checkpoints, and `audio.final` aliases `mixer.output`. The scalar probe's
historical `[1]` encoding of keyboard facts maps to the registry's original
rank-zero per-sound selection; this is an explicit representation mapping,
not permission to change historical files. Physical conversion remains an
early comparison checkpoint; actual observed maps must not be replaced with
another execution's values. Input exactness is normalized parameter bytes and
selected noise, with physical conversion differences reported before envelopes.

The stdlib validator checks the schema and graph invariants without importing
Torch or installing the root package. The Python 3.9 worker imports its file
directly. `CallTracker` validates original argument-object identity as well as
event order and output arity, including numerically equal but different inputs.
`validate_capture` checks complete ordered capture descriptors; it does not
authenticate a producer or check raw bytes it has not received. Future #23
owns selective capture/resource limits; #24 owns storage and scalar descriptor
integration, and #39 consumes observed or justified-derived checkpoints.

## Reproduce the bounded prototype

```sh
python3 -m unittest discover -s tests -p test_trace_registry.py -v
python3 tools/probe_trace_registry.py --output out/trace-registry-prototype
```

Choose a new output directory for every run; previous measurements are never
overwritten. The host runner rebuilds the unchanged release Dockerfile/lock,
launches offline Linux/amd64 with one CPU and 6 GiB memory, declares all threads
one, sets `MKL_CBWR=COMPATIBLE` and unsets `ATEN_CPU_CAPABILITY` before Python.
It refuses hosts outside DR-0006's measured Apple M5/macOS 26.5.1 and Docker
29.7.2 Linux/arm64 scope. The worker compares complete package/Torch-build/CPU
feature identity with the committed release measurement. CPU MHz and bogomips
are retained but excluded from identity comparison as dynamic clock readings.
No lock/source patch, root package installation, scalar substitution or host
auto-selection occurs.

Two independently constructed Voices render the same global-0/batch-32 draw,
first unhooked, then with passive hooks and original normalization call/return
observers. The worker requires complete batch audio bytes, all 78 name-keyed
normalized and actual-physical parameter bytes, and selected noise bytes to
agree, and parameter/noise state to remain unchanged. It snapshots all 32
selected traces, checks both main ADSRs against committed sentinel hashes,
compares all eleven existing sentinel artifacts, and directly checks all five
upsampler endpoints. Negative registry mutations must fail with their retained
reasons. Offline call-tracker controls reject wrong, swapped, missing and extra
invocations independently of waveform values.

Raw audio/traces and build/worker logs remain under ignored `out/`. The bounded
publication in [trace-registry-prototype.json](../sim/reference/trace-registry-prototype.json)
retains identities, name-keyed parameter values and byte encodings, all trace
hashes/descriptors, before/after comparisons, command, warnings and controls.
The host separately checks raw-file hashes after the worker exits. Inspection
of the committed record is not a fresh numerical run; rerun the command for
fresh evidence. This single-case experiment does not qualify corpus capture,
scalar equivalence, independent-model DSP, RTL or hardware.
