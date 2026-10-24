"""cocotb lane: patch-control render lifecycle at the scaled-down clip (#388).

Covers s7 (render happy path) and s8 (every rejection row) at
RENDER_SMALL_CLIP_BYTES. Mutations: ``tb/run_tb.py patch`` only.
"""
import cocotb

import patch_lane

SELECTED = patch_lane.install(
    {"s7-render", "s8-render-negative"}, patch_lane.rt.RENDER_SMALL_CLIP_BYTES
)


@cocotb.test()
async def test_patch_small_mirror_exact(dut):
    await patch_lane.check(dut, SELECTED, patch_lane.rt.RENDER_SMALL_CLIP_BYTES)
