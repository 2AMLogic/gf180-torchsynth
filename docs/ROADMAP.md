# Roadmap and dependency map

A hardware canary for the default TorchSynth Voice on gf180mcu: qualify the reference, freeze a fixed-point contract, prove one-shot RTL sample-exact against it, then measure FPGA and gf180mcu feasibility. Verification is the product.

Status as of 2026-11-13, `main` @ `b511430`.

This document explains the gates and says what can move. It is not a claim source and not a second graph: claim status comes only from [`spec/capabilities-v1.json`](../spec/capabilities-v1.json) through the [generated capability DAG](CAPABILITIES.md) and the [scorecard](SCORECARD.md) (see [refresh policy](CAPABILITY-WORKFLOW.md) and [bring-up and evidence architecture](BRINGUP-AND-EVIDENCE.md)). Scheduling lives in [Epic #1](https://github.com/2AMLogic/gf180-torchsynth/issues/1), [Epic #2](https://github.com/2AMLogic/gf180-torchsynth/issues/2) and the T1 tracker [#386](https://github.com/2AMLogic/gf180-torchsynth/issues/386). Issue closure and labels establish no qualification.

*Changed 2026-11-13:* added the status, Now / Next / Later and blocked sections; the milestone text below is condensed from the earlier version, with its order and exits unchanged.

## Where it stands

- **Capability graph:** 1 READY (`contract`), 1 NOT RUN (`apparatus`), 16 BLOCKED, 0 PASS ([`docs/CAPABILITIES.md`](CAPABILITIES.md)). Most nodes have only a planned, unregistered check, so the work recorded below has not yet been turned into graph evidence.
- **Milestones 0 to 3 (contract, corpus, float model, fixed model):** the decision records for the version pin, product profile, host boundary, canonical runtime, numeric contract and holdout policy are Accepted (DR-0001 to DR-0006, DR-0008, DR-0010, DR-0011, DR-0013, DR-0014); DR-0007 is a scoped diagnostic decision. DR-0009, DR-0012, DR-0015 and DR-0016 are still Proposed. The 32-case holdout is sealed and unread (#55).
- **Milestone 4, RTL:** the whole-voice one-shot RTL in `tb/sv/` is recorded sample-exact against the fixed model for all 31 `full`-profile cases, with 13 of 13 all-RTL mutations detected (`sim/evidence/oneshot-whole-voice-full-v1.json`, T1 spec rows F1 and F2).
- **Milestone 4, gf180mcu synthesis:** negative result. The whole voice did not synthesize through `klt synthesize`; 4 of 10 engine modules completed and none closes the 25 MHz evaluation clock at every corner (`asic/evidence/oneshot-synth-feasibility-v1.json`, [`asic/README.md`](../asic/README.md)). The causes are structural: unrolled restoring dividers, 96-bit `/` and `%`, and control-column memories lowered to flip-flops ([`asic/whole-voice-feasibility-plan.md`](../asic/whole-voice-feasibility-plan.md), rows B1 to B10).
- **Milestone 4, FPGA:** a measurement exists only in open PR #375, which reports that the core does not fit an ECP5 LFE5U-85F. Nothing is on `main` (`fpga/README.md` is still "reserved").
- **T1 checklist (#386), as graded by `klt signoff` 0.8.0** in `manifests/gf180-torchsynth.signoff-report.json`: 2 of 11 met.

| T1 item | State | Owner |
| --- | --- | --- |
| 1 Design sources | unmet: no whole-voice gate-level netlist | #82 |
| 2 Layout | unmet: no routed stream | #83 |
| 3 DRC, 4 LVS, 11 Power delivery | unmet, need item 2 | #413, #414, #415 |
| 5 Corner verification vs spec | unmet: functional half not in `klt functional-verification` form; no whole-voice STA | #388 (PR #392), #82 |
| 6 Monte Carlo, 7 Post-layout, 8 Characterization | unmet: need a ratified spec, then items 2 and 5 | none yet |
| 9 Testbenches shipped | met (`evidence/item9-attestation.json`) | done, #389 |
| 10 Repo hygiene | met (`evidence/item10-attestation.json`) | done, #422 |

## Now

Can start today, one PR each. The four without an issue number have drafts ready to file.

1. **Static sample-identity checks for the synthesis view** (part of #82; no issue yet). Checks C1 and C2 of the feasibility plan, plus the `+max_j` reading for rewrite class 3. Done when a test proves every `` `define `` in the derived view equals its package `localparam`, the ROM payload hash equals what `tb/run_voice.py` writes, and both division-loop forms give equal quotients and remainders over the full `den_msb` range; and the plan's sample-identity table cites the test. No tool run, no RTL edit.
2. **Re-check `klt` friction F1 to F12 against a current release** (no issue yet). [`asic/klt-friction.md`](../asic/klt-friction.md) was observed on `klt 0.7.1.dev` and says "Not yet filed upstream". Done when each item carries a dated status line (still present, changed, or fixed, with the command that shows it) and a paste-ready upstream title.
3. **Bump the `klayout-tools` pin from 0.8.0** (no issue yet; precedent #416). Done when `.github/workflows/tb-sim.yml` pins the newer PyPI release, the item 9 and 10 attestations and the signoff report are regenerated with it, and the report drift check is green.
4. **Rank the measured bottlenecks from the committed record** (check C3 of the plan; no issue yet). Done when a test or script compares ABC critical path to divider stage count for the completed modules and the plan records whether the divider is the dominant term. No tool run.
5. **Repair PR #375 (FPGA negative result, #80).** Its cited blocker #378 closed on 2026-10-17, but the branch now conflicts with `main`, and its `0013-fpga-measurement-target.md` collides with the DR-0013 already on `main`. Done when the PR is renumbered to the next free record, merges cleanly and is green.
6. **Finish the CI-scheduling record (#284).** Done when `docs/CI-SCHEDULING.md` replaces its two PENDING sections with the seven-day measurement and the observed (or explicitly not observed) no-op and main-advancement cases. Curated, not yet promoted.
7. **In flight:** #461 / PR #462 (one definition each for the f32le codec and trace digest).

## Next

- **Scoped per-engine path-depth measurement** (plan check C4) for the six engines that timed out. Done when a path-depth or `stat` number exists per engine. Needs an operator sign-off for a new tool run on the shared worker (#82 hold resolution).
- **Run the whole-voice lane against the derived synthesis view** (plan check C5). Needs a source-directory override in `tb/run_voice.py`, then a long simulation campaign: blocked on #454.
- **`klt functional-verification` report for the module lanes** (#388, PR #392). Blocked: the PR edits hashed RTL, so the whole-voice and synthesis records must be regenerated (#454).
- **Trace-level kill for `vco-pitch-wire-swap`** (#263, PR #275): same regeneration block. **The one-shot lanes in the aggregate RTL gate** (#264, PR #283) follow; that PR is parked with changes requested.
- **Divider and memory decision record** amending DR-0010 P2 and P3 (iterative or serialized dividers, macro or shared ROM, column memories). Done when the record is Accepted; only then may RTL change, starting with plan check C6 on `normalization_replay_engine`. Follows Now items 1 and 4.
- **Whole-voice mapped netlist with timing and achieved schedule** (#82 remaining criteria, T1 item 1). Follows the decision record and the RTL restructure.

## Later

- **gf180mcu floorplan, place, route and timing** (#83), then DRC, LVS and power delivery (#413, #414, #415). Question first: does a restructured voice map at all, and is storage a macro or distributed (DR-0010 P2)?
- **T1 items 6, 7 and 8.** Question first: which clock does DR-0010 P1 select, so that spec rows C1, T1, A1 and PW1 get bounds?
- **FPGA control, audio I/O and playback** (#81, #374). Question first: which board, given that the first measured device does not fit (#80)?
- **Blind holdout, once** (#55). Authorized by DR-0011; waits on one exclusive qualified execution lane (#274).
- **Sound-explorer instrument** (#59, #67): generate, audition, repeat, save, vary. Question first: is nebula sampling on the host or the device?

## Blocked and waiting

**No `loom:issue` item is dispatchable today.** Each of #80, #82, #382, #388 and #394 either has an open PR parked on a blocker or is limited by the #82 hold resolution to bounded investigation. The first four Now items are the dispatchable supply once filed.

- **Long-running execution substrate** (#454, operator decision; #274): regenerating the three whole-voice evidence records takes hours of Icarus simulation and no fleet-reachable host may run it. Holds PRs #275 and #392 (issues #263, #388) and the holdout run #55.
- **DR-0006 measured host** (#325): `mutation-runtime-v1.json` can be regenerated only on the recorded host scope. Holds PR #385 (#382).
- **Dependency updates:** torch 2.14.1 (#393, #395, requalification #394) and setuptools 84 (#368, #243, #411) wait on requalified reference evidence.
- **T1 spec ratification status:** #418 is closed and PR #419 merged, but `spec/T1-SPEC.md`, DR-0016 and the README still read "pending" / "Proposed". Until that is reconciled every verdict against the table is provisional.
- **Operator-held:** #456 (remove an orphaned example receipt), #431, #199, #4.

## Not planned

- A causal, live-keyboard profile. It would be a new, separately verified behaviour profile (DR-0002).
- A ladder filter, or any module outside the pinned Voice graph.
- Raising the Yosys bound to obtain a whole-voice netlist: plan check C0 shows it cannot change the timing verdict.
- Performance tuning or shrinking before fit is measured.

## Design order (kept from the original roadmap)

```text
version/profile contract
        v
reference environment + reproducible corpus
        v
traceable float model  +  qualified estimators and mutations
        v
fixed-point feasibility + numeric DR  ->  fixed model (golden vectors)
        v
one-shot RTL  ->  FPGA feasibility
        v
gf180 synthesis / physical feasibility  ->  sound-explorer integration
```

- **Milestone 0, contract and method.** Pin source, profile, identity and hashes; define claim layers, corpus split, provenance and mutation expectations. Exit: repository checks prove the manifests are internally consistent (not yet an audio render).
- **Milestone 1, qualified reference corpus.** Lock the CPU environment, render the 96-case development corpus twice and prove repeat, qualify one-sound execution against the batched reference, keep 32 holdout cases sealed. Exit: replayable corpus with immutable hashes and an honest portability report.
- **Milestone 2, transparent model and measurement rig.** Traceable float model matched to named traces; estimators qualified on analytic fixtures; negative-control mutation matrix; signal preparation qualified separately from the estimators it feeds. Exit: every estimator has known validity bounds and every named fault is detected by a required row.
- **Milestone 3, fixed-point feasibility.** Sweep widths, rounding, saturation and approximations; auditory experiments only after signal-level correctness; ratify arithmetic and thresholds in decision records. Exit: frozen fixed model and rubric, then one blind holdout run.
- **Milestone 4, implementation.** One-shot RTL against exact vectors; measure cycles, storage and replay cost; an FPGA build if it adds evidence; only then the gf180mcu flow. Exit: claims limited to the completed flow stage.
- **Milestone 5, instrument.** Generate / audition / repeat / save / vary around the verified core, parameter locking and favourite recall; live keyboard semantics stay a separately named profile.
