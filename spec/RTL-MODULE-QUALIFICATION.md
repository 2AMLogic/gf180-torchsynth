# RTL module qualification gate v1

## Scope and ownership

This document declares the **aggregation and gating layer** over the
per-module fixed-model-to-RTL conformance flows that issues #69-#77 landed
(#78). It owns:

- `tools/qualify_rtl_modules.py` — the aggregate runner, the diagnostic gate,
  the coverage report and the evidence-record writer.
- `tb/rtl-lint-baseline.json` — the committed waiver ledger the gate ratchets
  against.
- `tests/test_rtl_module_qualification.py` — the registered evaluator for the
  `rtl-module-qualification-v1` check.
- this document, and the `rtl-module-qualification` job in
  `.github/workflows/tb-sim.yml`.

It does **not** own, re-implement or re-verify any module engine, testbench or
vector set. Every bit-exact comparison, planted mutation and replay check
remains exactly where #69-#77 put it: in `tb/run_tb.py`'s lanes, documented
per module in `tb/README.md`. This layer runs them together, turns
previously-unexamined simulator diagnostics into failures, reports coverage
with stated exclusions, and writes one indexed record.

It changes no arithmetic profile, noise policy, normalization, parameter
ordering or clip timing. Any finding here that would require such a change is
a decision record, not an edit from this layer.

## Lane inventory

The lanes this layer aggregates are exactly `tb/run_tb.py`'s own `command`
choices. `tests/test_rtl_module_qualification.py` re-derives the list from
that file and fails if `tools/qualify_rtl_modules.py` drifts from it, so a
newly added lane cannot silently escape the gate.

| Lane | Issue | Conformance axes carried |
| --- | --- | --- |
| `selftest` | #68 | reset-replay |
| `anchor` | #68 | min-max |
| `adsr` | #70 | reset-replay, min-max, ties, discrete-modes |
| `patch` | #69 | reset-replay, discrete-modes |
| `lfo` | #71 | reset-replay, min-max, ties, discrete-modes |
| `modmatrix` | #72 | reset-replay, min-max, overflow-saturation |
| `vco` | #73 | min-max, ties, overflow-saturation |
| `vco2` | #74 | min-max, ties, discrete-modes, overflow-saturation |
| `noise` | #75 | reset-replay, min-max |
| `mix` | #76 | reset-replay, min-max, overflow-saturation |
| `normreplay` | #77 | reset-replay, min-max, ties, overflow-saturation |

The axes are #78's own acceptance criteria ("reset/replay, min/max/ties/
overflow/saturation and discrete modes are covered"). Every axis must be
claimed by at least one lane; the evaluator enforces that too. Per-lane detail
— which named vectors, fixture classes and directed matrices carry each axis —
stays in `tb/README.md`, which remains the module-level reference.

### Lint units

The gate compiles each lane's **own** source set, mirroring the exact file
lists `tb/run_tb.py` hands to `iverilog`. `normreplay` has two lint units
because its lane performs two compiles: the replay engine's bench, and issue
#211's `render_binding_top` integration bench.

## Integrated one-shot lanes (issue #264)

Two further flows are *standalone harnesses*, not `tb/run_tb.py` commands, so
they are deliberately **not** in the module-lane list above (whose equality
with `run_tb.py`'s choices is still enforced). They are inventoried and
dispatched separately by `tools/qualify_rtl_modules.py`
(`INTEGRATED_LANES`), and the harnesses remain the sole owners of their
conformance checks -- the gate never re-implements a simulation check and
never reads a verdict out of their stdout:

| Lane | Harness | Evidence it writes | What it covers |
| --- | --- | --- | --- |
| `oneshot-tail-chain` | `tb/run_oneshot.py` | `oneshot-evidence.json` | mixer (#76) -> replay controller (#77) over **host-fed** source/amplitude streams; **not** the whole-voice top |
| `oneshot-whole-voice` | `tb/run_voice.py` | `voice-evidence.json` | the integrated whole-voice top (#70-#77); the host still supplies the ratified shadow words, S1 entry words, C8 noise bytes and phase enables |

Per lane the aggregate ledger (the `== integrated lanes ==` block of the
transcript, which the evidence record's log hash covers) names the harness,
the selected profile, `executed` / `artifact` / `not-run` status, the exit
result, the coverage numbers read from the harness's own record (cases,
DETECTED/NOT DETECTED controls, both normalization branches), the lint units
and a profile/scope label.

**Two ways to include a lane.** `--integrated all|<list>` *executes* each
harness (`--integrated-profile`, default `regression`; per-lane timeout; the
harness's own `--workdir`). `--integrated-artifacts <dir>` instead *consumes*
the artifacts the existing `tb-sim.yml` producer jobs
(`oneshot-tail-chain`, `oneshot-whole-voice`) already wrote, so the bounded
aggregate job does not re-run ~1.5 hours of simulation. Consumed evidence is
bound to this revision: the producer's result (`--producer-result LANE=...`)
must be exactly `success`, and the record must pass
`tools/verify_oneshot_evidence.py`'s checks against `git rev-parse HEAD` -- PASS
result, clean tree, matching `git_head`, and digested RTL/vector sources still
byte-identical in this tree -- plus the requested profile, the lane's schema and
its declared obligations. Anything absent, unreadable, malformed, wrong-schema,
wrong-profile, stale, failed, timed out, cancelled, non-zero-exit (including the
harnesses' own exit 3 = no iverilog and exit 4 = inconclusive corrupt capture)
or carrying an undetected required mutation is a **failure**, never a pass.

**Profile honesty.** A `regression` result is labelled *regression profile
only (bounded; NOT full-profile qualification)* in the console, the ledger and
the record's `reason`; regression evidence cannot satisfy
`--integrated-profile full` (the profile, case-count and obligation checks all
refuse it). The tail chain's label always carries its host-fed scope.

**Verdict.** `PASS` now additionally requires every integrated lane to have
been executed/consumed *and enforceable*: the lane's lint units and runtime
transcript must be baselined in `tb/rtl-lint-baseline.json`, whose
`provenance.toolchain` must be CI's (Icarus 12.0, Verilator 5.020). Until that
CI-generated baseline is committed, the integrated lanes' diagnostics are
**reported but not enforced** and the verdict is `NO VERDICT` with the reason
stated -- neither silently waived nor gated against a baseline nobody
generated for them. The partial-selection `NO VERDICT` behaviour for module
lanes is unchanged.

**Lint units.** `oneshot-tail-chain` and `oneshot-whole-voice` each lint the
exact source set the harness hands to `iverilog` (`INTEGRATED_LINT_UNITS`).

**Baseline provenance and regeneration.** `--update-baseline` refuses any
toolchain that is not CI's unless `--allow-foreign-toolchain` is passed (which
records the actual versions, so the lanes stay unbaselined), refuses to run
against a failed/stale integrated lane, writes `provenance` (both tools'
versions, command, revision, time) and an `integrated_lanes` registry, and
preserves everything the run did not observe -- including the runtime waivers
of lanes that did not run. When it runs inside GitHub Actions it also records
`provenance.ci_run` (repository, workflow, job, run id and attempt, event,
SHA, ref, runner image OS/version) so the exact producing run can be found.
There are two sanctioned generators, both on CI's toolchain (Icarus 12.0 /
Verilator 5.020), and neither commits anything:

1. **The candidate step of `rtl-module-qualification`** (every tb-sim run).
   *After* the aggregate gate has compared the revision against the committed
   ledger, the same job re-runs `--lint --update-baseline --integrated all`
   over the same producer artifacts with
   `--baseline-output out/baseline-candidate/rtl-lint-baseline.json`, which
   writes the candidate there instead of over `tb/rtl-lint-baseline.json` and
   compares the run against the candidate (a code with no stated reason
   therefore fails the step). The committed ledger is never modified, nothing
   enforces against the candidate, and the step runs only if the gate passed.
   The candidate ships in the job's `rtl-modules-evidence` artifact.
2. **The manual `.github/workflows/rtl-lint-baseline.yml`**
   (workflow_dispatch, for a maintainer with dispatch rights), described
   below.

Either way a reviewer checks every added or changed waiver reason and count
before committing the candidate verbatim; the enforcement step never
regenerates before it compares.

**Generator revision binding.** The generator runs on the revision the
producers actually ran on, not on its dispatched ref. A `pull_request` tb-sim
run's jobs (producers and aggregate alike, all default `actions/checkout`)
check out GitHub's test merge commit, so their evidence records that merge
SHA, not the run's `headSha`; a `push` run records `headSha` itself. Given a
tb-sim `run_id`, the generator requires the run to be `TB sim` with both
producer jobs `success`, downloads their artifacts outside the work tree, reads
the single revision every evidence record names
(`tools/bind_producer_revision.py recorded`; absent, malformed or disagreeing
records fail), fetches and checks out exactly that commit, and then
(`... verify`) requires `git rev-parse HEAD` to equal it, the tree to be clean,
and the revision to belong to the run: equal to `headSha` for `push`, a
two-parent merge whose second parent is `headSha` for `pull_request` (the bare
PR head, a stale merge of an older head, or any other event fail).
`--update-baseline` then re-applies the usual evidence checks (clean record,
HEAD match, source digests current) against that tree. The uploaded ledger
therefore describes the merge revision's sources; the reviewer commits it to
the PR branch, and the next tb-sim run re-gates it.

## Diagnostic gate

`tb/run_tb.py` passes no warning flags and `_run()` only checks a subprocess's
exit status, so compile-time diagnostics have never been examined. The gate
adds two tiers:

- **lint tier** — every lint unit is compiled with
  `verilator --lint-only -Wall -Wno-fatal --timing -sv` and with
  `iverilog -g2012 -Wall -t null`. Verilator is the width/sign source: Icarus
  has no width-mismatch diagnostic at all (verified against iverilog 13.0),
  so an Icarus-only gate could not satisfy "width/sign warnings are failures".
  Both linters are PDK-free.
- **runtime tier** — each lane's captured transcript is scanned for declared
  runtime-diagnostic patterns (`TB-WARN`, simulator `warning:`/`ERROR:`,
  x/z-state reports, `$readmemh` truncation). `tb/run_tb.py`'s own `+ <cmd>`
  echo lines are skipped: they embed absolute temp paths and would otherwise
  match by accident.

### Fail-closed classification, then explicit waivers

Each diagnostic is classified into a class (`WIDTHTRUNC`, `BLKSEQ`,
`IVERILOG-SENSITIVITY`, …). A class named in
`qualify_rtl_modules.EXCLUDED_CLASSES` is dropped with a stated reason —
these are style/inventory reports about the generated constants package and
bench scaffolding (`UNUSEDPARAM`, `UNUSEDSIGNAL`, `DECLFILENAME`,
`TIMESCALEMOD`, `IMPORTSTAR`, `PROCASSINIT`, `PINCONNECTEMPTY`, `VARHIDDEN`,
`IVERILOG-TIMESCALE`). **Every other class gates.** A Verilator release that
adds a new diagnostic class therefore surfaces as a gate failure rather than
as silence.

Surviving diagnostics are tallied per `tool|source|class` key and compared
against `tb/rtl-lint-baseline.json`:

- a key the ledger does not waive → **failure**;
- a count above the waived count → **failure**;
- a waiver whose `reason` is missing or starts with `UNJUSTIFIED` →
  **failure** (a waiver is only explicit if somebody wrote down why);
- a count *below* the waived count → a note, not a failure; lower the ledger
  with `--update-baseline`.

The count stored per key is the **worst single lint unit's** count, never the
sum across units. Shared sources (`gf180_rtl_constants_pkg.sv`,
`patch_control.sv`) are compiled by several units, so a sum would make the
committed numbers depend on how many units include a file — adding a lint unit
would then fail the ratchet for sources nobody touched.

### The ledger is pinned to CI's linter versions, not any developer's local ones

`tb/rtl-lint-baseline.json` is seeded from, and validated against,
`.github/workflows/tb-sim.yml`'s exact toolchain: `apt-get install iverilog
verilator` on the `ubuntu-24.04`-based runner image (Verilator 5.020, Icarus
12.0 as of this writing). A developer running `--lint` locally with a
different linter build — e.g. a newer Homebrew Verilator on macOS — will
observe a *different* diagnostic set for the identical source: some classes
this ledger waives (`BLKANDNBLK`, `BLKLOOPINIT`, `VERILATOR-EXIT`,
`INITIALDLY` — all Verilator-5.020-only "Unsupported"/lint reports that a
newer Verilator resolves) will show as "no longer observed" notes locally,
while other classes a newer Verilator newly reports (observed locally:
`WIDTHTRUNC` on `gf180_rtl_constants_pkg.sv`, `MULTIDRIVENPROC` on
`patch_control.sv`) will show as unwaived local failures. Neither is a
regression — **CI's toolchain is the ledger's ground truth**, and the
`rtl-module-qualification` CI job installs the same apt packages
`--update-baseline` was run against. Re-baselining from a different linter
build would just shift which developer's environment disagrees with the
ledger; do it from a container matching `tb-sim.yml`'s toolchain (or from CI
itself) rather than from an arbitrary local install.

### Why the width/sign diagnostics are waived rather than fixed

The landed engines produce 200-plus `WIDTHTRUNC`/`WIDTHEXPAND` reports. They
describe DR-0006's arithmetic profile doing exactly what it declares:
widening intermediates to a full product width, then narrowing with the
declared rounding. Re-sizing those expressions to silence Verilator would
change declared arithmetic, which `spec/` owns and which requires a decision
record — not an RTL edit made from a verification-tooling change. They are
therefore waived **at their committed counts**, with that reason recorded in
the ledger, and the gate's value is the ratchet: no new or increased
width/sign diagnostic may appear.

The same reasoning applies to `BLKSEQ` (intra-cycle evaluation order mirroring
the fixed model) and `MULTIDRIVENPROC` (`patch_control`'s register file being
written by host writes and reset defaults). Each carries its own reason.

### Runtime-tier waivers, observed from real lane transcripts

The runtime tier scans each selected lane's own captured output, not a
lint unit's compile transcript, so its waivers are seeded by actually running
the lanes (`--lanes ... --update-baseline`), not by inspection. Two classes
are currently waived:

- `TB-WARN` — `tb_adsr_engine.sv`/`tb_lfo_vca_engine.sv` print `TB-WARN` when
  `out_valid` stays low, which is exactly what those lanes' own planted
  off-by-one timing mutation makes happen; the diagnostic is the negative
  control succeeding, not a defect.
- `SIM-WARNING` — dominated by Icarus's own runtime `$readmemh` truncation
  report: `tb_normalization_replay_engine.sv` declares its mix-stream buffers
  at the canonical 176,400-sample clip length regardless of how long a given
  directed/mutation vector actually is, so a shorter vector legitimately
  leaves the buffer's untouched tail at its declared default. The lane's own
  bit-exact comparison only reads the vector's declared range. A
  `'warning:'`-prefixed readmemh report is classified into this generic
  class rather than the dedicated `READMEM` class (see
  `RUNTIME_PATTERNS`'s match order in `tools/qualify_rtl_modules.py`) — the
  waiver is therefore per lane, by count, and gates on any increase
  regardless of which warning text produced it.

### Proving the gate can fail

A gate nobody has seen fail is a decoration.
`tests/test_rtl_module_qualification.py` plants a real width mismatch into a
copy of one engine and asserts the gate reports a failure for it, and plants a
synthetic runtime diagnostic and asserts the transcript scan reports it. Both
run wherever Verilator is installed; the pure-logic ratchet tests run
everywhere.

## Coverage, and its justified exclusions

- **Functional coverage — reported and gated.** Per lane, the declared
  obligations in `qualify_rtl_modules.COVERAGE_MODEL` are checked against what
  the lane actually printed: zero `-> FAIL` verdicts, at least the declared
  minimum of `-> OK` case/fixture verdicts, and the declared axes recorded in
  the table above.
- **Negative-control ("assertion") coverage — reported and gated.** Every
  planted mutation a lane reports must be observed `DETECTED`; a single
  `NOT DETECTED` fails, and a lane demonstrating fewer mutations than its
  declared minimum fails. This is how #78's "at least one intentional mutation
  per module family proves the test fails" is kept true over time rather than
  proven once.
- **Code (line/toggle/branch) coverage — excluded, justified.** Icarus
  Verilog exposes no coverage instrument, and Icarus is the simulator that
  arbitrates bit-exactness. A Verilator `--coverage` pass would need a second,
  separately-elaborated build of every testbench, and its numbers would
  describe a simulator that does not arbitrate conformance. Excluded.
- **SVA assertion/cover-point coverage — excluded, justified.** The
  testbenches carry no SVA cover properties; their checks are procedural
  bit-exact comparisons plus exported op-counter equality. Negative-control
  coverage above is reported in its place.
- **`sim/reference/sine-vco-golden-v1` and `sim/reference/square-saw-vco-golden-v1`
  vector-set hashes — excluded, justified.** Every vector filename in both
  directories uses a `:`-delimited naming scheme (e.g.
  `freq:mid-phase:min-unmod.json`, `frozen:waveform:vco_2:saw.json`). Walking
  either directory through `coverage_hashes()` once raised
  `CapabilityError: unsafe relative path`, because the grammar reserved for
  *declared* locators was also applied to names found on disk — observed by
  actually running `--record` against the committed tree, not by inspection.
  Issue #215 fixed that walk: a discovered name is judged by containment plus
  the symlink and escape refusals, and its bytes are hashed as stored, so
  neither directory raises any more. Whether this node's evidence should cover
  42 additional vector files is a separate declaration decision, not this
  aggregation/gating layer's to make, so both directories stay named in the
  `rtl-modules` node's `exclusions` for now. The bit-exact comparison itself
  was never affected — the `vco`/`vco2` lanes load and check every vector in
  both directories exactly as before; this exclusion is only about the evidence
  record's own input-hash list.

These exclusions are also declared on the `rtl-modules` node in
`spec/capabilities-v1.json`, so a reader of `docs/CAPABILITIES.md` sees them
next to the claim.

## Evidence record

`--record` writes a record conforming to
`spec/schemas/capability-evidence-v1.schema.json` for the `rtl-modules` node:

- `execution.command` / `exit_code` — the registered
  `rtl-module-qualification-v1` check, actually invoked during the run.
- `execution.log` — the aggregate transcript's repository-relative path and
  SHA-256. The transcript holds every lint unit's raw linter output, every
  selected lane's full captured output, the coverage summary and the verdict.
- `inputs` — the SHA-256 of every covered input, which is where the vector and
  source hashes are indexed: the golden-vector sets each lane consumes, the
  whole `tb/sv` tree, the harness, this document and the ledger.
- `controls["one-bit-mutation"]` — `executed`/`detected` derived from the
  lanes' own observed mutation markers.
- `result.verdict` — `PASS` only when the lint tier ran, **all** module lanes
  ran, **both integrated one-shot lanes** were executed/consumed and are
  baselined (see above), the registered check exited 0 and nothing failed. A
  partial selection is `NO VERDICT`, never a pass; the integrated lanes'
  profile is stated in `result.reason`.

### Why the record is not attached to the graph yet

`spec/capabilities-v1.json`'s `rtl-modules.evidence` stays `null`. The node
depends on `fixed-model`, which has no evidence, so
`capabilities._local()` would refuse an attached record with
`prerequisite evidence changed: fixed-model`, and the node would evaluate
BLOCKED and unhealthy under `tools/compile_capabilities.py --strict`. No node
in the graph carries evidence today.

Registering `rtl-module-qualification-v1` in `capabilities.CHECKS` is the part
that is honest now: it moves the node from `NOT RUN` ("planned check is not
registered") to `READY` ("registered check available; no evidence attached").

**Attachment precondition**: once `fixed-model` carries a PASS record, this
record becomes attachable by adding that record's SHA-256 under
`dependencies` and pointing `rtl-modules.evidence` at it. Until then the
record is a generated artifact — CI uploads it — and the graph does not cite
it.

## Running it

```bash
# Fast gate only (seconds; no lane simulation).
python3 tools/qualify_rtl_modules.py --lint

# Bounded aggregate regression, the shape CI runs.
python3 tools/qualify_rtl_modules.py --lint --lanes fast --parallel \
    --log out/rtl-modules.log --record out/rtl-modules-evidence.json

# The whole set; PASS is only reachable from here, and only once the CI
# lint baseline for the integrated lanes is committed.
python3 tools/qualify_rtl_modules.py --lint --lanes all --parallel \
    --integrated all --integrated-profile regression \
    --log out/rtl-modules.log --record out/rtl-modules-evidence.json

# Fold in the one-shot producer jobs' artifacts instead of re-running them.
python3 tools/qualify_rtl_modules.py --lint --lanes fast --parallel \
    --integrated all --integrated-artifacts out/integrated-artifacts \
    --producer-result oneshot-tail-chain=success \
    --producer-result oneshot-whole-voice=success

# After a deliberate source change, re-baseline and review the diff.
python3 tools/qualify_rtl_modules.py --lint --update-baseline
```

An unrun gate is never a pass: with neither linter installed the lint tier
reports a failure rather than succeeding vacuously.
