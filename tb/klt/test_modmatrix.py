"""cocotb lane: mod matrix + endpoint-aligned upsample vs golden vectors (#388).

Covers the sample-exact RTL-vs-vector check only (matrix words, audio
streams, audio digests). The reset/replay, op-budget and RTL-mutation stages
of ``tb/run_tb.py modmatrix`` are not covered.
"""
from __future__ import annotations

import contextlib
import io
from pathlib import Path

import cocotb

from klt_lane import run_tb as rt
from klt_lane import wait_for_bench

WORKDIR = Path.cwd()

_formats = rt.AcceptedFormats()
_fcp = rt.FixedControlPath(_formats.control_spec)
VECTORS = rt.mm_load_vectors()
CASE_IDS = sorted(VECTORS)
CASES = {
    case_id: rt.mm_derive_case(_fcp, _formats, VECTORS[case_id])
    for case_id in CASE_IDS
}
for _run, _case_id in enumerate(CASE_IDS):
    rt.mm_write_case(WORKDIR, _run, CASES[_case_id])
(WORKDIR / "runs.txt").write_text("%d\n" % len(CASE_IDS), encoding="utf-8")


def _read_captures():
    """Reuse ``mm_simulate``'s capture parser; the simulation already ran."""
    originals = (rt._compile_iverilog, rt._run)
    rt._compile_iverilog = lambda *a, **k: WORKDIR / "unused.vvp"
    rt._run = lambda *a, **k: None
    try:
        return rt.mm_simulate(WORKDIR, "iverilog", len(CASE_IDS), None)
    finally:
        rt._compile_iverilog, rt._run = originals


@cocotb.test()
async def test_modmatrix_sample_exact(dut):
    """Every committed mod-matrix vector: matrix + upsampled audio exact."""
    await wait_for_bench(dut)
    captures = _read_captures()
    failed = []
    log = io.StringIO()
    for run, case_id in enumerate(CASE_IDS):
        with contextlib.redirect_stdout(log):
            ok = rt.mm_check_case(captures[run], CASES[case_id], VECTORS[case_id], case_id)
        if not ok:
            failed.append(case_id)
    assert not failed, "RTL disagrees with golden vectors %s:\n%s" % (failed, log.getvalue())
