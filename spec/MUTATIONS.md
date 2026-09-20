# Mutation framework v1

## Scope and ownership

This is the sole fault-injection seam framework for the qualification
apparatus (#30). It provides named, composable, versioned mutation plans over
apparatus-owned seams so that downstream tooling refusals and NO VERDICT paths
are exercised as tests. It owns:
`src/torchsynth_voice/mutations.py`,
`src/torchsynth_voice/mutation_runtime.py`,
`tests/test_mutations.py`, `tools/qualify_mutations.py`,
`tools/qualify_mutations_runtime.py`, `env/release-era/mutation_worker.py`,
the seam overlay `spec/reference/mutation-seams-v1.json`, this document, the
bounded publications `sim/reference/mutation-framework-v1.json` (apparatus)
and `sim/reference/mutation-runtime-v1.json` (actual Voice), and
`.github/workflows/mutations.yml`.

It does not edit any producer or render-path module, does not fork #22's
trace registry or #23's capture implementation, does not make any Voice graph
value writable, and never edits a landed validator to make a fault pass.
#31/#32/#33 register fault-operator families through this public API in their
own files; #34 consumes plans, events and raw evidence.

## Non-perturbation and layering

- Seams live only in the apparatus/test layer (`mutation_runtime` harness
  callables and the capture-descriptor inventory presented to the landed
  validator). Producer, renderer, storage and scorecard modules are read-only
  inputs; zero producer/render-path modifications is a review gate for every
  change here.
- #23's capture remains strictly passive: its observers return `None` and are
  never mutators. The apparatus may fault the descriptor inventory *presented
  to* `trace_capture.validate_selected_capture`; it never wraps or replaces
  graph code.
- A passive or derived observation is not an executable seam: the catalog
  marks the derived evidence-digest step non-writable, and a plan declaring a
  mutation there fails closed at registration.

## Voice runtime bridge

Two voice seams are declared writable: `voice.post_module` (replacement of
one declared batch slot of one registry-named module output) and
`voice.parameter_value` (scoped by-reference replacement of one inventory-
named parameter slot). The normalization decision inside
`torchsynth.util.normalize_if_clipping` is declared non-writable: the exact
site has no observer/replacement hook, and the captured `mixer.peak` and
derived `mixer.gain` are strictly passive diagnostics. A plan declaring a
mutation there fails closed naming the required `AudioMixer.output` producer
handoff; it is never substituted by a convenient later edit.

The voice-runtime execution surface is split by contract and mechanics:

- `mutation_runtime.resolve_voice_plan` is stdlib host code. It validates the
  declared mutations against the landed #22 registry (call association comes
  from the registry's own producer binding, never from registration order),
  refuses unknown traces/parameters, passive or multi-output observations,
  slots outside the pinned batch, and module mutations declared before
  parameter mutations (graph causality), and emits a JSON
  `mutation-voice-schedule-v1`.
- `env/release-era/mutation_worker.py` executes the schedule inside the
  pinned DR-0006 release image (Python 3.9, Torch 1.12.1, pinned source).
  Passive #23 observers are installed first and stay the sole capture owner;
  replacement hooks are installed after them in one defined order. The
  replacement is applied in place at the seam value — the same tensor object
  keeps flowing to consumers, so the landed #22 CallTracker
  producer->consumer argument association stays intact — while the data is
  replaced after the observer's pre-mutation snapshot: the captured inventory
  keeps the original bytes, downstream modules and the returned audio consume
  the replacement. Parameter swaps are hosted at the keyboard anchor seam:
  the pinned producer re-randomizes every parameter at the start of each
  render, so the swap is applied in the first registry call's hook — after
  the producer's own initialization, before any later module read — by
  reference and restored; the keyboard module's own reads require a producer
  handoff. Every
  application is an ordered in-band event with original/replacement slot
  digests; a declared raise records the errored event and re-raises; all
  hooks and swaps are removed after success, render errors and partial setup
  failure.
- The host tool `tools/qualify_mutations_runtime.py` re-validates every event
  log against its plan with the landed contract, binds `mu1-` evidence
  envelopes, and writes the bounded publication. `--check-publication`
  re-verifies the committed publication (plans, schedules, events, envelope
  identities, input digests) with stdlib only.

The `bridge.*` operators are #30-owned test-only proofs of this bridge; they
are never published as #31/#32/#33 family qualification.

## Contracts

- **Registration.** Operators are explicit trusted code registered in
  `mutations.OPERATORS` (or via `register_operator`), binding id/version,
  supported seam, typed magnitude domain with native units, configuration
  schema, composition permissions and a summary. Plan text is never evaluated
  as code.
- **Plan.** `mp1-` identity over a domain-separated canonical encoding of the
  versioned plan core (source binding plus the ordered mutation instances).
  Unknown operator/version/seam, operator/seam mismatch, non-writable seams,
  boolean or non-finite or out-of-domain magnitudes, missing or wrong units,
  duplicate instance IDs, tampered plan identities and undeclared
  combinations fail closed before execution. Unknown seams name the required
  producer handoff instead of substituting a convenient edit.
- **Events.** Every application is recorded in-band as an ordered event with
  status `applied`/`ineffective`/`errored`/`refused` and a detail object.
  Seam exceptions are recorded as `errored` events naming the error type and
  then re-raised; nothing is silently swallowed. Completion refuses on
  missing, extra, duplicate or reordered events, so a fault that never ran is
  never evidence, and an impossible cross-seam declared order (for example a
  receipt drop ordered before the partial write that precedes it) is refused
  at completion because observed execution order must equal declared order.
- **Evidence.** The `mu1-` envelope is hashed externally over its own
  domain-separated canonical core with the identity field excluded (no
  self-hash cycle) and binds the plan identity, event summary, artifact byte
  digests/sizes, controls and the fault matrix. `mu1-` attempt artifact
  identities and `mp1-` plan identities can never collide with ordinary
  `ra1-` render artifacts or `cm1-` measurements, even on identical payload
  bytes. Sham plans keep distinct provenance from non-sham plans even when
  every payload byte matches the baseline.

## Controls and localization

Three controls are qualified: the ordinary baseline attempt, the framework
installed with an empty plan, and a registered sham plan that traverses
dispatch and returns the original value. All three must be byte-identical in
stored artifacts and receipts; the sham is marked sham, records
`ineffective` events, and can never count as a detected fault. A fault that
changes nothing on an inactive fixture is reported `ineffective`, never as
detector success.

Localization is proven across at least three distinct seam boundaries
(producer call, artifact write, receipt append, capture inventory): each
fault's event log localizes to exactly its declared seam, the fault's blast
radius stays inside that seam's data (a corrupted store keeps the original
receipt digest; a rebound receipt keeps the original stored bytes), and
untouched sibling-case bytes are unaffected. Plan identity changes when the
magnitude, operator or selection changes, even when outputs coincide.

