#!/usr/bin/env python3
"""Issue #82: reproducible gf180mcu *synthesis-feasibility* flow for the one-shot voice.

``python3 tools/synthesize_oneshot.py run  [--targets T,T] [--workdir DIR]``
``python3 tools/synthesize_oneshot.py record [--out PATH]``

What this is, and is not
------------------------
It drives the repository-required ``klt`` flow (``klt synthesize`` -> Yosys
+ ABC against a *named* gf180mcu liberty, then ``klt sta`` -> OpenSTA over
the mapped netlist at explicit clock / I/O-delay constraints) on the
whole-voice RTL that issue #79 proved bit-exact in simulation, and writes a
committed *synthesis-feasibility* evidence record
(``asic/evidence/oneshot-synth-feasibility-v1.json``). It is **not** a
layout, signoff, silicon, hardware-playback or sound-fidelity result and the
record says so in machine-checkable fields (``claims``).

Honesty rules enforced here (and re-checked by
``tests/test_synth_feasibility_evidence.py``):

* a target that did not finish is recorded as ``timeout`` / ``error`` with
  the tool's own message -- never as ok, and never with invented numbers;
* ``timing_verdict`` returns ``met`` only for a constrained, complete,
  non-negative-slack report at *every* requested corner; unconstrained or
  unclocked logic can only ever yield ``unverifiable`` (or ``not_met`` when
  a negative slack was measured regardless);
* a step that was not run is ``"not_run"`` in the record, not a pass.

The synthesis view of the RTL (``tools/synth_view.py``) is a *derived*
source: its relationship to the simulated RTL is hashed and stated, not
asserted equal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import synth_view  # noqa: E402

SCHEMA = "gf180-torchsynth/synthesis-feasibility-evidence-v1"
DEFAULT_WORKDIR = ROOT / "asic" / "build"
DEFAULT_RECORD = ROOT / "asic" / "evidence" / "oneshot-synth-feasibility-v1.json"
SCRIPTS_DIR = ROOT / "asic" / "evidence" / "scripts"

# ---- the named library / corner / PDK ---------------------------------------
PDK_VARIANT = "gf180mcuD"
PDK_ROOT = Path.home() / ".ciel"       # ciel store; hash recorded, path is not
CELL_LIBRARY = "gf180mcu_fd_sc_mcu7t5v0"          # 7-track, 5 V I/O-class core
SYNTH_CORNER = "tt_025C_5v00"                     # typical process, 25C, 5.00 V
STA_CORNERS = ("tt_025C_5v00", "ss_125C_4v50", "ff_n40C_5v50")

# ---- explicit constraints (klt exposes only these knobs; see friction file) --
CLOCK_PORT = "clk"
#: DR-0010 declared candidate clocks are 25/50/100 MHz and none is selected;
#: 25 MHz is the schedule register's own bound clock (SCHED_BOUND_CLOCK_MHZ).
CLOCK_PERIOD_NS = 40.0
INPUT_DELAY_NS = 8.0     # 20 % of the period on every non-clock input
OUTPUT_DELAY_NS = 8.0    # 20 % of the period on every output
RESET_ASSUMPTION = (
    "rst is an asynchronous active-high input (posedge rst in every engine); "
    "it is constrained like any other non-clock input (input delay 8.0 ns), "
    "so recovery/removal against clk are timed; no reset synchronizer exists "
    "in this RTL and none is assumed."
)
ENABLE_ASSUMPTION = (
    "ctl_*/audio phase enables are ordinary synchronous inputs sequenced by "
    "the host; no generated clock, clock gate or multicycle exception exists "
    "or is declared, so every path is timed single-cycle at the one clock. "
    "klt sta cannot express multicycle/false-path exceptions (friction F5)."
)
MEMORY_TREATMENT = (
    "no memory macro / SRAM is instantiated or assumed: the quarter-wave "
    "ROMs are plain 'reg [..] rom [0:4096]' arrays initialised by "
    "$readmemh in the synthesis view and left to Yosys memory inference "
    "(memory_map to flip-flops/mux logic or constant folding); klt's "
    "memory_mapping evidence records what actually happened."
)

KLT_DEFAULT_YOSYS_TIMEOUT_S = 300.0

TOP = "one_shot_voice_top"
PKG = synth_view.PKG
ALL_SOURCES = synth_view.SOURCE_ORDER
#: Per-target source sets (package first, dependency order).
TARGETS = {
    "adsr_engine": ["adsr_engine.sv"],
    "lfo_vca_engine": ["lfo_vca_engine.sv"],
    "mod_matrix_engine": ["mod_matrix_engine.sv"],
    "upsample_engine": ["upsample_engine.sv"],
    "quarter_wave_lut": ["quarter_wave_lut.sv"],
    "sine_vco_engine": ["sine_vco_engine.sv"],
    "square_saw_vco_engine": ["quarter_wave_lut.sv", "square_saw_vco_engine.sv"],
    "noise_stream_dut": ["noise_stream_dut.sv"],
    "audio_mix_engine": ["audio_mix_engine.sv"],
    "normalization_replay_engine": ["normalization_replay_engine.sv"],
    TOP: [name for name in ALL_SOURCES if name != PKG],
}
TARGET_ORDER = tuple(TARGETS)


# =============================================================================
# Pure policy functions (unit-tested without any tool installed)
# =============================================================================
def timing_verdict(sta_by_corner: dict, required_corners=STA_CORNERS) -> dict:
    """Decide ``met`` / ``not_met`` / ``unverifiable`` from per-corner STA.

    ``sta_by_corner`` maps corner -> the relevant ``klt sta`` fields:
    ``timing_status``, ``worst_slack_ns``, ``worst_hold_slack_ns``,
    ``setup_violation_count``, ``hold_violation_count`` and
    ``constraint_completeness`` (``status``). ``met`` requires *every*
    required corner present, constrained, complete and non-negative on both
    setup and hold. Unconstrained or unclocked logic can never produce
    ``met``; a measured negative slack always produces ``not_met``.
    """

    reasons: list[str] = []
    negative = False
    unverifiable = False
    for corner in required_corners:
        report = sta_by_corner.get(corner)
        if not isinstance(report, dict):
            unverifiable = True
            reasons.append("%s: no STA report" % corner)
            continue
        status = report.get("timing_status")
        completeness = (report.get("constraint_completeness") or {}).get("status")
        setup = report.get("worst_slack_ns")
        hold = report.get("worst_hold_slack_ns")
        if status == "constrained":
            for label, slack in (("setup", setup), ("hold", hold)):
                if isinstance(slack, (int, float)) and slack < 0:
                    negative = True
                    reasons.append("%s: negative %s slack %.3f ns" % (corner, label, slack))
        else:
            unverifiable = True
            reasons.append("%s: timing_status=%r (not a measurement)" % (corner, status))
        if completeness != "complete":
            unverifiable = True
            reasons.append("%s: constraint_completeness=%r" % (corner, completeness))
        if status == "constrained" and not (
            isinstance(setup, (int, float)) and isinstance(hold, (int, float))
        ):
            unverifiable = True
            reasons.append("%s: missing setup/hold slack" % corner)
        for key in ("setup_violation_count", "hold_violation_count"):
            count = report.get(key)
            if isinstance(count, int) and count > 0:
                negative = True
                reasons.append("%s: %s=%d" % (corner, key, count))
    if negative:
        verdict = "not_met"
    elif unverifiable:
        verdict = "unverifiable"
    else:
        verdict = "met"
        reasons.append("all required corners constrained, complete, slack >= 0")
    return {"verdict": verdict, "reasons": reasons,
            "required_corners": list(required_corners)}


def clip_cycle_schedule() -> dict:
    """Cycle schedule of THIS RTL, from the top's documented phase contract.

    Derived arithmetic, not a simulation measurement: control walk = 1,764
    ticks x 4 cycles; audio walk = 176,400 samples x 4 cycles per pass x 2
    passes (``tb/sv/one_shot_voice_top.sv`` "Phase contract"). The noise
    priming (4 bytes) and the replay engine's once-per-clip divide are
    excluded (a handful of cycles).
    """

    control = 1764 * 4
    audio = 176400 * 4 * 2
    cycles = control + audio
    return {
        "source": "tb/sv/one_shot_voice_top.sv phase contract (derived arithmetic; "
                  "not a simulation-measured count)",
        "control_cycles": control,
        "audio_cycles": audio,
        "cycles_per_clip": cycles,
        "min_clock_mhz_for_1x_realtime_4s": round(cycles / 4.0 / 1e6, 4),
        "dr0010_serialized_candidate": {
            "note": "NOT implemented by this RTL (DR-0010 schedule-candidate)",
            "counted_cycles_per_sample": 145,
            "clip_sample_slots": 352800,
            "cycles_per_clip_at_T0": 145 * 352800,
        },
    }


def realtime_factor(fmax_mhz: float | None) -> float | None:
    if not isinstance(fmax_mhz, (int, float)) or fmax_mhz <= 0:
        return None
    return round((clip_cycle_schedule()["cycles_per_clip"] / (fmax_mhz * 1e6)) / 4.0, 3)


# =============================================================================
# Helpers
# =============================================================================
def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sh(cmd, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def tool_version(cmd) -> str:
    try:
        out = sh(cmd, timeout=60)
        return (out.stdout or out.stderr).strip().splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError):
        return "unavailable"


def klt_python() -> str | None:
    """Interpreter klt itself runs under (its shebang)."""
    path = shutil.which("klt")
    if not path:
        return None
    first = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()[0]
    return first[2:].strip() if first.startswith("#!") else None


def rel(path: Path) -> str:
    return Path(path).resolve().relative_to(ROOT).as_posix()


def reap_orphan_abc() -> list[int]:
    """Kill ``yosys-abc`` left behind when klt/Yosys was killed on timeout.

    A killed ``yosys`` does not take its ``yosys-abc`` child with it (friction
    F3); on a shared worker that is a CPU leak. Only processes whose cwd is
    this repo and whose parent is not a ``yosys`` process are touched.
    """

    killed = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            comm = (entry / "comm").read_text().strip()
            if comm != "yosys-abc":
                continue
            if Path(os.readlink(entry / "cwd")).resolve() != ROOT:
                continue
            stat = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            ppid = int(stat[1])
            parent_comm = (Path("/proc") / str(ppid) / "comm").read_text().strip()
            if parent_comm == "yosys":
                continue
            os.kill(int(entry.name), 9)
            killed.append(int(entry.name))
        except (OSError, ValueError, IndexError):
            continue
    return killed


def sanitize(value):
    """Strip absolute host paths from every string in a JSON-like value."""
    if isinstance(value, str):
        return value.replace(str(ROOT), "<repo>").replace(str(Path.home()), "<home>")
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    if isinstance(value, dict):
        return {k: sanitize(v) for k, v in value.items()}
    return value


def parse_json_text(*texts):
    for text in texts:
        if not text:
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            continue
    return None


# =============================================================================
# The flow
# =============================================================================
def write_synth_request(workdir: Path, target: str, run_id: str) -> Path:
    req_dir = workdir / "req"
    req_dir.mkdir(parents=True, exist_ok=True)
    sources = [PKG] + TARGETS[target]
    request = {
        "schema": "klt.synthesize.request/1",
        "engine": "yosys",
        "run_id": run_id,
        "sources": [os.path.relpath(workdir / "src" / s, req_dir) for s in sources],
        "hdl_toplevel": target,
        "pdk": {"cell_library": CELL_LIBRARY, "corner": SYNTH_CORNER},
        "constraints": {"clock_period_ns": CLOCK_PERIOD_NS},
    }
    path = req_dir / ("synth_%s.json" % target)
    path.write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")
    return path


def run_klt_synthesize(request: Path, timeout_s: float) -> dict:
    """Run ``klt synthesize``; returns an outcome dict (never raises on tool failure)."""

    base = ["--pdk", PDK_VARIANT, "--pdk-root", str(PDK_ROOT), "--format", "json"]
    extended = timeout_s > KLT_DEFAULT_YOSYS_TIMEOUT_S
    if not extended:
        cmd = ["klt", "synthesize", rel(request)] + base
        display = "klt synthesize %s --pdk %s --pdk-root <ciel store> --format json" % (
            rel(request), PDK_VARIANT)
    else:
        py = klt_python()
        if py is None:
            return {"status": "error", "message": "cannot locate klt interpreter"}
        code = (
            "import sys, klayout_tools.synthesize as S; "
            "S._run_yosys.__kwdefaults__['timeout_s'] = float(sys.argv[1]); "
            "from klayout_tools.cli import main; "
            "sys.exit(main(['synthesize'] + sys.argv[2:]))"
        )
        cmd = [py, "-I", "-c", code, str(timeout_s), rel(request)] + base
        display = (
            "klt-interpreter -I -c 'S._run_yosys.__kwdefaults__[\"timeout_s\"]=%g; "
            "klayout_tools.cli.main([\"synthesize\", ...])' %s --pdk %s "
            "--pdk-root <ciel store> --format json" % (timeout_s, rel(request), PDK_VARIANT)
        )
    started = time.monotonic()
    try:
        done = sh(cmd, cwd=ROOT, timeout=timeout_s + 180)
        stdout, stderr, rc = done.stdout, done.stderr, done.returncode
    except subprocess.TimeoutExpired as exc:
        stdout, stderr, rc = exc.stdout or "", exc.stderr or "", None
    elapsed = round(time.monotonic() - started, 1)
    reaped = reap_orphan_abc()
    payload = parse_json_text(stdout, stderr)
    outcome = {"command": display, "yosys_timeout_s": timeout_s,
               "elapsed_s": elapsed, "exit_code": rc, "reaped_orphan_abc": len(reaped)}
    if rc == 0 and isinstance(payload, dict) and payload.get("status") == "ok":
        outcome["status"] = "ok"
        outcome["response"] = payload
        return outcome
    message = ""
    if isinstance(payload, dict):
        message = (payload.get("error") or {}).get("message", "") or ""
    message = message or (stderr or stdout or "no output").strip()[-600:]
    outcome["message"] = message
    outcome["status"] = "timeout" if "did not complete within" in message or rc is None else "error"
    return outcome


def write_sta_request(workdir: Path, target: str, netlist: Path) -> Path:
    req_dir = workdir / "req"
    request = {
        "schema": "klt.sta.request/1",
        "verilog": os.path.relpath(netlist, req_dir),
        "hdl_toplevel": target,
        "pdk": {"cell_library": CELL_LIBRARY, "corners": list(STA_CORNERS)},
        "constraints": {
            "clock_port": CLOCK_PORT,
            "clock_period_ns": CLOCK_PERIOD_NS,
            "input_delay_ns": INPUT_DELAY_NS,
            "output_delay_ns": OUTPUT_DELAY_NS,
        },
    }
    path = req_dir / ("sta_%s.json" % target)
    path.write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")
    return path


def run_klt_sta(request: Path, timeout_s: float = 1800.0) -> dict:
    cmd = ["klt", "sta", rel(request), "--pdk", PDK_VARIANT,
           "--pdk-root", str(PDK_ROOT), "--format", "json"]
    display = "klt sta %s --pdk %s --pdk-root <ciel store> --format json" % (
        rel(request), PDK_VARIANT)
    started = time.monotonic()
    try:
        done = sh(cmd, cwd=ROOT, timeout=timeout_s)
        stdout, stderr, rc = done.stdout, done.stderr, done.returncode
    except subprocess.TimeoutExpired as exc:
        stdout, stderr, rc = exc.stdout or "", exc.stderr or "", None
    payload = parse_json_text(stdout, stderr)
    outcome = {"command": display, "elapsed_s": round(time.monotonic() - started, 1),
               "exit_code": rc}
    if rc == 0 and isinstance(payload, dict) and payload.get("status") == "ok":
        outcome["status"] = "ok"
        outcome["response"] = payload
    else:
        message = ""
        if isinstance(payload, dict):
            message = (payload.get("error") or {}).get("message", "") or ""
        outcome["status"] = "timeout" if rc is None else "error"
        outcome["message"] = message or (stderr or stdout or "no output").strip()[-600:]
    return outcome


def summarize_synth(response: dict) -> dict:
    keys = ("instance_count", "area_um2", "sequential_area_um2",
            "instance_counts_by_type", "timing", "structural", "warnings",
            "memory_mapping", "blackbox_modules", "cell_exclusions",
            "netlist_sha256", "sta", "sta_unavailable_reason",
            "leakage_power_nw", "engine_version", "provenance")
    out = {k: response.get(k) for k in keys}
    mm = out.get("memory_mapping")
    if isinstance(mm, dict):
        mm = dict(mm)
        mm.pop("script_path", None)
        out["memory_mapping"] = mm
    return out


def hierarchy_from_stats(stats_path: Path) -> list:
    """Per-module cell count / area from klt's captured ``stat -json``."""
    if not stats_path.exists():
        return []
    data = json.loads(stats_path.read_text(encoding="utf-8"))
    rows = []
    for name, mod in sorted((data.get("modules") or {}).items()):
        rows.append({"module": name.lstrip("\\"),
                     "cells": mod.get("num_cells"),
                     "area_um2": mod.get("area")})
    return rows


