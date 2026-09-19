# Envelope and isolated-route qualification v1

This development-only family targets pinned TorchSynth
`2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`. It defines analytic estimator
qualification, not hardware tolerances, rendered Voice evidence or holdout
qualification. The twenty directed route patches remain prepared inputs.

## Preregistered experiment

The following bounds and controls are fixed before running the grid. Results
are written to `sim/qualification/envelope-routes-v1.json`. Changes to these
rules require a new qualification version.

| Method | Domain / minimum evidence | Analytic limit |
| --- | --- | --- |
| Direct ADSR | Nonnegative isolated control trace, known alpha and note duration; four interior samples each in rise, fall and positive sustain | 0.02 control sample for continuous boundaries; 0.00002 absolute amplitude |
| Direct release | Four positive release samples and its end observed | 0.02 control sample |
| Tone amplitude | Stationary sinusoid, known frequency, fitted DC and phase; at least 32 samples and four cycles; frequency at most rate/4 | 0.0000001 absolute peak amplitude |
| Broadband amplitude | IID zero-mean unit-variance Gaussian source with stationary gain, at least 8192 samples | 0.05 times gain absolute RMS amplitude |
| Isolated route | Excited source, all five named destinations, at least eight samples; linear response plus independent intercept | 0.00000001 dimensionless gain; 0.0000001 native destination depth |

The absolute amplitude/source-excitation floor is `1e-7`. An expected tolerance
below a method's declared resolution refuses a verdict. The direct fit uses
known alpha, not expected attack, decay, release, sustain or gain. The
constructed truth generator and estimator have separate code paths.

The direct grid crosses rates 100/441/1000 Hz, alpha 0.1/0.5/1/2/6, gain
0.25/1, sustain 0.2/0.8, and three attack/decay/release/note/observation settings:
`20.25/25.5/30.75/90.5/150` and `7.25/9.5/12.75/40.5/64` in control
samples, plus `0.7/0.6/1.1/1.8/4` in seconds. A
length sweep covers 0/1/2/3/4/5/6/8/12 control samples. Additional cases cover
note-off in attack and decay, no observable sustain, zero decay, zero release,
flat sustain=1, silent sustain=0, release beyond the clip, silence, near
silence, fractional sample origin, and truncated observations. The measured
floor tables retain refusals and minimum *tested* resolvable lengths.

Tone controls cross carrier phase, frequency, gain and shifted finite windows.
Slow carriers, too-short windows, gain transitions, silence and near-silence
are refusal controls. Broadband controls cross independent deterministic
Gaussian realizations and gains, with short, nonstationary and near-silent
controls. Its RMS estimates a stationary ensemble gain; it does not recover
instantaneous ADSR breakpoints or claim qualification for colored noise.

All twenty canonical routes use manifest case IDs and resolved-patch digests.
Matrix gain has unit `1`; downstream pitch depth has unit `semitone`, and VCA
depth has unit `linear_gain`. Both are measured from supplied traces. Each
destination is observed independently. A swap moves the signal to another
destination, without changing the expected route label. Canonical patch
weights remain nonnegative. Negative response fixtures are deliberate candidate
faults, not valid negative matrix parameters.

Independent controls change each breakpoint by +2 control samples, route sign
by -1, destination to the next named destination, and depth by +25%. A +0.005
control-sample breakpoint perturbation is a declared blind spot inside the
0.02-sample bound. Zero excitation refuses signed-route inference.

## Pinned continuous convention

`ControlRateModule.seconds_to_samples` multiplies seconds by control rate,
without rounding. Given requested attack A, decay D and note duration N, the
effective durations are `a=min(A,N)`, `d=min(max(N-A,0),D)`. These quantities
are first computed in seconds, then converted to control-sample coordinates.

For coordinate t, start s, duration u and epsilon e=`1e-6`, the rising ramp is
`min((max(t-s,0)+e)/u+e,1)**alpha`. A positive-duration inverse ramp is
`(1-min((max(t-s,0)+e)/u+e,1))**alpha`. At u=0 **both** ramp directions
are one. The envelope is attack times `sustain+(1-sustain)*decay_inverse`
times release_inverse. Thus truncation compresses stages to the effective
durations; zero release does not switch the envelope off.

Reported `attack_end`, `decay_end`, and `release_end` are inferred continuous
nominal coordinates a, a+d, N+R, never rounded discrete indices. Clipping of
a positive ramp actually occurs at `s+u*(1-e)-e`. `observed_peak_index` is
separately named and is a discrete index into the supplied observation.
`sustain_amplitude` is the observed pre-note-off plateau, including epsilon
factors; it is not the sustain parameter. Flat, merged or undersampled stages
are unidentifiable and return null NO VERDICT with a reason.

The optional pinned ADSR equation sentinel runs CPU float32 module outputs
against the independent binary64 construction using the actual physical
parameter readback. Its preregistered maximum absolute equation discrepancy is
`1e-4`, across fractional stages, alpha endpoints, zero stages, truncation and
release past the clip. Estimator attempts on those float32 traces are retained
separately, including refusals; this sentinel does not promote the binary64
estimator grid to a float32 Voice qualification.

## Preparation and evidence boundary

Only identity preparation is allowed for these primary properties. No onset
search, alignment, trimming, resampling, gain normalization, rectification or
Hilbert substitution is permitted. A supplied window is fixed before reading
the signal, with explicit sample origin and rate; it never changes the primary
paired timing/amplitude path. Tone regression and broadband RMS are estimator
operations, not generic shared preparation transforms.

