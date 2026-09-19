"""Shared signal preparation and fail-closed apparatus controls (v1).

No estimator algorithms or acceptance tolerances live here. See
spec/SIGNAL-PREPARATION.md for the conditional diagnostic interpolation bound.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import struct
import sys
from dataclasses import asdict, dataclass, replace
from fractions import Fraction
from numbers import Integral, Real

from .paired_metrics import artifact_reference, compare_paired
from .identity import SoundIdentity
from .scorecard import validate_row

VERSION = "preparation-v1"
TRANSFORMS = (
    "alignment",
    "trim",
    "padding",
    "resample",
    "normalize",
    "detrend",
    "gain_fit",
    "filter",
    "window",
)


def record_bytes(record):
    """Canonical strict JSON including final LF; hash these exact record bytes."""
    return (
        json.dumps(record, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode()


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _sample_bytes(samples):
    return b"".join(struct.pack("<d", sample) for sample in samples)


def _finite(value):
    return (
        type(value) in (int, float)
        and abs(value) <= sys.float_info.max
        and math.isfinite(value)
    )


def _samples(values):
    if (
        isinstance(values, (str, bytes, dict))
        or not hasattr(values, "__len__")
        or not hasattr(values, "__getitem__")
        or not len(values)
    ):
        raise ValueError("samples must be a nonempty finite one-dimensional sequence")
    dtype = getattr(values, "dtype", None)
    if getattr(values, "ndim", 1) != 1 or (
        dtype is not None
        and (
            getattr(dtype, "kind", None) not in ("i", "u", "f")
            or not 0 < getattr(dtype, "itemsize", 0) <= 8
        )
    ):
        raise ValueError(
            "samples must be one-dimensional real values of at most 64 bits"
        )
    result = []
    for value in values:
        dtype = getattr(value, "dtype", None)
        supported = type(value) in (int, float) or (
            dtype is not None
            and getattr(dtype, "kind", None) in ("i", "u", "f")
            and 0 < getattr(dtype, "itemsize", 0) <= 8
        )
        if not supported or not isinstance(value, Real) or isinstance(value, bool):
            raise ValueError("sample is not a supported real number")
        if isinstance(value, Integral) and abs(int(value)) > 2**53:
            raise ValueError("integer sample exceeds exact binary64 conversion range")
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("sample is not finite")
        result.append(value)
    return result


@dataclass(frozen=True)
class Signal:
    samples: object
    sample_rate_hz: float
    unit: str
    time_origin_samples: int = 0
    onset_sample: int | None = None


@dataclass(frozen=True)
class Preparation:
    """Estimator-local declaration, mandatory even for identity preparation.

    Window bounds are half-open sample offsets from the declared clip/onset.
    No filter is supplied; no boundary samples are inferred or extended.
    """

    estimator: str
    estimator_version: str
    path: str
    unit: str
    time_origin: str = "clip"
    window: tuple[int, int] | None = None
    allowed: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = TRANSFORMS
    boundary: str = "none-no-extension"
    refusal_conditions: tuple[str, ...] = (
        "invalid_input",
        "undeclared_or_forbidden_transform",
        "unavailable_window",
        "inapplicable_invariant",
    )
    invariances: tuple[str, ...] = ()
    measure_kind: str = "absolute"
    gain_domain: tuple[float, float] | None = None


def _validate_spec(spec):
    for text in (spec.estimator, spec.estimator_version, spec.unit):
        if not isinstance(text, str) or not text.strip():
            raise ValueError("estimator name/version and native unit must be declared")
    if spec.path not in ("exact", "property") or spec.time_origin not in (
        "clip",
        "onset",
    ):
        raise ValueError("unknown preparation path or time origin")
    if spec.boundary != "none-no-extension":
        raise ValueError("only no-filter/no-extension boundary policy is qualified")
    if set(spec.allowed) & set(spec.forbidden) or set(spec.allowed) | set(
        spec.forbidden
    ) != set(TRANSFORMS):
        raise ValueError(
            "allowed/forbidden transforms must partition the known operations"
        )
    if not spec.refusal_conditions or any(
        not isinstance(x, str) or not x.strip() for x in spec.refusal_conditions
    ):
        raise ValueError("refusal conditions must be declared")
    if set(spec.allowed) - {"window"}:
        raise ValueError("unqualified transform declared allowed")
    if spec.path == "exact" and (
        spec.allowed or spec.window is not None or spec.time_origin != "clip"
    ):
        raise ValueError("exact path permits identity preparation only")
    if spec.window is not None and (
        len(spec.window) != 2
        or any(type(n) is not int for n in spec.window)
        or spec.window[0] >= spec.window[1]
    ):
        raise ValueError("window must have increasing integer bounds")
    if spec.time_origin == "onset" and spec.window is None:
        raise ValueError("onset-relative preparation requires an explicit window")
    if set(spec.invariances) - {"onset_shift", "silence_padding", "common_gain"}:
        raise ValueError("unknown invariance declaration")


def prepare(signal: Signal, spec: Preparation, *, operations=()) -> dict:
    """Return independent raw/prepared samples and hashes, or explicit refusal.

    Binary64 encodings identify numeric input values, not original-file bytes.
    Bad input cannot be serialized as evidence: its raw samples/hash stay null.
    """
    result = {
        "version": VERSION,
        "status": "refused",
        "reason": "invalid_input",
        "config": asdict(spec),
        "config_sha256": None,
        "input": {
            "sample_rate_hz": signal.sample_rate_hz,
            "unit": signal.unit,
            "time_origin_samples": signal.time_origin_samples,
            "onset_sample": signal.onset_sample,
        },
        "raw_samples": None,
        "prepared_samples": None,
        "raw_sha256": None,
        "prepared_sha256": None,
        "window_bounds": None,
        "requested_operations": list(operations),
        "diagnostic_only": False,
    }
    try:
        _validate_spec(spec)
        if not _finite(signal.sample_rate_hz) or signal.sample_rate_hz <= 0:
            raise ValueError("sample rate must be finite and positive")
        if signal.unit != spec.unit or type(signal.time_origin_samples) is not int:
            raise ValueError("native unit or integer time origin mismatch")
        values = _samples(signal.samples)
        result.update(
            raw_samples=values.copy(), raw_sha256=_digest(_sample_bytes(values))
        )
        if signal.onset_sample is not None and (
            type(signal.onset_sample) is not int
            or not 0 <= signal.onset_sample < len(values)
        ):
            raise ValueError("declared onset is outside available samples")
        requested = set(operations) | ({"window"} if spec.window is not None else set())
        prohibited = requested - set(spec.allowed)
        if prohibited:
            raise ValueError(
                "undeclared_or_forbidden_transform: " + ",".join(sorted(prohibited))
            )
        if "window" in requested and spec.window is None:
            raise ValueError("window operation requires explicit bounds")
        start, stop = 0, len(values)
        if spec.window is not None:
            origin = signal.onset_sample if spec.time_origin == "onset" else 0
            if origin is None:
                raise ValueError("onset-relative window requires declared onset")
            start, stop = (origin + bound for bound in spec.window)
            if start < 0 or stop > len(values):
                raise ValueError("unavailable_window: boundary extension is forbidden")
        prepared = values[start:stop]
        result.update(
            status="valid",
            reason="declared preparation applied",
            prepared_samples=prepared,
            prepared_sha256=_digest(_sample_bytes(prepared)),
            window_bounds=[start, stop],
        )
    except (ValueError, TypeError, OverflowError) as error:
        result["reason"] = str(error)
    # Invalid metadata is retained as text rather than emitting NaN/Infinity.
    try:
        encoded = record_bytes(
            {
                "version": VERSION,
                "spec": result["config"],
                "input": result["input"],
                "operations": result["requested_operations"],
            }
        )
    except (ValueError, TypeError, OverflowError):
        result["input"] = {k: repr(v) for k, v in result["input"].items()}
        result["config"] = {k: repr(v) for k, v in result["config"].items()}
        result["requested_operations"] = [repr(x) for x in operations]
        result.update(
            status="refused",
            reason="nonserializable configuration",
            prepared_samples=None,
            prepared_sha256=None,
            window_bounds=None,
        )
        encoded = record_bytes(
            {
                "version": VERSION,
                "spec": result["config"],
                "input": result["input"],
                "operations": result["requested_operations"],
            }
        )
    result["config_sha256"] = _digest(encoded)
    return result


def prepare_pair(reference, candidate, spec, *, operations=()):
    """Both sides traverse precisely the same implementation and declaration."""
    return tuple(
        prepare(signal, spec, operations=operations)
        for signal in (reference, candidate)
    )


def compare_exact(reference, candidate, spec, *, operations=(), **metric_options):
    pair = prepare_pair(reference, candidate, spec, operations=operations)
    reason = next((r["reason"] for r in pair if r["status"] != "valid"), None)
    if spec.path != "exact":
        reason = "only exact identity preparation may enter primary paired rows"
    if reference.time_origin_samples != candidate.time_origin_samples:
        reason = "exact pair has different declared time origins"
    result = {
        "version": VERSION,
        "preparation": pair,
        "comparison": None,
        "status": "refused" if reason else "valid",
        "reason": reason or "identity pair",
    }
    if reason is None:
        result["comparison"] = compare_paired(
            pair[0]["prepared_samples"],
            pair[1]["prepared_samples"],
            reference_rate_hz=reference.sample_rate_hz,
            candidate_rate_hz=candidate.sample_rate_hz,
            unit=spec.unit,
            **metric_options,
        )
    return result


def check_invariant(
    name,
    baseline,
    transformed,
    *,
    applicable=True,
    reason="declared domain",
    absolute_tolerance=0.0,
    relative_tolerance=0.0,
):
    """Compare every component; never average away sign/time-varying failures."""
    result = {
        "name": name,
        "status": "not_applicable",
        "reason": reason,
        "baseline": None,
        "transformed": None,
        "absolute_tolerance": absolute_tolerance
        if _finite(absolute_tolerance)
        else None,
        "relative_tolerance": relative_tolerance
        if _finite(relative_tolerance)
        else None,
    }
    if not applicable:
        return result
    try:
        if any(
            not _finite(x) or x < 0 for x in (absolute_tolerance, relative_tolerance)
        ):
            raise ValueError("invariant tolerances must be finite and nonnegative")
        left, right = _samples(baseline), _samples(transformed)
        result.update(baseline=left, transformed=right)
        passed = len(left) == len(right) and all(
            abs(Fraction(x) - Fraction(y))
            <= Fraction(absolute_tolerance)
            + Fraction(relative_tolerance) * max(abs(Fraction(x)), abs(Fraction(y)))
            for x, y in zip(left, right)
        )
        result.update(
            status="pass" if passed else "fail",
            reason="all components invariant"
            if passed
            else "invariant component mismatch",
        )
    except (ValueError, TypeError, OverflowError) as error:
        result.update(status="refused", reason=str(error))
    return result


def symmetry_control(forward, reverse):
    """Detect side-specific preparation, even if a final scalar hides it."""
    valid = len(forward) == len(reverse) == 2 and all(
        item["status"] == "valid" for item in (*forward, *reverse)
    )
    passed = valid and record_bytes(forward) == record_bytes(tuple(reversed(reverse)))
    return {
        "name": "reference_candidate_symmetry",
        "status": "pass" if passed else "fail",
        "reason": "identical records under pair swap"
        if passed
        else "asymmetric or refused preparation",
    }


def invariant_trial(
    reference,
    candidate,
    spec,
    measure,
    *,
    kind,
    amount,
    absolute_tolerance=0.0,
    relative_tolerance=0.0,
):
    """Run an estimator-local callable under an applicable shared perturbation.

    The callable receives two prepared records and returns a finite vector.
    No production estimator or property-specific error floor is supplied here.
    """

    def inapplicable(reason):
        return check_invariant(kind, None, None, applicable=False, reason=reason)

    if kind not in spec.invariances or spec.path != "property":
        return inapplicable("invariant not declared for this property preparation")
    if kind == "common_gain":
        domain = spec.gain_domain
        if (
            spec.measure_kind != "ratio"
            or domain is None
            or len(domain) != 2
            or any(not _finite(x) for x in domain)
            or not 0 < domain[0] <= domain[1]
            or not _finite(amount)
            or not domain[0] <= amount <= domain[1]
        ):
            return inapplicable(
                "common gain requires ratio and declared positive nonzero gain domain"
            )
    elif (
        spec.time_origin != "onset"
        or spec.window is None
        or type(amount) is not int
        or (kind == "silence_padding" and amount < 0)
    ):
        return inapplicable(
            "shift/padding requires onset window and integer sample displacement"
        )
    pair = prepare_pair(reference, candidate, spec)
    if any(item["status"] != "valid" for item in pair):
        return {
            "name": kind,
            "status": "refused",
            "reason": "baseline preparation refused",
            "preparation": pair,
        }
    transformed = []
    for signal, prepared in zip((reference, candidate), pair):
        values = prepared["raw_samples"]
        onset = signal.onset_sample
        if kind == "common_gain":
            values = [x * amount for x in values]
        else:
            if amount < 0:
                if -amount > prepared["window_bounds"][0] or any(values[:-amount]):
                    return inapplicable(
                        "negative shift would discard required samples or nonsilence"
                    )
                values = values[-amount:]
            else:
                values = [0.0] * amount + values
            onset += amount
            if kind == "silence_padding":
                values += [0.0] * amount
        transformed.append(replace(signal, samples=values, onset_sample=onset))
    changed = prepare_pair(*transformed, spec)
    if any(item["status"] != "valid" for item in changed):
        return {
            "name": kind,
            "status": "refused",
            "reason": "perturbed preparation refused",
            "preparation": changed,
        }
    try:
        result = check_invariant(
            kind,
            measure(pair),
            measure(changed),
            absolute_tolerance=absolute_tolerance,
            relative_tolerance=relative_tolerance,
        )
    except (ValueError, TypeError, ArithmeticError) as error:
        result = {
            "name": kind,
            "status": "refused",
            "reason": f"estimator refused: {error}",
        }
    return {
        **result,
        "amount": amount,
        "baseline_preparation": pair,
        "transformed_preparation": changed,
    }


def resample_diagnostic(signal, *, target_rate_hz, max_frequency_hz, amplitude_bound):
    """Explicit linear interpolation, conditional bandlimited-input bound only.

    Requires a caller-declared continuous signal bounded by amplitude_bound and
    bandlimited to max_frequency_hz. Finite samples cannot prove that premise.
    No anti-alias filter, extrapolation, endpoint extension, or acceptance use.
    """
    spec = Preparation("linear-resampling-diagnostic", "1", "exact", signal.unit)
    result = prepare(signal, spec)
    result.update(diagnostic_only=True, error_bound=None)
    settings = {
        "method": "linear-binary64-v1",
        "target_rate_hz": target_rate_hz,
        "max_frequency_hz": max_frequency_hz,
        "amplitude_bound": amplitude_bound,
        "time_grid": "j/target_rate <= (N-1)/source_rate",
        "boundary": "no-extension",
        "premise": "caller-declared continuous bandlimited bounded signal; not inferred from samples",
    }
    try:
        rate = signal.sample_rate_hz
        if result["status"] != "valid":
            raise ValueError(result["reason"])
        if (
            rate not in (8000, 16000, 44100, 48000)
            or not _finite(target_rate_hz)
            or not rate < target_rate_hz <= 4 * rate
            or not _finite(max_frequency_hz)
            or not 0 <= max_frequency_hz <= rate / 32
            or not _finite(amplitude_bound)
            or not 0 < amplitude_bound <= 1
            or len(result["raw_samples"]) < 2
            or len(result["raw_samples"]) > 1000000
        ):
            raise ValueError("outside qualified linear-resampling diagnostic domain")
        values = result["raw_samples"]
        if max(map(abs, values)) > amplitude_bound:
            raise ValueError("samples violate declared amplitude bound")
        ratio = Fraction(target_rate_hz) / Fraction(rate)
        count = int((len(values) - 1) * ratio) + 1
        output = []
        for index in range(count):
            position = Fraction(index) / ratio
            lo = int(position)
            fraction = float(position - lo)
            output.append(
                values[lo]
                if fraction == 0
                else (1 - fraction) * values[lo] + fraction * values[lo + 1]
            )
        # Linear interpolation error <= max|f''|/(8 fs^2); Bernstein bound
        # max|f''| <= amplitude_bound*(2*pi*bandlimit)^2 under stated premise.
        bound = (
            amplitude_bound * (2 * math.pi * max_frequency_hz / rate) ** 2 / 8
            + 32 * sys.float_info.epsilon * amplitude_bound
        )
        result.update(
            prepared_samples=output,
            prepared_sha256=_digest(_sample_bytes(output)),
            error_bound=bound,
            reason="diagnostic interpolation under declared continuous-signal premise",
        )
    except (ValueError, TypeError, OverflowError) as error:
        result.update(
            status="refused",
            reason=str(error),
            prepared_samples=None,
            prepared_sha256=None,
        )
    # Keep invalid diagnostic settings strict-JSON serializable as well.
    try:
        record_bytes(settings)
    except (ValueError, TypeError, OverflowError):
        settings = {k: repr(v) for k, v in settings.items()}
    result["resampling"] = settings
    result["config_sha256"] = _digest(
        record_bytes(
            {"input_config_sha256": result["config_sha256"], "resampling": settings}
        )
    )
    return result


def gate_rows(rows, *, controls, preparation, raw_diagnostic: bytes):
    """Wrap validated v1 rows; apparatus failure removes every dependent verdict.

    Supply only the rows depending on these required controls. Not-applicable
    controls cannot qualify a dependent row; select the correct domain first.
    """
    for row in rows:
        validate_row(row)
    if type(raw_diagnostic) is not bytes:
        raise ValueError("raw diagnostic must be the original record bytes")
    if any(row["artifact"]["sha256"] != _digest(raw_diagnostic) for row in rows):
        raise ValueError("raw diagnostic bytes do not match row artifact digest")
    reasons = []
    if not controls:
        reasons.append("no required invariant controls supplied")
    for control in controls:
        if control.get("name") == "runtime_evidence" and not control.get(
            "normative_oracle"
        ):
            reasons.append("runtime control has no actual qualified normative evidence")
        if (
            not isinstance(control.get("name"), str)
            or not control["name"].strip()
            or not isinstance(control.get("reason"), str)
            or not control["reason"].strip()
            or control.get("status") != "pass"
        ):
            reasons.append(
                f"{control.get('name', 'unnamed')}: {control.get('reason', 'malformed invariant control')}"
            )
    if len(preparation) != 2 or any(p.get("status") != "valid" for p in preparation):
        reasons.append("preparation missing or refused")
    if any(p.get("diagnostic_only") for p in preparation):
        reasons.append("resampling diagnostic cannot enter acceptance rows")
    for item in preparation:
        if item.get("status") == "valid" and not item.get("diagnostic_only"):
            try:
                reproduced = prepare(
                    Signal(item["raw_samples"], **item["input"]),
                    Preparation(**item["config"]),
                    operations=item["requested_operations"],
                )
                if record_bytes(reproduced) != record_bytes(item):
                    reasons.append("prepared record failed reproduction")
            except (KeyError, TypeError, ValueError):
                reasons.append("malformed prepared record")
    if len(preparation) == 2 and preparation[0].get("config") != preparation[1].get(
        "config"
    ):
        reasons.append("reference/candidate preparation declarations differ")
    record = {
        "schema": "preparation-gated-diagnostic",
        "schema_version": 1,
        "preparation": preparation,
        "controls": controls,
        "raw_rows": rows,
        "raw_diagnostic_hex": raw_diagnostic.hex(),
        "refusal_reasons": reasons,
    }
    encoded = record_bytes(record)
    artifact = artifact_reference(
        artifact_id="prep1-" + _digest(encoded), record_bytes=encoded
    )
    output = copy.deepcopy(rows)
    configuration = _digest(
        record_bytes(
            {
                "preparation": [p.get("config_sha256") for p in preparation],
                "controls": controls,
            }
        )
    )
    for row in output:
        row["artifact"] = artifact.copy()
        row["estimator"]["version"] += "+" + VERSION + "." + configuration
        if reasons:
            row.update(
                observed=None,
                verdict="NO VERDICT",
                validity={"status": "invalid", "reason": "; ".join(reasons)},
            )
        validate_row(row)
    return output, encoded


def runtime_evidence_control(evidence, expected, read_artifact):
    """Validate the v1 consumer projection of producer reports, without rendering.

    tools/qualify_preparation.py owns the native #12/#88 adapters. The projection
    retains producer record identities, required cells, runtime/source/config,
    named input/trace artifact digests and refusal/divergence diagnostics. The
    resolver must return actual bytes, not merely echo an expected digest.
    Synthetic projections exercise this consumer but never qualify a runtime.
    """
    result = {
        "name": "runtime_evidence",
        "status": "refused",
        "reason": "missing producer report; actual integration pending",
        "evidence_category": "missing",
        "normative_oracle": False,
    }
    if evidence is None:
        return result
    try:
        if (
            type(evidence["schema_version"]) is not int
            or evidence["schema_version"] != 1
        ):
            raise ValueError("unsupported runtime consumer projection version")
        category = evidence["evidence_category"]
        if category not in ("synthetic", "actual"):
            raise ValueError("unknown runtime evidence category")
        result["evidence_category"] = category
        result["diagnostics"] = evidence["diagnostics"]
        for field in ("producer", "source", "configuration_sha256"):
            if evidence[field] != expected[field]:
                raise ValueError("stale or incorrect runtime " + field)
        cells = evidence["cells"]
        required = expected["cells"]
        if (
            not required
            or len({c["key"] for c in required}) != len(required)
            or not expected["repeat_groups"]
            or not expected["equality_groups"]
            or not expected["parameter_names"]
            or not {"normalized", "physical", "noise", "audio"}.issubset(
                expected["artifacts"]
            )
        ):
            raise ValueError("incomplete or vacuous runtime consumer request")
        if [c["key"] for c in cells] != [c["key"] for c in expected["cells"]]:
            raise ValueError("missing, duplicate, reordered or extra runtime cells")
        failures, verified = [], []
        for cell, request in zip(cells, expected["cells"]):
            if cell["status"] != "PASS":
                if cell["status"] not in ("FAIL", "NO_VERDICT") or not cell.get(
                    "reason"
                ):
                    raise ValueError("malformed runtime refusal")
                failures.append({"key": cell["key"], "reason": cell["reason"]})
                continue
            for field in (
                "runtime",
                "execution_width",
                "reproducible",
                "case_definition",
            ):
                if cell[field] != request[field]:
                    raise ValueError("incorrect runtime cell " + field)
            identity = SoundIdentity(request["sound_index"]).to_dict(
                request["identity_batch_size"]
            )
            if cell["identity"] != identity:
                raise ValueError("incorrect global sound/noise/train-test identity")
            names = expected["parameter_names"]
            for field in ("normalized", "physical"):
                if sorted(cell[field]) != names or not all(
                    _finite(x) for x in cell[field].values()
                ):
                    raise ValueError("missing/nonfinite named " + field + " parameters")
            if set(cell["artifacts"]) != set(expected["artifacts"]):
                raise ValueError("missing or extra named noise/audio/trace artifacts")
            for name, count in expected["artifacts"].items():
                record = cell["artifacts"][name]
                raw = read_artifact(record["locator"])
                if (
                    type(raw) is not bytes
                    or _digest(raw) != record["sha256"]
                    or len(raw) != count * 4
                ):
                    raise ValueError("artifact bytes/hash/count mismatch: " + name)
                values = [x[0] for x in struct.iter_unpack("<f", raw)]
                if not all(math.isfinite(x) for x in values):
                    raise ValueError("nonfinite runtime artifact: " + name)
                if name in ("normalized", "physical"):
                    if raw != struct.pack(
                        "<" + "f" * len(names), *[cell[name][n] for n in names]
                    ):
                        raise ValueError("named parameter artifact mismatch: " + name)
            if not cell.get("process_identity"):
                raise ValueError("missing fresh-process identity")
            verified.append(cell)
        # Groups are declared by the adapter from the preregistered plan, not
        # inferred from whichever cells happen to be present in a report.
        by_key = {c["key"]: c for c in verified}
        for group in expected["repeat_groups"]:
            if len(group) < 2 or any(key not in by_key for key in group):
                failures.append({"key": group, "reason": "required repeat unavailable"})
                continue
            repeated = [by_key[key] for key in group]
            if len({c["process_identity"] for c in repeated}) != len(repeated):
                raise ValueError("repeat reused a process identity")
            hashes = [
                {k: v["sha256"] for k, v in c["artifacts"].items()} for c in repeated
            ]
            if any(h != hashes[0] for h in hashes[1:]):
                failures.append(
                    {"key": group, "reason": "fresh-process artifact divergence"}
                )
        for group in expected["equality_groups"]:
            if len(group) < 2 or any(key not in by_key for key in group):
                failures.append(
                    {
                        "key": group,
                        "reason": "required batch/input comparison unavailable",
                    }
                )
                continue
            compared = [
                {
                    k: by_key[key]["artifacts"][k]["sha256"]
                    for k in expected["equal_artifacts"]
                }
                for key in group
            ]
            if any(h != compared[0] for h in compared[1:]):
                failures.append(
                    {"key": group, "reason": "batch/input artifact divergence"}
                )
        oracle = evidence["oracle_status"]
        if oracle not in ("normative", "diagnostic", "rejected"):
            raise ValueError("unknown scalar/runtime oracle decision")
        if oracle != "normative":
            failures.append(
                {
                    "key": "oracle_status",
                    "reason": oracle
                    + " oracle cannot qualify normative production rows",
                }
            )
        result.update(
            status="refused" if failures else "pass",
            failures=failures,
            reason="; ".join(f["reason"] for f in failures)
            if failures
            else "all required runtime artifacts revalidated",
            normative_oracle=not failures and category == "actual",
            producer=evidence["producer"],
            diagnostics=evidence["diagnostics"],
        )
    except (KeyError, TypeError, ValueError, OSError, OverflowError) as error:
        result.update(status="refused", reason=str(error))
    return result
