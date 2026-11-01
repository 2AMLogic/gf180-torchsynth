"""cocotb lane: audio VCAs + pre-normalization mixer vs the frozen model (#388).

Covers the sample-exact RTL-vs-model check over every param-committed receipt
case plus the declared directed fixtures, including the exported op counters.
The reset/replay, stimulus/RTL mutation, fixture-class and curve-evidence
stages of ``tb/run_tb.py mix`` are not covered.
"""
from __future__ import annotations

import contextlib
import io
from pathlib import Path

import cocotb

from klt_lane import run_tb as rt
from klt_lane import wait_for_bench

WORKDIR = Path.cwd()

_receipt, _formats, _frozen = rt.mix_load_receipt()
_manifest, _fixtures = rt.mix_load_fixtures()
PLAN = [(case_id, _frozen[case_id]) for case_id in sorted(_frozen)]
PLAN += [(case_id, None) for case_id in rt.MIX_FIXTURE_CASES]
CASES = {}
for _run, (_case_id, _fz) in enumerate(PLAN):
    if _fz is None:
        _physical = _fixtures[_case_id]["physical"]
        _normalized = _fixtures[_case_id]["normalized"]
    else:
        _physical, _normalized = _fz["parameters"], None
    CASES[_case_id] = rt.mix_derive_case(
        _formats, _case_id, _physical, _normalized, _fz
    )
    rt.mix_write_case(WORKDIR, _run, CASES[_case_id])
(WORKDIR / "runs.txt").write_text("%d\n" % len(PLAN), encoding="utf-8")


def _read_captures():
    """Reuse ``mix_simulate``'s capture parser; the simulation already ran."""
    originals = (rt._compile_iverilog, rt._run)
    rt._compile_iverilog = lambda *a, **k: WORKDIR / "unused.vvp"
    rt._run = lambda *a, **k: None
    try:
        return rt.mix_simulate(WORKDIR, "iverilog", len(PLAN), None)
    finally:
        rt._compile_iverilog, rt._run = originals


@cocotb.test()
async def test_mix_sample_exact(dut):
    """Every committed/fixture case: six streams and op counters exact."""
    await wait_for_bench(dut)
    captures = _read_captures()
    failed = []
    log = io.StringIO()
    with contextlib.redirect_stdout(log):
        for run, (case_id, _fz) in enumerate(PLAN):
            case = CASES[case_id]
            truth = {"streams": case["streams"], "aux": case["aux"]}
            ok = rt.mix_check_case(captures[run], truth, case_id)
            expected_ops = rt.mix_expected_ops(case["samples"], truth)
            if captures[run]["ops"] != expected_ops:
                ok = False
                print(
                    "AUDIO-MIX FAILED: exported counters %r != expected %r (%s)"
                    % (captures[run]["ops"], expected_ops, case_id)
                )
            if not ok:
                failed.append(case_id)
    assert not failed, "RTL disagrees with the model for %s:\n%s" % (failed, log.getvalue())
