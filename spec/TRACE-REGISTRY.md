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

## Recorded prototype, 2026-09-19

The final run used clean implementation commit
`d02c8e23daf548d0a5a7928e737f8f6658d5cd9d` and the command above with
`--output out/trace-registry-prototype-final`. All 32 captures, both main ADSRs,
eleven prior sentinel artifacts, five upsample endpoint pairs, and the complete
captured/uncaptured batch-audio and named-parameter byte comparisons passed.
All five structural negative controls were rejected with their exact reasons.
Python warnings, Docker stderr and build-warning lists were empty.

The selected final-audio SHA-256 is
`acb333168ddc547d800ea4e495f8130b557b613f198426387fd92614345d0463`;
the complete 32-row audio SHA-256 is
`6afe0dc58bf076e0f136333c7b7ca7552726ccd26888f93bee03a53a760e2506`.
The publication SHA-256 is
`b074579f66bcebb879621d59464cd4abd366fb61b1a368df265b7729f1af29ad`.
Report-inspection tests check these retained bindings and independently compare
the named-parameter byte encodings and existing sentinel hashes. They do not
substitute for the executed worker. No holdout case was accessed.

## Integrated evidence audit (#7), 2026-10-07

This audit maps each original #7 acceptance criterion to the evidence from
leaves #22, #23 and #24. It does not add any new numerical measurement. It
changes no DSP, registry, runtime admission or publication. Three kinds of
evidence are kept separate:

- **[U] unit/schema check**: stdlib-only tests or tool check modes, executed
  fresh for this audit on fakes, documents or committed bytes.
- **[R] retained-publication inspection**: reading the committed records.
  This is historical evidence from the qualified runs. It is not a fresh run.
- **[F] fresh numerical measurement**: a new qualified-runtime render. None
  was performed (see "Fresh measurements").

Statuses are **established**, **missing** or **unrun**.

### Audit identity

- Audit base: `origin/main` at `e29a8a946f564cbea4b0c318f0a86af1e3baa0e0`,
  branch `feature/issue-7`.
- Audit host: Linux x86_64 shared dispatch worker, interpreter
  `/usr/bin/python3` (Python 3.12.3), no Torch, no Docker run. This host is
  outside DR-0006's measured scope.
- Registry `spec/reference/trace-registry-v1.json`: SHA-256
  `6fd72ca97edda4ea9ede968208aeed69badc2b4f642296123cb12fe1a338d34c`,
  recomputed token
  `tr1-b89a589e184c08a9902925cd699adc593c1f6514973395e1724415a345f13259`.
  Schema SHA-256 `ca95f8bd36f42cc619deb35775185946739aef9c6cfa4fa1ba00119fbb987525`.
  32 unique names: 15 control-rate (441 Hz), 13 audio-rate (44,100 Hz), and
  4 scalar facts with a null rate.

| Publication | SHA-256 (committed bytes) | Producer commit (clean) | Image | Leaf / landing commit |
| --- | --- | --- | --- | --- |
| `sim/reference/trace-registry-prototype.json` | `b074579f66bcebb879621d59464cd4abd366fb61b1a368df265b7729f1af29ad` | `d02c8e23daf548d0a5a7928e737f8f6658d5cd9d` | `sha256:ae1d9940…` | #22 / `c53f464` |
| `sim/reference/trace-capture.json` | `b803f015a63f723c95a20c287027a5168d0bf6df0f7bcf95f6fa817b88ccb56d` | `73b1ef73fc8dd0c79d7f31e5800fd8d615b2a1b8` | `sha256:d8cbaacf…` | #23 / `18c1e43` |
| `sim/reference/trace-artifact-smoke.json` | `0a17a7e50d634894802bc75e7d7e5bd269ce16e37f446427b478f25cfde0022f` | `9226d0220806ec0c87059c6e38fcf4ee54d25bd5` | `sha256:b601c371…` | #24 / `752f79d` |

