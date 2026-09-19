"""Native-unit spectral/noise/mix apparatus; qualification is analytic only.

All numerical definitions and applicability limits are in SPECTRAL-ESTIMATORS.md.
No transforms, hardware tolerances, or aggregate quality scores live here.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

from .paired_metrics import BANDS, Rubric, compare_paired
from .paired_metrics import scorecard_rows as paired_rows
from .scorecard import validate_row

VERSION = "spectral-noise-mix-v1"
LAGS = (1, 2, 8, 32)
POWER_FLOOR = 1e-20
LOG_FLOOR = 1e-24
COHERENCE_RATIO = 1e-8
RUBRIC = {"id": "analytic-spectral-noise-mix", "version": "1"}


def require_numpy():
    """An explicitly requested numerical measurement must run or fail."""
    try:
        import numpy as np
    except ImportError as error:
        raise RuntimeError(
            "NumPy required: install the locked metrics extra"
        ) from error
    return np


def _metric(value, unit="1", reason="measured", status="valid"):
    if value is not None and not math.isfinite(value):
        value, status, reason = None, "invalid", "binary64_range_exceeded"
    return {
        "value": value,
        "unit": unit,
        "status": status,
        "reason": reason,
        "coverage": "none" if status == "missing" else "complete",
    }


def _refuse(unit, reason, status="insufficient"):
    return _metric(None, unit, reason, status)


def _result(kind, metrics, settings=None, inputs=None, **diagnostics):
    return {
        "schema": "spectral-noise-mix-measurement",
        "schema_version": 1,
        "estimator": {"name": kind, "version": VERSION},
        "settings": {
            "definition": VERSION,
            "preparation": "identity-native",
            "allowed_preparation": [],
            "forbidden_preparation": [
                "alignment",
                "trim",
                "padding",
                "resample",
                "normalize",
                "detrend",
                "gain_fit",
                "filter",
                "window",
            ],
            "selected_interval": "whole-input",
            "time_origin": "clip-index-zero",
            "boundary": "none-no-extension",
            **(settings or {}),
        },
        "implementation": {"python": sys.version.split()[0]},
        "inputs": inputs or {},
        "metrics": metrics,
        **diagnostics,
    }


def _samples(values, rate):
    # Reuse the public landed validation/identity boundary, not a new transform.
    checked = compare_paired(
        values,
        None,
        reference_rate_hz=rate,
        candidate_rate_hz=rate,
        unit="amplitude",
        spectral=False,
    )
    if values is not None and (not len(values) or any(abs(x) > 1e6 for x in values)):
        raise ValueError("nonempty samples with magnitude <= 1e6 required")
    return (None if values is None else [float(x) for x in values]), checked


def _absolute(checked, prefix="signal"):
    return {
        f"{prefix}.{name}": copy.deepcopy(checked["metrics"][f"reference.{name}"])
        for name in ("dc", "rms", "peak_abs", "peak_value", "peak_index")
    }


@dataclass(frozen=True)
class NoiseRecord:
    """Original selected noise payload, never a converted numeric hash."""

    data: bytes
    shape: tuple[int, ...]
    dtype: str
    seed: int
    slot: int

    def __post_init__(self):
        if (
            type(self.data) is not bytes
            or self.dtype not in ("<f4", "<f8")
            or not self.shape
            or any(type(n) is not int or n < 1 for n in self.shape)
            or type(self.seed) is not int
            or self.seed < 0
            or type(self.slot) is not int
            or self.slot < 0
            or len(self.data) != math.prod(self.shape) * int(self.dtype[-1])
        ):
            raise ValueError("invalid exact noise bytes/shape/dtype/selection")


def noise_identity(reference: NoiseRecord | None, candidate: NoiseRecord | None):
    descriptors = {}
    for name, record in (("reference", reference), ("candidate", candidate)):
        descriptors[name] = (
            None
            if record is None
            else {
                "sha256": hashlib.sha256(record.data).hexdigest(),
                "shape": list(record.shape),
                "dtype": record.dtype,
                "seed": record.seed,
                "slot": record.slot,
                "bytes": len(record.data),
            }
        )
    metric = (
        _refuse("1", "selected_noise_record_missing", "missing")
        if reference is None or candidate is None
        else _metric(int(reference == candidate))
    )
    return _result(
        "exact-noise-identity", {"noise.identity": metric}, inputs=descriptors
    )


def noise_statistics(samples, *, sample_rate_hz, spectral=False):
    if type(spectral) is not bool:
        raise ValueError("spectral must be bool")
    x, checked = _samples(samples, sample_rate_hz)
    metrics = _absolute(checked, "noise")
    settings = {
        "sample_rate_hz": sample_rate_hz,
        "lags": list(LAGS),
        "variance": "population-N",
        "autocorrelation": "centered-biased-N",
        "correlation_minimum_samples": 64,
        "variance_floor": POWER_FLOOR,
    }
    keys = {
        "noise.mean": "amplitude",
        "noise.variance": "(amplitude)^2",
        **{f"noise.ac.{lag}": "1" for lag in LAGS},
    }
    if x is None:
        metrics.update(
            {k: _refuse(u, "noise_samples_missing", "missing") for k, u in keys.items()}
        )
    else:
        mean = math.fsum(x) / len(x)
        centered = [v - mean for v in x]
        denominator = math.fsum(v * v for v in centered)
        variance = denominator / len(x)
        metrics.update(
            {
                "noise.mean": _metric(mean, "amplitude"),
                "noise.variance": _metric(variance, "(amplitude)^2"),
            }
        )
        for lag in LAGS:
            metrics[f"noise.ac.{lag}"] = (
                _refuse("1", "insufficient_duration")
                if len(x) < 64
                else _refuse("1", "variance_at_or_below_floor")
                if variance <= POWER_FLOOR
                else _metric(
                    math.fsum(a * b for a, b in zip(centered, centered[lag:]))
                    / denominator
                )
            )
    if spectral:
        band = band_statistics(x, sample_rate_hz=sample_rate_hz)
        metrics.update(band["metrics"])
        settings["bands"] = band["settings"]
    result = _result("noise-statistics", metrics, settings, checked["inputs"])
    if spectral:
        result["implementation"]["numpy"] = require_numpy().__version__
    return result


def band_statistics(samples, *, sample_rate_hz):
    np = require_numpy()
    x, checked = _samples(samples, sample_rate_hz)
    paired = compare_paired(
        None if x is None else [0.0] * len(x),
        x,
        reference_rate_hz=sample_rate_hz,
        candidate_rate_hz=sample_rate_hz,
        unit="amplitude",
        spectral=True,
    )
    metrics = {}
    for name, low, high in BANDS:
        m = copy.deepcopy(paired["metrics"][f"band.{name}.error_power"])
        metrics[f"band.{name}.power"] = m
        count = (
            0
            if x is None
            else sum(
                low <= k * (sample_rate_hz / len(x))
                and (high is None or k * (sample_rate_hz / len(x)) < high)
                for k in range(len(x) // 2 + 1)
            )
        )
        metrics[f"band.{name}.density"] = (
            _metric(m["value"] / (count * sample_rate_hz / len(x)), "(amplitude)^2/Hz")
            if m["value"] is not None and count
            else _refuse("(amplitude)^2/Hz", m["reason"], m["status"])
        )
        metrics[f"band.{name}.log_power"] = (
            _metric(10 * math.log10(m["value"]), "dB")
            if m["value"] is not None and m["value"] > LOG_FLOOR
            else _refuse(
                "dB",
                "power_at_or_below_log_floor"
                if m["value"] is not None
                else m["reason"],
                "insufficient" if m["value"] is not None else m["status"],
            )
        )
    result = _result(
        "rectangular-band-power",
        metrics,
        {
            "sample_rate_hz": sample_rate_hz,
            "fft": paired["settings"]["fft"],
            "window": "rectangular",
            "fft_length": None if x is None else len(x),
            "hop": None if x is None else len(x),
            "coherent_gain": 1,
            "power_gain": 1,
            "log_floor": LOG_FLOOR,
            "bands_hz": paired["settings"]["bands_hz"],
        },
        checked["inputs"],
    )
    result["implementation"]["numpy"] = np.__version__
    return result


def spectrum(samples, *, sample_rate_hz, fundamental_band_hz):
    """Attribute resolved coherent components; refuse off-bin/ambiguous ratios."""
    np = require_numpy()
    x, checked = _samples(samples, sample_rate_hz)
    lo, hi = fundamental_band_hz
    if not (
        math.isfinite(lo) and math.isfinite(hi) and 0 < lo < hi < sample_rate_hz / 2
    ):
        raise ValueError("fundamental search interval must be inside (0, Nyquist)")
    bands = band_statistics(x, sample_rate_hz=sample_rate_hz)
    metrics = {**_absolute(checked), **bands["metrics"]}
    keys = {
        "harmonic.frequency": "Hz",
        **{f"harmonic.h{h}_ratio": "1" for h in range(2, 6)},
        "harmonic.folded_power": "(amplitude)^2",
        "harmonic.inharmonic_power": "(amplitude)^2",
    }
    reason, status, bins, peak_bin = None, "insufficient", [], None
    if x is None:
        reason, status = "spectral_samples_missing", "missing"
    elif len(x) < 128:
        reason = "insufficient_duration"
    else:
        power = abs(np.fft.rfft(x, norm="forward")) ** 2
        power[1:] *= 2
        if len(x) % 2 == 0:
            power[-1] /= 2
        search = [
            k for k in range(len(power)) if lo <= k * sample_rate_hz / len(x) < hi
        ]
        if len(search) < 5:
            reason = "insufficient_search_bins"
        else:
            peak_bin = max(search, key=lambda k: power[k])
            base = float(power[peak_bin])
            if base <= POWER_FLOOR:
                reason = "fundamental_at_or_below_floor"
            elif any(
                power[k] > base * COHERENCE_RATIO for k in search if k != peak_bin
            ):
                reason = "off_bin_or_ambiguous_fundamental"
            else:
                bins = [
                    min((h * peak_bin) % len(x), len(x) - (h * peak_bin) % len(x))
                    for h in range(1, 6)
                ]
                if len(set(bins)) != 5 or any(k == 0 or 2 * k == len(x) for k in bins):
                    reason = "ambiguous_folded_components"
                elif any(
                    power[j] > base * COHERENCE_RATIO
                    for k in bins
                    for j in (k - 1, k + 1)
                    if 0 <= j < len(power) and j not in bins
                ):
                    reason = "off_bin_or_nonstationary_components"
                else:
                    metrics["harmonic.frequency"] = _metric(
                        peak_bin * sample_rate_hz / len(x), "Hz"
                    )
                    for h, k in enumerate(bins[1:], 2):
                        metrics[f"harmonic.h{h}_ratio"] = _metric(
                            math.sqrt(float(power[k]) / base)
                        )
                    metrics["harmonic.folded_power"] = _metric(
                        math.fsum(
                            float(power[k])
                            for h, k in enumerate(bins, 1)
                            if 2 * h * peak_bin > len(x)
                        ),
                        "(amplitude)^2",
                    )
                    metrics["harmonic.inharmonic_power"] = _metric(
                        math.fsum(
                            float(power[k])
                            for k in range(1, len(power))
                            if k not in bins
                        ),
                        "(amplitude)^2",
                    )
    if reason:
        metrics.update({k: _refuse(u, reason, status) for k, u in keys.items()})
    result = _result(
        "coherent-harmonic-attribution",
        metrics,
        {
            **bands["settings"],
            "fundamental_band_hz": list(fundamental_band_hz),
            "harmonics": [1, 2, 3, 4, 5],
            "power_floor": POWER_FLOOR,
            "coherence_ratio": COHERENCE_RATIO,
            "minimum_samples": 128,
        },
        checked["inputs"],
        measured_peak_bin=peak_bin,
        assigned_harmonic_bins=bins,
    )
    result["implementation"]["numpy"] = np.__version__
    return result


def mix(inputs, output, *, sample_rate_hz, clip_bounds=(-1.0, 1.0)):
    """Measure original named inputs against a pre-normalization mixed output."""
    if (
        not isinstance(inputs, dict)
        or not inputs
        or any(not isinstance(k, str) or not k for k in inputs)
    ):
        raise ValueError("nonempty named input mapping required")
    y, checked = _samples(output, sample_rate_hz)
    channels = {
        name: _samples(values, sample_rate_hz)[0] for name, values in inputs.items()
    }
    metrics = _absolute(checked, "mix.output")
    keys = {
        f"mix.{name}.{field}": unit
        for name in inputs
        for field, unit in (("gain", "1"), ("gain_db", "dB"))
    }
    keys.update({"mix.dc_offset": "amplitude", "mix.residual_rms": "amplitude"})
    clip = compare_paired(
        output,
        None,
        reference_rate_hz=sample_rate_hz,
        candidate_rate_hz=sample_rate_hz,
        unit="amplitude",
        clip_bounds=clip_bounds,
    )
    metrics["mix.boundary_count"] = clip["metrics"]["reference.clip_count"]
    reason, status, condition = None, "insufficient", None
    if y is None or any(x is None for x in channels.values()):
        reason, status = "original_mix_seam_missing", "missing"
    elif any(len(x) != len(y) for x in channels.values()):
        reason = "frame_mismatch"
    elif len(y) < 64:
        reason = "insufficient_duration"
    elif any(
        math.fsum((v - math.fsum(x) / len(x)) ** 2 for v in x) / len(x) <= POWER_FLOOR
        for x in channels.values()
    ):
        reason = "input_variance_at_or_below_floor"
    else:
        np = require_numpy()
        design = np.column_stack([*channels.values(), np.ones(len(y))])
        scales = np.linalg.norm(design, axis=0)
        normalized_design = design / scales
        condition = float(np.linalg.cond(normalized_design))
        if (
            not math.isfinite(condition)
            or condition > 1e6
            or np.linalg.matrix_rank(normalized_design) != len(inputs) + 1
        ):
            reason = "ambiguous_or_ill_conditioned_inputs"
            condition = condition if math.isfinite(condition) else None
        else:
            coefficients = np.linalg.lstsq(normalized_design, y, rcond=None)[0] / scales
            residual = np.asarray(y) - design @ coefficients
            for name, coefficient in zip(inputs, coefficients[:-1]):
                gain = float(coefficient)
                metrics[f"mix.{name}.gain"] = _metric(gain)
                metrics[f"mix.{name}.gain_db"] = (
                    _metric(20 * math.log10(abs(gain)), "dB")
                    if abs(gain) > 1e-12
                    else _refuse("dB", "gain_at_or_below_log_floor")
                )
            metrics["mix.dc_offset"] = _metric(float(coefficients[-1]), "amplitude")
            metrics["mix.residual_rms"] = _metric(
                math.sqrt(math.fsum(float(v) ** 2 for v in residual) / len(y)),
                "amplitude",
            )
    if reason:
        metrics.update({k: _refuse(u, reason, status) for k, u in keys.items()})
    result = _result(
        "native-mixer-gain",
        metrics,
        {
            "sample_rate_hz": sample_rate_hz,
            "clip_bounds": list(clip_bounds),
            "inputs": list(inputs),
            "condition_limit": 1e6,
            "power_floor": POWER_FLOOR,
            "regression": "original-inputs-plus-intercept; no signal transformed",
        },
        {
            "output": checked["inputs"]["reference"],
            **{
                k: _samples(v, sample_rate_hz)[1]["inputs"]["reference"]
                for k, v in inputs.items()
            },
        },
        design_condition=condition,
    )
    if condition is not None:
        result["implementation"]["numpy"] = require_numpy().__version__
    return result


def round_dtype(value, dtype):
    if dtype == "float32":
        return struct.unpack("<f", struct.pack("<f", value))[0]
    if dtype == "float64":
        return float(value)
    raise ValueError("dtype must be float32 or float64")


def normalization(
    pre,
    post,
    *,
    sample_rate_hz,
    complete_samples,
    dtype="float32",
    reported_peak=None,
    reported_index=None,
    reported_applied=None,
    reported_gain=None,
):
    """Complete-clip observations and separate captured-decision metadata checks."""
    round_dtype(0, dtype)
    if type(complete_samples) is not int or complete_samples <= 0:
        raise ValueError("positive complete clip sample count required")
    if reported_applied is not None and type(reported_applied) is not bool:
        raise ValueError("reported_applied must be bool or missing")
    if reported_index is not None and (
        type(reported_index) is not int or reported_index < 0
    ):
        raise ValueError("reported_index must be a nonnegative integer")
    for value in (reported_peak, reported_gain):
        if value is not None and (
            type(value) not in (int, float) or not math.isfinite(value)
        ):
            raise ValueError("normalization metadata must be finite")
    x, checked = _samples(pre, sample_rate_hz)
    y, post_checked = _samples(post, sample_rate_hz)
    keys = {
        "peak_abs": "amplitude",
        "peak_value": "amplitude",
        "peak_index": "sample",
        "gain": "1",
        "rule_error_max": "amplitude",
        "decision_match": "1",
        "reported_peak_error": "amplitude",
        "reported_index_match": "1",
        "reciprocal_error": "1",
    }
    reason, status = None, "insufficient"
    if x is None or y is None:
        reason, status = "complete_pre_post_clip_missing", "missing"
    elif len(x) != complete_samples or len(y) != complete_samples:
        reason = "incomplete_clip"
    elif any(round_dtype(v, dtype) != v for values in (x, y) for v in values):
        reason = "sample_not_representable_in_declared_dtype"
    if reason:
        metrics = {
            f"normalization.{k}": _refuse(u, reason, status) for k, u in keys.items()
        }
    else:
        index = max(range(len(x)), key=lambda k: abs(x[k]))
        peak = abs(x[index])
        applied = peak > 1
        gain = 1 / peak if applied else 1.0
        residual = max(
            abs(b - (round_dtype(a / peak, dtype) if applied else a))
            for a, b in zip(x, y)
        )
        metrics = {
            "peak_abs": _metric(peak, "amplitude"),
            "peak_value": _metric(x[index], "amplitude"),
            "peak_index": _metric(index, "sample"),
            "gain": _metric(y[index] / x[index])
            if peak
            else _refuse("1", "silent_input_gain_unidentifiable"),
            "rule_error_max": _metric(residual, "amplitude"),
            "decision_match": _metric(int(reported_applied == applied))
            if reported_applied is not None
            else _refuse("1", "applied_decision_metadata_missing", "missing"),
            "reported_peak_error": _metric(reported_peak - peak, "amplitude")
            if reported_peak is not None
            else _refuse("amplitude", "peak_metadata_missing", "missing"),
            "reported_index_match": _metric(int(reported_index == index))
            if reported_index is not None
            else _refuse("1", "peak_index_metadata_missing", "missing"),
            "reciprocal_error": _metric(reported_gain - round_dtype(gain, dtype))
            if reported_gain is not None
            else _refuse("1", "reciprocal_metadata_missing", "missing"),
        }
        metrics = {f"normalization.{k}": v for k, v in metrics.items()}
    return _result(
        "complete-clip-normalization",
        metrics,
        {
            "sample_rate_hz": sample_rate_hz,
            "complete_samples": complete_samples,
            "dtype": dtype,
            "rule": "strict-peak>1",
            "peak_index": "first-absolute-maximum",
        },
        {
            "pre": checked["inputs"]["reference"],
            "post": post_checked["inputs"]["reference"],
        },
        captured_metadata={
            "peak": reported_peak,
            "index": reported_index,
            "applied": reported_applied,
            "gain": reported_gain,
        },
    )


def resolution_floor(property_name):
    """Preregistered conservative apparatus resolution, never a hardware limit."""
    if property_name in (
        "noise.identity",
        "normalization.peak_abs",
        "normalization.peak_value",
        "normalization.peak_index",
        "normalization.decision_match",
        "normalization.reported_peak_error",
        "normalization.reported_index_match",
        "normalization.reciprocal_error",
        "mix.boundary_count",
    ):
        return 0.0
    if property_name == "harmonic.frequency":
        return 1e-9
    return (
        1e-10
        if property_name.startswith(("harmonic.", "band."))
        or property_name.endswith("gain_db")
        else 1e-12
    )


def scorecard_rows(
    measurement, *, case_id, trace, limits, invariant_ok=True, evidence_scope="analytic"
):
    """Existing v1 serialization; unresolved/floor-limited claims fail closed."""
    result = copy.deepcopy(measurement)
    result["raw_metrics"] = copy.deepcopy(measurement["metrics"])
    for name, metric in result["metrics"].items():
        if metric["status"] != "valid":
            continue
        reason = (
            "production_qualification_not_established"
            if evidence_scope != "analytic"
            else "preparation_invariant_failed"
            if not invariant_ok
            else "requested_resolution_below_apparatus_floor"
            if name in limits and limits[name].tolerance < resolution_floor(name)
            else None
        )
        if reason:
            result["metrics"][name] = _refuse(metric["unit"], reason)
    result["settings"]["evidence_scope"] = evidence_scope
    result["settings"]["invariant_ok"] = invariant_ok
    rows, data = paired_rows(
        result,
        case_id=case_id,
        partition="development",
        trace=trace,
        rubric=Rubric(RUBRIC["id"], RUBRIC["version"], limits) if limits else None,
    )
    if not limits:
        # This family rubric explicitly permits missing limits as refusals.
        # The landed serializer's Rubric constructor requires at least one limit.
        record = json.loads(data)
        record["rubric"].update(RUBRIC)
        data = (
            json.dumps(record, sort_keys=True, indent=2, allow_nan=False) + "\n"
        ).encode()
        digest = hashlib.sha256(data).hexdigest()
        for row in rows:
            row["rubric"] = dict(RUBRIC)
            row["artifact"] = {"identity": f"pm1-{digest}", "sha256": digest}
            validate_row(row)
    return rows, data


def load_preparation(source=None):
    """Read-only optional sibling API loading; no fallback transform copy."""
    source = (
        Path(source)
        if source is not None
        else Path(__file__).with_name("preparation.py")
    )
    source_bytes = source.read_bytes()
    name = "torchsynth_voice._spectral_preparation_dependency"
    spec = importlib.util.spec_from_file_location(name, Path(source))
    if spec is None or spec.loader is None:
        raise ValueError("preparation source cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    # Hash and execute the same snapshot even while its owner is editing it.
    exec(compile(source_bytes, str(source), "exec"), module.__dict__)  # noqa: S102 - explicit trusted local module; bind executed bytes
    module._spectral_loaded_source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    return module


def prepare_pair(
    reference, candidate, *, sample_rate_hz, source=None, window=None, operations=()
):
    """Small #86 consumer: symmetric explicit property window or identity only."""
    preparation = load_preparation(source)
    spec = preparation.Preparation(
        estimator="spectral-noise-mix",
        estimator_version=VERSION,
        path="property" if window is not None else "exact",
        unit="amplitude",
        window=window,
        allowed=("window",) if window is not None else (),
        forbidden=tuple(
            t for t in preparation.TRANSFORMS if window is None or t != "window"
        ),
    )
    pair = preparation.prepare_pair(
        preparation.Signal(reference, sample_rate_hz, "amplitude"),
        preparation.Signal(candidate, sample_rate_hz, "amplitude"),
        spec,
        operations=operations,
    )
    for result in pair:
        result["dependency"] = {
            "version": preparation.VERSION,
            "source_sha256": preparation._spectral_loaded_source_sha256,
        }
    return pair