def summarize_sta(response: dict) -> dict:
    corners = {}
    for entry in response.get("corners", []) or []:
        name = entry.get("corner") or (entry.get("provenance", {}).get("deck", {}) or {}).get("name", "")
        name = name.split("__")[-1] if name else name
        corners[name] = {k: entry.get(k) for k in (
            "timing_status", "worst_slack_ns", "total_negative_slack_ns",
            "worst_hold_slack_ns", "total_negative_hold_slack_ns", "fmax_mhz",
            "setup_violation_count", "hold_violation_count", "worst_setup_path",
            "worst_hold_path", "constraint_completeness")}
    return corners


def backfill_failure(workdir: Path, result: dict) -> dict:
    """For a target that did not finish: where Yosys was when it stopped.

    Reads the retained Yosys log and generated script of the failed run (klt
    keeps both) -- so the record says *which pass* the timeout landed in and
    pins the exact generated script, instead of only "timed out".
    """

    synth = result["synthesize"]
    if synth.get("status") == "not_run":
        return result
    if synth.get("status") == "ok":
        run_dir = workdir / "req" / ".klt" / "synthesize" / result["request"]["run_id"]
        log = run_dir / ("synth_%s.run.log" % result["target"])
        if log.exists():
            text = log.read_text(encoding="utf-8", errors="replace")
            synth["multiplier_mapping_from_yosys_log"] = {
                "maccmap_extmapper_uses": len(re.findall(r"Using extmapper maccmap", text)),
                "booth_pass_runs": len(re.findall(r"Executing BOOTH pass", text)),
                "macc_models_created": len(re.findall(r"creating \$macc model", text)),
                "note": "default Yosys synth: $mul -> alumacc $macc -> maccmap adder trees; no Booth recoding",
            }
        return result
    if "last_stage" in synth:
        return result
    run_dir = (workdir / "req" / ".klt" / "synthesize" / result["request"]["run_id"])
    target = result["target"]
    log = run_dir / ("synth_%s.run.log" % target)
    if log.exists():
        stages = re.findall(r"^(\d+(?:\.\d+)*\. Executing .*)$",
                            log.read_text(encoding="utf-8", errors="replace"), re.M)
        synth["last_stage"] = stages[-1] if stages else None
        synth["log_lines"] = len(log.read_text(encoding="utf-8", errors="replace").splitlines())
    script = run_dir / ("synth_%s.ys" % target)
    if script.exists():
        synth["script_sha256"] = sha256_file(script)
        SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(script, SCRIPTS_DIR / ("synth_%s.ys" % target))
    return result


