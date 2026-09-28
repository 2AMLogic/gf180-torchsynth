# Directed-fixture and scorecard phase review v1

This is the issue #5 phase review: the closure record for the directed-fixture
and scorecard contract phase whose work landed as #16 (canonical 78-parameter
inventory), #17 ([directed patch manifest](DIRECTED-FIXTURES.md)), #18
([scorecard row contract](SCORECARD-CONTRACT.md)) and #87
([case registry and evidence board](CASE-REGISTRY.md)). It answers the two
phase-level questions no single work unit owned — expected corpus size and
runtime, and which preregistered fixtures cannot have analytic truth — and
re-derives the phase's other acceptance criteria instead of restating them.

The normative upstream remains `2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`. This
review changes no target, arithmetic profile, noise policy, normalization rule,
parameter ordering or clip timing, introduces no tolerance, error metric,
estimator qualification or rubric, and renders no audio. It is a preparation
and contract review, not measurement evidence: nothing here establishes Voice
fidelity, fixed-point acceptance, synthesis, layout, signoff or playback.

## Generated report and declared truth basis

- [`reference/directed-phase-review-v1.json`](reference/directed-phase-review-v1.json)
  is the generated report. Every reviewed input and its exact SHA-256 is listed
  in that report's `inputs` map; this document deliberately keeps no second
  copy of those digests.
- [`reference/directed-truth-basis-v1.json`](reference/directed-truth-basis-v1.json)
  is the declared classification consumed by the generator: where an
  expectation for each canonical trace can come from, and the three family
  rules (analytically silent, noise audible, normalization targets). It is
  reviewed judgement, pinned for exact comparison; drift from it is an error to
  reconcile, never a value to update silently.
- `tools/review_directed_phase.py` regenerates the report from the landed
  artifacts. It is stdlib-only, imports no Torch, and refuses rather than
  emitting a figure it cannot derive.

## Method

The generator recomputes rather than reads: parameter and trace coverage by set
comparison against the landed inventory and trace registry, the manifest by
rebuilding it and comparing committed bytes, the sealed identity by
recomputation plus a mutation that must move the digest, the case registry's
directed pin against the manifest bytes it claims, and the scorecard refusals by
executing four live negative controls through the landed API in the same run.
Corpus size is arithmetic over the manifest and the registry's required-row
inventory; runtime and storage are arithmetic over pinned measured receipts.

## Accounting (checked against the generated report)

Each key resolves in `reference/directed-phase-review-v1.json`;
`tests/test_directed_phase_review.py` fails if a value here and the generated
report disagree.

| Key | Value |
| --- | ---: |
| `corpus.directed_cases` | 392 |
| `corpus.canonical_names_per_case` | 78 |
| `corpus.resolved_name_value_pairs` | 30576 |
| `corpus.required_rows_per_case` | 69 |
| `corpus.registry_development_cases` | 488 |
| `corpus.registry_development_expected_rows` | 33672 |
| `corpus.directed_share_of_development_rows` | 27048 |
| `corpus.registry_holdout_cases` | 32 |
| `corpus.registry_holdout_expected_rows` | 2208 |
| `projection.projected_render_seconds.median` | 292.318 |
| `projection.projected_publishing_seconds.median` | 6049.736 |
| `projection.projected_retained_bytes.full` | 3637233152 |
| `projection.projected_retained_bytes.partial` | 555957920 |
| `truth.traces_by_basis.closed-form` | 8 |
| `truth.traces_by_basis.closed-form-limited` | 18 |
| `truth.traces_by_basis.reference-capture-only` | 6 |
| `truth.fixtures_without_analytic_final_output.count` | 21 |
| `truth.fixtures_with_analytically_silent_output.count` | 16 |
| `truth.fixtures_whose_peak_must_be_measured.count` | 376 |
| `evidence_references.referenced_total` | 30 |
| `evidence_references.unreferenced_total` | 362 |

## Criterion closure

