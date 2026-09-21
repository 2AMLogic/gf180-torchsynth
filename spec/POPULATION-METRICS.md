# Population-drift diagnostics: FAD, FAD∞, and distance-induced MMD v1

`torchsynth_voice.population_metrics` implements the population-only
diagnostics of `docs/MEASUREMENT-PLAN.md` ladder level 6, scoped to the
Startable Subset declared on issue 46 (2026-09-20). Everything in this module
and its receipts answers exactly one question: does one population of sounds
retain the embedding-space coverage of another?

## House doctrine (non-negotiable)

- Population metrics answer **population questions only**. They never
  establish that sound index N still means the same patch, and never establish
  that the hardware implemented the graph. Per-index paired comparison
  (`spec/PAIRED-METRICS.md`) remains an independent mandatory gate.
- They are **never an acceptance oracle** and are **never an optimization
  target**. The One Billion Sounds paper (arXiv:2104.12922, "OBS") already
  demonstrated the pathology: MMD-optimized nebulae rewarded extreme pitches
  and drum imposters that blinded listeners rejected, and listeners preferred
  the manually designed nebula. We do not re-run that experiment on hardware
  formats or thresholds.
- No threshold is frozen. The narrow alerting/outlier role **is** decided —
  see §[Narrow-role decision (2026-09-20)](#narrow-role-decision-2026-09-20)
  below: auxiliary corpus-level diagnostics only, flag-only, never gating.
  FAD scores move with embedding version, sample size, and reference set;
  passing a distribution row is not evidence of correctness.

## Declared embedding pin

The reference embedding is a **declared pin**, not a repo dependency:

| Field | Value |
| --- | --- |
| Identity | `openl3-music-mel256-512-l1` |
| Family | OpenL3, `content_type=music`, `input_repr=mel256`, `embedding_size=512` |
| Audio distance | ℓ1 |
| Frame aggregation | `mean-over-frames-v1` (declared; OBS does not specify frame handling for its corpus MMD; fadtk convention) |
| Provenance | OBS §4.3 Table 2 (best DCG across presets; ℓ1 outperformed ℓ2) and §5 |
| Dependency status | `openl3`/`torch` deliberately absent from `pyproject.toml`; the producer refuses with `openl3_dependency_unavailable` until installed. Absence never silently skips. |

`provisional-envelope-v0` (per-window RMS + zero-crossing rate, stdlib)
exists so the machinery, the conditions harness, and receipts can run without
the pinned dependency. It is **not** the OBS pin, carries no perceptual
meaning, and every receipt using it says so.

## Estimators and numeric policy

Accumulation is versioned `fsum-binary64-v1`: `math.fsum` is order-independent
over a multiset, so identical partitions give exactly `0.0`. Inputs convert
individually to binary64; nonfinite, ragged, or mixed-dimension inputs raise
`PopulationMetricsError` before any result; no NaN or Infinity is ever
returned. Estimator identities: `obs2104.12922-eq2-distance-induced-v1`,
`frechet-gaussian-eq1-v1`, `fadinfty-linear-extrapolation-gui23-v1`.

- **MMD (OBS Eq. 2).** With `d` the declared embedding-space distance,
  `MMD(X, Y) = (1/n²) Σ_{i,j} [2·d(xᵢ,yⱼ) − d(xᵢ,xⱼ) − d(yᵢ,yⱼ)]` over all
  ordered pairs, equal partition sizes `n` (the paper's own assumption).
  Because `y = permutation(x)` leaves every distance multiset unchanged, the
  identity-permutation control is **exactly 0.0** — population metrics cannot
  catch output permutation, which is why per-index comparison stays mandatory.
- **FAD (Eq. 1, arXiv:2311.01616).** `‖μr−μg‖² + tr(Σr+Σg) − 2Σᵢ√λᵢ`, `λᵢ` =
  eigenvalues of `ΣrΣg`, computed as the symmetric product `Σg^{1/2}ΣrΣg^{1/2}`
  via a stdlib cyclic-Jacobi eigendecomposition. Exact and intended for the
  small qualification dimensions used here; the pinned 512-dimensional OBS
  embedding is documented as outside this pure-Python performance envelope.
- **FAD∞ (arXiv:2311.01616 §3.3, after FID∞).** Per-size mean FAD over
  bootstrap resamples (with replacement, declared seed) fitted linearly
  against `1/N`; the intercept is the estimate. The curve rows are the
  sample-size-bias evidence. The fit may overshoot below zero on degenerate
  self-comparisons; values are reported as computed, never clamped.

## Conditions harness and receipts

`run_population_conditions` executes declared condition kinds —
`permutation_control`, `split_half`, `cross_population`,
`parameter_shift_biased_resample`, `outlier_injection`, `fadinfty` — over
named embedding populations and returns receipt rows only. No condition
yields a verdict. `outlier_injection` replaces the trailing `k` rows of the
reference population with the first `k` rows of a declared outlier pool
(`inject_outliers`; deterministic and seed-free, so the same corpus and pool
reproduce the same injected population exactly) and measures the FAD/MMD
contamination response over a declared severity grid. `fadinfty` takes an
optional `reference` naming a different reference population; the default
remains the degenerate self-comparison.
`tools/population_drift_demo.py` loads a landed corpus store, builds the
declared populations, and writes a receipt; it never renders, repairs, or
writes anywhere but the declared receipt path. `--generated-utc` is a
declared determinism override: two runs sharing one fixed value produce
byte-identical receipts, and a default run differs only in `generated_utc`.

## First receipt on the landed development corpus

`sim/reference/population-drift-demo-first.json` (issue 46 evidence): the
landed 96-case development corpus (store index
`1b817013…50597`, per-case audio digests embedded), provisional embedding
(OpenL3 absent), seed 20260920:

| Condition | Result |
| --- | --- |
| Identity-permutation control | MMD exactly `0.0` (n=96) |
| Within-corpus 50/50 split baseline | MMD `0.029538` (n=48) |
| Extreme-pitch (decimation ×2 octave-up analog) | MMD `0.012311`, FAD `0.014025` |
| Noise-gain (+/− 0.25 seeded white noise) | MMD `1.175146`, FAD `0.187590` |
| Wrong-nebula stand-in (stratum-biased resample) | MMD `0.006494`, FAD `0.001259` |
| FAD∞ curve (N = 8…96, 20 trials, seed 20260922) | means `0.0529 → 0.0036`, strictly decreasing; fitted FAD∞ `−0.001259` |

Honest readings, none hidden:

- The sample-size bias is visible: identical populations score FAD `0.053` at
  N=8 but `0.004` at N=96. Small-N distribution scores are not comparable to
  large-N ones.
- The extreme-pitch drift is **smaller** than the within-corpus split
  baseline under the provisional envelope embedding: the embedding is nearly
  pitch-blind, faithfully reproducing the OBS caution that embeddings can be
  "insensitive to extreme pitch". This is a property of the demonstration
  embedding, not a pass.
- The FAD∞ fit overshoots to `−0.0013` on the degenerate self-comparison
  (the true limit is 0). Reported as computed; extrapolation is a bias
  reducer, not a truth guarantee.
- The wrong-nebula condition is a declared biased-resample stand-in. The
  ratified corruption ladder (`#44`) is **not** exercised here; running
  against its stimuli or listening outputs remains gated on #44's human
  listening execution.

## v2 receipt: outlier injection (2026-09-20)

`sim/reference/population-drift-demo-v2.json` (schema_version 2, issue 46
AC2) appends the declared outlier condition to the same machinery, corpus,
pin, and seed as the first receipt. Hygiene note from the first receipt is
resolved: this receipt was regenerated from a **clean tree** and records
`producer.git_context.clean = true`.

Declared construction rules (nothing implicit):

- **Outlier pool** `outlier_pool_noise_gain_x2`: one entry per corpus case —
  the corpus clip plus seeded uniform white noise at amplitude ±0.5 (2× the
  noise-gain condition's severity), seed 20260923. This is the "scaled
  noise-gain" pool named in the issue 46 curation.
- **Injection rule** (`inject_outliers`): the trailing `k` corpus rows are
  replaced by the first `k` pool rows. Deterministic and seed-free; `k` over
  the corpus size is the contamination rate.
- **Severity grid** `k ∈ {1, 4, 16}` (contamination 1.04 %, 4.17 %, 16.67 %).
- **FAD∞ response**: a second `fadinfty` row with `reference = corpus` and
  `test = corpus_outlier_k16` (the maximally injected population), sizes
  N = 8…96, 20 trials, seed 20260924.

Measured response (provisional embedding, OpenL3 honestly refused; values as
computed, never clamped or thresholded):

| Injection | Contamination | MMD | FAD |
| --- | --- | --- | --- |
| k = 1 | 1/96 (1.04 %) | 0.000353 | 0.000233 |
| k = 4 | 4/96 (4.17 %) | 0.005363 | 0.002689 |
| k = 16 | 16/96 (16.67 %) | 0.086239 | 0.029191 |

| FAD∞ row | Estimate |
| --- | --- |
| Self-comparison (clean corpus, unchanged from v1) | −0.001259 (degenerate; reported as computed) |
| Clean corpus vs trailing-16 injected | 0.029171 (curve means 0.0780 → 0.0286) |

Honest readings, none hidden:

- The contamination response is **monotone non-decreasing** across the
  severity grid in both statistics, and the extrapolated FAD∞ of the
  contaminated population (0.0292) separates cleanly from the degenerate
  self-comparison (−0.0013). The machinery *can* see pooled outliers; it
  still cannot locate them.
- Response magnitudes are embedding-dependent (the provisional envelope is
  nearly pitch-blind and noise-sensitive), so these numbers are properties of
  this demonstration embedding and corpus — not of TorchSynth, and not of any
  pinned OpenL3 configuration.
- No threshold is declared, no alerting rule is armed, and no row gates
  anything. The narrow role is decided in the next section.

## Narrow-role decision (2026-09-20)

**Decision (issue 46 AC6).** FAD∞/MMD population metrics serve as **auxiliary
drift diagnostics for corpus-level questions only**.

- **Alerting role: flag-only, no gating authority.** A population row may
  flag a distribution change for human review. It can never block or pass a
  build, a release, a merge, or a verification claim; nothing in CI or the
  scorecard consumes these rows as a gate.
- **Never per-sound identity.** The identity-permutation control is exactly
  `0.0` (see §Estimators): population metrics are structurally blind to
  output permutation, so per-index paired comparison
  (`spec/PAIRED-METRICS.md`) remains an independent mandatory gate.
- **Never an acceptance oracle.** `docs/MEASUREMENT-PLAN.md` prohibits
  population metrics as the sole acceptance criterion: they pass under
  permutation, hide single catastrophic outliers in an average, and change
  with embedding version, sample count, and reference corpus.
- **Never an optimization target.** The OBS negative result
  (arXiv:2104.12922, "OBS"): MMD-optimized nebulae rewarded extreme pitches
  and drum imposters that blind listeners rejected. No threshold is frozen
  and none may be tuned against these rows.
- **Grounding.** The decision rests on the landed sensitivities of the v1
  and v2 receipts above: sample-size bias (identical populations score FAD
  0.053 at N=8 vs 0.004 at N=96), embedding-dependence (extreme-pitch drift
  smaller than the within-corpus split baseline; outlier response magnitudes
  are properties of the demonstration embedding), and exact-zero permutation
  blindness.
- **Human-transparency row: NO VERDICT.** No human listening data exists and
  none is assumed: the operator declined listening sessions (2026-09-20;
  #44 closure comment 5753825121; DR-0004 records "lack of human data is
  explicit"). This decision is a purely computational/decision act over
  landed corpus artifacts and consumes zero listening outputs.

This section is a decision record over already-landed doctrine
(`docs/MEASUREMENT-PLAN.md` and the OBS negative results summarized in
§House doctrine); it introduces no new policy and changes no gate.

## Verification

`tests/test_population_metrics.py` is stdlib-only: hand-computed MMD values,
the exact-zero permutation control, 1-D Fréchet closed form and a
shared-rotation eigenvalue invariance, sample-size-bias direction, refusal
rules, the pinned producer's honest refusal, the full conditions harness on a
tiny synthetic corpus, the deterministic trailing-k injection (exact
replacement, refusal rules, monotone contamination response, seed-free
condition rows), and the `fadinfty` distinct-reference extension. Run:

```sh
python3 -m compileall -q src tests tools
timeout 600 python3 -m pytest tests/test_population_metrics.py -q
python3 -S -m unittest discover -s tests -p "test_population_metrics.py"
python3 tools/check_contract.py
python3 tools/population_drift_demo.py --store <store> --receipt <path>
```

Builder evidence recorded 2026-09-20, worktree `feature/issue-46`:

| Executed check | Observed result |
| --- | --- |
| `python3 -m compileall -q src tests tools` | Exit 0. |
| `timeout 600 python3 -m pytest tests/test_population_metrics.py -q` | 28 passed, 0 failed/skipped. |
| `python3 -S -m unittest discover -s tests -p "test_population_metrics.py"` | 28 tests OK with site-packages disabled (no NumPy, no Torch). |
| `python3 tools/check_contract.py` | `contract manifests are internally consistent`, exit 0. |
| `tools/population_drift_demo.py` against the landed store (v1 values) | All v1 rows reproduce exactly (table above). |
| `tools/population_drift_demo.py` v2 receipt | Receipt written in ~67 s; outlier rows and FAD∞ response as tabled above. |
| Byte stability | Two clean-tree runs sharing one declared `--generated-utc` are byte-identical; the committed receipt's sha256 is `e94a1ed9f1b9b17c94d3eebccc51e767ab8096e55d4b430e3ce1d061e539a44f`, and a default-timestamp run differs only in `generated_utc`. |

This establishes machinery and receipts only. No listening evidence, no
ladder comparison, no acceptance semantics, and no hardware claim is made.
