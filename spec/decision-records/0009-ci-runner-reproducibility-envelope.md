# DR-0009: CI runner-pool reproducibility envelope for committed reference bytes

- Status: Proposed (operator pre-approved landing; ratification pending reviewed
  merge). Amendment A1 (2026-09-24, issue #151) is recorded below and lands
  with this record's ratification.
- Date: 2026-09-20
- Decision owners: 2AM Logic
- Scope: exactly the CI jobs that compare committed reference bytes — the
  scalar sentinel's committed-seam compare
  (`.github/workflows/reference-scalar.yml:38-39`) and the repeatability
  sentinel (`.github/workflows/reference-repeatability.yml`) — on their named
  runner pool. It declares which byte regions those comparisons assert as
  platform-invariant versus platform-variable. This record does not change the
  canonical runtime (DR-0006, Accepted), any committed digest, the drift
  policy's qualification gates
  (`spec/decision-records/0006-canonical-runtime.md:187`), or the fixed-point
  numeric contract (DR-0008). Landing tracked by issue #138.

This record is written against the evidence trail of this session: #127's
landed job-env pins (`.github/workflows/reference-scalar.yml:16-20`,
`.github/workflows/reference-repeatability.yml:15-19`), #117's scoped-fix
ruling, #128's judge forensics ("the committed seam bytes' reproducibility
envelope on the `ci.yml` `ubuntu-22.04` runner pool needs its own decision
record"), #135's ISA-dependent repr set (declared, never silently tolerated),
and the #133/#135/#137 `-numerical` job pattern
(`.github/workflows/mutations.yml:43-47`, `:63-89`, `:91-117`). The
declared-guarantee comparison pattern those PRs landed for the signal and
timing publications (commit `2cfeedf7` — the squash merge of PR #135 into
`main`; developed at branch head `5160a5d`, not itself an ancestor of
`main` — `tests/test_mutations_signal.py`) is the mechanism adopted here, and the DR-0008
Choice Register's "choices are data" discipline
(`spec/decision-records/0008-fixed-point-numeric-contract.md:293-302`) is the
bookkeeping form.

## Problem

The CI committed-byte comparisons recompute a reference render and compare its
bytes against committed digests. Those bytes depend on host-CPU dispatch:
ATen, oneDNN and MKL select instruction-set paths from the runner CPU
(AVX-512 vs AVX2 vs baseline), and libm transcendental implementations vary
across CPU generations and container substrates. Different dispatch tiers
round differently at the float32 last ulp, so transcendental-heavy regions of
a reference render (oscillator phase, exp/log paths, spectral estimates) can
produce different float32 reprs on different machines of the same runner pool
— independent of the canonical runtime environment, which pins the software
versions, thread counts and the `release-mkl-compatible-v1` dispatch tier
(`spec/decision-records/0006-canonical-runtime.md:17-18`).

#127 pinned the four-variable job-env set
(`ATEN_CPU_CAPABILITY`, `MKL_CBWR`, `ONEDNN_MAX_CPU_ISA`,
`MKL_ENABLE_INSTRUCTIONS`) into both sentinel workflows, with a fail-fast
guard. The drift events continued anyway, with the pins verified active in the
failing logs: the process-environment pins do not fully control the residual
runner-environment variance beneath them (host CPU generation, container
glibc/libm substrate). The observed signature is consistent throughout: an
in-run byte-equivalence gate that PASSES while only the committed-byte compare
fails — per-machine self-consistent, cross-machine divergent.

The root defect is not the pins and not the sentinels; it is that a committed
digest is asserted byte-exact over byte regions whose reproducibility on the
named runner pool was never measured or declared. The committed seam bytes
assert more than the runner pool deterministically delivers, and the
reproducibility envelope — which byte regions are platform-invariant versus
platform-variable, and what each class guarantees — is undeclared. Undeclared,
the envelope can only be discovered by production drift events, each costing a
red check, a forensics pass and an exoneration.

## Decision (proposed)

1. **Region classes declared, asserted accordingly.** CI jobs comparing
   committed reference bytes keep the #127 job-env set unchanged, and every
   such comparison declares which byte regions are:
   - **Platform-invariant** — asserted byte-exact against the committed
     bytes, as today. These are the regions the pinned runtime stack renders
     identically across the runner pool (observed: parameter arrays, noise
     selection, labels, and the large majority of rendered samples).
   - **Platform-variable** — asserted by a **declared guarantee** instead of
     byte equality: the value's format, and its crossing of declared
     thresholds, exactly as the landed signal/timing publication pattern
     asserts (row identity byte-exact; trip verdict, declared-offset match,
     finite deviation beyond the trip threshold, and identity-format digests
     asserted; ISA-dependent reprs declared, never tolerated silently). Each
     platform-variable region is named with the mechanism that makes it
     variable (ATen/oneDNN/MKL ISA path or libm transcendental implementation).
   The envelope census itself is measurable evidence: a region is classified
   platform-variable only with recorded cross-runner disagreement, and
   platform-invariant regions stay byte-exact.
2. **Envelope changes are DR amendments.** Declaring a new reference-region
   class, reclassifying a region, or widening any declared guarantee requires
   an amendment to this record before the affected comparison changes. No
   comparison may relax a byte-exact assertion to a declared guarantee, or add
   a carve-out, silently.
3. **The runner pool is named.** This envelope is declared for the
   `ubuntu-22.04` pool of the reference-scalar and reference-repeatability
   workflows, per #128's forensics. Any other pool (a runner-image bump, a
   different label) is outside the declared envelope until measured and
   amended into this record.
