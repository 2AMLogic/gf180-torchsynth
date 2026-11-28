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

## Amendment for #575 (2026-11-26)

Campaign revision 3 binds the identity and signal publications regenerated
after the spectral mixer change. The following SHA-256 values identify the
two revisions at the #575 boundary.

| Source under `sim/reference/` | Revision 2 at `28bce63` | Revision 3 |
| --- | --- | --- |
| `mutation-identity-v1.json` | `17fb2fa1a51ad69a1b454a3c55ca0f9e3393f31b63929bf19f3f5d8ac7cfc8eb` | `6914a1c3b3509804389d897f64cd3523b50727e2d79f9e65501572127daf8cee` |
| `mutation-signal-v1.json` | `045793c215274eb98a20b002fac29e54ccb94750a9c4bd99ddf2b75f4f86671a` | `25d0078fb18ee0be87e635fce490c323623acf5f7be1196dc04a018a877a5e89` |

The frozen digest covers the inventory, sensitivity inventory, supporting
entries, cases and detectors. Those inputs reproduce the revision 2 digest
`b632d7ee43e2da2f0319e6ea9612f9101ef30040211af290f8d3ef0a914f1cd9`.
Schedules and reports distinguish the two source bindings by campaign
revision. Revision 3 is preregistration only.
