# DR-0016: Ratify the T1 block spec table (`spec/T1-SPEC.md`)

- Status: Proposed (ratified on operator approval of the PR that carries this
  record)
- Date: 2026-10-27
- Decision owners: 2AM Logic
- Issue: #418 (prerequisite for T1 items 5-8 on tracker #386; table drafted
  under #387)
- Scope: ratification bookkeeping only. No RTL, evidence record, other
  decision record or table row value changes. No synthesis, layout, signoff,
  hardware-playback or sound-fidelity claim is made.

## Context

`spec/T1-SPEC.md` was committed as "Proposed / not ratified" so that
klayout-tools T1 items 5, 6, 8 and 10 had a table to grade against. Until it
is ratified, every verdict graded against it is provisional and items 5-8 on
#386 stay blocked.

The operator ruled on 2026-10-27 (2AMLogic/2am#5346) that this repo's spec is
ratified by the standard path: the EE key and the market key review the
ratification PR, then the operator's approval of that PR is the ratification.
This repo had no `ratification/` directory, so neither key could run. This
PR installs `ratification/ee-key/` and `ratification/market-key/`, adapted to
a digital, deterministic, sample-exact block.

## Decision

1. **What is ratified.** The T1 table in `spec/T1-SPEC.md` exactly as it
   stands at commit `9cc9b68ee8ba253b6a4bbe1fda543196c0ce318d` (the last
   commit touching that file before this PR): rows F1, F2, C1, T1, A1 and PW1
   with their bounds, corner sets, grading verbs/records, row classes and
   item-6 statements, together with the "Item-6 account", "What is not a row"
   and "Pending items" sections. One correction applies on top of that
   commit, made in response to the EE key's review of this PR: PW1's
   leakage-critical corner is `ff_125C_5v50` (fast process, 125 C, 5.50 V), not
   `ff_n40C_5v50`, which is the hold-critical corner. What is ratified is the
   table as it stands at the head of this PR.
2. **Pending rows stay pending.** C1, T1, A1 and PW1 are ratified *as pending
   claims*: their bounds remain "pending #82/#83" and no value is filled in.
   Ratification does not select a clock; DR-0010 P1 candidates (25/50/100 MHz)
   remain unselected. A pending row that later acquires a number does so by
   its own record, and that is a change to a ratified row (not a fill-in).
3. **No row is statistical.** The item-6 account (`not_applicable` against the
   ratified table) is part of what is ratified.
4. **Status wording.** The `spec/T1-SPEC.md` status line changes from
   "Proposed / not ratified" to "Ratification pending; ratified on operator
   approval of PR carrying DR-0016". It becomes ratified by that approval; no
   later edit is needed to record it beyond updating this record's status.
5. **Who ratifies.** The EE key and the market key (non-author identities)
   post `RATIFY-KEY` markers on the PR; the operator's approval of the PR is
   the ratification. Builder and Champion do not merge it. An unrun key is
   reported as unrun, never as a pass.

## Consequences

- Verdicts graded against the table stop being provisional once the PR is
  approved; T1 items 5-8 on #386 can then be filed or released.
- Changing any ratified row (including relaxing a bound) is a separate
  decision record and takes the relax-after-measured-FAIL route.
- The table remains a statement of claims, not evidence: this record
  establishes no synthesis, layout, signoff, hardware or sound-fidelity result.

## Not decided here

Clock selection, any value for C1/T1/A1/PW1, `klt functional-verification`
mapping for F1, and everything under #82/#83.
