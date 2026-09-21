# Verification rubric v0 (frozen)

This is the versioned, frozen verification rubric for the default Voice
(issue #48): one document that composes every landed measurement family's
limits, floors, coverage minimums and negative-control obligations into a
single release decision procedure. It is a composition and index of settled
evidence, not new measurement: every row is grounded in a landed artifact by
path and SHA-256 digest, and the machine-readable form
[`reference/rubric-v0.json`](reference/rubric-v0.json) re-derives every
recorded value from those artifacts. The normative upstream stays
`2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`
([`reference/upstream.json`](reference/upstream.json)).

- Status: **frozen v0** (2026-09-21). Any change to an estimator, tolerance,
  coverage rule, or row set creates **rubric v1**; v0 is never edited in
  place.
- Numeric contract: `unbound:#53`; DR-0008 remains Proposed. This rubric
  ratifies no word width, rounding site, approximation, or any DR-0008
  value, and makes no fixed-model, RTL, hardware, or sound-fidelity claim.

## Verdict semantics (DR-0004, scorecard contract v1)

There are four row states and no fifth "close enough":

| Verdict | Meaning |
| --- | --- |
| `PASS` | qualified estimator/preparation/evidence, finite observation, complete coverage, within the preregistered limit |
| `FAIL` | valid evidence, observation outside the preregistered limit — a mismatch, never a tolerance problem |
| `NO VERDICT` | the attempted evaluation cannot support a decision (unqualified estimator, floor-limited comparison, preparation failure, open row); the observation stays null — insufficient evidence is never a zero error or pass |
| `MISSING EVIDENCE` | the required input or capture is absent — a distinct refusal state, never a numerical failure or pass |

Refusals are never merged into pass/fail counts, and unlike units are never
averaged into a fidelity score.

## Release rule

The release verdict is the **conjunction** of the mandatory rows and the
coverage requirements below — never an omnibus scalar, never a weighted
average, never a single summary score. Release requires all of:

1. every mandatory row is `PASS` under this frozen rubric (an open mandatory
   row is `NO VERDICT` and blocks release);
2. zero `MISSING EVIDENCE` rows among mandatory rows;
3. all required injected faults are detected under the frozen configuration
   (`R-L2-MM-COVERAGE`: 44/44 detected, 0 missed, 24 mandatory detectors);
4. coverage minimums are met: development corpus `QUALIFIED` over the full
   96-case denominator (`R-L6-STRATA`), case-registry required-row inventory
   satisfied (`R-L0-P3`: 69 required rows per case);
5. development and holdout summaries are kept mechanically separate, and the
   32-case holdout stays sealed until thresholds are frozen;
6. appendix rows (perceptual, population) are reported with full
   configuration and are never gating.

**Independent judge rule.** Numeric fitting may use a smooth
development-only surrogate, but release judgment uses this frozen rubric.
The optimizer objective must never be the sole acceptance judge, and no
tolerance is reverse-engineered from the candidate that must pass it.

## Preregistration and change control (DR-0008 §10)

- **Measure, then preregister:** every tolerance derives from an explicit
  numeric error budget and estimator floor, measured before preregistration,
  then preregistered as the **smallest power of two ≥ 2× the measured
  value**, before any RTL freeze.
- **Never increase after freeze:** thresholds are never increased after the
  rubric (or any threshold in it) is frozen.
- **A miss is a mismatch,** never a tolerance problem. There is no post-hoc
  tightening or loosening.
- **New tolerance ⇒ new rubric:** any later estimator or tolerance change
  creates rubric v1 and invokes the explicit holdout policy; a new tolerance
  does not rewrite the old scorecard.

## The ladder and the families (per-level claims)

Levels follow [`docs/MEASUREMENT-PLAN.md`](../docs/MEASUREMENT-PLAN.md).
Passing a lower row never waives a higher one. 73 rows total: 57 landed,
16 open (`NO VERDICT` until landed) — per level: L0 3, L1 1, L2 51, L3 8,
L4 1, L5 2, L6 7.

| Level | Claim | Family (rows) | Backing landed artifact (digest-pinned in the JSON) |
| --- | --- | --- | --- |
| 0 | Source/config identity | provenance (3) | `reference/upstream.json`; `reference/corpus-v0.json`; `reference/case-registry-v1.json` |
| 1 | Reference repeatability | repeatability (1) | `sim/reference/development-corpus-coverage.json` (96/96 byte-identical repeat, re-derived per identity; `holdout_identities_read: 0`) |
| 2 | Float decomposition fidelity | float-control-path (24) | `reference/control-path-rubric-v1.json` (preregistered per-trace limits; zero where measured maximum was exactly zero) + `sim/reference/control-path-float-v1.json` (10736/10736 PASS, endpoints exact, 5 mutations fail) |
| 2 | — | float-sources (7) | `sim/reference/float-sources-v1.json` (declared checkpoint limits; bit-exact noise identity, seed 13) |
| 2 | — | float-mix (6) | `sim/reference/trace-capture.json` (normalization digest bindings, branch relations) + `spec/FLOAT-MIX.md` classes |
| 2 | — | float-voice (6) | `sim/reference/float-voice-v1.json` (634 decided rows PASS, 0 FAIL; 3 composed mutations fail; store-gated rows stay receipts) |
| 2 | Estimator apparatus | estimator-qualification (5) | `sim/qualification/estimators-v1.json` (validity, floors, refusals, per-family census; preparation gate) |
| 2 | Negative controls | mutation-matrix (3) | `sim/reference/mutation-matrix-v1.json` (44/44 detected, 24 mandatory detectors, CI subset 20/20, family reruns PASS) |
| 3 | Fixed numeric fidelity | fixed-numeric (8: M0–M7) | **open** — metric definitions landed in DR-0008 §10; calibrated limits pending the candidate fixed model |
| 4 | RTL correctness | rtl (1) | **open** — sample-exact fixed-model/RTL comparison not yet landed |
| 5 | Auditory transparency | perceptual-appendix (2) | `sim/qualification/perceptual-benchmark-v1.json` — appendix only, never gating |
| 6 | Distribution preservation | coverage-strata (6), population-appendix (1) | `sim/reference/development-corpus-coverage.json`; `spec/reference/directed-coverage-v1.json`; `sim/reference/population-drift-demo-*.json` |

Per-family detail, units, applicability, estimator/config versions, threshold
values and sources, and per-row failure semantics live in
[`reference/rubric-v0.json`](reference/rubric-v0.json) (`rows` array); the
summary block recomputes the counts above.

## Landed highlights (what the frozen rows already decide)

- **Control path:** all 22 owned traces carry preregistered limits — the
  smallest power of two ≥ 2× the measured per-trace maximum over the full
  488-fixture set, never increased after commitment; upsample endpoints are
  byte-exact; all five module mutations fail.
- **Sources:** VCO checkpoint limits (1e-4 initial-phase, 5e-2 mod-depth,
  5e-5 tuning, 1e-3 saw waveform) and bit-exact noise (slot 0 digest equals
  the model's regeneration digest; no tolerance exists for noise).
- **Mix chain:** post-VCA lanes are byte-identical on captured inputs; the
  peak scalar and derived gain are digest- and branch-bound to the
  release-era capture, including the strict `peak > 1` bypass and the
  exactly-1.0 tie; all eight declared mutations must fail.
- **Composed voice:** structural identity (32 checkpoints), validity
  invariants, coverage, and 634/634 decided numeric rows PASS; the three
  composed wiring mutations fail and localize.
- **Estimator ledger:** per-family census recomputed from validated raw rows
  (for example envelope 1735 PASS / 164 FAIL / 508 NO VERDICT; spectral
  floor-limited cases refuse instead of loosening); a preparation failure
  forces every preparation-dependent family to `NO VERDICT` with a concrete
  reason.
- **Mutations:** every one of the 44 required injected faults is detected by
  at least one mandatory detector; sensitivity-`NO VERDICT` rows (13) are
  never counted as passes or detections; no scalar mutation score replaces
  the rows.

## Open rows (explicit `NO VERDICT` register)

These rows exist because a cited limit or artifact is not landed. They are
recorded, never invented, and each blocks release until landed:

| Rows | What is missing |
| --- | --- |
| `R-L2-FM-mixer.pre_normalization`, `R-L2-FM-mixer.output` | no committed artifact records the calibrated paired-row limit for the two mix traces (measure-then-preregister; bound by digest when it lands) |
| `R-L3-M0` … `R-L3-M7` | candidate fixed model and calibrated, committed limits for DR-0008 §10 metrics M0–M7 (DR-0008 remains Proposed; its draft band numbers are not ratified as thresholds here) |
| `R-L4-RTL` | sample-exact fixed-model/RTL comparison and sticky-counter agreement (#63/#75) |
| `R-L6-GAP1` … `R-L6-GAP4` | qualified methods for pitch stability, observed frequency ranges, observed noise contribution, observed envelope stages on mixed Voice output (the #21/#128 audit's four coverage gaps; linked directed families are `audio_render: not_run`) |
| `R-L5-HUMAN` | blinded human comparison (explicitly not collected; recorded 2026-09-20) |

## Perceptual and population bounded role (#47)

Per the #47 decision (closure comment 5754633329, 2026-09-20): perceptual
and population metrics are **auxiliary diagnostics, flag-only, Level 5
`NO VERDICT`; acceptance authority sits on Levels 0–4**. The appendix rows
(`R-L5-PERCEPTUAL`, `R-L5-HUMAN`, `R-L6-POP-DEMO`) are reported with full
configuration, are never merged into candidate statistics or coverage
counts, can never decide a release verdict, and can never trigger a
threshold change.

## Holdout seal

The 32 sealed holdout cases (`spec/reference/corpus-v0.json`,
`holdout_is_blind_until_thresholds_are_frozen`) were **not read during the
derivation of this rubric**: `holdout_artifacts_read` is empty, no source
path in the rubric references a holdout artifact, and the #21 audit's
denominator records `holdout_identities_read: 0` (asserted in `R-L1-R1`).
Scorecard partitions `development` / `holdout` are validated as separate
reports (`spec/SCORECARD-CONTRACT.md`); every rubric row is
development-partition. The holdout is revealed only after thresholds are
frozen, under the change-control policy above.

## Reproduction

```sh
# Internal consistency: every assertion cites a landed source digest, every
# digest matches the landed file, no row lacks a source, no aggregate score:
python3 tools/validate_rubric.py

# Consistency controls (stdlib; includes negative breakage tests):
timeout 600 python3 -m unittest discover -s tests -p test_rubric.py -v
```

## Verification

Bounded, honest checks executed for this document (worktree
`feature/issue-48`, base `0de0df0`):

| Executed check | Observed result |
| --- | --- |
| `timeout 120 python3 -m compileall -q src tests tools` | Exit 0. |
| `timeout 600 python3 -m unittest discover -s tests -p test_rubric.py -v` | 16 tests, all pass. |
| `python3 tools/validate_rubric.py` | `rubric v0 is internally consistent` — 73 rows (57 landed, 16 open), 26 digest-pinned sources, exit 0. |
| `.loom/scripts/verify-proposal-refs.sh` over the rubric's cited paths | all landed-artifact references resolve (see PR body). |
| Negative controls (in `tests/test_rubric.py`) | tampered threshold, tampered digest, source-less row, aggregate key, open-row flip, and holdout read are each caught. |

Scope guards: this freeze grants no numeric-format ratification (#53/DR-0008
Proposed), performs no holdout unsealing, invents no threshold beyond the
declared measure-then-preregister policy, and makes no synthesis, layout,
signoff, hardware-playback, or sound-fidelity claim.
