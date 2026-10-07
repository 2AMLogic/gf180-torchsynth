"""Directed trace-path planning and receipt verification (stdlib only, Python 3.9+).

Issue #286. The #23 qualification captured all 32 registry traces on three
normalization fixtures that run a flat-envelope sine path; that demonstrates the
capture path, not each signal path. This module owns the pure, Torch-free half
of the follow-up measurement:

- ``build_plan`` derives a deterministic trace-to-case plan from the
  preregistered ``directed-coverage-v1`` ``traces[*].cases`` lists, selecting at
  least one listed case per trace, deduplicating shared cases and carrying each
  trace's declared isolation limitation;
- every row carries an activation *expectation* fixed by the plan before any
  execution (time-series statistics for control/audio paths, a declared range
  for scalar keyboard seams, and the declared peak/gain/output relation for the
  serially coupled normalization seams);
- ``verify_receipt`` re-reads and rehashes every retained raw payload,
  recomputes the statistics from those bytes and rejects any receipt whose
  mapping, hashes, shapes, statistics or activation claims disagree.

Nothing here renders audio, changes DSP, the registry schema or runtime
admission. A receipt that was not produced by an admitted qualified-runtime
execution is ``UNRUN``, which is never a pass.
"""

from __future__ import annotations

import copy
import hashlib
import math
import re
import struct
import sys
from array import array
from pathlib import Path

try:
    from . import trace_registry
except ImportError:
    # The release-era worker imports this file directly on Python 3.9.
    import trace_registry

require = trace_registry.require

ROOT = trace_registry.ROOT
COVERAGE_PATH = ROOT / "spec/reference/directed-coverage-v1.json"
DIRECTED_PATH = ROOT / "spec/reference/directed-voice-v1.json"

RECEIPT_VERSION = "directed-trace-paths-v1"
RUNTIME_PROFILE = "release-mkl-compatible-v1"
PINNED_TORCHSYNTH_COMMIT = "2b0964d4c6c3d472a2a0d54d91b408caaeffca6d"
STATUSES = ("PASS", "FAIL", "UNRUN")
ROW_STATUSES = ("captured", "failed", "missing", "unrun")
EXPECTATION_KINDS = ("series_active", "scalar_in_range", "normalization")
MIXER_SEAMS = (
    "mixer.pre_normalization",
    "mixer.peak",
    "mixer.gain",
    "mixer.output",
)
ENVELOPE_PREFERENCE = ("cut-attack", "cut-decay", "long-release", "zero-stages")
IDENTITY_KEYS = (
    "audio_identical",
    "parameters_identical",
    "noise_identical",
    "rng_identical",
    "labels_all",
)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def f32(value):
    """Round a Python float to the nearest binary32 value."""
    try:
        return struct.unpack("<f", struct.pack("<f", value))[0]
    except OverflowError as error:
        raise ValueError("binary32 overflow") from error


