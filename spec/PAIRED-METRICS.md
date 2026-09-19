# Time-locked paired metrics v1

`torchsynth_voice.paired_metrics` measures one scalar waveform or named trace.
It never aligns, trims, crops, pads, rescales, resamples, detrends, or fits gain.
Index zero is the caller's declared common time origin. No aligned or scaled
diagnostic comparison is provided. All returned measurements are primary.

This measurement contract changes no target or arithmetic profile. The normative
TorchSynth commit remains `2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`.
The hardware product is one four-second, 44.1 kHz default-nebula sound per
trigger, potentially using resolved `batch_size=1, reproducible=False` execution.
Canonical batched capture is a qualification fixture protocol, not hardware
batching. These metrics also accept control-rate traces and analytic fixtures;
they do not assume an audio rate, four-second length, or batch dimension.

## API and input boundary

```python
from pathlib import Path
from torchsynth_voice.paired_metrics import (
    compare_paired, analytic_exactness_rubric, scorecard_rows,
)
from torchsynth_voice.scorecard import make_report, report_to_json

measurement = compare_paired(
    [0.0, 1.0, 0.0], [0.0, 0.0, 1.0],
    reference_rate_hz=44100, candidate_rate_hz=44100,
    unit="amplitude", window_samples=1024, clip_bounds=(-1, 1),
    spectral=False,
)
rows, diagnostic_bytes = scorecard_rows(
    measurement, case_id="analytic-delay", partition="development",
    trace="synthetic-output", rubric=analytic_exactness_rubric(),
)
# Save these exact bytes; the scorecard digest covers them, including the LF.
Path(rows[0]["artifact"]["identity"] + ".json").write_bytes(diagnostic_bytes)
report = make_report(rows, partition="development", rubric=rows[0]["rubric"])
encoded_report = report_to_json(report)
```

Both sample rates and a nonblank native unit are mandatory. Rates are finite,
positive numbers and must compare exactly equal before paired arithmetic.
Inputs are one-dimensional finite real sequences, including Python lists,
tuples, `array.array`, and NumPy integer/float arrays of at most 64 bits. Select
a trace/channel explicitly before calling: `(1, N)` is rejected, not squeezed.
Scalar NumPy types inside sequences obey the same width/type restrictions.
Complex, bool, object, string, nested, non-finite, extended-precision, and
arbitrary coercible inputs are rejected with `MetricsError`. Iterators are not
accepted. `None` explicitly records missing evidence; an empty sequence is
invalid evidence, never a perfect match.

Samples convert individually to binary64 before subtraction, avoiding integer
wraparound and float32 subtraction. Integer magnitudes above `2^53` are rejected
conservatively so conversion cannot erase a difference. Python floats and
float16/32/64 samples keep their supplied numeric values; no PCM decoding or
fixed-point scale conversion is implicit. Signed zeros compare equal. Equality
is numeric equality of these values, with no epsilon or tolerance; it is not a
bit-pattern test of the original representation. Input descriptors retain dtype
and SHA-256 of the exact converted sequence encoded as little-endian binary64.
These hashes identify the measured samples, not original files or qualified
render provenance. Sequences without a dtype are labeled `real-sequence`.

Unequal counts or rates produce `pair_status=frame_mismatch`. Independent input
summaries and signed count delta remain available, but all paired sample
arithmetic is refused. No common prefix or synthetic tail is scored. Missing
and extra samples therefore remain framing failures. Count/rate-matched empty
inputs produce `empty_input`. `framing_match` is numeric 1 only for two nonempty,
same-count, same-rate inputs; otherwise it is 0, or null when input is missing.
Malformed arguments raise before a result is returned.

## Measurements and numeric policy

Each entry in `metrics` contains `value`, `unit`, `status`, `reason`, and
`coverage`. Finite measurements use `status=valid`; this asserts successful
computation, not fidelity qualification. Refused values are null with an
explicit reason. No NaN or Infinity appears in a diagnostic or scorecard.

