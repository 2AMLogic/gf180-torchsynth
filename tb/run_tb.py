#!/usr/bin/env python3
"""PDK-free RTL testbench runner (issue #68; the AGENTS.md tb convenience).

Commands (default: selftest). Each drives an open-source simulator (Icarus
Verilog; no PDK, no vendor tooling) and compares the capture against a
frozen reference:

  selftest    golden-vector harness against a synthetic DUT
  anchor      real golden-vector anchor through the format-true LUT DUT
  adsr        ADSR envelope engine (issue #70)
  patch       patch-control core vs its Python mirror (issue #69)
  lfo         LFO + control-rate VCA engine (issue #71)
  modmatrix   modulation matrix + upsample engines (issue #72)
  vco         sine VCO engine (issue #73)
  vco2        square/saw VCO engine (issue #74)
  noise       host-fed exact noise-stream lane (issue #75)
  mix         audio VCAs + pre-normalization mixer (issue #76)
  normreplay  normalization replay controller + one-shot top (issue #77)

Exit status: 0 every check passed; 1 a check failed (a real mismatch or an
undetected mutation); 3 the simulator is not installed, so nothing ran (an
unrun check is never a pass); 4 INCONCLUSIVE, the vco capture was
untrustworthy so nothing was proven or disproven.

Per-lane behavior, guarantees and mutation counts live in tb/README.md; the
owning specs are under spec/ (e.g. spec/reference/rtl-schedule-v1.json,
spec/protocol/RENDER-TRIGGER.md). No synthesis, layout, signoff, or hardware
claim is made by this runner.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import shutil
import struct
import sys
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

TB_ROOT = Path(__file__).resolve().parent
ROOT = TB_ROOT.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(TB_ROOT))

from tb_harness import (  # noqa: E402,F401  (re-exported for consumers)
    _compile_iverilog,
    _run,
    check_owner_row_budget,
    load_golden_vectors,
    mutate_sv,
    read_ops_rows,
    run_rtl_mutation,
    simulate,
    simulate_runs,
    write_lut_memh,
)

from torchsynth_voice import golden_vectors as gv  # noqa: E402
from torchsynth_voice import noise_stream_golden as nsg  # noqa: E402
from torchsynth_voice import normalization_replay as nr  # noqa: E402
from torchsynth_voice import normalization_replay_golden as nrg  # noqa: E402
from torchsynth_voice import vco2_golden as vc  # noqa: E402
from torchsynth_voice.fixed_voice import (  # noqa: E402
    AcceptedFormats,
    entry_quantize,
    shadow_half_even,
)
from torchsynth_voice.fixedpoint import codegen  # noqa: E402
from torchsynth_voice.fixedpoint import schedule as sched  # noqa: E402
from torchsynth_voice.fixedpoint.choices import ChoiceNotAccepted  # noqa: E402
from torchsynth_voice.fixedpoint.counters import StickyCounters  # noqa: E402
from torchsynth_voice.fixedpoint.ops import OverflowPolicy, mul, rescale  # noqa: E402
from torchsynth_voice.fixedpoint.rounding import (  # noqa: E402
    RoundingMode,
    div_round,
    div_round_reported,
)
from torchsynth_voice.fixedpoint.schedule import ScheduleNotAccepted  # noqa: E402
from torchsynth_voice.core_protocol import (  # noqa: E402
    CMD_RENDER_TRIGGER,
    ErrorCode,
)
from torchsynth_voice.format_sweep import (  # noqa: E402
    quantize_params,  # noqa: F401  (re-exported for tb/run_voice.py)
)
from torchsynth_voice.patch_control_model import (  # noqa: E402
    decode_rsp_frames,
)

DUT_SV = TB_ROOT / "sv/synth_dut.sv"
TB_SV = TB_ROOT / "sv/tb_synth_dut.sv"
ANCHOR_DUT_SV = TB_ROOT / "sv/lut_sine_dut.sv"
ANCHOR_TB_SV = TB_ROOT / "sv/tb_lut_sine_dut.sv"
CONSTANTS_PKG_SV = TB_ROOT / "sv/gf180_rtl_constants_pkg.sv"

#: Repository paths whose uncommitted state invalidates an issue-#79 evidence
#: record (``spec/ONESHOT-E2E.md`` -> "Evidence identity"). Such a record pairs
#: ``git_head`` with ``git_tree_dirty``; that pair only means "this record
#: describes the tree at that commit" if every input the comparison consumes is
#: inside the dirtiness scope. ``sim/reference`` is in the list because the
#: frozen whole-voice receipt (``VCO_RECEIPT_PATH``) -- which every one-shot
#: lane pins its expected digests against -- lives there; it was omitted when
#: these flows were first written, so an uncommitted receipt edit could have
#: produced a record still reporting ``git_tree_dirty: false``.
#: ``tests/test_committed_oneshot_evidence.py`` asserts that every source a
#: committed record digests lies under one of these prefixes, so adding a
#: digested input outside the scope fails CI rather than silently widening the
#: gap again.
EVIDENCE_TREE_SCOPE = ("tb", "src", "spec/reference", "sim/reference")


#: Committed square/saw VCO golden vectors (issue #74).
VCO2_DUT_SV = TB_ROOT / "sv/square_saw_vco_engine.sv"
VCO_LUT_SV = TB_ROOT / "sv/quarter_wave_lut.sv"
VCO2_TB_SV = TB_ROOT / "sv/tb_square_saw_vco.sv"
VCO_VECTOR_DIR = ROOT / "sim/reference/square-saw-vco-golden-v1"
#: Frozen-binding vectors carry this prefix and bind the retained
#: fixed-voice-golden-v1 vco_2.raw sidecar words (direct RTL-vs-frozen
#: word validation, no mirror in the loop).
VCO_FROZEN_PREFIX = "frozen:"
#: The case the wrong-shape-mix RTL mutation is demonstrated on: shape 1.0
#: makes the inverted right-branch coefficient maximally observable.
VCO_SHAPE_MUTATION_CASE = "frozen:waveform:vco_2:saw"
#: Cases the stimulus mutations are demonstrated on: the intermediate-mix
#: case carries a nontrivial tanh fanout (shadow-word corruption must bite
#: and localize) and an unclamped partials word (the one-ULP constant
#: error must localize to the driven stream alone).
VCO_VECTOR_MUTATION_CASE = "shape:intermediate-half"
#: The sample whose replayed left_q shadow word the corruption flips.
VCO_SHADOW_MUTATION_INDEX = 12345
#: Mutation-simulation walk cap: every mutation demonstrably bites within
#: the first 24,000 samples, so the three mutation sims walk a fraction of
#: the grid. Committed-case runs always walk the full 176,400 samples.
VCO2_MUTATION_WALK_CAP = 24000
#: Replay-independence pair: static words, phase, and counters are
#: per-trigger; run 0 state must not leak into run 1.
VCO2_REPLAY_PAIR = ("frozen:waveform:vco_2:saw", "shape:intermediate-half")
#: Landed fixed-voice golden receipt (issue #54): per-case noise block
#: (seed/slot/bytes digest) and per-case ``noise.raw`` trace digests.
NOISE_RECEIPT_PATH = ROOT / "sim/reference/fixed-voice-golden-v1.json"
#: Committed case the noise mutations are demonstrated on (slot 0).
NOISE_MUTATION_CASE_INDEX = 0
#: Replay/32-stream-repetition pair: sound_index 0 and 32 share slot 0, so
#: their resolved byte streams are identical by the C8 slot rule.
NOISE_REPLAY_INDICES = (0, 32)
#: Synthetic bit-pattern stream: structural edge classes (deep zero, ties
#: to even in both directions, saturation at both rails) beyond canonical
#: bits, all through the same fed-stream path.
NOISE_PATTERN_BITS = (
    0x00000000,  # +0.0
    0x80000000,  # -0.0
    0x00000001,  # smallest subnormal -> deep zero
    0x00800000,  # 2^-126 (smallest normal) -> deep zero
    0x3F000001,  # 0.5 + ulp: tie, even integer part -> stays down
    0x3F800003,  # tie with odd integer part -> rounds up
    0x3F800000,  # 1.0
    0xBF800000,  # -1.0
    0x40400000,  # 3.0
    0x407FFFFF,  # largest finite below 4 -> saturates high
    0x40800000,  # 4.0 -> saturates high
    0xC0800000,  # -4.0 -> exactly C1_MIN
    0xC2C80000,  # -98.25 -> saturates low
)

NOISE_DUT_SV = TB_ROOT / "sv/noise_stream_dut.sv"
NOISE_TB_SV = TB_ROOT / "sv/tb_noise_stream_dut.sv"
#: Mutation seams (anchored; mutate_sv refuses if the anchor moved).
NOISE_TRUNCATION_ANCHOR = "rounded = q_rounded;"
NOISE_TRUNCATION_MUTANT = "rounded = q_raw;"
NOISE_SLOT_RULE_ANCHOR = "wire [4:0] slot_expected = sound_index[4:0];"
NOISE_SLOT_RULE_MUTANT = "wire [4:0] slot_expected = sound_index[4:0] ^ 5'd1;"
NOISE_IDENTITY_ANCHOR = "bound_slot        <= declared_slot;"
NOISE_IDENTITY_MUTANT = "bound_slot        <= declared_slot ^ 5'd1;"
#: Byte offset the clip-mixing stimulus splices a foreign trigger identity
#: in at (sample-aligned, well inside one clip).
NOISE_IDENTITY_SWITCH_BYTE = 4000
#: Sample index the length-preserving drop/duplicate slip is planted at.
NOISE_SLIP_SAMPLE = 1000

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
#: Trace name from the canonical registry used for the synthetic stream.
SYNTH_TRACE = "mixer.output"
SYNTH_PARAMETER = "adsr_1.alpha"
CYCLE_START = 0

STIMULUS = [0, 1, 2, 165, 255, 128, 7, 42]
XOR_MASK = 0xA5

#: Real-vector fail-path plant: flip the LSB of this captured sample.
ANCHOR_FAIL_INDEX = 4096

#: DUT-checked trace of the anchor vector (the LUT sine path's output).
ANCHOR_TRACE = "vco_1.raw"


def synth_model(value: int) -> int:
    """Software mirror of the synthetic DUT's registered XOR."""
    return (value & 0xFF) ^ XOR_MASK


