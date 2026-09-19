# Companion trace artifacts v1

## Scope and ownership

This is the sole integration layer between #23's validated passive Voice
capture, #22's versioned trace registry, and the landed v1 artifact/store/
corpus producers (#14/#15/#105). It stores trace tensors and registry
identity alongside render artifacts without making non-traced audio
identities ambiguous, and adds trace hashing, verification-only recheck, and
corpus index linkage. It does not fork or edit any owner module: the landed
`artifacts.py`, `storage.py`, `artifact_renderer.py`, `corpus.py`,
`trace_capture.py`, `trace_registry.py` and their specs are consumed exactly
as merged (zero-diff is a review gate of this issue).

Owned files: `src/torchsynth_voice/trace_artifacts.py`,
`tests/test_trace_artifacts.py`, `tools/qualify_trace_artifacts.py`, this
document, `spec/schemas/trace-bundle-v1.schema.json` (both companion forms),
the bounded publication `sim/reference/trace-artifact-smoke.json`, and
`.github/workflows/trace-artifacts.yml`.

The normative TorchSynth source remains
`2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`; the profile remains one
four-second, 44.1 kHz, default-nebula clip. Immutable render-artifact v1 is
unchanged; the `ra1-` input-identity contract is untouched.

## Identity binding

Render requests carry the trace identity before content identity exists.
`trace_registry_version` receives the content-qualified registry token
(`tr1-` + SHA-256 over the ASCII domain `torchsynth-trace-registry-v1\n` and
the exact registry file bytes), not a mutable friendly label; the token is
computed externally to the hashed document, avoiding a self-hash cycle. The
requested set is the unique graph-ordered subset produced by the approved
registry (`requested_names`); unknown, duplicate and reordered requests are
rejected before any identity is constructed. Changed registry bytes or a
changed requested set therefore change the artifact identity; existing IDs
are never reinterpreted or rewritten. Audio-only requests keep
`trace_registry_version: "audio-only-v1"` with an empty trace set and are
explicitly a different identity from any traced render of the same patch.

## Companion trace bundle

One bundle per complete traced artifact, published at
`trace-bundles/<artifact_id>.json` — strictly **outside** the completed
artifact directories. A bundle is not a substitute artifact identity, not a
self-authenticating digest, and never an undeclared file inside the store.

Each per-name entry carries the closed v1 file reference
(`{ref, sha256, size_bytes}`, preserved exactly as the artifact metadata
declares it) plus the descriptor facts: `encoding` (`f32le`), `dtype`
(`float32`), `shape`, `classification` (`scalar`/`control`/`audio`),
`rate_hz` and `sample_count` (both null for scalar facts — no Hz is invented
for keyboard, peak or gain), `boundary`, `observation`
(`observed`/`derived`, preserving #23's classification), and
`capture_sha256`, the digest recorded by the capture session. Validation
proves the chain `capture_sha256 == artifact metadata sha256 == stored
bytes`, so a bundle binds the actual #23 capture record to the exact stored
payload. Original dtype/endian/shape/count are preserved: no casting,
aligning, padding, scaling or resampling; payload width must equal
`element_count(shape) × 4`.

Bundle validation is strict: duplicate JSON keys, non-canonical
re-serialization, malformed or escaping paths, wrong or stale registry
digest/token, stale artifact metadata digests, scalar-versus-sampled
confusion, descriptor/registry disagreement and same-shaped swapped capture
records are all rejected. Publication uses the landed store's
`publish`/`verify(id, sha256=...)`, so missing, extra, duplicate, corrupt,
aliased, nonregular and wrong-size payloads fail at publication and again at
every verification.

## Companion corpus linkage

`trace-companions/<sha256>.json` is a versioned linkage document
(`torchsynth-trace-companion-index`) that pins the **exact validated v1
corpus-index bytes** (ref + SHA-256 + size), the optional exact
audio-only variant index, and the per-case bundle references. Cases mirror
the pinned index one-to-one — same case IDs, sound indices, fixtures,
statuses and artifact references — with full coverage and no silent omission:
failed cases stay listed with null bundles, and a complete case without a
bundle fails validation. Because v1 indexes permit one case per global sound
index, audio-only and traced variants live in separate valid indexes linked
by the companion. The validator revalidates both indexes through the landed
corpus validator, requires the variants of one sound identity to agree in
fixture, profile, runtime, execution, parameters, noise and source, to
differ exactly in `trace_registry_version` and `requested_traces`, and to
carry **byte-identical audio payloads** — proving unchanged audio within the
measured runtime while the two artifacts remain distinct. New trace requests
create new artifacts; traces are never grafted onto a completed audio-only
directory.

## Publication, interruption, resume

Companion files are published through the landed `write_once` primitive:
bundles first, companion last, never replacing existing bytes and never
deleting or rewriting completed artifacts. An interrupted attempt after
artifact publication leaves an unreferenced complete object — a valid store
artifact, not a complete traced corpus — and the previous valid companion
remains intact. On resume, the landed runner re-verifies each completed
artifact (every byte rehashed) with **zero** renderer and capture calls;
companion validation independently rehashes every trace payload, both
indexes' artifacts, and both audio payloads before reuse is granted. A
published artifact whose companion publication was interrupted is exactly
the "unreferenced complete object" case and cannot become a success by
assertion: only a re-validated companion completes the corpus.

## Production traced path and the profiler boundary

The stock landed Docker backend accepts audio-only requests and refuses
providers; #24 owns its process integration in
`tools/qualify_trace_artifacts.py`. It mirrors the landed admission gates
(DR-0006 host, image, clean frozen producer checkout checked before and
after every worker call, qualified runtime identity, request digest, worker
digest) and launches the same qualified image with a Python 3.9 driver that
imports the landed worker's `render_selected` seam and attaches a
`TraceCapture` provider `(request, voice, slot)` around the single Voice
call, returning exact selected-sound trace bytes plus the validated capture
descriptors.

The release worker owns the process's single profiler slot while it observes
the original normalization boundary. #23's normalization seams
(`mixer.pre_normalization`, `mixer.peak`, `mixer.gain`) are therefore not
re-captured through this path: the preregistered traced selection is the
other 29 registry traces, and the worker's own receipt retains the
pre-normalization observation digest for the same render. Selecting any
normalization seam through the traced driver is refused, never partially
honored. No trace is reconstructed from final audio.

## Consumer mapping and refusal (case registry)

`case_registry.render_reference` verifies optional render payloads and maps
`artifact_id` to scorecard `identity`; it does not read this companion, and
this issue does not change that. Its measurement capture schema admits only
sampled f32le/f64le values with a positive `sample_rate_hz`, so scalar facts
(`keyboard.*`, `mixer.peak`, `mixer.gain` — `rate_hz` null by registry
contract) are **refused** by that consumer as a mapping matter, not repaired
or assigned invented rates here. The board does not consume bundles today; a
future consumer change would be a separately coordinated board-schema
revision. All trace descriptors are preserved here regardless, so no
information is lost by the refusal.

## Verification-only recheck

Bundle, companion and publication validation run on the standard library
alone — no TorchSynth, Torch or NumPy import, no rendering, and no artifact
mutation (store writes are only the landed publish path during a run). The
inherited development/holdout access gate is enforced before data
resolution: holdout identities are refused before renderer or artifact-store
access, and every executed case in this issue's evidence is a development
identity. `python -S` verification is exercised by the test suite and
available through `--check-publication`.

## Storage cost and compression

Trace payloads are stored as original uncompressed little-endian binary32
bytes. No compression is used in v1 (`storage.compression: "none"` is a
pinned constant of both companion forms); any future compressed encoding
requires a new version with a pinned codec, settings, and decoded-byte
round-trip verification. The bounded publication reports per-case and
aggregate measured bytes: trace payloads, audio payloads, bundle bytes and
companion bytes.

## Evidence and reproduction

The committed bounded publication is `sim/reference/trace-artifact-smoke.json`:
the actual two-development-case (global-0, global-1) traced corpus rendered
through the landed #15/#23 APIs in the DR-0006 qualified runtime, its
audio-only variant corpus, the published companion surface, a resume of both
runs with zero renderer/capture calls and every referenced byte rehashed,
the measured storage costs, exact commands, host and producer identity,
registry binding, embedded documents, and honest limits. Inspection of the
committed record is not a fresh numerical run.

```sh
python3 -m unittest discover -s tests -p test_trace_artifacts.py -v
python3 tools/qualify_trace_artifacts.py --check-inputs
# host-gated qualified run (Apple M5 / macOS 26.5.1 / Docker 29.7.2, clean checkout):
python3 tools/qualify_trace_artifacts.py --store out/trace-artifacts-smoke-<fresh>
# with the retained raw store:
python3 tools/qualify_trace_artifacts.py --check-publication --store out/<store>
python3 tools/qualify_trace_artifacts.py --check-publication
```

CI (`.github/workflows/trace-artifacts.yml`) compiles the new surfaces, runs
the synthetic contract suite, `--check-inputs`, and `--check-publication`
when the publication exists; absence is reported as absent, never as a pass.

## Limits

Two development identities only; no holdout access, full 96-case corpus, or
drum nebula. Traces are unmodified observations of the pinned Voice graph;
nothing here establishes scalar promotion, runtime portability beyond the
one measured host, sound fidelity, independent-model DSP, RTL, synthesis,
layout, signoff, or hardware playback. The companion documents authenticate
exactly the bytes they pin; they are not a sandbox against an actor who can
rewrite the store and every trusted record.
