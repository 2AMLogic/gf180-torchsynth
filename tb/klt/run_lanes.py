#!/usr/bin/env python3
"""Run the ``klt functional-verification`` RTL lanes and write the evidence record.

One request per lane lives next to this file (``<lane>.request.json``) with a
cocotb module (``test_<lane>.py``); this driver runs each through
``klt functional-verification``, refuses anything that is not a clean pass
(zero tests, skipped tests, a missing ``results.xml`` and a build failure all
read as failure, never as a pass), and assembles
``sim/evidence/functional-verification-modules-v1.json`` with provenance.

  run_lanes.py run --out DIR [--lanes a,b] [--write REPORT] [--compare REPORT]
  run_lanes.py negative-controls --out DIR [--lanes a,b] [--merge REPORT]
  run_lanes.py check REPORT         # no simulation: hash freshness of a record

Exit status: 0 every requested lane passed; 1 a lane failed or an expected
failure did not happen; 3 a required tool (klt, cocotb, iverilog) is missing,
so nothing ran. An unrun check is never a pass.

Scope: sample-exact functional agreement of the listed RTL modules with the
frozen fixed-point model's golden vectors, simulated by Icarus Verilog. It
claims no synthesis, SDF, post-route, hardware or sound-fidelity result.
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
import tempfile
from pathlib import Path

KLT_DIR = Path(__file__).resolve().parent
TB_ROOT = KLT_DIR.parent
ROOT = TB_ROOT.parent
DEFAULT_REPORT = ROOT / "sim/evidence/functional-verification-modules-v1.json"
SCHEMA = "gf180-torchsynth/functional-verification-modules-v1"

#: Files whose uncommitted state invalidates the record (same scope as
#: ``run_tb.EVIDENCE_TREE_SCOPE``; ``sim/reference`` holds the frozen vectors).
TREE_SCOPE = ("tb", "src", "spec/reference", "sim/reference")

#: lane -> repo-relative vector inputs (files, or directories hashed recursively).
_RECEIPT = ["sim/reference/fixed-voice-golden-v1.json",
            "sim/reference/fixed-voice-golden-v1-traces"]
VECTOR_INPUTS = {
    "anchor": ["sim/reference/golden-vector-fixed-anchor.json"],
    "adsr": ["sim/reference/adsr-golden-v1"],
    "lfo": ["sim/reference/lfo-vca-golden-v1"],
    "modmatrix": ["sim/reference/mod-matrix-golden-v1",
                  "sim/reference/mod-matrix-golden-v1-traces"],
    "vco": ["sim/reference/sine-vco-golden-v1", *_RECEIPT],
    "vco2": ["sim/reference/square-saw-vco-golden-v1", *_RECEIPT],
    "noise": _RECEIPT,
    "mix": _RECEIPT,
    "normreplay": _RECEIPT,
    # The patch lanes compare against a cycle-exact Python mirror whose only
    # data input is the 78-name parameter inventory.
    "patch": ["spec/reference/parameter-inventory-v1.json"],
    "patch_small": ["spec/reference/parameter-inventory-v1.json"],
}

#: lane -> (repo-relative RTL source, exact old text, replacement). Each is a
#: single-site functional fault the lane's vectors must catch.
NEGATIVE_CONTROLS = {
    "anchor": (
        "tb/sv/lut_sine_dut.sv",
        "wire [C2_WIDTH-1:0]      phase_next = phase + phase_step;",
        "wire [C2_WIDTH-1:0]      phase_next = phase + phase_step + 1;",
    ),
    "adsr": (
        "tb/sv/adsr_engine.sv",
        "wire signed [C4_WIDTH-1:0] sustain_eff = sustain_entry;",
        "wire signed [C4_WIDTH-1:0] sustain_eff = -sustain_entry;",
    ),
    "lfo": (
        "tb/sv/lfo_vca_engine.sv",
        "wire signed [24:0] entry_word = negate ? -lut_word : lut_word;",
        "wire signed [24:0] entry_word = lut_word;",
    ),
    "modmatrix": (
        "tb/sv/mod_matrix_engine.sv",
        "C1_SAT_HI = 96'sd8388607;",
        "C1_SAT_HI = 96'sd2097151;",
    ),
    "vco": (
        "tb/sv/sine_vco_engine.sv",
        "wire [C5_INDEX_BITS-1:0] a_index = i[C5_INDEX_BITS-1:0];",
        "wire [C5_INDEX_BITS-1:0] a_index = i[C5_INDEX_BITS-1:0] + 1'b1;",
    ),
    "vco2": (
        "tb/sv/square_saw_vco_engine.sv",
        "wire signed [95:0] right_prod = $signed(s_shape) * $signed(cos2);",
        "wire signed [95:0] right_prod = (96'sd2097152 - $signed(s_shape)) * $signed(cos2);",
    ),
    "noise": (
        "tb/sv/noise_stream_dut.sv",
        "sample_word      <= narrow_value[C1_WIDTH-1:0];",
        "sample_word      <= narrow_value[C1_WIDTH-1:0] + 1'b1;",
    ),
    "mix": (
        "tb/sv/audio_mix_engine.sv",
        "wire signed [95:0] mix_out = mix_eff;",
        "wire signed [95:0] mix_out = mix_eff + 96'sd1;",
    ),
    "patch": (
        "tb/sv/patch_control.sv",
        "b = PATCH_DOMAIN_TAG[(DOMAIN_TAG_LEN*8-1) - c_fed*8 -: 8];",
        "b = 8'h00;",
    ),
    "patch_small": (
        "tb/sv/patch_control.sv",
        "(cap_c != rnd_digest)",
        "(1'b0)",
    ),
    "normreplay": (
        "tb/sv/normalization_replay_engine.sv",
        "if (peak_word > UNITY_INT) begin",
        "if (1'b1) begin",
    ),
}

#: Surfaces this record deliberately does not cover.
DECLARED_GAPS = [
    {
        "item": "whole-voice one-shot top (tb_one_shot_voice)",
        "status": "not covered",
        "owner": "tb/run_voice.py; whole-voice evidence under sim/evidence/",
    },
    {
        "item": "one-shot tail chain (tb_one_shot_tail)",
        "status": "not covered",
        "owner": "tb/run_oneshot.py; sim/evidence/oneshot-tail-chain-*.json",
    },
    {
        "item": "render binding (tb_render_binding)",
        "status": "not covered",
        "owner": "tb/run_tb.py patch / normreplay bind_reject paths",
    },
    {
        "item": "run_tb.py mutation, replay and budget stages",
        "status": "not covered by the cocotb lanes; still gated by tb/run_tb.py",
        "owner": "tb/run_tb.py <lane>",
    },
    {
        "item": "synthesis, SDF back-annotation, post-route simulation",
        "status": "not performed; no such claim is made",
        "owner": "#82, #83",
    },
]


class LaneRefused(Exception):
    """A lane did not produce a trustworthy clean pass."""


def lanes_available() -> list:
    return sorted(
        p.name[: -len(".request.json")] for p in KLT_DIR.glob("*.request.json")
    )


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def request_sources(lane: str) -> list:
    request = json.loads((KLT_DIR / ("%s.request.json" % lane)).read_text("utf-8"))
    return [(KLT_DIR / s).resolve() for s in request["sources"]]


def vector_files(lane: str) -> list:
    files = []
    for entry in VECTOR_INPUTS.get(lane, []):
        path = ROOT / entry
        files.extend(
            sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]
        )
    return files


def lane_identity(lane: str) -> dict:
    """Hashes of everything that determines the lane's verdict."""
    sources = request_sources(lane)
    return {
        "request_sha256": sha256_file(KLT_DIR / ("%s.request.json" % lane)),
        "test_module_sha256": sha256_file(KLT_DIR / ("test_%s.py" % lane)),
        "harness_sha256": {
            rel(p): sha256_file(p)
            for p in (*sorted(KLT_DIR.glob("*_lane.py")), TB_ROOT / "run_tb.py")
        },
        "rtl_sha256": {rel(p): sha256_file(p) for p in sources},
        "vector_sha256": {rel(p): sha256_file(p) for p in vector_files(lane)},
    }


