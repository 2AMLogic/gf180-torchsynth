#!/usr/bin/env python3
"""#374 (DR-0017): attempt the exact-sentinel simulation of the synthesized
``one_shot_voice_top`` netlist, or commit a refusal with the measured reason.

    run_postsynth_sentinel.py --root ROOT --venv VENV --out OUT --rep REP
        [--case normalization:below]
        [--compile-timeout S] [--sim-timeout S] [--verilator-timeout S]

What runs, in order (nothing is skipped silently; every step lands in the
record with its command, exit status, elapsed seconds and peak RSS):

1. **Netlist inventory** -- cell-type counts parsed from the ``synth_voice``
   stage's ``one_shot_voice_top.json`` (the post-synthesis ``synth_ecp5``
   netlist of the unmodified whole-voice top, staged sources, same flow).
2. **Primitive-model inventory** -- the pinned toolchain's own simulation
   library (``yowasp_yosys/share/ecp5``: ``cells_sim.v`` plus everything it
   includes) is parsed for behavioral (non-blackbox) module models, and every
   cell type the netlist uses is classified as modeled / blackbox-only /
   absent.
3. **Sentinel stimulus** -- the named case of the committed fixed-model
   golden vector (default ``normalization:below``, the param-committed
   regression case) is derived with ``tb/run_voice.py``'s own machinery and
   written exactly as the RTL lane writes it (same ``voice_write_case``).
   Every stimulus file is hashed into the record.
4. **Simulator attempts** -- Icarus Verilog (and Verilator) are asked to
   elaborate the netlist + the toolchain's ``cells_sim.v`` + the unmodified
   ``tb/sv/tb_one_shot_voice.sv`` bench, under a wall-clock cap. If Icarus
   elaborates, ``vvp`` runs under its own cap and every capture is compared
   sample-exactly with ``run_voice.voice_rows``/``voice_expected`` (integer
   equality, no tolerance -- the same check the RTL lane runs).

Verdicts (exit 0 is "a definitive outcome is committed", not "passed"):

- ``passed``   -- the netlist reproduced every expected checkpoint sample.
- ``refused``  -- the simulation could not run, and the record carries the
  measured reason (primitive-model gap, compile time/memory, or simulation
  time with an observed-rate extrapolation) plus the command and output.
  Per the issue: a committed refusal is a complete outcome; the design is
  never shrunk to make simulation fit.
- ``mismatch`` -- the netlist ran and disagreed with the model (exit 1).
- ``inconclusive`` -- the harness itself failed before either verdict could
  be established (exit 3); this is never reported as a pass or a refusal.
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

#: The wall-clock the sequencer schedules for one tb-driven clip:
#: 2 + 4*1764 + 1 + 2 * (11 + 4*176400), the same arithmetic make_report
#: records for fpga_oneshot_top's shim (the shim implements this schedule).
CLIP_CYCLES = 2 + 4 * 1764 + 1 + 2 * (11 + 4 * 176400)

SENTINEL_VECTOR = "sim/reference/fixed-voice-golden-v1.json"
ANCHOR_VECTOR = "sim/reference/golden-vector-fixed-anchor.json"


def sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


#: Yosys-internal JSON cell types that ``write_verilog`` renders as Verilog
#: *constructs* (``specify`` timing paths), not module instances, so they
#: need no simulation model however the netlist is compiled.
CONSTRUCT_CELLS = ("$specify2", "$specify3", "$specrule")


def run_timed(cmd, cwd: Path, timeout: int, log: Path):
    """Run cmd under GNU time; return {rc, elapsed_s, max_rss_kb, tail}."""
    if shutil.which("time") is None and not Path("/usr/bin/time").is_file():
        return {"rc": None, "elapsed_s": None, "max_rss_kb": None,
                "tail": "GNU time unavailable", "timed_out": False}
    argv = ["/usr/bin/time", "-v", "-o", str(log)] + [str(c) for c in cmd]
    start = time.monotonic()
    try:
        proc = subprocess.run(
            argv, cwd=str(cwd), capture_output=True, text=True,
            timeout=timeout)
        rc, timed_out, full = proc.returncode, False, proc.stdout + proc.stderr
    except subprocess.TimeoutExpired as expired:
        rc, timed_out = None, True
        full = (expired.stdout or b"") + (expired.stderr or b"")
        if isinstance(full, bytes):
            full = full.decode("utf-8", "replace")
        full = ("TIMED OUT after %ds (SIGKILL/timeout); partial output:\n%s"
                % (timeout, full))
    elapsed = time.monotonic() - start
    rss = None
    m = re.search(r"Maximum resident set size \(kbytes\): (\d+)",
                  log.read_text(errors="replace") if log.is_file() else "")
    if m:
        rss = int(m.group(1))
    # The compiler's own unknown-module set, extracted from the FULL output
    # (the tail alone truncates repeated errors away): Icarus and Verilator
    # word it differently.
    unknown = set(re.findall(r"Unknown module type: (\S+)", full))
    unknown |= set(re.findall(
        r"Cannot find file containing module: '([^']+)'", full))
    return {"rc": rc, "elapsed_s": round(elapsed, 1), "max_rss_kb": rss,
            "tail": full.strip().splitlines()[-25:], "timed_out": timed_out,
            "unknown_modules": sorted(unknown)}


def write_flat_sim_library(share: Path, dest: Path) -> dict:
    """Flatten the sim library's ``include`` chain, each header once.

    The pinned yowasp-yosys 0.70 ``cells_sim.v`` includes ``common_sim.vh``
    (which already pulls in ``cells_ff.vh``/``cells_io.vh`` at its end) and
    then includes those two again itself, so no compiler accepts the file
    as-is (yosys' own ``read_verilog +/ecp5/cells_sim.v`` fails with
    "Re-definition of module", and so does Icarus). This resolves the chain
    the way an include guard would: every header is inlined at its first
    appearance and skipped afterwards. **No module text is edited** -- only
    ``\\`include`` lines are substituted.
    """
    seen = []

    def load(name: str) -> str:
        if name in seen:
            return ""
        seen.append(name)
        out = []
        text_lines = (share / name).read_text(
            encoding="utf-8").splitlines(keepends=True)
        for line in text_lines:
            m = re.match(r'\s*`include\s+"([^"]+)"', line)
            out.append(load(m.group(1)) if m else line)
        return "".join(out)

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(load("cells_sim.v"), encoding="utf-8")
    return {"file": dest.name, "headers_inlined_in_order": list(seen),
            "sha256": sha(dest),
            "note": "include-chain flattening only (duplicate includes are "
                    "the pinned toolchain's own defect); module texts are "
                    "byte-identical to the headers"}