Let `r[i]` be reference, `c[i]` candidate, and **`e[i] = c[i] - r[i]`**.

| Property | Meaning / unit |
| --- | --- |
| `reference.sample_count`, `candidate.sample_count` | Independent counts, `sample` |
| `sample_count_delta` | Candidate minus reference count, `sample` |
| `framing_match`, `exact_equal` | Numeric 0/1, dimensionless `1`; equality requires a valid pair |
| `mismatch_count`, `first_divergence_index` | Number of unequal values and first unequal index, `sample`; equal pairs have null first divergence (`equal_no_divergence`) |
| `mean_error`, `mean_abs_error` | Mean signed error and mean absolute error, native unit |
| `error_rms`, `max_abs_error` | `sqrt(mean(e²))` and largest `abs(e)`, native unit |
| `max_abs_error_index` | Earliest index achieving maximum absolute error, `sample` |
| `reference.*`, `candidate.*`: `rms`, `dc` | Input RMS and signed mean, native unit |
| `reference.*`, `candidate.*`: `peak_abs`, `peak_value`, `peak_index` | Absolute peak, signed value at that peak, and index; earliest absolute-peak tie wins |
| `reference.clip_count`, `candidate.clip_count` | Count at or outside caller-supplied inclusive low/high bounds, `sample` |
| `snr_db` | `20 * (log10(reference.rms) - log10(error_rms))`, `dB` |

Clipping bounds have no default. An unspecified bound yields null
`clip_bounds_not_declared`; a boundary count alone does not prove a nonlinear
clipping operation occurred. There is no inferred normalization gain.
For nonempty all-zero arrays, peak and maximum-error ties select index zero.

Accumulation is versioned as `scaled-fsum-binary64-v1`: divide by maximum
absolute value, use `math.fsum` for sums (and squared normalized values for
RMS), then restore native units. This computational scaling avoids avoidable
sum/square overflow; it never adjusts the relative levels of the signals.
Arithmetic remains binary64, including subtraction, products, logs, FFT, and
final rounding; cancellation and very small contributions can still lose
precision. This API does not claim arbitrary-precision real arithmetic.
Subtraction overflow refuses affected error rows (`subtraction_overflow`),
while exact mismatch detection and input summaries remain available.
Nonrepresentable outputs, including a detected nonzero rescaling result that
rounds to zero, become null `binary64_range_exceeded`, never fabricated zero.
Numerical refusal is per metric/window; it does not hide other raw results.

There is no silence floor. Silence means exactly zero RMS:

| Reference RMS | Error RMS | SNR value / reason |
| --- | --- | --- |
| Positive | Positive | Finite formula above; log difference avoids ratio overflow |
| Zero | Zero | null / `undefined_both_silent` |
| Zero | Positive | null / `negative_infinity_silent_reference` |
| Positive | Zero | null / `positive_infinity_zero_error` |
| Unavailable | Any, or vice versa | null / `rms_unavailable` |

Near-silence remains a measurement at its native level. An equal nonzero pair
can pass explicit exactness while its infinite SNR independently refuses a
scalar scorecard verdict.

## Windows and spectral bands

`window_samples` defaults to 1024 and must be a positive integer. Windows are
nonoverlapping rectangular segments beginning at zero. The last short segment
is retained and divided by its actual count. `windows` lists half-open
`[start, stop)` bounds and the corresponding `window.K.` metric prefixes.
Each window retains mean, mean absolute, RMS, maximum absolute error and the
maximum's **global** sample index. No taper, overlap, silence trim, or padding
is applied. These windows only partition diagnostics; whole-array rows still
cover every sample.

`spectral=True` requests NumPy via the optional `metrics` extra. Without the
extra, spectral rows explicitly refuse with
`numpy_unavailable_install_metrics_extra`; time-domain results remain usable.
With `spectral=False` they explicitly refuse with `spectral_not_requested`.
Neither path imports Torch. The dedicated CI job installs the metrics extra
and requests the spectral tests, so absence cannot silently skip coverage.

