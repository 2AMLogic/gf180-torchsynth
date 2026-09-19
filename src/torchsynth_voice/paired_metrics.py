"""Time-locked scalar-trace measurements; see spec/PAIRED-METRICS.md.

No audio preparation or implicit fidelity limits. NumPy is imported only for
requested FFT measurements. Diagnostic bytes, not scorecard syntax, retain the
raw measurements when a rubric cannot issue a verdict.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from fractions import Fraction
from numbers import Integral, Real

from .scorecard import validate_row

VERSION = "1"
BANDS = (
    ("0_20", 0, 20),
    ("20_200", 20, 200),
    ("200_2000", 200, 2000),
    ("2000_20000", 2000, 20000),
    ("20000_up", 20000, None),
)


class MetricsError(ValueError):
    """Malformed input or rubric, rather than a measured waveform failure."""


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise MetricsError(f"{name} must be nonblank text")


def _finite(value, name):
    if (
        type(value) not in (int, float)
        or not -sys.float_info.max <= value <= sys.float_info.max
        or not math.isfinite(value)
    ):
        raise MetricsError(f"{name} must be a finite binary64 number")


@dataclass(frozen=True)
class Limit:
    """An absolute interval evaluated exactly on supplied int/binary64 values."""

    expected: float
    tolerance: float
    unit: str
    source: str

    def __post_init__(self):
        _finite(self.expected, "expected")
        _finite(self.tolerance, "tolerance")
        if self.tolerance < 0:
            raise MetricsError("tolerance must be nonnegative")
        _text(self.unit, "unit")
        _text(self.source, "source")


@dataclass(frozen=True)
class Rubric:
    id: str
    version: str
    limits: Mapping[str, Limit]

    def __post_init__(self):
        _text(self.id, "rubric.id")
        _text(self.version, "rubric.version")
        if not isinstance(self.limits, Mapping) or not self.limits:
            raise MetricsError("rubric requires explicit metric limits")
        for name, limit in self.limits.items():
            _text(name, "metric name")
            if not isinstance(limit, Limit):
                raise MetricsError("rubric values must be Limit instances")


def analytic_exactness_rubric() -> Rubric:
    """Only nonempty, same-rate/count, exactly equal analytic test arrays pass.

    This is not a float/fixed fidelity, hardware, or release qualification.
    Other properties remain NO VERDICT unless a caller supplies their limits.
    """
    source = "spec/PAIRED-METRICS.md: analytic exactness v1 (test fixtures only)"
    return Rubric(
        "paired-analytic-exactness",
        "1",
        {
            "framing_match": Limit(1, 0, "1", source),
            "exact_equal": Limit(1, 0, "1", source),
        },
    )


def _metric(value, unit, *, status="valid", reason="measured", coverage="complete"):
    if value is not None and not math.isfinite(value):
        value, status, reason = None, "invalid", "binary64_range_exceeded"
    return {
        "value": value,
        "unit": unit,
        "status": status,
        "reason": reason,
        "coverage": coverage,
    }


def _unavailable(unit, reason, *, status="insufficient", coverage="complete"):
    return _metric(None, unit, status=status, reason=reason, coverage=coverage)


def _samples(data, name):
    if data is None:
        return None, {
            "state": "missing",
            "sample_count": None,
            "dtype": None,
            "binary64_sha256": None,
        }
    if (
        isinstance(data, (str, bytes, Mapping))
        or not hasattr(data, "__len__")
        or not hasattr(data, "__getitem__")
    ):
        raise MetricsError(f"{name} must be a one-dimensional real sequence")
    dtype = getattr(data, "dtype", None)
    if getattr(data, "ndim", 1) != 1 or (
        dtype is not None
        and (
            getattr(dtype, "kind", None) not in ("i", "u", "f")
            or not 0 < getattr(dtype, "itemsize", 0) <= 8
        )
    ):
        raise MetricsError(
            f"{name} requires a one-dimensional int/float array of at most 64 bits"
        )
    converted, digest = [], hashlib.sha256()
    for sample in data:
        sample_dtype = getattr(sample, "dtype", None)
        supported_scalar = type(sample) in (int, float) or (
            sample_dtype is not None
            and getattr(sample_dtype, "kind", None) in ("i", "u", "f")
            and 0 < getattr(sample_dtype, "itemsize", 0) <= 8
        )
        if (
            not supported_scalar
            or isinstance(sample, bool)
            or not isinstance(sample, Real)
        ):
            raise MetricsError(
                f"{name} samples must be real numbers, not bools/objects"
            )
        if isinstance(sample, Integral) and abs(int(sample)) > 2**53:
            raise MetricsError(
                f"{name} integer exceeds the exact binary64 conversion range"
            )
        number = float(sample)
        if not math.isfinite(number):
            raise MetricsError(f"{name} samples must be finite")
        converted.append(number)
        digest.update(struct.pack("<d", number))
    return converted, {
        "state": "available",
        "sample_count": len(converted),
        "dtype": str(dtype) if dtype is not None else "real-sequence",
        "binary64_sha256": digest.hexdigest(),
    }


def _rescale(value, scale):
    result = value * scale
    # Preserve a refusal if a genuinely nonzero result rounds below binary64.
    return math.nan if value != 0 and scale != 0 and result == 0 else result


def _stats(values):
    peak_index = max(range(len(values)), key=lambda i: abs(values[i]))
    scale = abs(values[peak_index])
    if scale == 0:
        return 0.0, 0.0, 0.0, peak_index
    normalized = [x / scale for x in values]
    mean = _rescale(math.fsum(normalized) / len(values), scale)
    mean_abs = _rescale(math.fsum(abs(x) for x in normalized) / len(values), scale)
    rms = _rescale(math.sqrt(math.fsum(x * x for x in normalized) / len(values)), scale)
    return mean, mean_abs, rms, peak_index


def _summary(metrics, prefix, samples, unit, bounds):
    fields = {
        "rms": unit,
        "dc": unit,
        "peak_abs": unit,
        "peak_value": unit,
        "peak_index": "sample",
        "clip_count": "sample",
    }
    if not samples:
        for key, metric_unit in fields.items():
            metrics[f"{prefix}.{key}"] = _unavailable(
                metric_unit,
                "input_missing" if samples is None else "empty_input",
                status="missing" if samples is None else "invalid",
                coverage="none",
            )
        return
    dc, _, rms, peak = _stats(samples)
    values = {
        "rms": rms,
        "dc": dc,
        "peak_abs": abs(samples[peak]),
        "peak_value": samples[peak],
        "peak_index": peak,
    }
    for key, number in values.items():
        metrics[f"{prefix}.{key}"] = _metric(number, fields[key])
    metrics[f"{prefix}.clip_count"] = (
        _metric(sum(x <= bounds[0] or x >= bounds[1] for x in samples), "sample")
        if bounds is not None
        else _unavailable("sample", "clip_bounds_not_declared")
    )


def _error_stats(metrics, prefix, error, unit, offset=0):
    names = {
        "mean_error": unit,
        "mean_abs_error": unit,
        "error_rms": unit,
        "max_abs_error": unit,
        "max_abs_error_index": "sample",
    }
    if not all(math.isfinite(x) for x in error):
        for key, metric_unit in names.items():
            metrics[prefix + key] = _unavailable(
                metric_unit, "subtraction_overflow", status="invalid"
            )
        return
    mean, mean_abs, rms, peak = _stats(error)
    values = (mean, mean_abs, rms, abs(error[peak]), peak + offset)
    for (key, metric_unit), number in zip(names.items(), values):
        metrics[prefix + key] = _metric(number, metric_unit)


def _spectral(metrics, error, rate, unit, enabled):
    backend = None
    reason = "spectral_not_requested"
    if enabled:
        try:
            import numpy as np
        except ImportError:
            reason = "numpy_unavailable_install_metrics_extra"
        else:
            backend = {"name": "numpy.fft.rfft", "numpy_version": np.__version__}
            reason = "pair_unavailable"
    usable = error is not None and all(math.isfinite(x) for x in error)
    if backend is None or not usable:
        for label, _, _ in BANDS:
            for quantity, metric_unit in (
                ("error_rms", unit),
                ("error_power", f"({unit})^2"),
            ):
                if backend is not None and error is None:
                    unavailable = metrics["error_rms"]
                    metrics[f"band.{label}.{quantity}"] = _unavailable(
                        metric_unit,
                        unavailable["reason"],
                        status=unavailable["status"],
                        coverage=unavailable["coverage"],
                    )
                else:
                    metrics[f"band.{label}.{quantity}"] = _unavailable(
                        metric_unit,
                        reason if backend is None else "subtraction_overflow",
                        status="insufficient" if backend is None else "invalid",
                    )
        return backend
    n = len(error)
    scale = max(abs(x) for x in error)
    # Scale only the accumulation, then restore native units. No gain fitting,
    # padding, taper, detrending, or alteration of the paired sample sequence.
    samples = np.asarray(error, dtype=np.float64)
    spectrum = np.fft.rfft(samples / scale if scale else samples, norm="forward")
    power = np.abs(spectrum) ** 2
    power[1:] *= 2
    if n % 2 == 0:
        power[-1] /= 2
    frequency = np.arange(len(power), dtype=np.float64) * (rate / n)
    for label, low, high in BANDS:
        mask = frequency >= low
        if high is not None:
            mask &= frequency < high
        for quantity, metric_unit in (
            ("error_rms", unit),
            ("error_power", f"({unit})^2"),
        ):
            key = f"band.{label}.{quantity}"
            if not np.any(mask):
                metrics[key] = _unavailable(metric_unit, "no_fft_bins_in_band")
                continue
            rms = _rescale(math.sqrt(math.fsum(float(x) for x in power[mask])), scale)
            number = rms if quantity == "error_rms" else _rescale(rms, rms)
            metrics[key] = _metric(number, metric_unit)
    return backend


def compare_paired(
    reference,
    candidate,
    *,
    reference_rate_hz,
    candidate_rate_hz,
    unit: str,
    window_samples: int = 1024,
    clip_bounds=None,
    spectral: bool = False,
) -> dict:
    """Measure one named scalar trace with index zero as its declared time origin.

    Inputs are finite 1-D real sequences (or None for missing evidence). Rate
    and count mismatches refuse paired arithmetic; no common prefix is scored.
    Empty sequences are invalid, not silently perfect matches. See the spec
    for conversion, accumulation, FFT, tie, zero, and clipping conventions.
    """
    _text(unit, "unit")
    for rate in (reference_rate_hz, candidate_rate_hz):
        _finite(rate, "sample rate")
        if rate <= 0:
            raise MetricsError("sample rate must be positive")
    if type(window_samples) is not int or window_samples < 1:
        raise MetricsError("window_samples must be a positive integer")
    if type(spectral) is not bool:
        raise MetricsError("spectral must be bool")
    if clip_bounds is not None:
        if not isinstance(clip_bounds, (tuple, list)) or len(clip_bounds) != 2:
            raise MetricsError("clip_bounds requires two finite bounds")
        for bound in clip_bounds:
            _finite(bound, "clip bound")
        if clip_bounds[0] >= clip_bounds[1]:
            raise MetricsError("clip bounds must be increasing")
    ref, ref_info = _samples(reference, "reference")
    cand, cand_info = _samples(candidate, "candidate")
    metrics, windows = {}, []
    for label, samples in (("reference", ref), ("candidate", cand)):
        metrics[f"{label}.sample_count"] = (
            _metric(len(samples), "sample")
            if samples is not None
            else _unavailable(
                "sample", "input_missing", status="missing", coverage="none"
            )
        )
        _summary(metrics, label, samples, unit, clip_bounds)
    missing = ref is None or cand is None
    same_frame = (
        not missing and len(ref) == len(cand) and reference_rate_hz == candidate_rate_hz
    )
    reason = (
        "input_missing"
        if missing
        else "frame_mismatch"
        if not same_frame
        else "empty_input"
        if not ref
        else "measured"
    )
    for name, number, metric_unit in (
        ("sample_count_delta", None if missing else len(cand) - len(ref), "sample"),
        ("framing_match", None if missing else int(same_frame and bool(ref)), "1"),
    ):
        metrics[name] = (
            _metric(number, metric_unit)
            if not missing
            else _unavailable(metric_unit, reason, status="missing", coverage="none")
        )
    paired_fields = {
        "exact_equal": "1",
        "mismatch_count": "sample",
        "first_divergence_index": "sample",
        "mean_error": unit,
        "mean_abs_error": unit,
        "error_rms": unit,
        "max_abs_error": unit,
        "max_abs_error_index": "sample",
        "snr_db": "dB",
    }
    error = None
    if reason != "measured":
        for key, metric_unit in paired_fields.items():
            metrics[key] = _unavailable(
                metric_unit,
                reason,
                status="missing" if missing else "invalid",
                coverage="none" if missing or not ref or not cand else "partial",
            )
    else:
        mismatch = [i for i, (a, b) in enumerate(zip(ref, cand)) if a != b]
        metrics["exact_equal"] = _metric(int(not mismatch), "1")
        metrics["mismatch_count"] = _metric(len(mismatch), "sample")
        metrics["first_divergence_index"] = (
            _metric(mismatch[0], "sample")
            if mismatch
            else _unavailable("sample", "equal_no_divergence")
        )
        error = [b - a for a, b in zip(ref, cand)]
        _error_stats(metrics, "", error, unit)
        signal_rms, error_rms = (
            metrics["reference.rms"]["value"],
            metrics["error_rms"]["value"],
        )
        if signal_rms is None or error_rms is None:
            metrics["snr_db"] = _unavailable("dB", "rms_unavailable", status="invalid")
        elif signal_rms == 0 or error_rms == 0:
            snr_reason = (
                "undefined_both_silent"
                if signal_rms == error_rms == 0
                else "negative_infinity_silent_reference"
                if signal_rms == 0
                else "positive_infinity_zero_error"
            )
            metrics["snr_db"] = _unavailable("dB", snr_reason)
        else:
            metrics["snr_db"] = _metric(
                20 * (math.log10(signal_rms) - math.log10(error_rms)), "dB"
            )
        for index, start in enumerate(range(0, len(error), window_samples)):
            stop = min(start + window_samples, len(error))
            prefix = f"window.{index}."
            windows.append({"prefix": prefix, "start": start, "stop": stop})
            _error_stats(metrics, prefix, error[start:stop], unit, start)
    backend = _spectral(metrics, error, reference_rate_hz, unit, spectral)
    return {
        "schema": "paired-metrics",
        "schema_version": 1,
        "estimator": {"name": "time-locked-paired", "version": VERSION},
        "settings": {
            "reference_rate_hz": reference_rate_hz,
            "candidate_rate_hz": candidate_rate_hz,
            "unit": unit,
            "conversion": "binary64-exact-input-v1",
            "accumulation": "scaled-fsum-binary64-v1",
            "error_sign": "candidate-reference",
            "preparation": "none",
            "time_origin": "index-zero",
            "window_samples": window_samples,
            "window": "rectangular-nonoverlap-tail-included-v1",
            "clip_bounds": list(clip_bounds) if clip_bounds is not None else None,
            "spectral_requested": spectral,
            "fft": "rfft-N-forward-parseval-one-sided-v1",
            "bands_hz": [
                {"name": label, "low_inclusive": low, "high_exclusive": high}
                for label, low, high in BANDS
            ],
        },
        "implementation": {
            "python": sys.version.split()[0],
            "spectral_backend": backend,
        },
        "inputs": {"reference": ref_info, "candidate": cand_info},
        "pair_status": reason,
        "metrics": metrics,
        "windows": windows,
    }


def artifact_reference(*, artifact_id: str, record_bytes: bytes) -> dict:
    """Explicitly adapt an artifact_id to scorecard identity; hash stored bytes.

    This helper verifies neither render provenance nor artifact qualification.
    For a render record, pass its artifact_id and its original metadata bytes.
    """
    _text(artifact_id, "artifact_id")
    if type(record_bytes) is not bytes:
        raise MetricsError("record_bytes must be bytes")
    return {"identity": artifact_id, "sha256": hashlib.sha256(record_bytes).hexdigest()}


def _json_bytes(record):
    return (
        json.dumps(record, allow_nan=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def scorecard_rows(
    comparison: dict,
    *,
    case_id: str,
    partition: str,
    trace: str,
    rubric: Rubric | None = None,
) -> tuple[list[dict], bytes]:
    """Return validated v1 rows AND the exact referenced diagnostic bytes to save.

    Unspecified limits refuse decisions while retaining raw values in the
    diagnostic. No tolerance, measurement, or digest is invented to satisfy a
    schema. The caller must persist the returned bytes alongside the scorecard.
    """
    metrics = comparison["metrics"]
    if rubric is not None:
        if not isinstance(rubric, Rubric):
            raise MetricsError("rubric must be a Rubric")
        for name, limit in rubric.limits.items():
            if name not in metrics or limit.unit != metrics[name]["unit"]:
                raise MetricsError(f"unknown metric or mismatched rubric unit: {name}")
    rubric_id = (
        {"id": rubric.id, "version": rubric.version}
        if rubric
        else {"id": "no-decision-rubric", "version": "1"}
    )
    context = {"case": {"id": case_id, "partition": partition}, "trace": trace}
    record = {
        "schema": "paired-metrics-diagnostic",
        "schema_version": 1,
        **context,
        "comparison": comparison,
        "rubric": {
            **rubric_id,
            "limits": {name: asdict(limit) for name, limit in rubric.limits.items()}
            if rubric
            else {},
        },
    }
    data = _json_bytes(record)
    digest = hashlib.sha256(data).hexdigest()
    artifact = artifact_reference(artifact_id=f"pm1-{digest}", record_bytes=data)
    settings_digest = hashlib.sha256(
        _json_bytes(
            {
                "settings": comparison["settings"],
                "implementation": comparison["implementation"],
            }
        )
    ).hexdigest()
    estimator = {
        "name": comparison["estimator"]["name"],
        "version": f"{comparison['estimator']['version']}+{settings_digest}",
    }
    rows = []
    for name, metric in metrics.items():
        limit = rubric.limits.get(name) if rubric else None
        measured = (
            metric["status"] == "valid"
            and metric["value"] is not None
            and metric["coverage"] == "complete"
            and limit is not None
        )
        if measured:
            # Preserve integer limits and the exact values of binary64 inputs;
            # rounded subtraction can change a verdict at an interval boundary.
            verdict = (
                "PASS"
                if abs(Fraction(metric["value"]) - Fraction(limit.expected))
                <= Fraction(limit.tolerance)
                else "FAIL"
            )
            validity = {
                "status": "valid",
                "reason": "explicit absolute-interval rubric applied to native-unit metric",
            }
        else:
            verdict = (
                "MISSING EVIDENCE" if metric["status"] == "missing" else "NO VERDICT"
            )
            validity = {
                "status": metric["status"]
                if metric["status"] != "valid"
                else "insufficient",
                "reason": metric["reason"]
                if metric["status"] != "valid"
                else "no_declared_limit_for_metric",
            }
        row = {
            "schema_version": 1,
            **context,
            "property": name,
            "estimator": estimator,
            "rubric": rubric_id,
            "unit": metric["unit"],
            "expected": {
                "value": limit.expected if limit else None,
                "source": limit.source if limit else "unavailable: no declared limit",
            },
            "tolerance": {
                "value": limit.tolerance if limit else None,
                "source": limit.source if limit else "unavailable: no declared limit",
            },
            "observed": metric["value"] if measured else None,
            "validity": validity,
            "coverage": metric["coverage"],
            "verdict": verdict,
            "artifact": artifact,
        }
        validate_row(row)
        rows.append(row)
    return rows, data
