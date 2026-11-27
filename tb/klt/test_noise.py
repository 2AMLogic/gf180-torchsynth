"""cocotb lane: host-fed exact noise-stream DUT vs the golden receipt (#388).

One simulation carries every committed receipt case, the synthetic edge-
pattern stream and the sound 0 -> 32 replay pair, as in ``tb/run_tb.py
noise``. Covers the sample-exact RTL-vs-golden-lane compare, the clean-status
check (latched slot, byte/sample counts) and the replay equality. Not
covered here (stays in ``run_tb.py noise``): the pre-render gate and digest
bindings, the DR-0010 budget rows and the mutation battery.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

import cocotb

from klt_lane import run_tb as rt
from klt_lane import wait_for_bench

WORKDIR = Path.cwd()
nsg = rt.nsg

RECEIPT = json.loads(rt.NOISE_RECEIPT_PATH.read_bytes())
assert RECEIPT["kind"] == "fixed-voice-golden-release-receipt"

PLAN = []  # (label, sound_index, declared_slot, raw bytes, expected mirror)
for _case in RECEIPT["cases"]:
    _raw = nsg.resolve_canonical_bytes(_case["sound_index"])
    assert nsg.noise_bytes_sha256(_raw) == _case["noise"]["sha256"]
    _mirror = nsg.mirror_stream(_raw)
    assert nsg.trace_digest(_mirror) == _case["traces"]["noise.raw"]
    PLAN.append((_case["id"], _case["sound_index"],
                 nsg.canonical_slot(_case["sound_index"]), _raw, _mirror))
_by_index = {entry[1]: entry for entry in PLAN}  # receipt cases only
_patterns = b"".join(struct.pack("<I", b) for b in rt.NOISE_PATTERN_BITS)
_patterns += b"\x00\x00\x00\x00" * (
    nsg.EXPECTED_NOISE_BYTES // 4 - len(rt.NOISE_PATTERN_BITS)
)
PLAN.append(("synthetic-patterns", 0, 0, _patterns, nsg.mirror_stream(_patterns)))
_first, _replay = rt.NOISE_REPLAY_INDICES
_raw_first = _by_index[_first][3]
_raw_replay = nsg.resolve_canonical_bytes(_replay)
assert nsg.noise_bytes_sha256(_raw_replay) == nsg.noise_bytes_sha256(_raw_first), (
    "replay pair must share a slot by the C8 rule"
)
PLAN.append(("replay-sound-0", _first, 0, _raw_first, _by_index[_first][4]))
PLAN.append(("replay-sound-32", _replay, nsg.canonical_slot(_replay),
             _raw_replay, nsg.mirror_stream(_raw_replay)))

for _run, (_label, _idx, _slot, _raw, _m) in enumerate(PLAN):
    rt.noise_write_run(WORKDIR, _run, _idx, _slot, _raw)
(WORKDIR / "runs.txt").write_text("%d\n" % len(PLAN), encoding="utf-8")


@cocotb.test()
async def test_noise_sample_exact(dut):
    """Every fed stream reproduces the golden lane word-for-word."""
    await wait_for_bench(dut)
    failures = []
    captures = []
    for run, (label, _idx, slot, raw, mirror) in enumerate(PLAN):
        captured = [
            int(line) for line in
            (WORKDIR / ("run%d_captured.txt" % run)).read_text().splitlines()
            if line.strip()
        ]
        status = [
            int(v) for v in
            (WORKDIR / ("run%d_status.txt" % run)).read_text().split()
        ]
        captures.append(captured)
        if captured != mirror:
            where = next((i for i, (w, g) in enumerate(zip(mirror, captured))
                          if w != g), None)
            failures.append("%s: capture != golden lane (first mismatch %s, "
                            "lengths %d vs %d)"
                            % (label, where, len(mirror), len(captured)))
        elif status != [0, 0, len(raw), len(mirror), len(mirror), slot]:
            failures.append("%s: status %r not clean" % (label, status))
    if captures[-1] != captures[-2]:
        failures.append("replay pair captures differ")
    assert not failures, "\n".join(failures)