def run_target(workdir: Path, target: str, timeout_s: float, force: bool) -> dict:
    result_path = workdir / "results" / ("%s.json" % target)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    if result_path.exists() and not force:
        return json.loads(result_path.read_text(encoding="utf-8"))
    run_id = "r%d-%s" % (int(time.time()), re.sub(r"[^A-Za-z0-9]", "", target)[:20])
    request = write_synth_request(workdir, target, run_id)
    outcome = run_klt_synthesize(request, timeout_s)
    result = {"target": target, "request": json.loads(request.read_text()),
              "synthesize": {k: v for k, v in outcome.items() if k != "response"}}
    if outcome["status"] == "ok":
        resp = outcome["response"]
        result["synthesize"]["summary"] = summarize_synth(resp)
        script = ROOT / resp["script_path"]["path"]
        result["synthesize"]["script_sha256"] = sha256_file(script)
        SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(script, SCRIPTS_DIR / ("synth_%s.ys" % target))
        stats = ROOT / resp["netlist_path"]["path"].replace("_synth.v", "_stats.json")
        result["synthesize"]["hierarchy"] = hierarchy_from_stats(stats)
        (workdir / "resp").mkdir(exist_ok=True)
        (workdir / "resp" / ("%s.json" % target)).write_text(
            json.dumps(resp, indent=2) + "\n", encoding="utf-8")
        netlist = ROOT / resp["netlist_path"]["path"]
        sta_req = write_sta_request(workdir, target, netlist)
        sta = run_klt_sta(sta_req)
        result["sta"] = {k: v for k, v in sta.items() if k != "response"}
        if sta["status"] == "ok":
            corners = summarize_sta(sta["response"])
            result["sta"]["request"] = json.loads(sta_req.read_text())
            result["sta"]["corners"] = corners
            result["sta"]["engine_version"] = sta["response"].get("engine_version")
            result["sta"]["klt_version"] = (sta["response"].get("provenance") or {}).get("klt_version")
            result["sta"]["netlist_sha256"] = ((sta["response"].get("provenance") or {})
                                               .get("input") or {}).get("content_hash")
            result["sta"]["verdict"] = timing_verdict(corners)
        else:
            result["sta"]["verdict"] = {"verdict": "unverifiable",
                                        "reasons": ["klt sta did not produce a report"],
                                        "required_corners": list(STA_CORNERS)}
    else:
        result["sta"] = {"status": "not_run",
                         "reason": "no netlist: klt synthesize status=%s" % outcome["status"],
                         "verdict": {"verdict": "unverifiable",
                                     "reasons": ["no mapped netlist to time"],
                                     "required_corners": list(STA_CORNERS)}}
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    return result


