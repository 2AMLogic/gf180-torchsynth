# Periodic estimator qualification v1

The report/schema is v1. The final curvature algorithm and analytic rubric are
v2, superseding the first PR draft's cosine-ratio estimator. Qualification cells
bind the algorithm identity; superseded floors cannot silently qualify v2 rows.

This is an analytic apparatus contract, not a float/fixed or hardware tolerance.
Holdout remains sealed. The target is TorchSynth commit
`2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`, default nebula, four seconds,
44,100 Hz audio and 441 Hz control. Domains come from the committed parameter
inventory and pinned `module.py::{VCO,SineVCO,SquareSawVCO,LFO}`.

## Preregistration (before the first qualification run)

The runner owns the executable grid; changes require a new grid identity. Keep
every attempted case, including invalid cases, in the raw table. Never increase
these limits to accommodate a measured error. These limits test estimation of
analytic signals only.

* Oscillator cosine frequencies: MIDI -24 (about 2.04 Hz), MIDI 0, 20.125,
  110.37, 440.123, 1999.7, 8000.25, MIDI 127, 19800.125, and MIDI 151
  (above Nyquist). The no-modulation MIDI range is [-24,151] (keyboard [0,127]
  plus tuning [-24,24]); **with modulation** upstream clamps to [0,127].
  These are different source branches. A samples-only estimator cannot detect
  an already-aliased sinusoid: an explicit `fundamental_in_band` provenance
  assertion is required. This asserts the fundamental's band, not absence of
  harmonics (ideal LFO square/saw harmonics can alias). Frequencies at/above
  0.45 times sample rate are unqualified.
* LFO rates: 0, 0.125, 0.5, 0.75, 1.125, 4.37, 10.125, 20, 40 Hz.
  Base [0,20] plus depth [-10,20] times a [0,1] rate envelope, clamped at zero,
  accounts for [0,40]. Constant rates are qualified; varying rates refuse.
* Duration 0.125, 1, 4 seconds; at least three observed cycles and 16 samples.
  Phases -pi, -pi+0.0001, -0.31, 0, pi-0.0001; gains 1, 0.125, 2^-20,
  2^-40; time origins 0 and 0.375 s. Main frequency grid uses duration 4,
  phase -0.31, gain 1. Separate duration/phase/gain/origin grids cover their
  combinations at 110.37 audio Hz and 4.37 control Hz.
* Raw LFO shapes use five continuous weights, exponent 2.718281828:
  all five pure shapes, sin/tri (0.6,0.4,0,0,0), unequal five-way
  (0.2,0.7,0.4,0.1,0.6), and cancelling saw/rsaw (0,0,1,1,0).
  Shape grids cover 0.5, 1.125, 4.37, 20, 40 Hz at four seconds.
* Directed refusals include silence, insufficient cycles, envelope-shaped
  LFO, FM, a second tone, clipping, added noise, a transient, unsupported
  SquareSawVCO shapes 0/0.5/1, and missing preparation/alias provenance.
* Sine maximum estimator errors by Hz band [0,20), [20,2000), [2000,+inf):
  frequency 0.00001/0.0001/0.001 Hz; cents 0.01/0.01/0.01;
  phase 0.0001/0.0001/0.0001 rad; principal timing 0.1/0.01/0.001 samples;
  peak-to-peak depth 0.00001 in native amplitude units. Directed LFO
  rate caps: 0.02 Hz below 20, 0.1 Hz above; depth cap 0.01 native units.
  Non-sine source phase/timing are deliberately unidentified, especially
  square edges whose sub-sample phase is an interval, not a point.
* Independent faults: oscillator +5 cents, LFO +0.5 Hz, LFO depth +0.1,
  source phase +0.1 rad, +1 dB gain, and one-sample delay. Mandatory limits:
  0.05 cents, 0.02 Hz rate, 0.01 depth, 0.001 rad, 0.01 timing samples.
  Resolution controls use +1e-8 Hz and a requested 1e-10 Hz limit; retain
  undetectable or refused outcomes. No fitted signal replaces primary paired
  samples; +1 dB and delay must also fail time-locked paired exactness.

Supplemental controls preregistered before the curvature-v2 run: LFO offsets
+1e-14 and +1e-15 Hz at 4.37 Hz, each requesting a 1e-16 Hz limit. These bracket
the measured floating-resolution region. The record retains both requested and
actually representable offsets, raw estimates and any unresolved outcome.
They do not change any original cap, guard or mutation magnitude.

## Measurement and preparation boundary

`estimate_periodic` accepts one finite real sampled trace and explicit rate,
native unit, family, positive cents/timing reference, waveform weights, absolute
time origin of the first supplied sample, and preparation metadata. It never
uses expected fixture frequency as an estimate. The reference only converts
measured Hz to cents and phase to principal timing.

