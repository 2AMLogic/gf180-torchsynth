# Work plan

This snapshot records workflow state. Issue closure and Loom labels do not establish technical qualification; consult the committed evidence and capability reports for claim status.

<!-- guide:plan-body:start -->
_Generated: 2026-11-23T17:05:00Z (snapshot; compare against live labels)_
## Operator Attention: Merge-Risk-Hold Pileup

Judge-approved PRs under a hold (`loom:blocked` or `loom:operator`) — only a human merge decision is missing.

- **#542**: feat(fpga): post-synthesis sentinel attempt, refusal record and clean-checkout rebuild

## Very Important

`loom:very-important` (one counts per workspace); every stage takes these first.

- **#82**: [Epic #2] Run reproducible gf180mcu synthesis and timing feasibility

## Important

`loom:important`; taken before unlabelled work.

- **#55**: [Epic #1] Unseal and run the 32-case blind holdout once *(held)*
- **#274**: Provision access to the pinned AWS box (repo-remote, i-018841ef4169207ba) from the agent fleet *(held)*

## Ready

Human-approved issues (`loom:issue`) ready for implementation — excluding held (`loom:blocked`/`loom:operator`/`loom:frozen`), `loom:building`, and issues closed by an open `loom:pr` PR (listed under Approved).

- **#263**: [Epic #2] Give the whole-voice vco-pitch-wire-swap control a trace-level kill
- **#382**: Consolidate duplicated FamilySession and fixture harness in mutations_identity/mutations_timing
- **#388**: Emit a klt functional-verification report for the bit-exact RTL suite (T1 item 5, functional half)
- **#394**: Requalify PyTorch 2.14.1 patch evidence without changing the historical reference (#393)
- **#530**: asic/klt-friction.md F3: the paste-ready title is attributed to klayout-tools#5871, but #5798 is the issue that carries it

## In Progress

Issues currently being built (`loom:building`).

- **#298**: [Part of #9] Add explicit contract-wrong-but-perceptually-similar rows (small gain, one-sample delay) asserting identity/property failure
- **#374**: [Epic #2] FPGA post-route sentinel simulation and clean-checkout rebuild
- **#456**: Remove orphaned unratified example listening receipt (examples/listening): unreferenced, stale, superseded by session-01 manifest

## PRs Awaiting Review

PRs waiting on Judge (`loom:review-requested`).

_None._

## Approved (Awaiting Merge)

PRs that passed review and are queued for Champion auto-merge (`loom:pr`).

- **#542**: feat(fpga): post-synthesis sentinel attempt, refusal record and clean-checkout rebuild — closes #374
- **#545**: chore: remove unratified example listening receipt — closes #456

## Proposed

Issues carrying `loom:curated`.

- **#4**: [Epic #1] Phase plan: reproducible corpus artifacts *(curated)* *(held)*
- **#7**: [Epic #1] Phase plan: observable named Voice traces *(curated)* *(held)*
- **#55**: [Epic #1] Unseal and run the 32-case blind holdout once *(curated)* *(held)*
- **#82**: [Epic #2] Run reproducible gf180mcu synthesis and timing feasibility *(curated)*
- **#199**: Champion: Merge-Risk Hold Digest *(curated)* *(held)*
- **#243**: Renovate PR #232 (setuptools 83→84) fails scalar sentinel: uv.lock provenance requires re-baselined reference bytes *(curated)* *(held)*
- **#263**: [Epic #2] Give the whole-voice vco-pitch-wire-swap control a trace-level kill *(curated)*
- **#264**: [Epic #2] Fold both one-shot lanes into the #78 aggregate RTL-qualification gate *(curated)* *(held)*
- **#284**: CI: run TB sim and CI on the newest main on a schedule, not on every merge (~1,700 job-min/day on main) *(curated)*
- **#286**: [Epic #1] Execute per-path directed trace captures for each registry trace *(curated)* *(held)*
- **#287**: [Epic #1] Measure traced development-corpus storage and memory cost *(curated)* *(held)*
- **#298**: [Part of #9] Add explicit contract-wrong-but-perceptually-similar rows (small gain, one-sample delay) asserting identity/property failure *(curated)*
- **#325**: Requalify DR-0006 canonical-runtime host scope for the current Apple host (macOS 27.0.1, Docker 29.8.2) *(curated)* *(held)*
- **#374**: [Epic #2] FPGA post-route sentinel simulation and clean-checkout rebuild *(curated)*
- **#388**: Emit a klt functional-verification report for the bit-exact RTL suite (T1 item 5, functional half) *(curated)*
- **#394**: Requalify PyTorch 2.14.1 patch evidence without changing the historical reference (#393) *(curated)*
- **#411**: fix: isolate historical pip from release-era downloads *(curated)* *(held)*
- **#431**: Record release-image byte-identity run for the #430 mutation worker family refactor *(curated)* *(held)*
- **#454**: Measure Spot shards and regenerate the three whole-voice evidence records for PR #275 *(curated)* *(held)*
- **#456**: Remove orphaned unratified example listening receipt (examples/listening): unreferenced, stale, superseded by session-01 manifest *(curated)*
- **#525**: Run the klt functional-verification lanes without editing hashed RTL: pass +lut= through klt and drop the KLT_COCOTB ifdefs from PR #392 *(curated)* *(held)*

## Proposed (Architect / Hermit)

- **#6**: [Epic #1] Phase plan: render and audit the development corpus *(architect)* *(held)*
- **#35**: [Epic #1] Phase plan: independent traceable float Voice model *(architect)* *(held)*
- **#36**: [Epic #1] Phase plan: calibrate perceptual and distribution diagnostics *(architect)* *(held)*
- **#37**: [Epic #1] Phase plan: freeze rubric and run blind holdout *(architect)* *(held)*
- **#38**: [Epic #1] Phase plan: explore and freeze the fixed numeric model *(architect)* *(held)*
- **#57**: [Epic #2] Phase plan: one-shot Voice RTL and bit-exact verification *(architect)* *(held)*
- **#58**: [Epic #2] Phase plan: FPGA and gf180mcu feasibility evidence *(architect)* *(held)*
- **#59**: [Epic #2] Phase plan: generate-audition-repeat-save-vary instrument *(architect)* *(held)*
- **#67**: [Epic #2] Demonstrate and archive the end-to-end hardware sound explorer *(architect)* *(held)*
- **#81**: [Epic #2] Integrate FPGA control and audio I/O and capture playback *(architect)*
- **#83**: [Epic #2] Measure gf180mcu floorplan, place, route, and timing feasibility *(architect)* *(held)*
- **#476**: Split the self-contained ADSR lane out of tb/run_tb.py (the named-lane follow-on #383 invited) *(hermit)* *(held)*

## Epics

- **#1**: Epic: Qualify TorchSynth Voice and freeze a numeric contract
- **#2**: Epic: Implement and demonstrate the TorchSynth Voice sound explorer
- **#386**: Track the gap to T1 (klayout-tools design-evidence-tiers.md) *(held)*

## Backlog Balance

| Bucket | Count |
|------|-------|
| Operator merge-risk holds | 1 |
| Very important | 1 |
| Important | 2 |
| Ready (`loom:issue`, net of holds/building/merge-pending) | 5 |
| In Progress (`loom:building`) | 3 |
| PRs awaiting review | 0 |
| Approved PRs awaiting merge | 2 |
| Curated | 21 |
| Architect / Hermit proposals | 12 |
| Active epics | 3 |
<!-- guide:plan-body:end -->
