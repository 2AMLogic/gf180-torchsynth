"""cocotb lane: LFO + control-VCA engine vs the frozen golden vectors (#388).

Covers the sample-exact RTL-vs-vector check only. The reset/replay,
op-budget and RTL-mutation stages of ``tb/run_tb.py lfo`` are not covered.
"""
from __future__ import annotations

from pathlib import Path

import cocotb

from klt_lane import run_tb as rt
from klt_lane import wait_for_bench

WORKDIR = Path.cwd()

_formats = rt.AcceptedFormats()
_fcp = rt.FixedControlPath(_formats.control_spec)
VECTORS = rt.lfo_load_vectors()
CASE_IDS = sorted(VECTORS)
CASES = {
    case_id: rt.lfo_derive_case(_fcp, _formats, VECTORS[case_id])
    for case_id in CASE_IDS
}
rt.write_lut_memh(_fcp.table, WORKDIR / "lut.memh")
for _run, _case_id in enumerate(CASE_IDS):
    rt.lfo_write_case(WORKDIR, _run, CASES[_case_id])
(WORKDIR / "runs.txt").write_text("%d\n" % len(CASE_IDS), encoding="utf-8")


@cocotb.test()
async def test_lfo_sample_exact(dut):
    """Every committed LFO/VCA vector: two instances, sample-exact."""
    await wait_for_bench(dut)
    failures = []
    for run, case_id in enumerate(CASE_IDS):
        raw, post = {}, {}
        for index, side in enumerate(CASES[case_id]):
            pairs = []
            path = WORKDIR / ("run%d_captured%d.txt" % (run, index))
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    a, b = line.split()
                    pairs.append((int(a), int(b)))
            raw[rt.lgo.raw_trace(side)] = [a for a, _ in pairs]
            post[rt.lgo.vca_trace(side)] = [b for _, b in pairs]
        mismatch = rt.gv.first_mismatch(VECTORS[case_id], raw | post)
        if mismatch is not None:
            failures.append("%s: %s" % (case_id, rt.gv.format_mismatch(mismatch)))
    assert not failures, "RTL disagrees with golden vectors:\n" + "\n".join(failures)