The versioned FFT definition is `rfft-N-forward-parseval-one-sided-v1`:

- Whole error sequence of length **N**, rectangular window; no padding,
  detrending, resampling, overlap, log floor, or phase removal from the error.
- `numpy.fft.rfft(e, norm="forward")`: complex coefficients divided by N.
  Computational scaling by `max(abs(e))` is restored in the results to avoid
  intermediate overflow. This uses the FFT path, not a full-clip quadratic DFT.
- Squared coefficient magnitudes have weight 2 except DC and even-N Nyquist,
  which have weight 1. The last odd-N bin retains weight 2. The sum over all
  bands equals error mean-square to binary64/FFT precision (Parseval).
- Fixed bands are `[0,20)`, `[20,200)`, `[200,2000)`, `[2000,20000)`, and
  `[20000,+infinity)` Hz. Only actual bins through Nyquist participate. The
  serialized final upper bound is null, meaning unbounded, not an unknown edge.
  Frequencies are `k * (sample_rate / N)`; a boundary bin belongs to the band
  starting at that boundary, including when it is Nyquist.
- `band.NAME.error_power` is sum of weighted bin power in `(unit)^2`;
  `band.NAME.error_rms` is its square root in native units. Names are `0_20`,
  `20_200`, `200_2000`, `2000_20000`, `20000_up`. Bands with no bins return null
  `no_fft_bins_in_band`, distinct from measured zero power in a populated band.
  Squaring a representable RMS can overflow or underflow; power then refuses
  independently, preserving the RMS.

All settings, input counts/hashes/dtypes, Python version and actual NumPy version
are retained. Scorecard estimator versions append a SHA-256 of settings and
implementation metadata, preventing aggregation across different window/rate/
clipping/FFT configurations or backends under a shared estimator identity.
Frequency bands are diagnostic definitions, not hardware acceptance tolerances.

## Rubrics, diagnostics, and scorecards

`Limit(expected, tolerance, unit, source)` defines an explicit inclusive
absolute interval: `abs(observed - expected) <= tolerance`. Expected and
nonnegative tolerance must be Python integers or finite binary64 floats (not
bools), with magnitudes at most `sys.float_info.max`; units must match the named
metric. Unlike sample conversion, rubric integers need not be representable in
binary64: the interval decision uses exact rational arithmetic on the recorded
integer values and exact numeric values of the binary64 floats. It neither
rounds integers to floats nor rounds the difference before comparing it with
the tolerance. This preserves inclusive boundaries and zero-tolerance equality,
including beyond `2^53`, without changing the binary64 measurement computation.
The supplied values remain unchanged in the diagnostic and scorecard.
`Rubric(id, version, limits)` supplies limits keyed by exact metric names;
unknown names fail rather than silently omitting a check. `source` documents
the origin of both values. One-sided/lower-bound rules are not inferred.

No rubric is selected by default. Unspecified limits yield `NO VERDICT` and
`observed=null`, even if the raw measurement is finite. Finite rows with complete
coverage and a declared limit can yield PASS/FAIL. Undefined metrics refuse
even with a declared limit; missing inputs yield MISSING EVIDENCE for dependent
rows. Partial-frame evidence cannot pass a paired measurement. Existing
scorecard validators run on every emitted row and enforce the v1 contract.

The opt-in `analytic_exactness_rubric()` tests only `framing_match == 1` and
`exact_equal == 1`, both with tolerance zero. It is narrowly for synthetic
analytic fixtures, not a proposed float-to-fixed tolerance or hardware release
rubric. Other properties remain refusals. Caller rubrics are explicit producer
assertions; this module cannot establish their external qualification. There
is no aggregate fidelity or product verdict.

