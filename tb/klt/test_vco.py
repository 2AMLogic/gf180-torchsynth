"""cocotb lane: sine VCO engine vs the frozen whole-voice traces (#388).

Covers, in one simulation, every committed receipt case and every committed
directed sine-VCO vector (full 176,400-sample clip each): sample-exact
``vco_1.raw`` words and post-step phase words, plus the exported op-counter
row. NOT covered here (they stay in ``tb/run_tb.py vco``): reset/replay
independence, schedule/budget checks and RTL/stimulus mutation demonstrations.
Capture integrity uses ``run_tb.vco_read_captures`` (short walks, truncated
or missing files, wrong-width rows are all hard failures, never passes).
"""
from __future__ import annotations

from array import array
from pathlib import Path

import cocotb

from klt_lane import run_tb as rt
from klt_lane import wait_for_bench

WORKDIR = Path.cwd()

_receipt, _formats, _cases = rt.vco_load_receipt()
_fcp = rt.FixedControlPath(_formats.control_spec)
rt.write_lut_memh(_formats.table, WORKDIR / "lut_quarter_cos.memh")

#: (case id, expected vco words, expected phase words, ops, sidecar?)
RUNS = []


def _stage(case):
    run = len(RUNS)
    rt.vco_write_case(WORKDIR, run, case)
    RUNS.append(
        {
            "id": case["id"],
            "mirror": {
                "vco": array("q", case["mirror"]["vco"]),
                "phase": array("q", case["mirror"]["phase"]),
            },
            "ops": rt.vco_expected_ops(len(case["mirror"]["vco"]), case),
        }
    )


for _case_id in sorted(_cases):
    _stage(rt.vco_derive_case(_fcp, _formats, _cases[_case_id]))
_directed = rt.sine_load_vectors()
for _name in sorted(_directed):
    _stage(rt.sine_derive_vector(_formats, _directed[_name]))
(WORKDIR / "runs.txt").write_text("%d\n" % len(RUNS), encoding="utf-8")


@cocotb.test()
async def test_vco_sample_exact(dut):
    """Every receipt + directed case: vco_1.raw, phase and op counters."""
    await wait_for_bench(dut)
    captures = rt.vco_read_captures(
        WORKDIR, len(RUNS), rt.gv.CANONICAL_SAMPLE_COUNT
    )
    failures = []
    for run, expected in enumerate(RUNS):
        capture = captures[run]
        if not rt.vco_check_case(capture, expected, expected["id"]):
            failures.append("%s: sample mismatch" % expected["id"])
        elif capture["ops"] != expected["ops"]:
            failures.append(
                "%s: counters %r != %r"
                % (expected["id"], capture["ops"], expected["ops"])
            )
    assert not failures, "%d/%d cases failed:\n%s" % (
        len(failures), len(RUNS), "\n".join(failures))
