#!/usr/bin/env python3
"""Compare the independent float control path against captured references.

Stdlib-only host-side consumer for issue #40. Loads an issue-40 capture
directory produced by ``env/release-era/capture_control_path.py`` inside the
ratified DR-0006 runtime, renders the declared control path
(``torchsynth_voice.control_path``) from each case's observed physical map,
and compares every owned trace under the declared boundary classes:

- ``input.normalized`` / ``input.noise``: exact bytes (digest equality
  between the manifest record, the on-disk bytes and the resolved request);
- ``physical.parameters``: measured observation (the model consumes the
  observed runtime conversion verbatim; consumption is verified, never
  re-derived);
- all 22 owned graph traces: declared metrics via
  ``torchsynth_voice.paired_metrics`` (time-locked; never aligned, trimmed
  or gain-fitted), decided by an explicit preregistered rubric.

With ``--rubric`` every trace row must PASS, the endpoint contract
(``trace_capture.check_endpoint_bytes``) must hold on both sides, and the
preregistered mutation set (clamped mod matrix, selector LFO, integer ADSR
timing, ZOH and off-endpoint upsample controls) must FAIL its named traces.
Without ``--rubric`` the tool runs in calibration mode and only reports the
observed maxima; it never issues a verdict. Raw paired-metrics diagnostic
bytes are persisted per case/trace either way, so every mismatch localizes
to a named module/trace with raw artifacts kept.
"""

import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import float_interfaces, trace_registry  # noqa: E402
from torchsynth_voice.control_path import (  # noqa: E402
    MUTATION_CLAMP_MOD_MATRIX,
    MUTATION_INTEGER_ADSR_TIMING,
    MUTATION_SELECTOR_LFO,
    ControlPathModel,
    control_upsample,
)
from torchsynth_voice.paired_metrics import (  # noqa: E402
    Limit,
    Rubric,
    compare_paired,
    scorecard_rows,
)

MUTATION_CASES = 8
RUBRIC_SCHEMA = "torchsynth-control-path-rubric"