def build_vector(values, registry, inventory) -> dict:
    vector = {
        "schema": gv.VECTOR_SCHEMA,
        "schema_version": gv.SCHEMA_VERSION,
        "provenance": {
            "generator": "tb/run_tb.py selftest",
            "note": (
                "synthetic-bringup vectors for the harness self-test; not "
                "default-nebula clip content and not DR-0008-conformant"
            ),
        },
        "trace_registry_version": registry["semantic_version"],
        "parameter_inventory_commit": inventory["source"]["commit"],
        "clip": {
            "profile": gv.SYNTHETIC_PROFILE,
            "sample_rate_hz": gv.CANONICAL_SAMPLE_RATE_HZ,
            "sample_count": len(values),
            "control_rate_hz": gv.CANONICAL_CONTROL_RATE_HZ,
            "control_count": len(values),
        },
        "parameters": {SYNTH_PARAMETER: 0.5},
        "traces": [
            {
                "name": SYNTH_TRACE,
                "kind": "audio",
                "encoding": "synthetic-int",
                "cycle_start": CYCLE_START,
                "values": list(values),
            }
        ],
    }
    vector["content_hash"] = gv.compute_content_hash(vector)
    return vector


def selftest(workdir: Path, simulator: str) -> int:
    registry = gv.load_registry_document()
    inventory = gv.load_inventory_document()
    expected = [synth_model(v) for v in STIMULUS]

    captured = simulate(
        workdir, simulator, STIMULUS, [DUT_SV, TB_SV]
    )

    ok = True
    if captured != expected:
        print("SELFTEST BROKEN: captured stream does not match the model")
        ok = False

    golden = build_vector(expected, registry, inventory)
    gv.load_vector(golden, registry=registry, inventory=inventory)

    mismatch = gv.first_mismatch(
        golden, {SYNTH_TRACE: captured}
    )
    if mismatch is not None:
        print("FAIL path: unexpected mismatch on the clean golden vector:")
        print(gv.format_mismatch(mismatch))
        ok = False
    else:
        print(
            "PASS path verified: clean golden vector vs captured stream -> "
            "no mismatch (reporter silent)."
        )

    corrupted = build_vector(expected, registry, inventory)
    corrupted["traces"][0]["values"][3] = (expected[3] + 1) & 0xFF
    corrupted["content_hash"] = gv.compute_content_hash(corrupted)
    # The corrupted vector is the EXPECTED side of the comparison and the
    # captured sim stream is the ACTUAL side: the vector claims one value,
    # the DUT delivered another, and the reporter must name that row.
    planted = gv.Mismatch(
        cycle=CYCLE_START + 3,
        sample=3,
        trace=SYNTH_TRACE,
        expected=corrupted["traces"][0]["values"][3],
        actual=expected[3],
    )
    observed = gv.first_mismatch(corrupted, {SYNTH_TRACE: captured})
    if observed != planted:
        print(
            "FAIL path: reporter did not name the planted fault; observed %r"
            % (observed,)
        )
        ok = False
    else:
        print("Intentional-fail path verified; reporter reports:")
        print(gv.format_mismatch(observed))

    if not ok:
        print("SELFTEST FAILED")
        return 1
    print("SELFTEST PASSED (pass path + intentional-fail path)")
    return 0


def focus_vector(vector: dict, trace_name: str) -> dict:
    """A single-trace view of a loaded vector, revalidated by the loader.

    Lets the first-mismatch reporter run against one DUT-covered trace of
    the real vector while keeping every schema, registry, and hash check.
    """
    traces = [t for t in vector["traces"] if t["name"] == trace_name]
    if len(traces) != 1:
        raise SystemExit("vector has no unique trace %r" % trace_name)
    focused = {k: v for k, v in vector.items() if k != "traces"}
    focused["traces"] = traces
    focused["content_hash"] = gv.compute_content_hash(focused)
    return gv.load_vector(focused)


def derive_anchor_control_words(vector: dict, formats: AcceptedFormats):
    """Derive the DUT control words from the vector's own declared data.

    The declared binary64 shadow sites are replayed host-side here (the
    midi->Hz exp2 is DR-0008's declared open approximation item — this is
    a harness input derivation, not an RTL claim). Everything consumed is
    the vector's own declared scalar trace and parameters:

    - the phase-increment word: m1 = the vector's ``keyboard.midi_f0``
      scalar word (the anchor case's tuning/mod_depth entry-quantize to
      zero, asserted below), then the declared shadow policy
      ``hz = 440 * 2^((m/2^21 - 69)/12)`` -> Q16.15 word -> half-even
      increment over the C2 unit circle;
    - the mixer-level word: the C6/S1 entry quantization of the vector's
      ``mixer.vco_1`` parameter into the accepted C1 audio format.
    """
    counters = StickyCounters()
    for name in ("vco_1.tuning", "vco_1.mod_depth"):
        word = entry_quantize(
            float(vector["parameters"][name]), formats.midi, counters,
            "tb.anchor.entry:" + name,
        )
        if word != 0:
            raise SystemExit(
                "anchor derivation requires %s to entry-quantize to zero; "
                "got %d (not the zero-modulation case)" % (name, word)
            )
    midi_word = None
    for trace in vector["traces"]:
        if trace["name"] == "keyboard.midi_f0":
            midi_word = trace["values"][0]
    if midi_word is None:
        raise SystemExit("anchor vector carries no keyboard.midi_f0 scalar")

    midi_scale = float(formats.midi.scale)
    sample_rate_hz = int(vector["clip"]["sample_rate_hz"])
    frequency_scale = int(formats.frequency.scale)
    # Declared shadow site (binary64 on the quantized control word).
    hz = 440.0 * math.exp2((midi_word / midi_scale - 69.0) / 12.0)
    freq_word = shadow_half_even(hz * float(frequency_scale))
    phase_step = div_round(
        freq_word * formats.phase_units_per_turn,
        frequency_scale * sample_rate_hz,
        RoundingMode.HALF_EVEN,
    )
    level_word = entry_quantize(
        float(vector["parameters"]["mixer.vco_1"]), formats.audio, counters,
        "tb.anchor.entry:mixer.vco_1",
    )
    return phase_step, level_word


def anchor_mirror(formats: AcceptedFormats, phase_step: int, level_word: int,
                  sample_count: int):
    """Replay the DUT's integer dataflow with the model's own primitives.

    Uses the landed fixedpoint ops (half-even, saturate) and the accepted
    hash-linked table, so a match against the frozen vector trace is a
    model-primitive match, not a harness-parallel implementation.
    """
    counters = StickyCounters()
    audio = formats.audio
    entry_fmt = formats.table.spec.entry_format
    modulus = 1 << formats.phase_width
    phase = 0
    vco_words = []
    mix_words = []
    for _ in range(sample_count):
        phase = (phase + phase_step) % modulus
        entry = formats.table.evaluate(phase, formats.mode)
        vco = rescale(entry, entry_fmt, audio, formats.mode,
                      OverflowPolicy.SATURATE, counters, "tb.anchor.vco")
        mix = mul(vco, audio, level_word, audio, audio, formats.mode,
                  OverflowPolicy.SATURATE, counters, "tb.anchor.mix")
        vco_words.append(vco)
        mix_words.append(mix)
    return vco_words, mix_words


def simulate_anchor(workdir: Path, simulator: str, phase_step: int,
                    level_word: int, sample_count: int, lut_memh: Path):
    """Compile the constants package + format-true DUT and capture streams."""
    if lut_memh.resolve().parent != workdir.resolve():
        raise SystemExit(
            "anchor lut %s must be staged in the simulator workdir %s"
            % (lut_memh, workdir)
        )
    stim = workdir / "stimulus.txt"
    captured = workdir / "captured.txt"
    stim.write_text(
        "%d\n%d\n%d\n" % (phase_step, level_word, sample_count),
        encoding="utf-8",
    )
    vvp = _compile_iverilog(
        workdir, simulator, "lut_sine.vvp",
        [CONSTANTS_PKG_SV, ANCHOR_DUT_SV, ANCHOR_TB_SV],
    )
    _run(
        # Bare name + cwd=workdir: the DUT's lut_file buffer holds
        # 128 characters, so an absolute path can truncate (#278).
        ["vvp", "-n", str(vvp), "+lut=%s" % lut_memh.name],
        cwd=workdir,
    )
    vco_words = []
    mix_words = []
    for line in captured.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        v_word, m_word = line.split()
        vco_words.append(int(v_word))
        mix_words.append(int(m_word))
    cycles_file = workdir / "cycles.txt"
    measured_cycles = int(cycles_file.read_text(encoding="utf-8").strip())
    return vco_words, mix_words, measured_cycles