# =============================================================================
# Audits (computed from the real sources / real tool output)
# =============================================================================
SIM_ONLY = re.compile(
    r"\$value\$plusargs|\$readmemh|\$finish|\$display|\$fwrite|\$fopen|\$error|\$fatal|\$random|\binitial\b")
X_USE = re.compile(r"\d*'s?[bhd]x|\bcasex\b|===|!==")


def audit_sources() -> dict:
    per_file = {}
    for name in ALL_SOURCES:
        text = re.sub(r"//.*", "", (synth_view.SV / name).read_text(encoding="utf-8"))
        per_file[name] = {
            "simulation_only_constructs": sorted(set(SIM_ONLY.findall(text))),
            "x_literals_or_x_compares": len(X_USE.findall(text)),
            "always_blocks": len(re.findall(r"\balways\b", text)),
        }
    return per_file


FRONTEND_SCRIPT = """\
read_verilog {files}
hierarchy -check -top {top}
proc
opt_clean
tee -o {check_hier} check
write_json {json}
flatten
opt_clean
tee -o {check_flat} check
"""


def _module_totals(modules: dict, name: str, memo: dict) -> dict:
    """Instance-weighted coarse-cell inventory of ``name`` (recursive)."""
    if name in memo:
        return memo[name]
    total = {"cells": {}, "ff_bits": 0, "mul": [], "div_mod_cells": 0}
    for cell in modules[name]["cells"].values():
        ctype, params = cell["type"], cell.get("parameters", {})
        if ctype in modules:
            sub = _module_totals(modules, ctype, memo)
            for key, n in sub["cells"].items():
                total["cells"][key] = total["cells"].get(key, 0) + n
            total["ff_bits"] += sub["ff_bits"]
            total["mul"] += sub["mul"]
            total["div_mod_cells"] += sub["div_mod_cells"]
            continue
        total["cells"][ctype] = total["cells"].get(ctype, 0) + 1
        if ctype.startswith(("$dff", "$adff", "$sdff", "$dlatch", "$adlatch")):
            total["ff_bits"] += int(str(params.get("WIDTH", "0")), 2)
        if ctype == "$mul":
            total["mul"].append([int(str(params["A_WIDTH"]), 2), int(str(params["B_WIDTH"]), 2),
                                 int(str(params["Y_WIDTH"]), 2)])
        if ctype in ("$div", "$mod", "$divfloor", "$modfloor"):
            total["div_mod_cells"] += 1
    memo[name] = total
    return total


