#!/usr/bin/env python3
"""Issue #79: the integrated WHOLE-VOICE one-shot top, end to end.

``python3 tb/run_voice.py [--profile regression|full] [--workdir DIR]``

Drives ``tb/sv/one_shot_voice_top.sv`` -- every landed Epic #2 RTL engine
(#70 ADSR x6, #71 LFO/control-VCA x2, #72 mod matrix + 5 upsample columns,
#73 sine VCO, #74 square/saw VCO, #75 noise lane, #76 VCAs + mixer, #77
normalization replay controller) composed into one clip renderer -- over
the frozen fixed model's own cases, and:

1. compares **every declared checkpoint** of ``FixedVoiceModel.render()``
   sample by sample and tick by tick, exactly (integer equality; no
   tolerance exists anywhere in this file -- ``float_tolerance`` is
   recorded as ``null``);
2. localizes the first mismatch by trace / cycle / sample and retains the
   raw artifacts of any failed case;
3. proves two clean simulations artifact-hash identical;
4. requires every planted negative control to be DETECTED -- and, unlike
   the tail-chain increment (``tb/run_oneshot.py``), **every control here
   is a genuine RTL mutation**, including the parameter-shuffle, wrong-
   noise, interpolation and gain classes, because the RTL that owns each
   of those faults is now composed in. Binding faults are demonstrated on
   a declared *binding-distinct* stimulus, because the uniform
   ``special:stress`` parameters cannot distinguish the two things a
   binding fault confuses (see :func:`voice_binding_overrides`).

Separate entry point from ``tb/run_tb.py`` for the same reason
``run_oneshot.py`` is: it is not a lane of the #78 aggregate qualification
gate, whose lint ledger is calibrated to CI's toolchain. See
``spec/ONESHOT-E2E.md`` for the acceptance ledger, the runtime/regression
partition and the evidence commands.

Scope honesty: the host still supplies the ratified host-replayed shadow
words (DR-0010's 2026-09-22 amendment), the S1 entry words, the exact C8
noise bytes and the phase enables. No synthesis, layout, signoff,
hardware-playback or sound-fidelity claim is made or implied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_tb import (  # noqa: E402
    ADSR_DUT_SV, CONSTANTS_PKG_SV, LFO_DUT_SV, MIX_DUT_SV, MIX_FIXTURE_CASES,
    MIX_RTL_MUTATIONS, MM_DUT_SV, MM_UP_DUT_SV, NOISE_DUT_SV,
    NORMREPLAY_ALWAYS_OFF_MUTANT, NORMREPLAY_ALWAYS_ON_ANCHOR,
    NORMREPLAY_ALWAYS_ON_MUTANT, NORMREPLAY_DUT_SV,
    NORMREPLAY_WRONG_PEAK_ANCHOR, NORMREPLAY_WRONG_PEAK_MUTANT,
    NORMREPLAY_WRONG_RECIPROCAL_ANCHOR, NORMREPLAY_WRONG_RECIPROCAL_MUTANT,
    ROOT, TB_ROOT, VCO2_DUT_SV, VCO_DUT_SV, VCO_LUT_SV, VCO_RECEIPT_PATH,
    ChoiceNotAccepted, StickyCounters, _run, ag, dr, lgo, mix_expected_ops,
    mix_load_fixtures, mix_load_receipt, mm, mutate_sv, mx, nrg,
    quantize_params, vc, vg, write_lut_memh,
)
from torchsynth_voice.format_sweep import (  # noqa: E402
    CONTROL_SAMPLES, FixedControlPath, MOD_MATRIX_INPUTS, MOD_MATRIX_OUTPUTS,
)

VOICE_TOP_SV = TB_ROOT / "sv/one_shot_voice_top.sv"
VOICE_TB_SV = TB_ROOT / "sv/tb_one_shot_voice.sv"
#: Every engine the integrated top composes, in compile order.
VOICE_ENGINE_SV = (
    ADSR_DUT_SV, LFO_DUT_SV, MM_DUT_SV, MM_UP_DUT_SV, VCO_LUT_SV, VCO_DUT_SV,
    VCO2_DUT_SV, NOISE_DUT_SV, MIX_DUT_SV, NORMREPLAY_DUT_SV,
)
AUDIO_SAMPLES = 176400
NOISE_BYTES = AUDIO_SAMPLES * 4

#: Run profiles (the declared runtime/regression partition). Both walk the
#: complete 176,400-sample clip twice for every *committed* case; the
#: mutation simulations use the declared prefix cap below, exactly as the
#: #72/#73/#74/#76 module lanes do.
VOICE_PROFILE = "regression"
VOICE_REGRESSION_CASES = (
    "normalization:below",              # bypass branch, param-committed
    "voice:divide-distinct-levels",     # divide branch, three distinct levels
)
#: Per-route modulation depths for the binding-distinct fixture below. Every
#: one of the twenty words is distinct, and the two PITCH rows are pushed to
#: opposite ends of the range so a pitch-route fault is not merely a small
#: perturbation of a nearly equal word.
VOICE_BINDING_DEPTHS = {
    "vco_1_pitch": (0.07, 0.11, 0.13, 0.17),
    "vco_1_amp":   (0.23, 0.29, 0.31, 0.37),
    "vco_2_pitch": (0.89, 0.91, 0.93, 0.97),
    "vco_2_amp":   (0.41, 0.43, 0.47, 0.53),
    "noise_amp":   (0.59, 0.61, 0.67, 0.71),
}
#: Per-instance envelope formations for the same fixture, in the model's own
#: prefix order: (attack, decay, sustain, release). All six differ, and every
#: attack is shorter than the prefix-capped mutation walk (6,000 audio samples
#: = 60 control ticks = 0.136 s), so an envelope-role fault diverges inside
#: the cap rather than only in the release tail.
VOICE_BINDING_ENVELOPES = {
    "adsr_1":          (0.02, 0.10, 0.75, 0.20),
    "adsr_2":          (0.05, 0.15, 0.60, 0.30),
    "lfo_1_rate_adsr": (0.01, 0.08, 0.90, 0.12),
    "lfo_1_amp_adsr":  (0.03, 0.12, 0.45, 0.25),
    "lfo_2_rate_adsr": (0.04, 0.06, 0.80, 0.18),
    "lfo_2_amp_adsr":  (0.06, 0.20, 0.30, 0.35),
}


def voice_binding_overrides() -> dict:
    """``special:stress`` overrides that make every binding observable.

    ``special:stress`` is deliberately uniform: all twenty modulation depths
    are 1.0, all six envelope parameter sets are identical and the two LFO
    sides are identical. That uniformity makes a whole class of genuine RTL
    binding faults -- a permuted matrix source column, a swapped LFO
    envelope role, a swapped amplitude or pitch route -- produce *identical*
    output, so those mutations survive on it. (They did: the first run of
    this lane reported four NOT DETECTED controls for exactly this reason.)
    This map breaks every such symmetry.
    """

    overrides = {}
    for route, row in VOICE_BINDING_DEPTHS.items():
        for source, depth in zip(MOD_MATRIX_INPUTS, row):
            overrides["mod_matrix." + source + "->" + route] = depth
    for prefix, (attack, decay, sustain, release) in (
            VOICE_BINDING_ENVELOPES.items()):
        overrides[prefix + ".attack"] = attack
        overrides[prefix + ".decay"] = decay
        overrides[prefix + ".sustain"] = sustain
        overrides[prefix + ".release"] = release
    # The two LFO sides must differ in rate, depth, phase and shape mix, or
    # a swapped LFO source column is unobservable.
    overrides.update({
        "lfo_2.frequency": 7.0, "lfo_2.mod_depth": 12.0,
        "lfo_2.initial_phase": 0.25,
        "lfo_2.sin": 0.25, "lfo_2.tri": 0.0, "lfo_2.saw": 1.0,
        "lfo_2.rsaw": 0.75, "lfo_2.sqr": 0.5,
        # Three distinct mixer level words (as the divide case uses), so a
        # level-lane permutation would also be observable here.
        "mixer.vco_2": 0.75, "mixer.noise": 0.5,
    })
    return overrides


#: Declared derived fixtures: a base directed fixture plus physical-map
#: overrides. The receipt's own divide-branch cases are digest-custody
#: ``global-*`` corpus items whose physical parameters are never committed,
#: so no stimulus can be built from them -- same reason the tail-chain lane
#: declares its own derived divide case.
VOICE_DERIVED_CASES = {
    "voice:divide-distinct-levels": (
        "special:stress", {"mixer.vco_2": 0.75, "mixer.noise": 0.5},
    ),
    "voice:binding-distinct": ("special:stress", voice_binding_overrides()),
}
#: The divide-branch case every mutation is demonstrated on (it divides, so
#: a gain/normalization fault is load-bearing, and it has three distinct
#: level words so a route permutation is observable), and the bypass-branch
#: case the always-on normalization mutation needs.
VOICE_MUTATION_DIVIDE_CASE = "voice:divide-distinct-levels"
VOICE_MUTATION_BYPASS_CASE = "normalization:below"
#: The case every *binding* fault is demonstrated on. A binding fault is only
#: observable against a stimulus that distinguishes the two things it
#: confuses, which ``special:stress`` (and therefore the divide case derived
#: from it) does not -- see :func:`voice_binding_overrides`.
VOICE_MUTATION_BINDING_CASE = "voice:binding-distinct"
#: Controls demonstrated on the binding case rather than the divide case.
VOICE_BINDING_CONTROLS = (
    "matrix-column-shuffle", "lfo-envelope-swap", "amp-route-swap",
    "pitch-column-load-swap", "vco-pitch-wire-swap", "wrong-noise",
)
#: The case whose two clean simulations must be artifact-hash identical.
VOICE_HASH_CASE = "voice:divide-distinct-levels"
#: Mutation-simulation prefix cap. Every control below demonstrably bites
#: inside this many audio samples; committed-case runs always walk the full
#: 176,400. The control walk is never capped (it is 1,764 ticks).
VOICE_MUTATION_WALK_CAP = 6000
#: Back-to-back replay prefix cap (state-leak evidence, not a length claim).
VOICE_REPLAY_WALK_CAP = 6000
#: Sample the missing-sample link mutation drops, and where it must localize.
VOICE_DROP_SAMPLE = 1000
VOICE_ARTIFACTS = ("ctlcap", "audiocap", "linkcap", "outcap", "status", "ops")
#: Where raw artifacts of a failed committed run are retained.
VOICE_RETAIN_PREFIX = "voice-failed-"

# ---------------------------------------------------------------------------
# Anchored RTL mutation seams in the integrated top. mutate_sv refuses if an
# anchor moved, so a seam that drifts fails loudly instead of silently
# becoming an undetectable control.
# ---------------------------------------------------------------------------
VOICE_TOP_MUTATIONS = {
    # Genuine RTL parameter shuffle: the matrix's four source columns are
    # rotated against the pinned (adsr_1, adsr_2, lfo_1, lfo_2) order the
    # twenty depth words are packed in.
    "matrix-column-shuffle": (
        "    wire signed [C1_WIDTH-1:0] col_adsr_1 = adsr_env_arr[0];\n"
        "    wire signed [C1_WIDTH-1:0] col_adsr_2 = adsr_env_arr[1];\n"
        "    wire signed [C1_WIDTH-1:0] col_lfo_1  = lfo_post_arr[0];\n"
        "    wire signed [C1_WIDTH-1:0] col_lfo_2  = lfo_post_arr[1];",
        "    wire signed [C1_WIDTH-1:0] col_adsr_1 = adsr_env_arr[1];\n"
        "    wire signed [C1_WIDTH-1:0] col_adsr_2 = adsr_env_arr[0];\n"
        "    wire signed [C1_WIDTH-1:0] col_lfo_1  = lfo_post_arr[1];\n"
        "    wire signed [C1_WIDTH-1:0] col_lfo_2  = lfo_post_arr[0];"
        "  // MUTANT: matrix source columns permuted",
    ),
    # Genuine RTL control-path binding fault: the rate envelope and the
    # control-VCA gain envelope are swapped on both LFO sides.
    "lfo-envelope-swap": (
        "    assign lfo_rate_env[0] = adsr_env_arr[2];\n"
        "    assign lfo_rate_env[1] = adsr_env_arr[3];\n"
        "    assign lfo_gain[0]     = adsr_env_arr[4];\n"
        "    assign lfo_gain[1]     = adsr_env_arr[5];",
        "    assign lfo_rate_env[0] = adsr_env_arr[4];\n"
        "    assign lfo_rate_env[1] = adsr_env_arr[5];\n"
        "    assign lfo_gain[0]     = adsr_env_arr[2];\n"
        "    assign lfo_gain[1]     = adsr_env_arr[3];"
        "  // MUTANT: LFO rate/gain envelope roles swapped",
    ),
    # Genuine RTL gain fault: the vco_1 and noise amplitude columns are
    # swapped at the control/audio-rate crossing.
    "amp-route-swap": (
        "    wire signed [C1_WIDTH-1:0] amp_vco_1      = up_arr[1];",
        "    wire signed [C1_WIDTH-1:0] amp_vco_1      = up_arr[4];"
        "  // MUTANT: vco_1 VCA driven by the noise amplitude column",
    ),
    # Genuine RTL route fault at the control/audio-rate crossing: the two
    # pitch route words land in each other's upsample column memory, so both
    # pitch columns carry the wrong route. Observable directly on the
    # exported control_upsample.*_pitch traces.
    "pitch-column-load-swap": (
        "                .column_word    (mm_out_arr[gu]),",
        "                .column_word    (mm_out_arr[(gu == 0) ? 2 : "
        "((gu == 2) ? 0 : gu)]),"
        "  // MUTANT: the two pitch route words land in each other's column",
    ),
    # Genuine RTL route fault one stage later: vco_1's pitch *wire* is driven
    # by the vco_2 pitch column. This one is NOT observable on any compared
    # sample trace, and that is a property of the ratified architecture, not
    # of this bench: the frequency both VCOs integrate is the host-replayed
    # exp2 shadow word (DR-0010's 2026-09-22 amendment), so ``up_pitch``'s
    # only consumer inside the engine is the C4 MIDI sum, which is not an
    # output. It is still caught -- by the engine's own declared op-count
    # conformance surface, where the wrong column changes the measured MIDI
    # clamp count (``ops.V1.*[5]``). Closing the trace-level gap would need
    # the #73 engine to export its MIDI sum, which is an RTL change to a
    # qualified module and therefore out of this verification issue's scope.
    "vco-pitch-wire-swap": (
        "    wire signed [C1_WIDTH-1:0] up_pitch_vco_1 = up_arr[0];",
        "    wire signed [C1_WIDTH-1:0] up_pitch_vco_1 = up_arr[2];"
        "  // MUTANT: vco_1 pitch wire driven by the vco_2 pitch column",
    ),
    # Genuine RTL wrong-noise fault: the mixer consumes the previous noise
    # sample (a one-sample lag on the exact C8 stream).
    "wrong-noise": (
        "    wire signed [C1_WIDTH-1:0] raw_noise_bound = noise_sample_word;",
        "    reg signed [C1_WIDTH-1:0] noise_prev_r;\n"
        "    always @(posedge clk) begin\n"
        "        if (rst) noise_prev_r <= {C1_WIDTH{1'b0}};\n"
        "        else if (mix_en) noise_prev_r <= noise_sample_word;\n"
        "    end\n"
        "    wire signed [C1_WIDTH-1:0] raw_noise_bound = noise_prev_r;"
        "  // MUTANT: the mixer consumes the previous noise sample",
    ),
    # Genuine RTL missing-sample fault at the mixer -> replay link seam.
    "missing-sample": (
        "    wire link_valid = mix_out_valid;",
        "    reg [31:0] drop_count;\n"
        "    always @(posedge clk) begin\n"
        "        if (rst) drop_count <= 32'd0;\n"
        "        else if (mix_out_valid) drop_count <= drop_count + 32'd1;\n"
        "    end\n"
        "    wire link_valid = mix_out_valid && !(drop_count == 32'd%d);"
        "  // MUTANT: one sample dropped at the link" % VOICE_DROP_SAMPLE,
    ),
}
#: Genuine RTL interpolation fault, inside the upsample engine itself (the
#: declared ZOH seam the #72 module lane also mutates).
VOICE_ZOH_ANCHOR = "                    audio_word  <= q[C1_WIDTH-1:0];"
VOICE_ZOH_MUTANT = (
    "                    audio_word  <= left[C1_WIDTH-1:0];"
    "  // MUTANT: zero-order hold instead of the endpoint-aligned blend"
)

#: Trace names, in the order ``voice_rows`` compares them. Control-rate
#: traces carry 1,764 ticks; audio-rate traces 176,400 samples per pass.
VOICE_CTL_TRACES = (
    "adsr_1.output", "adsr_2.output",
    "lfo_1_rate_adsr.output", "lfo_2_rate_adsr.output",
    "lfo_1_amp_adsr.output", "lfo_2_amp_adsr.output",
    "lfo_1.raw", "lfo_2.raw",
    "lfo_1.post_control_vca", "lfo_2.post_control_vca",
) + tuple("mod_matrix." + route for route in MOD_MATRIX_OUTPUTS)
VOICE_AUDIO_TRACES = (
    tuple("control_upsample." + route for route in MOD_MATRIX_OUTPUTS)
    + ("vco_1.raw", "vco_2.raw", "noise.raw",
       "vco_1.post_vca", "vco_2.post_vca", "noise.post_vca",
       "mixer.pre_normalization")
)
VOICE_STATUS_NAMES = (
    "error", "error_code", "norm.peak", "norm.gain", "branch", "done",
    "pass_index", "norm.compares", "norm.selects", "norm.recip_divs",
    "norm.mults", "norm.narrows", "norm.saturations", "mixer.peak_feed",
)


def _u32(value: int) -> int:
    return int(value) & 0xFFFFFFFF


def _halves(word: int):
    word = int(word) & ((1 << 64) - 1)
    return word & 0xFFFFFFFF, (word >> 32) & 0xFFFFFFFF


def voice_interior_count(walk: int) -> int:
    """Interior (blended) samples in the first ``walk`` upsample positions."""

    num_unit = CONTROL_SAMPLES - 1
    den = AUDIO_SAMPLES - 1
    interior = 0
    rem = 0
    for _j in range(walk):
        if rem != 0:
            interior += 1
        rem = rem + num_unit - den if rem >= den - num_unit else rem + num_unit
    return interior


# ---------------------------------------------------------------------------
# Derivation: one case's whole-voice stimulus and golden expectations
# ---------------------------------------------------------------------------
def voice_derive_case(formats, fcp, case_id: str, physical: dict,
                      normalized: dict = None, frozen: dict = None,
                      sound_index: int = 0) -> dict:
    """Stimulus + golden truth for one whole-voice case.

    Every expected word is the frozen composed model's own
    (``mix_golden.render_model`` -> ``FixedVoiceModel.render()``). Every
    stimulus word is either an S1 entry word of the model's own
    quantization or a declared host-replayed shadow word whose mirror is
    asserted equal to the model's corresponding trace here -- so a drift in
    any mirror refuses the run rather than silently weakening the
    comparison. When the receipt commits this case's mixer trace digests,
    the model's rows are additionally pinned to them.
    """

    traces, diagnostics = mx.render_model(
        formats, physical, sound_index, normalized
    )
    counters = StickyCounters()
    words = quantize_params(physical, formats.midi, formats.mode, counters)

    # --- #70: six envelope engines -------------------------------------
    eps60 = ag.eps60_word(formats.mode)
    adsr_cases = []
    for prefix in ag.ADSR_PREFIXES:
        formation = ag.derive_formation(fcp, words, prefix)
        shadow = {}
        for stage in ("attack", "decay", "release"):
            start_q = (
                formation["attack_q"] if stage == "decay"
                else (formation["duration_q"] if stage == "release" else 0)
            )
            _values, shape_words = ag.mirror_ramp(
                fcp, formation[stage + "_q"], formation[stage + "_exact"],
                start_q, stage != "attack", formation["alpha"],
            )
            shadow[stage] = shape_words
        golden = fcp._adsr(words, prefix)
        if golden != traces[ag.trace_name(prefix)]:
            raise SystemExit(
                "envelope mirror drift on %s/%s" % (case_id, prefix)
            )
        combined = ag.combine_from_shadow(
            fcp, shadow["attack"], shadow["decay"], shadow["release"],
            formation["sustain_q"],
        )
        if combined != golden:
            raise SystemExit(
                "combine-from-shadow replication drifted on %s/%s"
                % (case_id, prefix)
            )
        adsr_cases.append({
            "prefix": prefix,
            "formation": formation,
            "shadow": shadow,
            "sustain_entry": words[prefix + "sustain"],
        })

    # --- #71: two LFO + control-VCA engines ----------------------------
    lfo_cases = []
    for side in lgo.LFO_SIDES:
        weights = lgo.weight_shadow(fcp, words, side)
        raw, clamps = lgo.mirror_lfo(
            fcp, words, side, traces[side[:-1] + "_rate_adsr.output"], weights
        )
        post = lgo.mirror_vca(
            fcp, raw, traces[side[:-1] + "_amp_adsr.output"]
        )
        if raw != traces[lgo.raw_trace(side)]:
            raise SystemExit("LFO mirror drift on %s/%s" % (case_id, side))
        if post != traces[lgo.vca_trace(side)]:
            raise SystemExit(
                "control-VCA mirror drift on %s/%s" % (case_id, side)
            )
        lfo_cases.append({
            "side": side,
            "freq": words[side + "frequency"],
            "depth": words[side + "mod_depth"],
            "init": lgo.init_word(fcp, words, side),
            "weights": weights,
            "clamps": clamps,
        })

    # --- #72: twenty depth words, route-major in the pinned source order
    depths = {
        route: [
            words["mod_matrix." + source + "->" + route]
            for source in MOD_MATRIX_INPUTS
        ]
        for route in MOD_MATRIX_OUTPUTS
    }
    matrix, matrix_counters = mm.mirror_mod_matrix(
        fcp, words,
        [traces["adsr_1.output"], traces["adsr_2.output"],
         traces["lfo_1.post_control_vca"], traces["lfo_2.post_control_vca"]],
    )
    for route in MOD_MATRIX_OUTPUTS:
        if matrix[route] != traces["mod_matrix." + route]:
            raise SystemExit(
                "mod-matrix mirror drift on %s/%s" % (case_id, route)
            )
    matrix_sats = sum(
        record["count"] for record in matrix_counters["records"]
        if record["kind"] == "saturation"
    )

    # --- #73/#74: the two audio sources and their declared shadow streams
    midi_f0_word = traces["keyboard.midi_f0"][0]
    vco1_tuning = vg.entry_quantize(
        float(physical["vco_1.tuning"]), formats.midi, counters,
        "s1.entry:vco_1.tuning",
    )
    vco1_depth = vg.entry_quantize(
        float(physical["vco_1.mod_depth"]), formats.midi, counters,
        "s1.entry:vco_1.mod_depth",
    )
    vco1_init = vg.initial_phase_word(
        physical["vco_1.initial_phase"], formats.phase_width
    )
    sine_streams, sine_aux = vg.mirror_sine_lane(
        formats, midi_f0_word, vco1_tuning, vco1_depth, vco1_init,
        traces["control_upsample.vco_1_pitch"],
    )
    if sine_streams["vco"] != traces["vco_1.raw"]:
        raise SystemExit("sine-lane mirror drift on %s" % case_id)

    vco2_words = vc.entry_words(formats, physical, counters)
    vco2_words["keyboard.midi_f0"] = midi_f0_word
    partials = vc.partials_constant(
        formats, midi_f0_word, vco2_words["vco_2.tuning"],
        vco2_words["vco_2.mod_depth"], counters,
    )
    vco2_init = vc.initial_phase_word(formats, physical["vco_2.initial_phase"])
    vco2_streams = vc.mirror_square_saw_vco(
        formats, vco2_words, partials, vco2_init,
        traces["control_upsample.vco_2_pitch"],
    )
    if vco2_streams["v2"] != traces["vco_2.raw"]:
        raise SystemExit("square/saw mirror drift on %s" % case_id)

    # Prefix-exact saturation/clamp tallies for the declared capped walks.
    # Without them a capped walk leaves the two VCO engines' sticky
    # saturation and MIDI-clamp counters unchecked -- and that op-count
    # conformance surface is the ONLY place the vco-pitch-wire-swap control
    # is observable, because the frequency the engines integrate is the
    # host-replayed exp2 shadow word. Re-running each mirror over the first
    # ``cap`` pitch words is exact, not an estimate: both are strictly
    # sequential per-sample walks.
    prefix_aux = {}
    for cap in sorted({VOICE_MUTATION_WALK_CAP, VOICE_REPLAY_WALK_CAP}):
        _prefix_sine, prefix_sine_aux = vg.mirror_sine_lane(
            formats, midi_f0_word, vco1_tuning, vco1_depth, vco1_init,
            traces["control_upsample.vco_1_pitch"][:cap],
        )
        prefix_vco2 = vc.mirror_square_saw_vco(
            formats, vco2_words, partials, vco2_init,
            traces["control_upsample.vco_2_pitch"][:cap],
        )
        prefix_aux[cap] = {
            "sine_sats": sum(
                record["count"]
                for record in prefix_sine_aux["counters"]["records"]
                if record["kind"] == "saturation"
            ),
            "sine_clamps": prefix_sine_aux["clamps"],
            "vco2_sats": prefix_vco2["counters"]["total_saturation"],
        }

    # --- #75: the exact C8 byte stream --------------------------------
    from torchsynth_voice import float_sources as fs
    noise_bytes = fs.NoiseSource.resolve(sound_index)
    if len(noise_bytes) != NOISE_BYTES:
        raise SystemExit("resolved noise clip is not %d bytes" % NOISE_BYTES)

    # --- #76: the three level words and the mixer-lane mirror ---------
    levels = mx.level_words(formats, physical, counters)
    raw = {
        "vco_1": traces["vco_1.raw"],
        "vco_2": traces["vco_2.raw"],
        "noise": traces["noise.raw"],
    }
    amp = {
        "vco_1": traces["control_upsample.vco_1_amp"],
        "vco_2": traces["control_upsample.vco_2_amp"],
        "noise": traces["control_upsample.noise_amp"],
    }
    mix_streams, mix_aux = mx.mirror_mix_lane(formats, raw, amp, levels)
    if mix_streams["mix"] != traces["mixer.pre_normalization"]:
        raise SystemExit("mixer-lane mirror drift on %s" % case_id)

    # --- #77: the branch, the peak and the gain -----------------------
    norm_counters = StickyCounters()
    norm_out, norm_diag = nrg.mirror_normalize(
        traces["mixer.pre_normalization"], formats, norm_counters
    )
    if norm_out != traces["mixer.output"]:
        raise SystemExit("normalization mirror drift on %s" % case_id)

    if frozen is not None:
        for name in mx.MIX_TRACES:
            digest = mx.digest_words(traces[name])
            if digest != frozen["traces"][name]:
                raise SystemExit(
                    "frozen-trace drift for %s on %s: rendered %s but the "
                    "receipt declares %s"
                    % (name, case_id, digest, frozen["traces"][name])
                )
        if bool(norm_diag["normalized_branch"]) != bool(
                frozen["branch"]["fixed"]):
            raise SystemExit(
                "frozen branch drift for %s" % case_id
            )

    return {
        "id": case_id,
        "frozen": frozen is not None,
        "sound_index": sound_index,
        "traces": traces,
        "diagnostics": diagnostics,
        "eps60": eps60,
        "adsr": adsr_cases,
        "lfo": lfo_cases,
        "depths": depths,
        "matrix_sats": matrix_sats,
        "statics": {
            "midi_f0": midi_f0_word,
            "vco1_tuning": vco1_tuning,
            "vco1_depth": vco1_depth,
            "vco1_init": vco1_init,
            "vco2_tuning": vco2_words["vco_2.tuning"],
            "vco2_depth": vco2_words["vco_2.mod_depth"],
            "vco2_shape": vco2_words["vco_2.shape"],
            "vco2_partials": partials.word,
            "vco2_init": vco2_init,
        },
        "levels": levels,
        "fq1": sine_streams["fq"],
        "fq2": vco2_streams["fq"],
        "square_q": vco2_streams["square_q"],
        "left_q": vco2_streams["left_q"],
        "sine_aux": sine_aux,
        "prefix_aux": prefix_aux,
        "vco2_sats": vco2_streams["counters"]["total_saturation"],
        "mix_truth": {"streams": mix_streams, "aux": mix_aux},
        "noise_bytes": noise_bytes,
        "norm_diag": norm_diag,
        "norm_counters": norm_counters,
        "keyboard": {
            "midi_f0": traces["keyboard.midi_f0"][0],
            "duration": traces["keyboard.duration"][0],
        },
    }


def voice_write_case(workdir: Path, run: int, case: dict,
                     walk: int = AUDIO_SAMPLES) -> None:
    """Write one run's stimulus files for the integrated top's bench."""

    full = 1 if walk == AUDIO_SAMPLES else 0
    (workdir / ("run%d_walk.txt" % run)).write_text(
        "%d %d\n" % (walk, full), encoding="utf-8")
    s = case["statics"]
    eps_lo, eps_hi = _halves(case["eps60"])
    (workdir / ("run%d_params.txt" % run)).write_text(
        " ".join(str(v) for v in (
            _u32(s["midi_f0"]), _u32(s["vco1_tuning"]), _u32(s["vco1_depth"]),
            _u32(s["vco1_init"]), _u32(s["vco2_tuning"]), _u32(s["vco2_depth"]),
            _u32(s["vco2_shape"]), _u32(s["vco2_partials"]), _u32(s["vco2_init"]),
            _u32(case["levels"]["vco_1"]), _u32(case["levels"]["vco_2"]),
            _u32(case["levels"]["noise"]),
            case["sound_index"], case["sound_index"] % 32, eps_lo, eps_hi,
        )) + "\n", encoding="utf-8")

    lines = []
    for entry in case["adsr"]:
        f = entry["formation"]
        row = []
        for name in ("duration_q", "attack_q", "decay_q", "release_q"):
            lo, hi = _halves(int(f[name]))
            row.extend((lo, hi))
        row.extend((
            1 if f["duration_zero"] else 0, 1 if f["attack_zero"] else 0,
            1 if f["decay_zero"] else 0, 1 if f["release_zero"] else 0,
        ))
        row.append(_u32(entry["sustain_entry"]))
        lines.append(" ".join(str(v) for v in row))
    (workdir / ("run%d_adsr.txt" % run)).write_text(
        "".join(line + "\n" for line in lines), encoding="utf-8")

    lines = []
    for entry in case["lfo"]:
        row = [_u32(entry["freq"]), _u32(entry["depth"]), _u32(entry["init"])]
        row.extend(_u32(w) for w in entry["weights"])
        lines.append(" ".join(str(v) for v in row))
    (workdir / ("run%d_lfo.txt" % run)).write_text(
        "".join(line + "\n" for line in lines), encoding="utf-8")

    (workdir / ("run%d_depths.txt" % run)).write_text(
        "".join(
            " ".join(str(_u32(w)) for w in case["depths"][route]) + "\n"
            for route in MOD_MATRIX_OUTPUTS
        ), encoding="utf-8")

    rows = []
    for tick in range(CONTROL_SAMPLES):
        for entry in case["adsr"]:
            for stage in ("attack", "decay", "release"):
                rows.append("%08x" % _u32(entry["shadow"][stage][tick]))
    (workdir / ("run%d_shadow.txt" % run)).write_text(
        "\n".join(rows) + "\n", encoding="utf-8")

    for name, stream in (("fq1", case["fq1"]), ("fq2", case["fq2"]),
                         ("sqq", case["square_q"]), ("lqq", case["left_q"])):
        (workdir / ("run%d_%s.txt" % (run, name))).write_text(
            "\n".join("%08x" % _u32(w) for w in stream[:walk]) + "\n",
            encoding="utf-8")

    (workdir / ("run%d_bytes.txt" % run)).write_text(
        "\n".join("%02x" % b for b in case["noise_bytes"][:walk * 4]) + "\n",
        encoding="utf-8")


def voice_simulate(workdir: Path, runs: int, sources: dict = None) -> list:
    """Compile + run the integrated top's bench; return per-run captures.

    ``sources`` maps a pristine source path to its mutated replacement.
    """

    sources = sources or {}
    (workdir / "runs.txt").write_text("%d\n" % runs, encoding="utf-8")
    # Two distinct tables: the accepted C5 Q1.22 audio-path table the
    # #73/#74 engines read (+lut), and the control path's Q1.23 table the
    # #71 LFO engines read (+ctl_lut). Composing both lanes in one
    # elaboration is exactly why the LFO engine prefers +ctl_lut.
    lut = workdir / "quarter_wave_audio.memh"
    ctl_lut = workdir / "quarter_wave_control.memh"
    write_lut_memh(_table("audio"), lut)
    write_lut_memh(_table("control"), ctl_lut)
    files = [CONSTANTS_PKG_SV] + [
        sources.get(path, path) for path in VOICE_ENGINE_SV
    ] + [sources.get(VOICE_TOP_SV, VOICE_TOP_SV), VOICE_TB_SV]
    vvp = workdir / "voice.vvp"
    _run(["iverilog", "-g2012", "-o", str(vvp)] + [str(f) for f in files],
         cwd=workdir)
    _run(["vvp", "-n", str(vvp), "+lut=%s" % lut.name,
          "+ctl_lut=%s" % ctl_lut.name], cwd=workdir)

    captures = []
    for run in range(runs):
        def rows(name, width):
            out = []
            for line in (workdir / ("run%d_%s.txt" % (run, name))).read_text(
                    encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                fields = line.split()
                if len(fields) != width:
                    raise SystemExit(
                        "%s row %r does not carry %d fields" % (name, line, width)
                    )
                try:
                    out.append([int(f) for f in fields])
                except ValueError:
                    out.append(None)   # an x-state emission: a mismatch
            return out

        ctl = rows("ctlcap", 16)
        audio = rows("audiocap", 15)
        link = rows("linkcap", 3)
        capture = {
            "ctl": ctl,
            # Split by the HOST's own pass number, not the replay
            # controller's pass_index: a prefix-capped walk never reaches
            # the controller's pass boundary, so its pass_index stays 1.
            # The controller's own pass_index travels in column 2 and is
            # checked against the host's on full-length walks.
            "audio1": [r for r in audio if r is None or r[1] == 1],
            "audio2": [r for r in audio if r is not None and r[1] == 2],
            "link1": [r for r in link if r is None or r[1] == 1],
            "link2": [r for r in link if r is not None and r[1] == 2],
            "out": [tuple(r) if r else (None, None) for r in rows("outcap", 2)],
        }
        status = (workdir / ("run%d_status.txt" % run)).read_text(
            encoding="utf-8").split()
        capture["status"] = [
            int(v) if v.lstrip("-").isdigit() else None for v in status
        ]
        ops = {}
        for line in (workdir / ("run%d_ops.txt" % run)).read_text(
                encoding="utf-8").splitlines():
            if not line.strip():
                continue
            fields = line.split()
            if fields[0] in ("A", "L"):
                key = (fields[0], int(fields[1]))
                rest = fields[2:]
            elif fields[0] == "U":
                key = ("U", int(fields[1]), int(fields[2]))
                rest = fields[3:]
            elif fields[0] == "M":
                key = ("M",)
                rest = fields[1:]
            else:
                key = (fields[0], int(fields[1]))
                rest = fields[2:]
            ops[key] = [
                int(v) if v.lstrip("-").isdigit() else None for v in rest
            ]
        capture["ops"] = ops
        captures.append(capture)
    return captures


#: The two quarter-wave tables the composition needs, keyed "audio" (the
#: accepted C5 Q1.22 table the #73/#74 engines read) and "control" (the
#: frozen control path's Q1.23 table the #71 LFO engines read). Set once
#: from the accepted register in :func:`voice`; never defaulted, so a
#: simulate call that somehow precedes the refusal gate fails loudly
#: instead of elaborating against an unaccepted table.
_LUT_TABLES = {}


def _table(which: str):
    if which not in _LUT_TABLES:
        raise SystemExit(
            "the %r quarter-wave table has not been loaded; the refusal "
            "gate must run before any simulation" % which
        )
    return _LUT_TABLES[which]


# ---------------------------------------------------------------------------
# Expectations and exact comparison
# ---------------------------------------------------------------------------
def voice_expected(case: dict, walk: int) -> dict:
    """The model's expected captures for one walk length. No tolerance."""

    traces = case["traces"]
    diag = case["norm_diag"]
    normalized = bool(diag["normalized_branch"])
    n = walk
    full = walk == AUDIO_SAMPLES
    # Column-oriented expectations: each entry is a slice of the model's own
    # trace list, so no per-sample row objects are built (a full-length case
    # compares 27 named traces x 176,400 samples).
    ctl = {name: traces[name] for name in VOICE_CTL_TRACES}
    audio = {name: traces[name][:n] for name in VOICE_AUDIO_TRACES}
    out = traces["mixer.output"] if full else None
    status = [
        0, 0, diag["peak_word"], diag["gain_word"],
        1 if normalized else 0, 1, 3,
        n, n, 1 if normalized else 0,
        n if normalized else 0, n if normalized else 0,
        case["norm_counters"].total(), diag["peak_word"],
    ] if full else None

    interior = voice_interior_count(n)
    coord_steps = n - 1 if full else n
    sine_sats = sum(
        record["count"]
        for record in case["sine_aux"]["counters"]["records"]
        if record["kind"] == "saturation"
    )
    # Prefix-exact tallies where the walk is one of the declared caps; None
    # (unchecked) only for a walk length nothing precomputed.
    prefix = None if full else case.get("prefix_aux", {}).get(n)
    exp_sine_sats = sine_sats if full else (
        prefix["sine_sats"] if prefix else None)
    exp_sine_clamps = case["sine_aux"]["clamps"] if full else (
        prefix["sine_clamps"] if prefix else None)
    exp_vco2_sats = case["vco2_sats"] if full else (
        prefix["vco2_sats"] if prefix else None)
    ops = {}
    for index in range(6):
        ops[("A", index)] = [
            4 * CONTROL_SAMPLES, 2 * CONTROL_SAMPLES,
            3 * CONTROL_SAMPLES, 3 * CONTROL_SAMPLES,
        ]
    for index in range(2):
        ops[("L", index)] = [
            6 * CONTROL_SAMPLES, 10 * CONTROL_SAMPLES, CONTROL_SAMPLES,
            case["lfo"][index]["clamps"],
        ]
    ops[("M",)] = [
        20 * CONTROL_SAMPLES, 15 * CONTROL_SAMPLES, 5 * CONTROL_SAMPLES,
        case["matrix_sats"],
    ]
    for pas in (1, 2):
        for route in range(5):
            ops[("U", pas, route)] = [
                interior, interior, interior, interior, coord_steps, None, n,
            ]
        ops[("V1", pas)] = [
            3 * n, 7 * n, 4 * n, n, exp_sine_sats, exp_sine_clamps,
        ]
        ops[("V2", pas)] = [8 * n, 8 * n, 7 * n, exp_vco2_sats]
        ops[("MIX", pas)] = (
            mix_expected_ops(n, case["mix_truth"]) if full else
            [6 * n, 2 * n, 4 * n, None, None, n, 0]
        )
        ops[("NZ", pas)] = [4 * n, n, n, 0, 0, case["sound_index"] % 32]
    return {
        "ctl": ctl, "audio": audio, "out": out, "status": status,
        "ops": ops, "walk": n, "full": full,
    }