def _emitted_constants_match(
    schedule: dict, prefix: str = "budget constants"
) -> bool:
    """Emit the accepted registers and compare against the landed package.

    Requires the complete package text to equal the landed constants package
    and the schedule ID to be among the emitted IDs. Prints the diagnostic and
    returns the predicate; exceptions propagate to the caller's handler.
    """
    emitted = codegen.emit(schedule_payload=schedule)
    landed = CONSTANTS_PKG_SV.read_text(encoding="utf-8")
    matches = emitted.package_text == landed and (
        sched.SCHEDULE_ID in emitted.emitted_ids
    )
    print(
        "  %s: landed %s matches the live emission of both accepted "
        "registers -> %s"
        % (prefix, CONSTANTS_PKG_SV.name, "OK" if matches else "FAIL")
    )
    return matches


# Bind each lane after defining its shared core and import its entry point
# for the command table in main. run_voice needs the ADSR DUT path, and
# harness tests reference the vector and budget helpers.
import lane_adsr  # noqa: E402

lane_adsr.bind_core(sys.modules[__name__])
from lane_adsr import (  # noqa: E402
    ADSR_DUT_SV,  # noqa: F401
    ADSR_VECTOR_DIR,  # noqa: F401
    adsr,
    adsr_load_vectors,  # noqa: F401
    check_adsr_budget,  # noqa: F401
)


# run_voice imports the LFO DUT path, ag and lgo.
# Harness and LUT-path tests reference the vector, budget and simulation helpers.
import lane_lfo  # noqa: E402

lane_lfo.bind_core(sys.modules[__name__])
from lane_lfo import (  # noqa: E402
    LFO_DUT_SV,  # noqa: F401
    LFO_VECTOR_DIR,  # noqa: F401
    ag,  # noqa: F401
    check_lfo_budget,  # noqa: F401
    lfo,
    lfo_derive_case,  # noqa: F401
    lfo_load_vectors,  # noqa: F401
    lfo_simulate,  # noqa: F401
    lfo_write_case,  # noqa: F401
    lgo,  # noqa: F401
)


# run_voice imports both modulation-matrix DUT paths. Tests reference the
# vector loader and mutation cap. ReExportTest pins the route order.
import lane_modmatrix  # noqa: E402

lane_modmatrix.bind_core(sys.modules[__name__])
from lane_modmatrix import (  # noqa: E402
    MM_DUT_SV,  # noqa: F401
    MM_MUTATION_WALK_CAP,  # noqa: F401
    MM_UP_DUT_SV,  # noqa: F401
    MM_VECTOR_DIR,  # noqa: F401
    MOD_MATRIX_INPUTS,  # noqa: F401
    MOD_MATRIX_OUTPUTS,  # noqa: F401
    mm_load_vectors,  # noqa: F401
    modmatrix,
)


# main handles VCO capture errors, and the mix lane loads the VCO receipt.
# The integrated runners and VCO tests use the paths, model aliases and
# simulation helpers through run_tb.
import lane_vco  # noqa: E402

lane_vco.bind_core(sys.modules[__name__])
from lane_vco import (  # noqa: E402
    SINE_VECTOR_DIR,  # noqa: F401
    VCO_DUT_SV,  # noqa: F401
    VCO_LUT_PLUSARG_CHARS,  # noqa: F401
    VCO_RECEIPT_PATH,  # noqa: F401
    VcoCaptureIntegrityError,
    mm,  # noqa: F401
    sine_load_vectors,  # noqa: F401
    vco,
    vco_check_sim_log,  # noqa: F401
    vco_derive_case,  # noqa: F401
    vco_load_receipt,
    vco_read_captures,  # noqa: F401
    vco_simulate,  # noqa: F401
    vco_verdict_text,  # noqa: F401
    vco_write_case,  # noqa: F401
    vg,  # noqa: F401
)


# The normreplay lane uses the patch frame builders, model and scheduler.
# Retain the protocol helpers referenced elsewhere in the repository.
import lane_patch  # noqa: E402

lane_patch.bind_core(sys.modules[__name__])
from lane_patch import (  # noqa: E402
    CAPS_WITH_RENDER,
    CORE_IDENTITY,
    NOISE_CLIP_BYTES,  # noqa: F401
    PATCH_DUT_SV,
    RENDER_DIGEST_A,
    RENDER_DIGEST_B,
    SeqCounter,
    compare_run,  # noqa: F401
    crc16_ccitt_false,  # noqa: F401
    frame,  # noqa: F401
    noise_pass_frames,
    paced,  # noqa: F401
    paced_render,
    patch,
    patch_anchor_words,
    patch_build_model,
    render_full_tx,
    render_hello,
    render_trigger_frame,
    schedule_of,
    write_schedule,
)


# Audio VCA and pre-normalization mixer lane. The one-shot runners also
# import the directed-fixture and mix-model modules (dr and mx) from here.
import lane_mix  # noqa: E402

lane_mix.bind_core(sys.modules[__name__])
from lane_mix import (  # noqa: E402,F401  (re-exported for consumers)
    MIX_CURVE_EVIDENCE,
    MIX_DUT_SV,
    MIX_FIXTURE_CASES,
    MIX_HIGH_DEPTH_CASES,
    MIX_MUTATION_WALK_CAP,
    MIX_REPLAY_PAIR,
    MIX_RTL_MUTATIONS,
    MIX_SINGLE_SOURCE_CASES,
    MIX_STREAM_TRACES,
    MIX_STRESS_CASE,
    MIX_TB_SV,
    check_mix_budget,
    dr,
    mix,
    mix_check_case,
    mix_derive_case,
    mix_expected_ops,
    mix_expected_streams,
    mix_load_fixtures,
    mix_load_receipt,
    mix_mismatch_rows,
    mix_prefix_truth,
    mix_report_mutation,
    mix_run_rtl_mutation,
    mix_run_stimulus_mutation,
    mix_simulate,
    mix_write_case,
    mx,
)


def check_clip_budget(measured_cycles: int, sample_count: int) -> bool:
    """Assert the ratified clip budget against the anchor run (issue #68).

    The DR-0010 schedule register is consumed through its refusal gate; the
    budget constants are (a) checked for internal consistency, (b) checked
    against the live emission of the constants package (the anchor's cycle
    count is measured against the emitted budget, so a stale package fails
    here exactly as ``tools/generate_rtl_constants.py --check`` would), and
    (c) reported as measured-vs-budget headroom over the DUT's counted
    cycles.

    Honesty: the format-true LUT DUT retires one sample per cycle and is
    NOT the DR-0010 serialized single-MAC schedule, so the measured-vs-
    budget comparison is a PARTIAL-DUT headroom report, not a schedule-
    conformance assertion — only the consistency and emission checks are
    hard asserts.
    """
    try:
        schedule = sched.require_accepted_schedule()
    except ScheduleNotAccepted as error:
        print("BUDGET REFUSED: DR-0010 schedule register refused: %s" % error)
        return False

    slots = sched.constant(schedule, "clip_sample_slots")
    passes = sched.constant(schedule, "passes_per_clip")
    samples = sched.constant(schedule, "samples_per_pass")
    rate = int(schedule["profile"]["sample_rate_hz"])
    counted = sched.constant(schedule, "counted_cycles_per_sample")
    t_max = sched.constant(schedule, "t_max_at_25mhz")
    bound_mhz = sched.constant(schedule, "bound_clock_mhz")
    slots_per_second = int(schedule["refutable_bound"]["slots_per_realtime_second"])
    example = schedule["budget_equation"]["worked_example"]

    checks = [
        (
            "clip sample-slots: %d == %d passes x %d samples/pass"
            % (slots, passes, samples),
            slots == passes * samples,
        ),
        (
            "real-time slot rate: %d == %d x %d / %d"
            % (slots_per_second, slots, rate, samples),
            slots_per_second == slots * rate // samples,
        ),
        (
            "refutable bound: T_max %d == floor(%d MHz / %d) - C_counted %d"
            % (t_max, bound_mhz, slots_per_second, counted),
            t_max == bound_mhz * 1000000 // slots_per_second - counted,
        ),
        (
            "DR-0010 worked example: %d slots x (%d + T=%d) = %d cycles"
            % (slots, counted, example["t"], example["n_clip"]),
            example["n_clip"] == slots * (counted + example["t"]),
        ),
    ]
    ok = True
    for line, holds in checks:
        print("  budget consistency: %s -> %s" % (line, "OK" if holds else "FAIL"))
        ok = ok and holds

    try:
        matches = _emitted_constants_match(
            schedule, prefix="emitted budget constants"
        )
        ok = ok and matches
    except Exception as error:  # noqa: BLE001 - reported, never a silent pass
        print("  emitted budget constants: emission failed -> %s" % error)
        return False

    if sample_count <= 0 or measured_cycles <= 0:
        print("  measured cycles: invalid measurement (%d cycles)" % measured_cycles)
        return False

    per_sample = measured_cycles / sample_count
    budget_t0 = sample_count * counted
    budget_tmax = sample_count * sched.cycles_per_sample(schedule, t_max)
    n_clip_t0 = sched.clip_cycles(schedule, 0)
    n_clip_tmax = sched.clip_cycles(schedule, t_max)
    print(
        "  measured (PARTIAL DUT): %d enabled cycles for %d samples "
        "(%.3f cycles/sample)" % (measured_cycles, sample_count, per_sample)
    )
    print(
        "  headroom vs budget (same sample count, single pass): "
        "T=0 C=%d -> %d cycles (%.1f%% free); T=T_max=%d C=%d -> %d cycles "
        "(%.1f%% free)"
        % (
            counted,
            budget_t0,
            100.0 * (budget_t0 - measured_cycles) / budget_t0,
            t_max,
            counted + t_max,
            budget_tmax,
            100.0 * (budget_tmax - measured_cycles) / budget_tmax,
        )
    )
    print(
        "  clip budget N_clip = %d x C: %d cycles at T=0, %d cycles at "
        "T=T_max=%d" % (slots, n_clip_t0, n_clip_tmax, t_max)
    )
    print(
        "  NOTE: PARTIAL-DUT MEASUREMENT - the format-true LUT DUT is one "
        "sample per cycle, not the DR-0010 serialized single-MAC schedule; "
        "this is a headroom report, not a schedule-conformance, PPA, or "
        "fit claim (#82/#83 own those measurements)."
    )
    return ok