4. **Drift events append to the evidence log.** A drift event — a committed-byte
   compare failure whose in-run gates pass — follows the established
   forensics-plus-green-rerun exoneration precedent (used for PRs #103, #116,
   #121 and #128) and **must** be appended to this record's evidence log with
   run URL, head, pin state and forensics link. The evidence log is the
   envelope's measurement history; it is never pruned to make the record look
   cleaner.
5. **Local evidence is not the arbiter.** Local renders (including the macOS
   development host) are not evidence about this envelope and never substitute
   for a CI arbiter run on the named pool. The declared classes are updated
   only from the named pool's own runs.

## Consequences

- The sentinel jobs stop consuming the runner pool's uncontrolled variance as
  if it were a regression: a platform-variable region's declared-guarantee
  check still detects real mutations (format change, threshold non-crossing)
  while no longer failing on host-CPU last-ulp drift. Platform-invariant
  regions keep their byte-exact property, so the negative controls and
  mutation detection on those regions are unchanged.
- Drift events on declared platform-variable regions stop being false
  alarms; a drift event inside a platform-invariant region remains a
  qualification failure under DR-0006's drift policy and now also identifies
  an envelope-classification defect to be amended here.
- The evidence log grows monotonically; each entry is dated, linked and never
  rewritten. The log, not memory, is the record of the pool's behavior.
- The census work (classifying every compared region) is bounded and
  mechanical, and can land region-by-region: unclassified regions default to
  platform-invariant (byte-exact, as today), so this record never loosens an
  existing assertion by default.
- Nothing here touches the canonical runtime, the committed bytes, the
  release-era qualification matrix, or any consumer of DR-0006/DR-0007/DR-0008.
  The pinned-runtime renders themselves stay governed by DR-0006; this record
  governs only what the CI comparison of those bytes asserts across the pool.
- Ladder boundary: the platform-variable declared-guarantee region applies
  only to the two sentinel workflows' committed-byte comparisons (Level-1
  evidence plumbing). It never reaches the float→fixed preregistered metrics
  (DR-0008) or Level-4/RTL, which remain sample-exact and are out of this
  envelope's scope.

## Alternatives considered

- **Full float64 reference storage.** Storing (and comparing) reference
  renders in float64 would move the ulp boundary far out of reach. Rejected:
  the sentinels exist to provide Level-1 float32 repeatability evidence for
  the canonical CPU float32 profile (DR-0006); float64 references measure a
  different quantity, destroy that evidence class, and still do not remove
  host-dispatch dependence — they only move it.
