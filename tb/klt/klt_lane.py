"""Shared helpers for the ``klt functional-verification`` cocotb lanes (#388).

Each lane module prepares its stimulus files in the simulator's working
directory at *import* time (cocotb imports the test module before the
bench's time-0 ``initial`` block opens them), waits for the bench's
``klt_done`` flag (set only after every capture file is closed), and then
compares the captures with the same golden-vector code ``tb/run_tb.py``
uses. A bench that ends early through ``$finish`` (every TB-ERROR path) leaves
the test unfinished, which cocotb reports as a failure.
"""
from __future__ import annotations

import sys
from pathlib import Path

KLT_DIR = Path(__file__).resolve().parent
TB_ROOT = KLT_DIR.parent
REPO_ROOT = TB_ROOT.parent
sys.path.insert(0, str(TB_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

import run_tb  # noqa: E402,F401  (imported for lane helpers; no side effects)
from cocotb.triggers import RisingEdge, Timer  # noqa: E402


async def wait_for_bench(dut, limit_ns: int = 10**12):
    """Return once the bench raised ``klt_done`` (or fail on a timeout)."""
    await Timer(1, unit="ns")
    if int(dut.klt_done.value) == 1:
        return
    await RisingEdge(dut.klt_done)
