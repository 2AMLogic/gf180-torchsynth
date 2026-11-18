#!/usr/bin/env python3
"""Plan check C4: per-engine Yosys ltp/stat depth for the timed-out engines.

Four engines (``sine_vco_engine``, ``adsr_engine``, ``lfo_vca_engine``,
``square_saw_vco_engine``) timed out in ``abc`` at the 300 s bound and
``mod_matrix_engine`` even at 2,700 s in the committed synthesis-feasibility
record (``asic/evidence/oneshot-synth-feasibility-v1.json``), so no
path-depth number exists for them there. C4 asks whether a cheap, unmapped
static analysis can produce one inside minutes.

This driver answers exactly that, nothing stronger. Per engine it runs ONE
local ``yosys`` process over the derived synthesis view
(``tools/synth_view.py`` output) with a scoped unmapped flow

    read_verilog ...; hierarchy -top <engine>;
    synth -run :fine -flatten; ltp -noff; stat

and records ``ltp``'s longest-path **cell-level count of the unmapped
netlist** and ``stat``'s cell-type counts. This is a structural depth figure,
*not* a time: no PDK, no liberty, no ``abc -D`` or any mapping, no
``klt synthesize``/``klt sta``, and the record never states a delay, slack,
timing verdict or "met". ``-flatten`` is required because ``ltp`` only
considers paths within a single module; ``-noff`` excludes flip-flop cells so
the figure is the combinational depth between registers. ``read_verilog``
without ``-sv`` is deliberate: the derived view exists precisely so the
non-SV frontend klt runs can read it -- the same frontend that produced the
committed record.

Each engine run is bounded by a declared wall-clock timeout (default 600 s,
recorded); a timeout is recorded as a result, never retried with a larger
bound. Engines run sequentially, one process at a time.

Modes:

- ``--check`` (default): no tool run. Re-verifies the committed record
  against the working tree (RTL and derived-view hashes regenerated in a
  temp dir, committed per-engine scripts byte-compared) and re-checks the
  plan's recorded ``pinned-ltp-figures`` block; exits nonzero on drift.
- ``--run``: build the view, run the engines, write the record
  (``asic/evidence/oneshot-synth-ltp-v1.json``) and the committed per-engine
  scripts.

A negative outcome is a complete outcome: an engine that times out here too,
or an ``ltp`` that yields nothing, is recorded as such. A refutation of
"B4-B7 cost what B1 suggests" is a valid C4 result.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import synth_view  # noqa: E402
import synthesize_oneshot as so  # noqa: E402

RECORD_PATH = ROOT / "asic" / "evidence" / "oneshot-synth-ltp-v1.json"
PLAN_PATH = ROOT / "asic" / "whole-voice-feasibility-plan.md"
SCRIPTS_DIR = ROOT / "asic" / "evidence" / "scripts"
#: View build area (gitignored, like the v1 flow's ``asic/build``).
BUILD_DIR = ROOT / "asic" / "build" / "ltp"
RESULTS_DIR = BUILD_DIR / "results"
#: The ``$readmemh`` directory text written into the derived view. It is part
#: of the view's bytes, so the record carries it and every re-verification
#: rebuilds the view with the same text before comparing hashes.
MEMH_DIR_TEXT = "asic/build/ltp/src"

SCHEMA = "gf180-torchsynth/ltp-depth-evidence-v1"
LABEL = "unmapped path-depth evidence"
DEFAULT_TIMEOUT_S = 600
YOSYS = shutil.which("yosys") or "yosys"

#: The engines whose abc run timed out in the committed feasibility record
#: (B4-B7 and B9 of the plan's bottleneck table). C4's question is whether an
#: unmapped static depth exists for them; everything else is out of scope.
ENGINES = ("sine_vco_engine", "adsr_engine", "lfo_vca_engine",
           "square_saw_vco_engine", "mod_matrix_engine")

ARITH_TYPES = ("$mul", "$div", "$mod", "$divfloor", "$modfloor")

#: Units discipline: a record of unmapped cell levels must never grow a
#: field that speaks timing. These key-name fragments are refused by
#: ``--check`` and by the unit test (word-delimited, so "note" passes).
FORBIDDEN_KEY_RE = re.compile(
    r"(^|_)(ns|ps|slack|timing|delay|met|fmax|period|latency)(_|$)")

STAT_NAMED_LINES = ("wires", "wire bits", "public wires", "public wire bits",
                    "ports", "port bits", "cells", "memories", "memory bits")

BLOCK_RE = re.compile(r"```pinned-ltp-figures\n(.*?)\n```", re.S)


# =============================================================================
# Yosys script construction and output parsing
# =============================================================================

def engine_sources(engine: str) -> list:
    """Read order for one engine: the package first, then the target's set."""
    return [synth_view.PKG] + list(so.TARGETS[engine])


