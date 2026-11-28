"""Normalization-replay lane of the gf180 testbench harness (issue #77,
split out by #597).

``tb/run_tb.py`` re-exports selected names such as ``normreplay`` and
calls :func:`bind_core` right after defining the shared symbols this lane
needs (``CONSTANTS_PKG_SV``, ``NOISE_RECEIPT_PATH`` and
``_emitted_constants_match``), so the lane reads them from the one live
``run_tb`` module (including when it runs as ``__main__``) instead of
copying them or importing a second copy.

The render-receiver half (issue #211) rides along:
``render_binding_frames`` / ``render_binding_integration`` take the patch
frame builders, model and scheduler straight from ``tb/lane_patch.py``.

The ``nrg`` (``normalization_replay_golden``) module alias travels with the
lane: ``tb/run_voice.py`` and ``tb/run_oneshot.py`` import it off
``run_tb``, which re-exports it from here.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

TB_ROOT = Path(__file__).resolve().parent
ROOT = TB_ROOT.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(TB_ROOT))

from lane_patch import (  # noqa: E402
    CAPS_WITH_RENDER,
    CORE_IDENTITY,
    PATCH_DUT_SV,
    RENDER_DIGEST_A,
    RENDER_DIGEST_B,
    SeqCounter,
    noise_pass_frames,
    paced_render,
    patch_anchor_words,
    patch_build_model,
    render_full_tx,
    render_hello,
    render_trigger_frame,
    schedule_of,
    write_schedule,
)
from tb_harness import (  # noqa: E402
    _compile_iverilog,
    _run,
    mutate_sv,
    run_rtl_mutation,
    simulate_runs,
)
from torchsynth_voice import normalization_replay as nr  # noqa: E402
from torchsynth_voice import normalization_replay_golden as nrg  # noqa: E402
from torchsynth_voice.core_protocol import (  # noqa: E402
    CMD_RENDER_TRIGGER,
    ErrorCode,
)
from torchsynth_voice.fixed_voice import AcceptedFormats  # noqa: E402
from torchsynth_voice.fixedpoint import schedule as sched  # noqa: E402
from torchsynth_voice.fixedpoint.choices import ChoiceNotAccepted  # noqa: E402
from torchsynth_voice.fixedpoint.schedule import ScheduleNotAccepted  # noqa: E402
from torchsynth_voice.patch_control_model import (  # noqa: E402
    decode_rsp_frames,
)

_core = None


def bind_core(core) -> None:
    """Bind the ``run_tb`` module that owns the shared core symbols."""

    global _core
    _core = core


# ======================================================================
# Normalization replay flow (issue #77): the replay controller + the
# one-shot top's two-pass sequencer against the frozen model's own
# normalize_words(), with the issue #188 / #208 / #211 framing and
# binding scenarios.
# ======================================================================

NORMREPLAY_DUT_SV = TB_ROOT / "sv/normalization_replay_engine.sv"
NORMREPLAY_TB_SV = TB_ROOT / "sv/tb_normalization_replay_engine.sv"
#: The isolated case the AC2 back-to-back replay pair is demonstrated on
#: (a divide-branch case: replay must reproduce the gain-word computation
#: and the applied narrowing identically the second time, with no
#: per-sample intermediate retained across the pair).
NORMREPLAY_REPLAY_CASE = "fixed:above-one-min"
#: Mutation seams (anchored; mutate_sv refuses if the anchor moved).
NORMREPLAY_ALWAYS_ON_ANCHOR = "if (peak_word > UNITY_INT) begin"
NORMREPLAY_ALWAYS_ON_MUTANT = "if (1'b1) begin"
NORMREPLAY_ALWAYS_OFF_MUTANT = "if (1'b0) begin"
NORMREPLAY_WRONG_PEAK_ANCHOR = (
    "if (mix_mag > peak_word)\n"
    "                                    peak_word <= mix_mag;"
)
NORMREPLAY_WRONG_PEAK_MUTANT = (
    "if (1'b1)\n"
    "                                    peak_word <= mix_mag;"
)
NORMREPLAY_WRONG_RECIPROCAL_ANCHOR = (
    "gain_word         <= recip_quo[C9_WIDTH-1:0];"
)
NORMREPLAY_WRONG_RECIPROCAL_MUTANT = (
    "gain_word         <= recip_quo[C9_WIDTH-1:0] + 1'b1;"
)
NORMREPLAY_OFF_BY_ONE_ANCHOR = (
    "P_PASS1: begin\n"
    "                    if (!error) begin\n"
    "                        if (mix_valid) begin\n"
    "                            if (samples_this_pass == SCHED_SAMPLES_PER_PASS) begin"
)
NORMREPLAY_OFF_BY_ONE_MUTANT = (
    "P_PASS1: begin\n"
    "                    if (!error) begin\n"
    "                        if (mix_valid) begin\n"
    "                            if (samples_this_pass == SCHED_SAMPLES_PER_PASS - 18'd1) begin"
)
#: Cases the always-on/always-off/wrong-peak/wrong-reciprocal mutations are
#: demonstrated on (mirrors tests/test_normalization_replay.py's own
#: ForbiddenSubstitutesMustFail case choices where named there).
NORMREPLAY_BYPASS_MUTATION_CASE = "fixed:below-one-max"
NORMREPLAY_DIVIDE_MUTATION_CASE = "fixed:above-one-min"
#: The off-by-one framing mutation is demonstrated on the one directed case
#: whose unique peak sits at the very last sample (index 176,399) — the
#: natural vector for a pass-boundary-decided-one-sample-early fault.
NORMREPLAY_OFF_BY_ONE_CASE = "fixed:late-peak"
# Issue #188: the receiver's pass/digest binding rejection must discard the
# clip entire. The mutant ignores bind_reject.
NORMREPLAY_BIND_REJECT_ANCHOR = (
    "end else if (bind_reject && (pass_index == P_PASS1 || "
    "pass_index == P_PASS2)) begin"
)
NORMREPLAY_BIND_REJECT_MUTANT = "end else if (1'b0) begin"
NORMREPLAY_BIND_REJECT_CASE = "fixed:above-one-min"
NORMREPLAY_ERR_BINDING_REJECTED = 3
# Issue #208: a sticky ERR_SAMPLE_OVERRUN raised in P_PASS1 must never let
# pass_index reach P_PASS2, and one raised in P_PASS2 must never let done
# reach 1 — "further mix_valid/mix_done are ignored until rst" applies to
# the framing errors too, not just #188's bind_reject discard.
NORMREPLAY_ERR_SAMPLE_OVERRUN = 1
NORMREPLAY_STICKY_OVERRUN_CASE = "fixed:above-one-min"
NORMREPLAY_P_PASS1 = 1
NORMREPLAY_P_PASS2 = 2
# Issue #211: the structural top that actually wires the protocol receiver's
# clip-discard decision to the engine's bind_reject input, and its bench.
RENDER_BINDING_TOP_SV = TB_ROOT / "sv/render_binding_top.sv"
RENDER_BINDING_TB_SV = TB_ROOT / "sv/tb_render_binding.sv"
#: Mutation seam: the one connection this top exists to declare. Dropping it
#: leaves both modules individually correct and the discard unreachable.
RENDER_BINDING_WIRE_ANCHOR = ".bind_reject       (receiver_bind_reject),\n" \
    "        .mix_in            (mix_in),"
RENDER_BINDING_WIRE_MUTANT = ".bind_reject       (1'b0),\n" \
    "        .mix_in            (mix_in),"


def normreplay_wrong_reciprocal_clip() -> list:
    """A near-full-scale divide-branch clip for the wrong-reciprocal mutant.

    ``normalization_replay.directed_cases()``'s bodies stay three decades
    below unity (by the module's own design, so the inserted peak stays
    unique) — a 1-ULP U1.22 gain error moves the narrowed output of a tiny
    sample by an immeasurable fraction of an output LSB, so none of those
    committed cases can demonstrate this mutation. This synthetic clip
    (not part of the committed golden grid) instead holds every sample at
    magnitude ``peak_int - 1`` (alternating sign), so a 1-ULP gain error
    moves nearly every sample's narrowed output — the mutation this
    module's own S5 narrow site must never silently pass.
    """
    peak_int = nr.UNITY_INT + 1  # the smallest peak that divides
    body = peak_int - 1
    clip = [body if i % 2 == 0 else -body for i in range(nr.CLIP_SAMPLES)]
    clip[100] = peak_int
    return clip


def normreplay_hexword(value: int) -> str:
    """One C1_WIDTH (24-bit) two's-complement word, $readmemh-ready."""
    return "%06x" % (value & 0xFFFFFF)


