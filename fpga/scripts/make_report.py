#!/usr/bin/env python3
"""Collect the FPGA measurement evidence into fpga/reports/ (DR-0013).

Usage: make_report.py ROOT OUT_DIR SRC_DIR REPORT_DIR FREQ_MHZ

Reads only files the build produced. Anything absent is recorded as
``null`` / "not run"; nothing is inferred. Writes
``fpga-build-report.json`` (machine readable) and copies small logs.
"""
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT, OUT, SRC, REP = (Path(a).resolve() for a in sys.argv[1:5])
FREQ = float(sys.argv[5])
SAMPLES = 176400
CTL_TICKS = 1764


def sha(p: Path):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def run(*cmd):
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True).stdout


def read(p: Path):
    return p.read_text(errors="replace") if p.is_file() else ""


def dist_versions():
    site = next((ROOT / "fpga/build/venv/lib").glob("python*/site-packages"))
    return sorted(d.name[:-len(".dist-info")] for d in site.glob("*.dist-info"))


def provenance():
    status = run("git", "status", "--porcelain=v1", "-uall",
                 "--", ".", ":(exclude)fpga/build")
    diff = run("git", "diff", "HEAD", "--", ".", ":(exclude)fpga/build")
    untracked = [l[3:] for l in status.splitlines() if l.startswith("??")]
    h = hashlib.sha256(diff.encode())
    for f in sorted(untracked):
        p = ROOT / f
        if p.is_file():
            h.update(f.encode() + b"\0" + p.read_bytes())
    return {
        "head": run("git", "rev-parse", "HEAD").strip(),
        "branch": run("git", "rev-parse", "--abbrev-ref", "HEAD").strip(),
        "dirty": bool(status.strip()),
        "porcelain": status.splitlines(),
        "tracked_diff_plus_untracked_sha256": h.hexdigest(),
    }


def parse_stat(text: str):
    """Last 'Number of cells' block of a flat `stat` (cell type -> count)."""
    cells = {}
    for m in re.finditer(r"^\s+(\d+)\s+(\S+)\s*$", text, re.M):
        cells[m.group(2)] = int(m.group(1))
    return cells


def stat_summary(cells: dict):
    def g(*names):
        return sum(v for k, v in cells.items() if any(k.endswith(n) for n in names))
    return {
        "CCU2C_carry_slices (2 LUT4 each)": g("CCU2C"),
        "LUT4": g("LUT4"),
        "PFUMX": g("PFUMX"),
        "L6MUX21": g("L6MUX21"),
        "TRELLIS_FF": g("TRELLIS_FF"),
        "TRELLIS_DPR16X4 (distributed RAM, 16x4)": g("TRELLIS_DPR16X4"),
        "DP16KD (block RAM)": g("DP16KD"),
        "MULT18X18D (DSP)": g("MULT18X18D"),
        "ALU54B": g("ALU54B"),
    }


