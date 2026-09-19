# Spectral, noise, mixer and normalization estimators v1

This is analytic apparatus qualification for issue #28, preregistered before
the first numerical run. It changes no hardware tolerance or target. The
normative source remains `2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`.
Holdout is sealed. Directed Voice normalization candidates from #17 remain
unmeasured; constructed boundary coverage does not qualify those patches.

## Preparation and evidence domain

Inputs are named, finite one-dimensional native-amplitude samples with an
explicit rate and index-zero clip origin. No alignment, padding, resampling,
normalization, detrending or filtering is performed. Spectral/noise analysis
uses the complete supplied interval. An explicit property window can be
selected only by #86's shared `Preparation`/`prepare_pair` implementation;
the local adapter never implements a transform. Normalization and exact noise
identity forbid windows. The boundary policy is no filter and no extension.
Raw and prepared hashes, sample counts, dtype and configuration are retained.
A refused shared preparation or failed invariant refuses dependent rows.

Numerical routines operate in binary64 on the supplied values. Normalization
additionally requires values exactly representable in the declared float32
or float64 dtype. The supported analysis magnitude is at most 1e6. Empty,
nonfinite, malformed or out-of-range inputs are rejected, never coerced into
silence. Missing seams are MISSING EVIDENCE. Short or ambiguous evidence is
NO VERDICT. Numerical dependency absence raises an error in the explicit suite.

## Versioned definitions

The configuration version is `spectral-noise-mix-v1`. Absolute PSD bands reuse
the landed `compare_paired(zeros, samples, spectral=True)` rectangular FFT:
one complete N-sample frame, hop N, no padding, forward 1/N coefficients,
Parseval one-sided power (double all non-DC/non-Nyquist bins), actual frequencies
k*Fs/N. Bands are [0,20), [20,200), [200,2000), [2000,20000), [20000,infinity)
Hz. Empty bands are unavailable, never zero. Band power is amplitude squared;
PSD density is band power divided by (bin count * Fs/N), in amplitude squared
per Hz. Log power is 10*log10(power); power <= 1e-24 refuses a log result
instead of substituting a floor. Absolute power can still measure zero.
Coherent gain and power gain of the rectangular window are both one.
No taper or alternative FFT changes the paired metric contract.

Harmonic analysis requires at least 128 samples and an explicit half-open
fundamental search interval strictly inside (0, Fs/2). It finds the largest
actual FFT bin there, not the nominal center. Fundamental bin power must
exceed 1e-20. Any second bin in that search interval above 1e-8 times the
fundamental power refuses attribution (off-bin leakage or multiple sources).
Harmonics 1..5 are mapped from that measured bin, folded modulo Fs at Nyquist;
DC/Nyquist or colliding assignments refuse ratios. A component's adjacent bins
must each be <= 1e-8 of fundamental power. These are conservative coherence
and ambiguity gates, not a general pitch estimator or a promise of resolving
arbitrary mixed audio. A search interval must contain at least five bins.
Returned harmonic ratios are amplitude Hn/H1. Folded-harmonic power sums only
identified harmonics whose unfolded frequency exceeds Nyquist. Inharmonic
power sums populated bins outside DC and the identified harmonic bins.
Absolute DC, RMS and band rows coexist with these gain-invariant ratios.

Noise mean is sum(x)/N; variance is population sum((x-mean)^2)/N, not an
unbiased population inference. Autocorrelation lags are 1, 2, 8, 32 with
numerator sum over N-lag pairs, denominator sum over all N centered squares
(biased finite-record convention). At least 64 samples and variance > 1e-20
are needed for correlation. PSD uses raw samples, including DC. The exact
identity API compares original bytes, validated shape/dtype, seed and stream
slot independently. Equal statistical results cannot waive a byte/selection
mismatch; converted-sample hashes are never called original-byte hashes.

Mixer estimates require all named original inputs and the pre-normalization
output at the same rate/count. Least squares on the original input columns
plus a constant measures signed gains and the added DC intercept; it is an
estimator, never a preprocessing gain correction. Full rank, at least 64
samples and column-normalized design condition <= 1e6 are required. Constant,
near-silent (centered mean square <= 1e-20), correlated or missing inputs
refuse gain attribution. dB gain is 20*log10(abs(gain)), with sign separate;
abs(gain) <= 1e-12 refuses dB. Native output DC/peak and residual RMS remain
separate. Clipping counts use inclusive caller bounds; boundary hits alone
do not prove clipping. Residual error on a constructed linear mix detects
nonlinear clipping independently of that count.

