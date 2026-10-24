# One-shot holdout unseal policy and refusal gate

Governing record: [DR-0011](decision-records/0011-holdout-unseal-policy.md),
issue #55. This document is policy and procedure only. It unseals nothing, and
no holdout identity (96-127) has been rendered, read or scored by the work that
introduced it. The frozen rubric (`spec/reference/rubric-v0.json`,
[RUBRIC.md](RUBRIC.md)) is not edited by this document.

## The question the single look is spent on

Does the frozen fixed model (`sim/reference/fixed-voice-golden-v1.json`,
`semantic_version: fixed-voice-golden-v1`) generalise under frozen rubric v0
(`spec/reference/rubric-v0.json`) to the 32 blind identities 96-127?

The unseal answers only that. It does not adjudicate any other pending retune,
candidate change or numeric-format choice, and a change that happens to be ready
is not a reason to spend the look. The output is one conjunction verdict for the
holdout partition (DR-0004, [SCORECARD-CONTRACT.md](SCORECARD-CONTRACT.md); no
weighted score), reported separately from the development partition.

## Re-use policy (settled before any unseal)

Settled on issue #55 (decision option `stands`) before the unseal:

1. The first and only exposure of identities 96-127 is final. **A failing
   result stands as published.** No threshold, estimator, coverage-rule or row
   edit under rubric v0, and no "fix and re-run" against the same 32 cases.
2. Any fix to the model or the rubric is **rubric v1** (the change-control rules
   already require `rubric-v1`, a holdout policy and "never rewrite"). A v1 claim
   needs a **fresh sealed holdout**: new, never-rendered identities and a new
   corpus manifest. That is separate follow-up work.
3. After the unseal, 96-127 are permanently classed `exposed`. They may be
   reported but never used to tune, select or gate anything, and a later pass on
   them is not evidence of generalisation.
4. A pass does not license threshold tightening, and does not license any
   sound-fidelity, synthesis, layout, signoff or hardware-playback claim.
5. The ledger is never reset or replaced to obtain another exposure
   ([CORPUS-RUNNER.md](CORPUS-RUNNER.md), "One-shot holdout gate").
6. A render crash during the unseal follows the existing same-run `--resume`
   rule and is part of the single exposure, never a new one.

The fitting objective must never be the scoring objective: fit on a smooth
development-only surrogate, score with the frozen rubric (rubric v0
`independent_judge`).

## Seal manifest

`spec/reference/holdout-seal-v0.json` pins, by SHA-256 and by one git commit
(`pinned_commit`, a commit on `origin/main`):

- `spec/reference/rubric-v0.json`, `spec/reference/corpus-v0.json`,
  `spec/reference/case-registry-v1.json`, `sim/reference/fixed-voice-golden-v1.json`;
- the model code, `src/torchsynth_voice/fixed_voice.py` plus every file of
  `src/torchsynth_voice/fixedpoint/` plus its transitive behavior-affecting
  inputs (`holdout_seal.MODEL_DEPENDENCIES`: the imported `torchsynth_voice`
  modules such as `float_sources` and `format_sweep`, and the JSON registers
  and schemas they load), as per-file hashes and a deterministic
  tree hash (`sha256` of the sorted `"<sha256>  <path>"` lines);
- the upstream pin `2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`;
- the holdout range 96-127 (32 identities) and `holdout_identities_read: 0`.

Hashes are computed from the content of the pinned commit, not from a working
tree: `python3 tools/check_holdout_seal.py --generate COMMIT` prints the manifest
and writes nothing. The manifest's own SHA-256 is pinned in
`tests/test_holdout_seal.py`, so a change to any sealed field shows in review
and in the fast suite. Moving the seal is a deliberate, reviewed act.

## Refusal gate

`tools/check_holdout_seal.py` (library: `src/torchsynth_voice/holdout_seal.py`)
evaluates every check and refuses with exit code 2 and structured JSON unless all
hold:

| Check | Meaning |
| --- | --- |
| `seal-manifest` | manifest is well formed with the exact expected fields |
| `pinned-hashes` | current files and model tree equal the pinned hashes |
| `pinned-commit-exists`, `pinned-commit-content` | the pinned commit exists and its content equals the pinned hashes |
| `pinned-commit-ancestor-of-head`, `...-origin-main` | the pinned commit is in `HEAD` and published `origin/main` history |
| `pinned-paths-clean` | no modified or untracked file in the pinned paths or model package |
| `upstream-pin` | seal upstream pin equals `spec/reference/upstream.json` |
| `rubric-validator` | `tools/validate_rubric.py` passes |
| `corpus-manifest-pinned` | the manifest being rendered is the pinned corpus manifest |
| `index-set` | the selection is exactly identities 96-127 |
| `frozen-rubric-equals-committed` | the `--frozen-rubric` file is schema-valid, `frozen: true`, and its `rubric` equals the committed rubric-v0 content; the freeze hash is recomputed from the file |
| `ledger-outside-repository`, `ledger-state` | the ledger is outside the repository and holds no admission for this corpus manifest (same-run `--resume` instead requires it) |

The gate is read-only: it creates no ledger, store or lock and reads no holdout
artifact. `tools/render_corpus.py --holdout-once` runs it before any renderer is
constructed, and `run_corpus(mode="holdout")` refuses unless a `seal_gate` is
supplied, invoking it before admission, ledger reservation, store creation or any
renderer call. The earlier text "the operator attests that freeze occurred" is
replaced by this verification.

## Sealing during development

Development mode refuses any index 96-127 before renderer or store access
(`select_cases`, `run_corpus`, `audit_corpus_coverage`). The fast-suite guard in
`tests/test_holdout_seal.py` additionally fails if a module outside the declared
holdout-aware set references the ledger, freeze or holdout-mode controls, if a
development or golden-vector tool ranges over 96-127, or if the default
scorecard partition stops being `development`.

## What the seal does NOT protect against

- An actor who can rewrite all trusted evidence: the repository, the pinned
  commit's history, `origin/main`, the ledger and the seal manifest together.
  This is the same limitation as [CORPUS-RUNNER.md](CORPUS-RUNNER.md).
- Derivation outside the repository, or reading holdout identities by a path the
  guard does not model. The guard is static and behavioral over this repository's
  tools; it is not a filesystem sandbox.
- A different ledger location chosen by the operator: the ledger is local and
  operator-controlled, with no global admission service.
- Any claim about gf180mcu synthesis, layout, signoff, hardware playback or
  sound fidelity. A holdout verdict is reference-software evidence only.

## Unseal procedure (operator-gated, not yet executed)

The unseal itself is a separate, irreversible step that needs an explicit
operator go. Outline: run `tools/check_holdout_seal.py` and keep its output; run
the one-shot command from CORPUS-RUNNER.md on the project's remote box with the
ledger outside the repository and the artifact store; run the fixed model on
96-127; render the holdout scorecard with
`tools/render_scorecard.py --partition holdout --allow-holdout "<reason naming issue #55>"`;
commit the unseal record (UTC time, operator, exact commands, gate output, ledger
hash, raw artifact index, scorecard path and hash, verdict). If any row fails,
the record states "stands as published; remediation = rubric v1 with fresh
holdout" and links a follow-up issue. Raw audio stays out of Git.