def audit_frontend(workdir: Path) -> dict:
    """Yosys front-end audit of the *whole voice*: no ABC, so it terminates.

    ``proc`` exposes inferred latches; ``check`` reports combinational
    loops, multiple drivers and undriven wires (run once hierarchically and
    once on the flattened design, so a loop that crosses an instance
    boundary is seen); the coarse netlist gives the multiplier inventory and
    flip-flop bit count before any mapping.
    """

    out_dir = workdir / "frontend"
    out_dir.mkdir(parents=True, exist_ok=True)
    files = " ".join(rel(workdir / "src" / n) for n in ALL_SOURCES)
    script = FRONTEND_SCRIPT.format(
        files=files, top=TOP, check_hier=rel(out_dir / "check_hier.txt"),
        json=rel(out_dir / "coarse.json"), check_flat=rel(out_dir / "check_flat.txt"))
    script_path = out_dir / "frontend.ys"
    script_path.write_text(script, encoding="utf-8")
    started = time.monotonic()
    done = sh(["yosys", "-l", rel(out_dir / "yosys.log"), "-q", "-s", rel(script_path)],
              cwd=ROOT, timeout=1200)
    log = (out_dir / "yosys.log").read_text(encoding="utf-8", errors="replace")
    warnings: dict[str, int] = {}
    for line in log.splitlines():
        if line.startswith("Warning:"):
            key = re.sub(r"\\[\w$.]+", "<id>", line.split(" See ")[0])[:100]
            warnings[key] = warnings.get(key, 0) + 1
    data = json.loads((out_dir / "coarse.json").read_text(encoding="utf-8"))
    modules = data["modules"]
    totals = _module_totals(modules, TOP, {})
    cells = totals["cells"]
    widths = {}
    for a, b, y in totals["mul"]:
        key = "%dx%d->%d" % (a, b, y)
        widths[key] = widths.get(key, 0) + 1
    problems = [int(m) for m in re.findall(r"Found and reported (\d+) problems", log)]
    div_sites = sorted({
        "%s %s %sb/%sb" % ((c.get("attributes", {}).get("src") or "?").split(":", 1)[0].split("/")[-1]
                           + ":" + (c.get("attributes", {}).get("src") or "?:?").split(":", 1)[-1].split("-")[0],
                           c["type"], int(str(c["parameters"]["A_WIDTH"]), 2),
                           int(str(c["parameters"]["B_WIDTH"]), 2))
        for m in modules.values() for c in m["cells"].values()
        if c["type"] in ("$div", "$mod", "$divfloor", "$modfloor")})
    return {
        "command": "yosys -s <read_verilog synthesis view; hierarchy -check -top %s; proc; opt_clean; check; write_json; flatten; opt_clean; check>" % TOP,
        "yosys": tool_version(["yosys", "-V"]), "exit_code": done.returncode,
        "elapsed_s": round(time.monotonic() - started, 1),
        "check_problem_counts_hier_then_flat": problems,
        "check_problems_total": sum(problems),
        "latch_cells": sum(n for t, n in cells.items() if "latch" in t),
        "ff_bits_pre_mapping": totals["ff_bits"],
        "memory_read_ports_pre_mapping": cells.get("$memrd", 0) + cells.get("$memrd_v2", 0),
        "memory_init_cells_pre_mapping": cells.get("$meminit_v2", 0) + cells.get("$meminit", 0),
        "div_mod_operator_cells": totals["div_mod_cells"],
        "div_mod_operator_sites": div_sites,
        "multiplier_operator_cells": len(totals["mul"]),
        "multiplier_widths_a_x_b_to_y": dict(sorted(widths.items())),
        "coarse_cells_by_type": dict(sorted(cells.items())),
        "yosys_warnings": dict(sorted(warnings.items())),
        "note": ("pre-mapping inventory. Yosys' default synth maps $mul via alumacc/maccmap "
                 "adder trees; no multiplier was mapped to gf180 cells in this record "
                 "except inside quarter_wave_lut (see targets). Division is not an "
                 "operator here: restoring long division is unrolled shift/subtract logic."),
    }


