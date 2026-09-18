# Reusable substrate audit

Audit date: **2026-09-18**. Deliverable for
[#60](https://github.com/2AMLogic/gf180-torchsynth/issues/60), informing the
separate reuse decision in
[#61](https://github.com/2AMLogic/gf180-torchsynth/issues/61).

The strongest small candidate is Parasynth's process runner with its negative
controls. I2S serialization and byte transport are useful starting points after
their interfaces are specified locally. Neither sibling's DSP, board clock,
measurement rubric, nor physical results establish TorchSynth compatibility.
This report recommends adaptations; it vendors no implementation and ratifies
no shared package or organization policy.

## Source boundary and identity

The GitHub REST repository metadata and default-branch commit endpoints were
read at **22:13:59 UTC**. The following were the current default branches at
that instant; subsequent movement does not change this audit's inputs.

| Key | Repository | Default branch / exact audited commit | Archived | Declared license text |
| --- | --- | --- | --- | --- |
| P | `2AMLogic/gf180-parasynth` | `main` / `11be1312d2fd42b3d8cac75de2d12068d4c79f6c` | No | [Apache-2.0][P46] |
| Y | `2AMLogic/gf180-polysynth` | `main` / `010efac225b6173105bfb4a13dcbf1b94121ec6f` | Yes | [Apache-2.0][Y16] |
| K | `2AMLogic/klayout-tools` | `main` / `2b7caa9939af3c4734a0fbd5fbddf7e7b852cc45` | No | [MIT][K08] |

[reuse-audit-sources.json](reuse-audit-sources.json) records **70 individual
files**, their full SHA-256 values, repository keys, and file-specific license
bases. Each source ID below links to an immutable file at the corresponding
commit; its manifest row supplies the full content hash. Companion benches,
reports, requests, and licenses are pinned too. Hashes identify bytes, not
qualification status.

The audit used disposable GitHub commit archives, checked their files against
Git tree blob IDs, and independently fetched the catalogued blobs to recompute
SHA-256. No existing sibling checkout, upstream branch, issue, or PR was
modified. No local or unmerged work was substituted for a default-branch file.
klayout-tools is included because it owns the required ASIC-flow interface.
Analog PLL/POR, USB, and other organization repositories were not promoted into
component candidates: this target has no chosen board or analog clock macro,
and neither USB nor their device-specific circuits are prerequisites for this
documentation audit. This is a scoped substrate inventory, not an organization
hardware qualification.

The comparison target remains [VOICE-CONTRACT.md](../spec/VOICE-CONTRACT.md):
TorchSynth `2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`, default Voice/default
nebula, **one four-second, 176,400-sample mono sound per trigger at 44.1 kHz**.
Canonical batched reference fixtures qualify identity and numerical behavior;
they do not require batched hardware. Resolved scalar execution may use
`batch_size=1, reproducible=False`; its numerical relationship to the batched
reference still needs qualification. Output width, clocks, transport, reset,
and arithmetic remain unratified. No row below silently selects them.

## Transport and board candidates

The identity column supplies the repository/commit/hash mapping through the
source IDs above. Apache-2.0 means the repository declaration; exceptions and
unresolved third-party origins are stated explicitly.

| Candidate / identity / license | Behavior and interface | Maturity and evidence | Assumptions, semantic fit, dependencies | Portability changes and recommendation |
| --- | --- | --- | --- | --- |
| Parasynth I2S: [P01][P01]; benches/model [P02][P02], [P03][P03], [P04][P04]. Apache-2.0. | `clk`, active-low synchronous reset, external 8-bit `cyc`, `sample_valid`, 16-bit sample; BCLK=`cyc[1]`, LRCLK=`cyc[7]`; mono duplicated into 32-bit left/right slots, one-bit I2S delay. | Pin decoder compares against a model and exposes shift/delay/channel controls. Source inspected; integrated verifier **not rerun**. | Scheduler supplies a sample before cycle 255; sample from frame f appears in f+1. Existing core uses 256 cycles at 48 kHz. Depends on shared frame counter and scheduler. | **Adapt** after local width, handshake, latency and reset decisions. Separate serializer timing from offline render scheduling; test last-cycle arrivals, reset and underrun. Preserve external-wire controls; they currently depend on the entire Parasynth model. |
| Polysynth I2S: [Y01][Y01], [Y02][Y02], [Y03][Y03]. Apache-2.0. | Same wire convention, but owns its 8-bit counter; sample held on valid, same word in both channels. | **Rerun:** normal bench decoded 24 left and 24 right words over 25 LRCLK periods; `TB PASS`. Combined late-bit/channel defect produced `TB FAIL (3)`; `make sim-check` exit 0. | 16-bit, no ready/backpressure, caller maintains sample cadence; fixed 256-clock divider assumes 12.288 MHz for 48 kHz. Icarus needed for this bench. | **Adapt**, preferred small serializer starting point if a standalone counter fits. Requalify at 44.1 kHz and chosen width; split the combined mutation into independently detected faults. Its Makefile scrapes verdict text because the bench uses `$finish`; do not assume it implements the runner's 0/1/2 convention. |
| Parasynth SPI receiver: [P05][P05], [P06][P06], [P07][P07], [P08][P08]. Apache-2.0. | Mode 0, CS-framed 48-bit `{CTL,A,D}` transactions; 8-bit address, 32-bit data, section/flag bits, four-entry queue, status MISO; drains a snapshot at frame tick. | Real receiver and model-origin write bench; retained rev-1 receiver is a negative control. Inspected, **not rerun**. | Two-flop input synchronization; SCK at most core/4. Queue-depth argument relies on 48 kHz frame draining and a stated 2 MHz SCK. Pages/registers describe continuous voice/drums. | **Adapt** transport only after resolved-patch protocol #62. Recompute throughput, CDC and overflow bounds; define atomic patch load/trigger/completion/reset. Reject the existing register map as TorchSynth's host interface. |
| Polysynth UART: [Y04][Y04]; core tests [Y05][Y05]. Apache-2.0. | 8N1 RX, two-flop synchronization plus edge history; synchronous byte/one-cycle valid, bad-stop bytes dropped. `CLKS_PER_BIT` defaults to 107. | Integrated cocotb tests and injected UART sampling fault exist; **not rerun**. | Byte framing is separable from DSP; no TX or flow control. Default baud divisor assumes the sibling clock. Tests also require its core/reference. | **Adapt** only if UART is selected. Qualify baud error, sustained traffic, framing errors and reset independently; do not import the voice command parser or note-event semantics. |
| Parasynth board/reset wrappers: [P09][P09], [P10][P10], [P11][P11], [P12][P12]. Apache-2.0 declaration; vendor-derived pin attribution unresolved. | Generic POR wrapper and ULX3S ECP5 PLL/lock counter/button reset, SPI/I2S routing, LED wire sniffer, ESP32 enable. | [P13][P13] records a different build commit and source blob IDs; [P14][P14] checks routed source-list equality. [P15][P15] explicitly reports no board measurement. `fpga/verify_fpga_netlist.py`, named in P10, is **absent** from the audited tree. | ULX3S 25 MHz, EHXPLLL, initialized FPGA registers, board-specific pins and polarity; nominal output 12.288135593 MHz. Needs `synth_top` reset synchronizer. These are not ASIC POR cells. | **Adapt** reset/lock sequencing ideas; **reject direct wrapper/constraint copying**. Select the board first, trace pin provenance, review asynchronous button/reset release, regenerate clock parameters, and run a real board check before claiming playback. |
| Polysynth board/PLL: [Y06][Y06], [Y07][Y07], [Y08][Y08], [Y09][Y09]. Apache-2.0 declaration; generated PLL/template and verbatim vendor LPF terms need tracing. | Icepi Zero rev 1.3, 50 MHz → ECP5 secondary PLL output; UART and Pi-header PCM I2S, optional MCLK. | README reports an FPGA build and explicitly no programming/electrical validation. **No build rerun**. | Different board, pinout, 48 kHz family, four-voice core. Generated PLL has tool-version caveats. | **Reject direct reuse**. Pin constraints are neither portable nor licensed merely by the enclosing repository. Recreate against the chosen board and generator, retain their attribution, and qualify clock/reset/IO independently. |
| Parasynth clock search: [P16][P16], context [P15][P15]. Apache-2.0. | Enumerates ECP5 divider candidates with PFD/VCO/output bounds; configurable input and target MHz; returns ranked error. Python standard library. | **Rerun at 44.1 kHz:** `--fin 25 --target 11.2896 --top 1` returns 11.290322581 MHz, +64.00 ppm, 44,102.823 Hz. This is arithmetic, not a board observation. | The 256× target is conditional on adopting that serializer divider, not a core cycle budget. Bounds come from the sibling's datasheet interpretation, not independently requalified here. | **Adapt; extract later** as a small board-design helper if useful. Add exact-rational checks and board-specific limits. Fix its unconditional final claim that a ±30 ppm crystal dominates: it is false for this +64 ppm result. No sample-rate tolerance is accepted by this audit. |

## Verification and evidence candidates

| Candidate / identity / license | Behavior and interface | Maturity and evidence | Assumptions, semantic fit, dependencies | Portability changes and recommendation |
| --- | --- | --- | --- | --- |
| Process runner [P17][P17] + tests [P18][P18]. Apache-2.0. | Shell commands → per-command state, actual return code, output and duration; timeout kills process group; bounded overall 0/1 exit. Verifier exit 2 means no evidence. | **18/18 upstream tests passed** in this audit, including misleading output, exit-code preservation, timeout/grandchild cleanup, parallelism and JSON. | Python stdlib, POSIX process groups; pytest only for tests. Trusted local shell commands; captures output in memory. `run_one(verifier=False)` exists, but the batch CLI always uses verifier semantics. | **Copy pinned** with tests as a future small local tool when needed, then **extract later** only after a second identical use. Declare supported OS and exit convention; add output bounds if long simulator logs demand them. Never run arbitrary untrusted command strings. No package needed now. |
| Provenance/base gate portions of [P19][P19], tests [P20][P20]. Apache-2.0. | Records commit/branch/dirty state, diff plus untracked-content hash, command, input/artifact hashes, engine, Python, time and `allow_stale`; checks selected dependencies against local `origin/main`. | Source and relevant test cases inspected; full `test_run_case.py` **not run**. Whole-batch refusal and explicit invalid metric without `error` are present. | The surrounding module imports NumPy/SciPy and drum models. Commit/input/diff hashes are truncated; argv is joined as text. Missing `origin/main` returns `checked=False`, not refusal; it does not fetch the remote. | **Adapt; extract later** only the provenance/refusal primitives. Use full hashes, structured argv, runtime/tool versions, complete declared inputs and explicit unknown states. Compare qualified pinned inputs rather than assuming latest main is normative. Do not import the renderer, reference WAVs or tolerances. |
| Scorecard [P21][P21], rules [P22][P22], tests [P20][P20]. Apache-2.0. | CSV required metrics + JSON results → pass/fail/no-verdict/not-run, engine and separate coverage/agreement. | **Rerun `--check`:** existing records yield 18/100 valid, 8 pass, 10 fail, 1 no verdict, 81 not run; all fixed-model. No audio rerendered. Probe found a required NaN after a finite metric still yields `pass`. | Shape checks are shallow; provenance presence is not hash validation. No per-result STALE state or finite-number gate; uses worst tolerance-normalized distance. | **Reject unchanged; adapt concepts** in #87. Validate schema, finiteness, positive tolerances, current covered hashes and explicit STALE; preserve per-property units and release conjunction. The existing records qualify neither the apparatus nor TorchSynth. |
| Capability DAG [P23][P23], [P24][P24], [P25][P25]. Apache-2.0. | Evidence execution/results, tags and git `covers` history → graph status and README. | Source inspected; **probe:** `run_evidence` accepts an arbitrary existing `evidence_file` as passed. Full graph/verifiers **not run**. | Repo-specific node groups/tags, shell tools, pytest; staleness checks committed history, not all dirty/untracked/runtime inputs. Assumes a tag was cut after qualification. | **Reject unchanged; adapt concepts** in #85. Validate evidence records and content hashes; no green state from mere file existence or tag ancestry. Separate not-run/error from disagreement and require controls. A shared graph framework is premature. |
| Workflow checker [P26][P26]. Apache-2.0. | Parses YAML; checks triggers, checkout and `origin/...` availability. Exit 0/1/2. | **Rerun:** four current workflows accepted. Three temp-fixture controls rejected malformed YAML, schedule-only trigger, and shallow origin access. | PyYAML; GitHub Actions-specific heuristics, not its complete schema or shell execution. Hard-coded repository workflow directory. | **Adapt; extract later** as a small independent lint only if needed. Do not treat acceptance as workflow execution; parameterize directory and preserve no-files/missing-dependency outcomes. Its checkout rule can reject valid jobs that need no source. |
| Merge simulation [P27][P27]. Apache-2.0. | `git merge-tree --write-tree` checks the merge result against base/head path sets, allowing deliberate deletions. | **Three temp-repo probes:** independent stale branch retains main-only file (0); conflicting edits (1); missing base (2). | Modern Git, available ancestry/refs; no network freshness check. “Deliberate” means absent from head relative to merge base, not human intent. | **Adapt** if Loom's existing pre-push/review gates leave a gap. Distinguish merge conflict from other `merge-tree` errors (currently all nonzero there become 1); test filenames with unusual characters. Avoid a second redundant merge workflow. |
| PDK-free runner [Y12][Y12], tests [Y05][Y05]. Apache-2.0. | cocotb runner with simulator/seed/defines, results XML, distinct 0 pass / 1 failed tests / 2 did-not-run. | Code inspected; cocotb suite **not rerun**. Explicitly does not mint a `klt` evidence record. | cocotb + Icarus/Verilator, sibling RTL/model/parameters and result schema. | **Adapt** a minimal local runner when RTL exists. Replace sources/top/model and prove empty XML/build failures cannot satisfy expected-failure controls. Keep `klt` as the ASIC-flow interface. No need to share its instrument-specific harness. |

## DSP and measurement: negative findings

| Candidate / identity / license | Behavior / actual evidence | Semantic mismatch and dependencies | Recommendation |
| --- | --- | --- | --- |
| Parasynth [voice model][P30], [voice RTL][P31], [contract][P32]. Apache-2.0. | Continuous paraphonic three-oscillator voice, PolyBLEP, ladder, glide, integer envelope/gain paths; sibling model/RTL verification claims do not target TorchSynth. Inspected, not rerun. | TorchSynth has sine plus square/saw, seeded noise, two LFOs, six advance-informed ADSRs, endpoint-aligned control interpolation and conditional whole-clip peak normalization; **no ladder**. Persistent live-note state, envelope laws, 48 kHz, saturation, gain and ROMs are different. Depends on sibling fixed arithmetic and tables. | **Reject DSP reuse**, including envelope/oscillator/ROM fragments chosen merely by name. Derive local behavior from the pinned Voice and qualify float → fixed → exact RTL. |
| Polysynth [voice RTL][Y10], [numeric contract][Y11]. Apache-2.0. | Per-frame phase/envelope advance and note commands; parameterized polyphonic core. Inspected, not rerun. | 24-bit phase, incremental live ADSR, 16-bit samples, 48 kHz and note-index transport are not TorchSynth's graph/timing/noise/normalization. Even a standalone oscillator needs a semantic proof before transfer. | **Reject DSP reuse**; neither a matching module name nor the unmerged shared-substrate proposal establishes equivalence. |
| Parasynth [audio estimators][P28], [case preparation/ratios][P19], tests [P29][P29], [P20][P20]. Apache-2.0. | Tests exist, but the current code retains the disputed filter edge, lead clamp, fixed Hann-floor prose, finite-record tail guard and nominal line probes. **No estimator suite or reference audio rerun** here. | NumPy/SciPy, external recordings/plugins, TR-808/Minimoog rubric. Preparation trims/filters/resamples; such changes cannot enter TorchSynth's primary sample comparison. Stationary known-answer tests do not qualify decaying or detuned inputs. | **Reject as a measurement dependency now.** Re-derive needed estimators and preparation domains with analytic fixtures/invariance/negative controls in #86 and related measurement work. Keep the useful invalid/no-distance pattern, not their tolerance values or scores. |

The substantial [comment on #60](https://github.com/2AMLogic/gf180-torchsynth/issues/60#issuecomment-5736627875)
was an investigation checklist. Live issue/PR reads and pinned source inspection
give the following narrower conclusions as of this audit:

| Reported concern | Rechecked state and source | What this audit establishes |
| --- | --- | --- |
| Filter edge and insufficient lead | Parasynth [#101](https://github.com/2AMLogic/gf180-parasynth/issues/101) open; `prepare` still clamps lead with `max(0, ...)`; `band_energy` calls `sosfiltfilt` without explicit padding policy. | The mechanism remains present. The issue's later comments correct its original blanket blast-radius claim and distinguish congas from rimshot/cowbell; do not repeat “every failure is an artifact.” Reported dB magnitudes were not independently reproduced here. |
| Nominal partial probes / decay-weighted energy | [#108](https://github.com/2AMLogic/gf180-parasynth/issues/108), [#109](https://github.com/2AMLogic/gf180-parasynth/issues/109) open; `tone_ratio_db` accepts specified frequencies, cowbell uses 800/540 Hz; `band_pair_db` integrates energy. | No new claim that these measure amplitude independently of detuning/decay. Their published sensitivity numbers are upstream reports, not this audit's measurements. |
| T20 truncation and inharmonic floor | [#118](https://github.com/2AMLogic/gf180-parasynth/issues/118), [#119](https://github.com/2AMLogic/gf180-parasynth/issues/119) open; last backward-integral sample gates truncation, and fixed −54 dB Hann-floor prose survives. | These preconditions/floor assumptions remain unresolved in the pin. This does not imply every T20 estimate is wrong. |
| Surge mapping | [PR #87](https://github.com/2AMLogic/gf180-parasynth/pull/87) merged at `74ce6a03333cc6dbd2a783d8bacc7f8ba7e34ba4`, 2026-09-18 20:42:40 UTC; corrected mapping/readback code [P44][P44] and report [P45][P45] are on the audited default branch. | “The rig is still awaiting this fix” is stale. Pre-fix figures remain unsuitable; the current report still needs its own estimator qualifications. No proprietary plugin or audio corpus was fetched. |

## ASIC-flow candidates and limits

| Candidate / identity / license | Behavior / maturity / evidence | Dependencies, fit and portability work | Recommendation |
| --- | --- | --- | --- |
| Parasynth `klt` driver [P33][P33], requests [P34][P34], [P35][P35], log [P36][P36]. Apache-2.0 declaration; PDN values attributed to ORFS. | Targets a 7-track ladder block; script strips signed declarations and inserts ties. Log identifies `klt 0.5.0+g604c4fb8a1ce`, ends after synthesis/netlist edits, and does **not** establish completed P&R. | Hard-coded sibling provisioning/PDK location, 81.38 ns clock, 7t cells, floorplan/PDN and Docker assumptions. Needs local top/constraints and independently verified PDK/library identity. | **Adapt request structure; reject verbatim wrapper.** Current K01 already strips signed declarations and emits `setundef` before `hilomap` for supported tie libraries. Its tie table still has gf180 **9t, not 7t**. Historical workaround presence is not proof current `klt` has the same defects. |
| Parasynth ORFS material [P37][P37], techmaps [P38][P38], [P39][P39], committed reports [P40][P40], [P41][P41], [P42][P42]. Repository Apache-2.0; techmaps explicitly derived from ORFS, original license/notice not established here. | Separate historical routed design reports exist for `joined-d1e5068`; they have their own source/constraints and are not results for TorchSynth or necessarily today's sibling source. No flow rerun. | ORFS image/platform, 7t cells, PDN, extraction corners and design-specific timing/area assumptions. A routed DRC count is not signoff/LVS/silicon evidence. | **Reject direct flow/techmap copying.** Use reports as a checklist for evidence contents. Trace original ORFS attribution before any copying; bring generic missing capabilities to klayout-tools without bypassing `klt`. |
| Polysynth signoff script [Y13][Y13], status [Y14][Y14], instructions [Y15][Y15]. Apache-2.0. | Proposed DRC → extraction → LVS → STA chain; explicit warning that nothing was run on gf180mcu; no antenna/ERC stage. | Specific core/library/config files, 5LM variant, installed PDK and tools. Script creation cannot qualify any stage. | **Reject as evidence; adapt only as a future checklist** against current `klt` schemas and actual local outputs. Do not advertise this as a reusable proven signoff flow. |
| Current `klt`: synthesis [K01][K01], P&R [K02][K02], provenance [K03][K03], engine [K04][K04], CLI [K05][K05], tests [K06][K06], dependencies [K07][K07]. MIT. | Structured requests/results, tool/PDK/input provenance, generated scripts and explicit errors. Source includes signed-netlist regression tests and supported tie-cell tests; **suite and ASIC tools not run** here. | Python package internals, KLayout, external Yosys/OpenROAD/PDK and selected libraries. This is existing shared infrastructure, not a pair of standalone functions. | **Adapt local requests to a pinned `klt` dependency** when ASIC work begins; do not extract or fork its internals. Preserve MIT attribution if copying ever becomes justified. Record tool/PDK/container identities and stage-specific evidence; report generic friction upstream. |

No gf180mcu synthesis, layout, signoff, hardware playback, or TorchSynth sound
fidelity is established by this audit. Existing sibling evidence is useful
because it names the producing design and limitations, not because it transfers
qualification to another instrument.

## Non-binding proposals and extraction boundary

Polysynth [PR #6](https://github.com/2AMLogic/gf180-polysynth/pull/6) is **open,
unmerged, and proposed** on the archived repository. Its head is
`b60e424ec570d5ef988c0b2c05dc71ba5eed1825`; the proposed
[`0003-sibling-instrument-repositories.md`](https://github.com/2AMLogic/gf180-polysynth/blob/b60e424ec570d5ef988c0b2c05dc71ba5eed1825/spec/decision-records/0003-sibling-instrument-repositories.md)
is absent from audited `main`. Its proposed single-master trigger at the second
instrument, its suggested common envelopes/ROMs, and its historical repository
counts are **non-binding input**, not canonical code or ratified cross-repo
policy. No organization policy was inferred from it. Parasynth's
[received plans][P43] also explicitly distinguish review input from the
repository's position. User-owned branches, draft fixes and references to local
measurements in issue comments were not promoted into present-day evidence.

Small independent seams worth considering after a real local need are the
process runner plus its tests, byte receiver, serializer plus a wire decoder,
clock-search arithmetic, and provenance/refusal primitives. Each can have a
small interface and its own negative controls. Even here, the two I2S candidates
differ in counter ownership, and the board/transport decisions are unresolved.

A shared instrument core, unified DSP library, board/PDK framework, or shared
scorecard/DAG package is premature: the consumers disagree on semantics and
have not qualified a common interface. `klt` already owns the ASIC abstraction.
Issue #61 owns the eventual copy/package/local-implementation decision, update
policy, attribution and divergence rules. This audit does not install a moving
Git dependency, create a submodule, extract a package, or enroll a fleet repo.

## Executed checks and reproductions

Checks ran on macOS arm64 using Python **3.14.7** for target stdlib checks and
Python **3.13.2**, pytest **9.1.1**, PyYAML **6.0.3** in a disposable audit
environment for sibling tooling. Git **2.55.0** and Icarus Verilog **13.0** were
used. The root project's environment was not modified. Test-generated files
were confined to disposable snapshots/temp fixtures; catalogued source bytes
were verified unchanged.

| Command / check | Observed outcome | Scope of evidence |
| --- | --- | --- |
| `python3 -m unittest discover -s tests -v` | **6 tests passed**, 0 failures/skips | Existing TorchSynth repository unit suite; no new runtime code in this PR. |
| `python3 tools/check_contract.py` | Exit **0**, `contract manifests are internally consistent` | Manifest consistency, not rendered audio. |
| `python3 -m compileall -q src tests tools` | Exit **0** | Existing Python syntax. |
| Parasynth `python -m pytest -q -p no:cacheprovider tools/test_run_all.py` | **18 passed in 9.32s** | Runner controls only, not every upstream test. |
| Polysynth `make -C fpga sim-check` | Exit **0**; normal `TB PASS`, injected `TB FAIL (3)` | One positive simulation and one caught combined fault; 24 words/channel. No synthesis or hardware. |
| Parasynth `python tools/check_workflows.py` | Exit **0**, four workflows accepted | Static heuristics; three additional adverse fixtures returned 1. No GitHub workflow was dispatched upstream. |
| Parasynth `python tools/scorecard.py --check` | Exit **0**, counts recorded above | Re-evaluation of committed records; **no new fidelity measurements**. |
| Eight focused probes | **8/8 observations reproduced** | Three workflow rejection controls, three merge fixtures, and two confirmed evidence-tool gaps. The gaps are findings, not qualification passes. |
| Parasynth `python fpga/scripts/pll_search.py --fin 25 --target 11.2896 --top 1` | Exit **0**, +64.00 ppm | Candidate-divider arithmetic only. |
| Source inventory | **70/70** pinned Git blobs and independently fetched SHA-256 values match | Immutable links, source integrity, and license-source availability. |

No heavy Torch dependency was required. Unrun suites are identified per row;
no upstream all-tests pass, FPGA build, physical flow, or board run is implied.

To reproduce the two evidence-tool gaps from the pinned Parasynth snapshot:

```python
import pathlib
import sys
import tempfile

sys.path.insert(0, "tools")
import compile_dag
import scorecard

case = {"required_measurements": "good; bad"}
result = {
    "engine": "fixed-model",
    "provenance": {
        "worktree": {"commit": "probe"},
        "command": "probe",
        "inputs": {"x": "probe"},
    },
    "metrics": {
        "good": {"valid": True, "error": 0.0, "tolerance": 1.0},
        "bad": {"valid": True, "error": float("nan"), "tolerance": 1.0},
    },
}
assert scorecard.evaluate(case, result)["state"] == "pass"  # observed gap
with tempfile.TemporaryDirectory() as directory:
    path = pathlib.Path(directory) / "not-evidence.txt"
    path.write_text("This file does not establish a passed check.\n")
    assert compile_dag.run_evidence("probe", {"evidence_file": str(path)})[0]
```

Workflow fixtures were isolated by assigning `check_workflows.WF` to a temporary
directory containing one `probe.yml`: respectively `on: [`, a mapping with only
`schedule` and empty jobs, and a push job with shallow `actions/checkout@v4`
followed by `git diff origin/main`. Each invocation of `main()` returned 1.
Merge fixtures used a temporary Git repository with a common ancestor: a
main-only file plus an independent topic-only file returned 0, conflicting
edits of a common file returned 1, and `--base missing-ref` returned 2.
These fixtures did not write into any audited source repository.

The source inventory can be rechecked without any DSP installation. From this
repository root, this command retrieves exact blobs through the GitHub contents
API (GitHub CLI authentication/rate limits apply), verifies the full hashes, and
fails on an inaccessible link or mismatch:

```python
import hashlib
import json
import subprocess
from pathlib import Path

catalog = json.loads(Path("docs/reuse-audit-sources.json").read_text())
for row in catalog["files"]:
    repo = catalog["repositories"][row["repository"]]
    endpoint = f"repos/{repo['repo']}/contents/{row['path']}?ref={repo['commit']}"
    raw = subprocess.check_output([
        "gh", "api", endpoint,
        "-H", "Accept: application/vnd.github.raw+json",
    ])
    assert hashlib.sha256(raw).hexdigest() == row["sha256"], row["id"]
print(f"{len(catalog['files'])} pinned source hashes match")
```

Acceptance mapping: current default branches/date/full hashes are recorded;
DSP has explicit semantic rejection reasons; archived/proposed policy and
unmerged work are separated from canonical source; negative findings include
executed controls; small extraction seams are distinguished from premature
shared infrastructure; the change consists solely of this report and its source
inventory. Governance implementation remains the separate #61 deliverable.

[P01]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/rtl-sketch/i2s_tx.v
[P02]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/rtl-sketch/tb_top_bx.v
[P03]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/rtl-sketch/verify_synth_top.py
[P04]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/model/synth_top_model.py
[P05]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/rtl-sketch/spi_ctl.v
[P06]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/rtl-sketch/tb_ctl.v
[P07]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/rtl-sketch/verify_ctl.py
[P08]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/spec/decision-records/0007-control-interface-spi-register-writes.md
[P09]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/fpga/rtl/fpga_top.v
[P10]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/fpga/rtl/ulx3s_top.v
[P11]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/fpga/boards/ulx3s.lpf
[P12]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/fpga/Makefile
[P13]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/fpga/reports/provenance.txt
[P14]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/fpga/reports/scope.txt
[P15]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/docs/fpga-clock.md
[P16]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/fpga/scripts/pll_search.py
[P17]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/tools/run_all.py
[P18]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/tools/test_run_all.py
[P19]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/tools/run_case.py
[P20]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/tools/test_run_case.py
[P21]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/tools/scorecard.py
[P22]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/docs/scorecard/README.md
[P23]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/tools/compile_dag.py
[P24]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/docs/dag.json
[P25]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/docs/dag-results.json
[P26]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/tools/check_workflows.py
[P27]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/tools/check_stale_base.py
[P28]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/model/audio_measure.py
[P29]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/model/test_audio_measure.py
[P30]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/model/voice_fx.py
[P31]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/rtl-sketch/voice_dp.v
[P32]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/spec/NUMERIC-CONTRACT.md
[P33]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/pnr/klt/ladder_dp/run-klt.sh
[P34]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/pnr/klt/ladder_dp/synth_request.json
[P35]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/pnr/klt/ladder_dp/par_request.json
[P36]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/pnr/klt/ladder_dp/run.log
[P37]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/pnr/orfs/README.md
[P38]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/pnr/orfs/gf180_7t/cells_adders.v
[P39]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/pnr/orfs/gf180_7t/cells_latch.v
[P40]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/pnr/orfs/evidence/synth_top/joined-d1e5068/SUMMARY.md
[P41]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/pnr/orfs/evidence/synth_top/joined-d1e5068/6_report.log
[P42]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/pnr/orfs/evidence/synth_top/joined-d1e5068/sta_corners.log
[P43]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/docs/plans/README.md
[P44]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/model/reference_rigs.py
[P45]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/docs/surge-waveform-mapping.txt
[P46]: https://github.com/2AMLogic/gf180-parasynth/blob/11be1312d2fd42b3d8cac75de2d12068d4c79f6c/LICENSE
[Y01]: https://github.com/2AMLogic/gf180-polysynth/blob/010efac225b6173105bfb4a13dcbf1b94121ec6f/fpga/i2s_tx.v
[Y02]: https://github.com/2AMLogic/gf180-polysynth/blob/010efac225b6173105bfb4a13dcbf1b94121ec6f/fpga/tb_i2s.v
[Y03]: https://github.com/2AMLogic/gf180-polysynth/blob/010efac225b6173105bfb4a13dcbf1b94121ec6f/fpga/Makefile
[Y04]: https://github.com/2AMLogic/gf180-polysynth/blob/010efac225b6173105bfb4a13dcbf1b94121ec6f/rtl/uart_rx.v
[Y05]: https://github.com/2AMLogic/gf180-polysynth/blob/010efac225b6173105bfb4a13dcbf1b94121ec6f/tb/test_synth_core.py
[Y06]: https://github.com/2AMLogic/gf180-polysynth/blob/010efac225b6173105bfb4a13dcbf1b94121ec6f/fpga/top.v
[Y07]: https://github.com/2AMLogic/gf180-polysynth/blob/010efac225b6173105bfb4a13dcbf1b94121ec6f/fpga/pll.v
[Y08]: https://github.com/2AMLogic/gf180-polysynth/blob/010efac225b6173105bfb4a13dcbf1b94121ec6f/fpga/icepi-zero.lpf
[Y09]: https://github.com/2AMLogic/gf180-polysynth/blob/010efac225b6173105bfb4a13dcbf1b94121ec6f/fpga/README.md
[Y10]: https://github.com/2AMLogic/gf180-polysynth/blob/010efac225b6173105bfb4a13dcbf1b94121ec6f/rtl/synth_voice.v
[Y11]: https://github.com/2AMLogic/gf180-polysynth/blob/010efac225b6173105bfb4a13dcbf1b94121ec6f/spec/NUMERIC-CONTRACT.md
[Y12]: https://github.com/2AMLogic/gf180-polysynth/blob/010efac225b6173105bfb4a13dcbf1b94121ec6f/tb/run_tb.py
[Y13]: https://github.com/2AMLogic/gf180-polysynth/blob/010efac225b6173105bfb4a13dcbf1b94121ec6f/asic/run-signoff.sh
[Y14]: https://github.com/2AMLogic/gf180-polysynth/blob/010efac225b6173105bfb4a13dcbf1b94121ec6f/asic/README.md
[Y15]: https://github.com/2AMLogic/gf180-polysynth/blob/010efac225b6173105bfb4a13dcbf1b94121ec6f/AGENTS.md
[Y16]: https://github.com/2AMLogic/gf180-polysynth/blob/010efac225b6173105bfb4a13dcbf1b94121ec6f/LICENSE
[K01]: https://github.com/2AMLogic/klayout-tools/blob/2b7caa9939af3c4734a0fbd5fbddf7e7b852cc45/src/klayout_tools/synthesize.py
[K02]: https://github.com/2AMLogic/klayout-tools/blob/2b7caa9939af3c4734a0fbd5fbddf7e7b852cc45/src/klayout_tools/place_and_route.py
[K03]: https://github.com/2AMLogic/klayout-tools/blob/2b7caa9939af3c4734a0fbd5fbddf7e7b852cc45/src/klayout_tools/_provenance.py
[K04]: https://github.com/2AMLogic/klayout-tools/blob/2b7caa9939af3c4734a0fbd5fbddf7e7b852cc45/src/klayout_tools/_openroad_engine.py
[K05]: https://github.com/2AMLogic/klayout-tools/blob/2b7caa9939af3c4734a0fbd5fbddf7e7b852cc45/docs/cli/synthesize.md
[K06]: https://github.com/2AMLogic/klayout-tools/blob/2b7caa9939af3c4734a0fbd5fbddf7e7b852cc45/tests/test_synthesize.py
[K07]: https://github.com/2AMLogic/klayout-tools/blob/2b7caa9939af3c4734a0fbd5fbddf7e7b852cc45/pyproject.toml
[K08]: https://github.com/2AMLogic/klayout-tools/blob/2b7caa9939af3c4734a0fbd5fbddf7e7b852cc45/LICENSE