def _repo_rel(path: Path) -> str:
    """Repo-root-relative posix path text; absolute only off-tree (tests)."""
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def engine_script(engine: str, src_dir: Path, results_dir: Path) -> str:
    """The exact yosys script text run for one engine (committed as-is).

    Paths are repo-root-relative: yosys is launched with ``cwd=ROOT`` (see
    ``run_engine``), and the committed script must not leak the host's
    checkout location.
    """
    reads = "\n".join("read_verilog %s" % _repo_rel(src_dir / name)
                      for name in engine_sources(engine))
    arith_json = _repo_rel(results_dir / ("%s.arith.json" % engine))
    return (
        "# generated by tools/synth_feasibility_ltp.py for %s (check C4);\n"
        "# unmapped flow: no PDK, no liberty, no abc, no mapping.\n"
        "%s\n"
        "hierarchy -top %s\n"
        "synth -run :fine -flatten\n"
        "tee -o %s ltp -noff\n"
        "tee -o %s stat\n"
        "select t:$mul t:$div t:$mod t:$divfloor t:$modfloor\n"
        "write_json -selected %s\n"
        % (engine, reads, engine,
           _repo_rel(results_dir / ("%s.ltp.txt" % engine)),
           _repo_rel(results_dir / ("%s.stat.txt" % engine)),
           arith_json)
    )


def parse_ltp(text: str) -> dict | None:
    """Parse ``ltp -noff`` output into depth (cell levels) and endpoints."""
    match = re.search(r"Longest topological path in (\S+) \(length=(\d+)\)", text)
    if not match:
        return None
    steps = re.findall(r"^\s+\d+: (.*)$", text, re.M)
    if not steps:
        return None
    ff = re.search(r"^\s+ff: (.*)$", text, re.M)
    return {
        "module": match.group(1).lstrip("\\"),
        "depth_cells": int(match.group(2)),
        "path_start": steps[0].strip(),
        "path_end": ("ff " + ff.group(1).strip()) if ff else steps[-1].strip(),
    }


def parse_stat(text: str) -> dict:
    """Parse ``stat`` cell-type counts (named summary lines excluded)."""
    counts = {}
    in_module = False
    for line in text.splitlines():
        if line.startswith("==="):
            in_module = True
            continue
        if not in_module:
            continue
        match = re.match(r"^\s+(\d+)\s{2,}(\S+)\s*$", line)
        if match and match.group(2) not in STAT_NAMED_LINES:
            counts[match.group(2)] = int(match.group(1))
    return counts


def parse_arith(path: Path) -> dict:
    """Widths histogram of the arithmetic operator cells from scoped JSON."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    widths: dict[str, dict[str, int]] = {}
    for module in data.get("modules", {}).values():
        for cell in module.get("cells", {}).values():
            ctype = cell.get("type", "")
            if ctype not in ARITH_TYPES:
                continue
            params = cell.get("parameters", {})
            key = "%dx%d->%d" % (int(str(params["A_WIDTH"]), 2),
                                 int(str(params["B_WIDTH"]), 2),
                                 int(str(params["Y_WIDTH"]), 2))
            widths.setdefault(ctype, {})
            widths[ctype][key] = widths[ctype].get(key, 0) + 1
    return {k: dict(sorted(v.items())) for k, v in sorted(widths.items())}


# =============================================================================
# The run: one yosys process per engine, sequential, bounded
# =============================================================================

def run_engine(engine: str, script_text: str, script_path: Path,
               timeout_s: float) -> dict:
    """Run one engine's yosys script; a timeout is a result, not a retry."""
    started = time.monotonic()
    try:
        done = subprocess.run(
            [YOSYS, "-q", "-l", (RESULTS_DIR / ("%s.yosys.log" % engine)).as_posix(),
             "-s", script_path.resolve().as_posix()],
            cwd=ROOT, capture_output=True, text=True, timeout=timeout_s)
        status, exit_code = ("ok" if done.returncode == 0 else "error"), done.returncode
    except subprocess.TimeoutExpired:
        status, exit_code = "timeout", None
    wall = round(time.monotonic() - started, 1)

    result = {
        "sources": engine_sources(engine),
        "yosys_version": yosys_version(),
        "yosys_command": "yosys -q -l asic/build/ltp/results/%s.yosys.log "
                         "-s asic/evidence/scripts/ltp_%s.ys  (paths relative "
                         "to the repo root; the as-run script in the "
                         "gitignored build area is byte-identical to the "
                         "committed one)" % (engine, engine),
        "yosys_script": script_text,
        "yosys_script_sha256": synth_view.sha256_file(script_path),
        "timeout_bound_s": timeout_s,
        "wall_time_s": wall,
        "status": status,
        "exit_code": exit_code,
        "input_sha256": input_hashes(engine, script_path.parent / "src"),
        "ltp": None,
        "stat_cell_counts": None,
        "arith_operator_widths": None,
        "note": "ltp depth is a cell-level count of the unmapped netlist "
                "('synth -run :fine -flatten'), not nanoseconds; no timing, "
                "slack or met claim exists anywhere in this record",
    }
    if status == "ok":
        ltp_path = RESULTS_DIR / ("%s.ltp.txt" % engine)
        stat_path = RESULTS_DIR / ("%s.stat.txt" % engine)
        if ltp_path.exists():
            result["ltp"] = parse_ltp(
                ltp_path.read_text(encoding="utf-8", errors="replace"))
        if stat_path.exists():
            result["stat_cell_counts"] = parse_stat(
                stat_path.read_text(encoding="utf-8", errors="replace"))
        result["arith_operator_widths"] = parse_arith(
            RESULTS_DIR / ("%s.arith.json" % engine))
    return result


