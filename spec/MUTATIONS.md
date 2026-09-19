# Mutation framework v1

## Scope and ownership

This is the sole fault-injection seam framework for the qualification
apparatus (#30). It provides named, composable, versioned mutation plans over
apparatus-owned seams so that downstream tooling refusals and NO VERDICT paths
are exercised as tests. It owns:
`src/torchsynth_voice/mutations.py`,
`src/torchsynth_voice/mutation_runtime.py`,
`tests/test_mutations.py`, `tools/qualify_mutations.py`, the seam overlay
`spec/reference/mutation-seams-v1.json`, this document, the bounded
publication `sim/reference/mutation-framework-v1.json`, and
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
fault×downstream matrix in-process and writes the bounded publication. Every
fault must trip its named downstream refusal — `artifacts.verify_sha256`,
`scorecard.validate_row`/`validate_report`,
`trace_capture.validate_selected_capture`,
`case_registry.evaluate`/`row_outcome`, and the attempt-denominator
completeness gate — and every control must pass, or nothing is written.
`--check` reruns the qualification and compares against the committed
publication plus the digests of every participating module; absence is
reported as absent, never as a pass.

## Not run here

Actual-Voice runtime fault injection at #23 capture seams (requires the
Torch release-era runtime), the #31/#32/#33 fault-operator families, and
#34's matrix publication. Synthetic apparatus proofs never count as runtime
evidence, and no detector-family qualification is claimed by this framework.
