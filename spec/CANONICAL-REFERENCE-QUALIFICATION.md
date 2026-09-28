# Canonical CPU reference — consolidated qualification record

Phase-closure record for issue #3 ("qualify the canonical CPU reference"), the
Epic #1 phase tracker whose four work units are #10 (reproducible release-era
CPU environment), #11 (selected commit versus v1.0.2 equivalence), #12
(canonical batched repeatability, identity, and the runtime decision record)
and #88 (single-sound scalar execution qualification).

Those four leaves landed separately, each with its own evidence. This document
exists because the tracker's stated closure condition is not that they landed —
it is that **their evidence is consistent with each other**, which is a
different claim and one nobody had checked. It records what was re-derived, by
what command, with what result; what disagreed; and what is still open.

It adds no measurement of its own. It renders no audio, ratifies no runtime,
and widens no host scope. Where it states a numeric outcome, that outcome was
measured by the cited leaf and is merely being cross-checked here.

- **Status:** consolidation verified 2026-09-28 against `origin/main` at
  `938915a`, with the exceptions recorded under [Open
  divergences](#open-divergences).
- **Governing records:** [DR-0001](decision-records/0001-torchsynth-version.md)
  (source pin), [DR-0006](decision-records/0006-canonical-runtime.md)
  (canonical runtime), [DR-0007](decision-records/0007-single-sound-execution.md)
  (scalar execution scope),
  [DR-0009](decision-records/0009-ci-runner-reproducibility-envelope.md)
  (CI comparison envelope; Proposed).
- **Re-run the cross-check:** `python3 tools/check_reference_consolidation.py`,
  also executed on every PR by `.github/workflows/ci.yml` through
  `tests/test_reference_consolidation.py`.

## Why this is a program and not only a table

A consistency review that lives only in prose decays silently: the artifacts it
describes keep moving, and nothing tells the next reader that the description
stopped being true. That is not hypothetical here — it is exactly how both
defects this consolidation found came to sit on `main` (see [Corrected during
consolidation](#corrected-during-consolidation)).

DR-0009 amendment A2 did two things: it republished
`sim/reference/repeatability-runtime.json`, and it changed the dispatch
environment the preregistered plan declares. Two hard-coded gates pin that
file's digest; A2 updated one. Eight host spawn paths declare that environment;
two of them already derived it from the plan and so needed no change, which is
why A2 could correctly describe itself as needing "zero code changes" — but the
remaining six restated the pins as literals and were left describing the
superseded environment. Both sentinel workflows stayed green throughout, and
correctly so: the *bytes* were fine, and the sentinels' own spawn is one of the
two plan-derived ones. What was stale was everything else *referring* to those
bytes and that plan, and no sentinel looks at those.

So the durable output of this phase is
`tools/check_reference_consolidation.py`: the cross-record facts below are
expressed as checks that run in CI on a machine with no torch, no torchsynth
and no docker. The two layers have different jobs and neither substitutes for
the other:

| Layer | Asserts | Runs where |
| --- | --- | --- |
| `reference-repeatability` / `reference-scalar` sentinels | the pinned stack still renders the committed bytes | pinned container, `ubuntu-22.04` pool |
| `tools/check_reference_consolidation.py` | the records, gates and spawns *about* those bytes still agree with each other and with the tree | any CI runner, standard library only |

## Decision-record supersession

DR-0001 pinned the source and explicitly deferred the runtime: its "Remaining
qualification" section required a later gate to "render repeatable fixtures,
compare a patched-import `v1.0.2` checkout against this commit in one
environment, and record cross-environment drift."

All three clauses are discharged, and DR-0001 now says so and points forward.
The supersession is **partial and bounded**, which is what DR-0006 itself
claims ("supersedes only DR-0001's unresolved runtime choice"):

| DR-0001 content | Status |
| --- | --- |
| Source commit `2b0964d4…`, default nebula, CPU-only, v1.0.2 as released baseline | **Still in force.** Not touched by DR-0006 or DR-0007. |
| Constraints (no moving `main`, canonical parameter names, frozen rates/dtype/hashes) | **Still in force.** |
| "Remaining qualification": canonical Python/PyTorch runtime unselected | **Superseded by DR-0006**, profile `release-mkl-compatible-v1`. |
| "Remaining qualification": repeatable fixtures + cross-environment drift | **Discharged by #12's evidence**, `sim/reference/repeatability-runtime.json`. |
| "Remaining qualification": patched-import v1.0.2 comparison | **Discharged by #11's evidence**, `env/release-era/source-comparison.json` and `sim/reference/source-equivalence.json`. |
| Batch-size-1 / single-sound execution | **Separately scoped by DR-0007** as a diagnostic oracle — explicitly *not* the corpus identity generator. |

DR-0007 records its own cross-check against DR-0006 (§"Reconciliation with
DR-0006 (2026-09-19)") and the two agree on the runtime settings they declare:
`MKL_CBWR=COMPATIBLE`, ATen capability selector left at the image default. The
declaration agrees; one *recorded measurement* of it does not, and that
disagreement is recorded under [Open divergences](#open-divergences) rather
than smoothed over.

Neither DR-0006 nor DR-0007 supersedes DR-0001 as a whole, and the
decision-record index correctly still lists DR-0001 as `Accepted`.

## Acceptance criteria, re-derived

Each row was checked against the tree at `938915a`, not taken from the leaf's
own summary. "Re-executed" means a command was run in this session and its
result read; "re-derived" means the artifact's own numbers were recomputed from
its bytes; "read" means the claim rests on the leaf's recorded measurement,
which this consolidation did not reproduce.

### 1. Environment definitions are reproducible from committed lock/container files

**Met — re-derived.** `env/release-era/Dockerfile` hashes to
`e375979e5fa3152e…` and `env/release-era/requirements.lock` to
`1966379fb95fed9d…`, exactly the `runtime_definitions.release`
`dockerfile_sha256`/`lock_sha256` values in the preregistered
`env/release-era/repeatability-matrix.json`. The base image is digest-pinned
(`python:3.9.13-slim-bullseye@sha256:b3bb5145…`), the upstream source archive is
fetched with `ADD --checksum=sha256:df90d3c9…`, the install is
`--require-hashes --only-binary=:all:`, and every requirement line in the lock
is `==`-pinned with hashes. No floating range, no mutable tag, no apt step.

### 2. Every reference run passes source SHA validation before import

**Met — re-derived by line order.** `validate_source()`
(`env/release-era/probe.py:30`) re-hashes every file of the pinned manifest and
raises before returning. In each render entry point the validation call
precedes the numerical import:

| Worker | Admits source at | Imports torch at |
| --- | --- | --- |
| `env/release-era/probe.py` | 102 | 109 |
| `env/release-era/qualify_repeatability.py` | 547 | 796 |
| `env/release-era/qualify_scalar.py` | 169 | 504 |
| `env/release-era/render_artifact.py` | 43 (`source_gate`) | 158 |

`render_artifact.py` additionally refuses outright if `torch` or `torchsynth`
is already in `sys.modules` ("numerical import preceded admission"). The
ordering is checked programmatically, so a future edit that moves the import
above the gate fails CI rather than passing quietly.

### 3. Selected commit versus v1.0.2 comparison isolates and records the import-only patch

**Met — re-derived.** Of the eleven pinned Voice files compared in
`env/release-era/source-comparison.json`, exactly two differ:
`torchsynth/synth.py` and `torchsynth/profile.py`. The recorded patch is one
removed and one added line —
`from pytorch_lightning.core.lightning import LightningModule` becomes
`from lightning import LightningModule` — and `profile.py` is the unused
Lightning profiling CLI, not Voice DSP. `config.py`, `module.py`,
`parameter.py`, `signal.py`, `util.py` and the default nebula are
byte-identical across `v1.0.2` and the selected commit.

The `selected` side's per-file digests equal `spec/reference/upstream.json`'s
pins with no exceptions, and the rendered counterpart
`sim/reference/source-equivalence.json` (status `passed`, two fresh containers,
with a changed-source negative control) still names the exact `env/release-era`
tree that produced it: all twelve files it pins, including `probe.py`,
`compare_sources.py` and `source-comparison.json` itself, hash to their
recorded values today.

### 4. Fresh-process repeats are byte-identical in the proposed canonical environment

**Met — re-derived from the record.** `sim/reference/repeatability-runtime.json`
carries 128 cells, all `PASS`, and 64 of 64 `repeat` comparisons `PASS`
byte-exact. Cell statuses were checked for the whole set, not sampled: a single
`NO_VERDICT` would have failed the census. The comparison payloads carry the
per-artifact `equal_bytes`, first-differing sample and max/mean/RMS fields, so
a "PASS" is backed by measurements rather than by a status string alone.

### 5. Batch-size independence is demonstrated on audio and named parameter maps

**Met — re-derived from the record.** 48 of 48 `batch` comparisons `PASS`
byte-exact. The matrix is square: 32 cells at each of batch sizes 32, 64, 128
and 256, across two runtimes and two fresh-process repeats. Comparisons cover
the normalized and physical 78-name parameter arrays, the selected noise, the
train/test label byte, the named seams and the final audio — not audio alone.

The preregistered probe identities are the ones issue #3 required: global 0, 31
and 32 (noise-slot and batch-boundary), 9215/9216 (the train/test boundary at
`(index // 1024) % 10`), 39942 — which is `synth1B1-312-6` at the upstream
nominal batch size of 128, `divmod(39942, 128) == (312, 6)` — and the directed
`normalization-off` / `normalization-on` pair exercising both branches of the
conditional whole-clip normalization.

### 6. Cross-environment results are preserved even if they disagree

**Met — re-derived from the record.** All 32 cross-runtime comparisons are
retained with status `FAIL` and their full unaligned metrics; none was dropped,
tolerated, or converted into a closeness threshold. The superseded initial
profile survives too, under `historical_baseline`, alongside the
`baseline_comparison` transition metrics. The consolidation check refuses a
record whose cross-runtime rows have been deleted, and a negative control in
`tests/test_reference_consolidation.py` proves that refusal fires.

The comparator arm is now historical rather than re-runnable — see [Open
divergences](#open-divergences).

### 7. A decision record names the canonical environment and explicitly bounds portability claims

**Met — read.** DR-0006 is `Accepted`, names `release-mkl-compatible-v1` with
its full package identity, and confines full-matrix qualification to the
measured host in `## Permitted host scope`, stating in terms that native ARM,
GPU, other CPU feature sets, other package versions and other batch sizes are
unqualified, and that a passing native sentinel "permits that job to attest
only its tested global-0 case." DR-0001 now carries the forward pointer to
DR-0006/DR-0007 that it previously lacked.

### 8. CI has a lightweight sentinel that fails on known-reference drift without requiring the full corpus

**Met — verified against the forge.** Both `.github/workflows/reference-repeatability.yml`
and `.github/workflows/reference-scalar.yml` run on `pull_request` and on
`push` to `main`, perform a real render in the pinned image, and compare
committed bytes — `qualify_repeatability.sh sentinel` for the canonical lane,
`qualify_scalar.sh --sentinel --mkl-compatible` plus `qualify_scalar.py verify`
for the scalar lane. Neither renders the corpus. Both were `success` on each of
the six most recent `main` commits through `938915a` (`gh run list --workflow
reference-repeatability.yml --branch main`, and the same for
`reference-scalar.yml`).

These are genuine drift detectors rather than schema checks: the scalar lane
runs three start-red controls (wrong parameter, wrong noise, fresh
randomization) that must each exit 1, and both lanes fail fast if the
four-variable dispatch environment is not pinned. Running the release-era
protocol suite locally in this session —
`python3 -S -m unittest discover -s env/release-era -q` — gave 79 tests `OK`
with `Apparatus PASS; scalar byte equivalence PASS`.

## Corrected during consolidation

Both findings belong to one amendment's incomplete sweep, and DR-0009 carries
the corresponding note in its own record (§"A2, note (the sweep was incomplete;
it is now a check — issue #3)") so the amendment no longer reads as finished.

**A stale companion pin on the ratified publication.** DR-0009 amendment A2
republished `sim/reference/repeatability-runtime.json`; its digest moved from
`5904089505c05c30…` to `611fe2121330a7a4…` at commit `2c4a20f`. Two independent
gates hard-code that digest and refuse to render if it moves, both raising
`"ratified runtime publication changed"`. A2 updated one
(`src/torchsynth_voice/artifact_renderer.py:33`) and missed the other
(`env/release-era/render_artifact.py`), which is the worker that actually runs
inside the container — so the host-side gate admitted the render and the
worker then refused it. A2's own note had drawn exactly this lesson ("a
republish of a digest-pinned reference file must sweep the repository for every
companion pin of that file before merge"); nothing enforced it.

The pin is corrected to the committed publication, and the sweep is now a
check rather than a lesson: `PUBLICATION_PIN_SITES` in
`tools/check_reference_consolidation.py` registers every gate site, CI proves
each one names the live digest, and a scan proves no *unregistered* file gates
the publication behind the same refusal message.

Both completeness scans (this one and `check_dispatch_profile_sites`) skip the
repo-relative prefixes `.git/` and `.loom/`, and both are stated here rather
than left in the code alone. These are matched **relative to the repository
root**, never against the absolute path. A Loom worktree lives at
`<repo>/.loom/worktrees/issue-N`, so an absolute-path test for a `.loom`
component matches every file in such a checkout and turns both scans into
silent no-ops in precisely the environment they are run from most; the
`ScanReachabilityTests` controls plant a synthetic unregistered occurrence and
require refusal from both a plain root and a worktree-shaped one, so a scan
that visits nothing can no longer report a pass. The publication-pin scan
carries one further exclusion beyond those two: it skips
`tools/check_reference_consolidation.py` itself, because the scanner
necessarily contains the refusal message as the constant it searches for, and
naming a sentinel is not gating a render on it. Registering the scanner as a
gate site would be both untrue and unsatisfiable — that arm additionally
requires the site to pin the publication's committed digest as a literal,
which a checker that recomputes that digest does not carry.

`check_dispatch_profile_sites` carries two further exclusions beyond the
shared `.git/`/`.loom/` pair, for a total of four; both are documented, with
rationale, where the scan is described in full below (§"repaired" table and
divergence 4): it exempts test modules (`path.name.startswith("test_")` —
"Test modules are exempt by design: asserting that a pin reaches the
container is their job", :301-303) and the sixth, not-yet-repaired spawn
named in `DISPATCH_RECAPTURE_GATED` (:305-307, :387-388).

Other references to the pre-A2 digest were audited and deliberately left alone,
because they are dated provenance rather than gates: `spec/SIGNAL-PREPARATION.md`
§"Repaired repeatability integration, 2026-09-19" and the
`actual_integrations[*].producer.report_sha256` entries in
`sim/qualification/preparation-v1.json` record which publication those
integrations actually consumed on that date. Rewriting them to the current
digest would claim a consumption that never happened. The distinction the scan
encodes is the operative one: a *gate* must name the live file, a *record* must
name what it saw.

**Six spawns that had drifted from the profile they declare; five repaired.** The same
amendment added `ONEDNN_MAX_CPU_ISA` and `MKL_ENABLE_INSTRUCTIONS` to
`profile_environment["release"]` in the preregistered plan. Two spawn paths
picked that up for free — `env/release-era/qualify_repeatability.py` already
derived its `docker run` environment from the plan, and `qualify_scalar.sh`
forwards whatever the job shell pins (DR-0009 A1) — and A2 accordingly
described its own change as needing "zero code changes".

Every *other* host-side spawn of a release-profile worker restated the pins as
literals (`--env MKL_CBWR=COMPATIBLE` … `env -u ATEN_CPU_CAPABILITY`), so all
six kept describing the pre-amendment two-variable environment while their
worker asserted the four-variable one. The release image sets neither ISA
variable, so each was a render the worker refuses on sight:

| Host spawn | Worker it launches | Refusal | Disposition |
| --- | --- | --- | --- |
| `src/torchsynth_voice/artifact_renderer.py` (`DockerBackend`) | `env/release-era/render_artifact.py` | `worker environment outside explicit profile` | repaired |
| `tools/qualify_trace_artifacts.py` | `env/release-era/render_artifact.py` (via its container driver) | same | repaired |
| `tools/capture_float_sources.py` | `env/release-era/render_artifact.py` (via its container driver) | same | repaired |
| `tools/qualify_mutations_runtime.py` | `env/release-era/mutation_worker.py` | `worker environment outside release-mkl-compatible-v1` | repaired |
| `tools/qualify_trace_capture.py` | `env/release-era/capture_traces.py` | same | repaired |
| `tools/probe_trace_registry.py` | itself, `--worker` | same | **recapture-gated** — [divergence 4](#open-divergences) |

The five repaired spawns now derive those flags from the plan (`--env` for each
declared value, `env -u` for each declared `null`) through one shared helper
(`release_profile_environment` / `dispatch_flags` / `dispatch_unset_flags` in
`src/torchsynth_voice/artifact_renderer.py`), mirroring
`qualify_repeatability.py`'s spawn. The declaration keeps its one home — the
preregistered plan — and `check_dispatch_profile_sites` in
`tools/check_reference_consolidation.py` registers all seven spawn/worker pairs,
proves each spawn derives rather than restates, proves each worker still
asserts the plan, and refuses any *unregistered* non-test module that
hard-codes a declared pin. Test modules are exempt by design: asserting that a
pin reaches the container is their job.

The sixth is not repaired here, and the check does not let that pass silently:
`DISPATCH_RECAPTURE_GATED` names it, requires its producer binding to stay
intact, and requires it to stay named in this record. See divergence 4.

None of these corrections changes a declared value, a committed digest, the
canonical runtime, the source pin or any rendered byte. They changed what each
gate *refers to*, not what it gates. Two consequences follow and are not
papered over:

- Every affected path is host-gated to the DR-0006 measurement host and is not
  exercised by CI, so the first real render on each after this repair is an
  unrecorded execution and must be recorded as one under DR-0006's drift
  policy. Nothing here claims those renders have been performed.
- `env/release-era/render_artifact.py` changing at all moves its own digest,
  which committed receipts carry as `worker_sha256` (`sim/reference/corpus-smoke.json`,
  the `tests/fixtures/float-sources/*/params.json` fixtures, both recording
  `6f12a867…`, the pre-repair digest). The host-side check that binds a receipt
  to the worker compares the live file, so it stays self-consistent; what
  changes is that a *future* corpus run cannot match those receipts'
  `worker_sha256` (which `tools/compare_development_corpus.py` treats as
  receipt-immutable). That is a recapture obligation, not a discrepancy to
  reconcile, and it is unavoidable: the stale pin made the pre-repair worker
  refuse every render, so the digest those receipts name can no longer produce
  one.

## Open divergences

Recorded, not resolved. None of these invalidates an acceptance criterion
above; each is a record-level disagreement that a future amendment should
settle explicitly rather than by erosion.

1. **The scalar receipt's math environment versus the prose of DR-0006/DR-0007.**
   Both records describe the profile as leaving `ATEN_CPU_CAPABILITY` unset,
   and the repeatability plan declares it `null` and enforces that with
   `env -u`. The committed scalar receipt
   (`sim/reference/scalar-execution.json` → `runtime.math_environment`) records
   `ATEN_CPU_CAPABILITY: "AVX2"`, because DR-0009 A1 made
   `qualify_scalar.sh` forward every dispatch variable the job shell sets and
   `.github/workflows/reference-scalar.yml` pins that variable to `AVX2`. The
   two sentinel lanes therefore run the same nominal profile with different
   ATen capability settings. A1's own census measured no byte difference
   between pinned and unpinned on the substrates it tested, so this is a
   declaration gap rather than a measured contradiction — but the declaration
   should be amended to say which it is.

2. **`sim/reference/scalar-execution.json` still reports the reconciliation as
   pending.** Its `runtime_reconciliation` field reads
   `"pending issue 12; no chosen-runtime bridge qualification"`, a literal
   emitted unconditionally by `env/release-era/qualify_scalar.py`. Issue #12 is
   closed and DR-0007 §"Reconciliation with DR-0006 (2026-09-19)" declares the
   cross-check complete with no divergence, so every receipt — including ones
   produced today, and the copy propagated into
   `sim/qualification/preparation-v1.json` — carries a superseded claim.
   Correcting the literal changes the emitted receipt, so it requires a
   sanctioned recapture in the pinned container, not an edit to the committed
   JSON.

3. **The cross-runtime comparator arm is historical, not re-runnable.** The
   preregistered matrix pins the `current` comparator to `uv.lock` at
   `66ce66150ccdffb3…`; `uv.lock` at `938915a` hashes to `0e49bf748c64b8f8…`,
   having moved with ordinary dependency maintenance. This does **not** affect
   the canonical `release` arm, whose lock and Dockerfile still match their
   declared digests exactly, and it fails safe: `qualify_repeatability.py`
   raises `"runtime lock hash mismatch"` rather than comparing against a
   different environment than the one recorded. The retained 32 cross-runtime
   `FAIL` rows remain valid evidence of what was measured; they are simply no
   longer reproducible at `HEAD` without a new recorded experiment, which is
   what DR-0006's drift policy already requires for a changed lock.

4. **`tools/probe_trace_registry.py`'s spawn is still pre-amendment, because
   repairing it requires a recapture.** It is the sixth drifted spawn in
   [Corrected during consolidation](#corrected-during-consolidation) and the one
   left alone. `sim/reference/trace-registry-prototype.json` pins the tool's own
   digest in its `producer_sha256` block (`833da8b5…`), and
   `tests/test_trace_registry.py` re-hashes every producer against the live
   tree, so editing the spawn makes the committed experiment describe a tool
   that did not produce it. The two available moves were both wrong: re-pinning
   the record to a tool that never ran it falsifies evidence, and a recapture
   needs the DR-0006 measurement host (macOS/M5 + Docker), which neither CI nor
   this consolidation has.

   So the code is unchanged and the gap is enforced instead:
   `DISPATCH_RECAPTURE_GATED` in `tools/check_reference_consolidation.py`
   registers the exemption, fails if the producer binding stops matching, and
   fails if this record stops naming the site. The consequence is bounded and
   already true of that path today: the prototype's own re-run is a
   host-gated experiment that has not been performed since DR-0009 A2, and it
   will refuse (`worker environment outside release-mkl-compatible-v1`) until
   the spawn is repaired together with a recapture. No committed byte or digest
   is affected, and nothing downstream reads the prototype's spawn command.

## What this record does not establish

No claim is made here about gf180mcu synthesis, layout, signoff, hardware
playback or sound fidelity; about GPU execution; about batch-1 identity; about
the sealed holdout; or about portability to any host outside DR-0006's
`## Permitted host scope`. Cross-runtime disagreement remains disagreement: it
is retained, not reconciled, and no tolerance, time alignment or level
normalization is introduced anywhere in this record.
