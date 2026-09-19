"""Execute the preregistered analytic grid. No Torch or holdout input."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.paired_metrics import (
    analytic_exactness_rubric,
    compare_paired,
    scorecard_rows,
)
from torchsynth_voice.periodic_estimators import (
    PROPERTIES,
    VERSION,
    canonical_bytes,
    digest,
    estimate_periodic,
    identity_metadata,
    property_error,
    range_key,
    score_periodic,
    wrap_phase,
)
from torchsynth_voice.scorecard import validate_row

# All imports above are stdlib-only. Limit threads before requesting NumPy.
for variable in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
):
    os.environ.setdefault(variable, "1")

SOURCE = "2b0964d4c6c3d472a2a0d54d91b408caaeffca6d"
PURE = [[int(i == j) for i in range(5)] for j in range(5)]
SHAPES = PURE + [[0.6, 0.4, 0, 0, 0], [0.2, 0.7, 0.4, 0.1, 0.6], [0, 0, 1, 1, 0]]
SHAPE_NAMES = ["sin", "tri", "saw", "rsaw", "sqr", "sin-tri", "five-way", "cancel"]


def midi(note):
    return 440 * 2 ** ((note - 69) / 12)


def grid():
    cases = []

    def add(case_id, family, frequency, **changes):
        case = {
            "id": case_id,
            "family": family,
            "frequency": frequency,
            "duration": 4,
            "phase": -0.31,
            "gain": 1,
            "origin": 0.0,
            "weights": PURE[0],
            "defect": None,
            "comparison": None,
            "mandatory": [],
        }
        case.update(changes)
        cases.append(case)

    for f in (
        midi(-24),
        midi(0),
        20.125,
        110.37,
        440.123,
        1999.7,
        8000.25,
        midi(127),
        19800.125,
        midi(151),
    ):
        add(f"audio-frequency-{f:.9g}", "oscillator", f)
    for f in (0, 0.125, 0.5, 0.75, 1.125, 4.37, 10.125, 20, 40):
        add(f"lfo-frequency-{f}", "lfo", f)
    for family, f in (("oscillator", 110.37), ("lfo", 4.37)):
        for duration in (0.125, 1, 4):
            for phase in (-math.pi, -math.pi + 0.0001, -0.31, 0, math.pi - 0.0001):
                for gain in (1, 0.125, 2**-20, 2**-40):
                    for origin in (0.0, 0.375):
                        add(
                            f"{family}-window-{duration}-phase-{phase}-gain-{gain}-origin-{origin}",
                            family,
                            f,
                            duration=duration,
                            phase=phase,
                            gain=gain,
                            origin=origin,
                        )
    for name, weights in zip(SHAPE_NAMES, SHAPES):
        for f in (0.5, 1.125, 4.37, 20, 40):
            add(f"shape-{name}-{f}", "lfo", f, weights=weights)
    for defect in (
        "silence",
        "mixture",
        "fm",
        "clipping",
        "noise",
        "transient",
        "preparation",
        "alias_provenance",
    ):
        add("refusal-" + defect, "oscillator", 110.37, defect=defect)
    add("refusal-envelope", "lfo", 4.37, defect="envelope")
    for shape in (0, 0.5, 1):
        add(
            f"refusal-SquareSawVCO-{shape}",
            "oscillator",
            110.37,
            defect="SquareSawVCO",
            shape=shape,
        )
    mutations = (
        ("tuning", "oscillator", 110.37 * 2 ** (5 / 1200), {"cents": 0}, ["cents"], {}),
        ("rate", "lfo", 4.87, {"frequency_hz": 4.37}, ["frequency_hz"], {}),
        (
            "depth",
            "lfo",
            4.37,
            {"depth_peak_to_peak": 1},
            ["depth_peak_to_peak"],
            {"gain": 1.1},
        ),
        (
            "phase",
            "oscillator",
            110.37,
            {"phase_rad": -0.31},
            ["phase_rad"],
            {"phase": -0.21},
        ),
        (
            "gain-1dB",
            "oscillator",
            110.37,
            {"depth_peak_to_peak": 2},
            ["depth_peak_to_peak"],
            {"gain": 10**0.05},
        ),
        (
            "delay-one-sample",
            "oscillator",
            110.37,
            {"timing_samples": 0},
            ["timing_samples"],
            {"phase": -math.tau * 110.37 / 44100},
        ),
        (
            "resolution-edge",
            "lfo",
            4.37 + 1e-8,
            {"frequency_hz": 4.37},
            [],
            {"resolution_limit": 1e-10},
        ),
        ("undetectable-control", "lfo", 4.37 + 1e-8, {"frequency_hz": 4.37}, [], {}),
    )
    for name, family, f, comparison, mandatory, changes in mutations:
        add(
            "mutation-" + name,
            family,
            f,
            comparison=comparison,
            mandatory=mandatory,
            **changes,
        )
    # Preregistered supplemental controls after the curvature correction;
    # unchanged acceptance caps/guards. Preserve representable injected deltas.
    for delta in (1e-14, 1e-15):
        add(
            f"mutation-floating-floor-{delta}",
            "lfo",
            4.37 + delta,
            comparison={"frequency_hz": 4.37},
            resolution_limit=1e-16,
            requested_delta_hz=delta,
            represented_delta_hz=(4.37 + delta) - 4.37,
        )
    return cases


def fixture(case):
    """Independent direct evaluation of source equations; no estimator helpers."""
    import numpy as np

    rate = 44100 if case["family"] == "oscillator" else 441
    time = np.arange(round(case["duration"] * rate)) / rate + case["origin"]
    phase = math.tau * case["frequency"] * time + case["phase"]
    if case["defect"] == "fm":
        phase += 0.3 * np.sin(math.tau * 1.37 * time)

    def lfo(argument):
        # Literal pinned make_lfo_shapes equations, independent of the
        # estimator's extrema/recurrence/crossing implementation.
        cosine = np.cos(argument + math.pi)
        ramp = np.remainder(argument, math.tau) / math.tau
        triangle = 2 * ramp
        triangle = np.where(triangle > 1, 2 - triangle, triangle)
        components = (
            (cosine + 1) / 2,
            triangle,
            ramp,
            1 - ramp,
            (np.sign(cosine) + 1) / 2,
        )
        powers = [w**2.718281828 for w in case["weights"]]
        return sum(p * component for p, component in zip(powers, components)) / sum(
            powers
        )

    if case["family"] == "oscillator":
        samples = case["gain"] * np.cos(phase)
        depth = 2 * case["gain"]
    else:
        samples = case["gain"] * lfo(phase)
        # Independent dense truth (2^18 phases). Max missed-extremum bound
        # for unit normalized shapes < 2*pi/2^18; reported in provenance.
        cycle = lfo(np.arange(2**18) * math.tau / 2**18)
        depth = case["gain"] * float(np.max(cycle) - np.min(cycle))
    defect = case["defect"]
    if defect == "silence":
        samples[:] = 0
    elif defect == "mixture":
        samples += 0.3 * np.cos(math.tau * 137.4 * time)
    elif defect == "clipping":
        samples = np.clip(samples, -0.6, 0.6)
    elif defect == "noise":
        samples += 0.05 * np.random.default_rng(2601).standard_normal(len(samples))
    elif defect == "transient":
        samples[len(samples) // 2] += 0.5
    elif defect == "envelope":
        samples *= np.linspace(0.1, 1, len(samples))
    elif defect == "SquareSawVCO":
        partials = 12000 / (case["frequency"] * math.log10(case["frequency"]))
        square = np.tanh(math.pi * partials * np.sin(phase) / 2)
        shape = case["shape"]
        samples = (1 - shape / 2) * square * (1 + shape * np.cos(phase))
    reference = 110.37 if case["family"] == "oscillator" else 4.37
    truth = {
        "frequency_hz": case["frequency"],
        "phase_rad": wrap_phase(case["phase"]),
        "timing_samples": -wrap_phase(case["phase"]) * rate / (math.tau * reference),
        "depth_peak_to_peak": depth,
    }
    if case["frequency"] > 0:
        truth["cents"] = 1200 * math.log2(case["frequency"] / reference)
    return samples, rate, reference, truth


def caps(case):
    f = case["frequency"]
    band = 0 if f < 20 else 1 if f < 2000 else 2
    pure_sine = case["weights"] == PURE[0]
    return {
        "frequency_hz": (1e-5, 1e-4, 1e-3)[band]
        if pure_sine
        else (0.02 if f < 20 else 0.1),
        "cents": 0.01
        if pure_sine
        else 1200 * math.log2(1 + (0.02 if f < 20 else 0.1) / max(f, 0.001)),
        "phase_rad": 1e-4,
        "timing_samples": (0.1, 0.01, 0.001)[band],
        "depth_peak_to_peak": 1e-5 if pure_sine else 0.01,
    }


def qualification():
    import numpy as np

    cases = grid()
    inventory = json.loads(
        (ROOT / "spec/reference/parameter-inventory-v1.json").read_text()
    )
    ranges = {p["name"]: [p["minimum"], p["maximum"]] for p in inventory["parameters"]}
    for key, value in {
        "keyboard.midi_f0": [0, 127],
        "vco_1.tuning": [-24, 24],
        "lfo_1.frequency": [0, 20],
        "lfo_1.mod_depth": [-10, 20],
    }.items():
        if ranges[key] != value:
            raise ValueError("pinned inventory domain changed: " + key)
    records, cells, failures = [], {}, []
    for case in cases:
        samples, rate, reference, truth = fixture(case)
        preparation = identity_metadata(
            len(samples), rate, "1", time_origin=case["origin"]
        )
        result = estimate_periodic(
            samples,
            sample_rate_hz=rate,
            unit="1",
            family=case["family"],
            reference_hz=reference,
            weights=case["weights"],
            time_origin=case["origin"],
            preparation=None if case["defect"] == "preparation" else preparation,
            fundamental_in_band=case["frequency"] < 0.45 * rate
            and case["defect"] != "alias_provenance",
            envelope_shaped=case["defect"] == "envelope",
        )
        errors = {
            name: property_error(name, value, truth[name], reference, rate)
            for name, value in result["estimates"].items()
            if value is not None and name in truth
        }
        record = {
            "case": case,
            "truth": truth,
            "measurement": result,
            "absolute_errors": errors,
            "maximum_allowed_errors": caps(case),
        }
        records.append(record)
        key = result["range_key"]
        if case["comparison"] is not None or case["defect"] is not None:
            if case["defect"] and result["status"] == "valid":
                failures.append(case["id"] + ": negative control accepted")
            continue
        expected_identifiable = (
            case["frequency"] > 0
            and case["frequency"] < 0.45 * rate
            and case["frequency"] * (len(samples) - 1) / rate >= 3
            and truth["depth_peak_to_peak"] > 2**-30
        )
        if expected_identifiable and result["status"] != "valid":
            failures.append(case["id"] + ": identifiable preregistered fixture refused")
        # Refusals remain indexed by requested domain, not dropped from floor tables.
        if key is None:
            key = range_key(
                result, frequency=case["frequency"], depth=truth["depth_peak_to_peak"]
            )
        cell = cells.setdefault(
            key,
            {
                "range_key": key,
                "algorithm": VERSION,
                "accepted": [],
                "refused": [],
                "measured_floors": dict.fromkeys(PROPERTIES),
                "guard_floors": dict.fromkeys(PROPERTIES),
                "qualified": True,
                "cap_failures": [],
            },
        )
        cell["accepted" if result["status"] == "valid" else "refused"].append(
            case["id"]
        )
        for name, error in errors.items():
            old = cell["measured_floors"][name]
            cell["measured_floors"][name] = max(old or 0, error)
            # Separate conservative guards: numerical for sine; sample-edge
            # frequency uncertainty for non-sine. These are NOT tolerances.
            guard = {
                "frequency_hz": 1e-7,
                "cents": 1e-5,
                "phase_rad": 1e-7,
                "timing_samples": 1e-5,
                "depth_peak_to_peak": 1e-7,
            }[name]
            if case["weights"] != PURE[0] and name in ("frequency_hz", "cents"):
                sampling = 2 * case["frequency"] / len(samples)
                guard = max(
                    guard,
                    sampling
                    if name == "frequency_hz"
                    else 1200 * math.log2(1 + sampling / case["frequency"]),
                )
            if case["weights"] != PURE[0] and name == "depth_peak_to_peak":
                guard = max(guard, math.tau / 2**18)
            cell["guard_floors"][name] = max(
                cell["guard_floors"][name] or 0, error, guard
            )
            if error > caps(case)[name]:
                cell["qualified"] = False
                cell["cap_failures"].append(
                    {"case": case["id"], "property": name, "error": error}
                )
                failures.append(
                    case["id"] + ": estimator error exceeds preregistered cap: " + name
                )
    for cell in cells.values():
        if not cell["accepted"]:
            cell["qualified"] = False
    # A qualified family must actually measure ordinary pure sine, each pure
    # directed LFO and at least the smooth continuous blend, not refuse all.
    for name in SHAPE_NAMES[:-2]:
        record = next(r for r in records if r["case"]["id"] == f"shape-{name}-4.37")
        if record["measurement"]["status"] != "valid":
            failures.append(record["case"]["id"] + ": required supported shape refused")
    for record in records:
        case, result = record["case"], record["measurement"]
        expected = record["truth"].copy()
        limits = caps(case)
        if case["comparison"]:
            expected.update(case["comparison"])
            limits.update(
                frequency_hz=0.02,
                cents=0.05,
                phase_rad=0.001,
                timing_samples=0.01,
                depth_peak_to_peak=0.01,
            )
        if "resolution_limit" in case:
            limits["frequency_hz"] = case["resolution_limit"]
        rows, diagnostic = score_periodic(
            result,
            case_id=case["id"],
            expected=expected,
            limits=limits,
            qualification=cells.get(result["range_key"]),
            truth_source="independent direct analytic equations: periodic grid "
            + digest(cases),
            limit_source="spec/PERIODIC-ESTIMATORS.md preregistration v1; analytic only",
        )
        record["rows"], record["diagnostic_json"] = rows, diagnostic.decode()
        for mandatory in case["mandatory"]:
            row = next(row for row in rows if row["property"] == mandatory)
            if row["verdict"] != "FAIL":
                failures.append(
                    case["id"] + ": mandatory detector did not FAIL: " + mandatory
                )
        if "resolution_limit" in case and rows[0]["verdict"] != "NO VERDICT":
            failures.append("resolution edge was laundered into a verdict")
    # Primary, unaligned +1 dB and a zero-padded one-sample delay.
    paired = []
    baseline_case = dict(cases[0], frequency=110.37, phase=0, duration=1)
    baseline, rate, _, _ = fixture(baseline_case)
    for name, candidate in (
        ("gain-1dB", baseline * 10**0.05),
        ("zero-padded-delay", np.concatenate(([0.0], baseline[:-1]))),
    ):
        measurement = compare_paired(
            baseline,
            candidate,
            reference_rate_hz=rate,
            candidate_rate_hz=rate,
            unit="1",
            spectral=False,
        )
        rows, diagnostic = scorecard_rows(
            measurement,
            case_id=name,
            partition="development",
            trace="analytic.cosine",
            rubric=analytic_exactness_rubric(),
        )
        equal = next(row for row in rows if row["property"] == "exact_equal")
        if equal["verdict"] != "FAIL":
            failures.append("paired exactness did not detect " + name)
        paired.append(
            {"case": name, "rows": rows, "diagnostic_json": diagnostic.decode()}
        )
    return {
        "schema_version": 1,
        "scope": "analytic-development-only",
        "grid_sha256": digest(cases),
        "source": {
            "upstream_commit": SOURCE,
            "inventory_sha256": hashlib.sha256(
                (ROOT / "spec/reference/parameter-inventory-v1.json").read_bytes()
            ).hexdigest(),
        },
        "runtime": {"python": platform.python_version(), "numpy": np.__version__},
        "implementation_sha256": hashlib.sha256(
            (ROOT / "src/torchsynth_voice/periodic_estimators.py").read_bytes()
        ).hexdigest(),
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "production_preparation": "pending #86 runtime evidence and later #23 Voice integration",
        "truth_depth_grid_bound": math.tau / 2**18,
        "range_cells": cells,
        "records": records,
        "paired_sentinels": paired,
        "qualification_failures": failures,
        "counts": {
            "cases": len(records),
            "valid": sum(r["measurement"]["status"] == "valid" for r in records),
            "refused": sum(r["measurement"]["status"] != "valid" for r in records),
        },
    }


def verify_report(report):
    """Verify actual embedded record bytes and all strict scorecard rows."""
    for item in report["records"] + report["paired_sentinels"]:
        sha = hashlib.sha256(item["diagnostic_json"].encode()).hexdigest()
        for row in item["rows"]:
            validate_row(row)
            if row["artifact"]["sha256"] != sha:
                raise ValueError("diagnostic digest mismatch")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "sim/qualification/periodic-v1.json"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="rerun and require byte-identical output in this runtime",
    )
    args = parser.parse_args()
    report = qualification()
    verify_report(report)
    encoded = canonical_bytes(report)
    if args.check:
        if args.output.read_bytes() != encoded:
            raise SystemExit(
                "qualification differs; inspect runtime/raw results, do not overwrite limits"
            )
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(encoded)
    print(
        json.dumps(
            {
                "counts": report["counts"],
                "range_cells": len(report["range_cells"]),
                "qualification_failures": report["qualification_failures"],
            },
            indent=2,
        )
    )
    return bool(report["qualification_failures"])


if __name__ == "__main__":
    raise SystemExit(main())
