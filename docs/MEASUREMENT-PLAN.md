# Measurement plan: proving that a hardware sound is TorchSynth Voice

## Executive conclusion

No learned audio distance should be the primary acceptance test. TorchSynth
Voice is deterministic when source, configuration, named parameters, and noise
are fixed. The strongest test is therefore a paired, time-locked comparison at
named intermediate nodes, followed by exact fixed-model-to-RTL comparison.

Perceptual and distribution metrics still help answer two different questions:

- Are fixed-point differences audible?
- Does a population drawn from the default nebula retain similar coverage?

They do not answer whether sound index N still means the same patch or whether
the hardware implemented the graph correctly.

## What the paper established—and did not establish

The One Billion Sounds paper compared multi-scale spectrogram, NSynth WaveNet,
OpenL3, and Coala representations, with L1 often outperforming L2 in its
listening comparison. It selected an OpenL3 music embedding configuration and
used OpenL3-L1 **maximum mean discrepancy (MMD)** for corpus comparison and
nebula optimization.

This is easy to misremember as “MSD.” The paper contains both multi-scale
spectrogram distance as a candidate metric and MMD as the population statistic.
Neither became a reliable correctness oracle. Optimizing MMD produced extreme
pitches and unpleasant sounds; blind listeners consistently preferred the
manually designed nebula. The paper explicitly leaves perceptually meaningful
evaluation open.

That negative result is a design requirement here: never optimize hardware
formats or acceptance thresholds against one unqualified summary score.

## Verification ladder

Each row is a separate claim with separate evidence. Passing a lower row does
not waive a higher one, and results with different units are not averaged.

| Level | Claim | Primary evidence | Explicit non-evidence |
| --- | --- | --- | --- |
| 0 | Source/config identity | commit, file hashes, nebula hash, runtime lock, CPU/platform, named patch, noise identity | “TorchSynth 1.x” alone |
| 1 | Reference repeatability | repeat render hashes for parameters, float samples, and named traces | two files that merely sound alike |
| 2 | Float decomposition fidelity | time-locked, named trace comparisons to pinned Voice | final-output-only similarity |
| 3 | Fixed numeric fidelity | per-trace error rows and property rows over development corpus | one mean score |
| 4 | RTL correctness | bit-exact fixed-model/RTL samples and states | float tolerance reused for RTL |
| 5 | Auditory transparency | calibrated objective diagnostics plus blinded listening | an embedding distance by itself |
| 6 | Distribution preservation | parameter coverage plus preregistered population tests | proof of per-sound identity |

## Canonical capture and provenance

Every render record must include:

- upstream commit and hashes from `spec/reference/upstream.json`;
- repository commit and dirty diff/untracked-file digest;
- OS, architecture, CPU, Python, PyTorch, Lightning, NumPy, and TorchSynth
  versions;
- device and dtype (canonical fixtures are CPU/float32);
- sample rate, control rate, duration, expected and observed sample counts;
- global sound index and equivalent batch coordinates;
- canonical normalized and physical parameter maps;
- parameter enumeration names for both randomization and forward paths;
- noise seed, noise slot, and noise artifact/trace hash;
- float audio and metadata hashes;
- peak, peak location, RMS, DC, clipping count, and normalization gain;
- warning/error counts and whether every requested trace was captured.

A Git commit is insufficient if the worktree was dirty. A missing dependency
version, silent resample, missing sample, or failed estimator invalidates that
row rather than becoming an implicit pass.

## Paired signal measurements

### Primary final-output rows

Compute without automatic delay alignment, level normalization, silence
trimming, or resampling:

- sample count and first divergent sample;
- maximum absolute error and its index;
- mean error (bias), RMSE, and error peak;
- signal RMS, error RMS, and SNR, with an explicit result for silent inputs;
- reference/candidate peak value and index;
- DC mean, clipping/saturation count, and normalization gain;
- windowed error RMS and maximum error by fixed time segment;
- error power by preregistered frequency band.

Aligned or level-matched results may be reported as diagnostics next to the
primary rows. They cannot replace them because a one-sample delay or gain error
is a hardware defect in this profile.

### Named intermediate traces

At minimum, expose and compare:

- keyboard pitch/note-on duration;
- both LFO outputs;
- all six envelope outputs;
- sine, square/saw, and noise sources before modulation/VCA;
- each modulation-matrix output;
- each post-VCA audio path;
- unnormalized mixer output, clip peak, normalization gain, and final output.

Trace boundaries prevent cancellation from hiding two wrong modules whose
errors happen to offset at the output.

## Property measurements

These are reported per case and property, in their native units. Estimator
coverage is separate from error.

| Property | Unit / output | Qualification signals |
| --- | --- | --- |
| Oscillator frequency | Hz and cents | exact unmodulated tones across range |
| Phase/timing | samples or radians | impulses and known-phase tones |
| Square/saw shape | duty/shape plus harmonic-band ratios | directed mode/shape sweeps |
| Alias/inharmonic energy | dB relative to intended components | high-frequency directed tones |
| LFO rate/depth | Hz and normalized amplitude | isolated LFO fixtures |
| ADSR breakpoints | samples and amplitude | isolated envelope stages, including short/degenerate cases |
| Modulation response | physical output per input depth | one-route-at-a-time fixtures |
| Noise | mean, variance, autocorrelation, and PSD band levels | exact canonical noise plus seeded alternatives |
| Mixer/gain | linear gain, dB, peak and clipping count | below/at/above normalization boundary |