def netlist_cells(netlist_json: Path) -> dict:
    design = json.loads(netlist_json.read_text(encoding="utf-8"))
    cells = {}
    for module in design.get("modules", {}).values():
        for cell in module.get("cells", {}).values():
            ctype = cell.get("type", "?")
            cells[ctype] = cells.get(ctype, 0) + 1
    return dict(sorted(cells.items()))


def sim_model_inventory(share_ecp5: Path) -> dict:
    """Classify modules of the toolchain's sim library (cells_sim.v chain).

    Returns {module: "behavioral" | "blackbox"} for everything cells_sim.v
    and its includes define, plus the files scanned.
    """
    text = (share_ecp5 / "cells_sim.v").read_text(encoding="utf-8")
    scanned = ["cells_sim.v"]
    for name in re.findall(r'`include\s+"([^"]+)"', text):
        inc = share_ecp5 / name
        if inc.is_file():
            text += "\n" + inc.read_text(encoding="utf-8")
            scanned.append(name)
    modules = {}
    for m in re.finditer(r"module\s+(\S+?)\s*[;(]", text):
        prev_end = text.rfind("endmodule", 0, m.start())
        window = text[max(0, prev_end):m.start()]
        modules[m.group(1)] = ("blackbox" if "blackbox" in window
                               else "behavioral")
    # cells_bb.v (never included by cells_sim.v) declares blackbox-only
    # primitives; scanning it classifies "absent" cells as blackbox-only.
    bb = share_ecp5 / "cells_bb.v"
    if bb.is_file():
        for m in re.finditer(r"module\s+(\S+?)\s*[;(]",
                             bb.read_text(encoding="utf-8")):
            modules.setdefault(m.group(1), "blackbox-only (cells_bb.v)")
    return {"modules": dict(sorted(modules.items())), "files": scanned}