- **Skipping committed-byte compares.** Dropping the cross-machine comparison
  and keeping only in-run gates. Rejected for the same reason plus one more:
  the committed-byte compare is the only cross-machine drift detector this
  repository has; it is how #117/#124 root-caused the dispatch variance in the
  first place. Removing it converts a declared envelope problem into an
  unobserved one.
- **Per-runner-class committed bytes** (commit a digest per machine class,
  compare within class, record the class in provenance). Deferred, not chosen:
  it multiplies committed artifacts by pool variance and still requires the
  same region census to say what the per-class bytes guarantee. Revisit only if
  the declared-guarantee pattern proves insufficient in practice.
- **Producer-container pinning for the sentinel jobs** (the #128 forensics
  suggestion: run the sentinel inside the same hash-pinned producer container
  the scheduled workflow uses). Compatible with this record and not precluded
  by it: it would shrink the platform-variable set, and any such change still
  needs this record's amendment path (Decision 2) because it changes what the
  comparison asserts. It is an implementation option for the envelope's owner,
  not part of this decision.

## Open questions for the operator (none blocking ratification)

- **Census depth.** Whether the initial region census should enumerate every
  compared field of every sentinel case in one amendment, or proceed
  region-by-region as drift events measure them. Default: region-by-region,
  seeded by the evidence log below — each real event measures exactly one
  region, so the census costs nothing extra. Not blocking: either path uses
  the same amendment mechanism.
- **Producer-container option.** Whether to adopt the #128
  producer-container-pinning suggestion for the sentinel jobs. Default: not
  now; the declared-guarantee pattern is sufficient and the container pinning
  can be adopted later through this record's amendment path. Not blocking: the
  envelope declaration is orthogonal to the container choice.

## Evidence log (seeded 2026-09-20; appended per Decision 4)

All entries: in-run gates passing, green on rerun at the identical head
unless noted. Scalar-side entries fail with `ValueError: sentinel drift:
global-6 canonical` from the committed-seam compare step; the
repeatability-side entry (09-20 11:21) fails with its own signature,
`ValueError: sentinel artifact hash mismatch` from `check_sentinel`
(`env/release-era/qualify_repeatability.py:940`). Dates 2026.

