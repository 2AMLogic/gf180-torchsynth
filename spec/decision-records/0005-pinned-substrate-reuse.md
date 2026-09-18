# DR-0005: Keep substrate reuse pinned and locally owned

- Status: Accepted
- Date: 2026-09-18
- Decision owners: 2AM Logic, for `gf180-torchsynth` only
- Issue: [#61](https://github.com/2AMLogic/gf180-torchsynth/issues/61)

## Decision and scope

Keep the instrument implementation and its artifact/scorecard contracts local.
When a specific substrate need is approved, prefer a small, reviewed, pinned
copy with its conformance tests over coupling the instrument repositories.
Use the existing `klt` package interface for ASIC work; do not copy its internals.
This record selects adoption rules and dispositions, **imports nothing**, and
installs no dependency. Acceptance of this governance does not qualify any
candidate or authorize an import without the gates below.

The target remains TorchSynth commit
`2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`, default Voice/default nebula,
one four-second, 176,400-sample mono sound per trigger at 44.1 kHz.
Canonical batched capture is a qualification fixture protocol. Resolved scalar
execution may use `batch_size=1, reproducible=False`; the relationship between
those executions still requires qualification. Reuse must not select hardware
batching, transport, board, arithmetic, noise, or normalization implicitly.
[DR-0003](0003-host-boundary-and-normalization.md) remains Proposed.

Reject submodules for this substrate: pinning their commit alone does not
provide local adaptation ownership or conformance, and checkout/update coupling
buys nothing for the small candidates. Moving Git references, floating package
versions for qualified runs, automatic upstream synchronization, and unreviewed
generated copies are forbidden. A future submodule exception needs a replacement
decision explaining a concrete advantage and retaining all provenance gates.

## Immutable decision inputs

The [landed audit](../../docs/REUSE-AUDIT.md) and
[source catalog](../../docs/reuse-audit-sources.json) are the source of each
candidate's exact file path, SHA-256, license basis, and evidence limitations.
This decision incorporates their versions landed in commit
`a6c03b34762cf7b72229a9b7f62f483cfca1bb7b`
([PR #91](https://github.com/2AMLogic/gf180-torchsynth/pull/91)).
The catalog's SHA-256 is
`8f9490827483be99247679bb6d699be9df06bd7f2661ad539a4efe21988963fb`;
the audit's SHA-256 is
`1637b47627995c712cfd7335f6dd6803a8ede28a023a254f624f255fe76cb5c0`.
If a working copy changes, use the
[immutable catalog](https://github.com/2AMLogic/gf180-torchsynth/blob/a6c03b34762cf7b72229a9b7f62f483cfca1bb7b/docs/reuse-audit-sources.json)
and [immutable audit](https://github.com/2AMLogic/gf180-torchsynth/blob/a6c03b34762cf7b72229a9b7f62f483cfca1bb7b/docs/REUSE-AUDIT.md).

| Catalog key | Repository | Exact audited commit | License source |
| --- | --- | --- | --- |
| P | `2AMLogic/gf180-parasynth` | `11be1312d2fd42b3d8cac75de2d12068d4c79f6c` | Apache-2.0, P46 |
| Y | `2AMLogic/gf180-polysynth` | `010efac225b6173105bfb4a13dcbf1b94121ec6f` | Apache-2.0, Y16; archived source |
| K | `2AMLogic/klayout-tools` | `2b7caa9939af3c4734a0fbd5fbddf7e7b852cc45` | MIT, K08 |

Catalog IDs in the following tables are exact references, not names to resolve
against a later branch. ID ranges are inclusive. Every referenced file inherits
its repository's declared license **subject to its individual `license_basis`**.
The Apache license files P46/Y16 both hash to
`d47483eb3e7e0b8cdc6f5252d857935661943f6c10bcecf49ae4b8dfe8c26317`;
MIT K08 hashes to
`721f4ce18acb00e7641fe083b619ca06437aeff9f3ddd13244dd151b55ba4f72`.
P11, Y07, Y08, P38 and P39 have unresolved vendor/generator/ORFS origins:
repository licensing is insufficient for their adoption. Trace the original
source, license and notices before proposing any derived copy. Check ORFS-derived
PDN material separately as well. These are attribution gates, not resolved
legal conclusions.

## Candidate dispositions and local ownership

**All candidates are currently unimported.** “Deferred copy/adapt” means a
possible later local implementation PR after its trigger and the common gates.
“Local” means keep or derive the local implementation rather than replace it
with the audited source. “Reject” means no import under this decision.
Paths below are intended destinations, not assertions that files exist.

2AM Logic's `gf180-torchsynth` maintainers own every local derivative and its
maintenance burden. Each import PR must name a local maintainer who accepts
custody of the listed path and tests; an upstream author or an agent is not an
implicit maintainer. The responsibility areas below route that review without
appointing maintainers for another repository. No willing custodian means no
import.

| Candidate and catalog identities | Disposition | Local responsibility / intended path | Import trigger and required adaptation/conformance |
| --- | --- | --- | --- |
| Parasynth I2S, P01-P04 | Deferred adapt, alternative to Y01 | FPGA/transport; `fpga/i2s_tx.v`, `tb/` | Choose external-counter ownership, width and local interface first. Decouple the scheduler/model; qualify one-bit wire delay, channel duplication, frame latency, last-cycle arrival, underrun and reset at 44.1 kHz. The sibling's 48 kHz/256-cycle schedule is not the render budget. |
| Polysynth I2S, Y01-Y03 | Deferred adapt, preferred standalone serializer starting point | FPGA/transport; same destination, choose one source | Select serializer/clock interface first. Qualify width, 44.1 kHz, latency and reset; split the audit's combined late-bit/channel mutation into separate detectable controls. Its bench scrapes verdict text and does not implement a reliable 0/1/2 exit convention by itself. |
| Parasynth SPI, P05-P08 | Deferred adapt of byte transport only | Host protocol; `rtl/spi_ctl.v`, `tb/` | A resolved-patch protocol and SPI selection must precede import. Replace sibling register pages; prove atomic load/trigger/completion/reset, CDC, queue overflow and throughput for the actual clocks. No inherited note/drum semantics. |
| Polysynth UART, Y04-Y05 | Deferred adapt of byte receiver only | Host protocol; `rtl/uart_rx.v`, `tb/` | UART selection and local protocol first. Derive baud divisor; test baud error, continuous traffic, bad stop bits and reset independently of the sibling core. No voice parser import. |
| Parasynth board/reset, P09-P15 | Reject wrapper/constraint copies; local reset design informed by audit | FPGA/board; `fpga/` | A board decision, traced pin attribution and reviewed reset/lock release are prerequisites to a new local wrapper. Recompute clocks and validate physical IO. Missing `verify_fpga_netlist.py` cannot count as executed evidence. |
| Polysynth board/PLL, Y06-Y09 | Reject direct copies | FPGA/board; `fpga/` | Recreate only after board selection and generator/template/vendor attribution. Qualify clock, reset and IO; a reported build with no electrical check does not establish playback. |
| Parasynth PLL search, P16 (context P15) | Deferred adapt; possible later extraction | FPGA/clock; `fpga/scripts/pll_search.py` | An ECP5 board-design need first. Check divider arithmetic with exact rationals and qualified limits. Remove unconditional crystal-dominates prose: the audited 44.1 kHz candidate is +64 ppm. No tolerance is accepted here. |
| Process runner, P17-P18 | Deferred pinned copy with tests; first small reuse candidate | Verification; `tools/run_all.py`, `tests/test_run_all.py` | A concrete need for multi-command timeout/refusal handling first. Declare POSIX support, trusted-command boundary, output budget and exit semantics; preserve misleading-output, timeout/grandchild, actual-exit-code and parallel controls. Audit's 18 passes cover this runner only. |
| Provenance/base-gate portions, P19-P20 | Local; deferred adaptation of isolated primitives only | Reference/artifacts; `src/torchsynth_voice/artifacts.py`, `tests/test_artifacts.py` | A demonstrated gap in the local artifact contract first. Use full hashes, structured argv, complete inputs/runtime and unknown/refusal states. Compare qualified pins, not latest `origin/main`; do not import NumPy/SciPy drum rendering, WAVs or tolerances. |
| Scorecard, P21-P22 (tests P20) | Reject unchanged; retain local implementation | Verification; `src/torchsynth_voice/scorecard.py`, `tests/test_scorecard.py` | No source import authorized. NaN-after-finite can pass upstream. Keep finite-value, explicit refusal, per-property unit, provenance and coverage controls under the local scorecard contract; no tolerance-normalized omnibus score. |
| Capability DAG, P23-P25 | Reject unchanged; local evidence integration if needed | Verification/evidence; `tools/`, `tests/` | A local evidence-consumer requirement first, not a shared framework. A merely existing file must not pass; verify schema, bytes, covered inputs, runtime and negative controls. Tags/ancestry are not qualification. |
| Workflow checker, P26 | Deferred adapt | Verification/tooling; `tools/check_workflows.py`, `tests/` | Demonstrate a gap in current CI checks. Parameterize the directory; retain malformed/trigger/shallow-ref controls and explicit no-files/missing-dependency outcomes. Account for valid jobs without checkout; static acceptance is not execution. |
| Merge simulation, P27 | Deferred adapt | Repository tooling; `tools/check_stale_base.py`, `tests/` | Demonstrate a gap in existing Loom gates before adding another checker. Separate conflicts from other Git errors; test missing refs, independent stale branches, deletions and unusual filenames. Define freshness separately. |
| PDK-free runner, Y12 (tests Y05) | Deferred minimal local adaptation | RTL verification; `tb/run_tb.py`, `tb/` | Local RTL/top/model and simulator contract first. Replace sibling inputs; prove build errors, missing/empty XML and no tests cannot satisfy expected-failure controls. No `klt` evidence may be minted by this convenience runner. |
| Parasynth DSP, P30-P32 | Reject | Voice/numeric; local future `rtl/` and model | No import trigger under this decision. Live paraphonic state, PolyBLEP/ladder/envelopes, ROMs, gain and 48 kHz differ. Derive behavior from pinned Voice; module names are not semantic proof. |
| Polysynth DSP, Y10-Y11 | Reject | Voice/numeric; local future `rtl/` and model | No import trigger under this decision. Note commands, phase width, incremental ADSR, sample width and cadence differ; any reconsideration needs explicit semantic proof and a new decision. |
| Audio measurement, P28-P29 (P19-P20 context) | Reject dependency; local qualified estimators | Measurement; `src/torchsynth_voice/`, `tests/` | Re-derive needed estimators with analytic, decay/detuning, invariance and defect controls. No inherited preprocessing, edge/lead assumptions, floor prose, tail guard, tolerance or reference score. |
| Parasynth `klt` driver/requests, P33-P36 | Reject wrapper; local requests informed by structure | ASIC; `asic/` | Local top, constraints, PDK/library and pinned `klt` first. No copied paths, 7t/clock/PDN assumptions or signed/tie workarounds. Audit's K01 already handles some historical workarounds and has gf180 9t ties, not 7t. Recheck actual supported cells. |
| ORFS material/reports, P37-P42 | Reject flow/techmap copying; evidence checklist only | ASIC/evidence; `asic/`, `evidence/` | No imported platform or alternative flow. Trace original techmap/PDN attribution before any new proposal. Old routed reports are design-specific; DRC counts alone are not signoff. |
| Polysynth signoff, Y13-Y15 | Reject as implementation/evidence; checklist only | ASIC/evidence; `asic/` | Define each required stage against actual local `klt` requests/results, including absent antenna/ERC coverage. An unrun script cannot qualify a stage. |
| `klt`, K01-K07 | Existing shared interface; deferred pinned package use | ASIC integration; `asic/` requests and environment record | When ASIC work starts, review an immutable package/source pin and lock its artifacts/dependencies; record tool, PDK, library and container identities. No fork/extraction of internal functions; rerun local stage-specific controls. The audited pin is a baseline, not a qualified environment. |

Supporting catalog rows P43-P45 are context, with **no import**: received plans
are review input, and the corrected Surge mapping/report does not qualify
TorchSynth or its measurement apparatus. They remain under documentation review
in the audit. P46, Y16 and K08 supply the license texts for the candidates.
Thus all 70 catalog identities have a disposition, including evidence and
attribution companions; none denotes an already reused or qualified component.

## Import, divergence and update control

1. **Open a local adoption/update PR.** Name the need, selected table row,
   destination, responsible maintainer and local interface contract. Include the
   tests and evidence it must satisfy. A change to Voice behavior requires a
   decision before RTL, under [DR-0001](0001-torchsynth-version.md),
   [DR-0002](0002-one-shot-product-profile.md) and
   [DR-0004](0004-verification-claims.md); this record grants no such change.
2. **Capture immutable provenance.** For each imported file, record upstream
   repository, full commit, original path, original SHA-256, license/notice paths
   and hashes, destination and adapted SHA-256 in a local per-component record
   (for example `docs/reuse/<component>.json`). List transitive copied inputs,
   tests and generated outputs. Record generator/version/input hashes and exact
   invocation for generated copies, and verify a repeat generation matches.
   Package use additionally needs the exact distribution/source digest and
   dependency lock. Neither an archive name nor a tag is a pin.
3. **Review bytes and attribution together.** Recompute hashes from the exact
   upstream commit. Preserve copyright headers, applicable license text and
   NOTICE/third-party attributions, and mark local modifications. Keep a
   reproducible diff from upstream to local plus its rationale; review generated
   diffs as source, never overwrite them on build or fetch. Missing attribution,
   inaccessible sources, or hash mismatch refuses adoption. This audit catalog
   stays historical; a new import/update gets a new provenance revision.
4. **Own divergence explicitly.** The local maintainer reviews changes through
   the normal PR process with an independent reviewer. Keep the original pin and
   local patch history even when fixes are deliberately not upstreamed. To
   update, compare old upstream to proposed upstream, then reapply/review local
   patches; classify interface, semantic, license and dependency changes. Record
   removals as well as additions. A conflict or lost negative control blocks the
   update; staying at the old reviewed pin is preferable to silent convergence.
5. **Invalidate affected evidence and requalify.** Changes to covered source,
   tests, generators, inputs, tool/runtime/PDK, interface or rubric cannot inherit
   the old result. Keep old records tied to their old identities; mark affected
   current claims stale/unqualified until new checks finish. A stale diagnostic
   is not a new scorecard-v1 enum: consumers must use the existing refusal forms
   described below. Hash equality alone does not qualify a changed environment.
6. **Land only the reviewed update.** Require the common and candidate-specific
   controls with actual outcomes and an explicit coverage statement. Refuse if
   unavailable prerequisites, semantic disagreement or attribution gaps remain.
   Do not relax tolerances or drop controls to admit a dependency. Roll back by
   reverting the update and restoring its pin/patch record; prior evidence may
   support only the exact old covered inputs/environment, not an unverified
   restoration. No scheduled cross-repository updates are created here.

## Conformance boundary

The local [artifact contract](../ARTIFACT-CONTRACT.md) and
[scorecard contract](../SCORECARD-CONTRACT.md) remain authoritative. Their current
validators/tests are kept; this decision neither replaces them with a common
framework nor claims they already implement a full evidence resolver.
Future integration must check actual referenced bytes and covered identities,
not just valid-looking digests. Invalid/stale supplied evidence must refuse a
verdict; unavailable evidence uses the contract's missing/unavailable forms.
Scorecard v1 expresses refusals as `NO VERDICT` or `MISSING EVIDENCE`, with
reasons and no invented observed value. A new state requires a versioned change.

Every adopted executable needs a passing known-answer case and independently
detected adverse controls. Process success text cannot override a failing exit;
timeout, unavailable tool, empty results or unrun tests cannot count as pass or
as a successfully detected DUT defect. Validate finite values and declared
units/tolerances without changing the local contract's exact-zero option.
Source/runtime/input mutations must invalidate affected evidence. Clean metadata
and schema checks establish neither apparatus qualification nor sound fidelity.

For behavior-bearing reuse, compare named intermediate traces and final samples
against the pinned Voice: declared float-to-fixed error metrics, then sample-exact
fixed-model-to-RTL. Preserve clip timing, normalization, parameter identity and
selected noise. Board wiring, serializer timing and tool operation need their
own controls; DSP similarity cannot substitute. ASIC work uses `klt` and requires
committed, stage-specific evidence before any synthesis/layout/signoff claim.
Generic `klt` friction belongs in `2AMLogic/klayout-tools`; this decision neither
files upstream changes nor authorizes writes there.

## Shared extraction and operator boundary

A second instrument is a reason to compare interfaces, not an automatic shared
master trigger. Consider extracting the runner, serializer/decoder, byte receiver,
clock arithmetic or provenance primitives only when **two actual consumers**
have independently qualified the same small interface and controls, and repeated
duplicate maintenance costs more than the proposed package's coupling. Require:

- named maintainers and consent from both consumers;
- traced licenses/attribution and a versioned, instrument-independent interface;
- independent consumer conformance suites, including negative controls;
- immutable releases, compatibility/update policy and independent consumer pins;
- a migration/rollback proposal that preserves existing evidence identities.

These criteria justify a future proposal, not package creation now. Shared DSP,
board/PDK frameworks, and scorecard/DAG unification have no demonstrated common
qualified contract. `klt` already supplies the shared ASIC interface.

Archived Polysynth [PR #6](https://github.com/2AMLogic/gf180-polysynth/pull/6)
is non-binding input from the audit; its proposed second-instrument rule and
envelope/ROM sharing are not adopted. Organization policy, fleet enrollment,
repository creation, sibling edits and upstream publication remain operator-owned
and need separate explicit authorization. This record governs only local reuse.

## Governance walkthroughs

These are review walkthroughs of the policy, **not executed future imports**.

- **Local runner update:** after a future approved P17/P18 import, suppose long
  simulator output motivates bounded capture. The verification custodian opens
  a local PR: P17's upstream SHA-256 remains
  `4244839476cc33c998a323d7158416842062c1ffe938cc4b788503d8f6cada2e`,
  while the record must contain measured before/after local hashes, changed test
  hashes and the retained upstream-to-local patch. Preserve P18's license and
  provenance. Re-run all inherited runner controls and add truncation/output-limit
  controls that cannot hide the actual return code. Old affected runner evidence
  is unqualified for the update until these run on the declared OS/runtime. No
  future hash or test result is fabricated here. If the upstream pin also changes,
  review both upstream commits and record new source/license hashes separately.
- **Rejected scorecard reuse:** propose importing P21 at catalog SHA-256
  `356f678bdf7ea4b0e9c6adcded27e4ddc19c241c1b9a94df8b7bfafb6c81e08f`
  with P22 rules because the sibling's `--check` exited zero. The verification
  custodian refuses: the audit's required-NaN-after-finite probe returns pass,
  so matching hashes and command success do not satisfy semantic controls.
  Preserve the finding, retain the local scorecard, and transfer none of the
  sibling's results. A proposed fix would need its own reviewed hashes, negative
  controls and requalification; no import is authorized by this rejection.

## Verification of this decision

Recheck the immutable catalog with the audit's source-inventory command, verify
each table ID resolves to a repository/commit/path/hash/license row, check local
links and ADR index/status, and confirm the diff contains only this ADR and its
index entry. The walkthroughs exercise ownership, changed-hash handling, refusal
and evidence invalidation without importing code or manufacturing qualification.

Checks performed on 2026-09-18 against local code baseline
`1315f8f80e5e30fdd9a77d91da4d00e4de844862`, using Python 3.14.7:

| Check | Observed result | Evidence boundary |
| --- | --- | --- |
| Audit source-inventory command, exact GitHub commit/file bytes | 70/70 SHA-256 values matched, including all three license files | Source identity/availability; unresolved vendor attribution remains unresolved. |
| ADR/catalog consistency and relative-link check | 70/70 IDs covered; three repository pins, two document hashes, 13 links and ADR statuses checked | Documentation consistency; DR-0003 remains Proposed. |
| `python3 -m unittest discover -s tests -v`, with `TORCHSYNTH_ROOT` set to the pinned checkout | 75 tests passed in 1.208s; no failures/skips | Existing stdlib suite, including source inventory checks; no Torch installation or rendering required. |
| `python3 tools/check_contract.py` | Exit 0; `contract manifests are internally consistent` | Existing manifest consistency. |
| `python3 -m compileall -q src tests tools` | Exit 0 | Existing Python syntax. |
| Two governance walkthroughs above | Reviewed update and refusal paths against the pinned audit and local contracts | Policy exercises, not simulator tests or completed imports. |

No upstream runner/simulator suite, audio render, FPGA build, ASIC flow or board
run is claimed by this decision. The audit's executed checks retain their own
scope and date; source-byte verification here does not broaden that evidence.