def write_stimulus(root: Path, case_id: str, workdir: Path) -> dict:
    """Derive + write the sentinel case exactly as the RTL lane does."""
    sys.path.insert(0, str(root / "src"))
    sys.path.insert(0, str(root / "tb"))
    import run_voice as rv  # noqa: E402  (heavy; imported after path setup)

    ctx = rv.voice_context("regression")
    case = rv.voice_derive_case_cached(ctx, case_id)
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "runs.txt").write_text("1\n", encoding="utf-8")
    rv.voice_write_case(workdir, 0, case)
    hashes = {p.name: sha(p) for p in sorted(workdir.glob("*.txt"))}
    return {"case": case_id, "profile": "regression",
            "derivation": "tb/run_voice.py voice_context/voice_derive_case/"
                          "voice_write_case (the RTL lane's own functions)",
            "stimulus_files_sha256": hashes,
            "expected": "every declared checkpoint of both passes and the "
                        "output clip, integer equality (voice_rows/voice_"
                        "expected), no tolerance"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="post-synthesis exact-sentinel attempt (#374)")
    for flag in ("root", "venv", "out", "rep"):
        parser.add_argument("--" + flag, type=Path, required=True)
    parser.add_argument("--case", default="normalization:below")
    parser.add_argument("--compile-timeout", type=int, default=2400)
    parser.add_argument("--sim-timeout", type=int, default=7200)
    parser.add_argument("--verilator-timeout", type=int, default=1800)
    args = parser.parse_args(argv)
    root, out, rep = args.root.resolve(), args.out.resolve(), args.rep.resolve()

    record = {
        "issue": 374, "decision_record": "DR-0017",
        "claim_scope": ("post-synthesis (synth_ecp5, yosys 0.70) netlist of "
                        "the unmodified one_shot_voice_top, staged sources, "
                        "this flow only; NO post-route netlist exists "
                        "(#80 did not fit), no gf180mcu / playback / Fmax / "
                        "sound-fidelity claim"),
        "simulators_considered": {},
    }

    netlist_json = out / "one_shot_voice_top.json"
    netlist_v = out / "one_shot_voice_top.v"
    missing = [p.name for p in (netlist_json, netlist_v) if not p.is_file()]
    if missing:
        print("run_postsynth_sentinel: netlist missing: %s (run the "
              "synth_voice stage first)" % ", ".join(missing),
              file=sys.stderr)
        return 3
    cells = netlist_cells(netlist_json)
    record["netlist"] = {
        "json_sha256": sha(netlist_json), "verilog_sha256": sha(netlist_v),
        "verilog_bytes": netlist_v.stat().st_size,
        "cell_counts": cells, "cell_total": sum(cells.values()),
        "source_stage": "build.sh synth_voice (read_slang + synth_ecp5 -run "
                        ":map_luts with 'abc -lut 4:7' + map_cells:, same "
                        "flow as the fpga_oneshot_top measurement netlist)",
    }

    share = next((args.venv.resolve() / "lib").glob(
        "python*/site-packages/yowasp_yosys/share/ecp5"), None)
    if share is None:
        print("run_postsynth_sentinel: yowasp_yosys share/ not found under "
              "%s (run the env stage first)" % args.venv, file=sys.stderr)
        return 3
    inv = sim_model_inventory(share)
    # $specify* cells are rendered as Verilog constructs, not instances, so
    # only primitive instance types can need a simulation model.
    instance_cells = {c: n for c, n in cells.items()
                      if not c.startswith(CONSTRUCT_CELLS)}
    construct_cells = {c: n for c, n in cells.items()
                       if c.startswith(CONSTRUCT_CELLS)}
    gaps = {c: inv["modules"].get(c, "absent from the simulation library")
            for c in instance_cells
            if inv["modules"].get(c, "absent") != "behavioral"}
    workdir = out / "vsim"
    flat = write_flat_sim_library(share, workdir / "ecp5_cells_sim_flat.v")
    record["primitive_models"] = {
        "library": "yowasp-yosys (pinned) share/ecp5",
        "files_scanned": inv["files"],
        "flattened_sim_library": flat,
        "netlist_cell_types": {
            c: (inv["modules"].get(c, "absent")
                if not c.startswith(CONSTRUCT_CELLS)
                else "yosys-internal, rendered as a Verilog specify "
                     "construct by write_verilog (needs no model)")
            for c in cells},
        "construct_cells": construct_cells,
        "gap": gaps,
    }

    workdir = out / "vsim"
    record["sentinel"] = write_stimulus(root, args.case, workdir)
    record["sentinel"]["vector_files_sha256"] = {
        name: sha(root / rel) for name, rel in
        (("fixed_voice_golden", SENTINEL_VECTOR),
         ("golden_vector_fixed_anchor", ANCHOR_VECTOR))}

    pkg = root / "tb/sv/gf180_rtl_constants_pkg.sv"
    tb = root / "tb/sv/tb_one_shot_voice.sv"
    simlib = workdir / "ecp5_cells_sim_flat.v"
    files = [pkg, tb, netlist_v, simlib]

    # --- Icarus Verilog --------------------------------------------------
    vvp = workdir / "voice_gl.vvp"
    iv_cmd = ["iverilog", "-g2012", "-o", str(vvp)] + \
        [str(f) for f in files]
    iv = run_timed(iv_cmd, cwd=workdir, timeout=args.compile_timeout,
                   log=out / "vsim_iverilog.time")
    iv_version = "not installed"
    if shutil.which("iverilog"):
        probe = subprocess.run(["iverilog", "-V"], capture_output=True,
                               text=True)
        iv_version = ((probe.stdout + probe.stderr).strip().splitlines()
                      or ["unknown"])[0]
    record["simulators_considered"]["iverilog"] = {
        "version": iv_version,
        "compile": dict(iv, command=" ".join(iv_cmd)),
    }

    reasons = []
    unknown_iv = set(iv.get("unknown_modules") or [])
    if gaps:
        reasons.append(
            "primitive-model gap: the pinned toolchain's simulation library "
            "provides no behavioral model for %s (%s); the simulator's own "
            "error output names exactly %s as unresolvable, and nothing in "
            "the open flow can elaborate these cells"
            % (", ".join(sorted(gaps)),
               "; ".join("%s: %s" % kv for kv in sorted(gaps.items())),
               ", ".join(sorted(unknown_iv)) or "nothing (see compile tail)"))

    sim = None
    if iv["rc"] == 0 and not iv["timed_out"]:
        # Elaboration succeeded against the toolchain's own models: run the
        # sentinel under its own cap and compare sample-exactly.
        vvp_cmd = ["vvp", "-n", vvp.name]
        sim = run_timed(vvp_cmd, cwd=workdir, timeout=args.sim_timeout,
                        log=out / "vsim_vvp.time")
        record["simulators_considered"]["iverilog"]["simulate"] = dict(
            sim, command=" ".join(vvp_cmd))
        sys.path.insert(0, str(root / "src"))
        sys.path.insert(0, str(root / "tb"))
        import run_voice as rv
        ctx = rv.voice_context("regression")
        case = rv.voice_derive_case_cached(ctx, args.case)
        expected = rv.voice_expected(case, rv.AUDIO_SAMPLES)
        capture = _captures(workdir, rv)
        rows = rv.voice_rows(capture, expected)
        counts = (len(capture["ctl"]), len(capture["audio1"]),
                  len(capture["audio2"]), len(capture["out"]))
        record["sentinel"]["observed_counts_ctl_a1_a2_out"] = list(counts)
        record["sentinel"]["mismatches"] = [str(r) for r in rows[:20]]
        record["sentinel"]["mismatch_count"] = len(rows)
        if sim["timed_out"]:
            cycles = _cycles_observed(workdir)
            rate = cycles / sim["elapsed_s"] if sim["elapsed_s"] else None
            reasons.append(
                "simulation time: %d of ~%d clip cycles in %.1f s (%.1f "
                "cycles/s) extrapolates to ~%.0f s (%.1f days) for one clip"
                % (cycles, CLIP_CYCLES, sim["elapsed_s"], rate,
                   CLIP_CYCLES / rate if rate else float("inf"),
                   CLIP_CYCLES / rate / 86400 if rate else float("inf")))
        elif counts == (rv.CONTROL_SAMPLES, rv.AUDIO_SAMPLES,
                        rv.AUDIO_SAMPLES, rv.AUDIO_SAMPLES) and not rows:
            record["verdict"] = "passed"
            record["reason"] = ("named sentinel %s reproduced sample-exactly"
                                " on the post-synthesis netlist" % args.case)
        else:
            record["verdict"] = "mismatch"
            record["reason"] = ("netlist disagreed with the fixed model "
                                "(see sentinel.mismatches)")
    elif iv["timed_out"]:
        reasons.append(
            "compile time: iverilog did not elaborate the %d-cell netlist "
            "within %d s (peak RSS %s kB)"
            % (record["netlist"]["cell_total"], args.compile_timeout,
               iv["max_rss_kb"]))
    else:
        reasons.append(
            "compile failed (rc=%s) after %.1f s, peak RSS %s kB -- "
            "output tail follows in simulators_considered.iverilog.compile"
            % (iv["rc"], iv["elapsed_s"] or -1, iv["max_rss_kb"]))

    # --- Verilator ---------------------------------------------------------
    if shutil.which("verilator"):
        ver_cmd = ["verilator", "--lint-only", "--timing", "-Wno-fatal",
                   "--top-module", "tb"] + [str(f) for f in files]
        ver = run_timed(ver_cmd, cwd=workdir,
                        timeout=args.verilator_timeout,
                        log=out / "vsim_verilator.time")
        record["simulators_considered"]["verilator"] = {
            "version": subprocess.run(["verilator", "--version"],
                                      capture_output=True,
                                      text=True).stdout.strip(),
            "lint_elaborate": dict(ver, command=" ".join(ver_cmd)),
        }

    if "verdict" not in record:
        tail_text = "\n".join(iv["tail"])
        gap_causal = (bool(unknown_iv) and unknown_iv <= set(gaps)) or (
            bool(gaps) and any(g in tail_text for g in gaps))
        if iv["timed_out"]:
            record["verdict"] = "refused"
            record["reason"] = reasons[-1]
        elif gaps and gap_causal and iv["rc"] != 0:
            record["verdict"] = "refused"
            record["reason"] = reasons[0] if reasons else "primitive-model gap"
        elif reasons and not gaps and iv["rc"] != 0:
            record["verdict"] = "refused"
            record["reason"] = reasons[0]
        else:
            record["verdict"] = "inconclusive"
            record["reason"] = (
                "compile failure not attributable to the measured model gap; "
                "see simulators_considered.iverilog.compile.tail")

    record["reasons"] = reasons
    record["no_shrink_note"] = ("the design was not modified, serialized or "
                                "re-mapped to make simulation feasible")
    (out / "post-synth-sim.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    rep.mkdir(parents=True, exist_ok=True)
    shutil.copy(out / "post-synth-sim.json", rep / "post-synth-sim.json")
    print("post-synth sentinel: %s (%s)" % (record["verdict"],
                                            record["reason"][:200]))
    if record["verdict"] == "mismatch":
        return 1
    if record["verdict"] == "inconclusive":
        return 3
    return 0


def _captures(workdir: Path, rv) -> dict:
    """Read one run's captures from workdir via the lane's own readers."""
    return {
        "ctl": rv.voice_capture_rows(workdir / "run0_ctlcap.txt", "ctlcap", 16),
        "audio1": [r for r in rv.voice_capture_rows(
            workdir / "run0_audiocap.txt", "audiocap", 15)
            if r is None or r[1] == 1],
        "audio2": [r for r in rv.voice_capture_rows(
            workdir / "run0_audiocap.txt", "audiocap", 15)
            if r is not None and r[1] == 2],
        "link1": [r for r in rv.voice_capture_rows(
            workdir / "run0_linkcap.txt", "linkcap", 3)
            if r is None or r[1] == 1],
        "link2": [r for r in rv.voice_capture_rows(
            workdir / "run0_linkcap.txt", "linkcap", 3)
            if r is not None and r[1] == 2],
        "out": [tuple(r) if r else (None, None) for r in
                rv.voice_capture_rows(workdir / "run0_outcap.txt", "outcap", 2)],
        "status": [int(v) if v.lstrip("-").isdigit() else None for v in
                   (workdir / "run0_status.txt").read_text().split()]
        if (workdir / "run0_status.txt").is_file() else [],
        "ops": _ops(workdir / "run0_ops.txt"),
    }


def _ops(path: Path) -> dict:
    """The bench's labeled op counters, parsed exactly as voice_simulate."""

    ops = {}
    if not path.is_file():
        return ops
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        fields = line.split()
        if fields[0] in ("A", "L"):
            key, rest = (fields[0], int(fields[1])), fields[2:]
        elif fields[0] == "U":
            key, rest = ("U", int(fields[1]), int(fields[2])), fields[3:]
        elif fields[0] == "M":
            key, rest = ("M",), fields[1:]
        else:
            key, rest = (fields[0], int(fields[1])), fields[2:]
        ops[key] = [int(v) if v.lstrip("-").isdigit() else None
                    for v in rest]
    return ops


def _cycles_observed(workdir: Path) -> int:
    cycles = 0
    for name, col in (("run0_ctlcap.txt", 0), ("run0_audiocap.txt", 0),
                      ("run0_outcap.txt", 0)):
        p = workdir / name
        if not p.is_file():
            continue
        for line in p.read_text(errors="replace").splitlines():
            fields = line.split()
            if fields:
                try:
                    cycles = max(cycles, int(fields[col]))
                except ValueError:
                    pass
    return cycles


if __name__ == "__main__":
    raise SystemExit(main())