The local adapter consumes #86's shared preparation contract once available;
analytic identity fixtures can execute independently. `preparation_adapter`
calls the shared `Signal`/`Preparation`/`prepare` API for each named trace,
executes the estimator on the returned samples, and refuses all dependent
properties if any preparation record refuses. Its integer clip-origin contract
is distinct from fractional ADSR boundary coordinates; fractional-origin
synthetic observations use the direct analytic path. Incompatible preparation
must refuse. Every diagnostic records method/configuration version, input
hashes, settings, applicability and qualification domain. Scorecard-v1 rows
reference actual stored diagnostic JSON bytes and the preregistered truth and
limit source. Unknown truth, resolution-limited or invalid observations remain
null. Passing this grid does not qualify arbitrary mixed audio.

## Replay and measured results

No NumPy, Torch or other dependency is needed for the estimators or analytic
grid. From the repository root:

```sh
python3 -S -m unittest discover -s tests -p test_envelope_estimators.py -v
python3 -S tools/qualify_envelope_estimators.py --check
```

`--check` executes the grid and independently derives the complete ordered
case/property inventory and all 1,380 obligation identities. Missing, extra,
duplicated or reordered cases/properties, removed controls, changed fixture
parameters, rates, prepared-patch identities or manifest hashes refuse. It
rederives every stored row and its diagnostic digest, recomputes floor and
minimum-stage tables exactly from the retained observations, then compares
them to the fresh experiment. Its printed denominators describe the validated
saved evidence. Implementation/preregistration hashes must match; replay writes
nothing.

Replay policy version 2 separates numerical reconstruction precision from
estimator qualification limits. Regenerated analytic truth values, numerical
observations and numerical diagnostics use `1e-9` absolute/relative precision.
Expected-source labels, configuration, applicability/verdicts, units and all
estimator decision limits remain exact. Platform math can change an analytic
truth by a last bit without changing its estimator limit. A difference beyond
the reconstruction bound refuses, even if it fits the estimator limit.

Every input retains its original little-endian binary64 bytes in base64, exact
sample count and SHA-256. Replay verifies the hash and count against those
retained bytes, then compares every sample with the independent regenerated
input using `1e-12` absolute/relative precision. This explicitly permits small
platform math differences in generated bytes while preserving their recorded
identity; merely changing a hash and rehashing the diagnostic cannot pass.
Missing, nonfinite or altered inputs outside that bound refuse. The saved
runtime remains the runtime that generated its original bytes. These replay
bounds do not change any estimator domain, floor or decision threshold, and
are neither hardware tolerances nor cross-platform byte-identity assertions.

The evidence was generated with the read-only operator Python 3.13.2 / Torch
2.14.0 environment, one CPU thread, using:

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
VECLIB_MAXIMUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1 \
/Users/joseph/dev/gf180-torchsynth/.venv/bin/python \
  tools/qualify_envelope_estimators.py \
  --upstream-root /tmp/torchsynth-review-20260918 \
  --preparation-root ../issue-86
```

The paths are operator-local command examples, not portable artifact identities.
With #86 installed in the same checkout, use `--preparation-root .`.
The record binds the shared module's actual file digest. That sentinel executed
identity preparation for direct, tone, broadband and route methods, plus nine
forbidden-operation cases: 13 actual adapter invocations in total. It does not
claim the entire shared preparation qualification suite.

The 387 constructed cases produced 2,407 scorecard rows: 1,735 PASS,
164 intentional FAIL and 508 NO VERDICT; all 1,380 preregistered verdict
obligations were satisfied. Refusals include unidentifiable/floor-limited
measurements and 224 discrete-peak diagnostic rows without a decision limit.
The 164 FAIL rows are four breakpoint-fault rows and 160 route-fault rows.
The +0.005-sample attack control remains a documented undetected perturbation.

| Property/domain | Maximum measured absolute error | Mean signed error | Minimum tested stage |
| --- | ---: | ---: | --- |
| Direct attack coordinate | 1.315e-9 control sample | 1.485e-11 | 4 control samples |
| Direct decay coordinate | 1.459e-8 control sample | -2.927e-10 | 4 control samples |
| Direct release coordinate | 2.274e-13 control sample | -5.882e-15 | 4 control samples |
| Direct peak amplitude | 2.779e-10 | 4.891e-12 | same stage requirements |
| Direct sustain plateau | 1.111e-16 | -7.594e-18 | 4 observed plateau samples |
| Stationary tone peak amplitude | 1.111e-16 | -9.252e-18 | 32 samples and 4 cycles |
| Stationary broadband ensemble RMS | 0.010171 | -0.000713 | 8192 samples |
| Constructed route gain / physical depth | 0 in this grid | 0 | 8 excited input samples |

The length sweep's four-sample minimum is at alpha=1, baseline sustain/gain,
and the declared origin; it is not a universal duration guarantee. Higher
alpha or lower amplitudes can leave fewer usable samples above the floor.
Reported maximum errors are empirical floors on this grid, not zero-error
proofs or uncertainty intervals for arbitrary inputs. In particular, measured
zero route error does not erase the preregistered nonzero resolution bound.

The nine actual pinned ADSR equation probes all met their `1e-4` sentinel
bound; the maximum observed discrepancy was `3.675e-7`. Their raw estimator
attempts preserve float32 alpha-endpoint and degenerate-stage refusals. No
Voice route audio was rendered, no batch-1 equivalence was tested, and no
holdout or hardware claim follows.
