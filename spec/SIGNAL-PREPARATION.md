# Signal preparation and invariance v1

This is apparatus qualification, not an estimator floor, sound-fidelity limit,
runtime ratification, or hardware claim. The source target remains
`2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`. Holdout is untouched.
Estimator algorithms, domains and floor tables belong to #26/#27/#28. Those
families use small local adapters to the contract below; this module does not
register or implement their algorithms.

## Input, declaration, and output

`torchsynth_voice.preparation.Signal(samples, sample_rate_hz, unit,
time_origin_samples=0, onset_sample=None)` identifies one finite, nonempty,
one-dimensional native-unit sample sequence. No channel selection, decoding,
unit conversion, squeezing, or dtype scaling is inferred. Samples support the
same real int/float types of at most 64 bits as paired metrics; bool, complex,
nested, nonfinite, and integers outside the exact binary64 conversion range
are refused. Input samples are copied, never modified.

`time_origin_samples` is the integer coordinate of the first input sample on
the caller's common native-rate time axis. `onset_sample`, if supplied, is an
explicit index inside this input, not an automatically detected onset.

Every consumer supplies a `Preparation` declaration:

| Field | Meaning |
| --- | --- |
| `estimator`, `estimator_version` | Explicit estimator identity, including its local algorithm/configuration version. |
| `path` | `exact` or `property`; resampling has a separate entry point. |
| `unit` | Required native input unit, matched against the signal. |
| `time_origin` | `clip` or `onset`. |
| `window` | `None` (all available samples), or explicit half-open integer offsets from clip/onset. |
| `allowed`, `forbidden` | Disjoint exhaustive partition of `TRANSFORMS`; unknown requested operations refuse. |
| `boundary` | Only `none-no-extension` is implemented: no filter, taper, extension or inferred boundary samples. |
| `refusal_conditions` | Nonempty estimator-local declarations, in addition to the always-enforced input/transform/window checks. |
| `invariances` | Applicable names among `onset_shift`, `silence_padding`, `common_gain`. |
| `measure_kind`, `gain_domain` | Common-gain controls require `ratio` and an explicit bounded positive, nonzero gain interval. |

`prepare(signal, declaration, operations=())` returns `version`, `status`
(`valid`/`refused`), a concrete `reason`, full `config`, `input` metadata,
`requested_operations`, `window_bounds`, `raw_samples`, `prepared_samples`,
`raw_sha256`, `prepared_sha256`, `config_sha256`, and `diagnostic_only`.
Refusal has null prepared samples/hash. Nonrepresentable raw input is not
fabricated as valid evidence. Numeric arrays are encoded as little-endian
binary64 for hashing, preserving signed zero. These hashes identify converted
numeric values; original-file byte identity remains a separate producer check.
Configuration hashes cover the preparation version, full declaration, input
rate/unit/origin/onset, and requested operations. The canonical JSON encoding
is sorted, indented strict UTF-8 JSON with a final LF (`record_bytes`).

`prepare_pair(reference, candidate, declaration)` calls this single entry point
twice with the same declaration. No side-dependent configuration is accepted.

## Exact and property paths

`compare_exact(reference, candidate, declaration, **metric_options)` accepts
only an exact declaration and equal declared time origins. It feeds identity
prepared values into the landed public `paired_metrics.compare_paired` API.
Count/rate mismatches are retained as that API's framing refusals. All timing,
gain, DC, clipping and sample-count errors remain visible. The exact declaration
forbids alignment, trimming, padding, resampling, normalization, detrending,
gain fitting, filtering and window selection. Explicit prohibited requests
refuse; they are never silently ignored. Paired diagnostic windows partition
the complete comparison; they are not preparation cropping.

Property preparation implements only explicit rectangular window selection:

```python
from torchsynth_voice.preparation import TRANSFORMS, Preparation, Signal, prepare

declaration = Preparation(
    estimator="family-local-estimator", estimator_version="1",
    path="property", unit="amplitude", time_origin="onset", window=(0, 3),
    allowed=("window",),
    forbidden=tuple(t for t in TRANSFORMS if t != "window"),
    invariances=("onset_shift", "silence_padding"),
)
prepared = prepare(Signal([0, 1, 0.5, 0], 44100, "amplitude", onset_sample=1), declaration)
assert prepared["prepared_samples"] == [1.0, 0.5, 0.0]
```

Unavailable requested samples refuse rather than shorten or pad a window.
One-sample and tail windows retain their actual samples. No filter is offered;
adding one requires its own versioned boundary qualification.