def normreplay_write_run(
    workdir: Path, run: int, n1: int, n2: int, mix1, mix2, reject=None
) -> None:
    """One run's stimulus: pass-1/pass-2 declared counts + the two streams.

    ``reject`` is an optional ``(pass, sample)`` point at which the testbench
    pulses the engine's ``bind_reject`` (issue #188's pass/digest binding,
    spec/protocol/RENDER-TRIGGER.md) just before that sample is fed.
    """

    stim = "%d %d" % (n1, n2)
    if reject is not None:
        stim += " %d %d" % reject
    (workdir / ("run%d_stim.txt" % run)).write_text(
        stim + "\n", encoding="utf-8"
    )
    (workdir / ("run%d_mix1.hex" % run)).write_text(
        "\n".join(normreplay_hexword(v) for v in mix1) + "\n", encoding="utf-8"
    )
    (workdir / ("run%d_mix2.hex" % run)).write_text(
        "\n".join(normreplay_hexword(v) for v in mix2) + "\n", encoding="utf-8"
    )


def normreplay_simulate(workdir: Path, simulator: str, runs: int, dut_sv: Path):
    """Compile + run the normreplay TB; return (captures, statuses) per run."""

    return simulate_runs(
        workdir, simulator, runs, "normreplay.vvp",
        [_core.CONSTANTS_PKG_SV, dut_sv, NORMREPLAY_TB_SV],
    )


