# Work plan

This snapshot records workflow state. Issue closure and Loom labels do not establish technical qualification; consult the committed evidence and capability reports for claim status.

<!-- guide:plan-body:start -->
## Operator Attention: Merge-Risk-Hold Pileup

Judge-approved PRs stuck under a `loom:operator` merge-risk hold — implementation work is done, only a human merge decision is missing.

- **#258**: fix: check the committed mutation publications before regenerating them
- **#293**: ci: schedule CI and TB sim on newest main (#284)

## Operator Priority

Issues the operator starred (`loom:operator-priority`); land these first.

- **#55**: [Epic #1] Unseal and run the 32-case blind holdout once

## Ready

Human-approved issues ready for implementation (`loom:issue`), excluding issues with open implementation PRs.

_None._

## In Progress

Issues currently being built (`loom:building`).

- **#9**: [Epic #1] Phase plan: negative-control mutation coverage

## PRs Awaiting Review

PRs waiting on Judge (`loom:review-requested`).

_None._

## Approved (Awaiting Merge)

PRs that passed review and are queued for Champion auto-merge (`loom:pr`).

- **#258**: fix: check the committed mutation publications before regenerating them
- **#293**: ci: schedule CI and TB sim on newest main (#284)
- **#321**: docs: re-verify mutation-coverage audit at ddadee1 (Part of #9)

## Proposed

Issues carrying `loom:curated`.

- **#4**: [Epic #1] Phase plan: reproducible corpus artifacts *(curated)*
- **#7**: [Epic #1] Phase plan: observable named Voice traces *(curated)*
- **#9**: [Epic #1] Phase plan: negative-control mutation coverage *(curated)*
- **#243**: Renovate PR #232 (setuptools 83→84) fails scalar sentinel: uv.lock provenance requires re-baselined reference bytes *(curated)*
- **#257**: matrix-numerical CI job regenerates the mutation matrix publication before checking it, so committed-artifact drift can never fail CI *(curated)*
- **#263**: [Epic #2] Give the whole-voice vco-pitch-wire-swap control a trace-level kill *(curated)*
- **#264**: [Epic #2] Fold both one-shot lanes into the #78 aggregate RTL-qualification gate *(curated)*
- **#274**: Provision access to the pinned AWS box (repo-remote, i-018841ef4169207ba) from the agent fleet *(curated)*
- **#284**: CI: run TB sim and CI on the newest main on a schedule, not on every merge (~1,700 job-min/day on main) *(curated)*
- **#286**: [Epic #1] Execute per-path directed trace captures for each registry trace *(curated)*
- **#287**: [Epic #1] Measure traced development-corpus storage and memory cost *(curated)*
- **#297**: [Part of #9] Add missing family mutations: -1 dB gain, truncation, ADSR decay/sustain/release breakpoints, second magnitude probes *(curated)*
- **#298**: [Part of #9] Add explicit contract-wrong-but-perceptually-similar rows (small gain, one-sample delay) asserting identity/property failure *(curated)*
- **#299**: [Part of #9] CI: run identity tests and runner --check, and a 20-row ci_subset sentinel step in mutations.yml *(curated)*
- **#300**: [Part of #9] Fresh actual-Voice runtime measurement of identity/timing/signal family operators on the pinned release image *(curated)*

## Proposed (Architect / Hermit)

- **#6**: [Epic #1] Phase plan: render and audit the development corpus *(architect)*
- **#35**: [Epic #1] Phase plan: independent traceable float Voice model *(architect)*
- **#36**: [Epic #1] Phase plan: calibrate perceptual and distribution diagnostics *(architect)*
- **#37**: [Epic #1] Phase plan: freeze rubric and run blind holdout *(architect)*
- **#38**: [Epic #1] Phase plan: explore and freeze the fixed numeric model *(architect)*
- **#55**: [Epic #1] Unseal and run the 32-case blind holdout once *(architect)*
- **#57**: [Epic #2] Phase plan: one-shot Voice RTL and bit-exact verification *(architect)*
- **#58**: [Epic #2] Phase plan: FPGA and gf180mcu feasibility evidence *(architect)*
- **#59**: [Epic #2] Phase plan: generate-audition-repeat-save-vary instrument *(architect)*
- **#67**: [Epic #2] Demonstrate and archive the end-to-end hardware sound explorer *(architect)*
- **#80**: [Epic #2] Build and measure a resource-generous FPGA core *(architect)*
- **#81**: [Epic #2] Integrate FPGA control and audio I/O and capture playback *(architect)*
- **#82**: [Epic #2] Run reproducible gf180mcu synthesis and timing feasibility *(architect)*
- **#83**: [Epic #2] Measure gf180mcu floorplan, place, route, and timing feasibility *(architect)*

## Epics

- **#1**: Epic: Qualify TorchSynth Voice and freeze a numeric contract
- **#2**: Epic: Implement and demonstrate the TorchSynth Voice sound explorer

## Backlog Balance

| Tier | Count |
|------|-------|
| Operator merge-risk holds | 2 |
| Operator priority | 1 |
| Ready (`loom:issue`) | 0 |
| In Progress (`loom:building`) | 1 |
| PRs awaiting review | 0 |
| Approved PRs awaiting merge | 3 |
| Curated | 15 |
| Architect / Hermit proposals | 14 |
| Active epics | 2 |
<!-- guide:plan-body:end -->
