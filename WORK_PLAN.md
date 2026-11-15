# Work plan

This snapshot records workflow state. Issue closure and Loom labels do not establish technical qualification; consult the committed evidence and capability reports for claim status.

<!-- guide:plan-body:start -->
## Operator Attention: Merge-Risk-Hold Pileup

Judge-approved PRs under a hold (`loom:blocked` or `loom:operator`) — only a human merge decision is missing.

- **#375**: FPGA feasibility measurement: ECP5 LFE5U-85F does not fit (DR-0017, #80)

## Very Important

`loom:very-important` (one counts per workspace); every stage takes these first.

- **#82**: [Epic #2] Run reproducible gf180mcu synthesis and timing feasibility

## Important

`loom:important`; taken before unlabelled work.

- **#55**: [Epic #1] Unseal and run the 32-case blind holdout once
- **#274**: Provision access to the pinned AWS box (repo-remote, i-018841ef4169207ba) from the agent fleet

## Ready

Human-approved issues ready for implementation (`loom:issue`).

- **#80**: [Epic #2] Build and measure a resource-generous FPGA core
- **#82**: [Epic #2] Run reproducible gf180mcu synthesis and timing feasibility
- **#382**: Consolidate duplicated FamilySession and fixture harness in mutations_identity/mutations_timing
- **#388**: Emit a klt functional-verification report for the bit-exact RTL suite (T1 item 5, functional half)
- **#394**: Requalify PyTorch 2.14.1 patch evidence without changing the historical reference (#393)
- **#481**: Hoist the duplicated DATAFLOW table and route list out of the two runtime adapters

## In Progress

Issues currently being built (`loom:building`).

- **#263**: [Epic #2] Give the whole-voice vco-pitch-wire-swap control a trace-level kill
- **#298**: [Part of #9] Add explicit contract-wrong-but-perceptually-similar rows (small gain, one-sample delay) asserting identity/property failure
- **#470**: Make spec/schemas/estimator-qualification-v1.schema.json live or non-normative: it has a dangling $ref and nothing loads it
- **#473**: asic/klt-friction.md: Removable workarounds says synth_view.py has two jobs; it has four
- **#484**: Simplify: hoist duplicated check_publication skeleton across five mutation qualifiers

## PRs Awaiting Review

PRs waiting on Judge (`loom:review-requested`).

- **#485**: docs: correct F1 caveat in klt-friction.md — the synthesis view has four rewrite jobs, not two
- **#486**: refactor: hoist duplicated check_publication skeleton into one shared helper

## Approved (Awaiting Merge)

PRs that passed review and are queued for Champion auto-merge (`loom:pr`).

- **#375**: FPGA feasibility measurement: ECP5 LFE5U-85F does not fit (DR-0017, #80)

## Proposed

Issues carrying `loom:curated`.

- **#4**: [Epic #1] Phase plan: reproducible corpus artifacts *(curated)*
- **#7**: [Epic #1] Phase plan: observable named Voice traces *(curated)*
- **#55**: [Epic #1] Unseal and run the 32-case blind holdout once *(curated)*
- **#80**: [Epic #2] Build and measure a resource-generous FPGA core *(curated)*
- **#82**: [Epic #2] Run reproducible gf180mcu synthesis and timing feasibility *(curated)*
- **#199**: Champion: Merge-Risk Hold Digest *(curated)*
- **#243**: Renovate PR #232 (setuptools 83→84) fails scalar sentinel: uv.lock provenance requires re-baselined reference bytes *(curated)*
- **#263**: [Epic #2] Give the whole-voice vco-pitch-wire-swap control a trace-level kill *(curated)*
- **#264**: [Epic #2] Fold both one-shot lanes into the #78 aggregate RTL-qualification gate *(curated)*
- **#284**: CI: run TB sim and CI on the newest main on a schedule, not on every merge (~1,700 job-min/day on main) *(curated)*
- **#286**: [Epic #1] Execute per-path directed trace captures for each registry trace *(curated)*
- **#287**: [Epic #1] Measure traced development-corpus storage and memory cost *(curated)*
- **#298**: [Part of #9] Add explicit contract-wrong-but-perceptually-similar rows (small gain, one-sample delay) asserting identity/property failure *(curated)*
- **#325**: Requalify DR-0006 canonical-runtime host scope for the current Apple host (macOS 27.0.1, Docker 29.8.2) *(curated)*
- **#388**: Emit a klt functional-verification report for the bit-exact RTL suite (T1 item 5, functional half) *(curated)*
- **#394**: Requalify PyTorch 2.14.1 patch evidence without changing the historical reference (#393) *(curated)*
- **#411**: fix: isolate historical pip from release-era downloads *(curated)*
- **#431**: Record release-image byte-identity run for the #430 mutation worker family refactor *(curated)*
- **#467**: Bump the klayout-tools pin from 0.8.0 to the current PyPI release and regenerate the T1 attestations and signoff report *(curated)*
- **#473**: asic/klt-friction.md: Removable workarounds says synth_view.py has two jobs; it has four *(curated)*
- **#481**: Hoist the duplicated DATAFLOW table and route list out of the two runtime adapters *(curated)*

## Proposed (Architect / Hermit)

- **#6**: [Epic #1] Phase plan: render and audit the development corpus *(architect)*
- **#35**: [Epic #1] Phase plan: independent traceable float Voice model *(architect)*
- **#36**: [Epic #1] Phase plan: calibrate perceptual and distribution diagnostics *(architect)*
- **#37**: [Epic #1] Phase plan: freeze rubric and run blind holdout *(architect)*
- **#38**: [Epic #1] Phase plan: explore and freeze the fixed numeric model *(architect)*
- **#57**: [Epic #2] Phase plan: one-shot Voice RTL and bit-exact verification *(architect)*
- **#58**: [Epic #2] Phase plan: FPGA and gf180mcu feasibility evidence *(architect)*
- **#59**: [Epic #2] Phase plan: generate-audition-repeat-save-vary instrument *(architect)*
- **#67**: [Epic #2] Demonstrate and archive the end-to-end hardware sound explorer *(architect)*
- **#81**: [Epic #2] Integrate FPGA control and audio I/O and capture playback *(architect)*
- **#83**: [Epic #2] Measure gf180mcu floorplan, place, route, and timing feasibility *(architect)*
- **#456**: Remove orphaned unratified example listening receipt (examples/listening): unreferenced, stale, superseded by session-01 manifest *(hermit)*
- **#476**: Split the self-contained ADSR lane out of tb/run_tb.py (the named-lane follow-on #383 invited) *(hermit)*
- **#478**: Remove tools/render_reference.py: orphaned wrapper duplicating the documented render CLI *(hermit)*

## Epics

- **#1**: Epic: Qualify TorchSynth Voice and freeze a numeric contract
- **#2**: Epic: Implement and demonstrate the TorchSynth Voice sound explorer
- **#386**: Track the gap to T1 (klayout-tools design-evidence-tiers.md)

## Backlog Balance

| Bucket | Count |
|------|-------|
| Operator merge-risk holds | 1 |
| Very important | 1 |
| Important | 2 |
| Ready (`loom:issue`) | 6 |
| In Progress (`loom:building`) | 5 |
| PRs awaiting review | 2 |
| Approved PRs awaiting merge | 1 |
| Curated | 21 |
| Architect / Hermit proposals | 14 |
| Active epics | 3 |
<!-- guide:plan-body:end -->