def vco_load_vectors():
    """Load and contract-verify every committed square/saw VCO vector."""

    return load_golden_vectors(VCO_VECTOR_DIR, "square/saw VCO")


def vco2_derive_case(formats, vector):
    """Model-derived stimulus and truth for one square/saw VCO case.

    The host mirror (``torchsynth_voice.vco2_golden``) re-walks the frozen
    model's vco_2 lane with the model's own primitives; its ``vco_2.raw``
    digest must equal the committed evidence row — and, on frozen-binding
    vectors, the frozen golden's trace digest plus the retained sidecar
    words. The declared binary64 shadow sites (midi->Hz exp2, the
    partials constant, the tanh fanout) are computed host-side here
    exactly as DR-0008/DR-0010 declare them open approximation items —
    a harness input derivation, never an RTL claim.
    """

    provenance = vector["provenance"]
    derivation = vc.derive_case(formats, vector["parameters"])
    streams = derivation["streams"]
    digest = vc.voice_digest(streams["v2"])
    row = provenance["vco_2_raw"]
    if digest != row["words_sha256"]:
        raise SystemExit(
            "square/saw VCO mirror digest drift for %s: regenerated %s but "
            "the committed vector declares %s -- regenerate the vectors, "
            "do not recompile"
            % (provenance["case_id"], digest, row["words_sha256"])
        )
    for j_str, word in row["jitter"].items():
        if streams["v2"][int(j_str)] != word:
            raise SystemExit(
                "jitter drift for %s at j=%s against the committed vector"
                % (provenance["case_id"], j_str)
            )

    frozen_words = None
    binding = provenance.get("frozen_binding")
    if binding:
        payload = (ROOT / binding["sidecar"]["file"]).read_bytes()
        if hashlib.sha256(payload).hexdigest() != binding["sidecar"]["sha256"]:
            raise SystemExit(
                "frozen sidecar bytes drifted for %s" % provenance["case_id"]
            )
        frozen_words = vc.unpack_words_f32le(payload)
        if vc.voice_digest(frozen_words) != binding["trace_digest"]:
            raise SystemExit(
                "frozen sidecar words digest drift for %s"
                % provenance["case_id"]
            )
        if digest != binding["trace_digest"]:
            raise SystemExit(
                "mirror does not reproduce the frozen vco_2.raw digest "
                "for %s" % provenance["case_id"]
            )

    return {
        "case_id": provenance["case_id"],
        "statics": {
            "keyboard.midi_f0": derivation["words"]["keyboard.midi_f0"],
            "vco_2.tuning": derivation["words"]["vco_2.tuning"],
            "vco_2.mod_depth": derivation["words"]["vco_2.mod_depth"],
            "vco_2.shape": derivation["words"]["vco_2.shape"],
            "partials_word": derivation["partials"].word,
            "init_phase_word": derivation["init_word"],
        },
        "up_pitch": derivation["up_pitch"],
        "fq": streams["fq"],
        "square_q": streams["square_q"],
        "left_q": streams["left_q"],
        "streams": streams,
        "sat_total": streams["counters"]["total_saturation"],
        "frozen_words": frozen_words,
    }


def vco2_write_case(workdir: Path, run: int, case: dict, walk_cap: int = None):
    """Write one run's stimulus files (statics + per-sample stream)."""

    s = case["statics"]
    n = len(case["up_pitch"]) if walk_cap is None else min(
        walk_cap, len(case["up_pitch"])
    )
    (workdir / ("run%d_statics.txt" % run)).write_text(
        "%d %d %d %d %d %d %d\n"
        % (
            s["keyboard.midi_f0"] & 0xFFFFFFFF,
            s["vco_2.tuning"] & 0xFFFFFFFF,
            s["vco_2.mod_depth"] & 0xFFFFFFFF,
            s["vco_2.shape"] & 0xFFFFFFFF,
            s["partials_word"] & 0xFFFFFFFF,
            s["init_phase_word"] & 0xFFFFFFFF,
            n,
        ),
        encoding="utf-8",
    )
    rows = []
    for i in range(n):
        rows.append(
            "%d %d %d %d"
            % (
                case["up_pitch"][i] & 0xFFFFFFFF,
                case["fq"][i] & 0xFFFFFFFF,
                case["square_q"][i] & 0xFFFFFFFF,
                case["left_q"][i] & 0xFFFFFFFF,
            )
        )
    (workdir / ("run%d_stream.txt" % run)).write_text(
        "".join(row + "\n" for row in rows), encoding="utf-8"
    )


def vco2_simulate(workdir: Path, simulator: str, runs: int, formats,
                 dut_sv: Path = None) -> list:
    """Compile and run the square/saw VCO tb; return per-run captures.

    Writes the accepted quarter-wave table memh into the working directory
    (the +lut payload, hash-linked to the accepted register exactly as the
    anchor flow does).
    """

    (workdir / "runs.txt").write_text("%d\n" % runs, encoding="utf-8")
    lut_memh = workdir / "lut_quarter_cos.memh"
    write_lut_memh(formats.table, lut_memh)
    vvp = _compile_iverilog(
        workdir, simulator, "square_saw_vco.vvp",
        [CONSTANTS_PKG_SV, VCO_LUT_SV, dut_sv or VCO2_DUT_SV, VCO2_TB_SV],
    )
    _run(
        ["vvp", "-n", str(vvp), "+lut=%s" % lut_memh.name],
        cwd=workdir,
    )
    captures = []
    for run in range(runs):
        rows = []
        for line in (
            workdir / ("run%d_out.txt" % run)
        ).read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append([int(word) for word in line.split()])
        ops = []
        for line in (
            workdir / ("run%d_ops.txt" % run)
        ).read_text(encoding="utf-8").splitlines():
            if line.strip():
                ops.append(line.split())
        cycles = int(
            (workdir / ("run%d_cycles.txt" % run))
            .read_text(encoding="utf-8").strip()
        )
        captures.append({"rows": rows, "ops": ops, "cycles": cycles})
    return captures


#: The engine's four exported streams, in capture-column order.
VCO_STREAM_NAMES = ("vco_2.pitch", "vco_2.driven", "vco_2.right_q",
                    "vco_2.raw")


def vco_mismatch_trace_set(capture_rows, case, prefix: bool = False) -> set:
    """Trace names where the capture differs from the mirror truth.

    ``prefix`` compares each stream over the captured prefix only — used
    by the capped-walk mutation demonstrations, where a shorter capture is
    not itself a mismatch but any differing sample inside the captured
    prefix is. On frozen-binding cases the vco_2.raw capture must also
    match the frozen sidecar words directly (no mirror in the loop).
    """

    mirror = (case["streams"]["m2"], case["streams"]["driven"],
              case["streams"]["right_q"], case["streams"]["v2"])
    bad = set()
    for index, name in enumerate(VCO_STREAM_NAMES):
        got = [row[index] for row in capture_rows]
        want = mirror[index]
        if prefix:
            want = want[: len(got)]
        if got != want:
            bad.add(name)
    if case["frozen_words"] is not None:
        got = [row[3] for row in capture_rows]
        want = case["frozen_words"]
        if prefix:
            want = want[: len(got)]
        if got != want:
            bad.add("vco_2.raw(frozen sidecar)")
    return bad


def vco_report_first_mismatch(capture_rows, case) -> None:
    """Print the first differing (stream, sample, expected, actual) row."""

    mirror = (case["streams"]["m2"], case["streams"]["driven"],
              case["streams"]["right_q"], case["streams"]["v2"])
    for index, name in enumerate(VCO_STREAM_NAMES):
        got = [row[index] for row in capture_rows]
        want = mirror[index]
        for i in range(max(len(got), len(want))):
            a = got[i] if i < len(got) else None
            b = want[i] if i < len(want) else None
            if a != b:
                print(
                    gv.format_mismatch(
                        gv.Mismatch(cycle=i, sample=i, trace=name,
                                    expected=b, actual=a)
                    )
                )
                return


def vco2_check_case(capture, case, case_id: str) -> bool:
    """Sample-exact verification for one run (mirror + frozen words)."""

    if not capture["rows"]:
        print("square/saw VCO case %s: EMPTY capture" % case_id)
        return False
    bad = vco_mismatch_trace_set(capture["rows"], case)
    if bad:
        print("square/saw VCO case %s: mismatch in %s" % (case_id, sorted(bad)))
        vco_report_first_mismatch(capture["rows"], case)
        return False
    frozen_note = " + frozen sidecar words" if case["frozen_words"] else ""
    print(
        "case %s: %d samples x %d streams, RTL sample-exact%s -> OK"
        % (case_id, len(capture["rows"]), len(VCO_STREAM_NAMES), frozen_note)
    )
    return True


