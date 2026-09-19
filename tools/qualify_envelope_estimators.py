"""Execute the preregistered development grid; never render Voice or open holdout."""

from __future__ import annotations

import argparse
import base64
import hashlib
import itertools
import json
import math
import platform
import random
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.directed import resolve_patch, validate_manifest
from torchsynth_voice.envelope_estimators import (
    DESTINATIONS,
    UNITS,
    broadband_amplitude,
    estimate_envelope,
    estimate_routes,
    preparation_adapter,
    qualification_rows,
    tone_amplitude,
)
from torchsynth_voice.paired_metrics import Limit
from torchsynth_voice.scorecard import make_report, validate_report

SOURCE = "spec/ENVELOPE-ESTIMATORS.md: preregistered experiment v1; independent constructed truth"
OUTPUT = ROOT / "sim/qualification/envelope-routes-v1.json"
REPLAY_PRECISION = 1e-9
INPUT_REPLAY_PRECISION = 1e-12
REPLAY_POLICY = {
    "version": "2",
    "analytic_truth_and_observation_abs_rel": REPLAY_PRECISION,
    "generated_input_abs_rel": INPUT_REPLAY_PRECISION,
    "input_encoding": "base64 of original little-endian binary64 bytes; exact SHA-256 and count",
    "identity": "exact fixture/configuration/case/property/obligation identity; estimator limits unchanged",
}


