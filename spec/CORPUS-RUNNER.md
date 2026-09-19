# Manifest corpus runner and artifact adapter

`tools/render_corpus.py` implements corpus-v0 development selection, strict v1
artifacts/indexes, durable attempts, and explicit holdout admission. It imports
only the standard library in the parent process. The normative source remains
`2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`; the product remains a four-second,
44100 Hz default Voice clip. This is reference software, with no hardware,
perceptual fidelity, or scalar qualification claim.

## Runtime and producer admission

The sole production backend is DR-0006 `release-mkl-compatible-v1`: the measured
Apple M5/macOS 26.5.1 host and Docker Linux/arm64 29.7.2 server, launching the
qualified immutable Linux/amd64 image under emulation. The image digest comes
from the committed runtime publication. The unchanged lock and Dockerfile are
checked; the helper checks actual package inventory, Python, Linux platform,
full CPU text, Torch build, numerical environment, and both Torch thread counts
against the measured release receipt. Launch fixes one CPU, 6 GiB, all declared
threads to one, `MKL_CBWR=COMPATIBLE`, and removes `ATEN_CPU_CAPABILITY` before
Python starts. A changed host/build refuses; a native CI sentinel does not
authorize production on that host. A refusal is retained as a failed attempt.

The root package requires Python >=3.11. Its process-isolated Python 3.9 helper,
`env/release-era/render_artifact.py`, imports the existing qualification source
and runtime checks without importing the root package. It verifies pinned
source, lock, configuration, installed packages and host before importing
Torch. Only upstream Voice executes DSP. A forward hook observes selected
noise and a call profiler clones the actual signal entering upstream
`normalize_if_clipping`; neither replaces DSP. The adapter derives a gain from
that observed peak. The derived gain and observed peak/hash are separately
named in the run receipt. More detailed module facts are explicitly unavailable.

Production requires a clean, frozen producer checkout. `project_git` identifies
the actual implementation checkout using the v1 commit/diff/untracked encoding;
it is checked before and after every worker call. The runtime's v1 closed object
has no math/thread fields: the fixed admitted policy is bound by
`renderer_version` and `project_git`, while actual full runtime/process/launch
receipts live in the run envelope. The package-lock hash alone does not identify
COMPATIBLE versus the failed historical baseline.

## Development commands

From the frozen producer checkout, on the admitted host:

```sh
python3 tools/render_corpus.py --store /physical/path/corpus-store --indices 0 1
python3 tools/render_corpus.py --store /physical/path/corpus-store --indices 0 1 --resume RUN_ID
python3 -S tools/render_corpus.py --store /physical/path/corpus-store --verify RUN_ID --run-sha256 ENVELOPE_SHA256
```

Use a physical, local POSIX path (no symlink ancestors). The default selection
is all 96 development indices; `--indices` requests a bounded subset. Issue #15
executes only 0 and 1. The full 96-case evidence run is separate #19 work.
Output includes the exact-byte index and run-envelope references and counts.
Exit 1 means one or more cases failed; verification of an honest failed run is
valid but does not turn it into a successful corpus.