def normreplay_expected_status(diag: dict) -> list:
    """The clean-run status row the host mirror's diagnostics predict.

    Matches tb_normalization_replay_engine.sv's status line order: error,
    error_code, peak_word, gain_word, branch_normalized, done, pass_index
    (P_DONE == 3) — the prefix every clean run must share.
    """

    return [
        0, 0,
        diag["peak_word"],
        diag["gain_word"],
        1 if diag["normalized_branch"] else 0,
        1,
        3,
    ]


def normreplay_binding_reject(
    workdir: Path, simulator: str, formats, dut_sv: Path, *, report: bool
) -> bool:
    """Issue #188: a pass/digest binding rejection discards the clip entire.

    DR-0010 "Clip lifecycle": one trigger is bound to one sound identity +
    one noise-stream digest; a pass-2 mismatch discards the clip entire. The
    receiver (spec/protocol/RENDER-TRIGGER.md) compares the transport-
    declared digest at the pass-2 trigger and pulses ``bind_reject``. Each
    scenario runs in its own simulation because the error is sticky until
    ``rst``. Returns True only if every scenario behaves as specified.
    """

    clip = nr.directed_cases()[NORMREPLAY_BIND_REJECT_CASE]
    out, _diag = nrg.mirror_normalize(clip, formats)
    mid = 5
    # (label, reject point, expected capture, extra clean run after it)
    scenarios = [
        ("pass-2 boundary (declared-digest check)", (2, 0), [], True),
        ("mid pass 2 (noise-stream fault)", (2, mid), out[:mid], False),
        ("mid pass 1 (noise-stream fault)", (1, 3), [], False),
    ]
    all_ok = True
    for index, (label, point, want_capture, then_clean) in enumerate(scenarios):
        sdir = workdir / ("scenario%d" % index)
        sdir.mkdir(parents=True, exist_ok=True)
        normreplay_write_run(sdir, 0, len(clip), len(clip), clip, clip, point)
        runs = 1
        if then_clean:
            # A later clean trigger must NOT render while the reject is
            # sticky: the discard is never hidden by the next clip.
            normreplay_write_run(sdir, 1, len(clip), len(clip), clip, clip)
            runs = 2
        captures, statuses = normreplay_simulate(sdir, simulator, runs, dut_sv)
        # error, error_code, done, pass_index
        want_status = [1, NORMREPLAY_ERR_BINDING_REJECTED, 0, 0]
        got_status = [statuses[0][0], statuses[0][1], statuses[0][5], statuses[0][6]]
        ok = captures[0] == want_capture and got_status == want_status
        if then_clean:
            got_after = [statuses[1][0], statuses[1][1], statuses[1][5], statuses[1][6]]
            ok = ok and captures[1] == [] and got_after == want_status
        if report:
            print(
                "  binding reject @ %s: %d sample(s) released (expected %d), "
                "status (error, error_code, done, pass_index) %r (expected %r)%s -> %s"
                % (
                    label, len(captures[0]), len(want_capture), got_status,
                    want_status,
                    "; a following clean trigger renders nothing while the "
                    "reject is sticky" if then_clean else "",
                    "OK" if ok else "FAIL",
                )
            )
        all_ok = all_ok and ok
    return all_ok


