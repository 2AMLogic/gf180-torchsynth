# DR-0011: Mechanical holdout seal and single-exposure re-use policy

- Status: Accepted
- Date: 2026-10-08
- Decision owners: 2AM Logic
- Issue: #55

## Decision

1. If the one-shot holdout (identities 96-127) fails under rubric v0, the
   failing result stands as published. Any fix is rubric v1, which requires a
   fresh sealed holdout. "Fix and re-run" against the same 32 cases is rejected.
   This is the `stands` option of the decision recorded on issue #55 (2026-10-08).
2. The freeze is verified, not attested. A checked-in seal manifest
   (`spec/reference/holdout-seal-v0.json`) pins the rubric, corpus manifest, case
   registry, fixed-model golden record, model code tree and upstream pin by
   SHA-256 and git commit. `tools/check_holdout_seal.py` refuses the unseal unless
   they match, the pinned commit is published history, the pinned paths are clean,
   the rubric validates, the frozen-rubric file equals the committed rubric, the
   selection is exactly 96-127 and no admission exists in the ledger.
3. The gate is wired into the unseal path: `render_corpus.py --holdout-once` and
   `run_corpus(mode="holdout")` cannot proceed without it.
4. After the unseal, identities 96-127 are permanently `exposed`.

## Scope

Policy and verification tooling only. The target, arithmetic profile, noise
policy, normalization, parameter order and clip timing are unchanged, rubric v0
is not edited, and no holdout identity is rendered or read by this decision. The
unseal itself needs a separate explicit operator go. Full text:
[HOLDOUT-UNSEAL.md](../HOLDOUT-UNSEAL.md).

## Consequences

Moving the seal requires a reviewed change to the manifest and to the manifest
hash pinned in `tests/test_holdout_seal.py`. A new tolerance or model fix costs a
new holdout set. The seal does not defend against an actor rewriting all trusted
evidence, and a pass supports no fidelity or hardware claim.
