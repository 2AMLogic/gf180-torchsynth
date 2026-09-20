# DR-0009: CI runner-pool reproducibility envelope for committed reference bytes

- Status: Proposed (operator pre-approved landing; ratification pending reviewed merge)
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