def audit_verilator(workdir: Path) -> dict:
    """``verilator --lint-only -Wall`` over the *original* simulated RTL."""
    files = [str(synth_view.SV / n) for n in ALL_SOURCES]
    version = tool_version(["verilator", "--version"])
    cmd = ["verilator", "--lint-only", "-Wall", "-Wno-fatal", "--top-module", TOP] + files
    done = sh(cmd, cwd=ROOT, timeout=600)
    categories: dict[str, int] = {}
    by_file: dict[str, dict[str, int]] = {}
    for match in re.finditer(r"%Warning-([A-Z0-9_]+): (\S+?):\d+", done.stderr):
        cat, fname = match.group(1), Path(match.group(2)).name
        categories[cat] = categories.get(cat, 0) + 1
        by_file.setdefault(cat, {})
        by_file[cat][fname] = by_file[cat].get(fname, 0) + 1
    errors = len(re.findall(r"^%Error", done.stderr, re.M))
    return {"tool": version,
            "command": "verilator --lint-only -Wall -Wno-fatal --top-module %s <tb/sv RTL, compile order>" % TOP,
            "exit_code": done.returncode, "errors": errors,
            "warning_categories": dict(sorted(categories.items())),
            "warnings_by_category_and_file": {c: dict(sorted(v.items())) for c, v in sorted(by_file.items())}}


