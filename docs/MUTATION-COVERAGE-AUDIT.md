# Mutation coverage audit (tracker #9)

Integrated-evidence audit of the negative-control mutation work (#30-#34)
against the eight original acceptance criteria and the eleven required
mutation categories of #9. It is an audit record only: no publication was
regenerated, no detector or tolerance was changed, and holdout stays sealed.
Nothing here claims gf180mcu synthesis, layout, signoff, hardware playback or
sound fidelity.

**Verdict: the tracker is NOT closable.** Criteria 2, 4, 5 and 6 are established
at the evidence scope stated per row; criteria 1, 3 (runtime scope), 7 and 8
are not established. Bounded follow-ups are listed at the end. This audit does
not reopen any completed leaf.

## Evidence classes

- **A - apparatus-only:** in-process synthetic harness faults (framework
  `apparatus.producer_call` and friends). Proves the detector trips, not that
  a Voice render behaves so.
- **P - publication validation:** a committed `sim/reference/mutation-*-v1.json`
  re-verified (stdlib or fresh in-memory rerun) against its tool. A
  schema-valid publication is not a fresh runtime measurement.
- **R - actual Voice runtime:** executed in the pinned DR-0006 release image.
  The only R evidence is `sim/reference/mutation-runtime-v1.json`, which
  covers the #30 `bridge.*` test-only operators (5 faults, 6 cases, 8
  attempts). **No #31/#32/#33 family operator has R evidence.** The family
  publications' own `not_run` lists and `spec/MUTATIONS.md` say so.

Plan-time refusal versus executed fault: the `voice.normalization_decision`
seam is non-writable. Its rows (`normalization-decision-replacement` in the
runtime and signal publications, matrix signal row of the same name) are
`mutations.validate_plan` fail-closed refusals, not injected faults. The
`norm.*` faults are executed instead as post-module replacements on directed
fixtures (class A/P); they do not exercise the real decision site.

## Checks actually run

Commit `1386c4d314c6308dcb5ac4e1db580b31cfa4ef0b` (main, clean worktree
`.loom/worktrees/issue-9`), host `loom-worker-1` (Ubuntu 24.04, Linux
7.0.0-1010-aws, x86_64 dispatch worker, not the AWS repo-remote box, not the
pinned release image). Two interpreters:

- stdlib: system Python 3.12.3 (no locked env).
- locked: Python 3.13.14 from `uv sync --locked --extra metrics --python 3.13`
  into the worktree-local `.venv` (git-ignored; NumPy 2.5.3), with
  `OMP/OPENBLAS/MKL/VECLIB` threads = 1, mirroring the `*-numerical` CI jobs.

| Check | Interpreter | Exit | Retained output |
|---|---|---|---|
| `python -m unittest discover -s tests -p test_mutations.py` | stdlib | 0 | Ran 75 tests, OK |
| same | locked | 0 | Ran 75 tests, OK |
| `... -p test_mutations_matrix.py` | stdlib | 0 | Ran 13 tests, OK |
| same | locked | 0 | Ran 13 tests, OK |
| `... -p test_mutations_identity.py` | locked | 0 | Ran 21 tests, OK |
| `... -p test_mutations_timing.py` | locked | 0 | Ran 36 tests, OK |
| `... -p test_mutations_signal.py` | locked | 0 | Ran 34 tests, OK |
| `tools/qualify_mutations.py --check` | stdlib | 0 | `PASS`, faults 7, envelope `mu1-cc7f4ed0...c5ed0` |
| `tools/qualify_mutations_runtime.py --check-publication` | stdlib | 0 | `PASS`, cases 6, faults 5, attempts 8 |
| `tools/qualify_mutations_identity.py --check` | locked | 0 | `PASS`, faults 14 |
| `tools/qualify_mutations_timing.py --check` | locked | 0 | `PASS`, faults 13 |
| `tools/qualify_mutations_signal.py --check` | locked | 0 | `PASS`, operators 14, faults 17, tripped 17 |
| `tools/qualify_mutations_matrix.py --check` | locked | 0 | `PASS`, fault_rows 44, mandatory_detectors 24, ci_subset_rows 20, sensitivity_no_verdict 13 |