| Criterion | Status | Re-derived in this review |
| --- | --- | --- |
| Every parameter and discrete mode maps to a planned fixture or a rationale | SATISFIED | All 78 inventory names appear in the coverage report with exactly the boundary variants the inventory implies; 324 boundary fixtures; zero discrete modes with the declared rationale that every canonical control is continuous. |
| Every named trace is exercised in isolation where the graph permits | SATISFIED-AS-PREPARATION | The 32 planned capture intents equal the canonical trace registry exactly, each with capture cases and an isolation note; 12 of them declare a graph limitation instead of claiming isolation. Three fixtures have an executed upstream capture of all 32 traces; the other 389 remain planned. |
| Normalization boundary cases include values on both sides and a tie policy | SATISFIED | The three declared targets are exactly representable in binary32, ordered below/tie/above, each carrying the strict `peak > 1` rule and the tie-retains-input policy. The pinned upstream capture took the division branch only for the above case. |
| Manifests are deterministic, name-keyed, schema-validated, no positional patches | SATISFIED | The manifest and coverage report rebuild to their committed bytes; all 392 patches resolve to exactly the 78 canonical names; the sealed identity recomputes and moves when one patch changes; the case registry pins those exact bytes and that identity. |
| The scorecard schema rejects missing units, missing validity, NaN results, and incompatible aggregation | SATISFIED | Four live negative controls refused in this run, with their reasons recorded in the report. |
| Coverage is reportable independently from error and verdict | SATISFIED | Control summaries whose verdict counts differ while coverage counts do not, plus the generated board carrying 33,672 development row obligations with zero accepted rows. |
| A review documents expected corpus size/runtime and the fixtures without analytic truth | SATISFIED | This document and its generated report. |

`SATISFIED-AS-PREPARATION` means the planned contract is complete while its
execution is not. It is a documentation judgement over landed artifacts, never
a scorecard verdict, and it is not evidence that any trace was captured,
activated or compared beyond the receipts cited here.

## Expected corpus size and runtime

Size, recomputed from the manifest and the registry:

- 392 directed fixtures — 324 parameter boundaries, 24 envelope edges, 20
  isolated matrix routes, 15 continuous waveform regimes, 3 audio sources, 3
  silence/stress cases and 3 normalization targets — each a complete
  name-keyed patch over all 78 canonical parameters (30,576 name/value pairs).
- 69 required result rows per case under the landed unqualified rubric, so the
  directed family alone owes 27,048 of the board's 33,672 development row
  obligations. The 32 sealed holdout cases add 2,208 rows that this phase never
  inspects.
- Each rendered clip is 176,400 binary32 samples; a full 32-trace capture
  retains 9,278,656 bytes per case and the four-trace subset 1,418,260 bytes.

Runtime and storage projections, arithmetic over pinned measured receipts and
**not** a measured directed-corpus run:

| Quantity | Basis | 392-case projection |
| --- | --- | ---: |
| Render seconds | 0.732–0.779 s per case, median 0.746 s, from the three batch-32 directed captures in `sim/reference/trace-capture.json` | ≈ 292 s (≈ 4.9 min) at the median |
| Render plus validate/publish seconds | 13.503–18.351 s per case, median 15.433 s, from the 96-case run in `sim/reference/development-corpus-first.json` | ≈ 6,050 s (≈ 1 h 41 min) at the median |
| Retained capture bytes, all 32 traces | 9,278,656 bytes per case | 3,637,233,152 bytes (≈ 3.39 GiB) |
| Retained capture bytes, four-trace subset | 1,418,260 bytes per case | 555,957,920 bytes (≈ 530 MiB) |

Both receipts record one host CPU under the `release-mkl-compatible-v1` runtime
profile. The render column is the model forward alone; the publish column
includes artifact validation and store publication for a different fixture
family. Neither is a CI budget, a multi-host claim, or an estimator cost: a
qualified measurement pass adds preparation and estimator time that no landed
receipt has measured for this corpus.

## Fixtures that cannot have analytic truth

Under the landed rubric `voice-comparison-unqualified` v1 every one of the
33,672 development row obligations is a paired reference-versus-candidate
comparison — framing match, exact equality, and the five final-output error
properties — with `expected` and `tolerance` null and an explicit reason. **No
row carries an analytic expected value today.** The classification below is
therefore about what a later qualified estimator could ground analytically, not
about any expectation that exists now.

Of the 32 canonical traces, 8 are closed-form (both keyboard scalars and all
six ADSR outputs), 18 are closed-form-limited — a mathematical expectation
exists but depends on accumulated float32 phase or on an input whose own basis
is limited, so it supports property-level truth such as rate, phase, route gain
or upsampler endpoints rather than a sample-level oracle — and 6 are
reference-capture-only: `noise.raw`, `noise.post_vca`,
`mixer.pre_normalization`, `mixer.output`, `mixer.peak` and `mixer.gain`.

