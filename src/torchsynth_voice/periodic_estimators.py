"""Bounded analytic periodic measurements, not a general pitch detector.

See spec/PERIODIC-ESTIMATORS.md. Expected truth never enters the estimator.
NumPy is an explicit optional numerical dependency; import has no Torch effect.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from fractions import Fraction

from .scorecard import validate_row

VERSION = "periodic-v2"
RUBRIC = {"id": "periodic-analytic-only", "version": "2"}
FORBIDDEN = (
    "alignment",
    "trimming",
    "padding",
    "resampling",
    "normalization",
    "detrending",
    "filtering",
    "gain_fitting",
)
PROPERTIES = {
    "frequency_hz": "Hz",
    "cents": "cent",
    "phase_rad": "rad",
    "timing_samples": "sample",
    "depth_peak_to_peak": "native",
}


def canonical_bytes(value):
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def wrap_phase(value):
    return (value + math.pi) % math.tau - math.pi


def identity_metadata(sample_count, sample_rate_hz, unit, *, time_origin=0.0):
    """Describe supplied analytic samples. Does not transform or qualify a trace."""
    return {
        "version": "periodic-analytic-identity-v1",
        "scope": "analytic",
        "status": "valid",
        "reason": "constructed_analytic_identity",
        "sample_rate_hz": sample_rate_hz,
        "unit": unit,
        "time_origin": time_origin,
        "window": [0, sample_count],
        "transforms": [],
        "allowed": ["identity"],
        "forbidden": list(FORBIDDEN),
        "boundary": "no_extension",
        "invariants_passed": True,
    }


def _finite(value):
    return (
        type(value) in (int, float)
        and abs(value) <= sys.float_info.max
        and math.isfinite(value)
    )


def _refuse(result, reason):
    result["status"], result["reason"] = "invalid", reason
    result["estimates"] = dict.fromkeys(PROPERTIES)
    result["property_reasons"] = dict.fromkeys(PROPERTIES, reason)
    return result


def _shape(np, phase, weights):
    """Model used for residual validation, never fixture truth generation."""
    cycle = np.remainder(phase / math.tau, 1.0)
    shapes = (
        (1 - np.cos(phase)) / 2,
        1 - np.abs(2 * cycle - 1),
        cycle,
        1 - cycle,
        (np.sign(-np.cos(phase)) + 1) / 2,
    )
    return sum(w * s for w, s in zip(weights, shapes))


def _shape_range(weights):
    """Piecewise extrema candidates with 1e-12-cycle one-sided edge probes."""
    points = [0.0, 0.25, 0.5, 0.75, 1.0]
    sine, tri, saw, rsaw, _ = weights
    if sine:
        for low, high in ((0, 0.5), (0.5, 1)):
            slope = (2 * tri if low == 0 else -2 * tri) + saw - rsaw
            q = -slope / (math.pi * sine)
            if abs(q) <= 1:
                a = math.asin(q) / math.tau
                for root in (a % 1, (0.5 - a) % 1):
                    if low < root < high:
                        points.append(root)
    values = []
    for t in points:
        for side in (-1e-12, 1e-12):
            u = (t + side) % 1
            values.append(
                sine * (1 - math.cos(math.tau * u)) / 2
                + tri * (1 - abs(2 * u - 1))
                + saw * u
                + rsaw * (1 - u)
                + weights[4] * float(0.25 < u < 0.75)
            )
    return max(values) - min(values)


def _sine(np, x, rate, family):
    # The second-order recurrence estimates frequency independently of the
    # declared reference. An intercept allows raw unipolar LFO samples.
    middle = x[1:-1]
    # Form the small curvature directly. Computing cos(w) as a ratio near 1
    # and then acos loses low-frequency precision in backend-dependent sums.
    curvature = (x[:-2] - middle) + (x[2:] - middle)
    centered = middle - np.mean(middle)
    half_sine_squared = float(
        -np.dot(centered, curvature - np.mean(curvature))
        / (4 * np.dot(centered, centered))
    )
    if not 0 < half_sine_squared < 1:
        return None
    frequency = math.asin(math.sqrt(half_sine_squared)) * rate / math.pi
    angle = math.tau * frequency * np.arange(len(x)) / rate
    design = np.column_stack((np.cos(angle), np.sin(angle), np.ones(len(x))))
    a, b, dc = (float(v) for v in np.linalg.lstsq(design, x, rcond=None)[0])
    amplitude = math.hypot(a, b)
    if amplitude == 0:
        return None
    phase = math.atan2(-b, a) if family == "oscillator" else math.atan2(b, -a)
    predicted = design @ np.array([a, b, dc])
    residual = float(np.sqrt(np.mean((x - predicted) ** 2))) / amplitude
    dc_error = abs(dc if family == "oscillator" else dc - amplitude) / amplitude
    return (
        frequency,
        phase,
        2 * amplitude,
        max(residual, dc_error),
        {"dc": dc, "relative_rms_residual": residual, "relative_dc_error": dc_error},
    )


def _directed(np, x, rate, weights):
    """Crossing regression followed by diagnostic known-shape consistency fit.

    Non-sine phase is not accepted. The fit estimates absolute depth; it never
    changes input samples or substitutes a fitted signal into paired metrics.
    """
    level = (float(np.max(x)) + float(np.min(x))) / 2
    if weights[4] == 1:
        return _square(np, x, rate, level)
    candidates = []
    for rising in (True, False):
        mask = (
            (x[:-1] <= level) & (x[1:] > level)
            if rising
            else (x[:-1] >= level) & (x[1:] < level)
        )
        edges = np.flatnonzero(mask)
        if len(edges) < 4:
            continue
        crossings = edges + (level - x[edges]) / (x[edges + 1] - x[edges])
        k = np.arange(len(crossings), dtype=float)
        kc = k - np.mean(k)
        period = float(np.dot(kc, crossings - np.mean(crossings)) / np.dot(kc, kc))
        spacing_error = float(np.max(np.abs(np.diff(crossings) - period)))
        candidates.append((spacing_error, rate / period, len(edges)))
    if not candidates:
        return None
    spacing_error, frequency, count = min(candidates)
    if spacing_error > 2.0:
        return None
    t = np.arange(len(x)) / rate

    def fit(f, phase):
        model = _shape(np, math.tau * f * t + phase, weights)
        denominator = float(np.dot(model, model))
        gain = float(np.dot(model, x)) / denominator if denominator else 0
        error = float(np.mean((x - gain * model) ** 2))
        return error, gain

    phase = min(
        (i * math.tau / 128 for i in range(128)), key=lambda p: fit(frequency, p)[0]
    )
    # Refine phase before allowing frequency changes: otherwise a coarse phase
    # grid can bias a perfectly measured continuous-edge frequency.
    low, high = phase - math.tau / 128, phase + math.tau / 128
    for _ in range(48):
        left, right = low + (high - low) / 3, high - (high - low) / 3
        if fit(frequency, left)[0] < fit(frequency, right)[0]:
            high = right
        else:
            low = left
    phase = (low + high) / 2
    best, gain = fit(frequency, phase)
    # Piecewise derivatives improve the diagnostic template fit. Large edge
    # residuals do not drive the local derivative; ALL samples still enter
    # every candidate's loss and the final refusal gate.
    for _ in range(16):
        angle = math.tau * frequency * t + phase
        cycle = np.remainder(angle / math.tau, 1)
        model = _shape(np, angle, weights)
        derivative = (
            weights[0] * np.sin(angle) / 2
            + (weights[1] * np.where(cycle < 0.5, 2, -2) + weights[2] - weights[3])
            / math.tau
        )
        residuals = x - gain * model
        mask = np.abs(residuals) < 0.2 * max(float(np.ptp(x)), 1e-12)
        jacobian = np.column_stack(
            (model, gain * derivative, gain * derivative * math.tau * t)
        )
        _dg, dp, df = np.linalg.lstsq(jacobian[mask], residuals[mask], rcond=None)[0]
        improved = False
        for factor in (1.0, 0.5, 0.25, 0.125):
            trial, trial_gain = fit(frequency + factor * df, phase + factor * dp)
            if trial < best:
                frequency, phase, best, gain = (
                    frequency + factor * float(df),
                    phase + factor * float(dp),
                    trial,
                    trial_gain,
                )
                improved = True
                break
        if not improved:
            break
    excursion = _shape_range(weights)
    depth = gain * excursion
    if depth <= 0:
        return None
    residual = math.sqrt(best) / depth
    return (
        frequency,
        phase,
        depth,
        residual,
        {
            "relative_rms_residual": residual,
            "crossings": count,
            "crossing_spacing_error_samples": spacing_error,
            "diagnostic_template_gain": gain,
            "diagnostic_template_phase": phase,
        },
    )


def _square(np, x, rate, level):
    """Intersect all transition-time intervals; never invent sub-sample edges."""
    high = x > level
    edges = np.flatnonzero(high[:-1] != high[1:])
    if len(edges) < 7:
        return None
    low_period, high_period = 0.0, math.inf
    for j in range(1, len(edges)):
        delta = edges[j] - edges[:j]
        steps = j - np.arange(j)
        low_period = max(low_period, float(np.max((delta - 1) / steps)))
        high_period = min(high_period, float(np.min((delta + 1) / steps)))
    if low_period >= high_period or low_period <= 0:
        return None
    half_period = (low_period + high_period) / 2
    k = np.arange(len(edges))
    origin_low = float(np.max(edges - k * half_period))
    origin_high = float(np.min(edges + 1 - k * half_period))
    if origin_low >= origin_high:
        return None
    gain = float(np.mean(x[high]))
    if gain <= 0:
        return None
    residual = float(np.sqrt(np.mean((x - high * gain) ** 2))) / gain
    phase = math.pi / 2 if high[edges[0] + 1] else 3 * math.pi / 2
    frequency = rate / (2 * half_period)
    phase -= math.tau * frequency * (origin_low + origin_high) / (2 * rate)
    return (
        frequency,
        phase,
        gain,
        residual,
        {
            "relative_rms_residual": residual,
            "transition_count": len(edges),
            "frequency_interval_hz": [
                rate / (2 * high_period),
                rate / (2 * low_period),
            ],
            "phase_identifiability": "interval_only_no_phase_verdict",
        },
    )


def range_key(result, *, frequency=None, depth=None):
    config = result["config"]
    frequency = result["estimates"]["frequency_hz"] if frequency is None else frequency
    depth = result["estimates"]["depth_peak_to_peak"] if depth is None else depth
    if frequency is None or depth is None:
        return None
    band = "low" if frequency < 20 else "mid" if frequency < 2000 else "high"
    gain_band = "near_silence" if depth < 1e-4 else "ordinary"
    return ":".join(
        (
            config["family"],
            digest(config["weights"])[:12],
            band,
            str(config["sample_rate_hz"]),
            str(config["sample_count"]),
            gain_band,
            config["unit"],
            str(config["reference_hz"]),
            str(config["time_origin"]),
        )
    )


def estimate_periodic(
    samples,
    *,
    sample_rate_hz,
    unit,
    family,
    reference_hz,
    preparation=None,
    time_origin=0.0,
    weights=(1, 0, 0, 0, 0),
    fundamental_in_band=False,
    envelope_shaped=False,
):
    """Measure a stationary cosine/raw LFO, or explicitly refuse its domain.

    fundamental_in_band is an external provenance assertion, not a samples-only test.
    Analytic identity scope is the only qualified preparation in this version.
    """
    if not _finite(sample_rate_hz) or sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be finite and positive")
    if not _finite(reference_hz) or reference_hz <= 0:
        raise ValueError("reference_hz must be explicit, finite and positive")
    if not _finite(time_origin) or not 0 <= time_origin <= 4:
        raise ValueError("time_origin must be in the analytic [0,4] second domain")
    if (
        family not in ("oscillator", "lfo")
        or not isinstance(unit, str)
        or not unit.strip()
    ):
        raise ValueError("explicit oscillator/lfo family and native unit required")
    if len(weights) != 5 or any(not _finite(w) or not 0 <= w <= 1 for w in weights):
        raise ValueError("five continuous waveform weights in [0,1] required")
    result = {
        "algorithm": VERSION,
        "status": "valid",
        "reason": "analytic_stationary_model",
        "config": {
            "sample_rate_hz": sample_rate_hz,
            "unit": unit,
            "family": family,
            "reference_hz": reference_hz,
            "time_origin": time_origin,
            "weights": list(weights),
            "fundamental_in_band": fundamental_in_band,
            "envelope_shaped": envelope_shaped,
            "sample_count": None,
        },
        "preparation": preparation,
        "estimates": dict.fromkeys(PROPERTIES),
        "property_reasons": {},
        "diagnostics": {},
        "input_sha256": None,
        "range_key": None,
    }
    try:
        import numpy as np
    except ImportError:
        return _refuse(result, "numpy_unavailable_install_metrics_extra")
    result["backend"] = {"numpy": np.__version__, "dtype": "binary64"}
    if samples is None:
        return _refuse(result, "samples_missing")
    if isinstance(samples, (list, tuple)) and any(
        isinstance(value, bool) for value in samples
    ):
        raise ValueError("boolean samples are not amplitudes")
    array = np.asarray(samples)
    if array.ndim != 1 or array.dtype.kind not in "fiu" or array.dtype.itemsize > 8:
        raise ValueError("one dimensional real samples of at most 64 bits required")
    if array.dtype.kind in "iu" and any(abs(int(v)) > 2**53 for v in array):
        raise ValueError("integer sample exceeds exact binary64 conversion")
    x = array.astype(np.float64)
    if not np.all(np.isfinite(x)):
        raise ValueError("finite samples required")
    result["input_sha256"] = hashlib.sha256(x.astype("<f8").tobytes()).hexdigest()
    result["config"]["sample_count"] = len(x)
    expected_preparation = identity_metadata(
        len(x), sample_rate_hz, unit, time_origin=time_origin
    )
    if preparation != expected_preparation:
        return _refuse(result, "preparation_missing_failed_or_unqualified")
    if fundamental_in_band is not True:
        return _refuse(
            result, "fundamental_in_band_provenance_required_aliases_unidentifiable"
        )
    if sample_rate_hz != (44100 if family == "oscillator" else 441):
        return _refuse(result, "sample_rate_outside_qualified_product_profile")
    if envelope_shaped:
        return _refuse(result, "envelope_shaped_lfo_not_stationary_raw_domain")
    if len(x) < 16 or len(x) > 4 * sample_rate_hz:
        return _refuse(result, "length_outside_16_samples_to_four_seconds")
    excursion = float(np.max(x) - np.min(x))
    if excursion <= 2**-30:
        return _refuse(result, "silence_or_below_declared_amplitude_floor")
    if max(abs(float(np.min(x))), abs(float(np.max(x)))) > 4:
        return _refuse(result, "amplitude_outside_analytic_domain")
    total = sum(w**2.718281828 for w in weights)
    if total == 0:
        return _refuse(result, "zero_waveform_weight_denominator")
    normalized = tuple(w**2.718281828 / total for w in weights)
    sine = normalized[0] == 1
    if family == "oscillator" and not sine:
        return _refuse(result, "SquareSawVCO_shape_model_unqualified")
    solution = (
        _sine(np, x, sample_rate_hz, family)
        if sine
        else _directed(np, x, sample_rate_hz, normalized)
    )
    if solution is None:
        return _refuse(result, "insufficient_stable_crossings_or_degenerate_recurrence")
    frequency, phase, depth, residual, diagnostics = solution
    result["diagnostics"] = diagnostics
    result["diagnostics"]["candidate_frequency_hz"] = frequency
    result["diagnostics"]["candidate_phase_at_first_sample_rad"] = phase
    result["diagnostics"]["candidate_depth_peak_to_peak"] = depth
    # Fixed before qualification; no automatic relaxation on a failed fixture.
    residual_bound = 1e-7 if sine else 1e-4
    if not math.isfinite(residual) or residual > residual_bound:
        return _refuse(
            result, "waveform_mixture_modulation_noise_clipping_or_transient"
        )
    if (
        not 0 < frequency < 0.45 * sample_rate_hz
        or (family == "lfo" and frequency > 40.00001)
        or (family == "oscillator" and frequency < 440 * 2 ** ((-24 - 69) / 12) - 1e-7)
    ):
        return _refuse(result, "frequency_outside_declared_domain")
    if frequency * (len(x) - 1) / sample_rate_hz < 3:
        return _refuse(result, "insufficient_cycles_minimum_three")
    phase = wrap_phase(phase - math.tau * frequency * time_origin)
    result["estimates"] = {
        "frequency_hz": frequency,
        "cents": 1200 * (math.log2(frequency) - math.log2(reference_hz)),
        "phase_rad": phase if sine else None,
        "timing_samples": (-phase / math.tau) * (sample_rate_hz / reference_hz)
        if sine
        else None,
        "depth_peak_to_peak": depth,
    }
    for name, value in result["estimates"].items():
        if value is not None and not math.isfinite(value):
            result["estimates"][name] = None
    result["property_reasons"] = {
        name: "measured" if value is not None else "non_sine_source_phase_unqualified"
        for name, value in result["estimates"].items()
    }
    result["range_key"] = range_key(result)
    return result


def property_error(name, observed, expected, reference_hz, rate):
    if name == "phase_rad":
        return abs(wrap_phase(observed - expected))
    if name == "timing_samples":
        period = rate / reference_hz
        return abs((observed - expected + period / 2) % period - period / 2)
    return abs(observed - expected)


def estimate_prepared_pair(
    reference,
    candidate,
    *,
    family,
    reference_hz,
    weights=(1, 0, 0, 0, 0),
    window=None,
    analytic=False,
    fundamental_in_band=False,
    preparation_api=None,
):
    """Small #86 consumer: shared code alone prepares both sides.

    The optional API argument supports a read-only sibling-worktree integration
    probe. It does not install/copy the sibling module. Production qualification
    remains refused; analytic=True must describe constructed analytic inputs.
    """
    if preparation_api is None:
        try:
            from . import preparation as preparation_api
        except ImportError as error:
            raise RuntimeError("preparation integration pending #86") from error
    api = preparation_api
    spec = api.Preparation(
        estimator=VERSION,
        estimator_version="1",
        path="property" if window else "exact",
        unit=reference.unit,
        window=window,
        allowed=("window",) if window else (),
        forbidden=tuple(t for t in api.TRANSFORMS if not window or t != "window"),
        refusal_conditions=(
            "invalid_input",
            "forbidden_transform",
            "unavailable_window",
            "nonstationary",
            "insufficient_cycles",
            "unknown_alias_provenance",
        ),
    )
    pair = api.prepare_pair(reference, candidate, spec)
    measurements = []
    for prepared in pair:
        usable = prepared["status"] == "valid" and not prepared["diagnostic_only"]
        samples = prepared["prepared_samples"] if usable else None
        rate = prepared["input"]["sample_rate_hz"]
        origin = (
            (
                (
                    prepared["input"]["time_origin_samples"]
                    + prepared["window_bounds"][0]
                )
                / rate
            )
            if usable
            else 0
        )
        metadata = (
            identity_metadata(len(samples), rate, spec.unit, time_origin=origin)
            if usable and analytic
            else None
        )
        result = estimate_periodic(
            samples,
            sample_rate_hz=rate,
            unit=spec.unit,
            family=family,
            reference_hz=reference_hz,
            weights=weights,
            time_origin=origin,
            preparation=metadata,
            fundamental_in_band=fundamental_in_band,
        )
        result["shared_preparation"] = {
            "version": prepared["version"],
            "config_sha256": prepared["config_sha256"],
            "prepared_sha256": prepared["prepared_sha256"],
            "record_sha256": hashlib.sha256(api.record_bytes(prepared)).hexdigest(),
            "scope": "analytic" if analytic else "production_integration_pending",
        }
        measurements.append(result)
    return {"preparation": pair, "measurements": measurements}


def score_periodic(
    measurement,
    *,
    case_id,
    expected=None,
    limits=None,
    qualification=None,
    truth_source="unavailable: analytic truth not supplied",
    limit_source="unavailable: analytic limits not supplied",
):
    """Emit strict rows and their exact diagnostic bytes; never inflate limits.

    Qualification cells are producer assertions backed by the retained raw
    qualification table. No built-in fidelity/release rubric is supplied.
    """
    expected, limits = expected or {}, limits or {}
    for mapping in (expected, limits):
        if set(mapping) - set(PROPERTIES):
            raise ValueError("unknown periodic property")
        if any(not _finite(v) for v in mapping.values()):
            raise ValueError("finite truth/limits required")
    if any(v < 0 for v in limits.values()):
        raise ValueError("limits must be nonnegative")
    payload = {
        "measurement": measurement,
        "case_id": case_id,
        "expected": expected,
        "limits": limits,
        "qualification": qualification,
        "truth_source": truth_source,
        "limit_source": limit_source,
    }
    record = canonical_bytes(payload)
    sha = hashlib.sha256(record).hexdigest()
    config = measurement["config"]
    preparation_valid = measurement["preparation"] == identity_metadata(
        config["sample_count"],
        config["sample_rate_hz"],
        config["unit"],
        time_origin=config["time_origin"],
    )
    rows = []
    for name, unit in PROPERTIES.items():
        observed = measurement["estimates"][name]
        reason = measurement["property_reasons"].get(name, measurement["reason"])
        q = qualification or {}
        floor = q.get("guard_floors", {}).get(name)
        if observed is not None:
            if not preparation_valid or measurement["status"] != "valid":
                reason = "preparation_or_measurement_invalid"
            elif (
                name not in expected
                or name not in limits
                or truth_source.startswith("unavailable:")
                or limit_source.startswith("unavailable:")
            ):
                reason = "truth_or_limit_unavailable"
            elif (
                q.get("algorithm") != measurement["algorithm"]
                or q.get("range_key") != measurement["range_key"]
                or q.get("qualified") is not True
                or not _finite(floor)
                or floor < 0
            ):
                reason = "range_qualification_or_floor_unavailable"
            elif Fraction(limits[name]) <= 4 * Fraction(floor):
                reason = "required_resolution_at_or_below_four_times_floor"
            else:
                reason = "analytic_range_qualified"
            if reason != "analytic_range_qualified":
                observed = None
        verdict = "NO VERDICT"
        if observed is not None:
            error = property_error(
                name,
                observed,
                expected[name],
                config["reference_hz"],
                config["sample_rate_hz"],
            )
            exact_error = (
                Fraction(error)
                if name in ("phase_rad", "timing_samples")
                else abs(Fraction(observed) - Fraction(expected[name]))
            )
            verdict = "PASS" if exact_error <= Fraction(limits[name]) else "FAIL"
        row = {
            "schema_version": 1,
            "case": {"id": case_id, "partition": "development"},
            "trace": "analytic." + config["family"],
            "property": name,
            "estimator": {
                "name": VERSION,
                "version": digest(
                    {
                        "algorithm": measurement["algorithm"],
                        "config": config,
                        "backend": measurement.get("backend"),
                    }
                ),
            },
            "rubric": RUBRIC.copy(),
            "unit": config["unit"] if unit == "native" else unit,
            "expected": {"value": expected.get(name), "source": truth_source},
            "observed": observed,
            "tolerance": {"value": limits.get(name), "source": limit_source},
            "validity": {
                "status": "valid" if observed is not None else "insufficient",
                "reason": reason,
            },
            "coverage": "complete" if measurement["input_sha256"] else "none",
            "verdict": verdict,
            "artifact": {"identity": "periodic1-" + sha, "sha256": sha},
        }
        validate_row(row)
        rows.append(row)
    return rows, record
