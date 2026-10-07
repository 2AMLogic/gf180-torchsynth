# Traced development-corpus storage and memory cost (#287)

This record covers how the storage and memory cost of a traced
development-corpus render is measured. It defines the instrument and records
that **the measurement has not been run**.

The committed receipt `sim/reference/development-trace-cost-v1.json` is
**`UNRUN`**. #7 criterion 8 ("Memory/storage cost for one case and the
development corpus is measured") therefore stays **missing** for the
development corpus. No figure in this record is a measured corpus cost.

## Scope

- **Identities.** The 96 development identities, global indices 0–95 from
  `spec/reference/corpus-v0.json`. The development partition refuses holdout
  indices 96–127 before admission, store or renderer access.
- **Selection.** The registry-ordered 29-trace production selection,
  `trace_artifacts.production_selection()`. It is the same formula as
  `tools/qualify_trace_artifacts.selection_record()`, and `--check-inputs`
  asserts that the two agree.
- **Normalization seams.** `mixer.pre_normalization`, `mixer.peak` and
  `mixer.gain` are not stored traces. The landed worker's boundary profiler
  retains them in each attempt receipt. Every completed row carries them as
  `normalization_seams.state = "receipt-retained"`, with the
  pre-normalization digest, peak and derived gain.
- **Path.** The landed #24 traced path: `request_template()` with the traced
  fields, `run_corpus()`, `render_artifact()`, and
  `qualify_trace_artifacts.TracedDockerBackend`. Bundles come from
  `build_case_bundle()`, the companion from `build_companion()`, and both are
  published with `publish_companion_surface()`.
- **Unchanged.** Not changed: DSP, the trace registry, `host_admission()`, the
  DR-0006 runtime publication, and the existing retained publications. The
  backend gains two optional hooks, `driver_source` and `collect_telemetry`.
  Their defaults reproduce the #24 smoke launch, driver and receipt exactly.

## Instrument

- **Runner.** `tools/measure_development_trace_cost.py`.
- **Accounting and verifier.** `src/torchsynth_voice/development_trace_cost.py`
  (stdlib only).

### Run order

1. **Producer check.** The runner requires a clean committed producer
   checkout.
2. **Freeze the plan.** The plan records the manifest digest, the producer
   commit and `project_git`, the profile `release-mkl-compatible-v1`, the
   ratified publication digest, the qualified image ID, the request runtime,
   the registry binding, the selection and its digest, the exact identities
   0–95, the request-template digest, the telemetry declaration and the
   telemetry-driver digest.
3. **Admission.** The runner calls the unchanged `host_admission()`.
   - If the gate raises, the runner writes an `UNRUN` receipt carrying the
     frozen plan, the host facts, the command and the refusal. No store,
     Docker or render access happens. The exit code is 2.
4. **Fresh store.** On admission, the runner requires a fresh store root and
   writes the frozen plan to `development-trace-cost/plan.json` inside it
   before the first render.
   - `--resume RUN_ID` continues an interrupted run. It must use the same
     store and a byte-identical plan.
5. **Render.** Each case runs in its own fresh container worker.
6. **Publish and verify.** The runner publishes the bundles and the
   companion. It then builds the receipt from the verified store and
   re-verifies that receipt against the store before writing it.
   - A retained measured receipt is never overwritten. Only an `UNRUN`
     receipt may be replaced.

### Telemetry

The measurement driver is the unchanged #24 driver body. A telemetry prelude
goes before it and a telemetry entry point after it. `--check-inputs` and the
tests assert that the body is embedded verbatim. The driver adds no DSP,
capture or admission logic; it only reads clocks and `getrusage`.