def verdict(report: dict, results_xml: Path | None) -> None:
    """Raise :class:`LaneRefused` unless ``report`` is a clean, non-vacuous pass."""
    if report.get("error"):
        raise LaneRefused("klt error: %s" % report["error"].get("message"))
    tests = report.get("tests") or []
    count = report.get("test_count")
    if report.get("status") != "pass":
        names = [t["name"] for t in tests if t.get("status") != "passed"]
        raise LaneRefused("status %r (non-passing tests: %s)" % (report.get("status"), names))
    if not count or count != len(tests):
        raise LaneRefused("test_count %r does not match %d reported tests" % (count, len(tests)))
    if report.get("passed_count") != count or report.get("failed_count") or report.get(
        "skipped_count"
    ):
        raise LaneRefused("not every test passed: %s" % {
            k: report.get(k) for k in ("passed_count", "failed_count", "skipped_count")
        })
    if results_xml is None or not results_xml.is_file() or results_xml.stat().st_size == 0:
        raise LaneRefused("results.xml is missing or empty")


def tool_versions() -> dict:
    out = {}
    iverilog = shutil.which("iverilog")
    out["iverilog"] = (
        subprocess.run([iverilog, "-V"], capture_output=True, text=True).stdout.splitlines()[0]
        if iverilog
        else None
    )
    klt = shutil.which("klt")
    out["klt"] = (
        subprocess.run([klt, "--version"], capture_output=True, text=True).stdout.strip()
        if klt
        else None
    )
    out["python"] = sys.version.split()[0]
    return out