def main():
    report = {"issue": 80, "decision_record": "DR-0013",
              "clock_constraint_mhz": FREQ,
              "device": "LFE5U-85F CABGA381 speed 6 (nextpnr --85k)"}
    report["git"] = provenance()
    report["python_packages_in_build_venv"] = dist_versions()
    yl = read(OUT / "synth_flat.log")
    m = re.search(r"Yosys (\S+) \(git sha1 (\w+)", yl)
    report["yosys"] = m.group(0) if m else None
    pl = read(OUT / "pnr.log")
    m = re.search(r"nextpnr-ecp5 -- Next Generation[^\n]*", pl)
    report["nextpnr_banner"] = m.group(0) if m else None
    report["hashes_sha256"] = {
        "tb_sv_sources": {p.name: sha(p) for p in sorted((ROOT / "tb/sv").glob("*.sv"))
                          if (SRC / p.name).exists()},
        "staged_sources": {p.name: sha(p) for p in sorted(SRC.glob("*.sv"))},
        "memory_images": {p.name: sha(p) for p in sorted(SRC.glob("*.memh"))},
        "wrapper": sha(ROOT / "fpga/rtl/fpga_oneshot_top.sv"),
        "build_script": sha(ROOT / "fpga/build.sh"),
        "requirements": sha(ROOT / "fpga/requirements.txt"),
        "golden_vector_fixed_voice": sha(ROOT / "sim/reference/fixed-voice-golden-v1.json"),
        "netlist_json": sha(OUT / "fpga_oneshot_top.json"),
    }
    report["staging_manifest"] = json.loads(read(OUT / "sources.json") or "[]")
    report["lut_manifest"] = json.loads(read(OUT / "luts.json") or "{}")
    report["commands"] = (ROOT / "fpga/build.sh").read_text().splitlines()

    flat = parse_stat(read(OUT / "stat_flat.txt"))
    report["synthesis_flat"] = {"cells": stat_summary(flat), "raw_cell_types": flat}
    report["warnings"] = {
        "yosys_slang_async_load_value_missing": len(re.findall(
            r"asynchronous load value missing", read(OUT / "synth_flat.stderr"))),
        "yosys_warning_lines_in_log": len(re.findall(r"^Warning", yl, re.M)),
    }

    # Per-engine bottleneck table: each engine synthesized standalone with the
    # same flow (build.sh `engines`), scaled by its instance count in
    # one_shot_voice_top. Standalone != in-context (no cross-boundary constant
    # propagation), so the sum is an estimate, not the flat total.
    eng = read(OUT / "stat_engines.txt")
    table = {}
    for blk in re.split(r"\n### ", "\n" + eng)[1:]:
        head, _, body = blk.partition("\n")
        name, _, n = head.rpartition(" x")
        s1 = stat_summary(parse_stat(body))
        s1["instances"] = int(n)
        s1["total_for_instances"] = {k: v * int(n) for k, v in s1.items()
                                     if k not in ("instances",)}
        table[name] = s1
    report["synthesis_per_engine_standalone"] = table
    report["engines_failed"] = re.findall(r"^FAILED (\S+)", eng, re.M)

    # Device capacity (LFE5U-85F, from the nextpnr utilisation table / data sheet)
    report["device_capacity"] = {"LUT4": 83640, "TRELLIS_FF": 83640,
                                 "MULT18X18D": 156, "DP16KD": 208,
                                 "source": "Lattice ECP5 family data sheet, LFE5U-85F"}

    rc = read(OUT / "pnr.rc").strip() or "not run"
    report["place_and_route"] = {"nextpnr_exit": rc}
    util = re.findall(r"^Info:\s+(\S+):\s+(\d+)/\s*(\d+)\s+(\d+)%", pl, re.M)
    report["place_and_route"]["utilisation_lines"] = [
        {"resource": r, "used": int(u), "avail": int(a), "pct": int(p)}
        for r, u, a, p in util]
    fm = re.findall(r"Max frequency for clock\s+'([^']+)':\s+([\d.]+) MHz \((PASS|FAIL) at ([\d.]+) MHz\)", pl)
    report["place_and_route"]["timing"] = [
        {"clock": c, "fmax_mhz": float(f), "result": r, "constraint_mhz": float(k)}
        for c, f, r, k in fm]
    errs = [l for l in (pl + read(OUT / "pnr.stderr")).splitlines() if "ERROR" in l]
    report["place_and_route"]["errors"] = errs[:20]
    report["place_and_route"]["warning_count"] = len(re.findall(r"^Warning", pl, re.M))

    # Cycle schedule of the measurement shim (counted from the sequencer;
    # check against fpga/tb/tb_fpga_cycles.sv).
    per_pass = 11 + 4 * SAMPLES
    report["cycles"] = {
        "samples_per_pass": SAMPLES,
        "control_walk_ticks": CTL_TICKS,
        "cycles_per_sample_per_pass": 4,
        "control_walk_cycles": 4 * CTL_TICKS + 2,
        "audio_pass_cycles": per_pass,
        "normalization_replay_pass_cycles (pass 2 re-render)": per_pass,
        "clip_cycles_start_to_done": 2 + 4 * CTL_TICKS + 1 + 2 * per_pass,
        "dr0010_serialized_candidate_clip_cycles": 86_400_000,
        "wall_seconds_IF_the_clock_were_met_NOT_ACHIEVED": {
            str(f): (2 + 4 * CTL_TICKS + 1 + 2 * per_pass) / (f * 1e6)
            for f in (25, 50, 100)},
        "basis": "sequencer arithmetic in fpga/rtl/fpga_oneshot_top.sv; "
                 "see sim_cycle_check for the simulated confirmation",
    }
    cyc = read(OUT / "cycle_check.txt").strip()
    report["sim_cycle_check"] = cyc or "not run"

    REP.mkdir(parents=True, exist_ok=True)
    (REP / "fpga-build-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n")
    for name in ("stat_flat.txt", "stat_engines.txt", "pnr.rc", "luts.json",
                 "sources.json", "cycle_check.txt"):
        if (OUT / name).is_file():
            shutil.copy(OUT / name, REP / name)
    err = read(OUT / "pnr.stderr").splitlines()
    if err:
        keep = err[:40] + ["[...]"] + err[-6:]
        (REP / "pnr_stderr_excerpt.txt").write_text("\n".join(keep) + "\n")
    if pl:
        # keep the tail (utilisation + timing summaries) only
        (REP / "pnr_tail.log").write_text("\n".join(pl.splitlines()[-200:]) + "\n")
    print(json.dumps({k: report[k] for k in ("synthesis_flat", "place_and_route")},
                     indent=1, sort_keys=True)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
