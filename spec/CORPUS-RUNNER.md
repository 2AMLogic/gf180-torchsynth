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

## Full 128-case corpus commands and storage

The corpus-v0 manifest has 128 sounds: development 0-95 and holdout 96-127.
A single invocation never mixes the partitions, so the full corpus is two runs
into the same or separate stores. The 96-case development command has been
executed: issue #19 rendered and verified the full development corpus (run
`9c443eed363e4874a07328d8ddadd095`; see `spec/DEVELOPMENT-CORPUS.md` and
`sim/reference/development-corpus-first.json`), and issue #20 byte-repeated it
(`spec/DEVELOPMENT-CORPUS-REPEAT.md`,
`sim/reference/development-corpus-repeat.json`). The holdout command has never
been run; it is a one-shot, post-freeze action:

```sh
# 96 development cases (default selection)
python3 tools/render_corpus.py --store /physical/path/corpus-store
# 32 holdout cases, once, after the rubric is frozen
python3 tools/render_corpus.py --store /physical/path/corpus-store \
  --indices $(seq 96 127) --holdout-once \
  --frozen-rubric /physical/path/frozen-rubric.json \
  --holdout-audit-root /physical/path/holdout-ledger
python3 -S tools/render_corpus.py --store /physical/path/corpus-store \
  --verify RUN_ID --run-sha256 ENVELOPE_SHA256 [--holdout-once]
```

Storage lower bound: each artifact holds one 705,600-byte float32 audio file
(176,400 samples x 4 bytes), so 96 cases need at least 67,737,600 bytes
(about 64.6 MiB) and 128 cases at least 90,316,800 bytes (about 86.1 MiB), plus
per-artifact metadata (a few KiB), manifests, indexes and run journals, and
one extra private staging copy of the artifact being published. Requested
traces add payload per case. For comparison, the recorded #19 development
store measured 390 files and 71,820,533 bytes
(`development-corpus-first.json` `storage`). The store must be a local POSIX filesystem; keep the
holdout ledger and frozen rubric outside the artifact store and under operator
control. Raw audio stays out of Git unless an explicit storage decision is made.

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

## Portable attempt receipts