## Controls and scorecard gate

`invariant_trial` accepts an estimator-local callable returning a finite vector
from a prepared pair. It runs baseline and transformed inputs through the same
declaration and compares every returned component using declared absolute and
relative numerical tolerances. These tolerances qualify the control's numeric
computation, not hardware fidelity. `check_invariant` exposes the vector check
separately. Nothing averages signed errors or independent windows.

Shift/padding requires an onset-relative declared window. The declared onset
moves by exactly the common integer shift/prefix padding. Negative shifts may
discard only silence outside required samples; other truncation is inapplicable.
Silence padding adds the declared number of zeros at both ends. Common gain
requires a ratio declaration and a gain in the stated strictly positive domain;
an estimator still must refuse zero denominators or other undefined domains.
Absolute amplitude, DC, clipping and normalization decisions are not gain
invariant. Undeclared/out-of-domain controls return `not_applicable`, not pass.

`symmetry_control` compares the full prepared records after swapping reference
and candidate. An asymmetric mutant permutes only candidate samples while
preserving its scalar sum; the control detects it. Primary one-sided timing and
gain faults remain errors even though common property transformations can be
invariant. The analytic grid also retains opposite-signed window errors whose
whole-clip mean is zero.

`gate_rows(rows, controls=..., preparation=..., raw_diagnostic=...)` accepts
already validated scorecard v1 rows and the exact original diagnostic bytes.
It checks their digest boundary, reproduces the prepared records, and requires
every supplied dependent control to pass. Missing, malformed, refused, failed,
or inapplicable required controls force `NO VERDICT`, null `observed`, and a
concrete invalidity reason. Select only the rows dependent on those controls.
Empty control lists refuse. Resampling records are barred from acceptance.

The returned diagnostic retains all original rows/observations, original
diagnostic bytes in hex, raw/prepared records, and controls. Persist its exact
returned bytes. Rows reference `artifact.identity = prep1-<sha256>` and the
digest of those bytes. Estimator versions include the preparation/config/control
identity. The public scorecard validator runs before and after the gate; no
shared schema is extended, and valid serialization alone establishes no claim.

## Separate resampling diagnostic

`resample_diagnostic` is linear interpolation on the explicit output grid
`j / target_rate <= (N-1) / source_rate`. An exactly coincident endpoint is
included; otherwise the last point is strictly inside the available interval.
No extrapolation, synthetic endpoint, filter, antialiasing or downsampling is
provided. Fractional grid coordinates are computed as rational numbers before
binary64 interpolation. Raw and prepared samples remain separate.

The qualified configuration region is: source rate 8000, 16000, 44100 or
48000 Hz; target rate strictly greater than source and at most 4 times source;
2 to 1,000,000 input samples; declared amplitude bound `0 < A <= 1`;
declared bandlimit `0 <= f <= source_rate/32`. Samples outside the declared
amplitude bound refuse. Rates and all configuration numbers must be finite.

The error bound is conditional on a continuous input bounded by A with second
derivative bounded by `A*(2*pi*f)^2` (the declared bandlimited-signal domain).
Finite samples alone cannot establish that premise. On a source interval of
width h, linear interpolation error is at most `max|x''|*h*h/8`.
The diagnostic therefore states
`A*(2*pi*f/source_rate)^2/8 + 32*binary64_epsilon*A`.
The second term budgets rounding for the bounded interpolation arithmetic.
The deterministic analytic grid measures the maximum actual error on twelve
cosine fixtures spanning the declared rates, frequencies, phases and levels,
including near silence, and checks it against the stated bound. This is not a
general audio-resampler qualification. Unverified bandlimit/curvature premises
cannot qualify arbitrary audio. Diagnostic output never enters exact rows.

## Runtime producer consumption

`runtime_evidence_control` validates a version-1 consumer projection: committed
producer identity, source/file hashes, configuration digest, every required case
and runtime, global `SoundIdentity` coordinates/noise slot/train-test mapping,
named normalized/physical parameters, actual float32 artifact bytes/hashes/counts,
required repeat/equality groups and distinct process identities. Requests come
from the preregistered plan, not the observed subset. Missing/stale/failed records
refuse. Synthetic complete/failing/missing/stale projections are consumer tests
only and can never set `normative_oracle=true`.