`git status` was clean after all runs (no publication rewritten). Output
lines above are the tools' final JSON status lines and unittest summaries;
full logs were kept only in the throwaway run directory and are not committed.
The `--check` runs are class P (fresh in-memory rerun versus committed
publication) for identity/timing/signal/framework; the runtime
`--check-publication` is class P only and is not a fresh Voice measurement.

**UNRUN:** `python -m compileall` CI step; the CI workflow itself (GitHub
Actions never executed by this audit); the default (generating) mode of every
runner (deliberately, to avoid rewriting publications);
`tools/qualify_mutations_runtime.py` default mode (needs the pinned release
image, Docker `linux/amd64`, DR-0006 gated host); any fresh actual-Voice
measurement; the AWS repo-remote box (`tools/run_fast_tests.py`); the full
repository test suite; Python 3.11 runs of the stdlib job.

Provenance caveat on the runtime publication: it records host `Apple M5`,
macOS 26.5.1, Docker linux/arm64 29.7.2, built from project commit
`6532ec08eee7c79fd642f95d77bc086507a05f79` with `dirty: true`. It was
validated here structurally, not reproduced.

## Acceptance criteria

| # | Criterion | Status | Evidence and class | Gap |
|---|---|---|---|---|
| 1 | Every required mutation has at least one mandatory detector that fails reliably | **NOT ESTABLISHED** | Matrix `faults_to_tests`: 44 rows, 44 detected, `missed_faults` empty (P, rerun). Category table below shows required mutations with no executed row. Each fault is a single deterministic magnitude on directed fixtures (A/P); "reliably" is not shown at the Voice level. | -1 dB, truncation, ADSR decay/sustain/release perturbations have no row; family faults lack R evidence (F1, F2, F3). |
| 2 | Every mandatory detector names the mutations it is expected to catch | **ESTABLISHED** (P) | Matrix `tests_to_faults`: 24 detector rows, each with `expected_faults`, expected refusals and wrong-then-right counts (wrong observed, then clean control accepted); `tests/test_mutations_matrix.py` binds rows to the landed registry. Holds for the published faults only; it cannot name mutations that have no row. | none beyond criterion 1 gaps |
| 3 | Mutations localized after a trace boundary do not falsely implicate upstream traces | **PARTIAL - apparatus/bridge established, family runtime not** | Framework: localization over producer call / artifact write / receipt append / capture inventory (A). Signal: `localization_topology.signal_trace_records` (osc faults change only the source trace and downstream mix, siblings unchanged) (P). Identity/timing sibling invariance controls true. Runtime bridge: `final-module-slot-replacement` leaves `mixer.output/peak/gain` captures original; `upstream-module-slot-replacement` leaves six sibling traces and 31 slots byte-identical (R, bridge operators only). | Family operators have no R localization (F1). |
| 4 | Below-floor mutations yield `NO VERDICT` or documented sensitivity, never a fabricated pass | **ESTABLISHED** (P) for the published probes | Timing `floor_probes`: attack shift 0.03 control samples detected, 0.005 not detected, qualified resolution 0.02 (expected = observed). Timing degenerate coverage cases (`attack-one-sample`, `silent-sustain`, `flat-sustain`, ...) are `NO VERDICT`. Signal `normalization_coverage` and matrix `sensitivity`: 13 cells labeled `sensitivity-NO VERDICT`, 7 detected inside applicability, never counted as detected. | Only the timing breakpoint has a two-sided magnitude probe; other operators carry one magnitude (F2). |
| 5 | Missing output and identity/provenance mutations are caught before audio metrics | **ESTABLISHED** (A/P) | Identity rows refuse at `render_batch_index`, `is_train`, parameter conversion with "perceptual rows not reached" (14 faults, 48 random receipts, 2 directed cases). Timing `missing-sample`/`duplicated-sample`: frame preflight refusal before paired comparison. Framework: `truncate.payload`, `drop.receipt`, `corrupt.*` trip `artifacts.verify_sha256`, `scorecard.validate_*` and the attempt-denominator gate. | The ordering is shown on synthetic/directed fixtures, not on a runtime Voice render (F1). |
| 6 | Matrix reports case coverage and false positives separately | **ESTABLISHED** (P) | Each `faults_to_tests` row carries separate `applicability` (cases/surface) and `false_positives` (clean control accepted, family controls passed 13/7/11, plausibility and tolerant-optional guards); `counts` lists detected, missed, no-verdict, composition rows separately; `score_policy` forbids a scalar score. | none |
| 7 | CI runs a small deterministic sentinel; the full matrix is reproducible on demand | **NOT ESTABLISHED** | Reproducible on demand: yes, the `--check` commands above all passed locally (P). CI: `.github/workflows/mutations.yml` was inspected but never executed here. Findings: (a) the framework step and the `matrix-numerical` step run the generating mode before `--check`, so committed-publication drift is rewritten before being compared (#257, open, not subsumed); (b) `tests/test_mutations_identity.py` is not invoked by any job and the identity runner `--check` has no CI step; (c) the 20-row `ci_subset` is published, but no step selects it as a small sentinel; CI runs the full surfaces. | F4 (CI ordering/coverage, with #257). |
| 8 | A perceptually similar but contract-wrong change (gain or delay) still fails identity/property rows | **NOT ESTABLISHED** | `gain.db` (+1 dB) and `timing.delay_audio_sample` (one sample) are detected by paired exactness rows (`max_abs_error` 0.1098 for +1 dB; unaligned primary row FAIL for delay) (A/P). No publication row declares either as the "perceptually similar" case or asserts failure of identity/property rows specifically; perceptual calibration is explicitly not run (`signal` not_run). | F5 (explicit contract-wrong-but-similar rows). |

## Required mutation categories

Operators and fault ids are those in `mutation-matrix-v1.json`. Class is the
strongest evidence class; for every row below, R evidence is absent.

| Category | Executed rows | Status | Gap |
|---|---|---|---|
| Parameter-position shuffle; wrong normalized/physical mapping | `positional-parameter-shuffle`, `wrong-normalized-physical-conversion` (identity, A/P) | covered | R missing |
| Wrong noise slot and seed | `wrong-noise-slot`, `wrong-noise-seed`, `composed-wrong-slot-then-wrong-seed` (identity, A/P) | covered | R missing |
| One audio-sample and one control-sample delay | `one-audio-sample-delay`, `one-control-sample-delay` (timing, A/P) | covered | R missing |
| Zero-order hold; `align_corners=False` instead of endpoint-aligned | `zero-order-hold-control`, `dropped-endpoint-coordinate` (`interp.off_endpoint`), composed ZOH+delay | covered, with interpretation caveat | No operator literally flips an `align_corners` flag; `interp.off_endpoint` drops the endpoint coordinate and ZOH is re-derived against the landed `align_corners=False` path. Whether this satisfies "align_corners=False instead of endpoint-aligned" is unresolved (F3b). |
| Oscillator tuning, initial phase, mode/shape | `osc.tuning_shift` (1 semitone), `osc.phase_offset` (pi/4), `osc.mode_substitute`, `osc.shape_scale` (x2), plus two property rows for tuning/phase (signal, A/P) | covered | R missing |
| LFO rate/depth; ADSR breakpoints | `lfo-rate-shift-plus-half-hz`, `lfo-depth-shift-plus-0.1`, `attack-breakpoint-shift-plus2` (timing, A/P) | PARTIAL | Only the attack-end breakpoint is perturbed; decay/sustain/release breakpoints have no row (F2). |
| Isolated modulation-route swap/sign/depth | `route-destination-swap`, `route-sign-flip`, `route-depth-shift-plus25` (timing, A/P) | covered | R missing |
| +1 dB, -1 dB, polarity inversion, DC offset | `gain.db` (GAIN_DB = +1.0 only), `gain.polarity`, `gain.dc_offset` (0.05) | PARTIAL | No -1 dB row (F2). |
| Truncation versus rounding; intentional saturation | `clip.round_step` (2^-7), `clip.saturation_ceiling` (0.75), composed round-then-saturate | PARTIAL | Rounding is covered; no truncation operator or row (F2). Saturation covered. |
| Normalization always on, always off, wrong peak, wrong reciprocal | `norm.always_on`, `norm.off`, `norm.wrong_peak`, `norm.wrong_reciprocal` (executed on directed above/at/below-1 fixtures, A/P); `normalization-decision-replacement` (plan-time refusal, not an injected fault) | covered with limits | 13 cells are `sensitivity-NO VERDICT` (e.g. `norm.always_on` on an above-1 peak is ineffective); the real decision site `normalize_if_clipping` is non-writable and needs an `AudioMixer.output` producer handoff (F1). |
| Missing and duplicated output samples | `missing-sample`, `duplicated-sample` (timing frame preflight, A/P) | covered | R missing |

## Cross-checks performed (from the committed publications)

- Family-to-matrix links: the matrix `registry` lists 32 family operators
  (identity 6, timing 12, signal 14) and 44 registered in total; the matrix
  carries 44 fault rows = identity 14 + timing 13 + signal 17, matching each
  family's `counts.faults`. Matrix `family_reruns` records PASS for all five
  runners, and the same checks passed locally (above).
- Both directions exist and agree: 44 `faults_to_tests` rows, 24
  `tests_to_faults` rows; each `faults_to_tests` row is bound to envelope ids
  and a family publication.
- Controls and false positives: baseline, empty-plan and sham controls are
  byte-identical to the plain attempt in every family (identity 13, timing 11,
  signal 7, framework 10 controls passed); sham events are `ineffective`.
- Composition: no cross-family pair is declared; only the plan-time refusal of
  undeclared cross-family composition is executed.
- Holdout: all fixtures are development-only; the matrix `not_run` lists
  holdout, #44 and #48 consumption, and numeric-format ratification.

## Re-verification at `6d1bdffa678958bc80228e0dc0bebb913feba9fa` (2026-10-07)

Re-run on the current `main` tip in `.loom/worktrees/issue-9` (macOS Darwin,
dev host; not the AWS box, not the pinned release image) with the locked
interpreter (`uv sync --locked --extra metrics --python 3.13`, Python 3.13.16,
NumPy 2.5.3, `OMP/OPENBLAS/MKL/VECLIB` threads = 1). Mutation surfaces
(`src/torchsynth_voice/mutat*`, `tools/qualify_mutations*`,
`tests/test_mutations*`, `sim/reference/mutation-*`, the workflow) are
unchanged since the commit above except `spec/MUTATIONS.md` (see finding).
No publication was regenerated.

| Check | Exit | Retained output (summary only; full logs not committed) |
|---|---|---|
| unittest `test_mutations.py` | 0 | Ran 75 tests, OK |
| unittest `test_mutations_matrix.py` | 0 | Ran 13 tests, OK |
| unittest `test_mutations_identity.py` | 0 | Ran 21 tests, OK |
| unittest `test_mutations_timing.py` | 0 | Ran 36 tests, OK |
| unittest `test_mutations_signal.py` | 0 | Ran 34 tests, OK |
| `tools/qualify_mutations.py --check` | 0 | PASS, faults 7 |
| `tools/qualify_mutations_identity.py --check` | 0 | PASS |
| `tools/qualify_mutations_timing.py --check` | 0 | PASS |
| `tools/qualify_mutations_signal.py --check` | 0 | PASS, operators 14, faults 17, tripped 17 |
| `tools/qualify_mutations_runtime.py --check-publication` | 0 | PASS, cases 6, faults 5, attempts 8 (class P only) |
| `tools/qualify_mutations_matrix.py --check` | **1** | `FAIL: committed publication is stale or drifted: inputs` |

**Finding (new, F6): the committed matrix publication is drifted on `main`.**
The matrix publication binds the SHA-256 of `spec/MUTATIONS.md` in its
`inputs`. The commit that added the audit and the "Evidence audit" section
(`37563ea`) changed that file after the matrix was last generated, so
`--check` now fails; a direct comparison of every comparable field shows the
`inputs` digest of `spec/MUTATIONS.md` is the only differing entry (committed
`e98682b2...`, current `06f32a96...`). The earlier PASS row for the matrix
above is true at `1386c4d`, before that edit. Nothing in CI catches this
today: the `matrix-numerical` job regenerates the publication before `--check`
(#257, open), so the drift is silently rewritten in the runner. This is
exactly the defect class #257 describes, and it is now observed rather than
hypothetical. Fixing it requires regenerating the matrix publication (or
moving the audit link out of the bound file); that was out of scope for this
audit pass and is left to F4/F6. Criterion 7 therefore stays NOT ESTABLISHED
with this additional evidence; it does not change any other row.

**UNRUN (unchanged):** CI workflow execution, `compileall` step, default
(generating) mode of every runner, runtime runner default mode (pinned
image), any fresh actual-Voice measurement, the AWS repo-remote box, the
full repository test suite, Python 3.11.

## Re-verification at `6492f0563178f4bc46a57d6f228753881f6f6406` (2026-10-07, Linux)

Run on `main` tip in `.loom/worktrees/issue-9` on host `loom-worker` (Linux
6.17.0-1019-aws, x86_64 dispatch worker; not the AWS repo-remote box, not the
pinned release image) with the worktree-local locked interpreter
(`uv sync --locked --extra metrics --python 3.13`, Python 3.13.16, NumPy 2.5.3,
`OMP/OPENBLAS/MKL/VECLIB` threads = 1). Versus `1386c4d`, the only change under
the mutation surfaces (`src/torchsynth_voice/mutat*`, `tools/qualify_mutations*`,
`tests/test_mutations*`, `sim/reference/mutation-*`, `spec/reference/mutation-seams-v1.json`,
`.github/workflows/mutations.yml`) is `spec/MUTATIONS.md` (+11 lines, the
"Evidence audit" section). No publication was regenerated; `git status` was
clean afterwards.

| Check | Exit | Retained output (summary; full logs not committed) |
|---|---|---|
| unittest `test_mutations.py` | 0 | Ran 75 tests, OK |
| unittest `test_mutations_matrix.py` | 0 | Ran 13 tests, OK |
| unittest `test_mutations_identity.py` | 0 | Ran 21 tests, OK |
| unittest `test_mutations_timing.py` | 0 | Ran 36 tests, OK |
| unittest `test_mutations_signal.py` | 0 | Ran 34 tests, OK |
| `tools/qualify_mutations.py --check` | 0 | PASS, faults 7, `mu1-cc7f4ed0...c5ed0` |
| `tools/qualify_mutations_runtime.py --check-publication` | 0 | PASS, cases 6, faults 5, attempts 8 (class P only) |
| `tools/qualify_mutations_identity.py --check` | 0 | PASS |
| `tools/qualify_mutations_timing.py --check` | 0 | PASS, faults 13 |
| `tools/qualify_mutations_signal.py --check` | 0 | PASS, operators 14, faults 17, tripped 17 |
| `tools/qualify_mutations_matrix.py --check` | **1** | `FAIL: committed publication is stale or drifted: inputs` |

F6 is confirmed still open: a digest comparison of every `inputs` entry in
`sim/reference/mutation-matrix-v1.json` against the working tree shows exactly
one mismatch, `spec/MUTATIONS.md` (committed `e98682b2...`, current
`06f32a96...`). Because that file embeds the link to this audit, editing the
audit link text in `spec/MUTATIONS.md` re-drifts the matrix; this pass
therefore leaves `spec/MUTATIONS.md` byte-unchanged.

Independent workflow-ordering inspection (`.github/workflows/mutations.yml`,
unchanged; #257 still OPEN): two steps run a generating mode before `--check`,
so drift is rewritten inside the runner before it is compared: the `stdlib`
job step "Qualify controls and fault matrix, then check the committed
publication" (`tools/qualify_mutations.py` then `--check`) and the
`matrix-numerical` job step (`tools/qualify_mutations_matrix.py` then
`--check`). The timing and signal `-numerical` jobs run `--check` only, so they
are correctly ordered. No job runs `tests/test_mutations_identity.py` or
`tools/qualify_mutations_identity.py --check`, and no step selects the 20-row
`ci_subset`. The workflow itself was not executed (UNRUN). Not fixed here.

Verdict is unchanged: criteria 1, 3 (runtime scope), 7 and 8 remain NOT
ESTABLISHED. **UNRUN (unchanged):** CI workflow execution, `compileall` step,
default (generating) mode of every runner, runtime runner default mode (pinned
image), any fresh actual-Voice measurement, the AWS repo-remote box, the full
repository test suite, Python 3.11.

## Re-verification at `d38cfb21fc35979715400531ddb12c83a55db2f0` (2026-10-07, Linux)

Run on `origin/main` tip in `.loom/worktrees/issue-9` on host `loom-worker-3`
(Linux 6.17.0-1019-aws, x86_64 dispatch worker; not the AWS repo-remote box,
not the pinned release image) with the worktree-local locked interpreter
(`uv sync --locked --extra metrics --python 3.13`, Python 3.13.16, NumPy 2.5.3,
`OMP/OPENBLAS/MKL/VECLIB` threads = 1). A `git diff --stat 6492f05..d38cfb2`
over the mutation surfaces (`src/torchsynth_voice/mutat*`,
`tools/qualify_mutations*`, `tests/test_mutations*`, `sim/reference/mutation-*`,
`spec/reference/mutation-seams-v1.json`, `.github/workflows/mutations.yml`,
`spec/MUTATIONS.md`) shows no change, so the earlier verdicts carry over. No
publication was regenerated; `git status` was clean after all runs. Full logs
were kept in a throwaway directory and are not committed.

| Check | Exit | Retained output (summary) |
|---|---|---|
| unittest `test_mutations.py` | 0 | Ran 75 tests, OK |
| unittest `test_mutations_matrix.py` | 0 | Ran 13 tests, OK |
| unittest `test_mutations_identity.py` | 0 | Ran 21 tests, OK |
| unittest `test_mutations_timing.py` | 0 | Ran 36 tests, OK |
| unittest `test_mutations_signal.py` | 0 | Ran 34 tests, OK |
| `tools/qualify_mutations.py --check` | 0 | PASS, faults 7, `mu1-cc7f4ed0...c5ed0` |
| `tools/qualify_mutations_runtime.py --check-publication` | 0 | PASS, cases 6, faults 5, attempts 8 (class P only) |
| `tools/qualify_mutations_identity.py --check` | 0 | PASS |
| `tools/qualify_mutations_timing.py --check` | 0 | PASS, faults 13 |
| `tools/qualify_mutations_signal.py --check` | 0 | PASS, operators 14, faults 17, tripped 17 |
| `tools/qualify_mutations_matrix.py --check` | **1** | `FAIL: committed publication is stale or drifted: inputs` |

F6 is confirmed still open. A digest comparison of the 13 `inputs` entries in
`sim/reference/mutation-matrix-v1.json` against the working tree finds exactly
one mismatch, `spec/MUTATIONS.md` (committed `e98682b2...`, current
`06f32a96...`). `spec/MUTATIONS.md` already links this audit (section
"Evidence audit") and states the evidence scope (only `bridge.*` has actual
Voice runtime evidence; family and matrix publications are revalidated
apparatus-domain evidence; schema-valid is not a fresh measurement), so this
pass deliberately leaves it byte-unchanged: any further edit would only widen
the drift until the matrix publication is regenerated under F6.

Workflow ordering (`.github/workflows/mutations.yml`, inspected independently
of test results; #257 still OPEN, not fixed here): the `stdlib` job step
"Qualify controls and fault matrix, then check the committed publication"
(lines 28-31) and the `matrix-numerical` step (lines 114-117) run the
generating mode before `--check`, so committed drift is rewritten inside the
runner before it is compared; that is why CI cannot see F6. The `timing-` and
`signal-numerical` jobs run `--check` only. No job runs
`tests/test_mutations_identity.py` or `qualify_mutations_identity.py --check`,
and no step selects the 20-row `ci_subset`. The workflow itself was not
executed (UNRUN).

Verdict unchanged: criteria 2, 4, 5, 6 established at their stated scope;
criteria 1, 3 (runtime scope), 7 and 8 NOT ESTABLISHED. **UNRUN (unchanged):**
CI workflow execution, `compileall` step, default (generating) mode of every
runner, runtime runner default mode (pinned image), any fresh actual-Voice
measurement, the AWS repo-remote box, the full repository test suite,
Python 3.11.

## Re-verification at `e5aa4c54edce79ec8666bf94412249b8624b2d0b` (2026-10-07, Linux)

Run on `main` tip in `.loom/worktrees/issue-9` on host `loom-worker-3` (Linux
6.17.0-1019-aws, x86_64 dispatch worker; not the AWS repo-remote box, not the
pinned release image) with the worktree-local locked interpreter
(`uv sync --locked --extra metrics --python 3.13`, Python 3.13.16, NumPy 2.5.3,
`OMP/OPENBLAS/MKL/VECLIB` threads = 1, no parallelism). `git diff --stat
d38cfb2 HEAD` over the mutation surfaces (`src/torchsynth_voice/mutat*`,
`tools/qualify_mutations*`, `tests/test_mutations*`, `sim/reference/mutation-*`,
`spec/reference/mutation-seams-v1.json`, `.github/workflows/mutations.yml`,
`spec/MUTATIONS.md`) is empty, so the earlier verdicts carry over. No
publication was regenerated; `git status` was clean after all runs. Full logs
were kept in a throwaway directory and are not committed.

| Check | Exit | Retained output (summary) |
|---|---|---|
| unittest `test_mutations.py` | 0 | Ran 75 tests, OK |
| unittest `test_mutations_matrix.py` | 0 | Ran 13 tests, OK |
| unittest `test_mutations_identity.py` | 0 | Ran 21 tests, OK |
| unittest `test_mutations_timing.py` | 0 | Ran 36 tests, OK |
| unittest `test_mutations_signal.py` | 0 | Ran 34 tests, OK |
| `tools/qualify_mutations.py --check` | 0 | PASS, faults 7, `mu1-cc7f4ed0...c5ed0` |
| `tools/qualify_mutations_runtime.py --check-publication` | 0 | PASS, cases 6, faults 5, attempts 8 (class P only) |
| `tools/qualify_mutations_identity.py --check` | 0 | PASS, faults 14 |
| `tools/qualify_mutations_timing.py --check` | 0 | PASS, faults 13 |
| `tools/qualify_mutations_signal.py --check` | 0 | PASS, operators 14, faults 17, tripped 17 |
| `tools/qualify_mutations_matrix.py --check` | **1** | `FAIL: committed publication is stale or drifted: inputs` |

F6 (matrix `inputs` digest of `spec/MUTATIONS.md` stale) is still open and is
now tracked as #314. `spec/MUTATIONS.md` already links this audit and states
the evidence scope (only `bridge.*` has actual Voice runtime evidence; family
and matrix publications are apparatus-domain evidence; schema-valid is not a
fresh measurement); this pass leaves it byte-unchanged because any edit would
widen the drift until #314 regenerates the matrix. Workflow ordering
(`mutations.yml`, unchanged, #257 open) is as in the previous section: the
`stdlib` and `matrix-numerical` steps generate before `--check`, no job runs
the identity tests or runner `--check`, and no step selects the `ci_subset`
(tracked by #299). The workflow itself was not executed (UNRUN).

Open follow-up issues: F1 -> #300, F2 -> #297, F4 -> #299 and #257,
F5 -> #298, F6 -> #314; F3a/F3b are spec decisions without an issue.

Verdict unchanged: criteria 2, 4, 5, 6 established at their stated scope;
criteria 1, 3 (runtime scope), 7 and 8 NOT ESTABLISHED. **UNRUN (unchanged):**
CI workflow execution, `compileall` step, default (generating) mode of every
runner, runtime runner default mode (pinned image), any fresh actual-Voice
measurement, the AWS repo-remote box, the full repository test suite,
Python 3.11.

## Re-verification at `a4ede9fb8d7cd37293b21ba7c9967158a0abb296` (2026-10-07, Linux)

Run on `main` tip in `.loom/worktrees/issue-9` on host `loom-worker-2` (Linux
7.0.0-1013-aws, x86_64 dispatch worker; not the AWS repo-remote box, not the
pinned release image) with the worktree-local locked interpreter
(`uv sync --locked --extra metrics --python 3.13`, Python 3.13.14, NumPy 2.5.3,
`OMP/OPENBLAS/MKL/VECLIB` threads = 1, no parallelism). `git diff --stat
e5aa4c5 HEAD` over the mutation surfaces (`src/torchsynth_voice/mutat*`,
`tools/qualify_mutations*`, `tests/test_mutations*`, `sim/reference/mutation-*`,
`spec/reference/mutation-seams-v1.json`, `.github/workflows/mutations.yml`,
`spec/MUTATIONS.md`) is empty, so earlier verdicts carry over. No publication
was regenerated. Logs were kept in a throwaway directory, not committed.

| Check | Exit | Retained output (summary) |
|---|---|---|
| unittest `test_mutations.py` | 0 | Ran 75 tests, OK |
| unittest `test_mutations_matrix.py` | 0 | Ran 13 tests, OK |
| unittest `test_mutations_identity.py` | 0 | Ran 21 tests, OK |
| unittest `test_mutations_timing.py` | 0 | Ran 36 tests, OK |
| unittest `test_mutations_signal.py` | 0 | Ran 34 tests, OK |
| `tools/qualify_mutations.py --check` | 0 | PASS, faults 7, `mu1-cc7f4ed0...c5ed0` |
| `tools/qualify_mutations_runtime.py --check-publication` | 0 | PASS, cases 6, faults 5, attempts 8 (class P only) |
| `tools/qualify_mutations_identity.py --check` | 0 | PASS |
| `tools/qualify_mutations_timing.py --check` | 0 | PASS |
| `tools/qualify_mutations_signal.py --check` | 0 | PASS, operators 14, faults 17, tripped 17 |
| `tools/qualify_mutations_matrix.py --check` | **1** | `FAIL: committed publication is stale or drifted: inputs` |

F6 (#314), F4 (#299, #257), F1 (#300), F2 (#297), F5 (#298) are all still
OPEN. `spec/MUTATIONS.md` already links this audit and states the evidence
scope, and is left byte-unchanged to avoid widening the matrix drift.
Verdict unchanged: criteria 2, 4, 5, 6 established at their stated scope;
criteria 1, 3 (runtime scope), 7 and 8 NOT ESTABLISHED. **UNRUN (unchanged):**
CI workflow execution, `compileall` step, default (generating) mode of every
runner, runtime runner default mode (pinned image), any fresh actual-Voice
measurement, the AWS repo-remote box, the full repository test suite,
Python 3.11.

## Bounded follow-ups (not performed here)

- **F1** Fresh actual-Voice runtime measurement of the family operators
  (identity/timing/signal) on the pinned release image and a sanctioned host,
  from a clean tree; also decide whether to request the `AudioMixer.output`
  handoff so normalization is injectable. Needed for criteria 1, 3, 5.
- **F2** Add the missing family faults through the public operator API and
  requalify the owning family: -1 dB gain, truncation, ADSR decay/sustain/
  release breakpoints, and a second magnitude (below/above floor) for the
  other operators. Needed for criteria 1 and 4.
- **F3a** Resolve what "reliably" means (repeat count or magnitude sweep) in a
  spec note. **F3b** Decide whether an `align_corners` flag operator is
  required in addition to `interp.off_endpoint`/`interp.zoh_control`.
- **F4** Fix workflow ordering (check before generate; tracked by #257), add
  the identity tests and `qualify_mutations_identity.py --check` to CI, and
  add a step that runs only the 20-row deterministic `ci_subset` sentinel.
- **F6** Rebind the committed matrix publication to the current
  `spec/MUTATIONS.md` digest (regenerate through the matrix runner after all
  family `--check` runs pass) so `--check` exits 0 again, and make any later
  edit to a bound input regenerate it in the same change. Land with F4 so CI
  checks before generating.
- **F5** Add explicit contract-wrong-but-perceptually-similar rows (small gain
  and one-sample delay) asserting failure of identity/property rows, without
  loosening any tolerance.

The tracker may close only when these leave criteria 1, 3, 7 and 8
established in a re-run of this audit.