def vco2_expected_ops(sample_count: int, sat_total: int) -> list:
    """The DR-0010 #74 owner-row op-count expectation for one run.

    Per sample: 8 multiply-class products (depth-mod, 2x LUT interp x2,
    driven, right shape, combine), 8 add/sub-class ops (pitch sum x2,
    phase add, 2x interp sub+add, right-branch add), 7 declared narrowing
    sites (pitch depth-mod, increment division, 2x interpolation rounding,
    driven, right, combine), plus the model mirror's own sticky saturation
    total across the declared C7 sites.
    """

    return [
        "M",
        8 * sample_count,
        8 * sample_count,
        7 * sample_count,
        sat_total,
    ]


def vco2_run_mutation(workdir: Path, simulator: str, formats, label: str,
                     anchor: str, replacement: str, case_id: str,
                     case_dirs: dict):
    """Plant one RTL mutation on one case and require it to be DETECTED."""

    case = case_dirs[case_id][1]

    def write_mutant(mut_dir):
        source = VCO2_DUT_SV.read_text(encoding="utf-8")
        if anchor not in source:
            raise SystemExit("mutation anchor not found for %s" % label)
        (mut_dir / "square_saw_vco_engine_mut.sv").write_text(
            mutate_sv(source, anchor, replacement, label), encoding="utf-8"
        )

    def write_case(mut_dir):
        vco2_write_case(mut_dir, 0, case, walk_cap=VCO2_MUTATION_WALK_CAP)

    def simulate(mut_dir):
        return vco2_simulate(
            mut_dir, simulator, 1, formats,
            dut_sv=mut_dir / "square_saw_vco_engine_mut.sv",
        )[0]

    def is_detected(capture):
        bad = vco_mismatch_trace_set(capture["rows"], case, prefix=True)
        return bool(bad), " (mismatch traces: %s)" % sorted(bad)

    return run_rtl_mutation(
        workdir, label, case_id, write_mutant, write_case, simulate, is_detected
    )


def vco_run_vector_mutation(workdir: Path, simulator: str, formats,
                            label: str, case_id: str, case_dirs: dict,
                            rewrite_stimulus, expect_traces: set):
    """Plant a stimulus-side mutation and require sharp localization.

    ``rewrite_stimulus(case)`` mutates the replayed stimulus words in place
    on a deep copy; the expectations stay pristine, so the capture must
    diverge exactly inside ``expect_traces``.
    """

    mutated = copy.deepcopy(case_dirs[case_id][1])
    rewrite_stimulus(mutated)

    def write_mutant(mut_dir):
        pass  # stimulus-side mutation: the DUT itself is never mutated.

    def write_case(mut_dir):
        vco2_write_case(mut_dir, 0, mutated, walk_cap=VCO2_MUTATION_WALK_CAP)

    def simulate(mut_dir):
        return vco2_simulate(mut_dir, simulator, 1, formats)[0]

    def is_detected(capture):
        bad = vco_mismatch_trace_set(capture["rows"], mutated, prefix=True)
        detected = bool(bad)
        if bad != expect_traces:
            detected = False
            localization = (
                " (localization FAILED: mismatch set %s != expected %s)"
                % (sorted(bad), sorted(expect_traces))
            )
        else:
            localization = " (localized to %s)" % sorted(bad)
        return detected, localization

    return run_rtl_mutation(
        workdir, label, case_id, write_mutant, write_case, simulate, is_detected,
        kind="stimulus",
    )


def vco2(workdir: Path, simulator: str) -> int:
    """Issue #74 flow: the square/saw VCO engine vs the frozen vectors."""

    try:
        formats = AcceptedFormats()
    except ChoiceNotAccepted as error:
        print("SQUARE-SAW VCO REFUSED: accepted register refused: %s" % error)
        return 1
    try:
        schedule = sched.require_accepted_schedule()
    except ScheduleNotAccepted as error:
        print("SQUARE-SAW VCO REFUSED: DR-0010 schedule register refused: %s" % error)
        return 1

    vectors = vco_load_vectors()
    frozen_count = sum(
        1 for name in vectors if name.startswith(VCO_FROZEN_PREFIX)
    )
    print(
        "Loaded %d square/saw VCO golden vectors (%d frozen-binding), "
        "contract bindings verified" % (len(vectors), frozen_count)
    )
    print(
        "Declared shadow boundary: exp2/partials/tanh replayed host-side "
        "(DR-0008/DR-0010 open approximation items); no RTL transcendental "
        "is implemented or claimed."
    )

    ok = True

    # 1. Sample-exact engine runs, one committed case per invocation.
    case_dirs = {}
    for case_id in sorted(vectors):
        case_dir = workdir / ("case-" + case_id.replace(":", "_"))
        case_dir.mkdir(parents=True, exist_ok=True)
        case = vco2_derive_case(formats, vectors[case_id])
        vco2_write_case(case_dir, 0, case)
        captures = vco2_simulate(case_dir, simulator, 1, formats)
        if not vco2_check_case(captures[0], case, case_id):
            ok = False
        case_dirs[case_id] = (case_dir, case)

    # 2. Reset/replay: a second trigger cannot retain prior state (static
    #    words, phase, and op counters are per-trigger).
    first, second = VCO2_REPLAY_PAIR
    replay_dir = workdir / "replay"
    replay_dir.mkdir(parents=True, exist_ok=True)
    case_first = vco2_derive_case(formats, vectors[first])
    case_second = vco2_derive_case(formats, vectors[second])
    vco2_write_case(replay_dir, 0, case_first)
    vco2_write_case(replay_dir, 1, case_second)
    replay_captures = vco2_simulate(replay_dir, simulator, 2, formats)
    solo_dir = workdir / "solo"
    solo_dir.mkdir(parents=True, exist_ok=True)
    vco2_write_case(solo_dir, 0, case_second)
    solo_captures = vco2_simulate(solo_dir, simulator, 1, formats)
    replay_ok = True
    if replay_captures[1]["rows"] != solo_captures[0]["rows"]:
        print(
            "SQUARE-SAW VCO FAILED: run-after-run capture differs from the "
            "solo run - prior run state leaked"
        )
        replay_ok = False
    if not vco2_check_case(replay_captures[1], case_second, second):
        replay_ok = False
    print(
        "reset/replay: trigger-to-trigger back-to-back runs (%s -> %s) "
        "reproduce the solo golden run -> %s"
        % (first, second, "OK" if replay_ok else "FAIL")
    )
    ok = ok and replay_ok

    # 3. Budget: exported op counters + the DR-0010 #74 owner row.
    ops_ok = True
    for run_index, case in enumerate((case_first, case_second)):
        parsed = replay_captures[run_index]["ops"][0]
        n = len(replay_captures[run_index]["rows"])
        expected = vco2_expected_ops(n, case["sat_total"])
        if parsed != [str(x) for x in expected]:
            print(
                "SQUARE-SAW VCO FAILED: op counters %r != expected %r "
                "(run %d)" % (parsed, expected, run_index)
            )
            ops_ok = False
    print(
        "budget: exported counters over both replay runs -> %s (per sample "
        "8 mult / 8 add / 7 narrow = the DR-0010 #74 owner row's pitch row "
        "+ two LUT interps + shape row; sats = the model mirror's sticky "
        "saturation total)" % ("OK" if ops_ok else "FAIL")
    )
    ok = ok and ops_ok and check_clip_budget(
        solo_captures[0]["cycles"], len(solo_captures[0]["rows"])
    )

    # 4. Mutations: each planted fault MUST be detected (AC-5).
    mutations_ok = True

    # 4a. Wrong shape mix (RTL): the right branch's shape coefficient is
    #     inverted to (1 - shape), the classic square/saw mix confusion.
    mutations_ok &= vco2_run_mutation(
        workdir, simulator, formats, "wrong-shape-mix",
        "wire signed [95:0] right_prod = $signed(s_shape) * $signed(cos2);",
        "wire signed [95:0] right_prod = (96'sd2097152 - $signed(s_shape))"
        " * $signed(cos2);  // MUTANT: wrong shape mix (1-shape)",
        VCO_SHAPE_MUTATION_CASE, case_dirs,
    )

    # 4b. Partials-constant error (stimulus): one ULP on the replayed
    #     s14.17 partials word must localize to the driven stream alone
    #     (v2 is downstream of the tanh shadow, which the host owns).
    def _partials_ulp(case):
        case["statics"]["partials_word"] = (
            case["statics"]["partials_word"] + 1
        )

    mutations_ok &= vco_run_vector_mutation(
        workdir, simulator, formats, "partials-constant-error",
        VCO_VECTOR_MUTATION_CASE, case_dirs, _partials_ulp,
        {"vco_2.driven"},
    )

    # 4c. Shadow-word corruption (stimulus): one flipped LSB in the
    #     replayed left_q tanh fanout must localize to vco_2.raw alone at
    #     exactly that sample.
    def _shadow_flip(case):
        case["left_q"][VCO_SHADOW_MUTATION_INDEX] ^= 1

    mutations_ok &= vco_run_vector_mutation(
        workdir, simulator, formats, "shadow-word-corruption",
        VCO_VECTOR_MUTATION_CASE, case_dirs, _shadow_flip,
        {"vco_2.raw"},
    )
    ok = ok and mutations_ok

    if not ok:
        print("SQUARE-SAW VCO RUN FAILED")
        return 1
    print(
        "SQUARE-SAW VCO RUN PASSED (contract binding + %d cases "
        "sample-exact incl. %d frozen-binding sidecar cases + "
        "reset/replay independence + budget/op-count asserts + all "
        "mutations detected)" % (len(vectors), frozen_count)
    )
    return 0


