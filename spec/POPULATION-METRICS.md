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
- No threshold is frozen and no alerting/outlier-role decision is taken here.
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
`parameter_shift_biased_resample`, `fadinfty` — over named embedding
populations and returns receipt rows only. No condition yields a verdict.
`tools/population_drift_demo.py` loads a landed corpus store, builds the
declared populations, and writes a receipt; it never renders, repairs, or
writes anywhere but the declared receipt path.

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

## Verification

`tests/test_population_metrics.py` is stdlib-only: hand-computed MMD values,
the exact-zero permutation control, 1-D Fréchet closed form and a
shared-rotation eigenvalue invariance, sample-size-bias direction, refusal
rules, the pinned producer's honest refusal, and the full conditions harness
on a tiny synthetic corpus. Run:

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
| `timeout 600 python3 -m pytest tests/test_population_metrics.py -q` | 22 passed, 0 failed/skipped. |
| `python3 -S -m unittest discover -s tests -p "test_population_metrics.py"` | 22 tests OK with site-packages disabled (no NumPy, no Torch). |
| `python3 tools/check_contract.py` | `contract manifests are internally consistent`, exit 0. |
| `tools/population_drift_demo.py` against the landed store | Receipt written in ~45 s; values as tabled above. |

This establishes machinery and receipts only. No listening evidence, no
ladder comparison, no acceptance semantics, and no hardware claim is made.
