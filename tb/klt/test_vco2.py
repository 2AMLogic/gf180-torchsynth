"""cocotb lane: square/saw VCO engine vs the frozen vectors (#388).

Covers, in one simulation, every committed square/saw VCO vector (including
the frozen-binding cases checked against the retained sidecar words): all
four exported streams sample-exact over the full 176,400-sample clip, plus
the exported op-counter row. NOT covered here (they stay in
``tb/run_tb.py vco2``): reset/replay independence, the DR-0010 schedule and
clip-budget checks, and the RTL/stimulus mutation demonstrations.
"""
from __future__ import annotations

from pathlib import Path

import cocotb

from klt_lane import run_tb as rt
from klt_lane import wait_for_bench

WORKDIR = Path.cwd()

_formats = rt.AcceptedFormats()
_vectors = rt.vco_load_vectors()
rt.write_lut_memh(_formats.table, WORKDIR / "lut_quarter_cos.memh")

CASE_IDS = sorted(_vectors)
CASES = {}
for _run, _case_id in enumerate(CASE_IDS):
    _case = rt.vco2_derive_case(_formats, _vectors[_case_id])
    rt.vco2_write_case(WORKDIR, _run, _case)
    for _heavy in ("up_pitch", "fq", "square_q", "left_q"):
        del _case[_heavy]
    CASES[_case_id] = _case
(WORKDIR / "runs.txt").write_text("%d\n" % len(CASE_IDS), encoding="utf-8")


def _read_rows(path: Path):
    if not path.is_file():
        raise AssertionError("%s is missing: the bench never wrote it" % path)
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            fields = line.split()
            if len(fields) != len(rt.VCO_STREAM_NAMES):
                raise AssertionError("%s: malformed row %r" % (path, line))
            rows.append([int(word) for word in fields])
    return rows


@cocotb.test()
async def test_vco2_sample_exact(dut):
    """Every committed case: four streams, frozen sidecar words, counters."""
    await wait_for_bench(dut)
    failures = []
    for run, case_id in enumerate(CASE_IDS):
        case = CASES[case_id]
        try:
            rows = _read_rows(WORKDIR / ("run%d_out.txt" % run))
        except (AssertionError, ValueError) as error:
            failures.append("%s: %s" % (case_id, error))
            continue
        if len(rows) != rt.gv.CANONICAL_SAMPLE_COUNT:
            failures.append("%s: %d rows, not %d"
                            % (case_id, len(rows), rt.gv.CANONICAL_SAMPLE_COUNT))
            continue
        if not rt.vco2_check_case({"rows": rows}, case, case_id):
            failures.append("%s: sample mismatch" % case_id)
            continue
        ops_path = WORKDIR / ("run%d_ops.txt" % run)
        ops_rows = [line.split() for line in
                    ops_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()]
        expected = [str(x) for x in
                    rt.vco2_expected_ops(len(rows), case["sat_total"])]
        if not ops_rows or ops_rows[0] != expected:
            failures.append("%s: counters %r != %r" % (case_id, ops_rows, expected))
    assert not failures, "%d/%d cases failed:\n%s" % (
        len(failures), len(CASE_IDS), "\n".join(failures))
