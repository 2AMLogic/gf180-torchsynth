# DR-0015: Runtime-family campaign manifest revision 2 (#297 additions)

- Status: Proposed
- Date: 2026-10-16
- Decision owners: 2AM Logic
- Issue: #379 (unblocks PR #328 for #297; amends DR-0012 D1)
- Scope: Preregistration bookkeeping only. No runtime measurement, host
  qualification, actual-Voice render or detection claim is made here.

## Context

DR-0012 D1 and the manifest `revision_policy` say #297/#298 additions enter the
frozen inventory only through a declared manifest revision. PR #328 (#297)
inserts publication fault rows into `sim/reference/mutation-timing-v1.json` and
`sim/reference/mutation-signal-v1.json` and adds registry code, so
`spec/reference/runtime-family-campaign-v1.json` (revision 1) no longer matched
its bound sources or the row positions of its entries.

## Decision

1. `spec/reference/runtime-family-campaign-v1.json` moves to `revision` 2: the
   four changed sources are rebound, every entry's `source.row_indices` is
   remapped to the post-#297 publication row by fault name, and the frozen
   inventory digest is recomputed.
2. Six new publication faults are registered, each as `deferred_unmeasured`
   (the DR-0012 D8 route) with its obligation open, no development cases and
   no attempts: `timing:decay-breakpoint-shift-plus2`,
   `timing:release-breakpoint-shift-plus2`,
   `timing:sustain-amplitude-shift-plus0.1`, `signal:gain.db (minus 1 dB)`,
   `signal:clip.truncate_step` and
   `signal:clip.truncate_step vs clip.round_step (directed distinction)`.
   Their seam, target and detector are proposed bindings only; truth rule is
   `not_executed`. Operators `envelope.sustain_shift` and `clip.truncate_step`
   are registered.
3. The twelve #297 floor probes (decay/release breakpoint, sustain, LFO rate,
   LFO depth and route depth, each below and above) are added to
   `sensitivity_inventory` (20 to 32). They are not executed and not counted.
4. Promoting any of these entries to a `runtime_cell` is a separate revision
   with its own record; this revision does not choose new runtime seams or
   detectors.

## Denominator

Inventory 37 to 43 entries, source rows 44 to 50, `deferred_unmeasured`
inventory entries 5 to 11 (total deferred 10 to 16). The executable denominator
is unchanged: 72 fault cells, 9 composition cells, 24 control attempts, 186
worker attempts in 6 worker processes, 7 expected refusals.

## What this record does not claim

No result of any kind for the new entries: they are neither measured nor
detected, and no sensitivity is claimed beyond what the #297 publications
record on synthetic fixtures.
