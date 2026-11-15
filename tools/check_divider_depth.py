#!/usr/bin/env python3
"""Plan check C3: divider-depth proportionality, arithmetic on the record.

Stdlib-only. Reads the committed synthesis-feasibility record
(``asic/evidence/oneshot-synth-feasibility-v1.json``) -- no synthesis tool,
PDK or simulator is invoked -- and compares each completed module's ABC
critical path with the unrolled restoring-divider stage count read off the
committed RTL loop bounds.

For every completed module it reports the ABC critical path, the TT setup
slack and the divider stage count. For the two divider modules (B1 and B2 of
``asic/whole-voice-feasibility-plan.md``) it computes path delay per divider
stage and states plainly whether the two rates agree within the tolerance
declared below. Either outcome is acceptable; the result is recorded in the
plan, not tuned here.

Modes:

- ``--check`` (default): print the table, then re-derive every figure and
  verify the plan's recorded ``c3-divider-depth`` block against them.
  Exits nonzero on any drift, including a flipped verdict.
- ``--report``: print the table only, without reading the plan.

Provenance guard: the per-stage comparison assumes uniform
``source: abc_stime`` timing (gate delay, no wire-load model); a record whose
timings were produced any other way is refused rather than compared.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORD_PATH = ROOT / "asic" / "evidence" / "oneshot-synth-feasibility-v1.json"
PLAN_PATH = ROOT / "asic" / "whole-voice-feasibility-plan.md"

# Restoring-divider stage counts are plan-side readings, deliberately not
# record pins; both match the committed RTL loop bounds:
# tb/sv/normalization_replay_engine.sv:181 (i = 63 .. 0) -> 64,
# tb/sv/upsample_engine.sv:155 (i = 48 .. 0) -> 49.
# quarter_wave_lut and noise_stream_dut have no restoring divider: they are
# the zero-stage controls, not missing data.
MODULE_STAGES = (
    ("normalization_replay_engine", 64),
    ("upsample_engine", 49),
    ("quarter_wave_lut", 0),
    ("noise_stream_dut", 0),
)

TT_CORNER = "tt_025C_5v00"
EXPECTED_TIMING_SOURCE = "abc_stime"

# A round +/-10% band on a gate-delay rate: two paths argued to be the same
# structure at two depths should produce per-stage rates within a tenth of
# each other to count as agreement. Declared before looking at any result;
# the verdict is whatever falls out of the record.
AGREEMENT_TOLERANCE = 0.10

BLOCK_RE = re.compile(r"```c3-divider-depth\n(.*?)\n```", re.S)


def analyze(record: dict) -> dict:
    """Derive the C3 figures from the record; never touches the plan."""
    rows = []
    for name, stages in MODULE_STAGES:
        timing = record["targets"][name]["synthesize"]["summary"]["timing"]
        source = timing.get("source")
        if source != EXPECTED_TIMING_SOURCE:
            raise ValueError(
                "%s: timing source %r, expected %r; the per-stage comparison "
                "assumes uniform %s provenance"
                % (name, source, EXPECTED_TIMING_SOURCE, EXPECTED_TIMING_SOURCE))
        slack = record["targets"][name]["sta"]["corners"][TT_CORNER]["worst_slack_ns"]
        path = timing["critical_path_ps"]
        rows.append({
            "name": name,
            "stages": stages,
            "critical_path_ps": path,
            "tt_slack_ns": slack,
            "ps_per_stage": (path / stages) if stages else None,
        })
    b1, b2 = rows[0], rows[1]
    ratio = b1["ps_per_stage"] / b2["ps_per_stage"]
    rel = abs(b1["ps_per_stage"] - b2["ps_per_stage"]) \
        / ((b1["ps_per_stage"] + b2["ps_per_stage"]) / 2)
    slope = (b1["critical_path_ps"] - b2["critical_path_ps"]) \
        / (b1["stages"] - b2["stages"])
    intercept = b2["critical_path_ps"] - slope * b2["stages"]
    return {
        "rows": rows,
        "tolerance": AGREEMENT_TOLERANCE,
        "ratio_b1_over_b2": ratio,
        "relative_difference": rel,
        "slope_ps_per_stage": slope,
        "intercept_ps": intercept,
        "verdict": "agree" if rel <= AGREEMENT_TOLERANCE else "disagree",
    }


def block_figures(result: dict) -> dict:
    """The recorded-outcome figures, at the precision the plan states them."""
    figures = {"tolerance_relative": round(result["tolerance"], 2)}
    for row in result["rows"]:
        figures[row["name"] + ".stages"] = row["stages"]
        figures[row["name"] + ".critical_path_ps"] = round(row["critical_path_ps"], 2)
        figures[row["name"] + ".tt_slack_ns"] = round(row["tt_slack_ns"], 2)
        if row["ps_per_stage"] is not None:
            figures[row["name"] + ".ps_per_stage"] = round(row["ps_per_stage"], 2)
    figures["ratio_b1_over_b2"] = round(result["ratio_b1_over_b2"], 4)
    figures["relative_difference"] = round(result["relative_difference"], 3)
    figures["slope_ps_per_stage"] = round(result["slope_ps_per_stage"], 2)
    figures["intercept_ps"] = round(result["intercept_ps"], 2)
    figures["verdict"] = result["verdict"]
    return figures


def check_plan(record: dict, plan_text: str) -> list:
    """Problems where the plan's recorded C3 block disagrees with the record."""
    match = BLOCK_RE.search(plan_text)
    if not match:
        return ["plan has no ```c3-divider-depth block"]
    problems = []
    stated = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition(" = ")
        if not sep:
            problems.append("bad block line: %r" % line)
            continue
        stated[key.strip()] = value.strip()
    expected = block_figures(analyze(record))
    for key in sorted(set(expected) | set(stated)):
        if key not in stated:
            problems.append("%s: missing from the plan block" % key)
            continue
        if key not in expected:
            problems.append("%s: not a figure this tool derives" % key)
            continue
        want, got = expected[key], stated[key]
        if isinstance(want, str):
            if got != want:
                problems.append("%s: plan %s != derived %s" % (key, got, want))
            continue
        try:
            gotf = float(got)
        except ValueError:
            problems.append("%s: block value %r is not a number" % (key, got))
            continue
        decimals = len(got.partition(".")[2])
        if round(want, decimals) != round(gotf, decimals):
            problems.append("%s: plan %s != derived %s"
                            % (key, got, format(round(want, decimals), "." + str(decimals) + "f")))
    return problems