- **RSS.** `resource.getrusage(resource.RUSAGE_SELF).ru_maxrss` is read
  inside the container. The unit is KiB and is accepted only when the worker
  reports `platform.system() == "Linux"`. It is sampled at three points:
  after imports, after the render call, and at the driver end.
  - The per-case peak is the driver-end value. It covers interpreter
    startup, imports, Voice setup, rendering, capture and output
    serialization.
  - It excludes the host launcher, Docker, the emulation layer outside the
    guest process accounting, and any simultaneous corpus memory.
  - One process runs per case, sequentially. Peaks are reported as
    count/min/median/max and the maximum case. They are never summed.
- **Container boundaries (`time.monotonic`).** Five marks:
  - `driver_prelude`, after interpreter startup;
  - `driver_imports_done`;
  - `render_call_start` and `render_call_end`, around
    `render_artifact.render_selected`;
  - `driver_end`, after all output files are written.

  The derived intervals are imports, render call, serialization and driver
  total. Each one is a difference of two marks, and the verifier recomputes
  them.
- **Host intervals.** These are labelled separately and are not worker
  rendering time:
  - `launcher_container_seconds` is host monotonic time around
    `docker run`;
  - `attempt_elapsed_seconds` is the landed corpus attempt time, which
    includes validation, the launcher, verification and store publication.
- **Missing telemetry.** Absent or invalid telemetry is recorded as
  `{"state": "missing", "reason": ...}`, never as zero. Possible causes
  include a non-Linux worker, a decreasing `ru_maxrss`, non-monotonic marks
  and incomplete fields.

### Storage accounting

Every regular file in the store is classified exactly once into one of these
categories: `trace_payload`, `audio`, `artifact_metadata`, `bundle`,
`companion` or `corpus_metadata`. The last category holds manifest copies,
indexes, run plans, journals and summaries, the frozen measurement plan and
the lock file. Symlinks, aliased files, staging residue and unknown paths are
refused.

- **Per case.** Each completed row reports the trace payload files and
  bytes, audio bytes, artifact metadata bytes, bundle bytes, the artifact
  logical total and the case logical total.
- **The companion.** It is one shared file, counted once under `shared`. It
  is never apportioned to cases.
- **Logical versus allocated bytes.**
  - Logical bytes are exact file lengths, and the verifier recomputes them.
  - Allocated bytes (`st_blocks * 512`) depend on the filesystem. They are
    reported under `storage.allocated` but are not verified.
- **Subtotals versus full-corpus totals.**
  `storage.completed_cases_subtotal` sums the completed rows only.
  `storage.full_corpus` is non-null only when all 96 identities completed.
- **Retained captures are not stored traces.** The #23 retained-capture
  bytes and the #24 stored trace payloads are different quantities.
- **Projections.** Projections live only under `projections`, labelled
  "not a measurement". They are smoke per-case trace bytes × 96
  (823,012,608) and the #23 capture projection (890,750,976).

### Case states and receipt status

- Every planned identity has exactly one row, in plan order. Duplicate,
  missing, unexpected and reordered rows are rejected.
- Each row's state is one of `completed`, `failed` (with the corpus failure
  code) or `unrun`.
- Receipt `status`:
  - `complete` only when all 96 identities completed with present telemetry;
  - `partial` when any case failed or lacks telemetry, with explicit
    subtotals and a `PARTIAL` limit;
  - `unrun` when admission refused.

### Verification and exit codes

`--verify-receipt` reports one of the following.

| Mode | Result | Exit |
| --- | --- | --- |
| no receipt | `ABSENT` | 2 |
| `UNRUN` receipt | `UNRUN` | 2 |
| measured receipt, no `--store` | `LIMITED`: document consistency only, raw bytes not rehashed | 2 |
| measured receipt with `--store` | `VERIFIED` when every byte was rehashed and every figure recomputed | 0 (`complete`) / 1 (`partial`) |
| any inconsistency | `FAIL` | 1 |

The document check verifies:

- the plan pins against the live registry, selection, manifest and the exact
  identities 0–95;
- the 96-row inventory;
- the state consistency of every row;
- the telemetry units and boundaries;
- every subtotal and distribution;
- the store-inventory reconciliation;
- the projections.