Pitch estimators must refuse cases without a stable periodic interval. Envelope
estimators must use a qualified amplitude representation appropriate to the
fixture (not an arbitrary absolute-value trace). Noise and ambiguous mixed
signals receive property-specific tests rather than invented pitch values.

## Estimator qualification and negative controls

Before an estimator contributes to acceptance, ground-truth it with analytic or
constructed signals spanning its intended range. Measure its error floor per
row. If the expected implementation error approaches that floor, the estimator
cannot distinguish pass from fail and must return `NO VERDICT`.

The suite must deliberately inject at least these faults and show which required
rows turn red:

- positional parameter shuffle;
- wrong normalized-to-physical mapping;
- wrong noise slot and wrong noise seed;
- one-sample delay and one-control-sample delay;
- zero-order-hold control instead of endpoint-aligned linear interpolation;
- `align_corners=False` interpolation;
- LFO rate/depth and ADSR breakpoint perturbations;
- oscillator tuning, phase, or waveform-mode perturbations;
- +1 dB and -1 dB output gain, DC offset, and polarity inversion;
- truncation/rounding and intentional saturation faults;
- normalization always-on, always-off, and wrong peak/reciprocal;
- one missing or duplicated output sample.

Each mandatory test must name the injected faults it is expected to detect.
Conversely, every injected fault must be detected by at least one mandatory
test. This mutation matrix is coverage evidence, not just a demo.

## Corpus strategy

The preregistered random corpus is global indices 0–127: 96 development cases
and 32 blind holdout cases. It validates the default sampling path, but random
coverage alone is not sufficient.

Before choosing numeric widths, add a separately versioned directed corpus that
covers:

- every discrete oscillator and modulation mode;
- physical minima, maxima, midpoints, and range-boundary neighborhoods;
- normalization just below, at, and above peak one;
- silence and near-silence;
- shortest/longest envelope stages and overlapping stage boundaries;
- extreme oscillator/LFO frequencies and modulation depths;
- isolated source/module routes and deliberately stressful combinations.

Freeze algorithms, tolerances, and development-derived limits before revealing
holdout results. A new tolerance or changed estimator creates a new rubric
version; it does not rewrite the old scorecard.

## Perceptual and population metrics: carefully bounded roles

### Useful secondary diagnostics

- Multi-resolution linear/log spectral errors can localize frequency/time-scale
  impairment. Always state FFT sizes, hops, windows, power/log floors, and
  whether phase is discarded.
- ITU-R BS.1387 PEAQ is designed for perceptual impairment of audio codecs. It
  may be informative once our fixed-point errors resemble codec impairment, but
  its version and implementation must be pinned and validated on our sounds.
- ViSQOL audio mode is a full-reference diagnostic trained around codec-like
  degradation. It requires 48 kHz, so using it introduces a pinned resampler;
  its own documentation warns that it can perform poorly outside its training
  use and that single scores are not meaningful.
- CDPAM is trained on human judgments of audio perturbations but is rooted in
  speech-processing data. It is exploratory until calibrated against blinded
  judgments on TorchSynth sounds.
- Human testing should use hidden references and explicit anchors. ABX answers
  detectability; MUSHRA-style testing ranks impairment. Neither should be
  replaced by informal listening from developers who know the condition.

### Population-only diagnostics

FAD/FAD-infinity or a preregistered MMD can flag broad distribution or outlier
changes across a large corpus. Report the embedding model and exact weights,
reference set, clip preparation, sample count, random trials/bootstrap, and
confidence interval. Modern FAD work demonstrates sample-size bias and strong
dependence on embeddings and reference data.

Population metrics are prohibited as the sole acceptance criterion because
they may:

- pass when sounds are permuted among indices;
- hide one catastrophic outlier in an average;
- reward extreme pitch or noise that spreads embedding space;
- change with embedding version, sample count, or reference corpus;
- be optimized without implementing the target graph.

## Reporting format

A scorecard presents:

1. provenance/preflight and failures;
2. one row per case, trace/property, metric, unit, tolerance, observed value,
   verdict, and artifact link;
3. coverage and `NO VERDICT` counts;
4. development and holdout summaries kept separate;
5. mutation-matrix results;
6. optional perceptual/population appendices with full configuration.

There is no all-up scalar. A release verdict is the conjunction of mandatory
rows and coverage requirements defined by a frozen rubric.

## Primary sources

- [One Billion Audio Sounds from GPU-enabled Modular Synthesis](https://arxiv.org/html/2104.12922v2)
- [Adapting Fréchet Audio Distance for Generative Music Evaluation](https://arxiv.org/abs/2311.01616)
- [CDPAM: Contrastive learning for perceptual audio similarity](https://arxiv.org/abs/2102.05109)
- [Audio Similarity is Unreliable as a Proxy for Audio Quality](https://arxiv.org/abs/2206.13411)
- [ITU-R BS.1387-2: objective measurement of perceived audio quality](https://www.itu.int/rec/R-REC-BS.1387-2-202305-I/en)
- [Google ViSQOL documentation](https://github.com/google/visqol)

The “audio similarity” caution is not an argument against exact comparisons.
It says similarity metrics should not be misrepresented as human quality. Here,
sample/trace identity establishes implementation behavior, while listening
evidence addresses perceptual transparency as a separate claim.