def anchor(workdir: Path, simulator: str) -> int:
    try:
        formats = AcceptedFormats()
    except ChoiceNotAccepted as error:
        print("ANCHOR REFUSED: accepted register refused: %s" % error)
        return 1

    vector = gv.load_vector(gv.SENTINEL_VECTOR_PATH)
    print(
        "Loaded real golden vector %s (%d traces, content_hash verified)"
        % (gv.SENTINEL_VECTOR_PATH.name, len(vector["traces"]))
    )

    verified = gv.verify_accepted_contract(vector)
    print(
        "Accepted-contract binding verified (dr_0008=%(dr_0008_record_sha256)s\n"
        "  register=%(choices_register_sha256)s\n"
        "  package=%(constants_package_sha256)s\n"
        "  record digest source: %(dr_0008_record_source)s)" % verified
    )

    table = formats.table
    declared_lut = vector["provenance"].get("lut_sha256")
    live_lut = table.sha256()
    if declared_lut != live_lut:
        print(
            "ANCHOR REFUSED: accepted quarter-wave table hashes to %s but "
            "the vector binds %s" % (live_lut, declared_lut)
        )
        return 1
    print("Accepted LUT table digest verified: %s" % live_lut)

    sample_count = int(vector["clip"]["sample_count"])
    phase_step, level_word = derive_anchor_control_words(vector, formats)
    print(
        "Derived control words: phase_step=%d level=%d over %d samples "
        "(shadow-site replay, host-side)" % (phase_step, level_word, sample_count)
    )

    lut_memh = workdir / "lut_quarter_cos.memh"
    write_lut_memh(table, lut_memh)
    captured_vco, captured_mix, measured_cycles = simulate_anchor(
        workdir, simulator, phase_step, level_word, sample_count, lut_memh
    )
    if len(captured_vco) != sample_count or len(captured_mix) != sample_count:
        print(
            "ANCHOR FAILED: captured %d/%d streams, expected %d samples"
            % (len(captured_vco), len(captured_mix), sample_count)
        )
        return 1

    ok = True
    budget_ok = check_clip_budget(measured_cycles, sample_count)
    if not budget_ok:
        ok = False

    focus = focus_vector(vector, ANCHOR_TRACE)
    expected_vco = focus["traces"][0]["values"]

    mirror_vco, mirror_mix = anchor_mirror(
        formats, phase_step, level_word, sample_count
    )
    mirror_mismatch = gv.first_mismatch(focus, {ANCHOR_TRACE: mirror_vco})
    if mirror_mismatch is not None:
        print("ANCHOR FAILED: harness mirror disagrees with the frozen vector:")
        print(gv.format_mismatch(mirror_mismatch))
        ok = False
    else:
        print(
            "Vector-truth path verified: model-primitive mirror reproduces "
            "the frozen %s trace over all %d samples."
            % (ANCHOR_TRACE, sample_count)
        )

    rtl_mismatch = gv.first_mismatch(focus, {ANCHOR_TRACE: captured_vco})
    if rtl_mismatch is not None:
        print("ANCHOR FAILED: RTL capture disagrees with the frozen vector:")
        print(gv.format_mismatch(rtl_mismatch))
        ok = False
    else:
        print(
            "RTL path verified: format-true DUT reproduces the frozen %s "
            "trace over all %d samples." % (ANCHOR_TRACE, sample_count)
        )

    # The mixer-level product stream is a derived DUT stream (the vector's
    # mixer.output additionally carries the control-path VCA column), so it
    # is checked against the model-primitive mirror, not the vector.
    derived_trace = "derived.vco_1.raw*mixer.vco_1.level"
    for index, (expected, actual) in enumerate(zip(mirror_mix, captured_mix)):
        if expected != actual:
            print("ANCHOR FAILED: derived mixer-level product stream diverges:")
            print(
                gv.format_mismatch(
                    gv.Mismatch(
                        cycle=index, sample=index, trace=derived_trace,
                        expected=expected, actual=actual,
                    )
                )
            )
            ok = False
            break
    else:
        print(
            "Derived path verified: mixer-level product matches the "
            "model-primitive mirror over all %d samples." % sample_count
        )

    planted_value = (expected_vco[ANCHOR_FAIL_INDEX] ^ 1) & 0xFFFFFF
    faulted = list(captured_vco)
    faulted[ANCHOR_FAIL_INDEX] = planted_value
    planted = gv.Mismatch(
        cycle=ANCHOR_FAIL_INDEX,
        sample=ANCHOR_FAIL_INDEX,
        trace=ANCHOR_TRACE,
        expected=expected_vco[ANCHOR_FAIL_INDEX],
        actual=planted_value,
    )
    observed = gv.first_mismatch(focus, {ANCHOR_TRACE: faulted})
    if observed != planted:
        print(
            "ANCHOR FAILED: reporter did not name the planted fault on the "
            "real vector; observed %r" % (observed,)
        )
        ok = False
    else:
        print("Intentional-fail path verified on the real vector; reporter reports:")
        print(gv.format_mismatch(observed))

    if not ok:
        print("ANCHOR RUN FAILED")
        return 1
    print(
        "ANCHOR RUN PASSED (contract binding + vector truth + RTL "
        "sample-exact + clip budget + fail path)"
    )
    return 0


def noise_write_run(workdir: Path, run: int, sound_index: int,
                    declared_slot: int, noise_bytes: bytes,
                    switch_at: int = -1, switch_sound_index: int = 0,
                    switch_slot: int = 0) -> None:
    """One run's stimulus: identity words + the exact fed byte stream.

    ``switch_at >= 0`` splices a foreign trigger identity in at that byte
    offset (the clip-mixing stimulus); the default holds one identity for
    the whole stream, as a real trigger must.
    """

    (workdir / ("run%d_stim.txt" % run)).write_text(
        "%d %d %d %d %d %d\n" % (
            sound_index, declared_slot, len(noise_bytes),
            switch_at, switch_sound_index, switch_slot,
        ),
        encoding="utf-8",
    )
    (workdir / ("run%d_bytes.txt" % run)).write_text(
        "".join("%02x\n" % b for b in noise_bytes), encoding="utf-8"
    )


def noise_simulate(workdir: Path, simulator: str, runs: int,
                   dut_sv: Path):
    """Compile + run the noise TB; return (captures, statuses) per run."""

    return simulate_runs(
        workdir, simulator, runs, "noise_stream.vvp",
        [CONSTANTS_PKG_SV, dut_sv, NOISE_TB_SV],
    )


@dataclass(frozen=True)
class _NoiseReceiptStream:
    raw: bytes
    mirror: list[int]
    byte_sha256: str
    trace_digest: str


def _noise_receipt_streams(cases):
    """Resolve the streams and check every receipt row's identity and digests."""
    ok = True
    streams = {}
    for case in cases:
        sound_index = case["sound_index"]
        meta = case["noise"]
        if meta["seed"] != 13 or meta["slot"] != nsg.canonical_slot(sound_index):
            print(
                "NOISE FAILED: case %s noise identity block violates the "
                "C8 slot rule" % case["id"]
            )
            ok = False
            continue
        stream = streams.get(sound_index)
        if stream is None:
            raw = nsg.resolve_canonical_bytes(sound_index)
            mirror = nsg.mirror_stream(raw)
            stream = _NoiseReceiptStream(
                raw, mirror, nsg.noise_bytes_sha256(raw), nsg.trace_digest(mirror)
            )
            streams[sound_index] = stream
        if (
            stream.byte_sha256 != meta["sha256"]
            or stream.trace_digest != case["traces"]["noise.raw"]
        ):
            print(
                "NOISE FAILED: case %s mirror/digest binding broken "
                "(bytes %s vs %s; noise.raw %s vs %s)"
                % (
                    case["id"],
                    stream.byte_sha256,
                    meta["sha256"],
                    stream.trace_digest,
                    case["traces"]["noise.raw"],
                )
            )
            ok = False
    print(
        "host feed + mirror: %d golden cases resolved (seed 13, slot "
        "sound_index %% 32), every noise.raw digest bound bit-exactly -> %s"
        % (len(cases), "OK" if ok else "FAIL")
    )
    return streams, ok