def report_lines(result: dict) -> list:
    lines = [
        "%-28s %7s %17s %13s %14s" % ("module", "stages", "critical_path_ps",
                                      "tt_slack_ns", "ps_per_stage"),
    ]
    for row in result["rows"]:
        rate = "n/a" if row["ps_per_stage"] is None \
            else format(round(row["ps_per_stage"], 2), ".2f")
        slack = format(row["tt_slack_ns"], "+.2f")
        lines.append("%-28s %7d %17s %13s %14s"
                     % (row["name"], row["stages"],
                        format(row["critical_path_ps"], ".2f"), slack, rate))
    lines += [
        "",
        "agreement band: relative difference <= %d%% of the mean"
        % round(result["tolerance"] * 100),
        "ratio B1/B2 per-stage rate = %.4f" % result["ratio_b1_over_b2"],
        "relative difference        = %.1f%% of the mean"
        % (result["relative_difference"] * 100),
        "affine fit in stage count: slope %.2f ps/stage, intercept %.2f ps"
        % (result["slope_ps_per_stage"], result["intercept_ps"]),
        "verdict: %s -- the two per-stage rates %s within the declared band"
        % (result["verdict"].upper(),
           "agree" if result["verdict"] == "agree" else "do not agree"),
    ]
    return lines


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", dest="mode", action="store_const", const="check",
                      help="verify the plan's recorded C3 block against the record (default)")
    mode.add_argument("--report", dest="mode", action="store_const", const="report",
                      help="print the table only, without reading the plan")
    parser.set_defaults(mode="check")
    args = parser.parse_args(argv)

    record = json.loads(RECORD_PATH.read_text(encoding="utf-8"))
    result = analyze(record)
    print("\n".join(report_lines(result)))
    if args.mode != "check":
        return 0
    problems = check_plan(record, PLAN_PATH.read_text(encoding="utf-8"))
    if problems:
        print()
        for problem in problems:
            print("C3 DRIFT: %s" % problem)
        return 1
    print()
    print("plan C3 block matches the record-derived figures")
    return 0


if __name__ == "__main__":
    sys.exit(main())
