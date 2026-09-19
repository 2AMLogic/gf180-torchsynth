"""Observed ADSR, stationary amplitude and isolated-route properties.

Analytic qualification only; see spec/ENVELOPE-ESTIMATORS.md. No truth duration
or route weight is an estimator input. No signal preparation is performed.
"""

from __future__ import annotations

import base64
import hashlib
import math
import struct
from collections.abc import Mapping
from numbers import Real

from .paired_metrics import Rubric, scorecard_rows

VERSION = "1"
FLOOR = 1e-7
MIN_STAGE_POINTS = 4
DESTINATIONS = ("vco_1_pitch", "vco_1_amp", "vco_2_pitch", "vco_2_amp", "noise_amp")
UNITS = {
    name: "semitone" if name.endswith("pitch") else "linear_gain"
    for name in DESTINATIONS
}
PREPARATION = {
    "allowed": ["identity"],
    "forbidden": ["align", "trim", "resample", "normalize", "rectify", "hilbert"],
    "onset": "declared trigger; no search",
    "window": "caller-fixed half-open observation",
    "qualification": "constructed development fixtures only",
}


def _number(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be real")  # noqa: TRY004 -- public invalid-input contract
    value = float(value)
    if not math.isfinite(value) or abs(value) > 1e20 or (positive and value <= 0):
        raise ValueError(f"{name} outside finite supported range")
    return value


def _trace(samples):
    if isinstance(samples, (str, bytes, Mapping)) or not hasattr(samples, "__len__"):
        raise ValueError("trace must be a one-dimensional finite sequence")
    values = [_number(x, "sample") for x in samples]
    data = b"".join(struct.pack("<d", x) for x in values)
    return values, {
        "samples": len(values),
        "binary64_le_sha256": hashlib.sha256(data).hexdigest(),
        "binary64_le_base64": base64.b64encode(data).decode("ascii"),
    }


def _metric(value, unit, reason="measured in declared analytic domain"):
    return {
        "value": value,
        "unit": unit,
        "status": "valid" if value is not None else "insufficient",
        "reason": reason,
        "coverage": "complete",
    }


def _result(method, settings, inputs, units, reason):
    return {
        "estimator": {"name": f"envelope-routes.{method}", "version": VERSION},
        "implementation": {"arithmetic": "python binary64", "version": VERSION},
        "settings": {**settings, "preparation": PREPARATION},
        "inputs": inputs,
        "metrics": {key: _metric(None, unit, reason) for key, unit in units.items()},
        "diagnostics": {},
    }


def _refuse(result, reason):
    for key, metric in result["metrics"].items():
        result["metrics"][key] = _metric(None, metric["unit"], reason)
    return result


def _context(rate_hz, origin_samples, preparation):
    rate = _number(rate_hz, "rate_hz", positive=True)
    origin = _number(origin_samples, "origin_samples")
    if abs(origin) > 2**40:
        raise ValueError("sample origin exceeds supported coordinate precision")
    settings = {
        "rate_hz": rate,
        "origin_samples": origin,
        "requested_preparation": preparation or "identity",
    }
    return rate, origin, settings


def _line(xs, ys):
    xmean, ymean = math.fsum(xs) / len(xs), math.fsum(ys) / len(ys)
    variance = math.fsum((x - xmean) ** 2 for x in xs)
    if variance == 0:
        return None
    slope = math.fsum((x - xmean) * (y - ymean) for x, y in zip(xs, ys)) / variance
    intercept = ymean - slope * xmean
    residual = max(abs(y - (slope * x + intercept)) for x, y in zip(xs, ys))
    return slope, intercept, residual


def estimate_envelope(
    samples,
    *,
    rate_hz,
    note_on_seconds,
    alpha,
    origin_samples=0,
    epsilon=1e-6,
    preparation="identity",
):
    """Infer continuous nominal boundaries from shaped segment regressions.

    Known note duration, alpha and epsilon describe the observation context.
    Durations/gain/sustain truth are deliberately absent from this API.
    """
    rate, origin, settings = _context(rate_hz, origin_samples, preparation)
    note_seconds = _number(note_on_seconds, "note_on_seconds", positive=True)
    note = note_seconds * rate
    alpha = _number(alpha, "alpha", positive=True)
    epsilon = _number(epsilon, "epsilon")
    if not 0.1 <= alpha <= 6 or not 0 <= epsilon <= 1e-6:
        raise ValueError("qualified alpha is [0.1,6], epsilon is [0,1e-6]")
    ys, info = _trace(samples)
    units = {k: "control_sample" for k in ("attack_end", "decay_end", "release_end")}
    units.update(
        peak_amplitude="1", sustain_amplitude="1", observed_peak_index="sample"
    )
    result = _result(
        "direct",
        {
            **settings,
            "note_on_seconds": note_seconds,
            "alpha": alpha,
            "epsilon": epsilon,
            "minimum_stage_points": MIN_STAGE_POINTS,
        },
        {"trace": info},
        units,
        "short_merged_or_unidentifiable_stage",
    )
    if preparation != "identity":
        return _refuse(result, "preparation_forbidden")
    if not ys or min(ys) < 0 or origin < 0:
        return _refuse(result, "nonnegative_isolated_envelope_required")
    peak = max(ys)
    if peak <= FLOOR:
        return _refuse(result, "amplitude_floor")
    xs = [origin + i for i in range(len(ys))]
    metrics, diagnostic = result["metrics"], result["diagnostics"]
    metrics["observed_peak_index"] = _metric(ys.index(peak), "sample")
    # After note-off the compressed attack/decay are complete. Linearizing the
    # release by its known exponent gives a zero crossing without knowing gain.
    indices = [i for i, (x, y) in enumerate(zip(xs, ys)) if x >= note and y > FLOOR]
    diagnostic["release_fit_points"] = len(indices)
    if len(indices) >= MIN_STAGE_POINTS:
        release = _line(
            [xs[i] for i in indices], [ys[i] ** (1 / alpha) for i in indices]
        )
        slope, intercept, residual = release
        diagnostic["release_root_fit"] = release
        if slope < 0 and residual <= peak ** (1 / alpha) * 1e-7:
            duration = (-intercept / slope - note + epsilon) / (1 - epsilon)
            end = note + duration
            if duration > 0 and any(x >= end and y == 0 for x, y in zip(xs, ys)):
                metrics["release_end"] = _metric(end, "control_sample")
            else:
                metrics["release_end"] = _metric(
                    None, "control_sample", "release_end_outside_observation"
                )
        else:
            metrics["release_end"] = _metric(
                None, "control_sample", "zero_release_or_non_adsr_shape"
            )
    # Find an observed plateau immediately before the known note-off. No
    # expected sustain/attack/decay parameter participates in segmentation.
    before = [i for i, x in enumerate(xs) if x < note]
    if len(before) < MIN_STAGE_POINTS or xs[-1] < note:
        return result
    stop = before[-1]
    start = stop
    while start > 0 and abs(ys[start - 1] - ys[stop]) <= 1e-14 * peak:
        start -= 1
    diagnostic["plateau_window"] = [start, stop + 1]
    if stop - start + 1 < MIN_STAGE_POINTS or ys[stop] <= FLOOR:
        return result
    plateau = math.fsum(ys[start : stop + 1]) / (stop - start + 1)
    metrics["sustain_amplitude"] = _metric(plateau, "1")
    k = max(before, key=lambda i: ys[i])
    rise = [i for i in range(k) if ys[i] > FLOOR]
    fall = [i for i in range(k + 1, start) if ys[i] - plateau > 1e-10 * peak]
    diagnostic.update(attack_fit_points=len(rise), decay_fit_points=len(fall))
    if min(len(rise), len(fall)) < MIN_STAGE_POINTS:
        return result
    ma, ba, ea = _line([xs[i] for i in rise], [ys[i] ** (1 / alpha) for i in rise])
    md, bd, ed = _line(
        [xs[i] for i in fall], [(ys[i] - plateau) ** (1 / alpha) for i in fall]
    )
    diagnostic.update(attack_root_fit=[ma, ba, ea], decay_root_fit=[md, bd, ed])
    if ma <= 0 or md >= 0 or max(ea, ed) > peak ** (1 / alpha) * 1e-7:
        return result
    # At the nominal attack duration a, the attack's unshaped scale is
    # (ma*a)**alpha. The decay's pre-start value includes its epsilon factor.
    lo, hi = 0.0, min(note, -bd / md)
    for _ in range(80):
        a = (lo + hi) / 2
        if (ma * a) ** alpha < plateau + max(0, md * a + bd) ** alpha:
            lo = a
        else:
            hi = a
    attack = (lo + hi) / 2
    decay = (-bd / md - attack + epsilon) / (1 - epsilon)
    diagnostic["inferred_attack_origin"] = epsilon * (1 + attack) - ba / ma
    if abs(diagnostic["inferred_attack_origin"]) > 0.02:
        for name in ("attack_end", "decay_end", "peak_amplitude"):
            metrics[name] = _metric(
                None, units[name], "attack_origin_or_shape_mismatch"
            )
        return result
    if attack > 0 and decay > 0 and attack + decay < note:
        metrics["attack_end"] = _metric(attack, "control_sample")
        metrics["decay_end"] = _metric(attack + decay, "control_sample")
        metrics["peak_amplitude"] = _metric((ma * attack) ** alpha, "1")
    return result


def _solve(matrix, rhs):
    """Small pivoted 3x3 normal system for sine/cosine/DC fitting."""
    rows = [list(row) + [value] for row, value in zip(matrix, rhs)]
    for column in range(3):
        pivot = max(range(column, 3), key=lambda i: abs(rows[i][column]))
        rows[column], rows[pivot] = rows[pivot], rows[column]
        divisor = rows[column][column]
        if abs(divisor) < 1e-12:
            return None
        rows[column] = [x / divisor for x in rows[column]]
        for i in range(3):
            if i != column:
                factor = rows[i][column]
                rows[i] = [x - factor * y for x, y in zip(rows[i], rows[column])]
    return [row[-1] for row in rows]


def tone_amplitude(
    samples, *, rate_hz, frequency_hz, origin_samples=0, preparation="identity"
):
    """Peak amplitude of an isolated stationary sinusoid; unknown phase/DC."""
    rate, origin, settings = _context(rate_hz, origin_samples, preparation)
    frequency = _number(frequency_hz, "frequency_hz", positive=True)
    ys, info = _trace(samples)
    result = _result(
        "tone",
        {**settings, "frequency_hz": frequency},
        {"trace": info},
        {"amplitude": "1"},
        "carrier_or_window_outside_domain",
    )
    if preparation != "identity":
        return _refuse(result, "preparation_forbidden")
    if len(ys) < 32 or len(ys) * frequency / rate < 4 or frequency > rate / 4:
        return result
    basis = [
        [
            math.sin(2 * math.pi * frequency * (i + origin) / rate),
            math.cos(2 * math.pi * frequency * (i + origin) / rate),
            1.0,
        ]
        for i in range(len(ys))
    ]
    matrix = [
        [math.fsum(row[j] * row[k] for row in basis) for k in range(3)]
        for j in range(3)
    ]
    rhs = [math.fsum(row[j] * y for row, y in zip(basis, ys)) for j in range(3)]
    coefficients = _solve(matrix, rhs)
    if coefficients is None:
        return result
    amplitude = math.hypot(*coefficients[:2])
    residual = math.sqrt(
        math.fsum(
            (y - math.fsum(c * b for c, b in zip(coefficients, row))) ** 2
            for row, y in zip(basis, ys)
        )
        / len(ys)
    )
    result["diagnostics"].update(coefficients=coefficients, residual_rms=residual)
    if amplitude <= FLOOR:
        return _refuse(result, "amplitude_floor")
    if residual > amplitude * 1e-6:
        return _refuse(result, "nonstationary_or_non_sinusoidal_window")
    result["metrics"]["amplitude"] = _metric(amplitude, "1")
    return result


def broadband_amplitude(
    samples, *, rate_hz, source_law, origin_samples=0, preparation="identity"
):
    """Stationary ensemble gain for declared IID N(0,1) noise; not a peak."""
    _, _, settings = _context(rate_hz, origin_samples, preparation)
    ys, info = _trace(samples)
    result = _result(
        "broadband",
        {**settings, "source_law": source_law},
        {"trace": info},
        {"amplitude": "1"},
        "broadband_domain_or_window_unqualified",
    )
    if preparation != "identity":
        return _refuse(result, "preparation_forbidden")
    if source_law != "iid_gaussian_unit_variance" or len(ys) < 8192:
        return result
    rms = math.sqrt(math.fsum(y * y for y in ys) / len(ys))
    if rms <= FLOOR:
        return _refuse(result, "amplitude_floor")
    blocks = [ys[len(ys) * i // 4 : len(ys) * (i + 1) // 4] for i in range(4)]
    block_rms = [
        math.sqrt(math.fsum(y * y for y in block) / len(block)) for block in blocks
    ]
    mean = math.fsum(ys) / len(ys)
    result["diagnostics"].update(raw_rms=rms, block_rms=block_rms, mean=mean)
    if max(abs(x / rms - 1) for x in block_rms) > 0.12 or abs(mean) > rms * 0.06:
        return _refuse(result, "nonstationary_or_nonzero_mean_broadband")
    result["metrics"]["amplitude"] = _metric(rms, "1")
    return result


def estimate_routes(
    source,
    outputs,
    *,
    rate_hz,
    physical_outputs=None,
    origin_samples=0,
    preparation="identity",
):
    """Measure all named matrix gains and optional downstream physical depths.

    No claimed route label or expected weight is accepted. Destination swaps
    are visible in the measured vector. Physical outputs must already be in
    destination units, not audio-derived pitch or inferred VCA amplitude.
    """
    _, _, settings = _context(rate_hz, origin_samples, preparation)
    if not isinstance(outputs, Mapping) or set(outputs) != set(DESTINATIONS):
        raise ValueError("all five named matrix destinations are required")
    if physical_outputs is not None and set(physical_outputs) != set(DESTINATIONS):
        raise ValueError("all five named physical destinations are required")
    xs, source_info = _trace(source)
    traces, inputs, units = {}, {"source": source_info}, {}
    for domain, mapping in (("gain", outputs), ("depth", physical_outputs or {})):
        for destination, samples in mapping.items():
            name = f"{domain}.{destination}"
            traces[name], inputs[name] = _trace(samples)
            units[name] = "1" if domain == "gain" else UNITS[destination]
            if len(traces[name]) != len(xs):
                raise ValueError("route traces must share sample count and origin")
    result = _result("route", settings, inputs, units, "source_excitation_floor")
    if preparation != "identity":
        return _refuse(result, "preparation_forbidden")
    if len(xs) < 8 or max(xs) - min(xs) <= FLOOR:
        return result
    for name, ys in traces.items():
        slope, intercept, residual = _line(xs, ys)
        result["diagnostics"][name] = {"intercept": intercept, "residual_max": residual}
        if residual <= max(FLOOR, max(abs(y) for y in ys) * 1e-7):
            result["metrics"][name] = _metric(slope, units[name])
        else:
            result["metrics"][name] = _metric(
                None, units[name], "nonlinear_or_wrong_source_response"
            )
    return result


def qualification_rows(result, *, case_id, limits):
    """Use the existing strict scorecard adapter, preserving floor refusals."""
    import copy

    result = copy.deepcopy(result)
    result["metrics"] = dict(sorted(result["metrics"].items()))
    method = result["estimator"]["name"].split(".")[-1]
    for name, limit in limits.items():
        metric = result["metrics"][name]
        resolution = (
            0.02
            if metric["unit"] == "control_sample"
            else 2e-5
            if method == "direct"
            else 0.05 * abs(limit.expected)
            if method == "broadband"
            else 1e-8
            if method == "route" and metric["unit"] == "1"
            else 1e-7
        )
        if metric["value"] is not None and limit.tolerance < resolution:
            result["metrics"][name] = _metric(
                None, metric["unit"], "requested_limit_below_qualified_resolution"
            )
    return scorecard_rows(
        result,
        case_id=case_id,
        partition="development",
        trace=result["estimator"]["name"],
        rubric=Rubric("envelope-routes-analytic", VERSION, limits),
    )


def preparation_adapter(
    traces, *, rate_hz, measure, units, origin_samples=0, operations=(), backend=None
):
    """Small #86 adapter: shared identity preparation, then estimator and gate.

    `measure` consumes a name-keyed dictionary of prepared traces. No transforms
    are reimplemented here. Fractional origins remain local analytic context;
    the current shared Signal contract requires integer clip origins.
    """
    if backend is None:
        from . import preparation as backend
    spec_type, signal_type = backend.Preparation, backend.Signal
    records = {}
    for name, samples in traces.items():
        spec = spec_type(
            estimator="envelope-routes.identity",
            estimator_version=VERSION,
            path="property",
            unit=units[name],
        )
        records[name] = backend.prepare(
            signal_type(
                samples, rate_hz, units[name], time_origin_samples=origin_samples
            ),
            spec,
            operations=operations,
        )
    valid = all(record["status"] == "valid" for record in records.values())
    result = measure(
        {name: record["prepared_samples"] for name, record in records.items()}
        if valid
        else traces
    )
    result["shared_preparation"] = records
    result["settings"]["shared_preparation_version"] = backend.VERSION
    result["settings"]["shared_preparation_configs"] = {
        name: record["config_sha256"] for name, record in records.items()
    }
    if not valid:
        return _refuse(
            result,
            "shared_preparation_refused: "
            + "; ".join(
                record["reason"]
                for record in records.values()
                if record["status"] != "valid"
            ),
        )
    return result