Decision (issue #289): every newly persisted attempt receipt, in a journal
finish record or a run envelope, is portable. It contains no host filesystem
path, as a key or value, standalone or embedded, at any nesting depth. This
extends the [artifact contract](ARTIFACT-CONTRACT.md) rule against host paths
in artifacts, indexes and plans to `attempts[*].receipt`. It covers only receipts.
Outer publication records such as the retained `commands[*].command` and storage
fields are outside this decision, except for the per-producer command
descriptions adopted under
[Portable command descriptions in evidence publications](#portable-command-descriptions-in-evidence-publications).

**Command representation.** The Docker backends (`DockerBackend` and the traced
`TracedDockerBackend` in `tools/qualify_trace_artifacts.py`) launch a real argv
with real mount sources. They never publish it. The receipt `command` is a
separately built public description with the same program, image identity,
environment flags, options and argument order. Only the two host mount sources
are replaced by named placeholders:

| Placeholder | Host source it stands for | Container target |
|---|---|---|
| `<project-root>` | resolved frozen producer checkout | `/repo` (read-only) |
| `<worker-output>` | per-invocation private temporary directory | `/output` |

A receipt that carries `command` must also carry
`command_representation: "portable-placeholders-v1"`. This marks the command as a
described launch, not the literal executed argv. Placeholder commands are never
executed. Every `src=`/`source=` mount option and `-v`/`--volume` argument must
name a declared placeholder. The request, worker, runtime, stdout and stderr
digests stay as before.

**Host-path detection** is lexical and independent of the verifier's OS. Each
receipt string, including dict keys, is scanned for path tokens. A token starts
at the beginning of the string or after whitespace or one of
`= , : ; " ' ( ) [ ] { } < > |`. The scan detects:

- POSIX absolute (`/…`), including tokens embedded as `src=/host/path` and
  the `//…` part of URL-like strings, which are rejected rather than parsed;
- home-relative (`~/…`, `~\…`);
- Windows drive (`C:\…`, `C:/…`), when the drive letter is not preceded by an
  ASCII letter or digit;
- Windows rooted and UNC (`\…`, `\\server\share`, `\\?\…`).

A lone `/` token, as in prose such as `a / b`, also counts, so the scan fails
closed. Windows, UNC and home forms are always rejected. A POSIX token is accepted only
when the declared receipt field allows its container root. The token must equal
that root or continue it with `/` and must contain no `..` component:

| Receipt field | Permitted container roots |
|---|---|
| `command` | `/repo`, `/output`, `/opt/torchsynth` |
| `runtime.torch_build` | `/opt/rh/devtoolset-9` (Torch wheel build toolchain recorded by the qualified image) |

Every other field, including fields this table does not name, allows no
absolute path. An unexpected field therefore cannot bypass the scan.

**Failed attempts.** A failed render attempt records no raw exception text that
could contain a host path. Its receipt has exactly four fields:

```json
{"error_type": "ValidationError", "message": "host outside DR-0006 measured scope",
 "message_sha256": "<sha256 of the UTF-8 message>", "message_status": "portable"}
```

If the message contains a host path, `message` is `null` and
`message_status` is `"withheld-host-path"`. `message_sha256` still binds the
original diagnostic, so an operator holding the raw log can match it.
`failure` remains the portable `render-<ExceptionType>` code.

Interrupted attempts and resume events keep the empty receipt `{}`.

**Enforcement points.** `render_artifact` checks the backend receipt before
artifact publication. `run_corpus` checks each receipt again before writing its
immutable finish record. If a successful render returns a non-portable receipt,
the attempt is recorded as failed (`render-ReceiptPortabilityError`) with the
portable diagnostic above. The finish record never contains the path-bearing
receipt. A later explicit resume may retry that case. `validate_run` also
applies the check before any summary is written, and `verify_run` applies it
when it reads stored runs. Counts, timing, artifact identities, payload digests,
and source/runtime admission are unchanged.

**Historical v1 records.** The run schema remains `corpus-run-v1`. This policy is
a stricter semantic validation of v1, not a new envelope version. Retained
evidence produced before this decision, including `sim/reference/corpus-smoke.json`
and the #19/#20 development stores, contains host mount sources and stays
byte-identical. Verification never rewrites, repairs or sanitizes it. The
current default policy (`receipt_policy="portable-v1"`) **rejects** those
historical runs. There are two ways to verify them:

1. Use the recorded producer commit, as this document already requires for
   reproduction.
2. Use the current verifier with the explicit compatibility mode
   `receipt_policy="historical-v1"` (`tools/render_corpus.py --verify RUN_ID
   --historical-receipts`). This mode runs every other check and skips only
   receipt portability. It reports `"receipt_policy": "historical-v1"`, never
   that receipts are portable. `tools/audit_development_corpus.py --store` uses
   this mode because its subject is the retained #19/#20 stores.

Resuming a historical run at the current commit is refused: its interim
validation uses the portable policy. Republishing historical evidence is a
separate decision.

## Portable command descriptions in evidence publications

Decision (issue #309), extending the command-representation policy above to
non-corpus evidence publications. This is a representation change only. The
executed argv, image and runtime admission, hashes and DSP behavior are
unchanged. Each adopting producer builds the executed argv (real host mount
sources) and the described argv (the two declared placeholders) from one launch
builder (`launch_and_described` in `src/torchsynth_voice/artifact_renderer.py`),
executes only the former and publishes only the latter. A publication that
carries a described command sets `command_representation:
"portable-placeholders-v1"` beside it (the `PORTABLE_COMMAND_REPRESENTATION`
constant). Container-internal absolute paths (`/repo`, `/output`,
`/opt/torchsynth`) are never replaced.

`validate_published_command` checks only the described command, not the rest of
the publication, which may legitimately carry other storage paths such as the
`build_command` host paths. Without the marker the command is the retained legacy
form and is accepted unchanged, so existing records still validate. With the
marker, the validator rejects an unknown representation version, an undeclared
`<...>` placeholder, a host path (POSIX, Windows, UNC or home), a `-v`/`--volume`
or stray `src=` mount, and any mount set other than exactly
`type=bind,src=<project-root>,dst=/repo,readonly` followed by
`type=bind,src=<worker-output>,dst=/output`. A forged target, a dropped
`readonly`, or a host source therefore fails.

| Producer | Decision | Rationale |
|---|---|---|
| `tools/capture_float_sources.py` | **Adopt** | `execution.command` is described. Its tool digest `recorded_from_tool` is informational: no validator compares it, and the retained record already differs from the live file. The retained record has `command: []` (assemble-only) and stays legacy. A non-empty command is marked. |
| `tools/qualify_trace_capture.py` | **Adopt** | `launch.command` is described. The tool is not in any retained `producer_sha256`. `--check-publication` validates marked commands and keeps accepting the retained unmarked `sim/reference/trace-capture.json`. |
| `tools/qualify_mutations_runtime.py` | **Adopt** | `launch.command` is described. The tool is not in any retained pin. `--check-publication` applies the same rule. The retained `mutation-runtime-v1.json` stays legacy. |
| `tools/qualify_directed_trace_paths.py` | **Defer** | `sim/reference/directed-trace-paths-v1.json` pins this tool's digest as `input_sha256.producer_sha256`, and `directed_trace_paths` rejects the record when the digest differs from the repository file. Any edit would require regenerating the record, which is a recapture and a rewrite of `sim/reference/`. |
| `env/release-era/qualify_repeatability.py` (producer and validator) | **Defer** | Its digest is pinned in `producer_sha256` of `trace-registry-prototype.json` (enforced by `tests/test_trace_registry.py`), `trace-capture.json` and `mutation-runtime-v1.json`. Editing it breaks those pins without a recapture. |
| `tools/probe_trace_registry.py` | **Defer** | Recapture-gated (`DISPATCH_RECAPTURE_GATED`): `trace-registry-prototype.json` pins its digest. Its bytes and gate are untouched. |

**Repeatability cell binding (decision for the deferred adoption).**
`validate_render_command` ties a described release command to its cell by two
checks: the host output `Path(...).name` must equal `cell["directory"]`, and the
whole command must equal `command_for(...)` rebuilt for that directory. A bare
`<worker-output>` would erase the cell identity, so accepting it is not
permitted. When the producer is later recaptured and adopted, the described
output source must be the declared placeholder followed by the cell directory
leaf (`<worker-output>/<cell.directory>`), the validator must compare that leaf to
`cell["directory"]`, and it must keep the worker, case-group, run-id and
rebuilt-command comparisons. The `current` runtime, which has no container,
keeps its absolute-path checks. Until then the legacy form is the only one
accepted and nothing in the repeatability producer or validator changes.

The deferrals are not claims that those records are portable. Retained records
under `sim/reference/` stay byte-identical, and nothing here renders,
requalifies or alters admission.

## Validation evidence

`tests/test_artifact_renderer.py` and `tests/test_corpus.py` exercise synthetic
name/noise/source/runtime faults, immutable collisions, exact reuse, failed
denominators/retries, coverage/count tampering, interrupted publication, explicit
synthetic holdout admission, and verification with site packages disabled.
They also cover portable receipts with synthetic, path-bearing negative
controls. These include standalone, embedded and nested POSIX, Windows, UNC
and home paths, path-bearing exceptions, and non-portable success receipts.
Each is rejected before a finish record. Historical-policy verification is
read-only. Both Docker backends run with mocked host and process facts: the
traced backend is covered in `tests/test_trace_artifacts.py`, and no Docker
process runs.
Actual bounded rendering commands, producer identity, measured receipts and
exact hashes are recorded separately in `sim/reference/corpus-smoke.json`.
That record is bounded software evidence; it does not establish the full
development corpus, holdout behavior on real data, named trace capture or sound
fidelity.
