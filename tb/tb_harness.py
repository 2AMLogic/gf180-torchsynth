"""Shared PDK-free testbench harness helpers (issue #383, increments 1-2).

Moved from ``tb/run_tb.py`` (``simulate`` is parameterized on stimulus and
sources), which re-exports every name here so existing ``import run_tb`` /
``from run_tb import ...`` consumers keep working.
Nothing here makes a synthesis, layout, signoff, or hardware claim.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def _run(command: list, cwd: Path) -> None:
    print("+ %s" % " ".join(command))
    subprocess.run(command, cwd=cwd, check=True)


def _compile_iverilog(workdir: Path, simulator: str, executable: str,
                      sources: list, flags: list = ()) -> Path:
    """Validate the simulator, compile ``sources`` in order, return the vvp.

    The one place the Icarus compile command is built. vvp execution,
    capture parsing and verdicts stay with each lane.
    """
    if simulator != "iverilog":
        raise SystemExit(
            "simulator %r is not wired up; this runner is PDK-free and "
            "currently supports iverilog" % simulator
        )
    vvp = workdir / executable
    _run(
        ["iverilog", "-g2012", *flags, "-o", str(vvp),
         *[str(source) for source in sources]],
        cwd=workdir,
    )
    return vvp


def simulate(workdir: Path, simulator: str, stimulus: list,
             sources: list) -> list:
    """Compile ``sources`` (DUT then testbench), drive ``stimulus``, capture.

    Writes ``stimulus.txt`` (one decimal value per line), runs the vvp, and
    returns the integers the testbench wrote to ``captured.txt``. Callers pass
    their own stimulus and source paths; nothing is read from module globals.
    """
    stim = workdir / "stimulus.txt"
    captured = workdir / "captured.txt"
    stim.write_text("".join("%d\n" % v for v in stimulus), encoding="utf-8")
    vvp = _compile_iverilog(workdir, simulator, "synth_selftest.vvp", sources)
    _run(["vvp", "-n", str(vvp)], cwd=workdir)
    return [
        int(line)
        for line in captured.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_lut_memh(table, path: Path) -> None:
    """Emit the accepted quarter-wave table as hex for $readmemh.

    Layout: C5_N_ENTRIES entry words, then the quarter-turn endpoint entry
    at index C5_N_ENTRIES (the DUT's endpoint branch reads it there).
    """
    lines = ["%06x" % entry for entry in table.entries]
    lines.append("%06x" % table.endpoint)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def mutate_sv(source: str, anchor: str, replacement: str, label: str) -> str:
    """Plant one anchored RTL mutation; refuse loudly if the anchor moved."""

    count = source.count(anchor)
    if count != 1:
        raise SystemExit(
            "MUTATION %s: anchor occurs %d times (expected 1); the RTL "
            "changed and the mutation seam must be re-anchored" % (label, count)
        )
    return source.replace(anchor, replacement)


def run_rtl_mutation(
    workdir: Path,
    label: str,
    case_id: str,
    write_mutant,
    write_case,
    simulate,
    is_detected,
    kind: str = "RTL",
    detected_message: str = "DETECTED (test fails the mutant)",
) -> bool:
    """Shared plant-mutation/simulate/detect/report skeleton.

    Every per-subsystem mutation runner shares this shape: make a scratch
    dir, plant one mutation, write the case, re-simulate, decide whether the
    mutant was caught, print a DETECTED/NOT DETECTED line, return
    ``detected``. Only the subsystem-specific pieces differ, so they are
    supplied as callables rather than folded in here:

    - ``write_mutant(mut_dir)`` -- write the mutated ``.sv`` (plus any
      auxiliary files, e.g. a LUT memh) into ``mut_dir``; a no-op for a
      stimulus-only mutation that leaves the DUT unmutated.
    - ``write_case(mut_dir)`` -- materialize the subsystem's test case.
    - ``simulate(mut_dir)`` -- re-run the subsystem's ``*_simulate`` helper
      and return whatever capture object ``is_detected`` expects (opaque to
      this helper).
    - ``is_detected(capture)`` -- the subsystem's own comparison logic;
      returns ``(detected, suffix)``, with ``suffix`` appended verbatim to
      the report line (``""`` for none, e.g. a localization note).
    """

    mut_dir = workdir / ("mut-" + label)
    mut_dir.mkdir(parents=True, exist_ok=True)
    write_mutant(mut_dir)
    write_case(mut_dir)
    capture = simulate(mut_dir)
    detected, suffix = is_detected(capture)
    print(
        "mutation %s (%s, case %s): %s%s"
        % (
            label,
            kind,
            case_id,
            detected_message if detected else "NOT DETECTED",
            suffix,
        )
    )
    return detected