Normalization requires complete clips of exactly the declared sample count.
Peak is whole-clip max(abs(pre)), with earliest-index ties, including silence
(index zero). Peak value keeps its sign. Effective gain is post/pre at that
peak, refusing silence; it does not infer a gain from final audio alone.
Rule residual compares every post sample with pre when peak <= 1, otherwise
the dtype-rounded quotient pre/peak. Applied/bypassed decision and reported
peak/index/reciprocal require explicit captured metadata; missing metadata
refuses its rows. Strict peak > 1 is the required decision. A division at peak
exactly one is observationally indistinguishable from bypass without that
metadata. Reciprocal metadata is compared with dtype-rounded 1/peak when
applied and one otherwise. The effective gain remains binary64 post/pre.

## Preregistered grid, truth and limits

All trials are development-only synthetic controls, not production evidence.
The generator and tests construct expected values independently of estimators.
Limits below are inclusive absolute apparatus error bounds, fixed before runs.
Observed errors/floors are recorded afterwards; they never tune these limits.

| Domain | Grid and independent truth | Error bound |
| --- | --- | --- |
| Spectrum | Fs=4096, N=1024/2048, f=128/900 Hz, phase=0.17/1.1, scale=0.25/1; finite square (odd 1/n) and saw (alternating 1/n) Fourier sums through h5 | ratios 1e-10, power 1e-10, frequency 1e-9 Hz |
| FFT/bands | odd/even N=129/128; DC, Nyquist, coherent/off-bin tone, first/last impulse; independent direct complex DFT sums | power 1e-10, log/density 1e-10 |
| Refusals | 128.5-Hz off-bin, two search-band tones, N=32, zero/1e-12 tone, empty high band, colliding folded harmonics | exact refusal reason |
| Noise | deterministic 256/1024-sample xorshift32 words, seeds 13/29, signed integer/2^31; exact rational mean, variance, lag covariance; direct DFT on N=128 | mean/variance/AC 1e-12, PSD 1e-10 |
| Finite ensemble | N=1024, seeds 1..16, deterministic xorshift32 uniform construction | report range/mean of finite-record statistics only; no population PASS |
| Mixer | N=256, Fs=4096, independent sine/cosine with DC 0.125/-0.25; signed gains 0.25/1/-0.5 and offsets 0/0.125 | gain/DC 1e-12, dB 1e-10, residual 1e-12 |
| Normalization | complete 176400-sample float32 clips: 0, 1e-12 rounded, 1-2^-24, 1, 1+2^-23, 2; both peak signs, equal ties and late max | peak/index/decision/metadata residual exact; effective gain/rule error <= 1e-12 |

Noise construction is a deterministic test generator, not Torch's canonical
seed-13 stream. A cyclic permutation and a different seed must fail exact
identity; a cyclic permutation has identical mean, variance and PSD. Lag
statistics are finite-record quantities and may change under that permutation.
For noise the recorded ensemble spread is finite-length variability, separate
from arithmetic estimation error; no one realization proves a population law.

Negative controls preregister expected detectors: wrong seed/slot/bytes ->
`noise.identity`; square/saw switch or h3 amplitude +1e-6 -> harmonic ratio;
injected off-harmonic tone amplitude 0.01 -> inharmonic power; folded h3
amplitude +0.01 -> folded power; gains +/-1 dB or +1e-6 -> signed gain;
DC +1e-6 -> intercept; saturation at +/-0.4 -> residual RMS (boundary count
is independently measured, but cannot identify clipping by itself);
wrong early peak -> reported peak/index; always-on below one / always-off above
one -> branch and gain/rule residual; reciprocal +1e-6 -> reciprocal and rule.
Boundary controls include +2e-12 gain/DC, +2e-10 harmonic ratio, a one-ULP peak
neighbor, and near-silence at/below/above correlation and spectral floors.
Every mutation retains its expected row and actual detection/refusal; a refusal
does not count as detection of a required valid-domain mutant.

## Reporting and reproduction

`scorecard_rows` emits the existing scorecard v1 contract through the landed
paired-metrics serializer (used only as a transport adapter). Its exact
diagnostic JSON bytes include these family measurements, configuration,
independent truth/limit sources and input hashes. The qualification JSON embeds
each returned diagnostic as its exact UTF-8 text and digest, plus all raw rows,
per-property errors, refusal counts and mutation expectations. No aggregate
quality score or release verdict is produced. Missing limits and floor-limited
requested resolution produce null NO VERDICT while retaining raw estimates.
The qualification runner is an explicit NumPy-required command; stdlib
discovery tests stdlib paths and the stored evidence contract without skips.

Commands and measured results are recorded after implementation below.

