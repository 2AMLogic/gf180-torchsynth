"""cocotb lane: normalization replay controller vs ``normalize_words()`` (#388).

Covers, in one simulation, the committed case list ``tb/run_tb.py normreplay``
runs: the isolated directed and anchored Q2.21 grids, the full-voice
normalization-family cases (trace digests bound to the frozen receipt), and
the back-to-back replay pair. Every capture must equal the host mirror
sample-exactly and every status row must match the mirror's diagnostics.
NOT covered here (they stay in ``tb/run_tb.py normreplay``): the DR-0010 op
budget and framing checks, the pass/digest binding-reject and sticky-overrun
runs, and the RTL mutation demonstrations.
"""
from __future__ import annotations

import json
from pathlib import Path

import cocotb

from klt_lane import run_tb as rt
from klt_lane import wait_for_bench

WORKDIR = Path.cwd()

_formats = rt.AcceptedFormats()
_cases = []
_directed = rt.nr.directed_cases()
for _label in sorted(_directed):
    _cases.append((_label, _directed[_label]))
_anchored = rt.nr.anchored_cases()
for _label in sorted(_anchored):
    _cases.append((_label, _anchored[_label][0]))

_receipt = json.loads(rt.NOISE_RECEIPT_PATH.read_bytes())
_receipt_cases = {c["id"]: c for c in _receipt["cases"]}
FIXTURE_FAILURES = []
for _case_id in rt.nrg.FULL_VOICE_CASES:
    _fixed, _diag = rt.nrg.resolve_full_voice_case(_case_id, _formats)
    for _trace in ("mixer.pre_normalization", "mixer.peak", "mixer.gain",
                   "mixer.output"):
        if (rt.nrg.digest_words(_fixed[_trace])
                != _receipt_cases[_case_id]["traces"][_trace]):
            FIXTURE_FAILURES.append("%s %s digest != frozen receipt"
                                    % (_case_id, _trace))
    _cases.append((_case_id, _fixed["mixer.pre_normalization"]))
_replay_clip = _directed[rt.NORMREPLAY_REPLAY_CASE]
_cases.append((rt.NORMREPLAY_REPLAY_CASE + "[replay-a]", _replay_clip))
_cases.append((rt.NORMREPLAY_REPLAY_CASE + "[replay-b]", _replay_clip))

EXPECTED = []
for _index, (_label, _mix) in enumerate(_cases):
    EXPECTED.append((_label, *rt.nrg.mirror_normalize(_mix, _formats)))
    rt.normreplay_write_run(WORKDIR, _index, len(_mix), len(_mix), _mix, _mix)
(WORKDIR / "runs.txt").write_text("%d\n" % len(_cases), encoding="utf-8")


def _ints(path: Path, split_lines: bool):
    if not path.is_file():
        raise AssertionError("%s is missing: the bench never wrote it" % path)
    text = path.read_text(encoding="utf-8")
    items = text.splitlines() if split_lines else text.split()
    return [int(item) for item in items if item.strip()]


@cocotb.test()
async def test_normreplay_sample_exact(dut):
    """Every committed case: capture == mirror output, clean status row."""
    await wait_for_bench(dut)
    failures = list(FIXTURE_FAILURES)
    captures = {}
    for index, (label, out, diag) in enumerate(EXPECTED):
        try:
            captured = _ints(WORKDIR / ("run%d_captured.txt" % index), True)
            status = _ints(WORKDIR / ("run%d_status.txt" % index), False)
        except (AssertionError, ValueError) as error:
            failures.append("%s: %s" % (label, error))
            continue
        captures[label] = captured
        if captured != out:
            first = next((i for i, (a, b) in enumerate(zip(out, captured))
                          if a != b), None)
            failures.append(
                "%s: capture != mirror (first mismatch %s; lengths %d/%d)"
                % (label, first, len(out), len(captured)))
            continue
        want = rt.normreplay_expected_status(diag)
        if status[:7] != want:
            failures.append("%s: status %r != %r" % (label, status[:7], want))
    a = captures.get(rt.NORMREPLAY_REPLAY_CASE + "[replay-a]")
    b = captures.get(rt.NORMREPLAY_REPLAY_CASE + "[replay-b]")
    if a is None or a != b:
        failures.append("replay pair captures differ (state leaked across triggers)")
    assert not failures, "%d problem(s):\n%s" % (len(failures), "\n".join(failures))