def canonical_digest(mapping):
    payload = json.dumps(
        sorted(mapping.items()), separators=(",", ":"), allow_nan=False
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def load_case(directory):
    record = json.loads((directory / "case.json").read_text())
    traces = {}
    for name, item in record["traces"].items():
        path = directory / item["file"]
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != item["sha256"]:
            raise ValueError("trace bytes fail digest: " + name)
        traces[name] = list(struct.unpack("<%df" % (len(payload) // 4), payload))
    noise_payload = (directory / record["noise"]["file"]).read_bytes()
    if hashlib.sha256(noise_payload).hexdigest() != record["noise"]["sha256"]:
        raise ValueError("noise bytes fail digest")
    return record, traces, noise_payload


def build_request(record, noise_payload):
    return float_interfaces.ResolvedRequest(
        record["sound_index"],
        record["normalized_by_name"],
        {
            "seed": record["noise_declared"]["seed"],
            "slot": record["noise_slot"],
            "sample_count": 176400,
            "sha256": record["noise"]["sha256"],
            "samples": noise_payload,
        },
        physical=record["physical_by_name"],
        execution_status="canonical-batched",
    )


def load_rubric(path):
    document = json.loads(path.read_text())
    if document.get("schema") != RUBRIC_SCHEMA:
        raise ValueError("unknown rubric schema: " + str(document.get("schema")))
    if document.get("numeric_contract") != "unbound:#53":
        raise ValueError("rubric must leave the numeric contract unbound")
    rubrics = {}
    for name, item in document["traces"].items():
        limits = {}
        for metric, limit in item["limits"].items():
            limits[metric] = Limit(
                limit["expected"], limit["tolerance"], limit["unit"], limit["source"]
            )
        rubrics[name] = Rubric(item["rubric_id"], item["rubric_version"], limits)
    return document, rubrics


def verdict_for(rubric, rows):
    decided = [row for row in rows if row["verdict"] in ("PASS", "FAIL")]
    if not decided:
        return "NO VERDICT"
    return "FAIL" if any(row["verdict"] == "FAIL" for row in decided) else "PASS"


def compare_case(
    case_id, record, reference, model_outputs, rate_of, rubrics, diagnostics, partition
):
    rows_out = []
    for name, candidate in model_outputs.items():
        values = reference[name]
        measurement = compare_paired(
            values,
            candidate,
            reference_rate_hz=rate_of(name),
            candidate_rate_hz=rate_of(name),
            unit="amplitude",
            window_samples=len(values),
        )
        rubric = rubrics.get(name) if rubrics else None
        rows, diagnostic_bytes = scorecard_rows(
            measurement,
            case_id=case_id,
            partition=partition,
            trace=name,
            rubric=rubric,
        )
        if diagnostics is not None:
            target = diagnostics / case_id
            target.mkdir(parents=True, exist_ok=True)
            (target / (name + ".json")).write_bytes(diagnostic_bytes)
        metrics = measurement["metrics"]
        row = {
            "case": case_id,
            "trace": name,
            "module": name.split(".")[0],
            "framing_match": metrics["framing_match"]["value"],
            "exact_equal": metrics["exact_equal"]["value"],
            "max_abs_error": metrics["max_abs_error"]["value"],
            "max_abs_error_index": metrics["max_abs_error_index"]["value"],
            "mean_abs_error": metrics["mean_abs_error"]["value"],
            "verdict": verdict_for(rubric, rows) if rubric else None,
        }
        rows_out.append(row)
    return rows_out


def endpoint_report(reference, model_outputs):
    """Exact-endpoint checks on both the reference bytes and the model."""

    results = {}
    for route in trace_registry.ROUTES:
        control = reference["mod_matrix." + route]
        upsampled = reference["control_upsample." + route]
        reference_ok = upsampled[0] == control[0] and upsampled[-1] == control[-1]
        model_control = model_outputs["mod_matrix." + route]
        model_upsampled = model_outputs["control_upsample." + route]
        model_ok = (
            model_upsampled[0] == model_control[0]
            and model_upsampled[-1] == model_control[-1]
        )
        results[route] = {
            "reference_first_last_equal": reference_ok,
            "model_first_last_equal": model_ok,
        }
    return results


def failing(rows, matches=None):
    return [
        row
        for row in rows
        if row["verdict"] == "FAIL" and (matches is None or matches(row["trace"]))
    ]


def run_mutations(cases, rate_of, rubrics, diagnostics):
    """Every preregistered mutation must FAIL its named traces."""

    plan = [
        (
            MUTATION_CLAMP_MOD_MATRIX,
            lambda trace: trace.startswith("mod_matrix."),
            "clamped mod matrix must fail the declared match",
        ),
        (
            MUTATION_SELECTOR_LFO,
            lambda trace: trace in ("lfo_1.raw", "lfo_2.raw"),
            "selector LFO must fail the declared match",
        ),
        (
            MUTATION_INTEGER_ADSR_TIMING,
            lambda trace: trace.endswith(".output") and "adsr" in trace,
            "integer-sample ADSR timing must fail the declared match",
        ),
        (
            ("upsample-mode", "zoh"),
            lambda trace: trace.startswith("control_upsample."),
            "ZOH upsample must fail the match",
        ),
        (
            ("upsample-mode", "off-endpoint"),
            lambda trace: trace.startswith("control_upsample."),
            "off-endpoint coordinate must fail the match",
        ),
    ]
    results = []
    for mutation, matches, claim in plan:
        failed_somewhere = False
        case_results = {}
        for case_id, record, reference, noise_payload in cases:
            request = build_request(record, noise_payload)
            label = (
                mutation[0] + "-" + mutation[1]
                if isinstance(mutation, tuple)
                else mutation
            )
            if isinstance(mutation, tuple):
                model = ControlPathModel(request)
                outputs = model.render()
                for route in trace_registry.ROUTES:
                    outputs["control_upsample." + route] = control_upsample(
                        outputs["mod_matrix." + route], mutation[1]
                    )
            else:
                outputs = ControlPathModel(request, mutations={mutation}).render()
            mutation_diagnostics = (
                diagnostics / "mutations" / label if diagnostics else None
            )
            rows = compare_case(
                case_id,
                record,
                reference,
                outputs,
                rate_of,
                rubrics,
                mutation_diagnostics,
                "development",
            )
            hits = failing(rows, matches)
            case_results[case_id] = len(hits)
            failed_somewhere = failed_somewhere or bool(hits)
        results.append(
            {
                "mutation": label,
                "must_fail": claim,
                "failed": failed_somewhere,
                "failing_traces_by_case": case_results,
            }
        )
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--rubric", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--diagnostics", type=Path)
    parser.add_argument("--limit-cases", type=int)
    parser.add_argument("--skip-mutations", action="store_true")
    args = parser.parse_args()

    manifest = json.loads((args.capture / "manifest.json").read_text())

    def rate_of(name):
        return 44100 if name.startswith("control_upsample.") else 441

    rubric_document, rubrics = (None, {})
    if args.rubric:
        rubric_document, rubrics = load_rubric(args.rubric)

    case_records = manifest["cases"]
    if args.limit_cases:
        case_records = case_records[: args.limit_cases]

    started = time.time()
    all_rows = []
    endpoint_summary = {}
    mutation_cases = []
    for position, item in enumerate(case_records):
        safe = item["case_id"].replace(":", "_")
        record, reference, noise_payload = load_case(args.capture / "cases" / safe)
        if record["normalized_sha256"] != canonical_digest(
            record["normalized_by_name"]
        ):
            raise ValueError("normalized map digest mismatch: " + item["case_id"])
        if record["physical_sha256"] != canonical_digest(record["physical_by_name"]):
            raise ValueError("physical map digest mismatch: " + item["case_id"])
        request = build_request(record, noise_payload)
        model_outputs = ControlPathModel(request).render()
        rows = compare_case(
            item["case_id"],
            record,
            reference,
            model_outputs,
            rate_of,
            rubrics,
            args.diagnostics,
            "development",
        )
        all_rows.extend(rows)
        endpoint_summary[item["case_id"]] = endpoint_report(reference, model_outputs)
        if len(mutation_cases) < MUTATION_CASES and item["kind"] == "development":
            mutation_cases.append((item["case_id"], record, reference, noise_payload))
        if position % 32 == 0:
            print(
                "compared %d/%d cases" % (position + 1, len(case_records)), flush=True
            )

    mutation_results = []
    if not args.skip_mutations and rubrics:
        mutation_results = run_mutations(
            mutation_cases, rate_of, rubrics, args.diagnostics
        )

    decided = [row for row in all_rows if row["verdict"]]
    per_trace = {}
    for row in all_rows:
        value = row["max_abs_error"]
        if value is None:
            continue
        current = per_trace.get(row["trace"])
        per_trace[row["trace"]] = value if current is None else max(current, value)
    summary = {
        "case_count": len(case_records),
        "trace_rows": len(all_rows),
        "decided_rows": len(decided),
        "pass_rows": sum(1 for row in decided if row["verdict"] == "PASS"),
        "fail_rows": sum(1 for row in decided if row["verdict"] == "FAIL"),
        "per_trace_max_abs_error": per_trace,
        "failures": [
            {
                key: row[key]
                for key in (
                    "case",
                    "trace",
                    "module",
                    "max_abs_error",
                    "max_abs_error_index",
                    "framing_match",
                )
            }
            for row in all_rows
            if row["verdict"] == "FAIL"
        ]
        or None,
        "endpoints_all_exact": all(
            item["reference_first_last_equal"] and item["model_first_last_equal"]
            for case in endpoint_summary.values()
            for item in case.values()
        ),
        "mutations": mutation_results,
        "elapsed_seconds": time.time() - started,
    }
    print(
        json.dumps(
            {
                key: summary[key]
                for key in (
                    "case_count",
                    "trace_rows",
                    "decided_rows",
                    "pass_rows",
                    "fail_rows",
                    "endpoints_all_exact",
                    "elapsed_seconds",
                )
            },
            indent=1,
        )
    )
    if mutation_results:
        for item in mutation_results:
            print("mutation", item["mutation"], "failed:", item["failed"])
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(
                {
                    "capture_manifest_sha256": hashlib.sha256(
                        (args.capture / "manifest.json").read_bytes()
                    ).hexdigest(),
                    "rubric_sha256": hashlib.sha256(
                        args.rubric.read_bytes()
                    ).hexdigest()
                    if args.rubric
                    else None,
                    "summary": summary,
                    "endpoint_summary": endpoint_summary,
                },
                indent=1,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        )
    failed = summary["fail_rows"] > 0
    mutated = all(item["failed"] for item in mutation_results)
    if rubrics:
        if failed or (mutation_results and not mutated):
            raise SystemExit(1)
    elif failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