def encoded(value):
    return (
        json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode()


def constructed_envelope(
    *,
    attack,
    decay,
    release,
    sustain,
    alpha,
    note,
    gain=1,
    count=180,
    origin=0,
    epsilon=1e-6,
):
    """Truth-only forward construction in continuous sample coordinates.

    This is not called by the estimator. It directly samples the pinned ramp
    equations, including compressed stages and the zero inverse special case.
    """
    attack_eff = min(attack, note)
    decay_eff = min(max(note - attack, 0), decay)

    def ramp(t, start, duration, inverse=False):
        if duration == 0:
            return 1.0
        fraction = min((max(t - start, 0) + epsilon) / duration + epsilon, 1.0)
        return ((1 - fraction) if inverse else fraction) ** alpha

    values = [
        gain
        * ramp(i + origin, 0, attack_eff)
        * (sustain + (1 - sustain) * ramp(i + origin, attack_eff, decay_eff, True))
        * ramp(i + origin, note, release, True)
        for i in range(count)
    ]
    peak = (
        gain
        * (sustain + (1 - sustain) * ramp(0, attack_eff, decay_eff, True))
        * ramp(0, note, release, True)
    )
    plateau = gain * (sustain if decay_eff > 0 else 1) * ramp(0, note, release, True)
    return values, {
        "attack_end": attack_eff,
        "decay_end": attack_eff + decay_eff,
        "release_end": note + release,
        "peak_amplitude": peak,
        "sustain_amplitude": plateau,
    }


def envelope_limits(truth):
    return {
        name: Limit(
            value,
            0.02 if name.endswith("end") else 2e-5,
            "control_sample" if name.endswith("end") else "1",
            SOURCE,
        )
        for name, value in truth.items()
    }


def envelope_cases():
    base = {
        "attack": 20.25,
        "decay": 25.5,
        "release": 30.75,
        "sustain": 0.4,
        "alpha": 1,
        "note": 90.5,
        "gain": 1,
        "count": 150,
        "origin": 0,
    }
    for rate, alpha, gain, sustain in itertools.product(
        (100, 441, 1000), (0.1, 0.5, 1, 2, 6), (0.25, 1), (0.2, 0.8)
    ):
        yield (
            f"grid:r{rate}:a{alpha}:g{gain}:s{sustain}",
            rate,
            {**base, "alpha": alpha, "gain": gain, "sustain": sustain},
            "grid",
        )
        for shape, durations in (
            ("short_fractional", (7.25, 9.5, 12.75, 40.5, 64)),
            ("seconds", (0.7 * rate, 0.6 * rate, 1.1 * rate, 1.8 * rate, 4 * rate)),
        ):
            attack, decay, release, note, count = durations
            yield (
                f"grid:{shape}:r{rate}:a{alpha}:g{gain}:s{sustain}",
                rate,
                {
                    **base,
                    "attack": attack,
                    "decay": decay,
                    "release": release,
                    "note": note,
                    "count": count,
                    "alpha": alpha,
                    "gain": gain,
                    "sustain": sustain,
                },
                "grid",
            )
    for stage in ("attack", "decay", "release"):
        for length in (0, 1, 2, 3, 4, 5, 6, 8, 12):
            yield f"length:{stage}:{length}", 441, {**base, stage: length}, "length"
    edges = {
        "off_in_attack": {"attack": 120},
        "off_in_decay": {"decay": 100},
        "no_sustain_window": {"note": 47},
        "zero_decay": {"decay": 0},
        "zero_release": {"release": 0},
        "flat": {"sustain": 1},
        "zero_sustain": {"sustain": 0},
        "release_past_clip": {"release": 200},
        "silence": {"gain": 0},
        "near_silence": {"gain": 1e-9},
        "fractional_origin": {"origin": 0.375},
        "clipped_before_noteoff": {"count": 40},
        "one_sample": {"count": 1},
    }
    for name, changes in edges.items():
        yield f"edge:{name}", 441, {**base, **changes}, "edge"
    for stage in ("attack", "decay", "release"):
        yield f"mutation:{stage}:+2", 441, {**base, stage: base[stage] + 2}, "mutation"
    yield (
        "blindspot:attack:+0.005",
        441,
        {**base, "attack": base["attack"] + 0.005},
        "blindspot",
    )


def run_grid():
    records, all_rows, obligations = [], [], []

    def add(case_id, domain, fixture, result, limits, *, required=None):
        result["fixture"] = fixture
        rows, diagnostic = qualification_rows(result, case_id=case_id, limits=limits)
        records.append(
            {
                "case_id": case_id,
                "domain": domain,
                "diagnostic_utf8": diagnostic.decode(),
                "rows": rows,
            }
        )
        all_rows.extend(rows)
        if required:
            for name, verdict in required.items():
                actual = next(row["verdict"] for row in rows if row["property"] == name)
                obligations.append(
                    {
                        "case_id": case_id,
                        "property": name,
                        "expected_verdict": verdict,
                        "observed_verdict": actual,
                        "satisfied": actual == verdict,
                    }
                )

    baseline = next(envelope_cases())[2]
    _, baseline_truth = constructed_envelope(
        **{**baseline, "alpha": 1, "gain": 1, "sustain": 0.4}
    )
    for case_id, rate, params, kind in envelope_cases():
        ys, truth = constructed_envelope(**params)
        required = None
        if kind in ("mutation", "blindspot"):
            truth = baseline_truth
            stage = case_id.split(":")[1]
            required = {f"{stage}_end": "FAIL" if kind == "mutation" else "PASS"}
        elif kind == "grid" or case_id == "edge:fractional_origin":
            required = {name: "PASS" for name in truth}
        elif case_id in ("edge:silence", "edge:near_silence", "edge:one_sample"):
            required = {name: "NO VERDICT" for name in truth}
        elif case_id in (
            "edge:off_in_attack",
            "edge:off_in_decay",
            "edge:no_sustain_window",
            "edge:flat",
            "edge:zero_decay",
        ):
            required = {"attack_end": "NO VERDICT", "decay_end": "NO VERDICT"}
        elif case_id in (
            "edge:zero_release",
            "edge:release_past_clip",
            "edge:zero_sustain",
        ):
            required = {"release_end": "NO VERDICT"}
        result = estimate_envelope(
            ys,
            rate_hz=rate,
            note_on_seconds=params["note"] / rate,
            alpha=params["alpha"],
            origin_samples=params["origin"],
        )
        add(
            case_id,
            "direct",
            {
                "generator": "pinned-equation-independent-v1",
                "parameters": params,
                "kind": kind,
            },
            result,
            envelope_limits(truth),
            required=required,
        )

    for frequency, phase, origin, gain in itertools.product(
        (100, 733, 5000), (0, 0.43, 2.8), (0, 17), (0.25, 1)
    ):
        count, rate = 2048, 44100
        ys = [
            gain * math.sin(2 * math.pi * frequency * (i + origin) / rate + phase)
            + 0.07
            for i in range(count)
        ]
        case = f"tone:f{frequency}:p{phase}:o{origin}:g{gain}"
        result = tone_amplitude(
            ys, rate_hz=rate, frequency_hz=frequency, origin_samples=origin
        )
        add(
            case,
            "tone",
            {
                "frequency": frequency,
                "phase": phase,
                "origin": origin,
                "gain": gain,
                "count": count,
                "dc": 0.07,
            },
            result,
            {"amplitude": Limit(gain, 1e-7, "1", SOURCE)},
            required={"amplitude": "PASS"},
        )
    for name, frequency, count, gain in (
        ("slow", 10, 2048, 1),
        ("short", 1000, 20, 1),
        ("near_silence", 1000, 2048, 1e-9),
        ("silence", 1000, 2048, 0),
        ("nyquist", 22000, 2048, 1),
        ("boundary", 1000, 2048, 1),
    ):
        ys = [
            gain
            * math.sin(2 * math.pi * frequency * i / 44100)
            * (0.5 if name == "boundary" and i >= count // 2 else 1)
            for i in range(count)
        ]
        add(
            f"tone_refusal:{name}",
            "tone",
            {"control": name, "frequency": frequency, "count": count, "gain": gain},
            tone_amplitude(ys, rate_hz=44100, frequency_hz=frequency),
            {"amplitude": Limit(gain, 1e-7, "1", SOURCE)},
            required={"amplitude": "NO VERDICT"},
        )

    for seed, gain in itertools.product(range(8), (0.25, 1)):
        rng = random.Random(seed)
        ys = [gain * rng.gauss(0, 1) for _ in range(8192)]
        add(
            f"broadband:seed{seed}:g{gain}",
            "broadband",
            {"seed": seed, "gain": gain, "count": 8192},
            broadband_amplitude(
                ys, rate_hz=44100, source_law="iid_gaussian_unit_variance"
            ),
            {"amplitude": Limit(gain, 0.05 * gain, "1", SOURCE)},
            required={"amplitude": "PASS"},
        )
    for name in ("short", "near_silence", "silence", "boundary", "unknown_law"):
        rng = random.Random(100)
        gain = 1e-9 if name == "near_silence" else 0 if name == "silence" else 1
        ys = [
            gain * rng.gauss(0, 1) * (0.25 if name == "boundary" and i < 4096 else 1)
            for i in range(128 if name == "short" else 8192)
        ]
        add(
            f"broadband_refusal:{name}",
            "broadband",
            {"control": name, "seed": 100, "gain": gain},
            broadband_amplitude(
                ys,
                rate_hz=44100,
                source_law="unknown"
                if name == "unknown_law"
                else "iid_gaussian_unit_variance",
            ),
            {"amplitude": Limit(gain, 0.05 * gain, "1", SOURCE)},
            required={"amplitude": "NO VERDICT"},
        )

    path = ROOT / "spec/reference/directed-voice-v1.json"
    manifest = json.loads(path.read_text())
    validate_manifest(manifest)
    routes = [case for case in manifest["cases"] if case["kind"] == "route"]
    expected_names = {
        f"mod_matrix.{s}->{d}"
        for s in ("adsr_1", "adsr_2", "lfo_1", "lfo_2")
        for d in DESTINATIONS
    }
    if {case["target"] for case in routes} != expected_names or len(routes) != 20:
        raise ValueError("canonical route enumeration changed")
    for case in routes:
        patch = resolve_patch(manifest, case)
        destination = case["target"].split("->")[1]
        scale = (
            patch[f"{destination[:5]}.mod_depth"]["physical"]
            if destination.endswith("pitch")
            else 1
        )
        gain = patch[case["target"]]["physical"]
        source_name = case["target"].split(".")[1].split("->")[0]
        source = [
            i / 127
            if source_name.startswith("adsr")
            else math.sin(2 * math.pi * i / 32)
            for i in range(128)
        ]
        for fault in ("identity", "sign", "destination", "depth", "no_excitation"):
            xs = [0] * len(source) if fault == "no_excitation" else source
            active = (
                DESTINATIONS[(DESTINATIONS.index(destination) + 1) % 5]
                if fault == "destination"
                else destination
            )
            factor = -1 if fault == "sign" else 1.25 if fault == "depth" else 1
            outputs = {
                name: [gain * factor * x if name == active else 0 for x in xs]
                for name in DESTINATIONS
            }
            physical = {
                name: [y * scale for y in outputs[name]] for name in DESTINATIONS
            }
            result = estimate_routes(
                xs, outputs, rate_hz=441, physical_outputs=physical
            )
            limits = {
                f"gain.{name}": Limit(
                    gain if name == destination else 0, 1e-8, "1", SOURCE
                )
                for name in DESTINATIONS
            }
            limits.update(
                {
                    f"depth.{name}": Limit(
                        gain * scale if name == destination else 0,
                        1e-7,
                        UNITS[name],
                        SOURCE,
                    )
                    for name in DESTINATIONS
                }
            )
            required = (
                {key: "PASS" for key in limits}
                if fault == "identity"
                else {
                    f"gain.{destination}": "NO VERDICT"
                    if fault == "no_excitation"
                    else "FAIL",
                    f"depth.{destination}": "NO VERDICT"
                    if fault == "no_excitation"
                    else "FAIL",
                }
            )
            if fault == "destination":
                required[f"gain.{active}"] = "FAIL"
            add(
                case["id"] if fault == "identity" else f"{case['id']}:{fault}",
                "route",
                {
                    "case_id": case["id"],
                    "source": source_name,
                    "destination": destination,
                    "patch_sha256": hashlib.sha256(encoded(patch)).hexdigest(),
                    "manifest_identity": manifest["identity"],
                    "manifest_file_sha256": hashlib.sha256(
                        path.read_bytes()
                    ).hexdigest(),
                    "weight": gain,
                    "physical_scale": scale,
                    "fault": fault,
                    "evidence": "constructed pairs; Voice patch prepared, audio not run",
                },
                result,
                limits,
                required=required,
            )

    floors = floor_table(records)
    report = make_report(
        all_rows,
        partition="development",
        rubric={"id": "envelope-routes-analytic", "version": "1"},
    )
    result = {
        "schema": "envelope-routes-qualification-v1",
        "scope": "constructed development only; holdout sealed; no Voice/hardware fidelity",
        "runtime": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
        },
        "preregistration": SOURCE,
        "replay_policy": REPLAY_POLICY,
        "records": records,
        "report": report,
        "floors": floors,
        "obligations": obligations,
        "upstream_sentinel": {"status": "not_run"},
        "shared_preparation": {"status": "not_run"},
    }
    result["source_sha256"] = {
        path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        for path in (
            "src/torchsynth_voice/envelope_estimators.py",
            "tools/qualify_envelope_estimators.py",
            "spec/ENVELOPE-ESTIMATORS.md",
        )
    }
    return result


def floor_table(records):
    floors = {}
    for record in records:
        for row in record["rows"]:
            key = f"{record['domain']}.{row['property']}"
            table = floors.setdefault(
                key,
                {
                    "unit": row["unit"],
                    "valid": 0,
                    "refused": 0,
                    "maximum_absolute_error": None,
                    "mean_signed_error": None,
                    "minimum_tested_stage_samples": None,
                },
            )
            fixture = json.loads(record["diagnostic_utf8"])["comparison"]["fixture"]
            # Faults and blind spots are controls, never pooled into the baseline floor.
            if (
                fixture.get("kind") in ("mutation", "blindspot")
                or fixture.get("fault", "identity") != "identity"
            ):
                continue
            if row["observed"] is None:
                table["refused"] += 1
            elif row["expected"]["value"] is not None:
                error = row["observed"] - row["expected"]["value"]
                table["valid"] += 1
                table["maximum_absolute_error"] = max(
                    table["maximum_absolute_error"] or 0, abs(error)
                )
                old = table["mean_signed_error"] or 0
                table["mean_signed_error"] = old + (error - old) / table["valid"]
                if fixture.get("kind") == "length" and row["property"].endswith("end"):
                    stage = row["property"].split("_")[0]
                    if record["case_id"].split(":")[1] == stage:
                        length = fixture["parameters"][stage]
                        old = table["minimum_tested_stage_samples"]
                        table["minimum_tested_stage_samples"] = (
                            length if old is None else min(old, length)
                        )
    return floors


def validate_evidence(evidence, *, reference=None):
    validate_report(evidence["report"])
    rows = []
    seen = set()
    for record in evidence["records"]:
        if record["case_id"] in seen:
            raise ValueError("duplicate qualification case")
        seen.add(record["case_id"])
        digest = hashlib.sha256(record["diagnostic_utf8"].encode()).hexdigest()
        for row in record["rows"]:
            if row["artifact"]["sha256"] != digest:
                raise ValueError("diagnostic bytes do not match scorecard digest")
        diagnostic = json.loads(record["diagnostic_utf8"])
        expected_rows, expected_bytes = qualification_rows(
            diagnostic["comparison"],
            case_id=record["case_id"],
            limits={
                key: Limit(**limit)
                for key, limit in diagnostic["rubric"]["limits"].items()
            },
        )
        if (
            expected_rows != record["rows"]
            or expected_bytes.decode() != record["diagnostic_utf8"]
        ):
            raise ValueError("diagnostic and derived rows disagree")
        rows.extend(record["rows"])
    if rows != evidence["report"]["rows"]:
        raise ValueError("report rows differ from raw records")
    observations = {
        (row["case"]["id"], row["property"]): row["verdict"] for row in rows
    }
    failed = [
        x
        for x in evidence["obligations"]
        if not x["satisfied"]
        or observations.get((x["case_id"], x["property"])) != x["expected_verdict"]
        or x["observed_verdict"] != x["expected_verdict"]
    ]
    if failed:
        raise ValueError(
            f"{len(failed)} qualification obligations failed: {failed[:5]}"
        )
    if evidence["floors"] != floor_table(evidence["records"]):
        raise ValueError("saved floor tables differ from retained observations")
    reference = run_grid() if reference is None else reference
    for key in (
        "schema",
        "scope",
        "preregistration",
        "replay_policy",
        "source_sha256",
        "obligations",
    ):
        if evidence.get(key) != reference[key]:
            raise ValueError("saved qualification identity differs: " + key)
    if [(r["case_id"], r["domain"]) for r in evidence["records"]] != [
        (r["case_id"], r["domain"]) for r in reference["records"]
    ]:
        raise ValueError("saved case inventory differs")
    for saved, fresh in zip(evidence["records"], reference["records"], strict=True):
        if [r["property"] for r in saved["rows"]] != [
            r["property"] for r in fresh["rows"]
        ]:
            raise ValueError("saved property inventory differs: " + saved["case_id"])
        old = json.loads(saved["diagnostic_utf8"])["comparison"]
        new = json.loads(fresh["diagnostic_utf8"])["comparison"]
        for key in ("fixture", "settings", "estimator", "implementation"):
            if old[key] != new[key]:
                raise ValueError("saved fixture/configuration identity differs: " + key)
        if old["inputs"].keys() != new["inputs"].keys():
            raise ValueError("saved input inventory differs")
        for name, record in old["inputs"].items():
            actual = retained_input(record)
            expected = retained_input(new["inputs"][name])
            require_replay_value(
                actual, expected, "input." + name, precision=INPUT_REPLAY_PRECISION
            )
        require_replay_value(old["metrics"], new["metrics"], "raw metrics")
        require_replay_value(old["diagnostics"], new["diagnostics"], "diagnostics")
        for left, right in zip(saved["rows"], fresh["rows"], strict=True):
            for key in set(left) | set(right):
                if key == "artifact":
                    continue  # independently checked against retained original bytes above
                if key == "observed":
                    require_replay_value(left[key], right[key], key)
                elif key == "expected":
                    if (
                        left[key].keys() != right[key].keys()
                        or left[key]["source"] != right[key]["source"]
                    ):
                        raise ValueError("saved expected truth identity differs")
                    require_replay_value(
                        left[key]["value"], right[key]["value"], "expected truth"
                    )
                elif left.get(key) != right.get(key):
                    raise ValueError("saved qualification state differs: " + key)
    require_replay_value(evidence["floors"], reference["floors"], "replayed floors")


def retained_input(record):
    """Provenance hashes bind original bytes; host regeneration compares values."""
    if set(record) != {"samples", "binary64_le_sha256", "binary64_le_base64"}:
        raise ValueError("missing or extra retained input provenance")
    data = base64.b64decode(record["binary64_le_base64"], validate=True)
    if (
        type(record["samples"]) is not int
        or record["samples"] < 0
        or len(data) != record["samples"] * 8
        or hashlib.sha256(data).hexdigest() != record["binary64_le_sha256"]
    ):
        raise ValueError("retained input hash/count mismatch")
    values = [value[0] for value in struct.iter_unpack("<d", data)]
    if not all(math.isfinite(value) for value in values):
        raise ValueError("nonfinite retained input")
    return values


def require_replay_value(saved, fresh, context, *, precision=REPLAY_PRECISION):
    if (
        isinstance(saved, dict)
        and isinstance(fresh, dict)
        and saved.keys() == fresh.keys()
    ):
        for key in saved:
            require_replay_value(
                saved[key], fresh[key], context + "." + key, precision=precision
            )
        return
    if isinstance(saved, list) and isinstance(fresh, list) and len(saved) == len(fresh):
        for left, right in zip(saved, fresh, strict=True):
            require_replay_value(left, right, context, precision=precision)
        return
    if type(saved) in (int, float) and type(fresh) in (int, float):
        if (
            math.isfinite(saved)
            and math.isfinite(fresh)
            and (
                saved == fresh
                if type(saved) is type(fresh) is int
                else math.isclose(saved, fresh, abs_tol=precision, rel_tol=precision)
            )
        ):
            return
    elif type(saved) is type(fresh) and saved == fresh:
        return
    raise ValueError(
        "saved numerical value differs beyond replay precision or shape: " + context
    )


def upstream_sentinel(root):
    """Execute only the pinned ADSR module; this does not run a Voice fixture."""
    from torchsynth_voice.contract import UpstreamContract

    contract = UpstreamContract.load()
    errors = contract.verify_source_tree(root, require_git_commit=True)
    if errors:
        raise ValueError(errors)
    sys.path.insert(0, str(root))
    import torch
    from torchsynth import module
    from torchsynth.config import SynthConfig

    if Path(module.__file__).resolve() != root.resolve() / "torchsynth/module.py":
        raise ValueError("sentinel imported a different TorchSynth source")
    torch.set_num_threads(1)
    results = []
    cases = {
        "fractional": {},
        "alpha_low": {"alpha": 0.1},
        "alpha_high": {"alpha": 6},
        "zero_attack": {"attack": 0},
        "zero_decay": {"decay": 0},
        "zero_release": {"release": 0},
        "off_in_attack": {"attack": 0.7},
        "off_in_decay": {"decay": 0.7},
        "release_past_clip": {"release": 2},
    }
    for name, changes in cases.items():
        requested = {
            "attack": 20.25 / 441,
            "decay": 25.5 / 441,
            "release": 30.75 / 441,
            "sustain": 0.4,
            "alpha": 1,
            **changes,
        }
        config = SynthConfig(batch_size=1, reproducible=False, buffer_size_seconds=0.5)
        adsr = module.ADSR(
            config,
            device=torch.device("cpu"),
            **{key: torch.tensor([value]) for key, value in requested.items()},
        )
        note = torch.tensor([90.5 / 441])
        actual = {key: float(adsr.p(key).detach()[0]) for key in requested}
        observed = adsr.output(note).detach()[0].tolist()
        params = {**actual, "note": float(note[0]), "count": len(observed)}
        params.update(
            {key: params[key] * 441 for key in ("attack", "decay", "release", "note")}
        )
        expected, _ = constructed_envelope(**params)
        error = max(abs(x - y) for x, y in zip(expected, observed))
        result = estimate_envelope(
            observed, rate_hz=441, note_on_seconds=float(note[0]), alpha=actual["alpha"]
        )
        results.append(
            {
                "case_id": name,
                "requested": requested,
                "actual_physical": actual,
                "max_equation_error": error,
                "limit": 1e-4,
                "limit_source": "spec/ENVELOPE-ESTIMATORS.md: pinned ADSR equation sentinel v1",
                "status": "pass" if error <= 1e-4 else "fail",
                "estimator_attempt": result,
            }
        )
    if any(case["status"] != "pass" for case in results):
        raise ValueError(f"upstream equation sentinel failed: {results}")
    return {
        "status": "executed",
        "source_commit": contract.target_commit,
        "source_hashes_verified": True,
        "python": platform.python_version(),
        "torch": torch.__version__,
        "device": "cpu",
        "dtype": "float32",
        "threads": 1,
        "scope": "ADSR equation sentinel only; no Voice render or scalar/batch equivalence",
        "cases": results,
    }


def shared_preparation_sentinel(root):
    import importlib

    import torchsynth_voice

    # Read-only peer package extension; only the shared preparation module is
    # loaded from the explicitly selected tree. No copy or transform duplication.
    peer = root.resolve() / "src/torchsynth_voice"
    torchsynth_voice.__path__.append(str(peer))
    backend = importlib.import_module("torchsynth_voice.preparation")
    samples, _ = constructed_envelope(
        attack=20.25, decay=25.5, release=30.75, sustain=0.4, alpha=1, note=90.5
    )

    def measure(traces):
        return estimate_envelope(
            traces["envelope"], rate_hz=441, note_on_seconds=90.5 / 441, alpha=1
        )

    records = []
    for operation in (
        None,
        "alignment",
        "trim",
        "resample",
        "normalize",
        "filter",
        "window",
    ):
        result = preparation_adapter(
            {"envelope": samples},
            rate_hz=441,
            measure=measure,
            units={"envelope": "1"},
            backend=backend,
            operations=() if operation is None else (operation,),
        )
        valid = result["metrics"]["attack_end"]["value"] is not None
        if valid != (operation is None):
            raise ValueError("shared preparation gate failed")
        records.append({"operation": operation or "identity", "result": result})
    tone = [0.5 * math.sin(2 * math.pi * 1000 * i / 44100) for i in range(1024)]
    rng = random.Random(27)
    noise = [rng.gauss(0, 1) for _ in range(8192)]
    route_input = [i / 31 for i in range(32)]
    route_traces = {
        "source": route_input,
        **{
            name: route_input if name == "vco_1_amp" else [0] * 32
            for name in DESTINATIONS
        },
    }
    families = (
        (
            "tone",
            {"tone": tone},
            44100,
            lambda ts: tone_amplitude(ts["tone"], rate_hz=44100, frequency_hz=1000),
            "amplitude",
        ),
        (
            "broadband",
            {"noise": noise},
            44100,
            lambda ts: broadband_amplitude(
                ts["noise"], rate_hz=44100, source_law="iid_gaussian_unit_variance"
            ),
            "amplitude",
        ),
        (
            "route",
            route_traces,
            441,
            lambda ts: estimate_routes(
                ts["source"], {name: ts[name] for name in DESTINATIONS}, rate_hz=441
            ),
            "gain.vco_1_amp",
        ),
    )
    for name, traces, rate, measure_family, key in families:
        for operation in (None, "normalize"):
            result = preparation_adapter(
                traces,
                rate_hz=rate,
                measure=measure_family,
                units={key: "1" for key in traces},
                backend=backend,
                operations=() if operation is None else (operation,),
            )
            if (result["metrics"][key]["value"] is not None) != (operation is None):
                raise ValueError(f"{name} shared preparation gate failed")
            records.append(
                {"family": name, "operation": operation or "identity", "result": result}
            )
    return {
        "status": "executed",
        "version": backend.VERSION,
        "source_sha256": hashlib.sha256(
            Path(backend.__file__).read_bytes()
        ).hexdigest(),
        "scope": "identity adapter and forbidden-operation refusals",
        "cases": records,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="execute grid and validate committed evidence without rewriting",
    )
    parser.add_argument(
        "--upstream-root",
        type=Path,
        help="execute pinned ADSR equation sentinel (requires Torch)",
    )
    parser.add_argument(
        "--preparation-root",
        type=Path,
        help="read-only #86 tree for the shared identity adapter sentinel",
    )
    args = parser.parse_args()
    evidence = run_grid()
    if args.upstream_root:
        evidence["upstream_sentinel"] = upstream_sentinel(args.upstream_root)
    if args.preparation_root:
        evidence["shared_preparation"] = shared_preparation_sentinel(
            args.preparation_root
        )
    validate_evidence(evidence, reference=evidence)
    if args.check:
        saved = json.loads(args.output.read_text())
        validate_evidence(saved, reference=evidence)
        evidence = saved
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(encoded(evidence))
    print(
        json.dumps(
            {
                "cases": len(evidence["records"]),
                "summary": evidence["report"]["summary"],
                "obligations": len(evidence["obligations"]),
                "outcome": "all obligations satisfied",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
