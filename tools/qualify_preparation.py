#!/usr/bin/env python3
"""Reproduce analytic qualification or revalidate committed producer evidence.

No Torch import/render, network access, holdout access, or producer writes.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import platform
from pathlib import Path
import struct
import subprocess
import sys
from dataclasses import replace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.identity import SoundIdentity  # noqa: E402
from torchsynth_voice.paired_metrics import analytic_exactness_rubric, scorecard_rows  # noqa: E402
from torchsynth_voice.preparation import (  # noqa: E402
    TRANSFORMS,
    Preparation,
    Signal,
    compare_exact,
    gate_rows,
    invariant_trial,
    prepare,
    prepare_pair,
    record_bytes,
    resample_diagnostic,
    runtime_evidence_control,
    symmetry_control,
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key: " + key)
            result[key] = value
        return result

    def invalid(value):
        raise ValueError("nonfinite JSON number: " + value)

    result = json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)
    record_bytes(result)  # Also rejects overflowing JSON exponents.
    return result


def analytic_report():
    """Dyadic boundary/control signals plus explicit analytic interpolation grid."""
    exact = Preparation("analytic-paired", "1", "exact", "amplitude")
    prop = Preparation(
        "analytic-ratio",
        "1",
        "property",
        "amplitude",
        time_origin="onset",
        window=(0, 3),
        allowed=("window",),
        forbidden=tuple(t for t in TRANSFORMS if t != "window"),
        invariances=("onset_shift", "silence_padding", "common_gain"),
        measure_kind="ratio",
        gain_domain=(0.25, 4.0),
    )
    artifacts, checks = {}, []

    def keep(value):
        data = record_bytes(value)
        digest = sha(data)
        artifacts[digest] = value
        return digest

    def check(name, passed, **details):
        checks.append({"name": name, "status": "pass" if passed else "fail", **details})

    for amplitude in (1.0, 2**-40):
        for onset in (0, 1, 4):
            signal = Signal(
                [0] * onset + [amplitude, amplitude / 2, 0],
                44100,
                "amplitude",
                onset_sample=onset,
            )
            for window in ((0, 3), (0, 1), (2, 3), (0, 4), (-1, 2)):
                prepared = prepare(signal, replace(prop, window=window))
                expected_valid = onset + window[0] >= 0 and onset + window[1] <= len(
                    signal.samples
                )
                check(
                    "window-boundary",
                    (prepared["status"] == "valid") == expected_valid,
                    amplitude=amplitude,
                    onset=onset,
                    window=window,
                    artifact=keep(prepared),
                )
    reference = Signal([0, 1, 2, 1, 0], 44100, "amplitude", onset_sample=1)
    candidate = replace(reference, samples=[0, 2, 4, 2, 0])

    def ratio(pair):
        return [sum(pair[1]["prepared_samples"]) / sum(pair[0]["prepared_samples"])]

    for kind, amounts in (
        ("onset_shift", (-1, 0, 3)),
        ("silence_padding", (0, 4)),
        ("common_gain", (0.25, 1, 4)),
    ):
        for amount in amounts:
            trial = invariant_trial(
                reference, candidate, prop, ratio, kind=kind, amount=amount
            )
            check(kind, trial["status"] == "pass", amount=amount, artifact=keep(trial))
    for kind, amount, spec in (
        ("common_gain", 0, prop),
        ("common_gain", -1, prop),
        ("common_gain", 2, replace(prop, measure_kind="absolute")),
        ("onset_shift", -2, prop),
        ("onset_shift", 1, exact),
    ):
        trial = invariant_trial(
            reference, candidate, spec, ratio, kind=kind, amount=amount
        )
        check(
            "inapplicable-domain",
            trial["status"] == "not_applicable",
            artifact=keep(trial),
        )
    pair = prepare_pair(reference, candidate, exact)
    swapped = prepare_pair(candidate, reference, exact)
    control = symmetry_control(pair, swapped)
    check("symmetry", control["status"] == "pass", artifact=keep(control))
    mutant = prepare_pair(
        reference, replace(candidate, samples=list(reversed(candidate.samples))), exact
    )
    # Use a non-palindromic perturbation whose mean remains identical.
    mutant[1]["prepared_samples"] = [0, 4, 2, 2, 0]
    control = symmetry_control(mutant, swapped)
    check(
        "asymmetric-mutant-detected",
        control["status"] == "fail",
        artifact=keep(control),
    )
    equal = compare_exact(reference, reference, exact, spectral=False)
    rows, raw = scorecard_rows(
        equal["comparison"],
        case_id="analytic-preparation",
        partition="development",
        trace="synthetic-output",
        rubric=analytic_exactness_rubric(),
    )
    gated, diagnostic = gate_rows(
        rows, controls=[control], preparation=pair, raw_diagnostic=raw
    )
    check(
        "failed-invariant-gates-scorecard",
        all(r["verdict"] == "NO VERDICT" and r["observed"] is None for r in gated),
        diagnostic_sha256=sha(diagnostic),
        diagnostic=keep(strict_json(diagnostic)),
        rows=keep(gated),
    )
    for transform in TRANSFORMS:
        refused = prepare(reference, exact, operations=(transform,))
        check(
            "exact-transform-refusal",
            refused["status"] == "refused",
            transform=transform,
            artifact=keep(refused),
        )
    for samples in ([0, 0, 1, 2, 1], [0, 2, 4, 2, 0]):
        comparison = compare_exact(
            reference, replace(reference, samples=samples), exact, spectral=False
        )
        check(
            "primary-timing-gain-visible",
            comparison["comparison"]["metrics"]["exact_equal"]["value"] == 0,
            preparation=keep(comparison["preparation"]),
        )
    varying = compare_exact(
        Signal([0] * 8, 44100, "amplitude"),
        Signal([1] * 4 + [-1] * 4, 44100, "amplitude"),
        exact,
        window_samples=4,
    )
    metrics = varying["comparison"]["metrics"]
    windows = [metrics[f"window.{n}.mean_error"]["value"] for n in (0, 1)]
    check(
        "non-collapsing-windows",
        windows == [1, -1] and metrics["mean_error"]["value"] == 0,
        window_means=windows,
        whole_mean=metrics["mean_error"]["value"],
        preparation=keep(varying["preparation"]),
    )
    for rate in (8000, 16000, 44100, 48000):
        for divisor, amplitude, phase in (
            (32, 1.0, 0.0),
            (64, 0.5, 0.5),
            (128, 2**-40, 1.0),
        ):
            frequency = rate / divisor
            signal = Signal(
                [
                    amplitude * math.cos(2 * math.pi * i / divisor + phase)
                    for i in range(33)
                ],
                rate,
                "amplitude",
            )
            prepared = resample_diagnostic(
                signal,
                target_rate_hz=2 * rate,
                max_frequency_hz=frequency,
                amplitude_bound=amplitude,
            )
            error = max(
                abs(x - amplitude * math.cos(2 * math.pi * i / (2 * divisor) + phase))
                for i, x in enumerate(prepared["prepared_samples"])
            )
            check(
                "resampling-analytic-bound",
                error <= prepared["error_bound"],
                frequency_hz=frequency,
                measured_max_error=error,
                error_bound=prepared["error_bound"],
                artifact=keep(prepared),
            )
    return {
        "schema": "preparation-qualification",
        "schema_version": 1,
        "evidence_category": "analytic-only",
        "runtime": {
            "python": platform.python_version(),
            "arithmetic": "stdlib binary64 and host libm",
        },
        "partition": "development",
        "source_target": strict_json(
            (ROOT / "spec/reference/upstream.json").read_bytes()
        )["target_commit"],
        "implementation_sha256": {
            name: sha((ROOT / name).read_bytes())
            for name in (
                "src/torchsynth_voice/preparation.py",
                "tools/qualify_preparation.py",
            )
        },
        "status": "PASS" if all(c["status"] == "pass" for c in checks) else "FAIL",
        "checks": checks,
        "artifacts": artifacts,
        "runtime_integration": {
            "status": "PENDING",
            "reason": "Analytic record does not establish actual #12/#88 runtime invariants. Run integrate against committed producers and actual raw artifacts; root coordinates closure.",
        },
    }


def committed_bytes(root, commit, name):
    """Refuse dirty/untracked producer inputs; never execute a draft runner."""
    result = subprocess.run(
        ["git", "-C", str(root), "show", f"{commit}:{name}"],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise ValueError(f"missing committed producer file: {commit}:{name}")
    data = result.stdout
    if (root / name).read_bytes() != data:
        raise ValueError("uncommitted producer file: " + name)
    return data


def safe_read(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("artifact path escapes supplied raw root")
    return path.read_bytes()


def load_producer(root, commit, kind):
    name = f"env/release-era/qualify_{kind}.py"
    runner = committed_bytes(root, commit, name)
    committed_bytes(root, commit, "env/release-era/probe.py")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root / "env/release-era"))
    spec = importlib.util.spec_from_file_location(
        "preparation_producer_" + kind, root / name
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, sha(runner)


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def integration_report(root, commit, kind, raw_root):
    """Read committed native reports and re-run their artifact comparisons."""
    try:
        require(
            len(commit) == 40 and all(c in "0123456789abcdef" for c in commit),
            "producer commit must be an exact 40-digit SHA",
        )
        stem = (
            "repeatability-runtime" if kind == "repeatability" else "scalar-execution"
        )
        path = f"sim/reference/{stem}.json"
        data = committed_bytes(root, commit, path)
        report = strict_json(data)
        require(report["schema_version"] == 1, "unsupported native report version")
        runner, runner_hash = load_producer(root, commit, kind)
        plan_name = (
            "repeatability-matrix" if kind == "repeatability" else "scalar-cases"
        )
        plan_data = committed_bytes(root, commit, f"env/release-era/{plan_name}.json")
        plan = strict_json(plan_data)
        manifest = strict_json((ROOT / "spec/reference/upstream.json").read_bytes())
        require(
            plan["source_commit"] == manifest["target_commit"], "wrong source target"
        )
        require(
            plan["configuration"]["sample_rate"] == 44100
            and plan["configuration"]["control_rate"] == 441
            and plan["configuration"]["buffer_size_seconds"] == 4.0,
            "producer configuration is not the canonical four-second profile",
        )
        names = sorted(
            p["name"]
            for p in strict_json(
                (ROOT / "spec/reference/parameter-inventory-v1.json").read_bytes()
            )["parameters"]
        )
        source = {"commit": manifest["target_commit"], "files": manifest["files"]}
        producer = {
            "commit": commit,
            "report_path": path,
            "report_sha256": sha(data),
            "runner_sha256": runner_hash,
        }
        decision_path = (
            "spec/decision-records/0006-canonical-runtime.md"
            if kind == "repeatability"
            else "spec/decision-records/0007-single-sound-execution.md"
        )
        producer["decision_record"] = {
            "path": decision_path,
            "sha256": sha(committed_bytes(root, commit, decision_path)),
        }
        expected = {
            "producer": producer,
            "source": source,
            "configuration_sha256": sha(plan_data),
            "parameter_names": names,
            "cells": [],
            "repeat_groups": [],
            "equality_groups": [],
        }
        if kind == "repeatability":
            cells, oracle, diagnostics = repeatability_projection(
                report, plan, runner, runner_hash, raw_root, expected, manifest
            )
        else:
            cells, oracle, diagnostics = scalar_projection(
                report, plan, runner, root, commit, raw_root, expected, manifest
            )
        evidence = {
            "schema_version": 1,
            "evidence_category": "actual",
            "producer": producer,
            "source": source,
            "configuration_sha256": sha(plan_data),
            "cells": cells,
            "oracle_status": oracle,
            "diagnostics": diagnostics,
        }
        result = runtime_evidence_control(
            evidence, expected, lambda name: safe_read(raw_root, name)
        )
        return {
            "schema": "preparation-runtime-integration",
            "schema_version": 1,
            "producer": producer,
            "control": result,
            "status": "PASS" if result["status"] == "pass" else "NO_VERDICT",
            "closure": "Root must reconcile runtime/scalar decisions before issue closure; this command never ratifies a DR.",
        }
    except (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        subprocess.CalledProcessError,
    ) as error:
        return {
            "schema": "preparation-runtime-integration",
            "schema_version": 1,
            "status": "NO_VERDICT",
            "producer_commit": commit,
            "kind": kind,
            "reason": str(error),
            "closure": "Actual runtime acceptance remains pending; refusal is not completion.",
        }


def repeatability_projection(
    report, plan, runner, runner_hash, raw_root, expected, manifest
):
    runner.validate_plan(plan)
    runner.validate_results(plan, report["cells"])
    require(
        plan["sample_count"] == 176400
        and plan["control_sample_count"] == 1764
        and plan["nebula"] == "default"
        and plan["noise_seed"] == 13
        and plan["configuration"]["reproducible"] is True,
        "repeatability profile mismatch",
    )
    require(report["source_commit"] == plan["source_commit"], "report source mismatch")
    require(
        report["provenance"]["runner_sha256"] == runner_hash,
        "stale producer implementation",
    )
    require(report["plan_sha256"] == runner.plan_hash(plan), "stale report plan")
    for cell in report["cells"]:
        for artifact in cell.get("artifacts", {}).values():
            require(
                (raw_root / cell["directory"] / artifact["file"])
                .resolve()
                .is_relative_to(raw_root.resolve()),
                "nonlocal producer artifact",
            )
    # Hash/count-check and replay every original-byte comparison, including drift.
    require(
        runner.summarize(plan, report["cells"], raw_root) == report["comparisons"],
        "reported repeat/batch/divergence comparisons do not reproduce",
    )
    records = {r["directory"]: r for r in report["records"]}
    require(
        len(records)
        == len(plan["runtime_definitions"])
        * len(plan["batch_sizes"])
        * len(plan["repeats"]),
        "incomplete fresh-process records",
    )
    executions = [
        r["worker"]["execution_id"]
        for r in records.values()
        if r.get("worker", {}).get("execution_id")
    ]
    require(len(set(executions)) == len(executions), "worker execution identity reused")
    expected["artifacts"] = {
        name: plan["control_sample_count"]
        if name.startswith(("adsr", "lfo"))
        else plan["sample_count"]
        for name in plan["seams"]
    }
    expected["artifacts"].update(normalized=78, physical=78)
    expected["equal_artifacts"] = list(expected["artifacts"])
    cells = []
    native = {runner.cell_key(c): c for c in report["cells"]}
    for request in runner.expected_cells(plan):
        raw = native[runner.cell_key(request)]
        case = next(c for c in plan["cases"] if c["name"] == request["case"])
        key = "/".join(map(str, runner.cell_key(request)))
        definition = plan["runtime_definitions"][request["runtime"]]
        runtime = {
            k: definition[k]
            for k in ("python", "torch", "numpy", "lightning", "lock_sha256")
        }
        wanted = dict(
            key=key,
            runtime=runtime,
            execution_width=request["batch_size"],
            reproducible=True,
            case_definition=case,
            sound_index=case["index"],
            identity_batch_size=request["batch_size"],
        )
        expected["cells"].append(wanted)
        cell = dict(wanted, status=raw["status"], reason=raw.get("error", "rendered"))
        if raw["status"] == "PASS":
            worker = records[raw["directory"]]["worker"]
            require(worker["run_id"] == report["run_id"], "stale fresh-process worker")
            require(
                all(
                    worker["source_sha256"].get(k) == v
                    for k, v in manifest["files"].items()
                ),
                "worker source hash mismatch",
            )
            require(
                worker["source_validated_before_import"]
                and worker["rng_sentinel"] == "PASS",
                "worker preflight failed",
            )
            require(
                worker["runtime"]["threads"]
                == worker["runtime"]["interop_threads"]
                == 1,
                "unexpected CPU threads",
            )
            require(
                raw["identity"]
                == runner.coordinates(case["index"], request["batch_size"]),
                "incorrect native global coordinates",
            )
            identity = SoundIdentity(case["index"]).to_dict(request["batch_size"])
            require(
                raw["parameter_names"] == expected["parameter_names"],
                "named parameter order mismatch",
            )
            require(
                raw["label_byte_hex"] == bytes([int(identity["is_train"])]).hex(),
                "incorrect train/test label",
            )
            inputs = dict(
                index=case["index"],
                physical_overrides=case["physical_overrides"],
                configuration=plan["configuration"],
                nebula=plan["nebula"],
                noise_seed=plan["noise_seed"],
            )
            require(
                raw["inputs"] == inputs
                and raw["input_sha256"] == runner.sha256(runner.json_bytes(inputs)),
                "incorrect named case configuration",
            )
            cell.update(
                identity=identity,
                runtime={k: worker["runtime"][k] for k in runtime},
                normalized=raw["normalized"],
                physical=raw["physical"],
                process_identity=worker["execution_id"],
                artifacts={
                    k: {
                        "sha256": v["sha256"],
                        "locator": raw["directory"] + "/" + v["file"],
                    }
                    for k, v in raw["artifacts"].items()
                },
            )
        cells.append(cell)
    for runtime in plan["runtime_definitions"]:
        for case in plan["cases"]:
            name = case["name"]
            for size in plan["batch_sizes"]:
                expected["repeat_groups"].append(
                    [f"{runtime}/{size}/{repeat}/{name}" for repeat in plan["repeats"]]
                )
            expected["equality_groups"].append(
                [f"{runtime}/{size}/1/{name}" for size in plan["batch_sizes"]]
            )
    # This checks invariance only, not ratification of canonical runtime/host scope.
    return (
        cells,
        "normative",
        {
            "native_status": report["status"],
            "comparisons": report["comparisons"],
            "runtime_ratification": "separate DR-0006 operator decision",
        },
    )


def scalar_projection(report, plan, runner, root, commit, raw_root, expected, manifest):
    require(
        report["status"] == "PASS" and report["scope"] == "full-preregistered-cases",
        "scalar report failed or sentinel-only",
    )
    require(
        report["provenance"]["source_commit"] == manifest["target_commit"],
        "scalar source target mismatch",
    )
    require(
        all(
            report["provenance"]["source_sha256"].get(k) == v
            for k, v in manifest["files"].items()
        ),
        "scalar source hash mismatch",
    )
    for name, digest in report["provenance"]["definition_sha256"].items():
        require(
            sha(committed_bytes(root, commit, name)) == digest,
            "stale scalar definition: " + name,
        )
    for name, digest in plan["input_sha256"].items():
        require(
            report["provenance"]["definition_sha256"].get(name) == digest,
            "scalar preregistered input mismatch",
        )
    for name in (
        "qualify_scalar.py",
        "qualify_scalar.sh",
        "scalar-cases.json",
        "requirements.lock",
    ):
        require(
            "env/release-era/" + name in report["provenance"]["definition_sha256"],
            "missing scalar definition identity",
        )
    require(
        report["fresh_render_processes"] == 4
        and report["fresh_process_repeats_equal"] is True,
        "missing scalar fresh repeats",
    )
    require(
        report["capture_manifest"]
        == [{"name": n, "shape": s} for n, s in runner.capture_manifest()],
        "scalar capture manifest mismatch",
    )
    runtime = report["runtime"]
    require(
        runtime["python"] == "3.9.13"
        and runtime["machine"] == "x86_64"
        and runtime["device"] == "cpu"
        and runtime["dtype"] == "float32"
        and runtime["lock_versions_verified"] is True,
        "scalar locked runtime mismatch",
    )
    require(
        runtime["threads"] == runtime["interop_threads"] == 1, "scalar thread mismatch"
    )
    require(
        runtime["packages"]["torch"] == "1.12.1+cpu", "scalar Torch runtime mismatch"
    )
    runs = [
        runner.compare_run(
            raw_root / f"run-{n}/canonical", raw_root / f"run-{n}/scalar"
        )
        for n in (1, 2)
    ]
    require(
        runs[0][2] == report["comparisons"],
        "scalar first-divergence records do not reproduce",
    )
    aliases = {
        "input.normalized": "normalized",
        "physical.parameters": "physical",
        "input.noise": "noise",
        "audio.final": "audio",
    }
    expected["artifacts"] = {
        aliases.get(n, n): math.prod(s) for n, s in runner.capture_manifest()
    }
    expected["equal_artifacts"] = ["normalized", "noise"]
    cells = []
    for repeat, runspec in enumerate(runs, 1):
        for side_index, side in enumerate(("canonical", "scalar")):
            run = runspec[side_index]
            directory = f"run-{repeat}/{side}"
            raw_report = safe_read(raw_root, directory + "/report.json")
            require(
                sha(raw_report)
                == report["run_report_sha256"][directory + "/report.json"],
                "scalar raw report hash mismatch",
            )
            require(
                run["provenance"] == report["provenance"] and run["runtime"] == runtime,
                "scalar run provenance mismatch",
            )
            require(
                run["rng_sentinel"] == "PASS"
                and run["source_unchanged_after_render"] is True,
                "scalar run preflight failed",
            )
            require(
                [c["id"] for c in run["cases"]] == [c["id"] for c in plan["cases"]],
                "scalar required case set mismatch",
            )
            for raw, case in zip(run["cases"], plan["cases"]):
                key = f"{repeat}/{side}/{case['id']}"
                wanted = dict(
                    key=key,
                    runtime=runtime,
                    execution_width=32 if side == "canonical" else 1,
                    reproducible=side == "canonical",
                    case_definition=case,
                    sound_index=case["sound_index"],
                    identity_batch_size=32,
                )
                expected["cells"].append(wanted)
                require(
                    raw["configuration"] == plan["configuration"],
                    "scalar configuration mismatch",
                )
                artifacts = {
                    aliases.get(t["name"], t["name"]): {
                        "sha256": t["sha256"],
                        "locator": directory + "/" + t["file"],
                    }
                    for t in raw["traces"]
                }
                physical = [
                    x[0]
                    for x in struct.iter_unpack(
                        "<f", safe_read(raw_root, artifacts["physical"]["locator"])
                    )
                ]
                require(
                    raw["parameter_order"] == expected["parameter_names"],
                    "scalar parameter order mismatch",
                )
                require(
                    raw["selected_noise_sha256"] == artifacts["noise"]["sha256"],
                    "scalar selected noise mismatch",
                )
                require(
                    raw["passive_capture_invariant"]
                    and raw["no_hook_audio_sha256"] == artifacts["audio"]["sha256"],
                    "scalar passive capture mismatch",
                )
                cells.append(
                    dict(
                        wanted,
                        status="PASS",
                        identity=raw["corpus_coordinates"],
                        execution_width=raw["execution_width"],
                        reproducible=raw["reproducible"],
                        case_definition=raw["case_definition"],
                        normalized=raw["normalized_by_name"],
                        physical=dict(zip(raw["parameter_order"], physical)),
                        artifacts=artifacts,
                        process_identity=run.get("execution_id"),
                    )
                )
    for case in plan["cases"]:
        for side in ("canonical", "scalar"):
            expected["repeat_groups"].append(
                [f"{n}/{side}/{case['id']}" for n in (1, 2)]
            )
        for n in (1, 2):
            expected["equality_groups"].append(
                [f"{n}/{side}/{case['id']}" for side in ("canonical", "scalar")]
            )
    for name, definition in plan["controls"].items():
        control = strict_json(safe_read(raw_root, f"controls/{name}/report.json"))
        recorded = report["negative_controls"][name]
        require(
            control == recorded["observed"]
            and recorded["detected"] is True
            and control["status"] == "NO_VERDICT"
            and control["error"].startswith(
                "ValueError: " + definition["expected_seam"] + ":"
            ),
            "scalar negative control does not reproduce: " + name,
        )
    return (
        cells,
        report["batch_1_oracle"],
        {
            "raw_comparisons_reproduced": True,
            "cases_per_run": len(plan["cases"]),
            "seams_per_case": len(runner.capture_manifest()),
            "fresh_process_identity_present": all(c["process_identity"] for c in cells),
            "oracle_status": report["batch_1_oracle"],
            "byte_equivalence": report["byte_equivalence"],
            "comparisons": report["comparisons"],
            "runtime_reconciliation": report["runtime_reconciliation"],
        },
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    analytic = sub.add_parser("analytic")
    analytic.add_argument("--check", type=Path)
    analytic.add_argument("--output", type=Path)
    integration = sub.add_parser("integrate")
    integration.add_argument("--producer-root", type=Path, required=True)
    integration.add_argument("--producer-commit", required=True)
    integration.add_argument(
        "--kind", choices=("repeatability", "scalar"), required=True
    )
    integration.add_argument("--raw-root", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "integrate":
        result = integration_report(
            args.producer_root.resolve(),
            args.producer_commit,
            args.kind,
            args.raw_root.resolve(),
        )
        print(record_bytes(result).decode(), end="")
        return 0 if result["status"] == "PASS" else 2
    report = analytic_report()
    encoded = record_bytes(report)
    if args.check and args.check.read_bytes() != encoded:
        print(
            "FAIL: analytic qualification differs from committed bytes", file=sys.stderr
        )
        return 1
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(encoded)
    print(
        f"{report['status']}: {len(report['checks'])} analytic controls; sha256={sha(encoded)}; actual runtime integration PENDING"
    )
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
