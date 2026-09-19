#!/usr/bin/env python3
"""Reproduce analytic qualification or revalidate committed producer evidence.

No Torch import/render, network access, holdout access, or producer writes.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import hashlib
import importlib.util
import io
import json
import math
import platform
import re
import struct
import subprocess
import sys
import tempfile
import uuid
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.identity import SoundIdentity
from torchsynth_voice.paired_metrics import (
    analytic_exactness_rubric,
    scorecard_rows,
)
from torchsynth_voice.preparation import (
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
    if kind == "repeatability":
        committed_bytes(root, commit, "env/release-era/source-comparison.json")
        for input_name in (
            "spec/reference/upstream.json",
            "spec/reference/parameter-inventory-v1.json",
        ):
            require(
                committed_bytes(root, commit, input_name)
                == (ROOT / input_name).read_bytes(),
                "producer differs from consumer source/input identity: " + input_name,
            )
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


SCALAR_MATH_PROFILE = {"ATEN_CPU_CAPABILITY": None, "MKL_CBWR": "COMPATIBLE"}


def named_bytes(values, names):
    require(
        isinstance(values, dict) and sorted(values) == names, "missing named inputs"
    )
    require(
        all(
            type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1
            for v in values.values()
        ),
        "invalid named normalized inputs",
    )
    return struct.pack("<" + "f" * len(names), *[values[n] for n in names])


def validate_scalar_records(report, plan, raw_root, names):
    """Independent input/role/process gates, before executing producer validators.

    Source bytes and normalized/noise inputs are identities. Physical conversion
    and subsequent DSP seams are observations, never substituted input values.
    """
    require(
        report["runtime"].get("math_environment") == SCALAR_MATH_PROFILE,
        "scalar math profile differs from explicit compatible diagnostic profile",
    )
    campaign = safe_read(raw_root, "campaign.id").decode().strip()
    require(campaign == report["campaign_id"], "scalar campaign mismatch")
    executions, hashes, runs, artifacts = [], {}, {}, {}

    def execution(run, side, repeat, directory, mutation=None):
        require(run.get("side") == side, "scalar execution role mismatch")
        value = run.get("execution", {})
        for key in ("uuid", "campaign_id"):
            require(
                str(uuid.UUID(value[key], version=4)) == value[key],
                "invalid scalar execution UUID",
            )
        started = datetime.datetime.fromisoformat(value["started_utc"])
        require(
            started.tzinfo is not None
            and started.utcoffset() == datetime.timedelta(0)
            and type(value["pid"]) is int
            and value["pid"] > 0,
            "invalid scalar process identity",
        )
        require(
            type(value["repeat"]) is int
            and value["repeat"] == repeat
            and value["campaign_id"] == campaign,
            "scalar execution association mismatch",
        )
        require(
            value["uuid"] not in {v["uuid"] for v in executions},
            "reused scalar execution identity",
        )
        require(run.get("mutation") == mutation, "scalar mutation association mismatch")
        record = dict(value, path=directory, side=side)
        if mutation:
            record["mutation"] = mutation
        executions.append(record)
        if side == "scalar":
            canonical_dir = f"run-{repeat}/canonical"
            require(
                run.get("canonical_execution_uuid")
                == runs[canonical_dir]["execution"]["uuid"]
                and run.get("canonical_report_sha256")
                == hashes[canonical_dir + "/report.json"],
                "scalar canonical report/UUID association mismatch",
            )

    def read_report(directory):
        data = safe_read(raw_root, directory + "/report.json")
        hashes[directory + "/report.json"] = sha(data)
        return strict_json(data)

    def read_trace(directory, trace, count):
        locator = directory + "/" + trace["file"]
        data = safe_read(raw_root, locator)
        require(
            len(data) == count * 4 and sha(data) == trace["sha256"],
            "scalar artifact hash/count mismatch",
        )
        require(
            all(math.isfinite(v[0]) for v in struct.iter_unpack("<f", data)),
            "nonfinite scalar artifact",
        )
        artifacts[locator] = sha(data)
        return data

    for repeat in (1, 2):
        for side in ("canonical", "scalar"):
            directory = f"run-{repeat}/{side}"
            run = read_report(directory)
            require(
                hashes[directory + "/report.json"]
                == report["run_report_sha256"][directory + "/report.json"],
                "scalar raw report hash mismatch",
            )
            execution(run, side, repeat, directory)
            for key in ("runtime", "provenance"):
                require(run.get(key) == report[key], "scalar run " + key + " mismatch")
            require(
                run.get("status") == "PASS"
                and run.get("rng_sentinel") == "PASS"
                and run.get("source_unchanged_after_render") is True
                and run.get("capture_version") == plan["capture_version"],
                "scalar render gates failed",
            )
            require(
                [c["id"] for c in run["cases"]] == [c["id"] for c in plan["cases"]],
                "scalar required case set mismatch",
            )
            for case, definition in zip(run["cases"], plan["cases"]):
                require(
                    case["case_definition"] == definition
                    and case["configuration"] == plan["configuration"]
                    and case["corpus_coordinates"]
                    == SoundIdentity(definition["sound_index"]).to_dict(32),
                    "scalar case/configuration/identity mismatch",
                )
                require(
                    type(case["execution_width"]) is int
                    and case["execution_width"] == (32 if side == "canonical" else 1)
                    and case["reproducible"] is (side == "canonical"),
                    "scalar execution configuration mismatch",
                )
                require(
                    case["parameter_order"] == names,
                    "scalar named parameter order mismatch",
                )
                require(
                    [{"name": t["name"], "shape": t["shape"]} for t in case["traces"]]
                    == report["capture_manifest"],
                    "scalar required trace/shape mismatch",
                )
                data = {
                    t["name"]: read_trace(directory, t, math.prod(t["shape"]))
                    for t in case["traces"]
                }
                require(
                    case.get("passive_capture_invariant") is True
                    and case.get("no_hook_audio_sha256") == sha(data["audio.final"]),
                    "scalar passive capture mismatch",
                )
                require(
                    data["input.normalized"]
                    == named_bytes(case["normalized_by_name"], names),
                    "scalar named input bytes mismatch",
                )
                if side == "scalar":
                    canonical = next(
                        c
                        for c in runs[f"run-{repeat}/canonical"]["cases"]
                        if c["id"] == case["id"]
                    )
                    require(
                        named_bytes(canonical["normalized_by_name"], names)
                        == data["input.normalized"],
                        "scalar normalized input mismatch",
                    )
                    noise = next(
                        t for t in canonical["traces"] if t["name"] == "input.noise"
                    )
                    require(
                        noise["sha256"] == sha(data["input.noise"]),
                        "scalar selected noise mismatch",
                    )
            runs[directory] = run
    require(set(report["run_report_sha256"]) == set(hashes), "extra scalar run reports")
    canonical_dir = "run-1/canonical"
    canonical = runs[canonical_dir]
    require(
        set(report["negative_controls"]) == set(plan["controls"]),
        "missing scalar controls",
    )
    for name, definition in plan["controls"].items():
        directory = "controls/" + name
        control = read_report(directory)
        execution(control, "scalar", 1, directory, name)
        recorded = report["negative_controls"][name]
        require(
            recorded["observed"] == control
            and recorded["detected"] is True
            and control["status"] == "NO_VERDICT"
            and control["error"].startswith(
                "ValueError: " + definition["expected_seam"] + ":"
            ),
            "scalar control outcome mismatch",
        )
        observed = control["control_observation"]
        for key in ("runtime", "provenance"):
            require(
                observed.get(key) == canonical[key],
                "scalar control " + key + " mismatch",
            )
        require(observed["case"] == definition["case"], "scalar control case mismatch")
        case = next(c for c in canonical["cases"] if c["id"] == definition["case"])
        expected = case["normalized_by_name"]
        require(
            named_bytes(observed["expected_normalized"], names)
            == named_bytes(expected, names),
            "scalar control expected named inputs mismatch",
        )
        actual = observed.get("actual_normalized", {})
        named_bytes(actual, names)
        changed = [
            n
            for n in names
            if struct.pack("<f", expected[n]) != struct.pack("<f", actual[n])
        ]
        noise = next(t for t in case["traces"] if t["name"] == "input.noise")
        original_noise = read_trace(canonical_dir, noise, math.prod(noise["shape"]))
        actual_noise = read_trace(
            directory, observed["actual_noise"], math.prod(noise["shape"])
        )
        slot = case["corpus_coordinates"]["noise_slot"]
        require(
            observed["expected_noise_sha256"] == sha(original_noise)
            and observed["actual_noise_sha256"] == sha(actual_noise)
            and observed["expected_noise_slot"] == slot,
            "scalar control noise binding mismatch",
        )
        if name == "wrong-noise":
            require(
                not changed
                and actual_noise != original_noise
                and observed["actual_noise_slot"] == 0
                and slot != 0,
                "scalar wrong-noise mutation not observed",
            )
            slot_zero = {
                t["sha256"]
                for c in canonical["cases"]
                if c["corpus_coordinates"]["noise_slot"] == 0
                for t in c["traces"]
                if t["name"] == "input.noise"
            }
            require(
                len(slot_zero) == 1 and sha(actual_noise) in slot_zero,
                "scalar control lacks actual canonical slot-zero noise identity",
            )
        else:
            require(
                actual_noise == original_noise
                and observed["actual_noise_slot"] == slot,
                "scalar control unchanged noise mismatch",
            )
            if name == "wrong-parameter":
                require(
                    changed == ["keyboard.midi_f0"]
                    and actual["keyboard.midi_f0"]
                    == (0.0 if expected["keyboard.midi_f0"] != 0 else 1.0),
                    "scalar wrong-parameter mutation not observed",
                )
            else:
                require(
                    name == "fresh-randomization" and len(changed) >= 2,
                    "scalar fresh-randomization mutation not observed",
                )
    require(
        executions == report["execution_records"],
        "scalar execution record bindings mismatch",
    )
    return {
        "execution_records": executions,
        "raw_report_sha256": hashes,
        "raw_artifact_sha256": artifacts,
    }


def replay_scalar_aggregate(runner, raw_root, report):
    """Producer aggregate writes only to a disposable consumer-owned directory."""
    with tempfile.TemporaryDirectory(prefix="preparation-scalar-") as name:
        stage = Path(name)
        for child in raw_root.iterdir():
            if child.name != "scalar-execution.json":
                require(
                    child.resolve().is_relative_to(raw_root.resolve()),
                    "nonlocal scalar aggregate input",
                )
                (stage / child.name).symlink_to(
                    child.resolve(), target_is_directory=child.is_dir()
                )
        with contextlib.redirect_stdout(io.StringIO()):
            runner.aggregate(stage, False)
        replayed = strict_json((stage / "scalar-execution.json").read_bytes())
    # These two fields describe the replay invocation, not the measured process.
    for key, value in replayed.items():
        if key not in ("recorded_utc", "host"):
            require(
                report.get(key) == value, "scalar aggregate does not reproduce: " + key
            )
    return {
        "status": "PASS",
        "compared_fields": sorted(set(replayed) - {"recorded_utc", "host"}),
    }


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
            for definition in plan["runtime_definitions"].values():
                name = definition["lock"]
                require(
                    sha(committed_bytes(root, commit, name))
                    == definition["lock_sha256"]
                    == sha((ROOT / name).read_bytes()),
                    "repeatability lock identity mismatch",
                )
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
            "evidence_status": "VALIDATED"
            if not [
                failure
                for failure in result.get("failures", [])
                if failure["key"] != "oracle_status"
            ]
            and "failures" in result
            else "REFUSED",
            "closure": "Root must reconcile runtime/scalar decisions before issue closure; this command never ratifies a DR.",
        }
    except (
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        IndexError,
        StopIteration,
        OverflowError,
        struct.error,
        OSError,
        subprocess.CalledProcessError,
    ) as error:
        return {
            "schema": "preparation-runtime-integration",
            "schema_version": 1,
            "status": "NO_VERDICT",
            "evidence_status": "REFUSED",
            "producer_commit": commit,
            "kind": kind,
            "reason": str(error),
            "closure": "Actual runtime acceptance remains pending; refusal is not completion.",
        }


def repeatability_projection(
    report, plan, runner, runner_hash, raw_root, expected, manifest
):
    require(
        plan.get("schema_version") == 2
        and plan.get("profile") == "release-mkl-compatible-v1"
        and plan.get("profile_environment")
        == {
            "release": SCALAR_MATH_PROFILE,
            "current": {"ATEN_CPU_CAPABILITY": None, "MKL_CBWR": None},
        },
        "repeatability math profile is missing, historical or mismatched",
    )
    raw_data = safe_read(raw_root, "repeatability-runtime.json")
    require(
        sha(raw_data) == report["raw_report_sha256"],
        "repeatability raw report hash mismatch",
    )
    native_report = strict_json(raw_data)
    # The producer publication omits bulky name maps. Rehydrate only from the
    # digest-bound actual report, and check the entire published cell projection.
    for key, value in native_report.items():
        if key == "cells":
            value = [
                {
                    k: v
                    for k, v in c.items()
                    if k not in ("normalized", "physical", "parameter_names", "inputs")
                }
                for c in value
            ]
        require(report.get(key) == value, "repeatability publication mismatch: " + key)
    report = native_report
    require(report["status"] == "PASS", "repeatability apparatus failed or refused")
    runner.validate_plan(plan)
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
    runner.validate_results(plan, report["cells"], raw_root)
    require(
        strict_json(safe_read(raw_root, "provenance.json")) == report["provenance"],
        "repeatability controller provenance mismatch",
    )
    require(
        strict_json(safe_read(raw_root, "preregistered-plan.json")) == plan,
        "repeatability raw preregistration mismatch",
    )
    # Hash/count-check and replay every original-byte comparison, including drift.
    require(
        runner.summarize(plan, report["cells"], raw_root) == report["comparisons"],
        "reported repeat/batch/divergence comparisons do not reproduce",
    )
    records = {r["directory"]: r for r in report["records"]}
    require(
        len(records)
        == len(report["records"])
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
    raw_reports = {}
    for directory, record in records.items():
        data = safe_read(raw_root, directory + "/execution.json")
        require(
            strict_json(data) == record,
            "repeatability controller execution receipt mismatch",
        )
        for filename in ("execution.json", "result.json", "stdout.json"):
            raw_reports[directory + "/" + filename] = sha(
                safe_read(raw_root, directory + "/" + filename)
            )
    require(
        set(report["controls"]) == set(plan["runtime_definitions"]),
        "repeatability source controls missing",
    )
    for runtime, control in report["controls"].items():
        result = control["result"]
        rejection = result["rejection"]
        command = control["command"]
        require(
            result["status"] == "PASS"
            and rejection["status"] == "NO_VERDICT"
            and rejection["torch_imported"] is False
            and rejection["error"]
            == "ValueError: source hash mismatch: torchsynth/config.py"
            and "negative" in command
            and command[command.index("--runtime") + 1] == runtime
            and command[command.index("--run-id") + 1] == report["run_id"],
            "repeatability source control association mismatch",
        )
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
        runtime["math_environment"] = plan["profile_environment"][request["runtime"]]
        wanted = {
            "key": key,
            "runtime": runtime,
            "execution_width": request["batch_size"],
            "reproducible": True,
            "case_definition": case,
            "sound_index": case["index"],
            "identity_batch_size": request["batch_size"],
        }
        expected["cells"].append(wanted)
        cell = dict(wanted, status=raw["status"], reason=raw.get("error", "rendered"))
        if raw["status"] == "PASS":
            worker = records[raw["directory"]]["worker"]
            actual_worker = strict_json(
                safe_read(raw_root, raw["directory"] + "/result.json")
            )
            require(
                worker == {k: v for k, v in actual_worker.items() if k != "cells"},
                "repeatability committed worker receipt mismatch",
            )
            require(
                worker["execution_id"] == raw["execution_id"]
                and worker["runner_sha256"] == runner_hash,
                "repeatability process/source association mismatch",
            )
            require(
                str(uuid.UUID(worker["execution_id"], version=4))
                == worker["execution_id"]
                and type(worker["process_id"]) is int
                and worker["process_id"] > 0
                and datetime.datetime.fromisoformat(worker["started_utc"]).tzinfo
                is not None,
                "repeatability process identity invalid",
            )
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
            inputs = {
                "index": case["index"],
                "physical_overrides": case["physical_overrides"],
                "configuration": plan["configuration"],
                "nebula": plan["nebula"],
                "noise_seed": plan["noise_seed"],
            }
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
        "diagnostic",
        {
            "native_status": report["status"],
            "scope": "preregistered 128-cell local matrix; runtime decision reconciliation pending",
            "comparisons": report["comparisons"],
            "raw_aggregate_sha256": sha(raw_data),
            "profile_environment": plan["profile_environment"],
            "bindings": {
                "raw_report_sha256": raw_reports,
                "raw_artifact_sha256": {
                    a["locator"]: a["sha256"]
                    for c in cells
                    for a in c.get("artifacts", {}).values()
                },
                "execution_records": [
                    {
                        "directory": directory,
                        **{
                            k: record["worker"].get(k)
                            for k in (
                                "execution_id",
                                "run_id",
                                "process_id",
                                "started_utc",
                            )
                        },
                    }
                    for directory, record in records.items()
                ],
            },
            "runtime_ratification": "pending reviewed producers and root DR-0006 reconciliation; no production oracle",
        },
    )


def scalar_projection(report, plan, runner, root, commit, raw_root, expected, manifest):
    require(
        report["status"] == "PASS" and report["scope"] == "full-preregistered-cases",
        "scalar report failed or sentinel-only",
    )
    require(
        report["batch_1_oracle"] == "diagnostic",
        "scalar diagnostic scope cannot promote a production oracle",
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
            report["provenance"]["definition_sha256"].get(name)
            == digest
            == sha((ROOT / name).read_bytes()),
            "scalar preregistered input mismatch",
        )
    for name in (
        "qualify_scalar.py",
        "qualify_scalar.sh",
        "scalar-cases.json",
        "requirements.lock",
        "probe.py",
        "Dockerfile",
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
    lock = committed_bytes(root, commit, "env/release-era/requirements.lock").decode()
    packages = {
        k.lower().replace("_", "-"): v
        for k, v in re.findall(
            r"^([\w-]+)(?:\[[^\]]+\])?==([^\s\\]+)", lock, re.MULTILINE
        )
    }
    packages["torch"] = "1.12.1+cpu"
    require(
        all(runtime["packages"].get(k) == v for k, v in packages.items()),
        "scalar package lock mismatch",
    )
    require(
        sha(json.dumps(runtime, sort_keys=True).encode()) == report["runtime_sha256"],
        "scalar runtime digest mismatch",
    )
    require(
        runtime["thread_environment"]
        == dict.fromkeys(
            (
                "OMP_NUM_THREADS",
                "MKL_NUM_THREADS",
                "OPENBLAS_NUM_THREADS",
                "VECLIB_MAXIMUM_THREADS",
            ),
            "1",
        ),
        "scalar thread environment mismatch",
    )
    require(
        sha(runner.json_bytes(report["provenance"]["package_sha256"]))
        == plan["package_tree_sha256"],
        "scalar source package byte identity mismatch",
    )
    require(
        "uv.lock" in report["provenance"]["definition_sha256"],
        "missing scalar lock identity",
    )
    for name in (
        "uv.lock",
        "env/release-era/requirements.lock",
        "env/release-era/Dockerfile",
        "env/release-era/probe.py",
    ):
        require(
            report["provenance"]["definition_sha256"][name]
            == sha((ROOT / name).read_bytes()),
            "scalar consumer source/lock mismatch: " + name,
        )
    raw_data = safe_read(raw_root, "scalar-execution.json")
    require(
        sha(raw_data) == report["profile_validation"]["local_full_report_sha256"],
        "scalar actual aggregate hash mismatch",
    )
    raw_aggregate = strict_json(raw_data)
    require(
        all(report.get(k) == v for k, v in raw_aggregate.items()),
        "scalar committed/current aggregate mismatch",
    )
    bindings = validate_scalar_records(
        report, plan, raw_root, expected["parameter_names"]
    )
    aggregate = replay_scalar_aggregate(runner, raw_root, report)
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
                wanted = {
                    "key": key,
                    "runtime": runtime,
                    "execution_width": 32 if side == "canonical" else 1,
                    "reproducible": side == "canonical",
                    "case_definition": case,
                    "sound_index": case["sound_index"],
                    "identity_batch_size": 32,
                }
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
                    raw["passive_capture_invariant"] is True
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
                        process_identity=run["execution"]["uuid"],
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
    return (
        cells,
        report["batch_1_oracle"],
        {
            "raw_comparisons_reproduced": True,
            "scope": "full twelve-case local scalar diagnostic only; no native raw revalidation or production oracle",
            "measured_host": report["host"],
            "aggregate_replay": aggregate,
            "bindings": bindings,
            "raw_aggregate_sha256": sha(raw_data),
            "math_environment": runtime["math_environment"],
            "cases_per_run": len(plan["cases"]),
            "seams_per_case": len(runner.capture_manifest()),
            "fresh_process_identity_present": all(c["process_identity"] for c in cells),
            "oracle_status": report["batch_1_oracle"],
            "byte_equivalence": report["byte_equivalence"],
            "comparisons": report["comparisons"],
            "runtime_reconciliation": report["runtime_reconciliation"],
        },
    )


def integration_receipt(result):
    """Bounded receipt; hashes bind the complete observations retained in raw data."""
    control = result.get("control", {})
    diagnostics = control.get("diagnostics", {})
    bindings = diagnostics.get("bindings", {})
    counts = {}
    for comparison in diagnostics.get("comparisons", []):
        key = (
            comparison.get("kind", "scalar")
            + "/"
            + comparison.get("status", comparison.get("byte_equivalence", "UNKNOWN"))
        )
        counts[key] = counts.get(key, 0) + 1
    return {
        "schema": "preparation-runtime-receipt",
        "schema_version": 1,
        "status": result["status"],
        "evidence_status": result["evidence_status"],
        "evidence_category": control.get("evidence_category", "unavailable"),
        "normative_oracle": control.get("normative_oracle", False),
        "reason": result.get("reason", control.get("reason")),
        "producer": result.get(
            "producer",
            {"commit": result.get("producer_commit"), "kind": result.get("kind")},
        ),
        "consumer_sha256": {
            name: sha((ROOT / name).read_bytes())
            for name in (
                "tools/qualify_preparation.py",
                "src/torchsynth_voice/preparation.py",
            )
        },
        "raw_aggregate_sha256": diagnostics.get("raw_aggregate_sha256"),
        "raw_report_sha256": bindings.get("raw_report_sha256", {}),
        "raw_artifact_count": len(bindings.get("raw_artifact_sha256", {})),
        "raw_artifact_manifest_sha256": sha(
            record_bytes(bindings.get("raw_artifact_sha256", {}))
        ),
        "execution_records": bindings.get("execution_records", []),
        "cases_per_run": diagnostics.get("cases_per_run"),
        "scope": diagnostics.get("scope", "unavailable evidence"),
        "measured_host": diagnostics.get("measured_host"),
        "comparison_count": len(diagnostics.get("comparisons", [])),
        "comparison_outcomes": counts,
        "seams_per_case": diagnostics.get("seams_per_case"),
        "byte_equivalence": diagnostics.get("byte_equivalence"),
        "math_environment": diagnostics.get(
            "math_environment", diagnostics.get("profile_environment")
        ),
        "aggregate_replay": diagnostics.get("aggregate_replay"),
        "comparison_records_sha256": sha(
            record_bytes(diagnostics.get("comparisons", []))
        ),
        "closure": result["closure"],
    }


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
    integration.add_argument(
        "--record",
        type=Path,
        help="append a bounded actual receipt to an existing analytic qualification",
    )
    integration.add_argument(
        "--summary",
        action="store_true",
        help="print the bounded receipt instead of full comparisons",
    )
    args = parser.parse_args(argv)
    if args.command == "integrate":
        result = integration_report(
            args.producer_root.resolve(),
            args.producer_commit,
            args.kind,
            args.raw_root.resolve(),
        )
        receipt = integration_receipt(result)
        if args.record:
            qualification = strict_json(args.record.read_bytes())
            require(
                qualification.get("schema") == "preparation-qualification"
                and qualification.get("evidence_category") == "analytic-only",
                "integration receipt requires an analytic qualification container",
            )
            qualification.setdefault("actual_integrations", []).append(receipt)
            args.record.write_bytes(record_bytes(qualification))
        print(record_bytes(receipt if args.summary else result).decode(), end="")
        return 0 if result["status"] == "PASS" else 2
    report = analytic_report()
    encoded = record_bytes(report)
    stored = strict_json(args.check.read_bytes()) if args.check else None
    if stored is not None:
        stored.pop(
            "actual_integrations", None
        )  # Independent evidence, never analytic credit.
    if stored is not None and record_bytes(stored) != encoded:
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
