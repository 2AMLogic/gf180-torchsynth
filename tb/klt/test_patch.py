"""cocotb lane: patch-control core vs its Python mirror, product clip (#388).

Covers s1-s6 and s9 (the 705,600 B/pass product-profile clip completion).
s7/s8 are the ``patch_small`` lane. Mutations: ``tb/run_tb.py patch`` only.
"""
import cocotb

import patch_lane

SELECTED = patch_lane.install(
    {"s1-full", "s2-negative", "s3-fatal", "s4-busy", "s5-timeout",
     "s6-badframe", "s9-render-full-clip"},
    patch_lane.rt.NOISE_CLIP_BYTES,
)


@cocotb.test()
async def test_patch_mirror_exact(dut):
    await patch_lane.check(dut, SELECTED, patch_lane.rt.NOISE_CLIP_BYTES)