Qualification correction before final artifact generation: the initial trial
mistakenly used zero as the expected inclusive boundary count for a signal
already exceeding the declared +/-0.4 bounds. Clamping does not change that
count. The clipping mutant is now detected only by its linear-model residual;
a separate count trial uses the independently counted original exceedances.
No numerical bound was changed. This correction prevents a false detector
claim and preserves the preregistered boundary-count semantics.

The runner carries independent literal band edges, lags and qualification
bounds; it never obtains truth or limits from the estimator under test. A
regression control that raises the estimator's claimed floor initially passed
qualification incorrectly, then correctly failed after removing that coupling.
The original preregistration is identified by Git blob
`51169df6467c0bba55f4ff59f941a6874bada9e8` and SHA-256
`141c0b13fd788c60d41e75890a56eb07b2d6f3d723d6e1c354903f48868bd4f1`;
these content identities survive branch rebases.

## API and executed analytic evidence

The public entry points are `noise_identity`, `noise_statistics`,
`band_statistics`, `spectrum`, `mix`, `normalization`, and `scorecard_rows`.
All return native-unit observations or explicit reasons. `scorecard_rows`
requires a caller's independent `Limit` mapping; no default acceptance limits
are supplied. `evidence_scope="production"` always refuses acceptance here.
Empirical bounds apply to the preregistered grids, not arbitrary signals with
the same sample rate. Broader estimator validity requires a new qualification.

`estimate_prepared_pair` accepts `spectrum`, `noise_statistics`, or
`band_statistics` and uses only #86's public entry points. Selected-window
indices are relative to that prepared window; its absolute source bounds are
retained. Exact identity and normalization cannot enter the windowed adapter.
`source=` optionally selects the read-only dependency file until #86 lands;
its exact executed snapshot hash is retained, separately from prepared samples.
A source change during a multi-call integration probe fails that probe.

Run from this issue checkout (set all numerical CPU thread limits to one):

```sh
uv sync --locked --extra metrics
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  uv run --no-sync python tests/test_spectral_estimators.py --numerical
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  uv run --no-sync python tools/qualify_spectral_estimators.py \
  --output sim/work/spectral-regenerated.json
python3 -S tools/qualify_spectral_estimators.py \
  --verify sim/qualification/spectral-noise-mix-v1.json
```

Before #86 lands, add
`--preparation-source ../issue-86/src/torchsynth_voice/preparation.py` to both
numerical commands. Without that module, a new qualification report explicitly
records missing preparation integration; that is not a passed shared-API test.
The committed report did execute the sibling API, source SHA-256
`0f8e9e6ee2b31dd6b2b2f5118e81a90d08849d013da13ff04d0c3100c660d297`.
Regeneration preserves exact diagnostic hashes on the same Python/NumPy/source
configuration. Different runtimes produce distinct records and independently
run the same frozen checks; they must not overwrite each other's provenance.
CI runs the numerical suite and the actual grids on locked Python 3.11/3.13
metrics environments, verifies committed record bytes, and uploads each fresh
report. A failed/unavailable numerical dependency fails the job.

Executed on 2026-09-18/19: Python 3.13.2 with NumPy 2.5.3 and isolated Python
3.11.16 with locked NumPy 2.4.6 each measured all 150 trials and detected all
21 intended mutation controls. The 2,396 raw rows contain 879 PASS, 33 FAIL
(all on intentional mutants), 1,458 NO VERDICT and 26 MISSING EVIDENCE.
Of the NO VERDICT rows, 1,007 retain diagnostics without a declared limit;
the others represent empty bands, floors, insufficient duration or ambiguous
evidence. This is a case inventory, not a quality score. Selected observed
errors in the committed Python 3.13 record are:

| Property | Largest observed absolute error | Preregistered bound | Unit |
| --- | ---: | ---: | --- |
| H3/H1 | 6.17e-15 | 1e-10 | 1 |
| Folded-harmonic power | 1.64e-15 | 1e-10 | amplitude squared |
| Noise mean / variance | 0 / 0 | 1e-12 | amplitude / amplitude squared |
| Lag-1 finite-record correlation | 1.12e-16 | 1e-12 | 1 |
| Mixer input-a gain | 1.12e-15 | 1e-12 | 1 |
| Added DC | 1.39e-16 | 1e-12 | amplitude |
| Normalization rule residual | 0 | 1e-12 | amplitude |

These rounded upper summaries do not replace the raw property/domain floor
table. Complete normalization trials include non-peak rational quotients,
opposite-sign ties and terminal maxima, with silence gain explicitly refused.
No canonical Torch noise capture, directed Voice peak qualification, scalar
byte identity, float/fixed tolerance, RTL, hardware or physical claim follows.