# =============================================================================
# Record assembly
# =============================================================================
def git_identity() -> dict:
    """Base commit plus whether the *RTL/spec inputs* match it.

    The producing tools are introduced by the same PR as this record, so the
    whole tree is expectedly dirty at generation time; what matters for the
    claim is that the RTL, frozen vectors and spec inputs are exactly the
    committed ones (their hashes are also recorded and re-checked by the
    test suite).
    """

    head = sh(["git", "rev-parse", "HEAD"], cwd=ROOT).stdout.strip()
    dirty = sh(["git", "status", "--porcelain", "--untracked-files=no", "--",
                "tb", "src", "spec", "sim/reference"], cwd=ROOT).stdout.strip()
    return {"git_head": head, "rtl_and_spec_inputs_match_git_head": not dirty,
            "producer_tools_sha256": {
                name: sha256_file(ROOT / "tools" / name)
                for name in ("synth_view.py", "synthesize_oneshot.py")},
            "producer_note": "tools are introduced by the PR that adds this record; "
                             "git_head is the base commit they were run on"}


def liberty_identity() -> dict:
    out = sh(["klt", "pdk", "find", "--pdk", PDK_VARIANT, "--pdk-root", str(PDK_ROOT),
              "--format", "json"], cwd=ROOT)
    info = parse_json_text(out.stdout) or {}
    lib = (PDK_ROOT / PDK_VARIANT / "libs.ref" / CELL_LIBRARY / "lib"
           / ("%s__%s.lib" % (CELL_LIBRARY, SYNTH_CORNER)))
    return {"pdk_variant": PDK_VARIANT, "open_pdks_version": info.get("version"),
            "cell_library": CELL_LIBRARY, "synthesis_corner": SYNTH_CORNER,
            "liberty_file": "%s__%s.lib" % (CELL_LIBRARY, SYNTH_CORNER),
            "liberty_sha256": sha256_file(lib) if lib.exists() else None,
            "sta_corners": list(STA_CORNERS)}


def derived_estimates(results: dict) -> dict:
    """Plain multiplication of *measured* leaf numbers by instance counts.

    Labelled arithmetic, not a synthesis result: nothing here was mapped as
    a whole, and sharing/optimisation across instances is not modelled.
    """

    out = {"note": "instance-count x measured-leaf arithmetic; NOT a synthesis or area measurement of the whole voice"}
    up = results.get("upsample_engine", {}).get("synthesize", {})
    if up.get("status") == "ok":
        sm = up["summary"]
        out["five_upsample_column_instances"] = {
            "instances": 5, "area_um2": round(5 * sm["area_um2"], 1),
            "flip_flop_cells": 5 * sum(n for k, n in sm["instance_counts_by_type"].items() if "__dff" in k),
        }
    qwl = results.get("quarter_wave_lut", {}).get("synthesize", {})
    if qwl.get("status") == "ok":
        out["five_rom_copies_as_logic"] = {
            "instances": 5, "area_um2": round(5 * qwl["summary"]["area_um2"], 1),
            "note": "2 quarter_wave_lut in square_saw_vco_engine + 1 inline in sine_vco_engine + 2 in lfo_vca_engine; the 24-bit and 25-bit ROM variants differ slightly, so this is approximate",
        }
    return out