def git_identity() -> dict:
    def git(*args):
        return subprocess.run(
            ["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True
        ).stdout.strip()

    head = git("rev-parse", "HEAD")
    dirty = bool(git("status", "--porcelain", "--", *TREE_SCOPE))
    return {"git_head": head, "git_tree_dirty": dirty}


def run_klt(request: Path, outdir: Path) -> dict:
    command = [
        "klt", "functional-verification", str(request),
        "-o", str(outdir), "--format", "json",
    ]
    print("+ " + " ".join(command), flush=True)
    done = subprocess.run(command, capture_output=True, text=True)
    try:
        return json.loads(done.stdout)
    except json.JSONDecodeError:
        raise LaneRefused(
            "klt produced no JSON (exit %d): %s" % (done.returncode, done.stderr[-2000:])
        )


def require_tools() -> None:
    missing = [t for t in ("klt", "iverilog") if shutil.which(t) is None]
    if missing:
        print("ERROR: %s not on PATH; nothing ran. An unrun check is never a pass." % missing)
        raise SystemExit(3)


def run_lane(lane: str, out: Path) -> dict:
    outdir = out / lane
    shutil.rmtree(outdir, ignore_errors=True)
    outdir.mkdir(parents=True)
    report = run_klt(KLT_DIR / ("%s.request.json" % lane), outdir)
    env = report.get("environment") or {}
    results_xml = Path(env["results_xml"]) if env.get("results_xml") else None
    verdict(report, results_xml)
    record = {
        "lane": lane,
        "status": "pass",
        "test_count": report["test_count"],
        "tests": [
            {"name": t["name"], "status": t["status"], "sim_time_ns": t.get("sim_time_ns")}
            for t in report["tests"]
        ],
        "engine": env.get("engine"),
        "engine_version": env.get("engine_version"),
        "cocotb_version": env.get("cocotb_version"),
        "results_xml_sha256": sha256_file(results_xml),
    }
    record.update(lane_identity(lane))
    return record


def assemble(lane_records: list, negative_controls: list) -> dict:
    tools = tool_versions()
    cocotb = {r["cocotb_version"] for r in lane_records}
    tools["cocotb"] = sorted(cocotb)[0] if len(cocotb) == 1 else sorted(cocotb)
    return {
        "schema": SCHEMA,
        "scope": (
            "Sample-exact functional agreement of the listed RTL modules with the "
            "frozen fixed-point model's golden vectors under Icarus Verilog, driven "
            "by cocotb through `klt functional-verification`. No synthesis, SDF, "
            "post-route, hardware or sound-fidelity claim is made."
        ),
        "result": "pass" if lane_records else "empty",
        "identity": {**git_identity(), "tools": tools},
        "lanes": lane_records,
        "negative_controls": negative_controls,
        "declared_gaps": DECLARED_GAPS,
    }


def stable_view(record: dict) -> dict:
    """The deterministic part of a record (what ``--compare`` and ``check`` pin)."""
    return {
        lane["lane"]: {
            k: lane[k]
            for k in ("status", "test_count", "request_sha256", "test_module_sha256",
                      "harness_sha256", "rtl_sha256", "vector_sha256")
        } | {"tests": [(t["name"], t["status"]) for t in lane["tests"]]}
        for lane in record["lanes"]
    }


def check_record(path: Path) -> list:
    """Static freshness: committed hashes must equal the current tree. No sim."""
    record = json.loads(path.read_text("utf-8"))
    problems = []
    if record.get("schema") != SCHEMA:
        problems.append("schema is %r" % record.get("schema"))
    lanes = {lane["lane"]: lane for lane in record.get("lanes", [])}
    if sorted(lanes) != lanes_available():
        problems.append("record lanes %s != requests on disk %s" % (sorted(lanes), lanes_available()))
    for name, lane in lanes.items():
        if lane.get("status") != "pass" or lane.get("test_count", 0) < 1:
            problems.append("%s: not a recorded non-vacuous pass" % name)
        if name in lanes_available():
            now = lane_identity(name)
            for key, value in now.items():
                if lane.get(key) != value:
                    problems.append("%s: %s is stale against the tree" % (name, key))
    if record.get("identity", {}).get("git_tree_dirty"):
        problems.append("record was produced from a dirty tree")
    return problems


def cmd_run(args) -> int:
    require_tools()
    lanes = args.lanes.split(",") if args.lanes else lanes_available()
    out = Path(args.out).resolve()
    records, failed = [], []
    for lane in lanes:
        try:
            records.append(run_lane(lane, out))
            print("LANE %s: PASS" % lane, flush=True)
        except LaneRefused as error:
            print("LANE %s: FAILED -- %s" % (lane, error), flush=True)
            failed.append(lane)
    record = assemble(records, [])
    if failed:
        record["result"] = "fail"
        record["failed_lanes"] = failed
    if args.write and not failed:
        Path(args.write).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    if args.compare and not failed:
        want = stable_view(json.loads(Path(args.compare).read_text("utf-8")))
        got = stable_view(record)
        if want != got:
            for lane in sorted(set(want) | set(got)):
                if want.get(lane) != got.get(lane):
                    print("COMPARE: lane %s differs from %s" % (lane, args.compare))
            return 1
        print("COMPARE: fresh run matches %s" % args.compare)
    return 1 if failed else 0


def cmd_negative(args) -> int:
    require_tools()
    lanes = args.lanes.split(",") if args.lanes else sorted(NEGATIVE_CONTROLS)
    out = Path(args.out).resolve()
    results, bad = [], []
    for lane in lanes:
        source, old, new = NEGATIVE_CONTROLS[lane]
        text = (ROOT / source).read_text("utf-8")
        if text.count(old) != 1:
            print("NEGATIVE %s: mutation site not found exactly once in %s" % (lane, source))
            bad.append(lane)
            continue
        work = out / ("negative-" + lane)
        shutil.rmtree(work, ignore_errors=True)
        (work / "rtl").mkdir(parents=True)
        mutated = work / "rtl" / Path(source).name
        mutated.write_text(text.replace(old, new), encoding="utf-8")
        request = json.loads((KLT_DIR / ("%s.request.json" % lane)).read_text("utf-8"))
        request["sources"] = [
            str(mutated) if (KLT_DIR / s).resolve() == (ROOT / source).resolve()
            else str((KLT_DIR / s).resolve())
            for s in request["sources"]
        ]
        request["testbench"]["search_path"] = str(KLT_DIR)
        if request.get("options", {}).get("includes"):
            request["options"]["includes"] = [
                str((KLT_DIR / i).resolve()) for i in request["options"]["includes"]
            ]
        request_path = work / "mutant.request.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        try:
            report = run_klt(request_path, work / "out")
        except LaneRefused as error:
            report = {"status": "error", "error": str(error)}
        detected = report.get("status") == "fail" and report.get("failed_count", 0) >= 1
        print("NEGATIVE %s: %s" % (lane, "DETECTED (test failed)" if detected else "NOT DETECTED"))
        results.append({
            "lane": lane, "mutated_source": source, "mutation": {"old": old, "new": new},
            "expected": "fail", "observed": report.get("status"), "detected": detected,
        })
        if not detected:
            bad.append(lane)
    if args.merge and not bad:
        path = Path(args.merge)
        record = json.loads(path.read_text("utf-8"))
        record["negative_controls"] = results
        path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return 1 if bad else 0


def cmd_check(args) -> int:
    problems = check_record(Path(args.report))
    for problem in problems:
        print("STALE/INVALID: " + problem)
    if not problems:
        print("record is fresh against the tree")
    return 1 if problems else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--out", required=True)
    run.add_argument("--lanes")
    run.add_argument("--write")
    run.add_argument("--compare")
    run.set_defaults(func=cmd_run)
    neg = sub.add_parser("negative-controls")
    neg.add_argument("--out", required=True)
    neg.add_argument("--lanes")
    neg.add_argument("--merge")
    neg.set_defaults(func=cmd_negative)
    chk = sub.add_parser("check")
    chk.add_argument("report", nargs="?", default=str(DEFAULT_REPORT))
    chk.set_defaults(func=cmd_check)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