def voice_rows(capture: dict, expected: dict) -> list:
    """Every first-mismatch row: (trace, cycle, sample, expected, actual).

    Exact integer comparison throughout -- there is no tolerance anywhere
    in this function. Each named trace is scanned independently and its
    first differing position reported; sequence rows are then ordered by
    the capture's own cycle number, so ``rows[0]`` is the earliest
    observable divergence in simulated time.
    """

    rows = []

    def seq(trace, got_rows, want, cycle_col, value_col):
        """Compare one named trace, reporting its first divergence."""

        for index in range(max(len(got_rows), len(want))):
            g = got_rows[index] if index < len(got_rows) else None
            w = want[index] if index < len(want) else None
            got = None if g is None else g[value_col]
            if got is None or w is None or got != w:
                rows.append((
                    trace,
                    None if (g is None or cycle_col is None) else g[cycle_col],
                    index, w, got,
                ))
                return

    # Control-rate traces (one tick per row; the tick index is column 0).
    for col, name in enumerate(VOICE_CTL_TRACES):
        seq(name, capture["ctl"], expected["ctl"][name], None, col + 1)
    # Audio-rate traces, both passes (DR-0010 P4: pass 2 re-renders).
    for pas, key, link in ((1, "audio1", "link1"), (2, "audio2", "link2")):
        for col, name in enumerate(VOICE_AUDIO_TRACES):
            seq("%s[pass%d]" % (name, pas), capture[key],
                expected["audio"][name], 0, col + 3)
        # What the replay controller actually consumed at the link seam.
        seq("link.replay_input[pass%d]" % pas, capture[link],
            expected["audio"]["mixer.pre_normalization"], 0, 2)
    if expected["out"] is not None:
        seq("mixer.output", capture["out"], expected["out"], 0, 1)
    timed = sorted(rows, key=lambda row: (row[1] is None, row[1] or 0))

    found = list(timed)
    if expected["full"]:
        # On a full-length walk the replay controller's own pass_index must
        # agree with the host's pass number on every captured sample.
        for pas, key in ((1, "audio1"), (2, "audio2")):
            for row in capture[key]:
                if row is None or row[2] != pas:
                    found.append((
                        "pass_index[pass%d]" % pas,
                        None if row is None else row[0], None, pas,
                        None if row is None else row[2],
                    ))
                    break
    if expected["status"] is not None:
        got_status = capture["status"]
        for name, want, got in zip(
                VOICE_STATUS_NAMES, expected["status"], got_status):
            if got != want:
                found.append(("status." + name, None, None, want, got))
        if len(got_status) != len(expected["status"]):
            found.append(("status.width", None, None,
                          len(expected["status"]), len(got_status)))
    for key, want in sorted(expected["ops"].items(), key=lambda kv: str(kv[0])):
        got = capture["ops"].get(key)
        if got is None or len(got) != len(want):
            found.append(("ops." + ".".join(str(p) for p in key), None, None,
                          want, got))
            continue
        for position, (w, g) in enumerate(zip(want, got)):
            if w is not None and w != g:
                found.append((
                    "ops.%s[%d]" % (".".join(str(p) for p in key), position),
                    None, None, w, g,
                ))
    return found