`scorecard_rows` returns `(rows, diagnostic_bytes)`. The strict UTF-8 JSON
diagnostic bundles the full raw comparison, case/partition/trace, and explicit
rubric limits and sources. Its content identity is `pm1-` plus the SHA-256 of
those exact bytes. Each row points to that identity and digest. **Persist the
returned bytes**: serializing only the rows would lose refused raw values.
This API returns bytes without choosing a storage root or modifying render
artifact schemas. No capture or file digest is invented for missing evidence.

`artifact_reference(artifact_id=..., record_bytes=...)` explicitly maps the
render/artifact name `artifact_id` to scorecard `artifact.identity` and computes
`artifact.sha256` from the actual supplied metadata/diagnostic bytes. For a
render artifact, pass its ID and original stored bytes after external artifact
validation; this small adapter does not validate render semantics or claim
that an input-identity hash is a metadata-file hash.

## Verification

`tests/test_paired_metrics.py` contains stdlib analytic/scorecard tests plus an
explicit `--spectral` suite. The latter requires NumPy and fails to start if it
is absent. Normal discovery has no optional test skips and imports no Torch.
Analytic tolerances in tests check floating computation error on known signals;
they are not fidelity limits. Full-clip smoke uses a synthetic terminal impulse,
not TorchSynth or hardware audio. Peak RSS is the process high-water mark,
including NumPy and prior tests, not an isolated FFT allocation measurement.

Run:

```sh
python3 -m unittest discover -s tests -v
python3 tools/check_contract.py
uv sync --locked --extra metrics
uv run --no-sync python tests/test_paired_metrics.py --spectral -v
```

This establishes metric and serialization behavior only. Source/runtime
qualification, float/fixed fidelity, RTL equivalence, listening, synthesis,
layout, signoff, and playback require their own evidence.

Builder evidence recorded 2026-09-18, based on main `1315f8f`:

| Executed check | Observed result |
| --- | --- |
| Full `unittest discover -s tests -v`, Python 3.14.7, with `TORCHSYNTH_ROOT` pointing to the verified pinned upstream checkout | 95 tests passed in 6.780 s, zero failures/skips; includes 20 new stdlib metrics tests. |
| Same full suite with `python3 -S` (site packages disabled) | 95 tests passed, zero failures/skips; confirms the normal suite needs neither NumPy nor Torch. |
| `python3 tools/check_contract.py` | `contract manifests are internally consistent`, exit 0. |
| `uv sync --locked --extra metrics` and explicit Torch absence check | Only project and NumPy installed; `find_spec('torch') is None`. |
| `tests/test_paired_metrics.py --spectral -v`, Python 3.13.2 / locked NumPy 2.5.3 | 29 passed (20 stdlib + 9 spectral), zero failures/skips. Full-clip smoke: 176,400 samples, 3.790 s, process peak RSS 65.6 MiB. |
| Same spectral suite, isolated Python 3.11.5 / locked NumPy 2.4.6 | 29 passed, zero failures/skips. Full-clip smoke: 3.105 s, process peak RSS 82.6 MiB. |
| `python3 -m compileall -q src tests tools` | Exit 0. |
| Ruff 0.16.8 `format --isolated --check` and `check --isolated --select E4,E7,E9,F` on both new Python files | Both passed. |
| Actionlint 1.7.12, dedicated metrics workflow; `git diff --check` | Both exited 0. |

The final full-suite and spectral runs above ran concurrently; timings reflect
host load and are smoke evidence, not benchmark guarantees. The synthetic
full-clip terminal impulse has first divergence 176,399, RMS error `1/420`, and
summed spectral error power `1/176400`, checked independently in the test.
The first 15 tests were written before the module and failed with
`ModuleNotFoundError: No module named 'torchsynth_voice.paired_metrics'`; they
passed after implementation. Additional edge and FFT tests followed. No actual
TorchSynth audio, RTL, hardware, or fidelity qualification is claimed.