def payload_filename(case_id, name):
    """Deterministic, path-safe raw payload file name for (case, trace)."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", case_id)
    return safe + "." + name + ".f32le"


def _envelope_variant_rank(case_id):
    variant = case_id.rsplit(":", 1)[-1]
    require(variant in ENVELOPE_PREFERENCE, "unknown envelope variant: " + case_id)
    return ENVELOPE_PREFERENCE.index(variant)


def select_cases(name, listed):
    """Preregistered selection rule over one trace's listed coverage cases.

    Envelope outputs use the first variant in ``ENVELOPE_PREFERENCE`` (a
    ``zero-stages`` patch deliberately deactivates its envelope, so it is the
    last resort). The normalization seams take every listed case because their
    expectation is a branch relation that differs per case. All other traces
    take the first listed case.
    """
    require(type(listed) is list and listed, "trace lists no coverage case: " + name)
    require(len(set(listed)) == len(listed), "duplicate listed case: " + name)
    if name in MIXER_SEAMS:
        return list(listed)
    if listed[0].startswith("envelope:"):
        return [min(listed, key=_envelope_variant_rank)]
    return [listed[0]]


def expectation_for(trace, case_id, coverage):
    """Activation expectation fixed by the plan, never inferred from output."""
    name = trace["name"]
    if name in MIXER_SEAMS:
        relation = case_id.split(":", 1)[1]
        target = coverage["normalization"][relation]["target_peak"]
        return {
            "kind": "normalization",
            "seam": name,
            "relation": relation,
            "target_peak": target,
        }
    if trace["kind"] == "scalar":
        return {
            "kind": "scalar_in_range",
            "minimum": trace["range"]["minimum"],
            "maximum": trace["range"]["maximum"],
        }
    return {"kind": "series_active"}


def check_identity_inputs(registry, coverage, directed):
    """Coverage/manifest/registry consistency required before planning."""
    names = [t["name"] for t in registry["traces"]]
    require(
        sorted(coverage["traces"]) == sorted(names),
        "coverage trace set differs from the registry trace set",
    )
    require(
        coverage["manifest_identity"] == directed["identity"],
        "coverage manifest identity differs from the directed manifest",
    )
    directed_ids = [case["id"] for case in directed["cases"]]
    require(len(set(directed_ids)) == len(directed_ids), "duplicate directed case id")
    for name in names:
        for case_id in coverage["traces"][name]["cases"]:
            require(case_id in set(directed_ids), "listed case not in manifest: " + case_id)
    return directed_ids


def build_plan(registry, coverage, directed):
    """Deterministic trace-to-case plan; one row per (trace, selected case)."""
    check_identity_inputs(registry, coverage, directed)
    rows = []
    for trace in registry["traces"]:
        name = trace["name"]
        entry = coverage["traces"][name]
        for case_id in select_cases(name, entry["cases"]):
            rows.append(
                {
                    "trace": name,
                    "case": case_id,
                    "listed_cases": list(entry["cases"]),
                    "limitation": entry["isolation"],
                    "expectation": expectation_for(trace, case_id, coverage),
                }
            )
    cases = []
    for row in rows:
        if row["case"] not in [c["id"] for c in cases]:
            cases.append({"id": row["case"], "traces": []})
    for case in cases:
        case["traces"] = [r["trace"] for r in rows if r["case"] == case["id"]]
        if any(t in MIXER_SEAMS for t in case["traces"]):
            require(
                case["traces"] == list(MIXER_SEAMS),
                "normalization case must plan all four seams together",
            )
    require(
        {r["trace"] for r in rows} == {t["name"] for t in registry["traces"]},
        "plan does not cover every registry trace",
    )
    return {"cases": cases, "rows": rows}


def read_f32le(data):
    """Decode little-endian binary32 payload bytes; rejects ragged lengths."""
    require(sys.byteorder == "little", "unsupported host byte order")
    require(len(data) % 4 == 0, "payload is not a whole number of binary32 values")
    values = array("f")
    values.frombytes(data)
    return values


def statistics(values):
    """Statistics derived from decoded payload values only."""
    count = len(values)
    finite = all(math.isfinite(v) for v in values)
    if not finite or count == 0:
        return {
            "sample_count": count,
            "all_finite": finite,
            "nonzero_count": None,
            "minimum": None,
            "maximum": None,
            "constant": None,
        }
    low, high = min(values), max(values)
    return {
        "sample_count": count,
        "all_finite": True,
        "nonzero_count": sum(1 for v in values if v != 0.0),
        "minimum": low,
        "maximum": high,
        "constant": low == high,
    }


def _max_abs(values):
    return f32(max(abs(v) for v in values))


def evaluate(expectation, trace, case_payloads):
    """Measurements and per-check booleans for one row from payload bytes."""
    name = trace["name"]
    values = read_f32le(case_payloads[name])
    measured = statistics(values)
    checks = {"finite": measured["all_finite"]}
    if not measured["all_finite"]:
        return measured, checks
    kind = expectation["kind"]
    if kind == "series_active":
        checks["nonzero"] = measured["nonzero_count"] > 0
        checks["nonconstant"] = measured["constant"] is False
    elif kind == "scalar_in_range":
        checks["nonzero"] = measured["nonzero_count"] > 0
        checks["within_declared_range"] = (
            expectation["minimum"] <= measured["minimum"]
            and measured["maximum"] <= expectation["maximum"]
        )
    elif kind == "normalization":
        checks.update(_normalization_checks(expectation, trace, case_payloads, measured))
    else:
        raise ValueError("unsupported activation expectation: " + str(kind))
    return measured, checks


def _normalization_checks(expectation, trace, case_payloads, measured):
    """Declared branch relation; scalar seams are not non-constant assertions."""
    seam = expectation["seam"]
    relation = expectation["relation"]
    peak_values = read_f32le(case_payloads["mixer.peak"])
    pre_values = read_f32le(case_payloads["mixer.pre_normalization"])
    require(len(peak_values) == 1, "mixer.peak must hold one binary32 value")
    peak = peak_values[0]
    require(math.isfinite(peak) and all(math.isfinite(v) for v in pre_values),
            "nonfinite normalization input")
    checks = {}
    if seam == "mixer.pre_normalization":
        checks["nonzero"] = measured["nonzero_count"] > 0
        checks["nonconstant"] = measured["constant"] is False
        checks["peak_equals_max_abs"] = peak == _max_abs(pre_values)
    elif seam == "mixer.peak":
        checks["equals_declared_target_peak"] = peak == f32(expectation["target_peak"])
        checks["relation_" + relation] = {
            "above": peak > 1.0,
            "below": peak < 1.0,
            "tie": peak == 1.0,
        }[relation]
    elif seam == "mixer.gain":
        gain = read_f32le(case_payloads["mixer.gain"])
        require(len(gain) == 1, "mixer.gain must hold one binary32 value")
        expected = f32(1.0 / peak) if peak > 1.0 else 1.0
        checks["gain_matches_rule"] = gain[0] == expected
        checks["relation_" + relation] = {
            "above": gain[0] != 1.0 and peak > 1.0,
            "below": gain[0] == 1.0 and peak < 1.0,
            "tie": gain[0] == 1.0 and peak == 1.0,
        }[relation]
    elif seam == "mixer.output":
        output = read_f32le(case_payloads["mixer.output"])
        require(len(output) == len(pre_values), "mixer.output length mismatch")
        if peak > 1.0:
            expected = array("f", (f32(v / peak) for v in pre_values))
        else:
            expected = pre_values
        checks["output_matches_original_branch"] = output == expected
        checks["relation_" + relation] = (
            (peak > 1.0 and output != pre_values)
            if relation == "above"
            else (peak <= 1.0 and output == pre_values)
        )
    else:
        raise ValueError("unknown normalization seam: " + seam)
    return checks


def activation_record(expectation, checks):
    return {
        "expectation": expectation["kind"],
        "checks": checks,
        "passed": bool(checks) and all(checks.values()),
    }


def payload_descriptor(trace, case_id, data):
    return {
        "file": payload_filename(case_id, trace["name"]),
        "sha256": sha256(data),
        "size_bytes": len(data),
        "dtype": trace["dtype"],
        "encoding": trace["encoding"],
        "shape": list(trace["shape"]),
        "sample_count": len(data) // 4,
    }


def expected_sample_count(trace):
    count = 1
    for dim in trace["shape"]:
        count *= dim
    return count


def load_payload(raw_dir, descriptor):
    """Read a retained payload by bare file name and rehash it."""
    file_name = descriptor["file"]
    require(
        type(file_name) is str and file_name == Path(file_name).name and file_name,
        "payload file must be a bare file name: " + str(file_name),
    )
    path = Path(raw_dir) / file_name
    require(path.is_file(), "retained payload missing: " + file_name)
    data = path.read_bytes()
    require(sha256(data) == descriptor["sha256"], "payload hash mismatch: " + file_name)
    require(len(data) == descriptor["size_bytes"], "payload size mismatch: " + file_name)
    return data


def build_row(plan_row, trace, case_payloads):
    """Captured row for one planned (trace, case); payload bytes in memory."""
    data = case_payloads[trace["name"]]
    descriptor = payload_descriptor(trace, plan_row["case"], data)
    measured, checks = evaluate(plan_row["expectation"], trace, case_payloads)
    return {
        "trace": plan_row["trace"],
        "case": plan_row["case"],
        "status": "captured",
        "expectation": plan_row["expectation"],
        "limitation": plan_row["limitation"],
        "payload": descriptor,
        "measurements": measured,
        "activation": activation_record(plan_row["expectation"], checks),
    }


def unaccounted_row(plan_row, status, reason):
    require(status in ("failed", "missing", "unrun"), "invalid unaccounted status")
    require(type(reason) is str and reason, "unaccounted rows need a reason")
    return {
        "trace": plan_row["trace"],
        "case": plan_row["case"],
        "status": status,
        "reason": reason,
        "expectation": plan_row["expectation"],
        "limitation": plan_row["limitation"],
    }


def summarize(rows):
    counts = {status: 0 for status in ROW_STATUSES}
    activated = 0
    for row in rows:
        counts[row["status"]] += 1
        if row["status"] == "captured" and row["activation"]["passed"]:
            activated += 1
    traces = {r["trace"] for r in rows}
    traces_ok = {
        t
        for t in traces
        if any(
            r["trace"] == t and r["status"] == "captured" and r["activation"]["passed"]
            for r in rows
        )
    }
    complete = (
        counts["captured"] == len(rows)
        and activated == len(rows)
        and len(traces_ok) == len(traces)
    )
    return {
        "rows": len(rows),
        "traces": len(traces),
        "traces_with_passing_row": len(traces_ok),
        "captured": counts["captured"],
        "activation_passed": activated,
        "failed": counts["failed"],
        "missing": counts["missing"],
        "unrun": counts["unrun"],
        "complete_path_coverage": complete,
    }


def derived_status(rows, case_checks, execution_admitted):
    if not execution_admitted:
        return "UNRUN"
    summary = summarize(rows)
    identity_ok = bool(case_checks) and all(
        all(c[key] is True for key in IDENTITY_KEYS) for c in case_checks.values()
    )
    return "PASS" if summary["complete_path_coverage"] and identity_ok else "FAIL"


def verify_receipt(receipt, registry, coverage, directed, raw_dir=None):
    """Strictly validate a receipt; returns the derived status string.

    Raises ``ValueError`` on any inconsistency. A passing/failing receipt needs
    ``raw_dir`` because receipt-only inspection cannot establish raw integrity;
    an ``UNRUN`` receipt carries no payloads and returns ``"UNRUN"``, which
    callers must never treat as a pass.
    """
    plan = build_plan(registry, coverage, directed)
    require(receipt.get("receipt_version") == RECEIPT_VERSION, "unknown receipt version")
    require(receipt.get("status") in STATUSES, "unknown receipt status")
    require(
        receipt.get("pinned_torchsynth_commit") == PINNED_TORCHSYNTH_COMMIT,
        "receipt pins a different TorchSynth commit",
    )
    require(receipt.get("runtime_profile") == RUNTIME_PROFILE, "wrong runtime profile")
    hashes = receipt.get("input_sha256", {})
    require(
        hashes.get("registry_sha256") == sha256(trace_registry.REGISTRY_PATH.read_bytes())
        and receipt.get("registry_token") == trace_registry.registry_token(),
        "receipt registry identity is stale",
    )
    require(
        hashes.get("coverage_sha256") == sha256(COVERAGE_PATH.read_bytes()),
        "receipt coverage identity is stale",
    )
    require(
        hashes.get("directed_sha256") == sha256(DIRECTED_PATH.read_bytes()),
        "receipt directed manifest identity is stale",
    )
    require(receipt.get("plan") == plan, "receipt plan differs from the derived plan")

    rows = receipt.get("rows")
    require(type(rows) is list, "receipt rows must be a list")
    seen = set()
    for row in rows:
        key = (row.get("trace"), row.get("case"))
        require(key not in seen, "duplicate row: " + str(key))
        seen.add(key)
    planned = {(r["trace"], r["case"]) for r in plan["rows"]}
    require(
        seen == planned and len(rows) == len(plan["rows"]),
        "row set differs from the plan (missing, extra or mismatched case mapping)",
    )
    registry_names = {t["name"] for t in registry["traces"]}
    require(
        {row["trace"] for row in rows} == registry_names,
        "receipt omits a registry trace name",
    )
    plan_rows = {(r["trace"], r["case"]): r for r in plan["rows"]}
    by_name = {t["name"]: t for t in registry["traces"]}
    for row in rows:
        planned_row = plan_rows[(row["trace"], row["case"])]
        require(
            row["case"] in coverage["traces"][row["trace"]]["cases"],
            "case not permitted for trace: " + row["trace"],
        )
        require(
            row["expectation"] == planned_row["expectation"]
            and row["expectation"]["kind"] in EXPECTATION_KINDS,
            "unsupported or altered activation expectation: " + row["trace"],
        )
        require(
            row["limitation"] == planned_row["limitation"],
            "declared isolation limitation dropped: " + row["trace"],
        )
        require(row["status"] in ROW_STATUSES, "unknown row status")

    status = receipt["status"]
    summary = receipt.get("summary")
    case_checks = receipt.get("case_checks", {})
    admitted = receipt.get("execution", {}).get("admitted") is True
    if status == "UNRUN":
        require(not admitted, "UNRUN receipt claims an admitted execution")
        for row in rows:
            require(
                row["status"] == "unrun"
                and type(row.get("reason")) is str
                and row["reason"]
                and "payload" not in row
                and "measurements" not in row
                and "activation" not in row,
                "UNRUN receipt row carries evidence or lacks a reason",
            )
        require(not case_checks, "UNRUN receipt carries case checks")
        require(
            summary == summarize(rows) and summary["complete_path_coverage"] is False,
            "UNRUN receipt summary inconsistent",
        )
        return "UNRUN"

    require(admitted, "non-UNRUN receipt lacks an admitted execution record")
    require(raw_dir is not None, "raw payload directory required to verify a measured receipt")
    payloads = {}
    for row in rows:
        if row["status"] != "captured":
            require(
                type(row.get("reason")) is str and row["reason"],
                "missing/failed/unrun row needs a reason: " + row["trace"],
            )
            continue
        trace = by_name[row["trace"]]
        require(
            type(row.get("payload")) is dict
            and "measurements" in row
            and "activation" in row,
            "captured row lacks payload/measurements/activation: " + row["trace"],
        )
        descriptor = row["payload"]
        require(
            descriptor["file"] == payload_filename(row["case"], row["trace"])
            and descriptor["dtype"] == trace["dtype"]
            and descriptor["encoding"] == trace["encoding"]
            and descriptor["shape"] == list(trace["shape"])
            and descriptor["sample_count"] == expected_sample_count(trace)
            and descriptor["size_bytes"] == 4 * expected_sample_count(trace),
            "payload dtype/shape/count mismatch: " + row["trace"],
        )
        data = load_payload(raw_dir, descriptor)
        payloads.setdefault(row["case"], {})[row["trace"]] = data
    for row in rows:
        if row["status"] != "captured":
            continue
        trace = by_name[row["trace"]]
        case_payloads = payloads[row["case"]]
        if row["expectation"]["kind"] == "normalization":
            for seam in MIXER_SEAMS:
                require(seam in case_payloads, "normalization case lacks payload: " + seam)
        measured, checks = evaluate(row["expectation"], trace, case_payloads)
        require(measured["all_finite"], "nonfinite payload: " + row["trace"])
        require(row["measurements"] == measured, "measurements differ from payload bytes")
        require(
            row["activation"] == activation_record(row["expectation"], checks),
            "unsupported activation claim: " + row["trace"],
        )
    for case in plan["cases"]:
        entry = case_checks.get(case["id"])
        require(entry is not None, "missing byte-identity record: " + case["id"])
        require(
            all(type(entry.get(key)) is bool for key in IDENTITY_KEYS),
            "malformed byte-identity record: " + case["id"],
        )
    require(set(case_checks) == {c["id"] for c in plan["cases"]}, "extra case check")
    require(summary == summarize(rows), "receipt summary differs from its rows")
    require(
        status == derived_status(rows, case_checks, admitted),
        "receipt status is not supported by its rows",
    )
    return status


def rows_from_worker(plan, registry, worker_report, raw_dir):
    """Rows and byte-identity records from a worker report plus retained bytes.

    Statistics come from the rehashed payload bytes, never from the worker. A
    trace the worker did not retain is ``missing``; a retained payload whose
    bytes disagree with the worker's inventory digest, registry shape/dtype or
    finiteness is ``failed``.
    """
    by_name = {t["name"]: t for t in registry["traces"]}
    worker_cases = {c["case"]["id"]: c for c in worker_report.get("cases", [])}
    rows = []
    case_checks = {}
    for case in plan["cases"]:
        result = worker_cases.get(case["id"])
        plan_rows = [r for r in plan["rows"] if r["case"] == case["id"]]
        if result is None:
            rows.extend(
                unaccounted_row(r, "missing", "worker reported no result for the case")
                for r in plan_rows
            )
            continue
        case_checks[case["id"]] = {
            key: result["identity"].get(key) is True for key in IDENTITY_KEYS
        }
        inventory = {item["name"]: item for item in result["capture_inventory"]}
        payloads = {}
        problems = {}
        for plan_row in plan_rows:
            name = plan_row["trace"]
            trace = by_name[name]
            item = inventory.get(name)
            if item is None:
                problems[name] = ("missing", "trace absent from worker capture inventory")
                continue
            try:
                data = load_payload(
                    raw_dir,
                    {
                        "file": item["file"],
                        "sha256": item["sha256"],
                        "size_bytes": item["size_bytes"],
                    },
                )
                require(
                    item["file"] == payload_filename(case["id"], name)
                    and len(data) == 4 * expected_sample_count(trace)
                    and item["dtype"] == trace["dtype"]
                    and item["shape"] == list(trace["shape"]),
                    "payload shape/dtype/name mismatch",
                )
                require(
                    statistics(read_f32le(data))["all_finite"], "nonfinite payload"
                )
            except ValueError as error:
                problems[name] = ("failed", str(error))
                continue
            payloads[name] = data
        for plan_row in plan_rows:
            name = plan_row["trace"]
            if name in problems:
                rows.append(unaccounted_row(plan_row, *problems[name]))
            elif plan_row["expectation"]["kind"] == "normalization" and any(
                seam not in payloads for seam in MIXER_SEAMS
            ):
                rows.append(
                    unaccounted_row(
                        plan_row, "failed", "normalization relation needs all four seams"
                    )
                )
            else:
                rows.append(build_row(plan_row, by_name[name], payloads))
    return rows, case_checks


def unrun_receipt(plan, registry, input_hashes, reason, host, command):
    """Receipt for a host that cannot or did not run the measurement."""
    rows = [unaccounted_row(r, "unrun", reason) for r in plan["rows"]]
    return {
        "receipt_version": RECEIPT_VERSION,
        "status": "UNRUN",
        "pinned_torchsynth_commit": PINNED_TORCHSYNTH_COMMIT,
        "runtime_profile": RUNTIME_PROFILE,
        "registry_token": trace_registry.registry_token(),
        "input_sha256": copy.deepcopy(input_hashes),
        "plan": copy.deepcopy(plan),
        "execution": {
            "admitted": False,
            "outcome": "unrun",
            "reason": reason,
            "host": host,
            "command": command,
        },
        "rows": rows,
        "summary": summarize(rows),
        "scope": SCOPE,
        "limitations": LIMITATIONS,
    }


def measured_receipt(plan, rows, case_checks, input_hashes, execution, extra):
    receipt = {
        "receipt_version": RECEIPT_VERSION,
        "status": derived_status(rows, case_checks, execution["admitted"]),
        "pinned_torchsynth_commit": PINNED_TORCHSYNTH_COMMIT,
        "runtime_profile": RUNTIME_PROFILE,
        "registry_token": trace_registry.registry_token(),
        "input_sha256": copy.deepcopy(input_hashes),
        "plan": copy.deepcopy(plan),
        "execution": execution,
        "case_checks": case_checks,
        "rows": rows,
        "summary": summarize(rows),
        "scope": SCOPE,
        "limitations": LIMITATIONS,
    }
    receipt.update(extra)
    return receipt


SCOPE = (
    "Bounded directed trace-path capture on the preregistered directed-coverage-v1 "
    "cases, batch-32 selected sound, production passive TraceCapture only"
)
LIMITATIONS = [
    "No holdout, corpus-wide, fidelity, RTL, hardware or gf180 claim",
    "A row's activation fact describes one directed case, not the trace's full range",
    "Graph-limited paths keep the isolation limitation declared in directed-coverage-v1",
    "Only a PASS receipt verified against rehashed raw payloads establishes complete "
    "path coverage; UNRUN, FAIL, missing or failed rows do not",
]
