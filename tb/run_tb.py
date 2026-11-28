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

from torchsynth_voice import directed as dr  # noqa: E402
from torchsynth_voice import golden_vectors as gv  # noqa: E402
from torchsynth_voice import mix_golden as mx  # noqa: E402
from torchsynth_voice import noise_stream_golden as nsg  # noqa: E402
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
from torchsynth_voice.format_sweep import (  # noqa: E402
    quantize_params,  # noqa: F401  (re-exported for tb/run_voice.py)
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


# The normreplay lane reads the constants package, the noise receipt and
# the emission check off the shared core. run_oneshot and run_voice
# import the mutation seams, the DUT path and nrg through run_tb.
import lane_normreplay  # noqa: E402

lane_normreplay.bind_core(sys.modules[__name__])
from lane_normreplay import (  # noqa: E402
    NORMREPLAY_ALWAYS_OFF_MUTANT,  # noqa: F401
    NORMREPLAY_ALWAYS_ON_ANCHOR,  # noqa: F401
    NORMREPLAY_ALWAYS_ON_MUTANT,  # noqa: F401
    NORMREPLAY_DUT_SV,  # noqa: F401
    NORMREPLAY_WRONG_PEAK_ANCHOR,  # noqa: F401
    NORMREPLAY_WRONG_PEAK_MUTANT,  # noqa: F401
    NORMREPLAY_WRONG_RECIPROCAL_ANCHOR,  # noqa: F401
    NORMREPLAY_WRONG_RECIPROCAL_MUTANT,  # noqa: F401
    normreplay,
    nrg,  # noqa: F401
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


MIX_DUT_SV = TB_ROOT / "sv/audio_mix_engine.sv"
MIX_TB_SV = TB_ROOT / "sv/tb_audio_mix_engine.sv"
#: The lane's declared output traces, in the model's registry naming.
MIX_STREAM_TRACES = (
    "vco_1.post_vca",
    "vco_2.post_vca",
    "noise.post_vca",
    "mixer.pre_normalization",
)
#: Declared directed fixtures (spec/reference/directed-voice-v1.json, the
#: manifest the frozen receipt's own case selection draws from). The
#: ``special`` family supplies the fixture classes the receipt's
#: param-committed cases cannot: total silence (every mixer level zero)
#: and the all-source / high-depth stress patch (all three mixer levels,
#: all twenty matrix routes, and every modulation/rate depth at 1.0).
MIX_FIXTURE_CASES = ("special:silence", "special:near-silence", "special:stress")
#: Per-lane curve evidence: the declared case whose recorded (normalized,
#: physical) pair makes that lane's upstream input-curve exponent
#: observable (``physical = normalized ** (1 / curve)``; a pair at 0 or 1
#: observes nothing). Independent per lane: the noise lane's power-40
#: curve and the two oscillators' linear curves are each read from their
#: own case, never inferred from one another.
MIX_CURVE_EVIDENCE = {
    "vco_1": "special:near-silence",
    "vco_2": "source:vco_2",
    "noise": "source:noise",
}
#: Receipt cases carrying the remaining declared fixture classes:
#: single-source isolation per audio path, the two high-depth boundaries,
#: and the declared peak-stress anchor variant.
MIX_SINGLE_SOURCE_CASES = ("source:vco_1", "source:vco_2", "source:noise")
MIX_HIGH_DEPTH_CASES = (
    "boundary:vco_1.mod_depth:upper",
    "boundary:vco_2.mod_depth:upper",
)
MIX_STRESS_CASE = "normalization-stress:anchor-3.9478583336"
#: Replay-independence pair: the level words, the peak register, and the
#: counters are per-trigger; run 0 state must not leak into run 1 (the
#: pair routes different lanes, so a leaked level word is visible).
MIX_REPLAY_PAIR = ("source:noise", "source:vco_1")
#: Mutation-simulation walk cap (+max_n): every mutation below
#: demonstrably bites within the first 24,000 audio samples (~0.54 s), so
#: the mutation sims walk a fraction of the grid. Committed-case and
#: fixture runs always walk the full 176,400 samples.
MIX_MUTATION_WALK_CAP = 24000

#: Anchored RTL mutation seams (mutate_sv refuses if an anchor moved).
MIX_RTL_MUTATIONS = {
    "level-route-swap": (
        "    wire signed [C1_WIDTH-1:0] lvl1_eff = lvl1_r;",
        "    wire signed [C1_WIDTH-1:0] lvl1_eff = lvl2_r;"
        "  // MUTANT: mixer level route swapped",
    ),
    "vca-2-polarity": (
        "    wire signed [95:0] vca2_eff = vca2_sat\n"
        "        ? ((vca2_narrow > 96'sd0) ? C1_MAX96 : C1_MIN96) : vca2_narrow;",
        "    wire signed [95:0] vca2_eff = -(vca2_sat\n"
        "        ? ((vca2_narrow > 96'sd0) ? C1_MAX96 : C1_MIN96) : vca2_narrow);"
        "  // MUTANT: vco_2 VCA polarity flipped",
    ),
    "mixer-dc-offset": (
        "    wire signed [95:0] mix_out = mix_eff;",
        "    wire signed [95:0] mix_out = mix_eff + 96'sd1;"
        "  // MUTANT: DC offset on the mix word",
    ),
    "mixer-truncate": (
        "    wire signed [95:0] mix_narrow =\n"
        "        div_half_even_pow2_signed(acc_sum, C1_FRAC_BITS);",
        "    wire signed [95:0] mix_narrow = (acc_sum >>> C1_FRAC_BITS);"
        "  // MUTANT: truncation instead of half-even",
    ),
    "mixer-wrap": (
        "    wire signed [95:0] mix_eff = mix_sat\n"
        "        ? ((mix_narrow > 96'sd0) ? C1_MAX96 : C1_MIN96) : mix_narrow;",
        "    wire signed [95:0] mix_eff = mix_narrow;"
        "  // MUTANT: C7 saturation dropped at the mixer narrowing",
    ),
    "dropped-noise-source": (
        "    wire signed [95:0] acc_sum = term1 + term2 + termn;",
        "    wire signed [95:0] acc_sum = term1 + term2;"
        "  // MUTANT: noise term dropped from the accumulator",
    ),
}


def mix_load_receipt():
    """The frozen receipt + accepted formats + the #76 declared cases.

    Reuses the sine lane's binding verification verbatim (same receipt,
    same live accepted contract: DR-0008 status, hash-linked C5 table,
    emitted constants package) and additionally requires every case this
    flow names.
    """

    payload, formats, cases = vco_load_receipt()
    required = (
        MIX_SINGLE_SOURCE_CASES + MIX_HIGH_DEPTH_CASES
        + (MIX_STRESS_CASE,) + MIX_REPLAY_PAIR
    )
    for case_id in required:
        if case_id not in cases:
            raise SystemExit(
                "receipt carries no param-committed case %r" % case_id
            )
    return payload, formats, cases


def mix_load_fixtures():
    """The declared directed fixtures (physical + normalized maps).

    The manifest is validated through its own landed validator (identity
    seal + upstream-commit pin), so a drifted manifest refuses the run
    instead of silently feeding a different patch.
    """

    document = json.loads(dr.MANIFEST_PATH.read_text(encoding="utf-8"))
    dr.validate_manifest(document)
    by_id = {case["id"]: case for case in document["cases"]}
    fixtures = {}
    wanted = set(MIX_FIXTURE_CASES) | set(MIX_CURVE_EVIDENCE.values())
    for case_id in sorted(wanted):
        if case_id not in by_id:
            raise SystemExit(
                "declared directed manifest carries no fixture %r" % case_id
            )
        patch = dr.resolve_patch(document, by_id[case_id])
        fixtures[case_id] = {
            "physical": {name: pair["physical"] for name, pair in patch.items()},
            "normalized": {
                name: pair["normalized"] for name, pair in patch.items()
            },
        }
    return document, fixtures


def mix_derive_case(formats, case_id: str, physical: dict,
                    normalized: dict = None, frozen: dict = None):
    """Model-derived stimulus + truth for one case, frozen-pinned if committed.

    ``mx.derive_case`` renders the frozen composed model and refuses
    unless the lane mirror reproduces the model's own rows and per-site
    counter records. When the receipt commits this case's trace digests,
    the model's rows are additionally pinned to them here: the RTL is then
    held to the frozen evidence, not to a fresh render.
    """

    case = mx.derive_case(formats, physical, 0, normalized)
    if frozen is not None:
        for name in mx.MIX_TRACES:
            digest = mx.digest_words(case["model_traces"][name])
            if digest != frozen["traces"][name]:
                raise SystemExit(
                    "frozen-trace drift for %s on %s: rendered %s but the "
                    "receipt declares %s"
                    % (name, case_id, digest, frozen["traces"][name])
                )
    case["id"] = case_id
    case["frozen"] = frozen is not None
    return case


def mix_prefix_truth(formats, case: dict, cap: int):
    """The mirror truth for a capped prefix walk of an already-derived case."""

    streams, aux = mx.mirror_mix_lane(
        formats, case["raw"], case["amp"], case["levels"], samples=cap
    )
    return {"streams": streams, "aux": aux}


def mix_write_case(workdir: Path, run: int, case: dict, levels: dict = None,
                   amp: dict = None):
    """Write one run's stimulus files (level words + per-sample streams).

    ``levels`` / ``amp`` override the derived stimulus for the declared
    stimulus-side mutations (a wrong gain word, a swapped route); the
    committed runs always pass the derived maps.
    """

    use_levels = case["levels"] if levels is None else levels
    use_amp = case["amp"] if amp is None else amp
    (workdir / ("run%d_params.txt" % run)).write_text(
        " ".join(
            str(int(use_levels[lane]) & 0xFFFFFFFF) for lane in mx.LANES
        ) + "\n",
        encoding="utf-8",
    )
    raw = case["raw"]
    lines = []
    for n in range(case["samples"]):
        lines.append(
            "%d %d %d %d %d %d"
            % (
                raw["vco_1"][n] & 0xFFFFFFFF,
                raw["vco_2"][n] & 0xFFFFFFFF,
                raw["noise"][n] & 0xFFFFFFFF,
                use_amp["vco_1"][n] & 0xFFFFFFFF,
                use_amp["vco_2"][n] & 0xFFFFFFFF,
                use_amp["noise"][n] & 0xFFFFFFFF,
            )
        )
    (workdir / ("run%d_streams.txt" % run)).write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def mix_simulate(workdir: Path, simulator: str, runs: int, dut_sv: Path,
                 max_n: int = None) -> list:
    """Compile and run the file-driven tb; return per-run captures."""

    (workdir / "runs.txt").write_text("%d\n" % runs, encoding="utf-8")
    vvp = _compile_iverilog(
        workdir, simulator, "audio_mix.vvp",
        [CONSTANTS_PKG_SV, dut_sv, MIX_TB_SV],
    )
    command = ["vvp", "-n", str(vvp)]
    if max_n is not None:
        command.append("+max_n=%d" % max_n)
    _run(command, cwd=workdir)
    captures = []
    for run in range(runs):
        rows = {
            "vco_1.post_vca": [],
            "vco_2.post_vca": [],
            "noise.post_vca": [],
            "mixer.pre_normalization": [],
            "abs": [],
            "peak": [],
        }
        order = list(rows)
        for line in (
            workdir / ("run%d_captured.txt" % run)
        ).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            fields = line.split()
            if len(fields) != len(order):
                raise SystemExit(
                    "captured row %r does not carry %d words"
                    % (line, len(order))
                )
            for name, raw in zip(order, fields):
                try:
                    rows[name].append(int(raw))
                except ValueError:
                    rows[name].append(None)  # an x-state emission: a mismatch
        cycles = int(
            (workdir / ("run%d_cycles.txt" % run))
            .read_text(encoding="utf-8").strip()
        )
        ops = None
        for line in (
            workdir / ("run%d_ops.txt" % run)
        ).read_text(encoding="utf-8").splitlines():
            if line.strip():
                ops = []
                for raw in line.split()[1:]:
                    try:
                        ops.append(int(raw))
                    except ValueError:
                        ops.append(None)  # an x-state counter: a mismatch
        capture = dict(rows)
        capture["cycles"] = cycles
        capture["ops"] = ops
        captures.append(capture)
    return captures


def mix_expected_streams(truth: dict) -> dict:
    """The mirror's captured-stream expectations, keyed like the capture."""

    streams = truth["streams"]
    return {
        "vco_1.post_vca": streams["post_vca"]["vco_1"],
        "vco_2.post_vca": streams["post_vca"]["vco_2"],
        "noise.post_vca": streams["post_vca"]["noise"],
        "mixer.pre_normalization": streams["mix"],
        "abs": streams["abs"],
        "peak": streams["peak"],
    }


def mix_mismatch_rows(capture: dict, truth: dict, prefix: bool = False) -> list:
    """Every (stream, index, expected, actual) first-mismatch row.

    With ``prefix`` (the capped mutation walks) a shorter capture is not
    itself a mismatch, but any differing sample inside the captured
    prefix is.
    """

    expected = mix_expected_streams(truth)
    rows = []
    for name, want in expected.items():
        got = capture[name]
        if prefix:
            want = want[: len(got)]
        for index in range(max(len(got), len(want))):
            a = got[index] if index < len(got) else None
            b = want[index] if index < len(want) else None
            if a != b:
                rows.append((name, index, b, a))
                break
    return rows


def mix_check_case(capture: dict, truth: dict, case_id: str,
                   prefix: bool = False) -> bool:
    """Sample-exact verification of all six captured streams for one run."""

    rows = mix_mismatch_rows(capture, truth, prefix=prefix)
    for name, index, want, got in rows:
        print(
            "AUDIO-MIX FAILED: %s differs (%s[%d]: expected %s got %s)"
            % (name, case_id, index, want, got)
        )
    return not rows


def mix_expected_ops(walked: int, truth: dict) -> list:
    """Expected exported counters for one full-or-capped walk.

    Static per-sample owner-row ops (DR-0010 #76: 6 mults + 2 adds + 4
    narrow sites); the C7 saturation and C6 rounding totals are the
    mirror's measured per-site counts; the peak compare is one per sample
    (charged to #77's row, exported here); Q6.42 accumulator-band faults
    must be zero.
    """

    aux = truth["aux"]
    return [
        6 * walked,
        2 * walked,
        4 * walked,
        aux["sats"],
        aux["rounds"],
        walked,
        0,
    ]


def check_mix_budget(schedule, walked: int, truth: dict, ops_row: list) -> bool:
    """Op-count conformance to the DR-0010 #76 owner row + emission check.

    DR-0010's owner row for #76: "audio rate: 6 mults + 2 adds + 4 narrow
    sites; 48-bit-class accumulator; S4 rescale to the pre-normalization
    mix word", split in the module table as "3x audio VCA: 3 / 0 / 3" plus
    "Mixer (level mults, accumulate, S4): 3 / 2 / 1". The engine declares
    exactly those counts per audio sample. The peak-feed compare is
    DR-0010's #77 row ("pass 1 peak tracking: 1 compare + 1 abs-select
    folded into the mixer output") and is reported against that row, never
    counted in this lane's adds. The complete clip schedule is asserted:
    the walked sample count must equal the emitted SCHED_SAMPLES_PER_PASS
    over one full pass. The serialized single-MAC cycle mapping remains
    the integration lanes; this is an op-count check, not a PPA/fit claim.
    """

    ok = True
    per_sample = {
        "multiply-class ops (3 VCA + 3 level)": (6, 6),
        "accumulator adds": (2, 2),
        "declared narrowings (3 VCA S4 + 1 mixer S4)": (4, 4),
    }
    for name, (actual, limit) in per_sample.items():
        verdict = "OK" if actual <= limit else "FAIL"
        ok = ok and actual <= limit
        print(
            "  budget: %d %s per sample vs DR-0010 #76 owner-row cap %d -> %s"
            % (actual, name, limit, verdict)
        )
    print(
        "  budget: 1 peak-feed compare per sample -> reported against the "
        "DR-0010 #77 owner row (pass-1 peak tracking), never folded into "
        "this lane's adds"
    )
    print(
        "  accumulator: three 24x24 -> 48-bit products summed exactly in "
        "%s (no intermediate rounding before the declared narrowing); "
        "observed band [%d, %d]"
        % (truth["aux"]["product_format"], truth["aux"]["acc_min"],
           truth["aux"]["acc_max"])
    )
    try:
        matches = _emitted_constants_match(schedule)
        ok = ok and matches
    except Exception as error:  # noqa: BLE001 - reported, never a silent pass
        print("  budget constants: emission failed -> %s" % error)
        return False
    samples_per_pass = sched.constant(schedule, "samples_per_pass")
    complete = (walked == samples_per_pass == gv.CANONICAL_SAMPLE_COUNT)
    print(
        "  complete clip schedule: walked %d samples == emitted "
        "samples_per_pass %d == canonical %d -> %s"
        % (walked, samples_per_pass, gv.CANONICAL_SAMPLE_COUNT,
           "OK" if complete else "FAIL")
    )
    ok = ok and complete
    expected = mix_expected_ops(walked, truth)
    counters_ok = ops_row == expected
    print(
        "  exported counters over the walk: %r vs expected %r -> %s"
        % (ops_row, expected, "OK" if counters_ok else "FAIL")
    )
    return ok and counters_ok


def mix_report_mutation(label: str, kind: str, case_id: str, rows: list,
                        ops_bad: bool, ops_row, expected_traces: set) -> bool:
    """Require one planted fault DETECTED, localized to the declared traces."""

    hit = {name for name, _i, _w, _g in rows}
    detected = bool(rows) or ops_bad
    localized = hit == expected_traces
    surface = []
    if rows:
        surface.append(
            "trace rows %s" % ",".join(
                "%s[%d]" % (name, index) for name, index, _w, _g in rows
            )
        )
    if ops_bad:
        surface.append("property rows (counters %r)" % (ops_row,))
    print(
        "mutation %s (%s, case %s): %s via %s; localization %s "
        "(expected %s)"
        % (
            label, kind, case_id,
            "DETECTED (test fails the mutant)" if detected
            else "NOT DETECTED",
            " + ".join(surface) if surface else "neither",
            "OK" if localized else "FAIL",
            ",".join(sorted(expected_traces)) if expected_traces else "(none)",
        )
    )
    return detected and localized


def mix_run_rtl_mutation(workdir: Path, simulator: str, label: str,
                         case: dict, truth: dict, expected_traces: set) -> bool:
    """Plant one anchored RTL mutation on one case; require DETECTED."""

    anchor, replacement = MIX_RTL_MUTATIONS[label]
    mut_dir = workdir / ("mut-" + label)
    mut_dir.mkdir(parents=True, exist_ok=True)
    mutated = mutate_sv(
        MIX_DUT_SV.read_text(encoding="utf-8"), anchor, replacement, label
    )
    (mut_dir / "audio_mix_engine_mut.sv").write_text(mutated, encoding="utf-8")
    mix_write_case(mut_dir, 0, case)
    captures = mix_simulate(
        mut_dir, simulator, 1, mut_dir / "audio_mix_engine_mut.sv",
        max_n=truth["aux"]["samples"],
    )
    capture = captures[0]
    rows = mix_mismatch_rows(capture, truth, prefix=True)
    walked = len(capture["mixer.pre_normalization"])
    ops_bad = capture["ops"] != mix_expected_ops(walked, truth)
    return mix_report_mutation(
        label, "RTL", case["id"], rows, ops_bad, capture["ops"],
        expected_traces,
    )


def mix_run_stimulus_mutation(workdir: Path, simulator: str, label: str,
                              case: dict, truth: dict, expected_traces: set,
                              levels: dict = None, amp: dict = None) -> bool:
    """Feed one mutated stimulus through the pristine RTL; require DETECTED."""

    mut_dir = workdir / ("mut-" + label)
    mut_dir.mkdir(parents=True, exist_ok=True)
    mix_write_case(mut_dir, 0, case, levels=levels, amp=amp)
    captures = mix_simulate(
        mut_dir, simulator, 1, MIX_DUT_SV, max_n=truth["aux"]["samples"]
    )
    capture = captures[0]
    rows = mix_mismatch_rows(capture, truth, prefix=True)
    walked = len(capture["mixer.pre_normalization"])
    ops_bad = capture["ops"] != mix_expected_ops(walked, truth)
    return mix_report_mutation(
        label, "stimulus", case["id"], rows, ops_bad, capture["ops"],
        expected_traces,
    )


def mix(workdir: Path, simulator: str) -> int:
    """Issue #76 flow: the audio VCA + mixer engine vs the frozen model."""

    try:
        receipt, formats, cases = mix_load_receipt()
    except ChoiceNotAccepted as error:
        print("AUDIO-MIX REFUSED: accepted register refused: %s" % error)
        return 1
    try:
        schedule = sched.require_accepted_schedule()
    except ScheduleNotAccepted as error:
        print(
            "AUDIO-MIX REFUSED: DR-0010 schedule register refused: %s" % error
        )
        return 1
    manifest, fixtures = mix_load_fixtures()

    print(
        "Loaded the frozen whole-voice receipt (%d cases, %d "
        "param-committed), bindings verified (DR-0008 %s, lut %s); plus %d "
        "declared directed fixtures to run and %d curve-evidence patches "
        "from %s (identity %s)"
        % (len(receipt["cases"]), len(cases),
           receipt["bindings"]["dr_0008_status"],
           receipt["bindings"]["lut_sha256"][:12] + "...",
           len(MIX_FIXTURE_CASES),
           len(set(MIX_CURVE_EVIDENCE.values()) - set(MIX_FIXTURE_CASES)),
           dr.MANIFEST_PATH.name,
           manifest["identity"]["sha256"][:12] + "...")
    )

    ok = True
    derived = {}

    # 1. Sample-exact engine runs over the full 176,400-sample clip: every
    #    param-committed receipt case (frozen-pinned) plus the declared
    #    silence / near-silence / all-source-stress fixtures. The exported
    #    op-counter property row must equal the model-measured counts on
    #    every clean run (an x-state counter is a named failure, never a
    #    silent pass).
    plan = [(case_id, cases[case_id]) for case_id in sorted(cases)]
    plan += [(case_id, None) for case_id in MIX_FIXTURE_CASES]
    for case_id, frozen in plan:
        case_dir = workdir / ("case-" + case_id)
        case_dir.mkdir(parents=True, exist_ok=True)
        if frozen is None:
            physical = fixtures[case_id]["physical"]
            normalized = fixtures[case_id]["normalized"]
        else:
            physical = frozen["parameters"]
            normalized = None
        case = mix_derive_case(
            formats, case_id, physical, normalized, frozen
        )
        truth = {"streams": case["streams"], "aux": case["aux"]}
        mix_write_case(case_dir, 0, case)
        captures = mix_simulate(case_dir, simulator, 1, MIX_DUT_SV)
        case_ok = mix_check_case(captures[0], truth, case_id)
        expected_ops = mix_expected_ops(case["samples"], truth)
        if captures[0]["ops"] != expected_ops:
            case_ok = False
            print(
                "AUDIO-MIX FAILED: exported counters %r != expected %r (%s)"
                % (captures[0]["ops"], expected_ops, case_id)
            )
        if not case_ok:
            ok = False
        case["ops"] = captures[0]["ops"]
        aux = case["aux"]
        print(
            "case %s: %d samples, levels %s (frozen %s), sats %d, rounds "
            "%d, peak %d@%d, RTL sample-exact -> %s"
            % (case_id, case["samples"],
               [case["levels"][lane] for lane in mx.LANES],
               case["frozen"], aux["sats"], aux["rounds"], aux["peak_word"],
               aux["peak_index"], "OK" if case_ok else "FAIL")
        )
        derived[case_id] = case

    # 2. The declared fixture classes, named against the cases that carry
    #    them (an unnamed class is never reported as covered).
    silence = derived["special:silence"]
    fixture_rows = [
        ("silence", "special:silence",
         all(word == 0 for word in silence["levels"].values())
         and all(word == 0 for word in silence["streams"]["mix"])
         and silence["aux"]["peak_word"] == 0),
        ("near-silence", "special:near-silence",
         derived["special:near-silence"]["levels"]["vco_1"] > 0
         and derived["special:near-silence"]["aux"]["peak_word"] > 0),
        ("single-source", ",".join(MIX_SINGLE_SOURCE_CASES),
         all(
             sum(1 for lane in mx.LANES if derived[cid]["levels"][lane] != 0) == 1
             for cid in MIX_SINGLE_SOURCE_CASES
         )),
        ("all-source", "special:stress",
         all(
             derived["special:stress"]["levels"][lane] != 0
             for lane in mx.LANES
         )),
        ("high-depth", ",".join(MIX_HIGH_DEPTH_CASES + ("special:stress",)),
         all(derived[cid]["aux"]["rounds"] > 0
             for cid in MIX_HIGH_DEPTH_CASES)),
        ("stress (mixer C7 saturation exercised)",
         "%s,special:stress" % MIX_STRESS_CASE,
         derived["special:stress"]["aux"]["sites"]["mixer.S4"]["saturation"] > 0),
    ]
    for label, carriers, holds in fixture_rows:
        print(
            "fixture class %s: carried by %s -> %s"
            % (label, carriers, "OK" if holds else "FAIL")
        )
        ok = ok and holds

    # 2b. The mixer input curves: the noise lane's recorded upstream curve
    #     is observable in the declared fixtures' own physical/normalized
    #     pair, and it enters the engine through the same S1 entry site as
    #     the oscillator gains (never as an RTL gain law).
    curve_ok = True
    for lane in mx.LANES:
        curve = mx.MIXER_INPUT_CURVES[lane]
        evidence_id = MIX_CURVE_EVIDENCE[lane]
        observed = mx.observed_curve_exponent(
            fixtures[evidence_id]["physical"],
            fixtures[evidence_id]["normalized"],
            lane,
        )
        expected = 1.0 / curve
        holds = observed is not None and abs(observed - expected) <= 1e-6 * expected
        print(
            "  mixer input curve %s: recorded curve %r (exponent %g), "
            "observed exponent %r from %s -> %s"
            % (lane, curve, expected, observed, evidence_id,
               "OK" if holds else "FAIL")
        )
        curve_ok = curve_ok and holds
        if evidence_id in cases:
            name = "mixer." + lane
            bound = (
                fixtures[evidence_id]["physical"][name]
                == cases[evidence_id]["parameters"][name]
            )
            print(
                "    binding: manifest %s physical == the receipt case's own "
                "committed parameter -> %s"
                % (name, "OK" if bound else "FAIL")
            )
            curve_ok = curve_ok and bound
    ok = ok and curve_ok

    # 3. Reset/replay: a second trigger cannot retain prior lane state.
    first, second = MIX_REPLAY_PAIR
    replay_dir = workdir / "replay"
    replay_dir.mkdir(parents=True, exist_ok=True)
    mix_write_case(replay_dir, 0, derived[first])
    mix_write_case(replay_dir, 1, derived[second])
    replay_captures = mix_simulate(replay_dir, simulator, 2, MIX_DUT_SV)
    solo_dir = workdir / "solo"
    solo_dir.mkdir(parents=True, exist_ok=True)
    mix_write_case(solo_dir, 0, derived[second])
    solo_captures = mix_simulate(solo_dir, simulator, 1, MIX_DUT_SV)
    replay_ok = True
    for name in list(MIX_STREAM_TRACES) + ["abs", "peak"]:
        if replay_captures[1][name] != solo_captures[0][name]:
            print(
                "AUDIO-MIX FAILED: run-after-run %s differs from the solo "
                "run - prior lane state leaked" % name
            )
            replay_ok = False
    if replay_captures[1]["ops"] != solo_captures[0]["ops"]:
        print(
            "AUDIO-MIX FAILED: run-after-run counters %r != solo %r - "
            "counters are not per-trigger"
            % (replay_captures[1]["ops"], solo_captures[0]["ops"])
        )
        replay_ok = False
    second_truth = {
        "streams": derived[second]["streams"],
        "aux": derived[second]["aux"],
    }
    replay_ok = replay_ok and mix_check_case(
        replay_captures[1], second_truth, second
    )
    print(
        "reset/replay: trigger-to-trigger back-to-back runs (%s -> %s) "
        "reproduce the solo golden run -> %s"
        % (first, second, "OK" if replay_ok else "FAIL")
    )
    ok = ok and replay_ok

    # 4. Budget: owner-row op counts + emission + complete clip schedule.
    budget_case = derived["source:vco_1"]
    ok = ok and check_mix_budget(
        schedule,
        budget_case["samples"],
        {"streams": budget_case["streams"], "aux": budget_case["aux"]},
        budget_case["ops"],
    )

    # 5. Mutations: each planted fault MUST be detected AND localized.
    cap = MIX_MUTATION_WALK_CAP
    prefix_truth = {
        case_id: mix_prefix_truth(formats, derived[case_id], cap)
        for case_id in (
            "source:vco_1", "source:vco_2", "source:noise",
            "special:silence", "special:stress",
        )
    }
    mutations_ok = True
    mix_only = {"mixer.pre_normalization", "abs", "peak"}

    # 5a. Route: the mixer level words swapped (the only nonzero level
    #     reaches the wrong lane). The VCA outputs are untouched.
    mutations_ok = mix_run_rtl_mutation(
        workdir, simulator, "level-route-swap",
        derived["source:vco_1"], prefix_truth["source:vco_1"], mix_only,
    ) and mutations_ok

    # 5b. Polarity: the vco_2 VCA output negated. A pure sign inversion is
    #     INVISIBLE to the magnitude feed (|mix| and the running peak are
    #     sign-blind, and this case saturates nowhere, so the negated mix
    #     has identical magnitudes), so it must be caught on the signed
    #     lane word and the signed mix word — exactly the localization
    #     asserted here. A lane that only checked the peak feed would miss
    #     it.
    mutations_ok = mix_run_rtl_mutation(
        workdir, simulator, "vca-2-polarity",
        derived["source:vco_2"], prefix_truth["source:vco_2"],
        {"vco_2.post_vca", "mixer.pre_normalization"},
    ) and mutations_ok

    # 5c. DC: a constant offset on the mix word, caught on the silence
    #     fixture where the true mix is identically zero.
    mutations_ok = mix_run_rtl_mutation(
        workdir, simulator, "mixer-dc-offset",
        derived["special:silence"], prefix_truth["special:silence"], mix_only,
    ) and mutations_ok

    # 5d. Rounding: truncation instead of half-even at the mixer narrowing.
    mutations_ok = mix_run_rtl_mutation(
        workdir, simulator, "mixer-truncate",
        derived["source:vco_1"], prefix_truth["source:vco_1"], mix_only,
    ) and mutations_ok

    # 5e. Saturation: C7 saturation dropped, caught on the stress fixture
    #     whose accumulator genuinely exceeds the C1 band.
    mutations_ok = mix_run_rtl_mutation(
        workdir, simulator, "mixer-wrap",
        derived["special:stress"], prefix_truth["special:stress"], mix_only,
    ) and mutations_ok

    # 5f. Route: the noise term dropped from the accumulator, caught on the
    #     noise-only case.
    mutations_ok = mix_run_rtl_mutation(
        workdir, simulator, "dropped-noise-source",
        derived["source:noise"], prefix_truth["source:noise"], mix_only,
    ) and mutations_ok

    # 5g. Gain: the recorded noise input curve dropped — the case's own
    #     NORMALIZED noise level fed as though the lane were linear
    #     (curve 1.0) instead of its recorded power-40 conversion.
    #     Stimulus-side: the curve lives at the measured-observation seam,
    #     never in RTL.
    noise_case = derived["source:noise"]
    counters = StickyCounters()
    dropped_curve = dict(noise_case["levels"])
    dropped_curve["noise"] = entry_quantize(
        float(fixtures["source:noise"]["normalized"]["mixer.noise"]),
        formats.audio, counters, "tb.mix.curve_dropped",
    )
    if dropped_curve["noise"] == noise_case["levels"]["noise"]:
        raise SystemExit(
            "the curve-dropped noise level word equals the committed one; "
            "this mutation would prove nothing"
        )
    mutations_ok = mix_run_stimulus_mutation(
        workdir, simulator, "noise-curve-dropped", noise_case,
        prefix_truth["source:noise"], mix_only, levels=dropped_curve,
    ) and mutations_ok

    # 5h. Gain: a one-ULP noise level word error (the gain is load-bearing
    #     at ULP resolution, not merely at audible scale).
    one_ulp = dict(noise_case["levels"])
    one_ulp["noise"] = one_ulp["noise"] + 1
    mutations_ok = mix_run_stimulus_mutation(
        workdir, simulator, "noise-gain-one-ulp", noise_case,
        prefix_truth["source:noise"], mix_only, levels=one_ulp,
    ) and mutations_ok

    # 5i. Route: the vco_1 / vco_2 amplitude columns swapped upstream (the
    #     #72 lane's routes crossed). Both VCA outputs move; the mix
    #     follows.
    vco1_case = derived["source:vco_1"]
    swapped_amp = dict(vco1_case["amp"])
    swapped_amp["vco_1"], swapped_amp["vco_2"] = (
        vco1_case["amp"]["vco_2"], vco1_case["amp"]["vco_1"]
    )
    mutations_ok = mix_run_stimulus_mutation(
        workdir, simulator, "amp-route-swap", vco1_case,
        prefix_truth["source:vco_1"],
        {"vco_1.post_vca", "vco_2.post_vca"} | mix_only,
        amp=swapped_amp,
    ) and mutations_ok

    ok = ok and mutations_ok

    if not ok:
        print("AUDIO-MIX RUN FAILED")
        return 1
    print(
        "AUDIO-MIX RUN PASSED (frozen-binding + %d receipt cases and %d "
        "declared fixtures sample-exact over the complete clip + "
        "reset/replay independence + owner-row/op-count asserts + all 9 "
        "mutations detected and localized)"
        % (len(cases), len(MIX_FIXTURE_CASES))
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