The cosine model is `A*cos(2*pi*f*t + phase)` with A positive. Raw sine LFO is
`depth*(1-cos(2*pi*f*t + phase))/2`. Phase is at absolute t=0, wrapped to
[-pi,pi); positive phase leads. Timing is `-phase*rate/(2*pi*reference_hz)`;
it is a modulo-period representative, not absolute delay recovery. Phase
comparison uses the shortest circular distance, including across the wrap.
Upstream cumulative phase includes one frequency increment at sample zero;
reported phase therefore is **not** automatically the `initial_phase` control.
The inventory's initial-phase limits are ±3.1415927410125732 (pinned float32
pi), whereas this analytic phase convention uses mathematical pi and modulo
equivalence; it does not emulate the upstream float32 accumulation.

Depth is continuous peak-to-peak excursion in the supplied native units,
not the LFO `mod_depth` parameter (which is Hz per envelope unit), not a
selector weight, and not automatically the envelope gain of a blended LFO.
Raw LFO and envelope-shaped LFO are distinct domains. All-zero weights and
constant cancelling blends refuse. SquareSawVCO requires another qualified
shape model and currently refuses; it is not an ideal saw or square.

Allowed preparation: identity only, including explicitly supplied analytic
windows. Forbidden: alignment, trimming, padding, resampling, normalization,
detrending, filtering, gain fitting. No boundary extension. Selecting a window
requires passing its true absolute first-sample origin. The estimator can fit
model coefficients as measurements; their absolute amplitude, phase, DC and
full-window residual are retained and never used to rewrite the input.

Analytic identity metadata works without #86. The estimator-local adapter
consumes #86's published API; its default import awaits that module's landing.
No shared transform is copied.
Production trace preparation and directed Voice integration remain pending
until their real artifacts are checked. Analytic fixtures cannot qualify them.

## Floors, scorecards and reproducibility

The raw table preserves each input hash, actual truth, estimated properties,
residuals, refusal reason, and configuration. Per-range cells separate family,
waveform, frequency band, duration, gain band, units, explicit reference and
time origin. They report measured maximum errors and accepted/refused counts;
a null floor means no qualified samples.
Measured floors are not universal guarantees between grid points. Conservative
numerical/sampling guards are recorded separately. A requested tolerance at or
below four times the guard/floor refuses; it is never enlarged. Missing truth,
limit, preparation, or qualification also yields null `NO VERDICT`.

Every row uses scorecard v1 and references SHA-256 of its actual strict JSON
diagnostic bytes. The qualification artifact embeds the diagnostic text so its
digest can be verified without a hidden sidecar. Raw rejected estimates remain
diagnostics only. A valid row asserts analytic apparatus qualification within
the recorded cell, not production fidelity. Reports have no aggregate sound
quality verdict. Qualification failures remain failures in the table.

The estimator version/configuration and NumPy version are in each diagnostic;
the report also binds the actual estimator and runner source hashes. Oscillator
and LFO rate use separately named traces (`analytic.oscillator` and
`analytic.lfo`) with property `frequency_hz`; independent `cents`, `phase_rad`,
`timing_samples`, and `depth_peak_to_peak` rows cannot substitute for each other.

Sine frequency uses centered second differences to measure `sin(omega/2)^2`
with a DC intercept, avoiding a ratio near one followed by `acos`. Sin/cos
coefficient measurement follows, with a full-window residual/DC gate of 1e-7.
Directed shapes use crossings and a diagnostic template consistency fit; the
full-window relative RMS residual gate is 1e-4. Square rate instead intersects
all transition-time intervals, preserving its frequency interval and refusing
point phase. Fitting never removes a sample from the final residual or changes
the primary input. These checks cannot prove absence of arbitrarily small noise
or distinguish identical sampled aliases; those remain provenance/domain
assumptions, not detector guarantees.

## Execution and integration

```sh
uv sync --locked --extra metrics --python 3.13
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
  uv run --no-sync python tests/test_periodic_estimators.py --numerical
uv run --no-sync python tools/qualify_periodic_estimators.py
uv run --no-sync python tools/qualify_periodic_estimators.py --check
TORCHSYNTH_ROOT=/tmp/torchsynth-review-20260918 PYTHONPATH=src \
  python3 -S -m unittest discover -s tests -q
TORCHSYNTH_ROOT=/tmp/torchsynth-review-20260918 \
  python3 tools/check_contract.py --require-git-commit
```

`--check` regenerates all actual measurements and compares exact bytes in the
recorded runtime. It is not a cross-platform byte-identity claim. Dedicated CI
runs the actual numerical suite and full grid under locked Python 3.11/3.13
environments, uploading each host's raw report. Missing NumPy fails that suite;
stdlib discovery tests the metadata/committed-record contract without skips.

