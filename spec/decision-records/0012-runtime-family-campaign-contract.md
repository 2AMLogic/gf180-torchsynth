# DR-0012: Actual-Voice family campaign contract (preregistration)

- Status: Proposed
- Date: 2026-10-09
- Decision owners: 2AM Logic
- Issue: #333 (Part of #300; epic #9)
- Scope: Preregistration only. No runtime measurement, host qualification,
  actual-Voice render, synthesis or signoff is performed or claimed here.

## Context

The identity, timing and signal fault families (#31/#32/#33) and the
bidirectional matrix (#34) are apparatus-domain evidence on synthetic fixtures;
only the `bridge.*` operators have actual-Voice evidence
(`docs/MUTATION-COVERAGE-AUDIT.md`). #300 asks for a fresh actual-Voice
measurement of the family operators. Before adapters (#334, #335, #336) and the
campaign (#337) exist, the choices that decide what counts as a result must be
fixed. This record resolves them; the machine-readable form is
[`spec/reference/runtime-family-campaign-v1.json`](../reference/runtime-family-campaign-v1.json),
its schema is
[`spec/schemas/runtime-family-campaign-v1.schema.json`](../schemas/runtime-family-campaign-v1.schema.json),
and `tools/validate_runtime_family_campaign.py` checks it (non-writing,
stdlib only).

Unchanged and preserved: pinned upstream
`2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`, default nebula, 4 s at 44100 Hz
(441 Hz control), seed-13 noise, `release-mkl-compatible-v1`, and the sealed
holdout (corpus identities 96-127; DR-0011). No target semantics, arithmetic
profile, noise policy, normalization, parameter order or clip timing changes.

Ratification follows the project convention (DR-0006): reviewed merge ratifies
this record. Until then it is Proposed and consumers must not describe it as
ratified; the Builder does not approve it.

## Decisions

### D1. Inventory and dispositions

The manifest freezes 37 inventory entries covering all 44 committed family
fault-matrix rows (identity 7 faults x 2 apparatus cases, timing 13, signal 17;
the identity cases and the two signal property rows are carried as source rows
of their entry). Each entry records stable ID, operators, magnitude and
configuration, the runtime seam and target, the mandatory detector and its
bound statement (verbatim from the family publication), expected event,
applicability, blast radius, invariance assertions and a disposition:

| Disposition | Entries | Counts as a runtime kill |
| --- | --- | --- |
| `runtime_cell` | 25 | yes, only after the ratified reliability rule |
| `composition` | 3 | no; executed and reported separately |
| `refusal` | 3 | no; `timing:missing-sample`, `timing:duplicated-sample` (length-changing faults cannot be applied in place at `voice.post_module`; refused by the frame preflight on retained lane bytes) and `signal:normalization-decision-replacement` (plan-time) |
| `second_detector` | 2 | no extra attempt; second mandatory detector of a shared cell |
| `deferred_unmeasured` | 4 | no; the four `norm.*` faults (D3) |

A separate `sensitivity_inventory` lists the 2 floor probes, 6 coverage rows and
12 normalization cells; none is executed and none counts. The 48 identity
representative-random receipts are `supporting` (apparatus-only). Runtime
evidence is classed `graph_runtime` (injected through `voice.post_module` or
`voice.parameter_value`) or `boundary_runtime` (identity faults applied at
`apparatus.producer_call` to the resolved request/payload of a real render; the
Voice graph is not perturbed). The two classes are always reported separately
and a boundary result never stands for graph perturbation.

Development cases are `global-0` (slot 0), `global-31` (slot 31) and `global-32`
(batch block 1, slot 0), all at batch 32 and all inside the development
partition (0-95). A fault that proves ineffective on a case stays open; it is
never silently dropped. The #297/#298 additions enter only through a declared
manifest revision. The frozen inventory digest makes any silent change a
validator failure.

Planned denominator (a plan, not a result): 75 fault cells, 9 composition
cells, 24 control attempts, 192 worker attempts in 6 fresh worker processes,
7 host-side expected refusals.

### D2. Reliability (ratifies the proposal)

A cell is established only when two independent fresh-process executions
(separate worker processes, fresh Voice per attempt) each pass: the plain,
empty-plan, sham and clean-rerun controls pass and are byte-identical as
specified; exactly the declared events are observed with the expected status
(`applied`, never `ineffective`); every mandatory detector bound to the cell
FAILs on the faulted attempt and PASSes on the controls; invariance holds; and
the two repeats' fault bytes are equal. There is no retry-to-pass. Repetition
reliability is separate from magnitude sensitivity: second-magnitude and
below-floor probes are not part of this denominator and no statistical
detection-rate claim is made. Two repeats establish determinism of detection
under the declared profile, not a population statistic.

### D3. Normalization handoff: deferred, unmeasured

Decision: the `AudioMixer.output` handoff is **not** requested by this record.
The pinned `normalize_if_clipping` (`torchsynth/util.py`, called from
`AudioMixer.output` at `torchsynth/module.py` line 1109 at the pin) has no
observer or replacement hook, and `mixer.peak`/`mixer.gain` are passive
diagnostics. A replacement of `mixer.output` after the fact cannot express a
decision fault. Adding a hook would edit the pinned producer or patch the
release image, which reverses the accepted framework rule that producer and
render-path modules are read-only inputs (`spec/MUTATIONS.md`); that is routed
as authority question AQ-1, not approved here.

If later pursued, the handoff must specify (recorded in the manifest):
decision operands (per-slot pre-normalization output, candidate peak, the
`> 1.0` comparison and the divisor), producer ownership of the hook, passive
original observations captured and retained first with a replacement consumed
by the graph only afterwards, hook cleanup after success, error and partial
setup, and the existing plan-time refusal retained until requalified.

Until then the four `norm.*` entries and the detected normalization cells are
`deferred_unmeasured`, the plan-time refusal stays a refusal, and normalization
remains **unmeasured in the actual Voice**: the #300/#9 obligation stays open.
Completion of this manifest cannot close it.

### D4. Literal `align_corners` toggling is required (as a proposed addition)

Pinned upstream: `ControlRateUpsample` builds
`torch.nn.Upsample(buffer_size, mode="linear", align_corners=True)`
(`torchsynth/module.py` lines 1127-1129, whose file digest matches
`spec/reference/upstream.json`), and `spec/VOICE-CONTRACT.md` and the trace
registry (`i*1763/176399`) agree. `interp.off_endpoint` replaces that lane with
the coordinate `j*(N-1)/M`: a dropped-endpoint fault. It is not a flag toggle,
and its registered summary ("instead of the landed align_corners=False
convention") mis-states the landed convention (upstream is `True`). The
operator remains an executable `runtime_cell`; its summary should be corrected
by the timing owner in a later change (editing the family module here would
invalidate its bound publication).

Decision: a literal `interp.align_corners_false` operator is required for the
campaign to claim `align_corners` coverage. Site: replacement of the declared
slot of a `control_upsample.<destination>` output at `voice.post_module`,
computed as `torch.nn.functional.interpolate(<same invocation's input>,
size=176400, mode="linear", align_corners=False)`; the passive observer
snapshots the original first, the CallTracker association is preserved and the
target semantics are not altered. Detector: time-locked exactness on the
`control_upsample.*` trace against the plain attempt plus the registry endpoint
contract must FAIL. It is recorded under `proposed_additions`, outside the
frozen denominator, pending operator registration and family requalification
(AQ-3, owner #335).

### D5. Host admission: AWS box versus DR-0006

Facts, kept separate. DR-0006 limits full-matrix qualification to its measured
Apple M5 / Docker 29.7.2 host and credits the native CI host (AMD EPYC 9V45)
with the global-0 sentinel only. `AGENTS.md`/`CLAUDE.md` sanction instance
`i-018841ef4169207ba` (Xeon 8175M) for release-era campaign work, but the
repository contains no committed fingerprint, repeatability run or sentinel
receipt for it, and `tools/qualify_mutations_runtime.py` hard-codes the Apple
host (`sw_vers`, `Apple M5`, `linux/arm64 29.7.2`), so it cannot run on that box
today. Sanction is not qualification and a sentinel alone cannot establish
host coverage.

Decision: **no host is admitted by this record.** Admission requires, in the
manifest's `qualification_predicate`: a committed host identity record (Q1); a
complete 128-cell repeatability run on that host, unchanged image/lock/profile,
fresh processes (Q2); 64/64 repeat and 48/48 batch pairs byte-exact and release
artifacts byte-identical to the committed canonical bytes, any difference being
a stop with raw drift metrics and never a tolerance (Q3); a reviewed decision
record extending DR-0006 scope (Q4, AQ-2); a data-driven fingerprint gate
replacing the hard-coded Apple predicate (Q5); and per run a clean tree,
fingerprint equality and a same-run sentinel PASS (Q6). #325 (alternate Apple
host) is a separate path, not an automatic prerequisite. Loom dispatch workers
are not admitted hosts. DR-0006 is neither edited nor reinterpreted.

### D6. Worker, CLI, artifacts, verifier, publication, completion

Recorded in the manifest. The existing producer
(`env/release-era/mutation_worker.py`, launched only through
`tools/qualify_mutations_runtime.py`, schedule `mutation-voice-schedule-v1`,
report `worker.json`) is extended by the adapters; the proposed
`--campaign` option and `tools/verify_runtime_family_campaign.py` do not exist
yet. Pins: base image digest
`python:3.9.13-slim-bullseye@sha256:b3bb5145...`, Dockerfile and lock digests
(equal to the DR-0006 preregistered matrix), source archive digest, profile
environment, image id and layers per run, and a clean-tree gate (a dirty tree
is NO_VERDICT). Attempt order per (case, repeat) process: plain, empty, sham,
faults in manifest order, clean rerun; detectors run offline on retained raw
bytes. Retained artifacts, the offline stdlib verifier, a new publication kind
(`mutation-runtime-family-v1`, proposed) and the completion rule are specified;
historical evidence is preserved unchanged. Completion statuses are
COMPLETE / INCOMPLETE / NO_VERDICT / FAIL; refusals, sensitivity, ineffective
events, deferred entries, compositions and boundary-class results never count
toward graph runtime kills, and completing the manifest closes only its scope.

## Authority questions (not approved here)

- **AQ-1**: allow a scoped hook in the pinned `AudioMixer.output` (or a patched
  release image) for normalization injection. Interim: deferred, obligation open.
- **AQ-2**: admit the AWS box as an additional DR-0006 host and at what scope.
  Interim: not admitted.
- **AQ-3**: register `interp.align_corners_false` and requalify the timing
  family. Interim: proposed addition, outside the denominator.

## Downstream staleness (do not repair by editing hashes)

`spec/MUTATIONS.md` links this contract. It is a member of `INPUT_PATHS` in
`tools/qualify_mutations_matrix.py`, so `sim/reference/mutation-matrix-v1.json`
binds its previous digest. That matrix check already fails on main
(`FAIL: committed publication is stale or drifted: inputs`, recorded in the
audit and owned by #314, with check-first CI under #257). The bound digest for
`spec/MUTATIONS.md` already differed from `main` before this edit (checked by
hashing `main:spec/MUTATIONS.md` against the publication's `inputs`), so this
edit adds no new failure class but leaves the matrix digest stale until #314 rebinds
and regenerates it through the matrix tool. No publication hash is hand-edited
here. The three family publications and the runtime publication do not bind
`spec/MUTATIONS.md` and are not affected. The manifest deliberately does not
bind `spec/MUTATIONS.md`.

## What this record does not claim

No actual-Voice family result, no host qualification, no normalization
coverage, no literal `align_corners` coverage, no closure of #300 or #9, no
sound-fidelity, synthesis, layout or signoff claim. The validator's PASS means
the preregistration is internally consistent and reconciled with the committed
publications.