def voice_print_rows(label: str, rows: list, cap: int = 6) -> None:
    for trace, cycle, sample, want, got in rows[:cap]:
        print("VOICE FAILED: %s first mismatch: trace=%s cycle=%s sample=%s "
              "expected=%s actual=%s"
              % (label, trace, cycle, sample, want, got))
    if len(rows) > cap:
        print("VOICE FAILED: %s ... and %d further mismatch rows"
              % (label, len(rows) - cap))


def voice_artifact_hashes(workdir: Path, runs: int = 1) -> dict:
    """sha256 of every raw artifact one clean simulation produced."""

    hashes = {}
    for run in range(runs):
        for name in VOICE_ARTIFACTS:
            path = workdir / ("run%d_%s.txt" % (run, name))
            hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def voice_tool_identity() -> dict:
    """Exact commit/tool identity for an evidence record (no PDK, no float)."""

    def git(*args):
        try:
            return subprocess.run(
                ["git", "-C", str(ROOT)] + list(args), check=True,
                capture_output=True, text=True,
            ).stdout.strip()
        except Exception:  # noqa: BLE001 - identity is best effort, flagged
            return "unavailable"

    try:
        iverilog = subprocess.run(
            ["iverilog", "-V"], capture_output=True, text=True
        ).stdout.splitlines()[0]
    except Exception:  # noqa: BLE001
        iverilog = "unavailable"

    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    return {
        "git_head": git("rev-parse", "HEAD"),
        "git_tree_dirty": bool(git("status", "--porcelain", "--", "tb", "src",
                                   "spec/reference")),
        "iverilog": iverilog,
        "rtl_sha256": {
            path.name: digest(path)
            for path in (CONSTANTS_PKG_SV,) + VOICE_ENGINE_SV
            + (VOICE_TOP_SV, VOICE_TB_SV)
        },
        "fixed_vector_sha256": {
            "fixed-voice-golden-v1.json": digest(VCO_RECEIPT_PATH),
            "directed-voice-v1.json": digest(dr.MANIFEST_PATH),
        },
    }