def estimate_prepared_pair(
    reference,
    candidate,
    *,
    estimator,
    sample_rate_hz,
    source=None,
    window=None,
    operations=(),
    **options,
):
    """No estimates are accepted after refusal; callers retain the preparation."""
    if estimator not in (spectrum, noise_statistics, band_statistics):
        raise ValueError(
            "adapter supports only explicitly windowable property estimators"
        )
    pair = prepare_pair(
        reference,
        candidate,
        sample_rate_hz=sample_rate_hz,
        source=source,
        window=window,
        operations=operations,
    )
    if any(p["status"] != "valid" for p in pair):
        return {"status": "refused", "preparation": pair, "measurements": None}
    measured = [
        estimator(p["prepared_samples"], sample_rate_hz=sample_rate_hz, **options)
        for p in pair
    ]
    for result, prepared in zip(measured, pair):
        result["preparation"] = prepared
        result["settings"].update(
            preparation="shared-declared-preparation",
            allowed_preparation=list(prepared["config"]["allowed"]),
            forbidden_preparation=list(prepared["config"]["forbidden"]),
            selected_interval=prepared["window_bounds"],
            time_origin="prepared-window-index-zero"
            if window is not None
            else "clip-index-zero",
        )
        result["settings"]["shared_preparation_config_sha256"] = prepared[
            "config_sha256"
        ]
    return {"status": "valid", "preparation": pair, "measurements": measured}