The tool's native adapters read #12's repeatability and #88's scalar reports,
plans and runners only at a supplied exact Git commit. Untracked or dirty
producer inputs refuse. They invoke the committed producer's read-only byte
comparison functions to reproduce named trace metrics and first divergences,
then check the consumer projection against independently derived requests.
The scalar adapter reads both fresh runs on both sides and compares only
normalized inputs/noise across execution widths; it does not assume physical
parameters, intermediate traces or final audio are byte-identical. A scalar
`diagnostic` or `rejected` oracle status always refuses normative production
promotion. Runtime selection/host scope and scalar DR reconciliation remain
root's separate operator decision; the tool cannot ratify prose decisions.

```sh
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
VECLIB_MAXIMUM_THREADS=1 NUMEXPR_NUM_THREADS=1 \
python3 -S tools/qualify_preparation.py integrate \
  --kind repeatability --producer-root ../issue-12 \
  --producer-commit <exact-committed-head> --raw-root <actual-matrix-artifacts>
# Repeat with --kind scalar, ../issue-88 and its actual raw-artifact root.
```

Exit 0 means the named consumer check passed, not a product/release verdict.
Exit 2 means `NO_VERDICT`; the report retains the concrete refusal. Producer
reports and artifacts must be revalidated again on integration. No ad-hoc
substitute renders are run here.

## Reproduction and evidence limits

```sh
PYTHONPATH=src python3 -S -m unittest discover -s tests -p test_preparation.py -q
python3 -S tools/qualify_preparation.py analytic \
  --check sim/qualification/preparation-v1.json
TORCHSYNTH_ROOT=/tmp/torchsynth-review-20260918 PYTHONPATH=src \
  python3 -S -m unittest discover -s tests -q
TORCHSYNTH_ROOT=/tmp/torchsynth-review-20260918 \
  python3 tools/check_contract.py --require-git-commit
```

The committed analytic artifact contains all raw/prepared arrays/configurations
in separate content-addressed records plus per-control applicability, refusal,
mutant and measured-error outcomes. Generate with `analytic --output <path>`.
No wall-clock timestamp, worktree path or Git dirtiness is part of analytic
identity. The artifact pins the exact module/tool bytes. Reproduction requires
the same binary64/libm results; it is checked by exact record bytes, not merely
schema agreement. There are no optional numerical dependencies or skipped
numerical suites in this implementation.

The checked-in record was generated with **Python 3.14.7**. Use that interpreter
for byte-for-byte `analytic --check` reproduction (for example,
`python3.14 -S tools/qualify_preparation.py analytic --check
sim/qualification/preparation-v1.json`). Its runtime field and the underlying
paired diagnostic retain the actual Python version. An initial clean-clone
check using Python 3.11.5 therefore refused the byte comparison; selecting the
recorded interpreter reproduced the bytes exactly. The clean clone also passed
all 186 stdlib tests and the pinned-source contract check. Functional tests
also run on other supported Python versions.

Actual #12/#88 integration is explicitly **pending** in this analytic artifact.
A correct missing-report refusal does not complete the original TorchSynth
runtime acceptance criterion. Root coordinates committed actual evidence and
DR reconciliation before issue closure. No batch-1 byte identity, fidelity,
hardware, physical-validation or holdout qualification is claimed.

### Observed producer integration, 2026-09-19

A read-only consumer run used #88 commit
`707abf541ec916579ee7c69b85e0fe4dfbded35e`, its committed report digest
`9f64430f6562e335305fec39b7067aa7fa9bd3f8e9e0a173db38aa69a1bc9fd7`, and
raw directory `../issue-88/out/scalar-full-final`. The committed native
comparison code reproduced both canonical/scalar pairs, each with all twelve
cases and 36 seams; the report's byte-comparison result was reproduced.
The consumer returned **NO_VERDICT / missing fresh-process identity**: the
run records lack independent process identifiers. A directory name is not
substituted for that evidence. The committed wrapper describes separate
container executions, but this consumer cannot verify distinct executions
from the report records alone. Root must resolve that evidence interface.

DR-0007 and the report both classify batch-1 as **diagnostic** in the measured
release-era environment, with runtime reconciliation pending #12. Neither
this bounded revalidation nor the diagnostic byte-comparison result promotes
batch-1 to a normative oracle. At #12 commit
`535c25c165f4a63622bf3151a41b7b2d9c88d64c`, the repeatability report was still
absent, and its integration command returned `NO_VERDICT`. Both producers must
be revalidated at integration; the original actual-runtime AC remains open.