# ---------------------------------------------------------------------------
# The flow
# ---------------------------------------------------------------------------
def voice(workdir: Path) -> int:
    """Issue #79 flow: the integrated whole-voice top vs the frozen model."""

    profile = VOICE_PROFILE
    try:
        receipt, formats, cases = mix_load_receipt()
    except ChoiceNotAccepted as error:
        print("VOICE REFUSED: accepted register refused: %s" % error)
        return 1
    _manifest, fixtures = mix_load_fixtures()
    fcp = FixedControlPath(formats.control_spec)
    _LUT_TABLES["audio"] = formats.table
    _LUT_TABLES["control"] = fcp.table

    if profile == "full":
        plan = [(cid, cases[cid]) for cid in sorted(cases)]
        plan += [(cid, None) for cid in MIX_FIXTURE_CASES]
        plan += [(cid, None) for cid in sorted(VOICE_DERIVED_CASES)]
    else:
        plan = [(cid, cases.get(cid)) for cid in VOICE_REGRESSION_CASES]

    print("VOICE profile %s: %d cases through the INTEGRATED WHOLE-VOICE top "
          "(1,764-tick control walk + two 176,400-sample audio passes each); "
          "frozen receipt bindings verified (DR-0008 %s)"
          % (profile, len(plan), receipt["bindings"]["dr_0008_status"]))
    print("VOICE engines composed: %s"
          % ", ".join(path.stem for path in VOICE_ENGINE_SV))

    ok = True
    derived = {}
    divide_seen = bypass_seen = False

    # 1. Committed cases: every declared checkpoint bit-exact over the full
    #    176,400-sample clip, both passes, plus exact status/op sequences.
    for case_id, frozen in plan:
        case_dir = workdir / ("case-" + case_id.replace(":", "_"))
        case_dir.mkdir(parents=True, exist_ok=True)
        if case_id in VOICE_DERIVED_CASES:
            base_id, overrides = VOICE_DERIVED_CASES[case_id]
            physical = dict(fixtures[base_id]["physical"])
            physical.update(overrides)
            normalized = fixtures[base_id]["normalized"]
        elif frozen is None:
            physical = fixtures[case_id]["physical"]
            normalized = fixtures[case_id]["normalized"]
        else:
            physical = frozen["parameters"]
            normalized = None
        case = voice_derive_case(
            formats, fcp, case_id, physical, normalized, frozen
        )
        derived[case_id] = case
        expected = voice_expected(case, AUDIO_SAMPLES)
        voice_write_case(case_dir, 0, case)
        capture = voice_simulate(case_dir, 1)[0]
        rows = voice_rows(capture, expected)
        counts = (
            len(capture["ctl"]), len(capture["audio1"]),
            len(capture["audio2"]), len(capture["out"]),
        )
        count_ok = counts == (CONTROL_SAMPLES, AUDIO_SAMPLES, AUDIO_SAMPLES,
                              AUDIO_SAMPLES)
        if not count_ok:
            print("VOICE FAILED: %s capture counts ctl/pass1/pass2/out = "
                  "%d/%d/%d/%d (need %d/%d/%d/%d)"
                  % ((case_id,) + counts + (CONTROL_SAMPLES, AUDIO_SAMPLES,
                                            AUDIO_SAMPLES, AUDIO_SAMPLES)))
        voice_print_rows(case_id, rows)
        case_ok = count_ok and not rows
        if not case_ok:
            keep = Path(tempfile.mkdtemp(prefix=VOICE_RETAIN_PREFIX))
            shutil.copytree(case_dir, keep / case_dir.name)
            print("VOICE: raw artifacts of failed case %s retained at %s"
                  % (case_id, keep / case_dir.name))
        ok = ok and case_ok
        divide_seen = divide_seen or bool(case["norm_diag"]["normalized_branch"])
        bypass_seen = bypass_seen or not case["norm_diag"]["normalized_branch"]
        print("case %s: 1764 ticks + 176400 samples x 2 passes over %d named "
              "traces, branch %s, peak %d, gain %d, frozen-pinned %s -> %s"
              % (case_id, len(VOICE_CTL_TRACES) + len(VOICE_AUDIO_TRACES) + 1,
                 "divide" if case["norm_diag"]["normalized_branch"] else "bypass",
                 case["norm_diag"]["peak_word"], case["norm_diag"]["gain_word"],
                 frozen is not None, "OK" if case_ok else "FAIL"))
    branches_ok = divide_seen and bypass_seen
    print("branch coverage: divide %s, bypass %s -> %s"
          % (divide_seen, bypass_seen, "OK" if branches_ok else "FAIL"))
    ok = ok and branches_ok

    # 1b. The binding case. Every *binding* control below needs a stimulus
    #     that distinguishes the two things the fault confuses; the uniform
    #     `special:stress` parameters do not (see voice_binding_overrides).
    #     In the `full` profile this case is one of the committed
    #     full-length cases above; in `regression` it is derived here. Either
    #     way the pristine RTL is proven bit-exact on it over the declared
    #     prefix cap FIRST, so a later DETECTED verdict is attributable to the
    #     planted mutation rather than to a pre-existing divergence.
    if VOICE_MUTATION_BINDING_CASE not in derived:
        base_id, overrides = VOICE_DERIVED_CASES[VOICE_MUTATION_BINDING_CASE]
        physical = dict(fixtures[base_id]["physical"])
        physical.update(overrides)
        derived[VOICE_MUTATION_BINDING_CASE] = voice_derive_case(
            formats, fcp, VOICE_MUTATION_BINDING_CASE, physical,
            fixtures[base_id]["normalized"], None,
        )
    binding = derived[VOICE_MUTATION_BINDING_CASE]
    baseline_dir = workdir / "binding-baseline"
    baseline_dir.mkdir(parents=True, exist_ok=True)
    voice_write_case(baseline_dir, 0, binding, walk=VOICE_MUTATION_WALK_CAP)
    baseline_rows = voice_rows(
        voice_simulate(baseline_dir, 1)[0],
        voice_expected(binding, VOICE_MUTATION_WALK_CAP),
    )
    voice_print_rows("%s baseline" % VOICE_MUTATION_BINDING_CASE, baseline_rows)
    baseline_ok = not baseline_rows
    print("binding baseline %s: pristine RTL bit-exact over the first %d "
          "audio samples (every binding distinguishable: 20 distinct "
          "modulation depths, 6 distinct envelopes, 2 distinct LFO sides) "
          "-> %s"
          % (VOICE_MUTATION_BINDING_CASE, VOICE_MUTATION_WALK_CAP,
             "OK" if baseline_ok else "FAIL"))
    ok = ok and baseline_ok

    # 2. Two clean simulations are artifact-hash identical (determinism).
    digests = []
    for attempt in (1, 2):
        d = workdir / ("hash-%d" % attempt)
        d.mkdir(parents=True, exist_ok=True)
        voice_write_case(d, 0, derived[VOICE_HASH_CASE])
        voice_simulate(d, 1)
        digests.append(voice_artifact_hashes(d))
    hash_ok = digests[0] == digests[1]
    print("artifact hashes %s: two clean full-length simulations %s "
          "(%d files) -> %s"
          % (VOICE_HASH_CASE, "identical" if hash_ok else "DIFFER",
             len(digests[0]), "OK" if hash_ok else "FAIL"))
    ok = ok and hash_ok

    # 3. Back-to-back renders with no reset in between: no datapath state
    #    (phase accumulators, envelope indices, column memories, peak
    #    trackers) may leak from run 0 into run 1. Prefix-capped: this is
    #    state-leak evidence, not a clip-length claim.
    replay_dir = workdir / "replay"
    replay_dir.mkdir(parents=True, exist_ok=True)
    first, second = VOICE_MUTATION_BYPASS_CASE, VOICE_HASH_CASE
    for index, cid in enumerate((first, second)):
        voice_write_case(replay_dir, index, derived[cid],
                         walk=VOICE_REPLAY_WALK_CAP)
    pair = voice_simulate(replay_dir, 2)
    solo_dir = workdir / "solo"
    solo_dir.mkdir(parents=True, exist_ok=True)
    voice_write_case(solo_dir, 0, derived[second], walk=VOICE_REPLAY_WALK_CAP)
    solo = voice_simulate(solo_dir, 1)[0]
    capped = {cid: voice_expected(derived[cid], VOICE_REPLAY_WALK_CAP)
              for cid in (first, second)}
    replay_ok = (
        bool(pair[1]["audio1"])
        and [row[3:] for row in pair[1]["audio1"]]
        == [row[3:] for row in solo["audio1"]]
        and [row[1:] for row in pair[1]["ctl"]]
        == [row[1:] for row in solo["ctl"]]
        and not voice_rows(pair[0], capped[first])
        and not voice_rows(pair[1], capped[second])
    )
    print("replay: %s then %s back-to-back (no reset, %d-sample prefix) "
          "reproduces the solo run and both match the model -> %s"
          % (first, second, VOICE_REPLAY_WALK_CAP,
             "OK" if replay_ok else "FAIL"))
    ok = ok and replay_ok

    # 4. Negative controls: every planted RTL fault MUST be detected.
    divide = derived[VOICE_MUTATION_DIVIDE_CASE]
    bypass = derived[VOICE_MUTATION_BYPASS_CASE]
    expect_cache = {}

    def expectation(case, walk):
        key = (case["id"], walk)
        if key not in expect_cache:
            expect_cache[key] = voice_expected(case, walk)
        return expect_cache[key]

    mutants = []

    def sim_mutant(label, case, target, anchor, replacement, localize=None,
                   walk=VOICE_MUTATION_WALK_CAP):
        mut_dir = workdir / ("mut-" + label)
        mut_dir.mkdir(parents=True, exist_ok=True)
        path = mut_dir / ("mutant_" + target.name)
        path.write_text(
            mutate_sv(target.read_text(encoding="utf-8"), anchor,
                      replacement, label),
            encoding="utf-8",
        )
        voice_write_case(mut_dir, 0, case, walk=walk)
        capture = voice_simulate(mut_dir, 1, sources={target: path})[0]
        rows = voice_rows(capture, expectation(case, walk))
        detected = bool(rows)
        first_row = rows[0] if rows else None
        if localize is not None and detected:
            located = (first_row[0], first_row[2]) == localize
            if not located:
                print("VOICE FAILED: mutation %s detected but not localized "
                      "(first row trace=%s sample=%s; need trace=%s sample=%s)"
                      % ((label, first_row[0], first_row[2]) + localize))
                detected = False
        print("mutation %s (RTL, case %s, %d-sample walk): %s%s"
              % (label, case["id"], walk,
                 "DETECTED (test fails the mutant)" if detected
                 else "NOT DETECTED",
                 "; first mismatch trace=%s cycle=%s sample=%s expected=%s "
                 "actual=%s" % first_row if first_row else ""))
        mutants.append((label, detected, walk, case["id"]))
        return detected

    mut_ok = True
    # Binding faults: demonstrated on the binding case, whose stimulus
    # distinguishes every source column, envelope role and route. Each bites
    # inside the declared prefix cap -- five on a named sample trace, and
    # vco-pitch-wire-swap on the vco_1 engine's MIDI-clamp op counter (the
    # host-replayed exp2 shadow hides it from every sample trace).
    for label in VOICE_BINDING_CONTROLS:
        anchor, replacement = VOICE_TOP_MUTATIONS[label]
        mut_ok = sim_mutant(label, binding, VOICE_TOP_SV, anchor,
                            replacement) and mut_ok
    anchor, replacement = VOICE_TOP_MUTATIONS["missing-sample"]
    mut_ok = sim_mutant(
        "missing-sample", divide, VOICE_TOP_SV, anchor, replacement,
        localize=("link.replay_input[pass1]", VOICE_DROP_SAMPLE),
    ) and mut_ok
    mut_ok = sim_mutant("interpolation-zoh", divide, MM_UP_DUT_SV,
                        VOICE_ZOH_ANCHOR, VOICE_ZOH_MUTANT) and mut_ok
    mut_ok = sim_mutant("mixer-truncate", divide, MIX_DUT_SV,
                        *MIX_RTL_MUTATIONS["mixer-truncate"]) and mut_ok
    # The normalization faults live entirely in the replay controller, whose
    # branch decision is only reached at sample 176,400 -- they are therefore
    # demonstrated on a FULL-LENGTH walk, never on a prefix.
    for label, case, anchor, replacement in (
        ("normalization-always-off", divide, NORMREPLAY_ALWAYS_ON_ANCHOR,
         NORMREPLAY_ALWAYS_OFF_MUTANT),
        ("normalization-always-on", bypass, NORMREPLAY_ALWAYS_ON_ANCHOR,
         NORMREPLAY_ALWAYS_ON_MUTANT),
        ("normalization-wrong-reciprocal", divide,
         NORMREPLAY_WRONG_RECIPROCAL_ANCHOR,
         NORMREPLAY_WRONG_RECIPROCAL_MUTANT),
        ("normalization-wrong-peak", divide, NORMREPLAY_WRONG_PEAK_ANCHOR,
         NORMREPLAY_WRONG_PEAK_MUTANT),
    ):
        mut_ok = sim_mutant(label, case, NORMREPLAY_DUT_SV, anchor,
                            replacement, walk=AUDIO_SAMPLES) and mut_ok
    ok = ok and mut_ok

    record = {
        "schema": "gf180-torchsynth/oneshot-whole-voice-evidence-v1",
        "profile": profile,
        "result": "PASS" if ok else "FAIL",
        "cases": [cid for cid, _ in plan],
        "case_count": len(plan),
        "samples_per_case": AUDIO_SAMPLES,
        "passes_per_case": 2,
        "control_ticks_per_case": CONTROL_SAMPLES,
        "named_traces_compared": (
            list(VOICE_CTL_TRACES) + list(VOICE_AUDIO_TRACES)
            + ["mixer.output", "mixer.peak", "mixer.gain"]
        ),
        "host_fed_checkpoints": ["keyboard.midi_f0", "keyboard.duration"],
        "float_tolerance": None,
        "branch_coverage": {"divide": divide_seen, "bypass": bypass_seen},
        "mutations": {
            label: {
                "verdict": "DETECTED" if det else "NOT DETECTED",
                "walk": walk,
                "case": cid,
            }
            for label, det, walk, cid in mutants
        },
        "mutation_kind": "all-RTL",
        "mutation_walk_cap": VOICE_MUTATION_WALK_CAP,
        "replay_walk_cap": VOICE_REPLAY_WALK_CAP,
        "binding_case": VOICE_MUTATION_BINDING_CASE,
        "binding_baseline": "PASS" if baseline_ok else "FAIL",
        "artifact_hashes": digests[0],
        "artifact_hash_case": VOICE_HASH_CASE,
        "identity": voice_tool_identity(),
        "scope": "integrated whole-voice one-shot top: #70 x6, #71 x2, #72 "
                 "(matrix + 5 upsample columns), #73, #74, #75, #76, #77. "
                 "Host supplies the ratified host-replayed shadow words, the "
                 "S1 entry words, the exact C8 noise bytes and the phase "
                 "enables. No synthesis/layout/signoff/hardware claim.",
    }
    (workdir / "voice-evidence.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("VOICE evidence record written to %s"
          % (workdir / "voice-evidence.json"))
    if not ok:
        print("VOICE RUN FAILED")
        return 1
    print("VOICE RUN PASSED (profile %s: %d cases bit-exact over 1764 control "
          "ticks + 2 x 176400 audio samples across %d named traces + exact "
          "status/op sequences + reset/replay independence + two-clean-sim "
          "artifact-hash identity + %d all-RTL mutations detected; integrated "
          "whole-voice top)"
          % (profile, len(plan),
             len(VOICE_CTL_TRACES) + len(VOICE_AUDIO_TRACES) + 1, len(mutants)))
    return 0


def main(argv=None) -> int:
    global VOICE_PROFILE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="regression",
                        choices=["regression", "full"])
    parser.add_argument("--workdir", type=Path, default=None,
                        help="keep raw artifacts here instead of a temp dir")
    args = parser.parse_args(argv)
    VOICE_PROFILE = args.profile
    if shutil.which("iverilog") is None:
        print("ERROR: iverilog is not installed; the whole-voice run cannot "
              "execute here. An unrun check is never a pass.")
        return 3
    if args.workdir is not None:
        args.workdir.mkdir(parents=True, exist_ok=True)
        return voice(args.workdir)
    with tempfile.TemporaryDirectory(prefix="tb-voice-") as tmp:
        return voice(Path(tmp))


if __name__ == "__main__":
    sys.exit(main())