def build_record(workdir: Path) -> dict:
    ledger = synth_view.build(workdir / "src")
    results = {}
    for target in TARGET_ORDER:
        path = workdir / "results" / ("%s.json" % target)
        if path.exists():
            results[target] = backfill_failure(workdir, json.loads(path.read_text(encoding="utf-8")))
        else:
            results[target] = {"target": target,
                               "synthesize": {"status": "not_run"},
                               "sta": {"status": "not_run",
                                       "verdict": {"verdict": "unverifiable",
                                                   "reasons": ["target was not run"],
                                                   "required_corners": list(STA_CORNERS)}}}
    top = results[TOP]
    leaf_ok = [t for t, r in results.items() if r["synthesize"].get("status") == "ok"]
    overall = {
        "whole_voice_synthesis": top["synthesize"].get("status"),
        "whole_voice_timing_verdict": top["sta"]["verdict"]["verdict"],
        "modules_synthesized": sorted(t for t in leaf_ok if t != TOP),
        "modules_not_completed": sorted(
            t for t, r in results.items() if r["synthesize"].get("status") != "ok"),
    }
    env = parse_json_text(sh(["klt", "env-provenance", "emit", "--format", "json"], cwd=ROOT).stdout)
    record = {
        "schema": SCHEMA,
        "label": "synthesis feasibility",
        "claims": {"synthesis_feasibility": True, "layout": False, "signoff": False,
                   "silicon": False, "hardware_playback": False, "sound_fidelity": False},
        "scope": ("gf180mcu synthesis feasibility of the issue-#79 whole-voice one-shot "
                  "RTL (tb/sv/one_shot_voice_top.sv and its engines) through klt "
                  "(Yosys+ABC, OpenSTA at declared constraints), on a derived "
                  "synthesis view of that RTL. No layout, signoff, silicon, hardware "
                  "playback or sound-fidelity claim."),
        "identity": dict(git_identity(),
                         rtl_sha256=ledger["original_sha256"],
                         synthesis_view_sha256=ledger["synthesis_view_sha256"],
                         synthesis_view_rewritten_files=ledger["rewritten"],
                         rom_payload_sha256=ledger["memh_sha256"]),
        "tools": {
            "klt": tool_version(["klt", "--version"]),
            "yosys": tool_version(["yosys", "-V"]),
            "openroad": tool_version(["openroad", "-version"]),
            "verilator": tool_version(["verilator", "--version"]),
            "environment_provenance": env,
        },
        "library": liberty_identity(),
        "constraints": {
            "clock_port": CLOCK_PORT, "clock_period_ns": CLOCK_PERIOD_NS,
            "clock_mhz": 1000.0 / CLOCK_PERIOD_NS,
            "clock_basis": "DR-0010 bound clock (SCHED_BOUND_CLOCK_MHZ=25); candidate clocks 25/50/100 MHz, none selected",
            "input_delay_ns": INPUT_DELAY_NS, "output_delay_ns": OUTPUT_DELAY_NS,
            "reset_assumption": RESET_ASSUMPTION,
            "generated_clocks_and_enables": ENABLE_ASSUMPTION,
            "memory_treatment": MEMORY_TREATMENT,
            "wire_load": "none: klt sta verilog mode, liberty default, no placement/parasitics",
            "abc_delay_target_ps": CLOCK_PERIOD_NS * 1000,
            "klt_yosys_timeout_s": KLT_DEFAULT_YOSYS_TIMEOUT_S,
        },
        "targets": results,
        "overall": overall,
        "sample_schedule": dict(
            clip_cycle_schedule(),
            achieved=None,
            achieved_reason=("no timing-verified fmax exists for the whole voice: "
                             "see overall.whole_voice_synthesis / whole_voice_timing_verdict"),
        ),
        "derived_arithmetic_not_measured": derived_estimates(results),
        "audit": {
            "source_scan": audit_sources(),
            "yosys_frontend_whole_voice": audit_frontend(workdir),
            "verilator_lint_original_rtl": audit_verilator(workdir),
            "synthesis_view_rewrites": [
                "constants package -> `define macros (klt read_verilog has no -sv)",
                "ROM $value$plusargs/$readmemh initial blocks -> fixed $readmemh of the committed-hash ROM payload",
                "upsample_engine +max_j plusarg -> constant 0 (the full walk)",
                "adsr_engine/lfo_vca_engine division loop start 'den_msb + 60' -> constant start with guard 'if (i <= den_msb + 60)'",
            ],
        },
    }
    return sanitize(record)


# =============================================================================
def cmd_run(args) -> int:
    workdir = Path(args.workdir).resolve()
    synth_view.build(workdir / "src")
    wanted = args.targets.split(",") if args.targets else list(TARGET_ORDER)
    unknown = [t for t in wanted if t not in TARGETS]
    if unknown:
        print("unknown targets: %s" % ", ".join(unknown))
        return 2
    for target in wanted:
        timeout = args.timeouts.get(target, args.timeout)
        print("== %s (yosys timeout %gs)" % (target, timeout), flush=True)
        result = run_target(workdir, target, timeout, args.force)
        print("   synthesize=%s sta-verdict=%s" % (
            result["synthesize"].get("status"), result["sta"]["verdict"]["verdict"]), flush=True)
    return 0


def cmd_record(args) -> int:
    workdir = Path(args.workdir).resolve()
    record = build_record(workdir)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("wrote %s" % out)
    print(json.dumps(record["overall"], indent=2))
    return 0


def parse_timeouts(text: str) -> dict:
    out = {}
    for item in filter(None, (text or "").split(",")):
        name, _, value = item.partition("=")
        out[name] = float(value)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--workdir", default=str(DEFAULT_WORKDIR))
    run.add_argument("--targets", default="")
    run.add_argument("--timeout", type=float, default=KLT_DEFAULT_YOSYS_TIMEOUT_S,
                     help="Yosys timeout per target in seconds (>300 patches klt's fixed bound)")
    run.add_argument("--timeouts", type=parse_timeouts, default={},
                     help="per-target overrides, e.g. upsample_engine=1800")
    run.add_argument("--force", action="store_true")
    rec = sub.add_parser("record")
    rec.add_argument("--workdir", default=str(DEFAULT_WORKDIR))
    rec.add_argument("--out", default=str(DEFAULT_RECORD))
    args = parser.parse_args(argv)
    return cmd_run(args) if args.cmd == "run" else cmd_record(args)


if __name__ == "__main__":
    sys.exit(main())