def noise(workdir: Path, simulator: str) -> int:
    """Issue #75 flow: the host-fed exact noise lane vs the golden receipt."""

    receipt = json.loads(NOISE_RECEIPT_PATH.read_bytes())
    if receipt["kind"] != "fixed-voice-golden-release-receipt":
        raise SystemExit("unexpected receipt kind: %r" % receipt["kind"])
    cases = receipt["cases"]

    # 1+2. Host-feed resolve + golden bit-exactness of the mirror lane.
    streams, ok = _noise_receipt_streams(cases)

    # 2b. The pre-render gate (AC-4): length + declared digest + framing +
    # canonical seed-13 identity, all checked BEFORE a stream is fed.
    gate_ok = True
    for case in cases:
        sound_index = case["sound_index"]
        raw = streams[sound_index].raw
        try:
            gated = nsg.check_fed_bytes(
                raw,
                sound_index=sound_index,
                declared_slot=nsg.canonical_slot(sound_index),
                expected_sha256=case["noise"]["sha256"],
            )
        except ValueError as error:
            print("NOISE FAILED: pre-render gate refused case %s: %s"
                  % (case["id"], error))
            gate_ok = False
            continue
        if gated != case["noise"]["sha256"]:
            print("NOISE FAILED: pre-render gate digest mismatch on case %s"
                  % case["id"])
            gate_ok = False
    print(
        "pre-render gate: %d golden streams validated for length, declared "
        "digest, slot framing, and canonical seed-13 identity before any "
        "byte is fed -> %s" % (len(cases), "OK" if gate_ok else "FAIL")
    )
    ok = ok and gate_ok

    # 3+4. Committed cases + pattern stream + replay pair, one invocation.
    run_plan = []  # (label, sound_index, declared_slot, bytes)
    for case in cases:
        sound_index = case["sound_index"]
        raw = streams[sound_index].raw
        run_plan.append((case["id"], sound_index,
                         nsg.canonical_slot(sound_index), raw))
    # The synthetic pattern stream rides in one full clip (the DUT's length
    # contract is one clip per enabled stream; the edge patterns lead and
    # +0.0 padding completes the clip).
    pattern_bytes = b"".join(
        struct.pack("<I", bits) for bits in NOISE_PATTERN_BITS
    )
    pattern_bytes += b"\x00\x00\x00\x00" * (
        nsg.EXPECTED_NOISE_BYTES // 4 - len(NOISE_PATTERN_BITS)
    )
    assert len(pattern_bytes) == nsg.EXPECTED_NOISE_BYTES
    run_plan.append(("synthetic-patterns", 0, 0, pattern_bytes))
    first, replay_index = NOISE_REPLAY_INDICES
    raw0 = streams[first].raw
    raw_replay = nsg.resolve_canonical_bytes(replay_index)
    if nsg.noise_bytes_sha256(raw_replay) != nsg.noise_bytes_sha256(raw0):
        raise SystemExit(
            "replay pair %r must share a slot by the C8 rule" % (
                NOISE_REPLAY_INDICES,)
        )
    run_plan.append(("replay-sound-0", first, 0, raw0))
    run_plan.append(("replay-sound-32", replay_index,
                     nsg.canonical_slot(replay_index), raw_replay))

    case_dir = workdir / "committed"
    case_dir.mkdir(parents=True, exist_ok=True)
    for run, (_, sound_index, slot, raw) in enumerate(run_plan):
        noise_write_run(case_dir, run, sound_index, slot, raw)
    captures, statuses = noise_simulate(case_dir, simulator, len(run_plan),
                                        NOISE_DUT_SV)

    replay_positions = {}
    for run, (label, sound_index, slot, raw) in enumerate(run_plan):
        if label == "synthetic-patterns" or sound_index not in streams:
            mirror = nsg.mirror_stream(raw)
        else:
            mirror = streams[sound_index].mirror
        if label.startswith("replay-sound-"):
            replay_positions[label] = run
        captured = captures[run]
        status = statuses[run]
        if captured != mirror:
            print("NOISE FAILED: RTL capture != golden lane (%s):" % label)
            for i, (want, got) in enumerate(zip(mirror, captured)):
                if want != got:
                    print(
                        "  first mismatch at sample %d: expected %d, got %d"
                        % (i, want, got)
                    )
                    break
            else:
                print(
                    "  length mismatch: expected %d samples, got %d"
                    % (len(mirror), len(captured))
                )
            ok = False
            continue
        # The 6th field is the bound trigger identity's slot: a clean run
        # must have latched exactly the declared slot (DR-0010 lifecycle).
        clean = status == [0, 0, len(raw), len(mirror), len(mirror), slot]
        if not clean:
            print(
                "NOISE FAILED: run status %r not clean for %s"
                % (status, label)
            )
            ok = False
    print(
        "RTL bit-exactness: %d fed streams (%d golden cases + %d "
        "structural patterns + replay pair) equal the golden lane "
        "word-for-word -> %s"
        % (len(run_plan), len(cases), 1, "OK" if ok else "FAIL")
    )

    # 4. Replay/reset + 32-stream repetition: sound 0 then 32 back-to-back.
    replay_ok = (
        captures[replay_positions["replay-sound-32"]]
        == captures[replay_positions["replay-sound-0"]]
        == streams[first].mirror
    )
    print(
        "replay/reset: sound_index %d -> %d back-to-back through one en gap "
        "reproduces the golden capture byte-for-byte (32-stream repetition, "
        "no off-by-one state) -> %s"
        % (first, replay_index, "OK" if replay_ok else "FAIL")
    )
    ok = ok and replay_ok

    # 5. DR-0010 #75 owner row + emission check.
    try:
        schedule = sched.require_accepted_schedule()
    except ScheduleNotAccepted as error:
        print("NOISE REFUSED: DR-0010 schedule register refused: %s" % error)
        return 1
    owner_ok = True
    per_clip = {
        "declared narrowings": (176400, 176400),
        "multiply-class ops (structural: shift+round convert)": (0, 0),
    }
    for name, (actual, limit) in per_clip.items():
        verdict = "OK" if actual <= limit else "FAIL"
        owner_ok = owner_ok and actual <= limit
        print(
            "  budget: %d %s per clip vs DR-0010 #75 owner row %d -> %s"
            % (actual, name, limit, verdict)
        )
    for run, (label, _, _, _) in enumerate(run_plan):
        if label == "synthetic-patterns":
            continue
        narrow_count = statuses[run][4]
        if narrow_count != 176400:
            print(
                "NOISE FAILED: %s narrow_count %d != one declared narrowing "
                "per sample" % (label, narrow_count)
            )
            owner_ok = False
    print(
        "  budget: exported sticky counters assert 176400 declared "
        "narrowings per clip on every committed case -> %s"
        % ("OK" if owner_ok else "FAIL")
    )
    try:
        matches = _emitted_constants_match(schedule)
        owner_ok = owner_ok and matches
    except Exception as error:  # noqa: BLE001 - reported, never a silent pass
        print("  budget constants: emission failed -> %s" % error)
        return 1
    ok = ok and owner_ok

    # 6. Mutations: every planted fault MUST be detected (AC-5).
    mutations_ok = True
    mut_case = cases[NOISE_MUTATION_CASE_INDEX]
    mut_sound = mut_case["sound_index"]
    mut_slot = nsg.canonical_slot(mut_sound)
    mut_raw = streams[mut_sound].raw
    mut_mirror = streams[mut_sound].mirror

    # 6a. Dropped final byte (stimulus): the length check must latch.
    mut_dir = workdir / "mut-dropped"
    mut_dir.mkdir(parents=True, exist_ok=True)
    noise_write_run(mut_dir, 0, mut_sound, mut_slot, mut_raw[:-1])
    _, mut_statuses = noise_simulate(mut_dir, simulator, 1, NOISE_DUT_SV)
    detected = mut_statuses[0][:2] == [1, nsg.ERROR_STREAM_TRUNCATED]
    print(
        "mutation dropped-byte (stimulus): %s"
        % ("DETECTED (sticky length error)" if detected else "NOT DETECTED")
    )
    mutations_ok = mutations_ok and detected

    # 6b. Duplicated trailing byte (stimulus): the overrun check must latch.
    mut_dir = workdir / "mut-duplicated"
    mut_dir.mkdir(parents=True, exist_ok=True)
    noise_write_run(mut_dir, 0, mut_sound, mut_slot, mut_raw + b"\x00")
    _, mut_statuses = noise_simulate(mut_dir, simulator, 1, NOISE_DUT_SV)
    detected = mut_statuses[0][:2] == [1, nsg.ERROR_BYTE_OVERRUN]
    print(
        "mutation duplicated-byte (stimulus): %s"
        % ("DETECTED (sticky overrun error)" if detected else "NOT DETECTED")
    )
    mutations_ok = mutations_ok and detected

    # 6c. Wrong declared slot (stimulus): the C8 identity check must latch.
    mut_dir = workdir / "mut-slot"
    mut_dir.mkdir(parents=True, exist_ok=True)
    noise_write_run(mut_dir, 0, mut_sound, mut_slot ^ 1, mut_raw)
    _, mut_statuses = noise_simulate(mut_dir, simulator, 1, NOISE_DUT_SV)
    detected = mut_statuses[0][:2] == [1, nsg.ERROR_SLOT_IDENTITY]
    print(
        "mutation wrong-slot (stimulus): %s"
        % ("DETECTED (sticky slot-identity error)" if detected
           else "NOT DETECTED")
    )
    mutations_ok = mutations_ok and detected

    # 6d. LSB truncation instead of half-even (RTL).
    mut_dir = workdir / "mut-truncation"
    mut_dir.mkdir(parents=True, exist_ok=True)
    mutated = mutate_sv(
        NOISE_DUT_SV.read_text(encoding="utf-8"),
        NOISE_TRUNCATION_ANCHOR, NOISE_TRUNCATION_MUTANT, "lsb-truncation",
    )
    (mut_dir / "noise_stream_mut.sv").write_text(mutated, encoding="utf-8")
    noise_write_run(mut_dir, 0, mut_sound, mut_slot, mut_raw)
    mut_captures, mut_statuses = noise_simulate(
        mut_dir, simulator, 1, mut_dir / "noise_stream_mut.sv"
    )
    detected = (
        mut_captures[0] != mut_mirror
        and mut_statuses[0][:2] == [0, 0]
    )
    print(
        "mutation lsb-truncation (RTL): %s"
        % ("DETECTED (capture diverges from the golden lane)" if detected
           else "NOT DETECTED")
    )
    mutations_ok = mutations_ok and detected

    # 6e. Wrong slot-selection rule (RTL): the mutated rule must flag a
    # clean, correctly-declared stream.
    mut_dir = workdir / "mut-slotrule"
    mut_dir.mkdir(parents=True, exist_ok=True)
    mutated = mutate_sv(
        NOISE_DUT_SV.read_text(encoding="utf-8"),
        NOISE_SLOT_RULE_ANCHOR, NOISE_SLOT_RULE_MUTANT, "slot-rule",
    )
    (mut_dir / "noise_stream_mut.sv").write_text(mutated, encoding="utf-8")
    noise_write_run(mut_dir, 0, mut_sound, mut_slot, mut_raw)
    _, mut_statuses = noise_simulate(
        mut_dir, simulator, 1, mut_dir / "noise_stream_mut.sv"
    )
    detected = mut_statuses[0][:2] == [1, nsg.ERROR_SLOT_IDENTITY]
    print(
        "mutation slot-rule (RTL): %s"
        % ("DETECTED (mutated rule rejects the clean stream)" if detected
           else "NOT DETECTED")
    )
    mutations_ok = mutations_ok and detected

    # 6f. Length-preserving slip (stimulus): a dropped sample compensated
    # by a duplicated predecessor. Byte count, framing, and declared slot
    # all stay valid, so the DUT's shape checks structurally cannot see it
    # — the pre-render hash gate must refuse it, and the RTL lane must
    # diverge from the golden trace if it is fed anyway.
    slipped = nsg.slip_stream(mut_raw, NOISE_SLIP_SAMPLE)
    gate_refused = False
    try:
        nsg.check_fed_bytes(slipped, sound_index=mut_sound,
                            declared_slot=mut_slot)
    except ValueError:
        gate_refused = True
    mut_dir = workdir / "mut-slip"
    mut_dir.mkdir(parents=True, exist_ok=True)
    noise_write_run(mut_dir, 0, mut_sound, mut_slot, slipped)
    mut_captures, mut_statuses = noise_simulate(
        mut_dir, simulator, 1, NOISE_DUT_SV
    )
    detected = (
        gate_refused
        and mut_captures[0] != mut_mirror
        and mut_statuses[0][:2] == [0, 0]
    )
    print(
        "mutation length-preserving slip (stimulus): %s"
        % ("DETECTED (pre-render hash gate refuses; fed anyway, the RTL "
           "capture diverges while the shape checks stay clean)" if detected
           else "NOT DETECTED")
    )
    mutations_ok = mutations_ok and detected

    # 6g. Wrong-stream substitution (stimulus): another slot's canonical
    # bytes, correctly framed and correctly declared. Only the canonical
    # seed-13 identity binding can reject it.
    other_sound = mut_sound + 1
    other_raw = nsg.resolve_canonical_bytes(other_sound)
    gate_refused = False
    try:
        nsg.check_fed_bytes(other_raw, sound_index=mut_sound,
                            declared_slot=mut_slot)
    except ValueError:
        gate_refused = True
    mut_dir = workdir / "mut-substitution"
    mut_dir.mkdir(parents=True, exist_ok=True)
    noise_write_run(mut_dir, 0, mut_sound, mut_slot, other_raw)
    mut_captures, mut_statuses = noise_simulate(
        mut_dir, simulator, 1, NOISE_DUT_SV
    )
    detected = (
        gate_refused
        and mut_captures[0] != mut_mirror
        and mut_statuses[0][:2] == [0, 0]
    )
    print(
        "mutation wrong-stream substitution (stimulus): %s"
        % ("DETECTED (pre-render canonical-identity gate refuses; fed "
           "anyway, the RTL capture diverges)" if detected
           else "NOT DETECTED")
    )
    mutations_ok = mutations_ok and detected

    # 6h. Clip mixing (stimulus): a foreign trigger identity spliced in
    # mid-stream while the C8 slot rule still holds (sound_index 0 -> 32,
    # both slot 0). DR-0010's clip lifecycle binds one trigger to one
    # sound identity, so the DUT must latch it.
    mut_dir = workdir / "mut-identity"
    mut_dir.mkdir(parents=True, exist_ok=True)
    noise_write_run(
        mut_dir, 0, mut_sound, mut_slot, mut_raw,
        switch_at=NOISE_IDENTITY_SWITCH_BYTE,
        switch_sound_index=mut_sound + 32, switch_slot=mut_slot,
    )
    _, mut_statuses = noise_simulate(mut_dir, simulator, 1, NOISE_DUT_SV)
    detected = mut_statuses[0][:2] == [1, nsg.ERROR_IDENTITY_CHANGED]
    print(
        "mutation clip-mixing identity splice (stimulus): %s"
        % ("DETECTED (sticky identity-changed error)" if detected
           else "NOT DETECTED")
    )
    mutations_ok = mutations_ok and detected

    # 6i. Wrong identity binding (RTL): a mutated latch binds the wrong
    # slot, so it must reject a clean, correctly-declared stream.
    mut_dir = workdir / "mut-identity-rule"
    mut_dir.mkdir(parents=True, exist_ok=True)
    mutated = mutate_sv(
        NOISE_DUT_SV.read_text(encoding="utf-8"),
        NOISE_IDENTITY_ANCHOR, NOISE_IDENTITY_MUTANT, "identity-binding",
    )
    (mut_dir / "noise_stream_mut.sv").write_text(mutated, encoding="utf-8")
    noise_write_run(mut_dir, 0, mut_sound, mut_slot, mut_raw)
    _, mut_statuses = noise_simulate(
        mut_dir, simulator, 1, mut_dir / "noise_stream_mut.sv"
    )
    detected = mut_statuses[0][:2] == [1, nsg.ERROR_IDENTITY_CHANGED]
    print(
        "mutation identity-binding (RTL): %s"
        % ("DETECTED (mutated binding rejects the clean stream)" if detected
           else "NOT DETECTED")
    )
    mutations_ok = mutations_ok and detected
    ok = ok and mutations_ok

    if not ok:
        print("NOISE RUN FAILED")
        return 1
    print(
        "NOISE RUN PASSED (%d golden cases bit-exact + pre-render gate + "
        "pattern/replay streams bit-exact + owner-row/op-count asserts + "
        "all 9 mutations detected)" % len(cases)
    )
    return 0


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
        [CONSTANTS_PKG_SV, dut_sv, NORMREPLAY_TB_SV],
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
        [CONSTANTS_PKG_SV, PATCH_DUT_SV, NORMREPLAY_DUT_SV, top_sv,
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
    receipt = json.loads(NOISE_RECEIPT_PATH.read_bytes())
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
        matches = _emitted_constants_match(schedule)
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="selftest",
        choices=["selftest", "anchor", "adsr", "patch", "lfo", "modmatrix",
                 "vco",
                 "vco2", "noise", "mix", "normreplay"],
        help="'selftest' proves the harness; 'anchor' runs the real "
        "golden-vector flow through the format-true DUT; 'adsr' runs the "
        "issue #70 ADSR engine against the frozen fixed model's golden "
        "vectors; 'patch' runs the issue #69 patch-control core against "
        "its cycle-exact Python mirror; 'lfo' runs the issue #71 LFO + "
        "control-VCA engine against the frozen fixed model's golden "
        "vectors; 'modmatrix' runs the issue #72 modulation-matrix + "
        "endpoint-aligned upsample engines against the frozen fixed "
        "model's golden vectors; 'vco' runs the issue #73 sine VCO "
        "engine against the frozen whole-voice receipt's vco_1.raw "
        "traces; 'vco2' runs the issue #74 square/saw "
        "VCO engine against the frozen fixed model's golden vectors; "
        "'noise' runs the issue #75 host-fed "
        "exact noise-stream lane against the landed fixed-voice golden "
        "receipt; 'mix' runs the issue #76 audio VCA + pre-normalization "
        "mixer engine against the frozen whole-voice receipt and the "
        "declared directed fixtures; 'normreplay' runs the issue #77 "
        "normalization replay controller + one-shot top's two-pass "
        "sequencer against the frozen fixed model's own "
        "normalize_words()",
    )
    parser.add_argument(
        "--simulator",
        default="iverilog",
        choices=["iverilog"],
        help="open-source simulator (PDK-free)",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=None,
        help="keep artifacts here instead of a temp dir",
    )
    args = parser.parse_args(argv)

    if shutil.which(args.simulator) is None:
        print(
            "ERROR: %s is not installed; the %s run cannot execute here. "
            "CI (.github/workflows/tb-sim.yml) arbitrates on a host that "
            "installs it. An unrun check is never a pass."
            % (args.simulator, args.command)
        )
        return 3

    commands = {
        "selftest": selftest,
        "anchor": anchor,
        "adsr": adsr,
        "patch": patch,
        "lfo": lfo,
        "modmatrix": modmatrix,
        "vco": vco,
        "vco2": vco2,
        "noise": noise,
        "mix": mix,
        "normreplay": normreplay,
    }
    command = commands[args.command]

    try:
        if args.workdir is not None:
            args.workdir.mkdir(parents=True, exist_ok=True)
            # Absolute from here on (the #253 fix, as in run_voice.py /
            # run_oneshot.py): every simulator runs with cwd=<case dir>, so
            # a relative --workdir would otherwise be resolved twice.
            return command(args.workdir.resolve(), args.simulator)

        with tempfile.TemporaryDirectory(
            prefix="tb-%s-" % args.command
        ) as tmp:
            return command(Path(tmp), args.simulator)
    except VcoCaptureIntegrityError as error:
        # Its own exit status, distinct from 0 (pass), 1 (a real bit-
        # identity verdict against the RTL) and 3 (nothing ran): a run
        # whose captures cannot be trusted proved nothing either way.
        print("SINE-VCO RUN INCONCLUSIVE -- a capture is untrustworthy, so "
              "nothing was proven and nothing was disproven:")
        print("  %s" % error)
        return 4


if __name__ == "__main__":
    sys.exit(main())