## Cleanup and RNG isolation

Wrappers are installed only inside the injection session and are removed
after success, operator errors and partial setup failure; a declared seam
with no target refuses before anything is wrapped. A clean rerun after a
producer crash is byte-identical to the pristine control. The framework
consumes no randomness; the global RNG state is unchanged across attempts,
and any future stochastic operator must bind an isolated seed into its plan.

## Holdout discipline

The bounded qualification uses constructed, directed development fixtures
only. Injected partition-claims exist to prove that the landed
`case_registry` access gate refuses before any stat or read of holdout
payloads: the refusal is demonstrated against a root path where any
filesystem read would fail differently, so gate-first ordering is observable.
This issue neither opens nor tunes on holdout, and does not authorize
relabeling holdout allocations as development.

## Qualification protocol

`tools/qualify_mutations.py` (default mode) runs the controls and the full
fault×downstream matrix in-process and writes the bounded apparatus
publication. Every fault must trip its named downstream refusal —
`artifacts.verify_sha256`, `scorecard.validate_row`/`validate_report`,
`trace_capture.validate_selected_capture`, `case_registry.evaluate`/
`row_outcome`, and the attempt-denominator completeness gate — and every
control must pass, or nothing is written. `--check` reruns the qualification
and compares against the committed publication plus the digests of every
participating module; absence is reported as absent, never as a pass.