def yosys_version() -> str:
    try:
        out = subprocess.run([YOSYS, "-V"], capture_output=True, text=True,
                             timeout=60)
        return (out.stdout or out.stderr).strip().splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError):
        return "unavailable"


def input_hashes(engine: str, src_dir: Path) -> dict:
    """Hashes of every file the engine's script actually reads.

    The ``.memh`` payloads join when a source's ``initial $readmemh`` (a
    synthesis-view rewrite) references them; yosys reads them at memory
    inference, so they are inputs, not decorations.
    """
    hashes = {}
    for name in engine_sources(engine):
        hashes[name] = synth_view.sha256_file(src_dir / name)
        if "$readmemh" in (src_dir / name).read_text(encoding="utf-8"):
            for memh in (synth_view.AUDIO_MEMH, synth_view.CONTROL_MEMH):
                path = src_dir / memh
                if path.exists() and memh not in hashes:
                    hashes[memh] = synth_view.sha256_file(path)
    return hashes


def git_head() -> dict:
    done = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True)
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "tb/sv", "spec"], cwd=ROOT,
        capture_output=True, text=True).stdout.strip()
    return {"git_head": done.stdout.strip(),
            "rtl_and_spec_inputs_match_git_head": dirty == ""}


def build_record(timeout_s: float, out_path: Path) -> dict:
    if RESULTS_DIR.exists():
        shutil.rmtree(RESULTS_DIR)
    RESULTS_DIR.mkdir(parents=True)
    ledger = synth_view.build(BUILD_DIR / "src", memh_dir_text=MEMH_DIR_TEXT)

    engines = {}
    for engine in ENGINES:
        print("== %s (yosys timeout %gs)" % (engine, timeout_s), flush=True)
        script_path = BUILD_DIR / ("ltp_%s.ys" % engine)
        script_text = engine_script(engine, BUILD_DIR / "src", RESULTS_DIR)
        script_path.write_text(script_text, encoding="utf-8")
        engines[engine] = run_engine(engine, script_text, script_path, timeout_s)
        status = engines[engine]["status"]
        depth = engines[engine]["ltp"]
        print("   status=%s wall=%gs depth=%s" % (
            status, engines[engine]["wall_time_s"],
            depth["depth_cells"] if depth else "none"), flush=True)

    for engine in ENGINES:
        committed = SCRIPTS_DIR / ("ltp_%s.ys" % engine)
        committed.parent.mkdir(parents=True, exist_ok=True)
        committed.write_text(engines[engine]["yosys_script"], encoding="utf-8")
        engines[engine]["yosys_script_sha256"] = synth_view.sha256_file(committed)

    same_yosys = None
    v1 = ROOT / "asic" / "evidence" / "oneshot-synth-feasibility-v1.json"
    if v1.exists():
        pinned = json.loads(v1.read_text(encoding="utf-8"))["tools"].get("yosys")
        same_yosys = pinned == engines[ENGINES[0]]["yosys_version"]

    head = git_head()
    record = {
        "schema": SCHEMA,
        "label": LABEL,
        "scope": "unmapped per-engine static path depth of the issue-#79 "
                 "whole-voice engines on the derived synthesis view: one "
                 "local yosys process per engine, 'synth -run :fine "
                 "-flatten', ltp -noff, stat. No PDK, no liberty, no abc "
                 "mapping, no klt synthesize/sta. Depth is a cell-level "
                 "count of the unmapped netlist, not nanoseconds; this "
                 "record states no timing, slack, area or met claim.",
        "claims": {"layout": False, "signoff": False, "silicon": False,
                   "hardware_playback": False, "sound_fidelity": False,
                   "unmapped_path_depth": True},
        "units": "cell levels (ltp length on the unmapped netlist); never "
                 "a time",
        "timeout_s_per_engine": timeout_s,
        "sequential_runs": "engines ran one after another, one yosys "
                           "process at a time",
        "identity": {
            "git_head": head["git_head"],
            "rtl_and_spec_inputs_match_git_head":
                head["rtl_and_spec_inputs_match_git_head"],
            "rtl_sha256": ledger["original_sha256"],
            "synthesis_view_sha256": ledger["synthesis_view_sha256"],
            "memh_sha256": ledger["memh_sha256"],
            "memh_dir_text": MEMH_DIR_TEXT,
        },
        "comparability_note": (
            "yosys version equals the committed feasibility record's (%s); "
            "the flows differ (:fine unmapped here vs klt's abc mapping "
            "there), so these cell-level depths are not comparable to that "
            "record's abc critical-path picoseconds"
            % ("same" if same_yosys else "different")),
        "engines": engines,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    return record


# =============================================================================
# The check: record vs tree, plan pins, units discipline
# =============================================================================

def check_record(record: dict, view_ledger: dict | None = None,
                 root: Path = ROOT) -> list:
    """Return problems (empty == the record is citeable as depth evidence).

    ``view_ledger`` is the freshly rebuilt ``synth_view.build`` ledger; when
    given, every engine's recorded input hashes must agree with it (this is
    what ties the record to the working tree -- the build area itself is
    gitignored and must never be trusted). The record must also leak no
    absolute host path (same predicate as the feasibility record's checker
    in ``tests/test_synth_feasibility_evidence.py``).
    """
    problems = []
    if record.get("schema") != SCHEMA:
        problems.append("schema")
    if record.get("label") != LABEL:
        problems.append("label")
    claims = record.get("claims", {})
    for key in ("layout", "signoff", "silicon", "hardware_playback",
                "sound_fidelity"):
        if claims.get(key) is not False:
            problems.append("claims.%s must be false" % key)
    if claims.get("unmapped_path_depth") is not True:
        problems.append("claims.unmapped_path_depth must be true")
    if "cell level" not in record.get("units", ""):
        problems.append("units must say cell levels")

    def walk(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                if FORBIDDEN_KEY_RE.search(str(key)):
                    problems.append("forbidden timing-like key: %s%s"
                                    % (path, key))
                walk(value, path + str(key) + ".")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, path + str(i) + ".")

    walk(record)
    if re.search(r"/home/|/Users/|\.ciel/|\.volare/", json.dumps(record)):
        problems.append("absolute host path leaked")
    if set(record.get("engines", {})) != set(ENGINES):
        problems.append("engines do not match the declared set")

    timeout_bound = record.get("timeout_s_per_engine")
    for name, engine in sorted(record.get("engines", {}).items()):
        if engine.get("status") not in ("ok", "timeout", "error"):
            problems.append("%s: bad status" % name)
        if engine.get("timeout_bound_s") != timeout_bound:
            problems.append("%s: timeout bound not recorded" % name)
        if not isinstance(engine.get("wall_time_s"), (int, float)):
            problems.append("%s: wall time not recorded" % name)
        if not engine.get("yosys_script"):
            problems.append("%s: command script not recorded" % name)
        script = root / "asic" / "evidence" / "scripts" / ("ltp_%s.ys" % name)
        if not script.exists() or synth_view.sha256_file(script) \
                != engine.get("yosys_script_sha256"):
            problems.append("%s: committed script missing or drifted" % name)
        if view_ledger is not None:
            view_sha = view_ledger["synthesis_view_sha256"]
            memh_sha = view_ledger["memh_sha256"]
            inputs = engine.get("input_sha256", {})
            for src, digest in inputs.items():
                expected = memh_sha.get(src) if src.endswith(".memh") \
                    else view_sha.get(src)
                if expected != digest:
                    problems.append("%s: input hash stale: %s" % (name, src))
            if not set(engine.get("sources", [])) <= set(inputs):
                problems.append("%s: input set != recorded sources" % name)
        if engine.get("status") == "ok":
            depth = (engine.get("ltp") or {}).get("depth_cells")
            if not isinstance(depth, int) or depth < 0:
                problems.append("%s: ok without an integer depth" % name)
            if engine.get("stat_cell_counts") is None:
                problems.append("%s: ok without stat counts" % name)
        else:
            if engine.get("ltp") is not None:
                problems.append("%s: depth present without an ok run" % name)
    return problems


def check_hashes(record: dict) -> tuple:
    """Regenerate the view in a temp dir; the record's hashes must match.

    The rebuild must use the same ``$readmemh`` directory text the run used:
    it is part of the view's bytes, so comparing hashes built with a
    different text would report drift that never happened.
    """
    problems = []
    memh_dir_text = record.get("identity", {}).get("memh_dir_text",
                                                   "asic/build/src")
    with tempfile.TemporaryDirectory() as tmp:
        ledger = synth_view.build(Path(tmp) / "src", memh_dir_text=memh_dir_text)
    identity = record.get("identity", {})
    if ledger["synthesis_view_sha256"] != identity.get("synthesis_view_sha256"):
        problems.append("synthesis-view hashes drifted from the record")
    if ledger["memh_sha256"] != identity.get("memh_sha256"):
        problems.append("ROM payload hashes drifted from the record")
    if ledger["original_sha256"] != identity.get("rtl_sha256"):
        problems.append("RTL hashes drifted from the record")
    return problems, ledger


def block_pins(plan_text: str) -> list:
    match = BLOCK_RE.search(plan_text)
    if not match:
        return []
    pins = []
    for line in match.group(1).splitlines():
        if line.strip() and not line.strip().startswith("#"):
            path, _, expected = line.partition(" = ")
            if _:
                pins.append((path.strip(), expected.strip()))
    return pins


def check_plan(record: dict, plan_text: str) -> list:
    """Problems where the plan's pinned-ltp-figures block disagrees."""
    problems = []
    pins = block_pins(plan_text)
    if not pins:
        return ["plan has no ```pinned-ltp-figures block"]
    for path, expected in pins:
        node = record
        try:
            for part in path.split("."):
                node = node[int(part)] if isinstance(node, list) else node[part]
        except (KeyError, IndexError, ValueError, TypeError):
            problems.append("%s: not in the record" % path)
            continue
        if str(node) != expected:
            problems.append("%s: plan %s != record %r" % (path, expected, node))
    return problems


def report_lines(record: dict) -> list:
    lines = ["%-24s %-8s %10s %10s  %s"
             % ("engine", "status", "wall_s", "depth", "bound_s")]
    for engine in ENGINES:
        item = record["engines"].get(engine, {})
        depth = (item.get("ltp") or {}).get("depth_cells")
        lines.append("%-24s %-8s %10s %10s  %s"
                     % (engine, item.get("status"),
                        item.get("wall_time_s"),
                        depth if depth is not None else "none",
                        item.get("timeout_bound_s")))
    lines += ["", "depth is a cell-level count of the unmapped netlist "
                  "('synth -run :fine -flatten', ltp -noff); never a time"]
    return lines


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", dest="mode", action="store_const", const="check",
                      help="verify the committed record against the tree and "
                           "the plan (default)")
    mode.add_argument("--run", dest="mode", action="store_const", const="run",
                      help="run the engines and (re)write the record")
    parser.set_defaults(mode="check")
    parser.add_argument("--timeout-s", type=float, default=DEFAULT_TIMEOUT_S,
                        help="per-engine wall-clock bound (default %gs)"
                             % DEFAULT_TIMEOUT_S)
    parser.add_argument("--out", type=Path, default=RECORD_PATH,
                        help="record output path (--run)")
    args = parser.parse_args(argv)

    if args.mode == "run":
        record = build_record(args.timeout_s, args.out)
        print("\n".join(report_lines(record)))
        run_problems, ledger = check_hashes(record)
        problems = check_record(record, ledger) + run_problems
        if problems:
            print("\n".join("CHECK: %s" % p for p in problems))
            return 1
        print("record written: %s" % args.out.relative_to(ROOT))
        return 0

    if not RECORD_PATH.exists():
        print("no record at %s; run with --run first" % RECORD_PATH.relative_to(ROOT))
        return 2
    record = json.loads(RECORD_PATH.read_text(encoding="utf-8"))
    print("\n".join(report_lines(record)))
    problems, ledger = check_hashes(record)
    problems += check_record(record, ledger)
    if PLAN_PATH.exists():
        problems += check_plan(record, PLAN_PATH.read_text(encoding="utf-8"))
    if problems:
        print("\n".join("DRIFT: %s" % p for p in problems))
        return 1
    print("check: record matches the tree and the plan's pinned block")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
