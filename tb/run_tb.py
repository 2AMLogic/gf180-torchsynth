#!/usr/bin/env python3
"""PDK-free RTL testbench runner (issue #68; the AGENTS.md tb convenience).

``selftest`` proves the golden-vector harness end to end against a tiny
synthetic SystemVerilog DUT using an open-source simulator (Icarus
Verilog; no PDK, no vendor tooling):

1. generate a synthetic stimulus and its matching golden vector,
2. compile and run tb/sv/synth_dut.sv + tb/sv/tb_synth_dut.sv,
3. load the captured stream and assert the pass path (reporter: no
   mismatch), then
4. compare against an intentionally corrupted vector and assert the
   first-mismatch reporter names the exact cycle/sample/trace/expected/
   actual row of the planted fault.

Exit 0 only if BOTH paths are demonstrated. Nothing here touches the
DR-0008 choice register or claims any numeric conformance.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TB_ROOT = Path(__file__).resolve().parent
ROOT = TB_ROOT.parent
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import golden_vectors as gv  # noqa: E402

DUT_SV = TB_ROOT / "sv/synth_dut.sv"
TB_SV = TB_ROOT / "sv/tb_synth_dut.sv"

#: Trace name from the canonical registry used for the synthetic stream.
SYNTH_TRACE = "mixer.output"
SYNTH_PARAMETER = "adsr_1.alpha"
CYCLE_START = 0

STIMULUS = [0, 1, 2, 165, 255, 128, 7, 42]
XOR_MASK = 0xA5


def synth_model(value: int) -> int:
    """Software mirror of the synthetic DUT's registered XOR."""
    return (value & 0xFF) ^ XOR_MASK


def build_vector(values, registry, inventory) -> dict:
    vector = {
        "schema": gv.VECTOR_SCHEMA,
        "schema_version": gv.SCHEMA_VERSION,
        "provenance": {
            "generator": "tb/run_tb.py selftest",
            "note": (
                "synthetic-bringup vectors for the harness self-test; not "
                "default-nebula clip content and not DR-0008-conformant"
            ),
        },
        "trace_registry_version": registry["semantic_version"],
        "parameter_inventory_commit": inventory["source"]["commit"],
        "clip": {
            "profile": gv.SYNTHETIC_PROFILE,
            "sample_rate_hz": gv.CANONICAL_SAMPLE_RATE_HZ,
            "sample_count": len(values),
            "control_rate_hz": gv.CANONICAL_CONTROL_RATE_HZ,
            "control_count": len(values),
        },
        "parameters": {SYNTH_PARAMETER: 0.5},
        "traces": [
            {
                "name": SYNTH_TRACE,
                "kind": "audio",
                "encoding": "synthetic-int",
                "cycle_start": CYCLE_START,
                "values": list(values),
            }
        ],
    }
    vector["content_hash"] = gv.compute_content_hash(vector)
    return vector


def _run(command: list, cwd: Path) -> None:
    print("+ %s" % " ".join(command))
    subprocess.run(command, cwd=cwd, check=True)


def simulate(workdir: Path, simulator: str) -> list:
    stim = workdir / "stimulus.txt"
    captured = workdir / "captured.txt"
    stim.write_text("".join("%d\n" % v for v in STIMULUS), encoding="utf-8")
    if simulator == "iverilog":
        vvp = workdir / "synth_selftest.vvp"
        _run(
            ["iverilog", "-g2012", "-o", str(vvp), str(DUT_SV), str(TB_SV)],
            cwd=workdir,
        )
        _run(["vvp", "-n", str(vvp)], cwd=workdir)
    else:
        raise SystemExit(
            "simulator %r is not wired up; this runner is PDK-free and "
            "currently supports iverilog" % simulator
        )
    values = [
        int(line)
        for line in captured.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return values


def selftest(workdir: Path, simulator: str) -> int:
    registry = gv.load_registry_document()
    inventory = gv.load_inventory_document()
    expected = [synth_model(v) for v in STIMULUS]

    captured = simulate(workdir, simulator)

    ok = True
    if captured != expected:
        print("SELFTEST BROKEN: captured stream does not match the model")
        ok = False

    golden = build_vector(expected, registry, inventory)
    gv.load_vector(golden, registry=registry, inventory=inventory)

    mismatch = gv.first_mismatch(
        golden, {SYNTH_TRACE: captured}
    )
    if mismatch is not None:
        print("FAIL path: unexpected mismatch on the clean golden vector:")
        print(gv.format_mismatch(mismatch))
        ok = False
    else:
        print(
            "PASS path verified: clean golden vector vs captured stream -> "
            "no mismatch (reporter silent)."
        )

    corrupted = build_vector(expected, registry, inventory)
    corrupted["traces"][0]["values"][3] = (expected[3] + 1) & 0xFF
    corrupted["content_hash"] = gv.compute_content_hash(corrupted)
    # The corrupted vector is the EXPECTED side of the comparison and the
    # captured sim stream is the ACTUAL side: the vector claims one value,
    # the DUT delivered another, and the reporter must name that row.
    planted = gv.Mismatch(
        cycle=CYCLE_START + 3,
        sample=3,
        trace=SYNTH_TRACE,
        expected=corrupted["traces"][0]["values"][3],
        actual=expected[3],
    )
    observed = gv.first_mismatch(corrupted, {SYNTH_TRACE: captured})
    if observed != planted:
        print(
            "FAIL path: reporter did not name the planted fault; observed %r"
            % (observed,)
        )
        ok = False
    else:
        print("Intentional-fail path verified; reporter reports:")
        print(gv.format_mismatch(observed))

    if not ok:
        print("SELFTEST FAILED")
        return 1
    print("SELFTEST PASSED (pass path + intentional-fail path)")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        nargs="?",
        default="selftest",
        choices=["selftest"],
        help="only 'selftest' exists today",
    )
    parser.add_argument(
        "--simulator",
        default="iverilog",
        choices=["iverilog"],
        help="open-source simulator (PDK-free)",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=None,
        help="keep artifacts here instead of a temp dir",
    )
    args = parser.parse_args(argv)

    if shutil.which(args.simulator) is None:
        print(
            "ERROR: %s is not installed; the self-test cannot run here. "
            "CI (.github/workflows/tb-sim.yml) arbitrates on a host that "
            "installs it. An unrun check is never a pass." % args.simulator
        )
        return 3

    if args.workdir is not None:
        workdir = args.workdir
        workdir.mkdir(parents=True, exist_ok=True)
        return selftest(workdir, args.simulator)

    with tempfile.TemporaryDirectory(prefix="tb-selftest-") as tmp:
        return selftest(Path(tmp), args.simulator)


if __name__ == "__main__":
    sys.exit(main())