Each publication has been changed by exactly one commit, which landed it. All
three were produced on the DR-0006 host (Apple M5, macOS 26.5.1, Docker server
linux/arm64 29.7.2) under `release-mkl-compatible-v1` (shared runtime SHA-256
`560616912a12…` in #22 and #23). Each records empty warnings.

### Commands executed for this audit

All commands ran from the worktree root on the audit host above.

| Command | Kind | Exit | Actual result |
| --- | --- | --- | --- |
| `python3 -m unittest discover -s tests -p test_trace_registry.py -v` | U | 0 | 12 tests OK, 0 skipped |
| `python3 -m unittest discover -s tests -p test_trace_capture.py -v` | U | 0 | 38 tests OK, 0 skipped (fake Torch/Voice double; not runtime evidence) |
| `python3 -m unittest discover -s tests -p test_trace_artifacts.py -v` | U | 0 | 27 tests OK, 0 skipped (synthetic store/provider) |
| `python3 -m unittest discover -s tests -p test_trace_capture_provider.py` | U | 0 | 10 tests OK |
| `python3 -m unittest tests.test_artifact_renderer.RendererTests.test_worker_source_gate_precedes_numerical_import` | U | 0 | 1 test OK |
| `python3 -m unittest discover -s tests -p test_reference_consolidation.py` / `python3 tools/check_reference_consolidation.py` | U | 0 / 0 | 35 tests OK; 9 consolidation checks passed |
| `python3 tools/qualify_trace_capture.py --check-inputs` | U | 0 | `PASS`: 5 cases, 4-trace subset, 11 negative controls, 5 endpoint pairs |
| `python3 tools/qualify_trace_capture.py --check-publication` | U+R | 0 | `PASS` against the current registry token, 5 cases |
| `python3 tools/qualify_trace_artifacts.py --check-inputs` | U | 0 | 29-trace selection, `selection_sha256` `6d6b822a…5e89`, 3 excluded seams, cases 0/1, 5 negative controls |
| `python3 tools/qualify_trace_artifacts.py --check-publication` | U+R | 0 | Document-level validation passed. **The store-dependent payload rehash did not run**: no retained raw store exists on this host |
| `python3 tools/qualify_trace_capture.py --output <fresh /tmp dir>` | F gate | 1 | Refused `unqualified host` before any build or write: **unrun** |
| `python3 tools/qualify_trace_artifacts.py --store <fresh /tmp dir> --receipt <fresh /tmp path>` | F gate | 1 | Refused at host admission (`sysctl machdep.cpu.brand_string` absent on Linux; uncaught `CalledProcessError`, not a clean message) before any store write: **unrun** |

The cross-leaf binding check was a read-only stdlib script run outside the
repository against the three publications and the registry ([R]). It found:

- **Registry binding.** Registry SHA-256 and token agree in all three
  publications. The #22 and #23 source pins (12 upstream files) and runtime
  identity are equal.
- **Trace order and descriptors.** The #22 inventory and #23 `global-0`
  full inventory both equal registry order. Their 32 per-trace SHA-256 values
  agree. For every #23 case, rate and boundary agree with the registry.
- **Artifact selection.** The 29-trace selection is the registry-ordered
  complement of the three normalization seams. Each #24 `global-0` bundle
  `capture_sha256` equals the #23 `global-0` capture hash, and equals its
  stored-file SHA-256.
- **Audio.** `global-0` selected audio is
  `acb333168ddc547d800ea4e495f8130b557b613f198426387fd92614345d0463` in the
  #22 uncaptured and captured runs, in all three #23 modes, and in both #24
  variants. The #24 `global-1` traced and audio-only variants are also
  byte-identical.
- **Storage arithmetic.** The 32-trace per-case bytes (9,278,656) equal the
  29-trace bytes (8,573,048) plus `mixer.pre_normalization` (705,600) and two
  4-byte scalars.

### Criterion-by-criterion results

**1. Enabling traces leaves final audio and parameter hashes byte-identical
to an untraced render: established (bounded scope).**

- [R] #22, `global-0` in batch 32: captured and uncaptured runs have equal
  selected-audio and 32-row batch-audio hashes, equal noise
  `dbc4c21b…`, `parameter_bytes_unchanged` for all 78 name-keyed parameters,
  and `passive_capture_equal_bytes: true`.
- [R] #23, five cases × {uncaptured, partial, full}: each case has
  identical selected-audio, batch-audio and noise hashes across modes, and
  `render_drew_randoms: false`. The worker raises on any mutation of named
  parameters or noise (`env/release-era/capture_traces.py`, `render_mode`).
  `global-0` also passes the injected-failure cleanup and conflicting-profiler
  controls.
- [R] #24: traced and audio-only variants of `global-0` and `global-1` have
  distinct `ra1-` IDs and byte-identical audio.
- [U] `test_full_capture_validates_and_matches_untraced_bytes` and the
  #24 variant-linkage tests pass on fakes.

Limit: this holds within the one measured runtime and for 6 distinct
development or directed identities. It is not a corpus-wide claim.

**2. Every required trace has a stable unique name, rate, expected shape,
dtype and semantic description: established.**

- [U] The registry schema, uniqueness and alias tests pass
  (`test_complete_directed_names_and_aliases`,
  `test_content_identity_binds_registry_and_schema`). Every registry entry
  carries `name`, `producer`, `consumers`, `meaning`, `unit`, `range`,
  `rate_hz`, `sample_count`, `shape`, `dtype`, `encoding`, `boundary`,
  `time` and `interpolation`.
- The 32 names cover every required-work group: keyboard pitch and duration;
  six envelopes; raw and post-control-VCA LFOs; five matrix outputs; five
  upsamplers; raw sine, square/saw and noise; three post-VCA paths;
  pre-normalization mix; peak; derived gain; and output.
- The normalization *decision* is not a separate trace in v1. It is
  determined by the observed `mixer.peak` (strict `peak > 1`) and the derived
  `mixer.gain`. Final audio is the `mixer.output` alias (see the capture-seam
  decisions above).

**3. Control/audio-rate boundaries and endpoint samples are explicit:
established.**

- [U] `test_scalar_and_endpoint_metadata`, and the #23 endpoint tests
  (five routes, loss, truncation, padding, wrong rate not repaired).
- [R] All five upsampler endpoint pairs pass in #22 and in every #23 case:
  1,764 inputs to 176,400 outputs, first and last samples equal.
- The registry states `time` (`i/441`, `i/44100`) and the endpoint-aligned
  `i*1763/176399` mapping.

**4. Trace count/shape mismatches fail the artifact, not just warn:
established.**

- [U] Missing, wrong-shape, truncated, padded, extra, corrupt and
  non-canonical captures or payloads raise. The relevant tests are
  `test_observed_inventory_rejects_missing_and_wrong_shape`,
  `test_wrong_shape_rejected`, `test_truncated_trace_not_repaired`,
  `test_corrupt_truncated_extra_and_missing_payloads_refused`,
  `test_bundle_requires_requested_registry_subset` and
  `test_coverage_omission_and_stale_references_rejected`.
- [R] The retained `wrong-shape`, `wrong-capture-shape`, `missing-trace`
  and `missing-capture` controls are recorded `rejected` with reasons.

**5. At least one directed fixture demonstrates each trace path: missing.**

- [R] Three executed directed fixtures (`normalization:above`,
  `normalization:below`, `normalization:tie`) captured all 32 traces. The
  capture passed descriptor validation, the endpoint checks and the
  original-branch normalization check. `peak > 1` was observed only for
  `above`.
- This demonstrates the capture path for every trace. It does not
  demonstrate each signal path: those patches use a flat-envelope sine path,
  and no activation fact is recorded.
  [DIRECTED-PHASE-REVIEW.md](DIRECTED-PHASE-REVIEW.md) classifies the trace
  criterion `SATISFIED-AS-PREPARATION`, and the per-trace capture cases in
  `directed-coverage-v1` remain `planned`.
- Bounded follow-up: #286.

**6. Tests catch swapped trace names, a missing trace, rate confusion, and a
trace captured after unintended clamping: established.**

- [U] Swapped names and calls: `test_swapped_equal_shaped_names_rejected`,
  `test_call_tracker_rejects_swapped_missing_extra_and_wrong_arguments`,
  `test_same_shaped_swapped_capture_records_rejected`.
- [U] Missing trace: `test_missing_capture_rejected`,
  `test_missing_call_rejected_at_finish`.
- [U] Rate confusion: `test_wrong_rate_rejected`,
  `test_wrong_rate_control_not_repaired`,
  `test_scalar_rate_and_sampled_confusion_rejected`.
- [U] Clamp site and boundary: `test_wrong_clamp_site_rejected`,
  `test_wrong_boundary_rejected`,
  `test_normalization_from_wrong_caller_is_the_wrong_clamp_site`.
- [R] #23 retains 11 rejected negative controls with reasons. #22 retains 5.

Limit: clamp detection is boundary-descriptor and call-association
detection. It is not a value-level detector for a tensor taken after an
internal clamp.

**7. The adapter validates upstream file hashes before installing
hooks/capture: established.**

- [R] #22 and #23 record the 12 pinned upstream file hashes and
  `source_validated_before_import: true`. That field is written by the worker
  only after `qualification.source_gate` has returned. By code inspection,
  the gate is called before `import torch` and before any observer is
  attached (`env/release-era/capture_traces.py`, `worker`;
  `tools/probe_trace_registry.py`).
- [U] The #24 traced driver runs through `render_artifact.render_selected`.
  Its gate-before-import ordering is tested by
  `test_worker_source_gate_precedes_numerical_import` and by the
  consolidation ordering check.

Hardening observation, not a criterion gap: the static
`SOURCE_GATED_WORKERS` ordering check in
`tools/check_reference_consolidation.py` does not list `capture_traces.py`
or `probe_trace_registry.py`. A future reordering in those two workers would
be caught only by review.

**8. Memory/storage cost for one case and the development corpus is
measured: missing (one case established; development corpus missing).**

- [R] One case (#23 `global-0`):
  - batch allocation 22,579,200 bytes;
  - retained capture 9,278,656 bytes for all 32 traces, 1,418,260 bytes for
    the 4-trace subset;
  - process peak RSS 582,712 / 737,164 / 737,372 KB for uncaptured / partial
    / full;
  - render about 0.94–1.04 s.

  RSS is monotonic over the process, so per-mode values depend on run order.
- [R] Stored bytes (#24, per case): 8,573,048 trace-payload bytes for the
  29-trace selection, a 17,906-byte bundle, and 705,600 audio bytes. Across
  both cases the companion is 2,648 bytes, uncompressed.
- The 96-case development corpus has only the derived projection
  `projection_96_cases_selected_bytes = 890750976`, and the publication
  labels it as not a measurement. No traced development-corpus render exists.
- Bounded follow-up: #287.

### Artifact selection versus the registry

The #24 traced driver stores **29 of 32** registry traces.
`mixer.pre_normalization`, `mixer.peak` and `mixer.gain` are excluded,
because the release worker owns the single profiler slot at the original
normalization boundary. Selecting any of those seams through that path is
refused. The worker receipt retains the pre-normalization observation digest
for the same render.

All 32 traces, including the three seams, are captured only through the #23
qualified worker, for five bounded cases. Those captures are hash-level
publications with raw bytes under ignored `out/`. They are not
content-addressed artifact traces. Nothing in this audit claims that all 32
traces are stored through the #24 artifact path.

### Fresh measurements

**[F] status: unrun.** Both producers gate fresh runs to DR-0006's Apple M5 /
macOS 26.5.1 / Docker 29.7.2 host, and both refused this Linux worker before
any build or write (table above). No other qualified host was available.

Historical retained evidence is enough for criteria 1–4, 6 and 7, within
their stated bounded scope. It is not enough for criterion 5 or for the
development-corpus half of criterion 8. Rerunning the existing producers
would not close either gap, because both need new executed scopes (#286,
#287).

The retained raw stores (`out/`) are absent here, so the #24
store-dependent payload rehash was not re-executed. The publication's own
record of a historical rehash (`every_referenced_byte_rehashed: true`) is
retained evidence, not a fresh check.

### Overall result

Criteria 1, 2, 3, 4, 6 and 7 are established in bounded scope. Criteria 5 and
8 are missing. #7 therefore stays open until #286 and #287 land.

No holdout identity was accessed. Every executed identity cited here is a
development or directed case. This audit makes no corpus-wide, holdout,
scalar, fixed-model, RTL, synthesis, layout, signoff, hardware-playback or
sound-fidelity claim.

### Re-verification at `a38ab0a`, 2026-10-07

A second #7 pass re-ran the audit checks on a newer base. It did not
regenerate evidence or add measurements, and it did not access holdout data.

- Base: `origin/main` at `a38ab0a38fb218fbf35291ada614557b6af4ec1a`, branch
  `feature/issue-7`. The tree was clean before the edit. Host and
  interpreter: Linux x86_64 shared dispatch worker, `/usr/bin/python3`
  (Python 3.12.3). `python3.11` is not installed, and no Torch or Docker
  run took place. This host is outside DR-0006's measured scope.
- Audit inputs are unchanged since the first pass.
  `git diff --quiet e29a8a9 HEAD -- sim/reference spec/reference tools tests env src`
  exited 0. The recomputed SHA-256 values of the three publications and of
  `spec/reference/trace-registry-v1.json` equal the values in the "Audit
  identity" table. Each publication still has exactly one commit on
  `origin/main` (`c53f464`, `18c1e43` and `752f79d`).

| Command | Kind | Exit | Actual result |
| --- | --- | --- | --- |
| `python3 -m unittest discover -s tests -p test_trace_registry.py` | U | 0 | 12 tests OK, 0 skipped |
| `python3 -m unittest discover -s tests -p test_trace_capture.py` | U | 0 | 38 tests OK, 0 skipped (fake Torch/Voice double; not runtime evidence) |
| `python3 -m unittest discover -s tests -p test_trace_artifacts.py` | U | 0 | 27 tests OK, 0 skipped (synthetic store/provider) |
| `python3 -m unittest discover -s tests -p test_trace_capture_provider.py` | U | 0 | 10 tests OK |
| `python3 -m unittest tests.test_artifact_renderer.RendererTests.test_worker_source_gate_precedes_numerical_import` | U | 0 | 1 test OK |
| `python3 -m unittest discover -s tests -p test_reference_consolidation.py` / `python3 tools/check_reference_consolidation.py` / `python3 tools/check_contract.py` | U | 0 / 0 / 0 | 35 tests OK; 9 consolidation checks passed; contract manifests consistent |
| `python3 tools/qualify_trace_capture.py --check-inputs` | U | 0 | `PASS`: token `tr1-b89a589e…3259`, 5 cases, 4-trace subset, 11 negative controls, 5 endpoint pairs |
| `python3 tools/qualify_trace_capture.py --check-publication` | U+R | 0 | `PASS`: same token, 5 cases |
| `python3 tools/qualify_trace_artifacts.py --check-inputs` | U | 0 | 29-trace selection, `selection_sha256` `6d6b822a…5e89`, 3 excluded normalization seams, cases 0/1, 5 negative controls |
| `python3 tools/qualify_trace_artifacts.py --check-publication` | U+R | 0 | Document-level validation passed. **The store-dependent payload rehash did not run**: `out/` is absent and no `--store` was given |
| `python3 tools/qualify_trace_capture.py --output /tmp/i7-fresh-a38ab0a/capture` | F gate | 1 | Refused `unqualified host`. Nothing was written to the output path: **unrun** |
| `python3 tools/qualify_trace_artifacts.py --store /tmp/i7-fresh-a38ab0a/store --receipt /tmp/i7-fresh-a38ab0a/receipt.json` | F gate | 1 | Uncaught `CalledProcessError` from `sysctl -n machdep.cpu.brand_string`. No store or receipt was written: **unrun** |

Result: this pass changes no status. Criteria 1, 2, 3, 4, 6 and 7 remain
established within their stated bounded scope. Criterion 5 remains
**missing** (follow-up #286). Criterion 8 remains **missing** for the
development corpus (follow-up #287). Fresh qualified-runtime measurement
remains **unrun**: no DR-0006 host was available. Rerunning the existing
producers would not close #286 or #287 in any case. Both follow-ups were
still open (`loom:triage`) when this pass ran, so #7 stays open.

The hardening observation under criterion 7 still holds:
`SOURCE_GATED_WORKERS` in `tools/check_reference_consolidation.py` lists
only `probe.py`, `qualify_repeatability.py`, `qualify_scalar.py` and
`render_artifact.py`. It is not a criterion gap, and this pass did not file
it.

### Re-verification at `5400ec2`, 2026-10-07

A third #7 pass re-ran the checks after #286 landed. It did not regenerate
evidence, add measurements, change DSP or runtime admission, or access holdout
data.

- Base: `origin/main` at `5400ec2b88bbf6da8c4473a593f8475e80651794`, branch
  `feature/issue-7`, clean tree. Host and interpreter: Linux x86_64 shared
  dispatch worker, `/usr/bin/python3` (Python 3.12.3). `python3.11` is not
  installed (limitation), and no Torch or Docker run took place. This host is
  outside DR-0006's measured scope.
- Inputs since `a38ab0a`: the only change under `sim/reference/trace-*.json`,
  `spec/reference/trace-registry-v1.json`, `src/torchsynth_voice/trace_*.py`,
  `tools/qualify_trace_*.py`, `tests/test_trace_*.py` and `env/` is the new
  #286 worker `env/release-era/capture_directed_trace_paths.py`. The registry
  and the three publications hash exactly as in the "Audit identity" table
  (registry `6fd72ca9…d34c`; prototype `b074579f…29ad`; capture `b803f015…b56d`;
  artifact smoke `0a17a7e5…022f`).

| Command | Kind | Exit | Actual result |
| --- | --- | --- | --- |
| `python3 -m unittest discover -s tests -p test_trace_registry.py` | U | 0 | 12 tests OK |
| `python3 -m unittest discover -s tests -p test_trace_capture.py` | U | 0 | 38 tests OK (fake Torch/Voice double; not runtime evidence) |
| `python3 -m unittest discover -s tests -p test_trace_artifacts.py` | U | 0 | 27 tests OK (synthetic store/provider) |
| `python3 tools/qualify_trace_capture.py --check-inputs` | U | 0 | `PASS`: token `tr1-b89a589e…3259`, 5 cases, 11 negative controls, 5 endpoint pairs |
| `python3 tools/qualify_trace_capture.py --check-publication` | U+R | 0 | `PASS`: same token, 5 cases |
| `python3 tools/qualify_trace_artifacts.py --check-inputs` | U | 0 | 29-trace selection, `selection_sha256` `6d6b822a…5e89`, 3 excluded normalization seams, cases 0/1, 5 negative controls |
| `python3 tools/qualify_trace_artifacts.py --check-publication` | U+R | 0 | Document-level validation passed. **The store-dependent payload rehash did not run**: no `--store` and `out/` absent |
| `python3 tools/qualify_trace_capture.py --output /tmp/i7-5400ec2/capture` | F gate | 1 | Refused `unqualified host`: **unrun** |
| `python3 tools/qualify_trace_artifacts.py --store /tmp/i7-5400ec2/store --receipt /tmp/i7-5400ec2/r.json` | F gate | 1 | Uncaught `CalledProcessError` from `sysctl -n machdep.cpu.brand_string`; nothing written: **unrun** |

Result: no status changes. Criteria 1, 2, 3, 4, 6 and 7 remain established
within their stated bounded scope (retained publications plus fresh
unit/schema checks; no fresh numerical measurement). Criterion 5 remains
**missing**: #286 landed the plan and verifier, but its receipt is `UNRUN`
(see below), so no per-path demonstration exists. Criterion 8 remains
**missing** for the development corpus (#287, open); only the two-case
measurement and the derived 96-case projection exist. The 29-trace artifact
selection versus 32-trace registry boundary is unchanged: the three
normalization seams are captured only by the #23 hash-level worker. #7 stays
open.

### Re-verification at `a4d7a3f`, 2026-10-07

A fourth #7 pass re-ran the checks on the current base. It did not regenerate
evidence, add measurements, change DSP or runtime admission, or access holdout
data.

- Base: `origin/main` at `a4d7a3f0b9f9c76a6e2dd7959bc2c474ec399b84`, branch
  `feature/issue-7`, clean tree. Host and interpreter: Linux x86_64 shared
  dispatch worker, `/usr/bin/python3` (Python 3.12.3). `python3.11` is not
  installed (limitation); no Torch or Docker run took place. This host is
  outside DR-0006's measured scope.
- Inputs since `5400ec2`: `git diff --quiet 5400ec2 HEAD -- sim/reference
  spec/reference tools tests env src` exited 0, so no audit input changed. The
  registry and three publications hash as in the "Audit identity" table
  (registry `6fd72ca9…d34c`; prototype `b074579f…29ad`; capture `b803f015…b56d`;
  artifact smoke `0a17a7e5…022f`).

| Command | Kind | Exit | Actual result |
| --- | --- | --- | --- |
| `python3 -m unittest discover -s tests -p test_trace_registry.py` | U | 0 | 12 tests OK |
| `python3 -m unittest discover -s tests -p test_trace_capture.py` | U | 0 | 38 tests OK (fake Torch/Voice double; not runtime evidence) |
| `python3 -m unittest discover -s tests -p test_trace_artifacts.py` | U | 0 | 27 tests OK (synthetic store/provider) |
| `python3 tools/qualify_trace_capture.py --check-inputs` | U | 0 | `PASS`: 5 cases, 11 negative controls, 5 endpoint pairs |
| `python3 tools/qualify_trace_capture.py --check-publication` | U+R | 0 | `PASS`: token `tr1-b89a589e…3259`, 5 cases |
| `python3 tools/qualify_trace_artifacts.py --check-inputs` | U | 0 | 29-trace selection, 3 excluded normalization seams, 5 negative controls |
| `python3 tools/qualify_trace_artifacts.py --check-publication` | U+R | 0 | Document-level validation passed. **The store-dependent payload rehash did not run**: no `--store`, `out/` absent |
| `python3 tools/qualify_trace_capture.py --output /tmp/i7-a4d7a3f/capture` | F gate | 1 | Refused `unqualified host`: **unrun** |
| `python3 tools/qualify_trace_artifacts.py --store /tmp/i7-a4d7a3f/store --receipt /tmp/i7-a4d7a3f/r.json` | F gate | 1 | Uncaught `CalledProcessError` from `sysctl -n machdep.cpu.brand_string`; nothing written: **unrun** |

Result: no status changes. Criteria 1, 2, 3, 4, 6 and 7 remain established
within their stated bounded scope (retained publications plus fresh
unit/schema checks; no fresh numerical measurement). Criterion 5 remains
**missing** (#286 receipt `UNRUN`). Criterion 8 remains **missing** for the
development corpus (#287); only the two-case measurement and its derived
projection exist. The artifact driver selects 29 of the 32 registry traces;
the three normalization seams are captured only by the #23 hash-level worker,
so not all traces are captured through the artifact path. #7 stays open.

### Re-verification at `4356def`, 2026-10-07

A fifth #7 pass re-ran the checks after #307 (the #287 cost instrument) changed
one audit input. It did not regenerate evidence, add measurements, change DSP or
runtime admission, or access holdout data.

- Base: `origin/main` at `4356def`, branch `feature/issue-7`, clean tree. Host
  and interpreter: Linux x86_64 shared dispatch worker, `/usr/bin/python3`
  (Python 3.12.3). `python3.11` is not installed (limitation); no Torch or
  Docker run took place. This host is outside DR-0006's measured scope.
- Inputs since `a4d7a3f`: `git diff --stat a4d7a3f HEAD -- sim/reference
  spec/reference tools tests env src` is not empty (`git diff --quiet` exited
  1), so the earlier "inputs unchanged" premise no longer holds. The delta is
  five paths: new #287 files `sim/reference/development-trace-cost-v1.json`,
  `src/torchsynth_voice/development_trace_cost.py`,
  `tests/test_development_trace_cost.py` and
  `tools/measure_development_trace_cost.py`, plus a modification of
  `tools/qualify_trace_artifacts.py` (+20/-1). The registry
  `spec/reference/trace-registry-v1.json` and the three trace publications are
  byte-identical to `a4d7a3f` (`git diff --quiet` exited 0 on those paths) and
  hash as in the "Audit identity" table (registry `6fd72ca9…d34c`; prototype
  `b074579f…29ad`; capture `b803f015…b56d`; artifact smoke `0a17a7e5…022f`).
- Assessment of the `tools/qualify_trace_artifacts.py` change, by code
  inspection only (not a fresh numerical measurement): it adds a `time` import,
  class attribute `driver_source = DRIVER_SOURCE`, a `collect_telemetry` hook
  returning `None`, writes `self.driver_source` instead of `DRIVER_SOURCE`, times
  the `docker run` call, and sets `receipt["worker_telemetry"]` only when the
  hook returns non-`None`. With the defaults the driver bytes, docker command and
  receipt fields are the same as before, so the published two-case smoke
  evidence is not affected by default. It was not re-executed, because the
  qualified runtime is unavailable here. The four #287 additions are new
  evidence instruments whose receipt is `UNRUN`; their unit tests were not part
  of this pass.

| Command | Kind | Exit | Actual result |
| --- | --- | --- | --- |
| `python3 -m unittest discover -s tests -p test_trace_registry.py` | U | 0 | 12 tests OK |
| `python3 -m unittest discover -s tests -p test_trace_capture.py` | U | 0 | 38 tests OK (fake Torch/Voice double; not runtime evidence) |
| `python3 -m unittest discover -s tests -p test_trace_artifacts.py` | U | 0 | 27 tests OK (synthetic store/provider) |
| `python3 -m compileall -q` on the `trace-artifacts.yml` CI targets | U | 0 | No syntax errors in the modified tool |
| `python3 tools/qualify_trace_capture.py --check-inputs` | U | 0 | `PASS`: 5 cases, 11 negative controls, 5 endpoint pairs |
| `python3 tools/qualify_trace_capture.py --check-publication` | U+R | 0 | `PASS`: token `tr1-b89a589e…3259`, 5 cases |
| `python3 tools/qualify_trace_artifacts.py --check-inputs` | U | 0 | Registry/selection recorded, 2 cases, 5 negative controls |
| `python3 tools/qualify_trace_artifacts.py --check-publication` | U+R | 0 | Document-level validation passed. **The store-dependent payload rehash did not run**: no `--store`, `out/` absent |
| `python3 tools/qualify_trace_capture.py --output /tmp/i7-4356def/capture` | F gate | 1 | Refused `unqualified host`: **unrun** |
| `python3 tools/qualify_trace_artifacts.py --store /tmp/i7-4356def/store --receipt /tmp/i7-4356def/r.json` | F gate | 1 | Uncaught `CalledProcessError` from `sysctl -n machdep.cpu.brand_string`; nothing written: **unrun** |

No repository markdown or doc-consistency check exists in `.github/workflows`
or `tools`, so none was run.

Result: no status changes. Criteria 1, 2, 3, 4, 6 and 7 remain established
within their stated bounded scope (retained publications plus fresh
unit/schema checks; no fresh numerical measurement). Criterion 5 remains
**missing** (#286 receipt `UNRUN`). Criterion 8 remains **missing** for the
development corpus (#287 receipt `UNRUN`); only the two-case measurement and
its derived projection exist. The artifact driver selects 29 of the 32 registry
traces; the three normalization seams are captured only by the #23 hash-level
worker, so not all traces are captured through the artifact path. #7 stays
open.

## Directed per-path capture (#286): plan, verifier and unrun receipt

The #7 criterion "at least one directed fixture demonstrates each trace path"
stays **missing**. #286 adds the instrument for measuring it and records that
the measurement has **not been run**.

- Plan: `src/torchsynth_voice/directed_trace_paths.py` derives a deterministic
  trace-to-case plan from `spec/reference/directed-coverage-v1.json`
  `traces[*].cases` (40 rows, 21 deduplicated cases, all 32 registry names).
  Envelope outputs use the first of `cut-attack`, `cut-decay`, `long-release`,
  `zero-stages` (a zero-stage patch deliberately deactivates its envelope); the
  four normalization seams take all three listed `normalization:*` cases; every
  other trace takes its first listed case. Each row carries the isolation
  limitation declared in the coverage report.
- Expectations are fixed by the plan before execution: time-series paths must be
  finite, non-zero and non-constant; keyboard scalars must be finite, non-zero
  and inside the registry range (not a non-constant assertion); the seams
  `mixer.pre_normalization`, `mixer.peak`, `mixer.gain`, `mixer.output` must
  satisfy the declared strict `peak > 1` relation, target peak, gain rule and
  output rule of their case.
- Worker: `env/release-era/capture_directed_trace_paths.py` (reuses the #23
  worker's gates, Harness and production `TraceCapture`; no DSP, registry or
  admission change) renders each planned case uncaptured and captured, requires
  audio/parameter/noise/RNG byte identity, and retains raw payloads in a fresh
  directory. Statistics are never taken from the worker.
- Runner/verifier: `tools/qualify_directed_trace_paths.py`
  (`--check-inputs`, `--check-receipt [--raw DIR]`). A measured receipt is only
  verified by rehashing the retained payloads and recomputing every measurement
  and activation check; a receipt alone cannot establish raw integrity.
- Receipt: `sim/reference/directed-trace-paths-v1.json` is currently
  **`UNRUN`**: the run host (Linux x86_64 shared dispatch worker, no Torch, no
  Docker run) is outside DR-0006's measured scope, and the launcher's Apple-host
  gate was not changed. The sanctioned AWS box was not used and is not presumed
  admitted by that gate. All 40 rows are `unrun`; `complete_path_coverage` is
  false; `--check-receipt` exits 2 for it.
- Unchanged: the existing publications (`trace-capture.json`,
  `trace-registry-prototype.json`, `trace-artifact-smoke.json`), the registry,
  runtime admission, the pinned TorchSynth commit
  `2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`. No holdout was accessed. Synthetic
  verifier tests (`tests/test_directed_trace_paths.py`) are not runtime
  evidence. The criterion becomes measured only when a run on an admitted host
  replaces the `UNRUN` receipt with a verified `PASS` and retained raw bytes.

## Development-corpus trace cost (#287): instrument and unrun receipt

The development-corpus half of #7 criterion 8 stays **missing**. #287 adds
the instrument for measuring it and records that the measurement has **not
been run**.

- **Instrument.** `tools/measure_development_trace_cost.py` is the runner.
  `src/torchsynth_voice/development_trace_cost.py` is the stdlib accounting
  and verifier. [DEVELOPMENT-TRACE-COST.md](DEVELOPMENT-TRACE-COST.md) is the
  record.
  - The plan is frozen before admission: identities 0–95, the 29-trace
    production selection, the registry, manifest, producer and runtime. The
    gate is the unchanged `host_admission()`.
  - Rendering goes through the #24 traced path with a telemetry driver.
    `ru_maxrss` is recorded in KiB, with monotonic boundaries.
  - Storage is recomputed from the verified store. Every file is classified
    once, and the shared companion is counted once.
  - The three normalization seams are receipt-retained.
- **Receipt.** `sim/reference/development-trace-cost-v1.json` is **`UNRUN`**.
  - The run host was a shared Linux x86_64 dispatch worker.
    `host_admission()` refused it with `CalledProcessError` from `sysctl`
    before any store, Docker or render access.
  - All 96 rows are `unrun`, and `--verify-receipt` exits 2.
  - The sanctioned AWS box was not used. The gate would refuse it too,
    because it is not the DR-0006 Apple M5 host.
- **Unchanged.** DSP, the registry, runtime admission, the pinned TorchSynth
  commit `2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`, and the existing
  publications (`trace-capture.json`, `trace-artifact-smoke.json`,
  `development-corpus-first.json`). No holdout identity was accessed.
- **Not evidence.** The synthetic tests (`tests/test_development_trace_cost.py`)
  and the labelled projections are not measurements.
- **What completes the criterion.** It becomes measured only when an
  admitted-host campaign replaces the `UNRUN` receipt with a `complete`
  receipt. That receipt must verify `VERIFIED` against the retained raw
  store.