def normreplay_sticky_overrun(
    workdir: Path, simulator: str, formats, dut_sv: Path, *, report: bool
) -> bool:
    """Issue #208: a sticky ERR_SAMPLE_OVERRUN must discard the clip entire.

    The module's own header ("Framing") documents "Once raised, further
    mix_valid/mix_done are ignored until rst" — a sticky error raised in
    P_PASS1 must never let ``pass_index`` reach P_PASS2, and one raised in
    P_PASS2 must never let ``done`` reach 1'b1 (DR-0010 "Clip lifecycle").
    This reproduces the issue's own exact repro (pass 1 fed
    SCHED_SAMPLES_PER_PASS + 1 words) plus the equivalent pass-2 case.
    Returns True only if both scenarios behave as specified.
    """

    clip = nr.directed_cases()[NORMREPLAY_STICKY_OVERRUN_CASE]
    all_ok = True

    # Pass-1 overrun-then-mix_done (the issue's own repro): feed one more
    # than the declared pass-1 sample count, then a normal-looking pass 2.
    # audio_out_valid is only ever asserted in P_PASS2, so if the sticky
    # error correctly keeps pass_index pinned at P_PASS1, zero samples can
    # ever be released and done must stay 0.
    p1dir = workdir / "pass1-overrun"
    p1dir.mkdir(parents=True, exist_ok=True)
    normreplay_write_run(p1dir, 0, len(clip) + 1, len(clip), clip + [0], clip)
    p1_captures, p1_statuses = normreplay_simulate(p1dir, simulator, 1, dut_sv)
    p1_got = p1_captures[0]
    p1_error, p1_error_code, _p1_peak, _p1_gain, _p1_branch, p1_done, p1_pass = (
        p1_statuses[0][:7]
    )
    pass1_ok = (
        p1_got == []
        and p1_error == 1
        and p1_error_code == NORMREPLAY_ERR_SAMPLE_OVERRUN
        and p1_done == 0
        and p1_pass == NORMREPLAY_P_PASS1
    )
    if report:
        print(
            "  pass-1 overrun-then-mix_done: %d sample(s) released "
            "(expected 0), status (error, error_code, done, pass_index) = "
            "(%d, %d, %d, %d) (expected (1, %d, 0, %d)) -> %s"
            % (
                len(p1_got), p1_error, p1_error_code, p1_done, p1_pass,
                NORMREPLAY_ERR_SAMPLE_OVERRUN, NORMREPLAY_P_PASS1,
                "OK" if pass1_ok else "FAIL",
            )
        )
    all_ok = all_ok and pass1_ok

    # Pass-2 overrun-then-mix_done (the same defect class): pass 1 completes
    # normally, then pass 2 is fed one more than the declared sample count.
    # The clean samples up to the overrun release exactly as a normal
    # render would; the sticky error raised on the extra sample must then
    # block the following mix_done from ever reaching P_DONE/done=1.
    want, _diag = nrg.mirror_normalize(clip, formats)
    p2dir = workdir / "pass2-overrun"
    p2dir.mkdir(parents=True, exist_ok=True)
    normreplay_write_run(p2dir, 0, len(clip), len(clip) + 1, clip, clip + [0])
    p2_captures, p2_statuses = normreplay_simulate(p2dir, simulator, 1, dut_sv)
    p2_got = p2_captures[0]
    p2_error, p2_error_code, _p2_peak, _p2_gain, _p2_branch, p2_done, p2_pass = (
        p2_statuses[0][:7]
    )
    pass2_ok = (
        p2_got == want
        and p2_error == 1
        and p2_error_code == NORMREPLAY_ERR_SAMPLE_OVERRUN
        and p2_done == 0
        and p2_pass == NORMREPLAY_P_PASS2
    )
    if report:
        print(
            "  pass-2 overrun-then-mix_done: %d sample(s) released "
            "(expected %d, the clean prefix only), status (error, "
            "error_code, done, pass_index) = (%d, %d, %d, %d) "
            "(expected (1, %d, 0, %d)) -> %s"
            % (
                len(p2_got), len(want), p2_error, p2_error_code, p2_done,
                p2_pass, NORMREPLAY_ERR_SAMPLE_OVERRUN, NORMREPLAY_P_PASS2,
                "OK" if pass2_ok else "FAIL",
            )
        )
    all_ok = all_ok and pass2_ok

    return all_ok


#: Per-pass noise-stream length the integration bench runs the receiver at.
#: A scale-down: the product 705,600-B clip is completed end-to-end by
#: tb/run_tb.py 'patch' (scenario s9), and the byte stream and the mix sample
#: stream are separate declared interfaces here (see render_binding_top.sv).
RENDER_BINDING_CLIP_BYTES = 2048


def render_binding_frames(model, words: dict, clip_bytes: int, *,
                          reject: bool) -> tuple:
    """(proto_a, proto_b) frame lists for one integration run.

    ``proto_a`` brings the receiver to "pass 1 complete": HELLO with
    CAP_RENDER, a full 78-name patch commit (the render gate), the pass-1
    RENDER_TRIGGER that binds (identity, declared digest), and that pass's
    whole NOISE_STREAM. ``proto_b`` is the pass-2 trigger, carrying either
    the bound digest or a different one; when it binds, pass 2's whole
    NOISE_STREAM follows so both runs end with the receiver back in
    ``ready`` — one because its clip completed, one because its clip was
    discarded entire.
    """
    seqs = SeqCounter()
    proto_a = [render_hello(model, CAPS_WITH_RENDER)]
    proto_a += render_full_tx(model, words, seqs, b"tx-bind-wire-01")
    proto_a.append(render_trigger_frame(seqs, 1, CORE_IDENTITY,
                                        RENDER_DIGEST_A))
    proto_a += noise_pass_frames(seqs, 1, clip_bytes)
    proto_b = [render_trigger_frame(
        seqs, 2, CORE_IDENTITY,
        RENDER_DIGEST_B if reject else RENDER_DIGEST_A)]
    if not reject:
        proto_b += noise_pass_frames(seqs, 2, clip_bytes)
    return proto_a, proto_b


