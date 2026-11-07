"""Shared body of the two patch-control cocotb lanes (#388).

``tb/sv/tb_patch_control.sv`` elaborates one ``NOISE_CLIP_BYTES`` value, so
the patch scenarios split across two requests: ``patch`` (product-profile
clip: s1-s6 and s9) and ``patch_small`` (the scaled-down clip: s7, s8). The
scenario table, the mirror and every scenario's check callable come from
``tb/run_tb.py``'s ``patch_plan`` and ``compare_run`` -- the same code
``run_tb.py patch`` uses -- so the verdict logic is not forked; only the
simulation step is replaced by this request's bench outputs.

Not covered here (still gated by ``tb/run_tb.py patch``): the eleven RTL
mutation demonstrations and the accepted-schedule clip-length bound check.
"""
from __future__ import annotations

from pathlib import Path

from klt_lane import run_tb as rt
from klt_lane import wait_for_bench

WORKDIR = Path.cwd()


def install(keys, clip_bytes):
    """Write one stimulus file per selected scenario; return the plan."""
    model = rt.patch_build_model()
    words, vector = rt.patch_anchor_words()
    _scenarios, _render, entries = rt.patch_plan(model, words, vector)
    selected = [e for e in entries if e[1] in keys]
    assert [e[1] for e in selected] == [k for k in _ordered(entries) if k in keys]
    for index, (_label, key, run_model, schedule, _checks) in enumerate(selected):
        assert run_model.noise_clip_bytes == clip_bytes, (
            "%s mirrors a %d B clip but the bench is elaborated at %d B"
            % (key, run_model.noise_clip_bytes, clip_bytes)
        )
        rt.write_schedule(WORKDIR / ("run%d_stimulus.txt" % index), schedule)
    (WORKDIR / "runs.txt").write_text("%d\n" % len(selected), encoding="utf-8")
    return selected


def _ordered(entries):
    return [e[1] for e in entries]


async def check(dut, selected, clip_bytes):
    await wait_for_bench(dut)
    by_dir = {key: index for index, (_l, key, _m, _s, _c) in enumerate(selected)}

    def recorded(workdir, simulator, stim, dut_sv, clip=None):
        assert clip == clip_bytes, "clip %r != bench %r" % (clip, clip_bytes)
        index = by_dir[Path(stim).parent.name]
        return rt.patch_read_outputs(
            WORKDIR / ("run%d_captured.txt" % index),
            WORKDIR / ("run%d_final.txt" % index),
        )

    original = rt.patch_simulate
    rt.patch_simulate = recorded
    try:
        failed = [
            key
            for label, key, run_model, schedule, checks in selected
            if not rt.compare_run(label, schedule, WORKDIR / "compare" / key,
                                  "iverilog", run_model, rt.PATCH_DUT_SV, checks)
        ]
    finally:
        rt.patch_simulate = original
    assert not failed, "RTL disagrees with the patch-control mirror: %s" % failed
