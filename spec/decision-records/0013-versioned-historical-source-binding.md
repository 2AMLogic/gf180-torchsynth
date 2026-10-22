# DR-0013: Versioned historical source binding for sealed rubrics

Status: Accepted (operator ruling on PR #378, 2026-10-15 and 2026-10-16)

## Context

`spec/reference/rubric-v0.json` is sealed by `holdout-seal-v0.json`, and
`rubric-v1.json` follows the same never-rewrite change control. Both pin
`sim/reference/mutation-matrix-v1.json` by sha256. Regenerating that
publication to bind the current `spec/MUTATIONS.md` (issue #314) changes its
digest, so either the sealed rubrics would have to be edited and re-sealed, or
their validation would fail.

## Decision

- Rubric v0 and v1 bytes are unchanged. v0 is not re-sealed, and no hash
  verification is skipped.
- The matrix bytes the rubrics pinned are preserved at
  `sim/reference/history/mutation-matrix-v1-7c87809d.json`
  (sha256 `7c87809d0d3f6a53328e26b63be44ed16837e4a3ce61b7622de143f3faf11704`).
- `tools/validate_rubric.py` carries an explicit `HISTORICAL_SOURCE_BINDINGS`
  table. For a listed (rubric, source id) pair it resolves the source to the
  archived file and still checks the rubric's pinned sha256 against it.
- The live `sim/reference/mutation-matrix-v1.json` is free to advance with its
  runner (`tools/qualify_mutations_matrix.py --check`).

## Evidence limits

No new actual-Voice runtime evidence, holdout read, approval or re-seal is
claimed. A future rubric version binds the live matrix directly.