def render_binding_integration(workdir: Path, simulator: str, formats,
                               top_sv: Path, *, report: bool) -> bool:
    """Issue #211: does a protocol rejection discard the clip in the engine?

    Drives render_binding_top (the receiver + the replay engine, wired) from
    real RENDER_TRIGGER / NOISE_STREAM frames. Run 0 declares a different
    noise-stream digest at the pass-2 trigger: the receiver must answer
    ERR_RENDER_BINDING (0x0E) and pulse ``bind_reject``, the engine must
    raise the sticky ERR_BINDING_REJECTED and release ZERO samples. Run 1
    declares the bound digest: the same wire must stay quiet and the clip
    must complete bit-exactly against the frozen model's normalization.
    """

    clip = nr.directed_cases()[NORMREPLAY_BIND_REJECT_CASE]
    expected_out, _diag = nrg.mirror_normalize(clip, formats)
    model = patch_build_model(RENDER_BINDING_CLIP_BYTES)
    words, _vector = patch_anchor_words()
    workdir.mkdir(parents=True, exist_ok=True)

    # (label, reject?, expected capture, expected trigger answer)
    runs = [
        ("pass-2 boundary digest mismatch", True, [], ErrorCode.RENDER_BINDING),
        ("pass-2 binding matches", False, list(expected_out), None),
    ]
    (workdir / "runs.txt").write_text("%d\n" % len(runs), encoding="utf-8")
    mix_hex = "\n".join(normreplay_hexword(v) for v in clip) + "\n"
    for index, (_label, reject, _want, _code) in enumerate(runs):
        proto_a, proto_b = render_binding_frames(
            model, words, RENDER_BINDING_CLIP_BYTES, reject=reject
        )
        write_schedule(workdir / ("run%d_proto_a.txt" % index),
                       schedule_of(paced_render(proto_a) + [300]))
        write_schedule(workdir / ("run%d_proto_b.txt" % index),
                       schedule_of(paced_render(proto_b) + [300]))
        (workdir / ("run%d_stim.txt" % index)).write_text(
            "%d\n" % len(clip), encoding="utf-8"
        )
        (workdir / ("run%d_mix.hex" % index)).write_text(
            mix_hex, encoding="utf-8"
        )

    vvp = _compile_iverilog(
        workdir, simulator, "render_binding.vvp",
        [_core.CONSTANTS_PKG_SV, PATCH_DUT_SV, NORMREPLAY_DUT_SV, top_sv,
         RENDER_BINDING_TB_SV],
        flags=[
            "-I", str(TB_ROOT / "sv"),
            "-Ptb_render_binding.NOISE_CLIP_BYTES=%d"
            % RENDER_BINDING_CLIP_BYTES,
        ],
    )
    _run(["vvp", "-n", str(vvp)], cwd=workdir)

    all_ok = True
    for index, (label, _reject, want_capture, want_code) in enumerate(runs):
        captured = [
            int(line)
            for line in (workdir / ("run%d_captured.txt" % index))
            .read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        status = [
            int(v)
            for v in (workdir / ("run%d_status.txt" % index))
            .read_text(encoding="utf-8").split()
        ]
        rsp = bytes(
            int(line)
            for line in (workdir / ("run%d_rsp.txt" % index))
            .read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
        (
            s_error, s_code, s_done, s_pass, s_pulses, s_max_run, s_session,
        ) = status
        try:
            answers = decode_rsp_frames(rsp)
        except AssertionError:
            answers = []
        trigger_answers = [f for f in answers if f[1] == CMD_RENDER_TRIGGER]
        if want_code is None:
            answer_ok = (
                len(trigger_answers) == 2
                and all(f[0] == 2 for f in trigger_answers)
            )
            engine_ok = (
                s_error == 0 and s_done == 1 and s_pass == 3
                and s_pulses == 0
            )
        else:
            answer_ok = (
                len(trigger_answers) == 2
                and trigger_answers[-1][0] == 3
                and trigger_answers[-1][3] == bytes([int(want_code)])
            )
            engine_ok = (
                s_error == 1
                and s_code == NORMREPLAY_ERR_BINDING_REJECTED
                and s_done == 0 and s_pass == 0 and s_pulses == 1
            )
        ok = (
            captured == want_capture and answer_ok and engine_ok
            and s_max_run <= 1 and s_session == 1
        )
        if report:
            print(
                "  binding wire [%s]: receiver answered %s, drove %d "
                "bind_reject pulse(s) (max run %d), engine (error, code, "
                "done, pass) %r, %d sample(s) released (expected %d), "
                "session %d -> %s"
                % (
                    label,
                    (trigger_answers[-1][3].hex() if trigger_answers and
                     trigger_answers[-1][0] == 3 else "RSP"),
                    s_pulses, s_max_run,
                    [s_error, s_code, s_done, s_pass],
                    len(captured), len(want_capture), s_session,
                    "OK" if ok else "FAIL",
                )
            )
        all_ok = all_ok and ok
    return all_ok


def normreplay(workdir: Path, simulator: str) -> int:
    """Issue #77 flow: the normalization replay controller + one-shot top's
    two-pass sequencer vs the frozen model's own ``normalize_words()``."""

    try:
        formats = AcceptedFormats()
    except ChoiceNotAccepted as error:
        print("NORMREPLAY REFUSED: accepted register refused: %s" % error)
        return 1

    ok = True

    # 1. The isolated directed Q2.21 case grid (issue #52's own boundary
    #    fixtures): below/at/above-one, peak tie, silence, extrema,
    #    late-peak, tied-max, plus the two release anchors — this is the
    #    grid that actually names "silence" and "extrema" (AC1).
    cases = []
    directed = nr.directed_cases()
    for label in sorted(directed):
        cases.append((label, directed[label]))
    anchored = nr.anchored_cases()
    for label in sorted(anchored):
        cases.append((label, anchored[label][0]))

    # 2. Full-voice normalization-family cases (fixture mapping source 1):
    #    regenerated through the composed fixed model and bound against the
    #    frozen fixed-voice-golden-v1 receipt's own per-trace digests before
    #    use as this module's own declared RTL input interface
    #    (mixer.pre_normalization) — no dependency on issue #76's RTL.
    receipt = json.loads(_core.NOISE_RECEIPT_PATH.read_bytes())
    receipt_cases = {c["id"]: c for c in receipt["cases"]}
    fixture_ok = True
    for case_id in nrg.FULL_VOICE_CASES:
        fixed, _diag = nrg.resolve_full_voice_case(case_id, formats)
        want = receipt_cases[case_id]
        for trace in (
            "mixer.pre_normalization", "mixer.peak", "mixer.gain", "mixer.output",
        ):
            if nrg.digest_words(fixed[trace]) != want["traces"][trace]:
                print(
                    "NORMREPLAY FAILED: %s regenerated %s diverges from the "
                    "frozen fixed-voice-golden-v1 receipt" % (case_id, trace)
                )
                fixture_ok = False
        cases.append((case_id, fixed["mixer.pre_normalization"]))
    print(
        "fixture regeneration: %d isolated boundary cases + %d full-voice "
        "cases, every full-voice trace digest bound to the frozen receipt "
        "-> %s" % (len(directed) + len(anchored), len(nrg.FULL_VOICE_CASES),
                   "OK" if fixture_ok else "FAIL")
    )
    ok = ok and fixture_ok

    # AC2: back-to-back replay pair — the same case rendered twice with no
    # reset in between must reproduce its own capture byte-for-byte (no
    # per-sample intermediate survives across passes OR across triggers).
    replay_clip = directed[NORMREPLAY_REPLAY_CASE]
    cases.append((NORMREPLAY_REPLAY_CASE + "[replay-a]", replay_clip))
    cases.append((NORMREPLAY_REPLAY_CASE + "[replay-b]", replay_clip))

    # 3. Host mirror expectations (normalize_words() itself, no
    #    reimplementation) + the committed-case RTL run.
    case_dir = workdir / "committed"
    case_dir.mkdir(parents=True, exist_ok=True)
    expected = {}
    for index, (label, mix_words) in enumerate(cases):
        out, diag = nrg.mirror_normalize(mix_words, formats)
        expected[label] = (out, diag)
        normreplay_write_run(
            case_dir, index, len(mix_words), len(mix_words), mix_words, mix_words
        )
    captures, statuses = normreplay_simulate(
        case_dir, simulator, len(cases), NORMREPLAY_DUT_SV
    )

    bitexact_ok = True
    replay_positions = {}
    for index, (label, mix_words) in enumerate(cases):
        out, diag = expected[label]
        captured = captures[index]
        status = statuses[index]
        if label.endswith("[replay-a]") or label.endswith("[replay-b]"):
            replay_positions[label] = index
        if captured != out:
            print("NORMREPLAY FAILED: %s capture != host mirror output:" % label)
            for i, (want, got) in enumerate(zip(out, captured)):
                if want != got:
                    print(
                        "  first mismatch at sample %d: expected %d, got %d"
                        % (i, want, got)
                    )
                    break
            else:
                print(
                    "  length mismatch: expected %d samples, got %d"
                    % (len(out), len(captured))
                )
            bitexact_ok = False
            continue
        want_status = normreplay_expected_status(diag)
        if status[:7] != want_status:
            print(
                "NORMREPLAY FAILED: %s status %r != expected %r "
                "(error, error_code, peak_word, gain_word, "
                "branch_normalized, done, pass_index)"
                % (label, status[:7], want_status)
            )
            bitexact_ok = False
    print(
        "RTL bit-exactness: %d cases (isolated + full-voice) match the "
        "host mirror sample-exactly, status registers clean -> %s"
        % (len(cases), "OK" if bitexact_ok else "FAIL")
    )
    ok = ok and bitexact_ok

    replay_ok = (
        captures[replay_positions[NORMREPLAY_REPLAY_CASE + "[replay-a]"]]
        == captures[replay_positions[NORMREPLAY_REPLAY_CASE + "[replay-b]"]]
        == expected[NORMREPLAY_REPLAY_CASE + "[replay-a]"][0]
    )
    print(
        "replay/reset (AC2): %r rendered twice back-to-back (no reset "
        "between triggers) reproduces its own capture byte-for-byte -> %s"
        % (NORMREPLAY_REPLAY_CASE, "OK" if replay_ok else "FAIL")
    )
    ok = ok and replay_ok

    # 4. DR-0010 #77 owner row (op counts, reported) + AC3 framing +
    #    schedule-constants emission check, from a dedicated single-render
    #    run so "since rst" counters equal exactly this one clip's counts.
    try:
        schedule = sched.require_accepted_schedule()
    except ScheduleNotAccepted as error:
        print("NORMREPLAY REFUSED: DR-0010 schedule register refused: %s" % error)
        return 1
    budget_dir = workdir / "budget"
    budget_dir.mkdir(parents=True, exist_ok=True)
    budget_clip = nr.directed_cases()["fixed:extremum"]  # divide branch
    normreplay_write_run(
        budget_dir, 0, len(budget_clip), len(budget_clip), budget_clip, budget_clip
    )
    _, budget_statuses = normreplay_simulate(
        budget_dir, simulator, 1, NORMREPLAY_DUT_SV
    )
    (
        b_error, b_error_code, b_peak, b_gain, b_branch, b_done, b_pass,
        b_compares, b_selects, b_recip_divs, b_mults, b_narrows, b_sats,
    ) = budget_statuses[0]

    def sc(name: str):
        return sched.constant(schedule, name)

    samples_per_pass = sc("samples_per_pass")
    passes_per_clip = sc("passes_per_clip")
    clip_sample_slots = sc("clip_sample_slots")
    pass2_folded_cycles_max = sc("pass2_folded_cycles_max")

    owner_ok = (
        b_error == 0 and b_branch == 1
        and b_compares == samples_per_pass
        and b_selects == samples_per_pass
        and b_recip_divs == 1
        and b_mults == samples_per_pass
        and b_narrows == samples_per_pass
    )
    per_clip = {
        "pass-1 compares (1/sample, folded into the mixer output)": (
            b_compares, samples_per_pass,
        ),
        "pass-1 abs-selects (1/sample, folded into the mixer output)": (
            b_selects, samples_per_pass,
        ),
        "per-clip reciprocal divisions (declared extra, 0 or 1)": (
            b_recip_divs, None,
        ),
        "pass-2 gain multiplies (normalized branch, 1/sample)": (
            b_mults, samples_per_pass,
        ),
        "pass-2 declared narrowings (normalized branch, 1/sample)": (
            b_narrows, samples_per_pass,
        ),
    }
    for name, (actual, limit) in per_clip.items():
        if limit is None:
            print("  budget: %d %s -> n/a (reported)" % (actual, name))
            continue
        verdict = "OK" if actual == limit else "FAIL"
        owner_ok = owner_ok and actual == limit
        print(
            "  budget: %d %s vs DR-0010 #77 owner-row count %d -> %s"
            % (actual, name, limit, verdict)
        )
    slots_consistent = clip_sample_slots == passes_per_clip * samples_per_pass
    print(
        "  budget: passes_per_clip=%d, samples_per_pass=%d, "
        "clip_sample_slots=%d (== passes x samples_per_pass) -> %s"
        % (
            passes_per_clip, samples_per_pass, clip_sample_slots,
            "OK" if (passes_per_clip == 2 and slots_consistent) else "FAIL",
        )
    )
    owner_ok = owner_ok and passes_per_clip == 2 and slots_consistent
    print(
        "  budget: the once-per-clip reciprocal division and the pass-1/"
        "pass-2 transition are combinational (one clock edge) -> within "
        "pass2_folded_cycles_max=%d headroom (PARTIAL-DUT measurement, no "
        "schedule-conformance or PPA/fit claim)" % pass2_folded_cycles_max
    )
    try:
        matches = _core._emitted_constants_match(schedule)
        owner_ok = owner_ok and matches
    except Exception as error:  # noqa: BLE001 - reported, never a silent pass
        print("  budget constants: emission failed -> %s" % error)
        return 1
    ok = ok and owner_ok

    # 5. Mutations (AC6): every planted fault MUST be detected.
    mutations_ok = True

    def run_mutation(label, anchor, replacement, case_id, clip=None):
        if clip is None:
            clip = nr.directed_cases()[case_id]

        def write_mutant(mut_dir):
            mutated_sv = mut_dir / "normalization_replay_engine_mut.sv"
            mutated_sv.write_text(
                mutate_sv(
                    NORMREPLAY_DUT_SV.read_text(encoding="utf-8"),
                    anchor, replacement, label,
                ),
                encoding="utf-8",
            )

        def write_case(mut_dir):
            normreplay_write_run(mut_dir, 0, len(clip), len(clip), clip, clip)

        def simulate(mut_dir):
            mutated_sv = mut_dir / "normalization_replay_engine_mut.sv"
            mut_captures, mut_statuses = normreplay_simulate(
                mut_dir, simulator, 1, mutated_sv
            )
            return mut_captures[0], mut_statuses[0]

        def is_detected(capture):
            mut_out, mut_status = capture
            pristine_out, _pristine_diag = nrg.mirror_normalize(clip, formats)
            detected = (
                mut_out != pristine_out
                or mut_status[0] != 0  # a sticky error is also a divergence
            )
            return detected, ""

        return run_rtl_mutation(
            workdir, label, case_id, write_mutant, write_case, simulate,
            is_detected,
            detected_message=(
                "DETECTED (diverges from the pristine golden capture)"
            ),
        )

    mutations_ok &= run_mutation(
        "always-on", NORMREPLAY_ALWAYS_ON_ANCHOR, NORMREPLAY_ALWAYS_ON_MUTANT,
        NORMREPLAY_BYPASS_MUTATION_CASE,
    )
    mutations_ok &= run_mutation(
        "always-off", NORMREPLAY_ALWAYS_ON_ANCHOR, NORMREPLAY_ALWAYS_OFF_MUTANT,
        NORMREPLAY_DIVIDE_MUTATION_CASE,
    )
    mutations_ok &= run_mutation(
        "wrong-peak", NORMREPLAY_WRONG_PEAK_ANCHOR, NORMREPLAY_WRONG_PEAK_MUTANT,
        NORMREPLAY_DIVIDE_MUTATION_CASE,
    )
    mutations_ok &= run_mutation(
        "wrong-reciprocal", NORMREPLAY_WRONG_RECIPROCAL_ANCHOR,
        NORMREPLAY_WRONG_RECIPROCAL_MUTANT, "synthetic:near-full-scale",
        clip=normreplay_wrong_reciprocal_clip(),
    )
    mutations_ok &= run_mutation(
        "off-by-one", NORMREPLAY_OFF_BY_ONE_ANCHOR, NORMREPLAY_OFF_BY_ONE_MUTANT,
        NORMREPLAY_OFF_BY_ONE_CASE,
    )
    ok = ok and mutations_ok

    # 5b. Issue #208: a sticky ERR_SAMPLE_OVERRUN raised in P_PASS1 or
    #     P_PASS2 must discard the clip entire, exactly as the module's own
    #     "Framing" contract and DR-0010 "Clip lifecycle" require.
    print("sticky overrun discard (issue #208, DR-0010 Clip lifecycle):")
    sticky_overrun_ok = normreplay_sticky_overrun(
        workdir / "sticky-overrun", simulator, formats, NORMREPLAY_DUT_SV,
        report=True,
    )
    print("sticky overrun discard -> %s" % ("OK" if sticky_overrun_ok else "FAIL"))
    ok = ok and sticky_overrun_ok

    # 6. Pass/digest binding (issue #188, DR-0010 "Clip lifecycle"): the
    #    receiver's bind_reject discards the clip entire, and a mutant that
    #    ignores it must be caught by the same scenarios.
    print("binding rejection (issue #188, spec/protocol/RENDER-TRIGGER.md):")
    reject_ok = normreplay_binding_reject(
        workdir / "bind-reject", simulator, formats, NORMREPLAY_DUT_SV,
        report=True,
    )
    print("binding rejection -> %s" % ("OK" if reject_ok else "FAIL"))
    ok = ok and reject_ok
    mut_dir = workdir / "mut-ignore-bind-reject"
    mut_dir.mkdir(parents=True, exist_ok=True)
    mutated_sv = mut_dir / "normalization_replay_engine_mut.sv"
    mutated_sv.write_text(
        mutate_sv(
            NORMREPLAY_DUT_SV.read_text(encoding="utf-8"),
            NORMREPLAY_BIND_REJECT_ANCHOR, NORMREPLAY_BIND_REJECT_MUTANT,
            "ignore-bind-reject",
        ),
        encoding="utf-8",
    )
    reject_detected = not normreplay_binding_reject(
        mut_dir, simulator, formats, mutated_sv, report=False
    )
    print(
        "mutation ignore-bind-reject (RTL, case %s): %s"
        % (NORMREPLAY_BIND_REJECT_CASE,
           "DETECTED (binding-rejection scenarios fail)" if reject_detected
           else "NOT DETECTED")
    )
    ok = ok and reject_detected

    # 7. The wire itself (issue #211): the protocol receiver
    #    (tb/sv/patch_control.sv) driving this engine's bind_reject through
    #    tb/sv/render_binding_top.sv, from real RENDER_TRIGGER /
    #    NOISE_STREAM frames rather than a testbench-synthesised pulse.
    print(
        "binding wire (issue #211, patch_control -> "
        "normalization_replay_engine.bind_reject):"
    )
    wire_ok = render_binding_integration(
        workdir / "bind-wire", simulator, formats, RENDER_BINDING_TOP_SV,
        report=True,
    )
    print("binding wire -> %s" % ("OK" if wire_ok else "FAIL"))
    ok = ok and wire_ok
    wire_mut_dir = workdir / "mut-drop-bind-wire"
    wire_mut_dir.mkdir(parents=True, exist_ok=True)
    mutated_top = wire_mut_dir / "render_binding_top_mut.sv"
    mutated_top.write_text(
        mutate_sv(
            RENDER_BINDING_TOP_SV.read_text(encoding="utf-8"),
            RENDER_BINDING_WIRE_ANCHOR, RENDER_BINDING_WIRE_MUTANT,
            "drop-bind-wire",
        ),
        encoding="utf-8",
    )
    wire_detected = not render_binding_integration(
        wire_mut_dir, simulator, formats, mutated_top, report=False
    )
    print(
        "mutation drop-bind-wire (integration top): %s"
        % ("DETECTED (the receiver's discard no longer reaches the engine)"
           if wire_detected else "NOT DETECTED")
    )
    ok = ok and wire_detected

    if not ok:
        print("NORMREPLAY RUN FAILED")
        return 1
    print(
        "NORMREPLAY RUN PASSED (%d isolated + full-voice cases bit-exact "
        "+ replay pair bit-exact + owner-row/op-count asserts + sticky "
        "overrun discards the clip entire + binding rejection discards the "
        "clip entire + the patch_control -> bind_reject wire proven "
        "end-to-end + all 7 mutations detected)"
        % len(cases)
    )
    return 0