| Date (UTC) | Run | Head | Pins | Exoneration |
| --- | --- | --- | --- | --- |
| 09-19 20:10 | [35466547891](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/35466547891) | `main` | pre-#127 | rerun green |
| 09-19 (PR #103) | [35461682861](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/35461682861/attempts/1) | `f189a41d` | pre-#127 | attempt 2 green, same head (#117) |
| 09-19 (PR #116) | [35465234583](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/35465234583/job/105956240960) | `f3d9dc95` | pre-#127 | rerun job 105960062341 green, no code change |
| 09-19 22:12 + 22:17 (PR #121) | [35472655716](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/35472655716) | PR branch | pre-#127 | forensics in #124; drift on both canonical and scalar sides |
| 09-20 00:13 (PR #128) | [35478180904](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/35478180904) | `de23508` | pre-#127 | #128 forensics; green rerun |
| 09-20 00:23 (PR #128) | (see #128 forensics) | `545120b` | **active, verified in log** | #128 forensics; green rerun |
| 09-20 05:09 | [35490965833](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/35490965833) | `b3ef9cc`, `main` | active (post-#127) | signature spot-verified |
| 09-20 07:15 | [35496359125](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/35496359125) | `c91a193`, `main` | active (post-#127) | — |
| 09-20 08:00 | [35498376775](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/35498376775) | `bc3fa64`, PR branch (pull_request, `feature/issue-32`) | active (post-#127) | — |
| 09-20 10:28 | [35505181070](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/35505181070) | `def62b3`, `main` | active (post-#127) | signature spot-verified |
| 09-20 10:50 | [35506161396](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/35506161396) | `8b24352`, PR branch (pull_request, `feature/issue-33`) | active (post-#127) | signature spot-verified |
| 09-20 11:21 (repeatability side) | [35507583628](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/35507583628) | `2cfeedf7`, `main` | active (post-#127) | next `main` repeatability run green (`2245e9e`) |
| 09-20 12:00 | [35509410280](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/35509410280) | `1629c67`, PR branch (pull_request, `feature/issue-34`) | active (post-#127) | signature spot-verified |

The 2026-09-19 occurrences established the dispatch-variance signature and
produced #127's pins; the 2026-09-20 occurrences — three `main` events and
three PR-branch (pull_request) runs on the same pool, labeled as such above —
all with the pins verified active, establish that the pins are necessary but
not sufficient on both sentinel workflows (scalar committed-seam and
repeatability sides): the direct motivation for this record.

Amendment A1 (2026-09-24) supersedes the "pins verified active in the
failing logs" interpretation: those pins were active in the job shell, and
that was the whole of their effect — see the root-cause refinement below,
whose evidence log entries follow the table.

## Amendment A1 (2026-09-24, issue #151)

### Root-cause refinement

The #127 pins set the four dispatch variables in the `reference-scalar`
job shell (`.github/workflows/reference-scalar.yml:16-20`), but the render
container never received them. The job builds the pinned linux/amd64 image
and runs every render inside it
(`env/release-era/qualify_scalar.sh`), and the wrapper forwarded into the
container only `MKL_CBWR=COMPATIBLE`, and only when invoked with
`--mkl-compatible`; `ATEN_CPU_CAPABILITY`, `MKL_ENABLE_INSTRUCTIONS` and
`ONEDNN_MAX_CPU_ISA` stopped at the shell. The committed receipt's runtime
record confirms it: `runtime/math_environment` shows
`ATEN_CPU_CAPABILITY: null`, `MKL_ENABLE_INSTRUCTIONS: null`,
`ONEDNN_MAX_CPU_ISA: null` for the 2026-09-21 capture — "pins active" in the
job log was always "pins active where they had no effect." The same capture
was also taken on a divergent substrate (macOS arm64 host, `linux/amd64`
under Rosetta, MKL `MKL_NATIVE`), so the runner-pool machines (native x86,
native MKL dispatch) had no dispatch-tier agreement with the committed bytes
to hold against; the drift followed the transcendental-heavy seams exactly as
this record's Problem section predicts (23 seam regions on `global-6`, 6 on
`sine-bypass`, 6 on `noise-applied`; every other compared region byte-identical
in the same runs).

With the pins binding the tier inside the container, the pre-#151 receipt was
simply the wrong reference: an under-pinned capture from an unratified-for-pool
substrate, compared byte-exact against a pool whose tier was never pinned to
match it.

### Fix (assertion mechanics and evidence only)

1. The wrapper forwards every set variable of the declared runtime's dispatch
   set into the render container and appends `MKL_CBWR=COMPATIBLE` for the
   `--mkl-compatible` profile; a runtime that does not set a pin is left
   byte-for-byte as declared, with the unset values recorded as `null` in
   `runtime/math_environment` (`env/release-era/qualify_scalar.sh`). An
   undeclared runtime therefore cannot impersonate a pinned one: the receipt
   comparison already bounds the whole `math_environment` object, and a
   pin-less capture now fails verify explicitly instead of drifting.
2. The committed sentinel receipt is recaptured in full scope (all 12
   preregistered cases, both fresh-process repeats, three start-red controls)
   under the enforced `release-mkl-compatible-v1` pins, single threaded,
in the digest-pinned linux/amd64 image, on a native x86 host; the capture
   carries a `recapture` annotation preserving the replaced receipt's
   identity (issue #151, `sim/reference/scalar-execution.json`).
3. The declared-envelope mechanism of Decision 1 is implemented and controlled:
   `spec/reference/scalar-envelope-v1.json` declares the census; regions not
   named in it assert byte-exact as today (Decision 1's default); a named
   region asserts the `declared-bounded-deviation` guarantee — little-endian
   binary32 format, declared count, sample finiteness, and per-sample absolute
   deviation from the committed envelope-reference bytes bounded by a declared
   maximum — with the reference bytes digest-bound to the declaration so a
   bound is never re-anchored silently (issue 151,
   `env/release-era/qualify_scalar.py:939-1043`). The stdlib controls exercise
   the declared path end to end, including refusal cases, without any docker
   or TorchSynth import (`env/release-era/test_qualify_scalar.py`).
4. The workflow's verify step is unchanged in invocation
   (`.github/workflows/reference-scalar.yml:38-39`); it now reads the
   committed census and the committed receipt, both of which land with this
   amendment. One line of the wrapper (`--progress plain`) was removed so the
   same script runs unmodified on the CI pool's BuildKit docker and on a stock
   Ubuntu `docker.io` legacy builder; the change is log cosmetics only.

No target, arithmetic profile, normalization, parameter ordering, or clip
timing changes: this amendment changes what the CI comparison asserts (and how
the reference capture is produced), not what the float target is.

### First census: no platform-variable region measured

Measured under the enforced pins (all comparisons: run-1 sentinel bytes, both
process sides, 216 f32le files per comparison):

- x86 AWS instance (Intel Xeon 8259CL, Ice Lake, native AVX2, native MKL
  build) versus the same host under the same pins in a second independent
  process campaign: 0 of 216 files differ (byte-exact, cross-campaign).
- The same pinned x86 capture versus an M-series macOS host running the
  identical pinned linux/amd64 image under Rosetta (AVX2-emulated, MKL native
  build): 0 of 216 files differ; maximum absolute sample deviation 0.0.
- Same host, pins unset versus pins enforced, both `release-mkl-compatible-v1`
  profiles: 0 of 216 files differ — on this substrate the pins bind the tier
  the hardware already dispatches rather than changing it; their force is that
  they bind it.

Every compared sentinel region is therefore platform-invariant under the
enforced pins, and the committed census is `regions: {}` — the Decision 1
default, asserted byte-exact, with nothing relaxed. Disagreement was observed
only between the pool runs and the pre-amendment under-pinned receipt, which
is the root cause corrected above, not a region-class measurement. Per
Decision 5 the named pool's own first measurement arrives with the merged
pull request's CI runs; any pool event that measures a disagreement inside a
currently byte-exact region classifies it in a further amendment with
committed reference bytes (Decisions 1 and 2), never silently.

### Evidence log additions (Decision 4)

All entries: in-run gates passing, green on rerun at the identical head
unless noted. The 09-21/09-22 scalar entries failed
`ValueError: sentinel drift: global-6 canonical` from the committed-seam
compare with the pins active in the job shell and unset in the container —
the A1 root cause — and each captured run directory shows the
transcendental-heavy seam signature with every other region byte-identical.
The repeatability-side entries are logged for the evidence log's completeness;
the repeatability lane's own fix is a sibling of issue #151, not part of it.
Dates 2026.

| Date (UTC) | Run | Head | Pins | Exoneration |
| --- | --- | --- | --- | --- |
| 09-21 | [35552390494](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/35552390494) | `main` | job-shell active; container unset (A1) | root-caused by issue #151 (this amendment); no code change needed for the run itself |
| 09-21 | [35557299034](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/35557299034) | `main` | job-shell active; container unset (A1) | same signature; same root cause |
| 09-22 | [35692233752](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/35692233752) | `main` | job-shell active; container unset (A1) | 23/6/6 seam-drift set recorded from the captured run directory; root cause A1 |
| 09-22 | [35788572974](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/35788572974) | renovate PR branch | job-shell active; container unset (A1) | provenance-compare refusal, not sentinel drift: the branch changes a pinned definition file, so the branch's fresh provenance cannot equal the committed receipt's — the binding working as designed until a receipt regeneration lands on that branch |
| 09-22 22:41 (repeatability side) | [35793687982](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/35793687982) | `main` | repeat-lane pins (sibling lane) | logged for completeness; sibling of issue #151 |
| 09-23 (repeatability side) | [35914237994](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/35914237994) | `feature/issue-75` PR branch | repeat-lane pins (sibling lane) | logged for completeness; sibling of issue #151 |

### Follow-ups noted, out of this amendment's scope

- The repeatability sentinel's committed-artifact compare has the same
  structure (an un-pinned capture bound into the compare) and is deferred as
  a sibling of issue #151; it amends this record through the same path when
  it lands.
- `spec/reference/scalar-envelope-v1.json` is a versioned census (a `-v1`
  file), matching the repository's reference-publication convention; future
  reclassifications amend it here first (Decision 2).
- Pinning the render image's identity into the workflow (the receipt already
  records the per-capture `image_id`; the image content follows the
  digest-pinned Dockerfile and the committed `env/release-era/` tree) is a
  possible further hardening and would need its own amendment step; not
  required by, and not claimed by, this record.

## Ratification

The decision is submitted for Loom review together with its seeded evidence
log. Reviewed merge ratifies this record; the Builder does not approve or
merge its own PR. Until then the envelope is proposed: comparisons keep their
current behavior (byte-exact assertions unchanged), and no platform-variable
carve-out is implemented on the strength of a Proposed record. Consumers must
not describe this record as already ratified.

## Citation basis

Repository citations use `path:line` form verified against `origin/main`
(run URL, head and pin-state claims verified against the Actions log and the
cited issues/comments in this session, 2026-09-20). This record makes **no**
gf180mcu synthesis, layout, signoff, hardware playback, or sound-fidelity
claim, and no claim that the declared envelope makes the committed bytes
host-portable: it bounds what the named pool's comparisons assert, nothing
more.

## Amendment A2 (2026-09-24, issue #177) — the second sentinel workflow

### Root cause (measured)

`Reference repeatability` failed on `main` at `bea7564` (run 35951522637,
2026-09-24T03:27Z) with seam byte divergence on the full-matrix
`baseline_drift` report. The render ran on a pool host reporting
**Intel Xeon Platinum 8573C (Emerald Rapids)** — an AVX512/AMX-class
generation — while the frozen 128-cell baseline under audit was captured on a
\<= AVX2 substrate. The sentinel renders inside the same pinned-image model as
the scalar flow, but its container dispatch environment is not the job shell:
it is the *active plan*. `env/release-era/repeatability-matrix.json`
`profile_environment["release"]` declared exactly
`{"MKL_CBWR": "COMPATIBLE", "ATEN_CPU_CAPABILITY": null}`, and the spawn path
forwarded that generically into the container
(`qualify_repeatability.py` — `--env k=v` for non-null values, `env -u k` for
nulls, exact-assertion on the worker side). The red run's own receipt confirms
it: `worker.runtime.math_environment = {"ATEN_CPU_CAPABILITY": null,
"MKL_CBWR": "COMPATIBLE"}` — **no ISA-layer pin ever reached the container**.
OneDNN/MKL then dispatched beyond AVX2 on that host and the math-heavy seam
cells diverged from the <= AVX2-era base. This is the same disease as A1 on the
scalar side, and the class this record anticipated on 2026-09-20: "the pins
are necessary but not sufficient on both sentinel workflows (scalar
committed-seam and repeatability sides)."

### Fix (declared profile; zero code changes)

`profile_environment["release"]` gains the canonical AVX2 tier pins, matching
the tier established by `release-mkl-compatible-v1` and the job-shell
`env:` block of `reference-repeatability.yml`:

```json
"ONEDNN_MAX_CPU_ISA": "AVX2",
"MKL_ENABLE_INSTRUCTIONS": "AVX2"
```

The spawn/receipt/worker-assertion machinery is untouched; after the
amendment, the shell, the plan, and the container describe the same
four-variable environment on all three layers. `null` for
`ATEN_CPU_CAPABILITY` is unchanged (the tier is pinned through the ISA-layer
variables; the ATen capability selector stays at the image default, recorded
as `null`, exactly as declared).

### Cross-substrate evidence (2026-09-24, native-x86 AWS instance)

A differential campaign ran the pinned-image sentinel on one physical host —
Intel Xeon Platinum 8175M (Skylake-SP, AVX512F-present, no AMX), native
`linux/amd64` — rendering the same pinned cell (release, batch 32, repeat 1,
case `global-0`) under (pre) the original plan and (post) the amended plan:

| quantity | pre (original plan) | post (amended plan) |
|---|---|---|
| `input_sha256` | `3e96e35f…4fb175649a` | identical |
| `label_byte_hex` | `01` | identical |
| 12/12 artifact `sha256` (adsr_1/2, audio, lfo_1/2, noise, normalized, physical, pre_normalization, vco_1/2) | equal to the frozen expected block | **byte-identical to pre** |
| 78/78 normalized parameter values | equal to the frozen baseline | **bit-equal to pre** |
| `plan_sha256` | `c4acd050…c00614ab` | `8510b636…aa690cd20a1d` (amended plan, repo `plan_hash` function; matches the post cell's own recorded hash) |

The pre-amendment run against the frozen baseline was **PASS** on this host
while the pool's pre-amendment run on the 8573C host was red (run
35951522637): pre-amendment byte-stability was host-generation-dependent, which
is precisely the drift class this sentinel exists to bound. The post-amendment
render is byte-identical to the pre-amendment render on a <=AVX2-dispatch
substrate (the pins select the kernel that substrate already used), and on
>AVX2-dispatch substrates the pins *force* the <=AVX2 path by construction.
The PR's own `Reference repeatability` CI run on the pool (8573C-class
generation) is the recorded cross-host confirmation for the >AVX2 case.

### Sentinel expectation re-pin (mechanical, the machinery's own prescription)

`check_sentinel` (`qualify_repeatability.py`) binds the current plan to the
frozen expectation by `plan_sha256`, so an authorized plan amendment
re-registers the expectation from a sanctioned substrate run. The frozen
record `sim/reference/repeatability-runtime.json` top-level `sentinel` block
moves `plan_sha256` from `c4acd0500515a7cf45c1b84bf7a3bf4364925ef78c900937b1ca4184c00614ab`
to `8510b636050fd1a30e07cd383849445679d1d7b481ccf22abf37aa690cd20a1d`; its other three fields are unchanged, and the campaign measures them
unchanged. The historical 128-cell raw matrix and its digests
(`baseline_plan_sha256`, `baseline_raw_report_sha256`) are untouched: the
amendment declares the container *environment*, not the experiment — every
other plan field (cases, seeds, shapes, dtype, threads, comparisons) is
invariant. No synthetic re-capture of the historical matrix is performed or
claimed.

### Ratification

As under A1: reviewed merge ratifies this amendment; until then it is
Proposed and no consumer may treat the amended tier as ratifying on
>AVX2-dispatch substrates. As under the record's Decision 2, this record
makes no synthesis, layout, signoff, or playback claims.

### A2, note (companion republish pin)

Amendment A2's republish of `sim/reference/repeatability-runtime.json`
moved the whole-file digest that the artifact renderer pins at
`src/torchsynth_voice/artifact_renderer.py:33` (`QUALIFICATION_SHA256`,
the "ratified runtime publication" gate). The first republish left that
companion pin behind, which the `Trace artifacts` job caught on main (run
36044935976 at `2c4a20f`; `qualification()` -> "ratified runtime
publication changed"). The pin is updated here to `611fe2121330a7a467ffab1deade50e59f6dd0ea25d4da86d129b9b501fa9cc3`
(the digest of the A2 publication). Lesson: a republish of a
digest-pinned reference file must sweep the repository for every companion
pin of that file before merge (the sentinel re-pin, the renderer
publication gate, and any contract-mifest pin are distinct).

### A2, note (recapture practice)

The scalar sentinel receipt recapture must be (a) at the
`full-preregistered-cases` scope required by the protocol-refusal tests,
and (b) taken from the **current** main tree, i.e. after this amendment's
re-pin of the runtime publication, since the receipt's provenance and the
protocol gate both track the current tree. The initial recapture (#179),
being sentinel-scoped and taken from a pre-A2 parent, is superseded by
the full-scope republish from `d664805` carried here (replaced receipt:
source tree `89aadc21`, file sha256 recorded in the receipt's
`recapture` block).
