"""cocotb lane: format-true LUT sine DUT vs the frozen anchor vector (#388).

Covers the sample-exact RTL-vs-vector check on ``vco_1.raw``, the derived
mixer-level product vs the model-primitive mirror, the contract/LUT-digest
bindings and the clip cycle budget. Not covered here (stays in
``tb/run_tb.py anchor``): the planted-fault reporter check.
"""
from __future__ import annotations

from pathlib import Path

import cocotb

from klt_lane import run_tb as rt
from klt_lane import wait_for_bench

WORKDIR = Path.cwd()

FORMATS = rt.AcceptedFormats()
VECTOR = rt.gv.load_vector(rt.gv.SENTINEL_VECTOR_PATH)
rt.gv.verify_accepted_contract(VECTOR)
LUT_BOUND = VECTOR["provenance"].get("lut_sha256") == FORMATS.table.sha256()
SAMPLE_COUNT = int(VECTOR["clip"]["sample_count"])
PHASE_STEP, LEVEL_WORD = rt.derive_anchor_control_words(VECTOR, FORMATS)

rt.write_lut_memh(FORMATS.table, WORKDIR / "lut_quarter_cos.memh")
(WORKDIR / "stimulus.txt").write_text(
    "%d\n%d\n%d\n" % (PHASE_STEP, LEVEL_WORD, SAMPLE_COUNT), encoding="utf-8"
)


@cocotb.test()
async def test_anchor_sample_exact(dut):
    """RTL reproduces the frozen vco_1.raw trace and the mixer product."""
    assert LUT_BOUND, "accepted LUT digest does not match the vector binding"
    await wait_for_bench(dut)
    vco, mix = [], []
    for line in (WORKDIR / "captured.txt").read_text(encoding="utf-8").splitlines():
        if line.strip():
            v_word, m_word = line.split()
            vco.append(int(v_word))
            mix.append(int(m_word))
    cycles = int((WORKDIR / "cycles.txt").read_text(encoding="utf-8").strip())
    assert len(vco) == SAMPLE_COUNT and len(mix) == SAMPLE_COUNT, (
        "captured %d/%d samples, expected %d" % (len(vco), len(mix), SAMPLE_COUNT)
    )
    assert rt.check_clip_budget(cycles, SAMPLE_COUNT), "clip cycle budget failed"

    focus = rt.focus_vector(VECTOR, rt.ANCHOR_TRACE)
    mismatch = rt.gv.first_mismatch(focus, {rt.ANCHOR_TRACE: vco})
    assert mismatch is None, "RTL vs vector:\n" + rt.gv.format_mismatch(mismatch)

    _, mirror_mix = rt.anchor_mirror(FORMATS, PHASE_STEP, LEVEL_WORD, SAMPLE_COUNT)
    for index, (expected, actual) in enumerate(zip(mirror_mix, mix)):
        assert expected == actual, (
            "derived mixer product diverges at sample %d: expected %d, got %d"
            % (index, expected, actual)
        )
