"""cocotb lane: ADSR engine vs the frozen fixed model's golden vectors (#388)."""
from __future__ import annotations

from pathlib import Path

import cocotb

from klt_lane import run_tb as rt
from klt_lane import wait_for_bench

WORKDIR = Path.cwd()

_formats = rt.AcceptedFormats()
_fcp = rt.FixedControlPath(_formats.control_spec)
VECTORS = rt.adsr_load_vectors()
CASE_IDS = sorted(VECTORS)
CASES = {
    case_id: rt.adsr_derive_case(_fcp, _formats, VECTORS[case_id])
    for case_id in CASE_IDS
}
for _run, _case_id in enumerate(CASE_IDS):
    rt.adsr_write_case(WORKDIR, _run, CASES[_case_id])
(WORKDIR / "runs.txt").write_text("%d\n" % len(CASE_IDS), encoding="utf-8")


@cocotb.test()
async def test_adsr_sample_exact(dut):
    """Every committed ADSR vector: six instances, sample-exact."""
    await wait_for_bench(dut)
    failures = []
    for run, case_id in enumerate(CASE_IDS):
        captured = {}
        for index, prefix in enumerate(CASES[case_id]):
            path = WORKDIR / ("run%d_captured%d.txt" % (run, index))
            captured[rt.ag.trace_name(prefix)] = [
                int(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        mismatch = rt.gv.first_mismatch(VECTORS[case_id], captured)
        if mismatch is not None:
            failures.append("%s: %s" % (case_id, rt.gv.format_mismatch(mismatch)))
    assert not failures, "RTL disagrees with golden vectors:\n" + "\n".join(failures)