`estimate_prepared_pair` calls #86's `Preparation` and `prepare_pair` directly,
then passes their samples and true window origin to this estimator. Its optional
API argument allows a read-only sibling-source probe; no transform is duplicated.
The default import clearly fails if #86 has not landed. `analytic=True` is an
explicit constructed-fixture assertion; production inputs continue to refuse.
The returned shared records retain original/prepared samples and hashes, and
each measurement binds the exact shared record bytes. Example integration check:

```sh
uv run --no-sync python tests/test_periodic_estimators.py --numerical \
  --preparation-source ../issue-86/src/torchsynth_voice/preparation.py
```

The sibling-source probe is analytic integration evidence only. Runtime
preparation invariants (#86 consuming #12/#88), actual directed Voice capture
(#23), and float/fixed/RTL or holdout qualification remain pending.

## Executed evidence (2026-09-18 local date, base fdc909d)

The committed [raw report](../sim/qualification/periodic-v1.json) contains 321
cases: 202 valid raw measurements and 119 refused cases, with 41 floor/validity
cells and zero qualification failures. Its 1,605 periodic rows contain 934
PASS, 665 NO VERDICT, and six **expected mutation FAILs**. These counts describe
apparatus checks, not a sound-quality verdict. All requested identifiable grid
fixtures were measured; all twelve negative controls refused. The 119 refusals
also retain silence/near-silence, zero rate, insufficient cycles, and cancelling
blend coverage. The six mandatory tuning/rate/depth/phase/gain/delay rows fail;
the 1e-10 Hz resolution request refuses, and the same 1e-8 Hz perturbation is
honestly undetectable under the ordinary 0.02 Hz limit. Both time-locked paired
sentinels independently fail exact equality. The phase delay uses a periodic
continuation; the paired delay instead prepends zero and drops the last sample.
Both supplemental floating-floor controls refuse the requested 1e-16 Hz limit.
Their represented offsets are 9.77e-15 and 8.88e-16 Hz; their raw estimated
offsets are 1.24e-14 and 4.44e-15 Hz. The latter does not resolve the injected
offset reliably, and the raw diagnostics retain that outcome.

Approximate measured maxima for sine grids are shown below for orientation;
the machine report retains the separate duration/gain/reference/origin cells
and additional conservative guards. These are empirical errors, not guarantees
for every unsampled point or substitutes for the preregistered limits.

| Family / frequency band | Hz error | Cents error | Phase error (rad) |
| --- | ---: | ---: | ---: |
| Audio, below 20 Hz | 4.89e-15 | 2.73e-12 | 6.18e-14 |
| Audio, 20–2000 Hz | 1.31e-12 | 2.14e-11 | 6.85e-12 |
| Audio, above 2000 Hz | 1.10e-11 | 1.82e-12 | 1.44e-10 |
| LFO, below 20 Hz | 7.11e-15 | 2.67e-12 | 4.22e-14 |
| LFO, 20–40 Hz | 7.11e-15 | 0 measured | 9.73e-14 |

Executed checks:

* Python 3.13.2 / locked NumPy 2.5.3: full grid and 16 dedicated tests passed;
  exact-byte regeneration `--check` passed. The raw report binds these source
  files, runtime versions and grid hash.
* Isolated Python 3.11.16 / locked NumPy 2.4.6: full grid and 16 dedicated tests
  passed, with the same case validity counts and zero qualification failures.
  Its floating bytes are not claimed identical to the committed 3.13 report.
* Standard-library-only discovery with pinned source: 169 tests passed, zero
  failures/skips on base fdc909d. After rebasing onto 2b8934c (#87), 191 tests
  passed with zero failures/skips; Git/source-hash contract check and clean
  committed-worktree full-grid regeneration passed again.
* Actual read-only #86 adapter probe passed against preparation source SHA-256
  `0f8e9e6ee2b31dd6b2b2f5118e81a90d08849d013da13ff04d0c3100c660d297`:
  explicit window, absolute gain and phase, matching prepared-sample hash,
  and production-scope refusal. This source was concurrently under development;
  the default import still needs its merge, and production evidence is pending.
* Ruff 0.16.8 lint/format on all three Python additions, compileall, Actionlint
  on the dedicated workflow, and whitespace checks passed. No shared files or
  lockfiles changed.

Initial tests failed because the new module did not exist. The first grid also
exposed a square-edge point-fit failure; transition-interval estimation fixed
it without changing the grid's limits. No holdout, TorchSynth audio, scalar
byte-identity, hardware or physical evidence is claimed here.

The first Linux CI run exposed backend-dependent cancellation in the original
cosine-ratio recurrence on the out-of-domain 1 Hz refusal control. The curvature
form above removes that cancellation; the complete grid and both locked local
numerical suites were rerun. All preregistered limits and conservative guards
remain unchanged. The original 319 cases retain their 200/119 validity counts;
the two supplemental controls add valid raw estimates with refused frequency
verdicts. Algorithm and rubric v2 distinguish this correction from the first
draft, and qualification cells from a superseded algorithm are rejected.
