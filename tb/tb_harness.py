"""Shared PDK-free testbench harness helpers (issues #383, #472).

Moved from ``tb/run_tb.py`` (``simulate`` is parameterized on stimulus and
sources), which re-exports every name here so existing ``import run_tb`` /
``from run_tb import ...`` consumers keep working. #472 folded in the lane
scaffold every engine used to copy per lane -- the golden-vector loader,
the op-counter reader, the multi-run simulate wrapper and the owner-row
budget loop -- parameterized on exactly the things that differ per lane,
so adding an engine adds a table and a call site, not another copy.
Nothing here makes a synthesis, layout, signoff, or hardware claim.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Importable on its own, not only as a side effect of ``tb/run_tb.py``
# having already put ``src/`` on the path before importing this module.
_SRC_ROOT = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from torchsynth_voice import golden_vectors as gv  # noqa: E402


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


def load_golden_vectors(directory: Path, label: str, hint: str = "") -> dict:
    """Load and contract-verify every committed golden vector in a directory.

    The one loader every engine lane's ``*_load_vectors`` wrapper calls:
    ``*.json`` in sorted order, each vector hash-linked to the accepted
    numeric contract (``verify_accepted_contract``, the #68 anchor flow),
    keyed by file stem. An empty directory is a loud ``SystemExit`` naming
    ``label`` and ``directory``, with ``hint`` appended verbatim, so a
    stale constants package or choice register refuses the run with
    "regenerate, don't recompile" rather than silently recompiling.
    """

    vectors = {}
    for path in sorted(directory.glob("*.json")):
        vector = gv.load_vector(path)
        gv.verify_accepted_contract(vector)
        vectors[path.stem] = vector
    if not vectors:
        raise SystemExit(
            "no %s golden vectors found in %s%s" % (label, directory, hint)
        )
    return vectors


def read_ops_rows(workdir: Path, runs: int) -> list:
    """Per-run per-instance op counter totals from a tb's ops files.

    Reads ``run<N>_ops.txt`` for each run and returns one integer row per
    non-blank line, in run-major order. Shared by every engine lane whose
    testbench exports its sticky op counters this way.
    """

    rows = []
    for run in range(runs):
        path = workdir / ("run%d_ops.txt" % run)
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append([int(v) for v in line.split()])
    return rows


def simulate_runs(workdir: Path, simulator: str, runs: int, executable: str,
                  sources: list):
    """Compile + run a multi-run TB; return (captures, statuses) per run.

    Writes ``runs.txt``, compiles ``sources`` into ``executable``, runs the
    vvp once, then reads the per-run ``run<N>_captured.txt`` /
    ``run<N>_status.txt`` files the testbench wrote. Every multi-run lane
    shares this exact shape; only the vvp name and the source list differ.
    """

    (workdir / "runs.txt").write_text("%d\n" % runs, encoding="utf-8")
    vvp = _compile_iverilog(workdir, simulator, executable, sources)
    _run(["vvp", "-n", str(vvp)], cwd=workdir)
    captures = []
    statuses = []
    for r in range(runs):
        captured = [
            int(line)
            for line in (
                workdir / ("run%d_captured.txt" % r)
            ).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        status = [
            int(v)
            for v in (
                workdir / ("run%d_status.txt" % r)
            ).read_text(encoding="utf-8").split()
        ]
        captures.append(captured)
        statuses.append(status)
    return captures, statuses


def check_owner_row_budget(schedule, expectations: dict, owner_row_label: str,
                           constants_check) -> bool:
    """Table-driven DR-0010 owner-row op-count report + emission check.

    ``expectations`` maps a counter name to ``(actual, limit)``; a ``None``
    limit is reported, never asserted. ``owner_row_label`` names the owner
    row in the printed line ("DR-0010", "DR-0010 #71"). The per-engine
    expectation tables stay with each lane -- only the printing/compare
    loop and the pass/fail accumulation are shared.

    The constants-package comparison is injected as
    ``constants_check(schedule)`` (the same callable convention
    :func:`run_rtl_mutation` uses) so this helper needs no
    constants-package global of its own. An exception from it is reported
    and returns False: an emission that could not be compared is never a
    silent pass.
    """

    ok = True
    for name, (actual, limit) in expectations.items():
        verdict = "n/a (reported)"
        if limit is not None:
            verdict = "OK" if actual <= limit else "FAIL"
            ok = ok and actual <= limit
        print(
            "  budget: %d %s per control tick vs %s owner-row cap %s -> %s"
            % (actual, name, owner_row_label, limit, verdict)
        )
    try:
        matches = constants_check(schedule)
        ok = ok and matches
    except Exception as error:  # noqa: BLE001 - reported, never a silent pass
        print("  budget constants: emission failed -> %s" % error)
        return False
    return ok