After publishing evidence in another commit, return to the **recorded producer
commit** for independent reproduction (#20). A separate clone checked out at
that exact commit is suitable. Do not run from a later evidence commit and
overwrite `project_git` with an older value. Independent repeats use a fresh
store/run; same-run `--resume` verifies existing content and must not render
again. Do not point at a mutable tag or install another Torch stack.

## Public adapter and capture seam

```python
from torchsynth_voice.artifact_renderer import (
    DockerBackend, fixture, render_artifact, request_template,
)
from torchsynth_voice.storage import ArtifactStore

request = dict(request_template(), fixture=fixture(0))
product = render_artifact(request, ArtifactStore(store_root), DockerBackend())
reference = product.reference  # artifact_id, exact metadata sha256, portable ref
```

`request_template` accepts batch size 32, named physical
locks, `trace_registry_version`, and ordered `requested_traces`. The adapter
validates source/runtime/configuration before calling its backend. Parameters
must be the authentic 78 names from the pinned inventory; positional, missing
and extra names fail. The worker checks its returned forward parameter tensor
against actual named parameters and its selected noise against index modulo 32.
All audio/noise/pre-normalization arrays are exactly 176400 finite float32
samples. Raw audio is authoritative. No audition WAV is added to the artifact.

This adapter deliberately supports only the qualified width 32. Larger batches
can compute holdout sounds incidentally while selecting a development index;
they are refused before backend access. Every development batch here stays
inside 0–95. Explicit holdout execution computes its canonical 96–127 batch
but publishes only the admitted selected sounds.

`render_artifact(request, store, backend, capture_provider=...)` is the #24 seam:
registry identity and requested names are present before the artifact input ID
is formed. The backend receives the provider during the original render and
returns validated selected-sound trace payloads before atomic publication.
The worker's `render_selected` accepts a provider `(request, voice, slot)` that
is a context manager yielding the named payload mapping. It enters before the
single Voice call and exits after that call. The provider owns registry checks
and trace semantics; this issue implements no production named-trace provider.
The stock Docker backend therefore accepts audio-only requests and refuses
requested traces with no provider. #24 owns its process integration and richer
trace companion outside immutable artifact directories. Synthetic tests use an
injected backend/provider and do not establish production trace capture.

An empty requested/captured set explicitly means audio-only. Traced and
audio-only requests receive different identities. Exact corpus resume verifies
the original reference and skips both backend rendering and capture. Consumers
such as #64 can consume this adapter; they must enforce this corpus's holdout
policy when selecting preregistered identities. The adapter is not an explorer
session/player implementation.

## Durable publication and completeness

```text
store/
  artifacts/ra1-.../                 landed immutable ArtifactStore layout
  manifests/<sha256>.json            original exact manifest bytes
  indexes/<sha256>.json              strict stable corpus-index-v1 bytes
  runs/<run-id>/
    plan.json                       immutable selection/request/provenance
    .run.lock                       cooperative single-writer lock
    events/000000-start.json         durable before worker invocation
    events/000000-finish.json        result, reference/receipt, measured timing
    summaries/000002.json            separately versioned corpus-run-v1 envelope
    admission.json                  holdout runs only
    frozen-rubric.json               holdout runs only
```

The manifest is expanded before any selected case is resolved. Duplicate or
overlapping ranges, missing/extra identities, duplicate requests, out-of-range
indices and partition inconsistencies fail. Indexes contain every selected
identity, including failed/unattempted cases with null artifact references.
The selection and complete request declaration are bound through an immutable
plan; the envelope references exact plan/index bytes. The stable v1 index has
no telemetry additions. Same successful artifacts produce byte-identical indexes
on resume, despite new timing/resume events.

Counts reconcile against the complete ordered event history: `expected` is the
selected denominator; `observed` and `success` are COMPLETE cases; `failure` is
the remaining cases. `attempt` counts renderer invocations, `retry` counts
invocations after the first for each case, and `resume` counts verified reuse
events. Failed attempts stay in the envelope after a successful retry. Elapsed
time is measured per event and summed; an interrupted attempt with no finish
has explicit null elapsed time, making the aggregate null as well. Null never
means a zero-duration successful measurement.

Start/finish records and snapshots are published from fsynced private files
without replacing existing bytes. A process interrupted before completion leaves
a failed `interrupted-attempt` in the reconstructed journal. Explicit same-run
resume may retry it. A completed finish receipt survives interrupted index or
summary publication and resumes without a worker call. If a worker published
an artifact but died before its finish receipt, retry may recompute; the landed
store still verifies exact bytes and refuses collisions. There is no claim of
exactly-once execution across that uncertainty window.

Resume checks the full selected request/provenance against the immutable plan,
then rehashes metadata, audio and every trace through the existing store's
read-only verifier. Stale inputs, corrupted files, missing or extra files,
changed metadata digests and same-ID/different-byte output cannot silently
reuse or overwrite an artifact. The local read-only store adapter avoids the
landed constructor's directory creation; verification never repairs content or
creates even a missing root. `python -S` verification imports no TorchSynth,
Torch, NumPy or jsonschema. Supply an externally retained envelope digest to
authenticate the latest snapshot as well as its referenced content.

The filesystem scope is the landed store's cooperating local POSIX writers.
Run/audit locks are persistent; do not delete them during use. Immutable data,
the audit ledger and producer checkout must remain controlled by the operator.
This is not a sandbox against an actor rewriting all trusted evidence.

## One-shot holdout gate

Development mode refuses any index 96–127, including a mixed request, before
renderer or artifact-store access. Explicit holdout mode requires both an
operator-frozen rubric document and a single persistent audit ledger shared by
all runs for that corpus:

```json
{"schema":"torchsynth-frozen-rubric","schema_version":1,"frozen":true,"rubric":{"thresholds":"operator-supplied frozen rubric contents"}}
```

The `rubric` object contains the actual frozen rubric/threshold declaration;
the exact complete file hash is the freeze identity. The operator attests that
freeze occurred before exposure. Do not create this record as a shortcut to
inspect holdout while choosing a rubric.

`--holdout-once --frozen-rubric FILE --holdout-audit-root LEDGER` reserves one
admission for the entire corpus manifest, storing its hash, selected identities,
request-template hash, freeze hash and run ID before case resolution/rendering.
Any new run under that ledger is refused, even for another subset. An explicit
`--resume` of that same run may finish/retry only its original cases with exactly
the same rubric and inputs; completed cases are rehashed and reused. Interrupted
attempts and retries remain part of that single audited exposure. Changing
rubric, inputs, selection or run ID is a new exposure and is refused.

Do not reset or replace the ledger to obtain another exposure. Auditing this
policy across machines requires using the same operator-controlled ledger;
there is no remote/global admission service. Holdout verification also requires
the explicit `--holdout-once` flag and remains read-only. All holdout tests in
this implementation use synthetic payloads. No actual holdout data was rendered
or inspected by issue #15.

## Validation evidence

`tests/test_artifact_renderer.py` and `tests/test_corpus.py` exercise synthetic
name/noise/source/runtime faults, immutable collisions, exact reuse, failed
denominators/retries, coverage/count tampering, interrupted publication, explicit
synthetic holdout admission, and verification with site packages disabled.
Actual bounded rendering commands, producer identity, measured receipts and
exact hashes are recorded separately in `sim/reference/corpus-smoke.json`.
That record is bounded software evidence; it does not establish the full
development corpus, holdout behavior on real data, named trace capture or sound
fidelity.