The store check adds the following:

- the frozen plan in the store;
- `verify_run()` against the pinned run-summary digest, which rehashes every
  artifact;
- the corpus plan's identity and template binding;
- `validate_companion()`, which rehashes every bundle and trace payload;
- a full recomputation of the receipt, allocated bytes excepted.

A document-only check cannot establish the integrity of absent raw bytes.

## Current evidence: UNRUN

`sim/reference/development-trace-cost-v1.json` comes from producer commit
`1553f66` (clean). The command was:

```sh
python3 tools/measure_development_trace_cost.py --store /home/ubuntu/gf180-287-store
```

- **Host.** The run happened on a shared Linux x86_64 Loom dispatch worker.
  It was not the sanctioned `repo-remote` AWS box and not the DR-0006 Apple
  M5 host.
- **Outcome.** Exit 2. `host_admission()` refused with `CalledProcessError`
  from `sysctl -n machdep.cpu.brand_string` before any store, Docker or
  render access. The store path was not created.
- **Rows.** All 96 rows are `unrun`.
- **Verification.** `--verify-receipt` reports `UNRUN` (exit 2).

This receipt is not a pass, does not measure anything, and does not establish
#7 criterion 8.

## Reproduction and live execution

Stdlib checks run on any host. On the sanctioned AWS box, use `python3.11`
from the worktree root.

```sh
python3.11 tools/measure_development_trace_cost.py --check-inputs
python3.11 tools/measure_development_trace_cost.py --verify-receipt   # UNRUN -> exit 2
python3.11 -m unittest discover -s tests -p 'test_development_trace_cost.py' -v
python3.11 -m unittest discover -s tests -p 'test_trace_artifacts.py'
python3.11 -m unittest discover -s tests -p 'test_corpus.py'
```

`tools/run_fast_tests.py` is the box's `tb/run_tb.py` lane wrapper. It does
not run these unit suites.

### Campaign

Run the campaign from a clean checkout of the producer commit, with a fresh
store under `/home/ubuntu`, because `/tmp` is wiped when the box restarts:

```sh
python3.11 tools/measure_development_trace_cost.py \
  --store /home/ubuntu/gf180-287-store-$(date -u +%Y%m%dT%H%M%SZ)
# after an interruption, the same store and run:
python3.11 tools/measure_development_trace_cost.py --store <same> --resume <RUN_ID>
# verify with the retained raw store:
python3.11 tools/measure_development_trace_cost.py --verify-receipt --store <same>
```

### Expected admission on the AWS box

`host_admission()` admits only the DR-0006 host: Darwin, macOS 26.5.1, arm64
Apple M5, with Docker server matching the publication. On the AWS box
(Linux, Intel Xeon 8175M) the campaign command therefore records `UNRUN` and
exits 2. Sanctioning the box as a substrate does not change admission or
establish DR-0006 qualification. Admitting it requires separately reviewed
qualification evidence, which this record does not provide.

### Completing the measurement

The live measurement remains **pending**. To complete it, run the campaign on
an admitted host and retain the raw store. Then replace the `UNRUN` receipt
with a `complete` receipt that verifies `VERIFIED` (exit 0) against that
store. Record the exact commands, exit codes, timestamps and identities. A
`partial` receipt lists its failed and unrun cases and does not establish the
full-corpus cost.

## Limits

- Synthetic tests (`tests/test_development_trace_cost.py`) use fake
  backends, a fake admission gate and a three-case subset. One test runs the
  composed driver against stub worker modules on the host Python. None of
  them is runtime evidence.
- The telemetry driver has not run in the qualified image's Python 3.9. It
  was parsed with `feature_version=(3, 9)` only.
- Under the DR-0006 host's amd64 emulation, `ru_maxrss` is the guest
  process's kernel accounting. It does not cover translator overhead outside
  that process.
- No holdout, fidelity, scalar, synthesis, layout, signoff or hardware
  playback claim is made.