Joining that basis to per-fixture activation facts derived from the resolved
patches:

- **21 fixtures have no analytic final-output truth.** `mixer.noise` is nonzero
  and at least one `mod_matrix.*->noise_amp` weight is nonzero, so the audible
  samples are the pinned seeded stream: the 20 noise-only fixtures (the noise
  source case plus the noise-path boundaries and routes) and `special:stress`,
  where all three sources are audible at once. Regenerating that stream from a
  second copy of the pinned source is repeatability, not analytic truth.
- **376 fixtures require a measured peak.** A whole-clip extremum over 176,400
  modulated samples has no closed form, so `mixer.peak` — and the `mixer.gain`
  derived from it — cannot be predicted for any fixture with an audible source.
  The three normalization fixtures are the sharpest case: their exact binary32
  target peaks are design intents, and a candidate patch that misses its target
  leaves the branch uncovered rather than approximately covered.
- **16 fixtures are analytically silent and non-discriminating.** Every audio
  source is deactivated, so the clip is identically zero, the peak is zero, the
  gain is one and the division is not applied. Their final-output truth is
  fully analytic and also uninformative: a candidate that emits silence for an
  unrelated reason matches it. They qualify parameter deactivation, not audio.
- The remaining fixtures have closed-form control-path truth (envelope stage
  times, route gains, upsampler endpoints) and no closed-form sample-level mix
  truth. Property-level truth still requires a separately qualified estimator;
  the landed periodic and envelope qualifications are analytic-development-only.

## Outstanding obligations at the end of this phase

- The manifest's coverage report remains a static preparation result:
  `audio_render` is `not_run`, all trace claims are `planned`, and the three
  normalization peaks are `unmeasured`. Executed captures live in their own
  receipts and are cited here, never merged into that document — regenerating
  it from the manifest cannot import an execution result.
- 30 of the 392 preregistered fixtures are referenced by name in a reviewed
  snapshot of landed evidence records (16 boundaries, 4 waveforms, 3 sources, 3
  envelopes, 3 normalization targets, 1 route; no silence/stress case). 362 are
  referenced by none. A reference is not a render, a verdict or a
  qualification; the per-record lists are in the generated report.
- Expected values and tolerances stay null until a rubric is frozen, and the
  registered candidate engine is still `integrated-rtl-not-implemented`, so
  every development case is NOT RUN by construction.

## Commands

```sh
python3 tools/review_directed_phase.py
python3 tools/review_directed_phase.py --check
python3 -m unittest discover -s tests -p test_directed_phase_review.py -v
python3 tools/check_contract.py
```

`--check` compares the committed report byte for byte and writes nothing. The
report contains no clock, host path, random identifier or Git self-reference;
the tool hashes itself into `inputs`, so changing the generator or any reviewed
input requires an explicit regeneration and the test suite fails until then.

## Builder evidence — 2026-09-28

Executed on Linux x86_64 under Python 3.12.3, with no `TORCHSYNTH_ROOT` set, so
the source-backed upstream checks skipped rather than ran.

- `python3 -m compileall -q src tests tools` exited 0.
- `python3 tools/review_directed_phase.py --check` exited 0 with
  `review report is identical: spec/reference/directed-phase-review-v1.json`.
- `python3 -m unittest discover -s tests -p test_directed_phase_review.py -v`
  ran 15 tests, all OK. They include the four live scorecard negative
  controls, refusals for a removed parameter, a drifted normalization target,
  a removed trace, a positional patch, an unpinned manifest and an incomplete
  truth basis, and a fresh-interpreter check that no Torch import occurs.
- `python3 -m unittest discover -s tests` — the full CI suite — ran 1577 tests
  in 2747.188 s and reported `OK (skipped=8)`. The eight skips are the
  pre-existing source-backed cases that require a pinned upstream checkout;
  none is new here and a skip is not reported as a pass.
- `python3 tools/check_contract.py` printed
  `contract manifests are internally consistent` and exited 0.
- Ruff is not a gate in this repository: there is no Ruff configuration and
  `.github/workflows/ci.yml` does not invoke it. Under the host's default
  settings `ruff format --check src tools tests` would reformat 120 of 195
  committed files, and `ruff check` already reports findings on files landed by
  #17, so no Ruff result is claimed for this change either way.
- No audio was rendered, no estimator executed, no holdout metadata read, and
  no Torch or PDK tool imported while producing this review.