`tools/qualify_mutations_runtime.py` (default mode, DR-0006 gated host) runs
the bounded actual-Voice protocol inside the pinned release image: ordinary
traced baseline, empty-plan and sham attempts must be byte-identical to each
other and to the committed #22 prototype sentinel (final audio, every
captured trace, named parameters, noise, RNG state, invocation counts); each
declared fault must change exactly its blast radius (declared slot audio and
expected downstream traces diverge; sibling traces, undeclared batch slots,
normalization decision evidence and invocation counts stay byte-identical);
the composed ordered crash must abort the render with in-band events and a
byte-identical clean rerun; and the normalization decision seam must refuse
at plan time naming the producer handoff. `--check-publication` revalidates
the committed publication with stdlib only.

## Bidirectional coverage matrix (#34)

`tools/qualify_mutations_matrix.py` composes the three landed family
publications into the single bidirectional mutation-coverage matrix and
writes the bounded publication
`sim/reference/mutation-matrix-v1.json`; `tests/test_mutations_matrix.py`
binds the committed publication to the landed registry, and the
`matrix-numerical` job of `.github/workflows/mutations.yml` executes the
composition and `--check` under the locked metrics extra (four family rows
— two timing periodic, two signal periodic property — need NumPy for their
full trips; a missing dependency fails the rerun, never a skip).

The matrix is rerun, never grandfathered: default mode re-executes the
landed verification entry points first (`qualify_mutations.py --check`,
`qualify_mutations_runtime.py --check-publication`, and each family
runner's `--check`, which re-trips every fault against a fresh in-memory
rerun), and only composes the publication after all of them pass. The
runner itself injects no fault; its own executed evidence is plan-time
only — three undeclared cross-family compositions must refuse with
``undeclared combination`` while a declared intra-family pair validates
(wrong-then-right for the composition gate itself). No cross-family
composition pair is declared, and editing a family module to declare one
requires that family's own requalification first.

The publication carries both directions. `faults_to_tests`: one row per
executed fault (44 rows across the families, including the three executed
intra-family composed rows) with its registered operator(s), declared
seam, typed magnitude domain bound into the ``mp1-`` plan identity,
applicability (the directed cases and family surface it ran over),
validity, false-positive evidence (accepted clean control plus the
family's statistical-plausibility and tolerant-optional guards), missed
faults (must be empty), and raw row links (family publication, downstream
binding, observed refusal, ``mu1-`` envelope ids). `tests_to_faults`: one
row per mandatory detector (grouped by downstream binding) naming the
faults it must catch with wrong-then-right counts — the expected failing
control observed, then the clean case observed — and its baseline status.
Below-floor probes, degenerate coverage rows and out-of-applicability
normalization cells are labeled ``sensitivity-NO VERDICT`` and are never
counted as passes or detected faults; required faults caught by a
plan-time fail-closed refusal are recorded against the non-writable
catalog seam. The deterministic CI subset selects rows by operator
prefix over five categories — identity, delay, interpolation, gain and
normalization — and every selected row must be detected with an accepted
control. Counts are navigation aids only: no scalar mutation score
replaces the rows. #44 consumes selected mutations from this matrix and
#48 consumes its coverage; both consumption contracts are declared in the
publication, neither is executed by it, and holdout stays sealed.

## Not run here

The #31/#32/#33 fault-operator families (they register through this public
API in their own issues; `bridge.*` operators are test-only bridge proofs,
never family operators) and #34's matrix publication, which has its own
section above. Injection at
`voice.normalization_decision` is not executable without the named producer
handoff; its fail-closed refusal is executed host-side instead. Synthetic
apparatus proofs never count as runtime evidence, and no detector-family
qualification is claimed by this framework.
