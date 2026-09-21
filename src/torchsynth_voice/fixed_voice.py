"""Candidate fixed Voice composition: the whole-voice fixed model (issue #54).

Composes the accepted DR-0008 numeric contract (Accepted by reviewed merge,
2026-09-21) into one whole-voice render with the same topology as the landed
float composition (``torchsynth_voice.float_voice``): the fixed control path
(#50's certified baseline instantiation) feeds the mod-matrix columns and
their endpoint-aligned upsamples into the fixed audio sources (u32 wrapping
phase, Q16.15 frequency words, the C5 quarter-wave table), the exact host-fed
noise stream (C8), the three VCAs and the mixer, and the C9 declared-precision
normalization replay. Every consumed width, scale, rounding mode, and table
geometry is pulled from the machine-readable choice register through
``torchsynth_voice.fixedpoint.choices.require_accepted`` — the model refuses
to construct against any register state that is not accepted.

Declared binary64 shadow sites (open approximation items, the #74 declaration
class; each operates on already-quantized fixed operands and is tallied in
the render diagnostics, never silently):

- the MIDI-to-Hz ``440 * 2^((midi-69)/12)`` conversion (``math.exp2`` on the
  quantized Q10.21 control word; DR-0008 Section 3 op order preserved: the
  [0, 127] clamp is exact integer saturation in the Q10.21 word *before* the
  conversion);
- the vco_2 ``tanh`` distortion term and the ``partials_constant`` scale
  (instantiated exactly like the calibrated #51 sweep so the M1/M2 evidence
  binds);
- the ADSR ``** alpha`` and LFO shape weights, owned by
  ``torchsynth_voice.format_sweep`` and declared there.

The model imports neither TorchSynth nor the float DSP chain: expected
outputs derive from the fixed integer dataflow plus the enumerated shadow
sites above. Determinism is total — same request, same bytes — because every
stage is integer arithmetic, table lookup, or a shadow function of quantized
operands.

This module makes NO RTL claim of any kind: no synthesis, layout, signoff,
hardware playback, or sound-fidelity claim is made or implied. It is the
candidate model whose bit-exact golden vectors future RTL must reproduce.
"""

from __future__ import annotations

import math
import struct
from fractions import Fraction
from typing import Any, Dict, List, Tuple

from . import float_interfaces as fi
from .fixedpoint.choices import ChoiceNotAccepted, require_accepted
from .fixedpoint.counters import StickyCounters
from .fixedpoint.formats import FixedFormat, parse_identity
from .fixedpoint.ops import OverflowPolicy, apply_policy, mul, rescale
from .fixedpoint.phase import PhaseAccumulator
from .fixedpoint.rounding import RoundingMode, div_round
from .float_sources import PI_F32
from .format_sweep import (
    AUDIO_SAMPLES,
    CandidateSpec,
    FixedControlPath,
)

MODEL_IDENTITY = "fixed-voice-v1"
CONTROL_UP_FRAC_BITS = 31

MIDI_A440 = 69.0
SEMITONES_PER_OCTAVE = 12.0
MIDI_CLAMP_MIN = 0
MIDI_CLAMP_MAX = 127
AUDIO_RATE_HZ = fi.AUDIO_RATE_HZ
SAMPLE_COUNT = AUDIO_SAMPLES

SHADOW_EXP2 = "vco.midi_to_hz.exp2.shadow"
SHADOW_TANH = "vco_2.tanh.shadow"
SHADOW_PARTIALS = "vco_2.partials_constant.shadow"
SHADOW_SITES = (SHADOW_EXP2, SHADOW_TANH, SHADOW_PARTIALS)


def _fmt_from_params(params: Dict[str, Any]) -> FixedFormat:
    return FixedFormat(
        signed=bool(params["signed"]),
        int_bits=int(params["int_bits"]),
        frac_bits=int(params["frac_bits"]),
        modular=bool(params.get("modular", False)),
    )


class AcceptedFormats:
    """The accepted C1-C9 instantiation, read as data through the refusal gate.

    Construction refuses any register state that is not accepted; every
    format attribute derives only from the register's own parameters, and
    the emitted quarter-wave table must match the accepted C5 geometry.
    """

    def __init__(self) -> None:
        c1 = require_accepted("C1")
        c2 = require_accepted("C2")
        c3 = require_accepted("C3")
        c4 = require_accepted("C4")
        c5 = require_accepted("C5")
        c6 = require_accepted("C6")
        c9 = require_accepted("C9")
        self.audio = _fmt_from_params(c1["parameters"])
        self.phase_width = int(c2["parameters"]["width"])
        self.phase_units_per_turn = int(c2["parameters"]["units_per_turn"])
        self.frequency = _fmt_from_params(c3["parameters"])
        self.midi = _fmt_from_params(c4["parameters"])
        self.lut_entries = int(c5["parameters"]["n_entries"])
        self.lut_phase_bits = int(c5["parameters"]["phase_bits"])
        self.lut_entry_width = int(c5["parameters"]["entry_width"])
        mode_name = c6["parameters"]["rounding_mode"]
        if mode_name != "half_even":
            raise ChoiceNotAccepted(
                "the composed model implements the accepted half-even "
                "rounding contract; %r has no composed instantiation" % mode_name
            )
        self.mode = RoundingMode.HALF_EVEN
        gain_params = c9["parameters"]
        self.gain = _fmt_from_params(gain_params)
        self.reciprocal_frac_bits = int(gain_params["reciprocal_frac_bits"])
        self.lut_entry_format = FixedFormat(
            signed=True, int_bits=1, frac_bits=self.lut_entry_width - 2
        )
        from .fixedpoint.lut import QuarterWaveSpec, generate_quarter_cos

        self.table = generate_quarter_cos(
            QuarterWaveSpec(
                n_entries=self.lut_entries,
                entry_format=self.lut_entry_format,
                phase_bits=self.lut_phase_bits,
            )
        )
        spec = self.table.spec
        if (
            spec.n_entries != self.lut_entries
            or spec.phase_bits != self.lut_phase_bits
            or spec.entry_format.width != self.lut_entry_width
        ):
            raise ChoiceNotAccepted(
                "generated quarter-wave table geometry disagrees with the "
                "accepted C5 parameters"
            )
        self.control_spec = CandidateSpec(
            midi_format=parse_identity(self.midi.identity),
            ctrl_format=parse_identity(self.audio.identity),
            pitch_format=parse_identity(self.audio.identity),
            up_frac_bits=CONTROL_UP_FRAC_BITS,
            lut_entries=self.lut_entries,
            mode=self.mode,
        )
        self.identities = {
            "audio": self.audio.identity,
            "phase": "u%d:modular" % self.phase_width,
            "frequency": self.frequency.identity,
            "midi": self.midi.identity,
            "gain": self.gain.identity,
            "lut_entries": self.lut_entries,
            "lut_entry_width": self.lut_entry_width,
            "lut_entry_format": self.lut_entry_format.identity,
            "lut_sha256": self.table.sha256(),
            "rounding": "half_even",
            "control_spec": self.control_spec.identity,
            "control_up_frac_bits": CONTROL_UP_FRAC_BITS,
        }

    def to_json(self) -> Dict[str, Any]:
        return {
            "audio": self.audio.to_json(),
            "phase_width": self.phase_width,
            "phase_units_per_turn": self.phase_units_per_turn,
            "frequency": self.frequency.to_json(),
            "midi": self.midi.to_json(),
            "gain": self.gain.to_json(),
            "reciprocal_frac_bits": self.reciprocal_frac_bits,
            "lut_sha256": self.table.sha256(),
            "identities": dict(self.identities),
        }


def entry_quantize(
    value: float, fmt: FixedFormat, counters: StickyCounters, site: str
) -> int:
    """S1-class entry site: host scalar into ``fmt`` at the accepted mode."""

    exact = Fraction(value) * fmt.scale
    word = div_round(exact.numerator, exact.denominator, RoundingMode.HALF_EVEN)
    return apply_policy(word, fmt, OverflowPolicy.SATURATE, counters, site)


def shadow_half_even(value: float) -> int:
    """Half-even round of an exact-in-binary64 scaled product to an integer."""

    return round(value)


def normalize_words(
    mix_words: List[int],
    audio: FixedFormat,
    gain: FixedFormat,
    counters: StickyCounters,
) -> Tuple[List[int], Dict[str, Any]]:
    """The C9 normalization replay in the integer domain (site S5).

    The peak is measured on the pre-normalization mix in its accepted audio
    word; the strict ``peak > 1`` branch applies the declared-precision
    reciprocal (half-even at S5) as a gain-word multiply narrowed once through
    the canonical scalar. Returns the output words plus the peak word, gain
    word, and branch decision.
    """

    peak = 0
    for word in mix_words:
        magnitude = -word if word < 0 else word
        if magnitude > peak:
            peak = magnitude
    one = 1 << audio.frac_bits
    branch = peak > one
    if branch:
        gain_word = div_round(
            1 << (audio.frac_bits + gain.frac_bits), peak, RoundingMode.HALF_EVEN
        )
        if not gain.contains(gain_word):
            raise FixedVoiceError("reciprocal gain word outside its accepted format")
        out = [
            mul(word, audio, gain_word, gain, audio, RoundingMode.HALF_EVEN,
                OverflowPolicy.SATURATE, counters, "norm.S5")
            for word in mix_words
        ]
    else:
        gain_word = 1 << gain.frac_bits
        out = list(mix_words)
    return out, {
        "peak_word": peak,
        "gain_word": gain_word,
        "normalized_branch": branch,
    }


class FixedVoiceError(ValueError):
    """Raised for contract violations in the composed fixed Voice model."""


class FixedVoiceModel:
    """The candidate fixed whole-voice model for one resolved request.

    ``render()`` returns every declared checkpoint keyed by its registry
    name in registry order — audio and control traces as accepted-format
    integer words, the keyboard and mixer scalar traces as one-element word
    lists — plus a ``diagnostics`` mapping (sticky counters, shadow-site
    tallies, normalization branch, provenance) that is not part of the
    checkpoint set.
    """

    def __init__(self, request: fi.ResolvedRequest, formats: AcceptedFormats = None):
        if not isinstance(request, fi.ResolvedRequest):
            raise FixedVoiceError(
                "the fixed model requires a resolved request; use"
                " float_interfaces.ResolvedRequest"
            )
        if request.physical is None:
            raise FixedVoiceError(
                "physical.parameters is a measured seam: the observed runtime"
                " conversion must be supplied, never derived"
            )
        self.formats = formats if formats is not None else AcceptedFormats()
        self.request = request
        self.control = FixedControlPath(self.formats.control_spec)

    def declared_outputs(self) -> List[str]:
        from .float_voice import voice_checkpoints

        return voice_checkpoints()

    def render(self) -> Tuple[Dict[str, List[int]], Dict[str, Any]]:
        formats = self.formats
        audio = formats.audio
        midi_fmt = formats.midi
        counters = StickyCounters()
        shadow_tally = {site: 0 for site in SHADOW_SITES}

        control_words = self.control.render_words(self.request.physical)
        midi_f0_word = control_words["keyboard.midi_f0"][0]
        up_pitch = {
            "vco_1": control_words["control_upsample.vco_1_pitch"],
            "vco_2": control_words["control_upsample.vco_2_pitch"],
        }
        amp_columns = (
            control_words["control_upsample.vco_1_amp"],
            control_words["control_upsample.vco_2_amp"],
            control_words["control_upsample.noise_amp"],
        )

        physical = self.request.physical
        tuning_words = {
            key: entry_quantize(
                physical[key + ".tuning"], midi_fmt, counters, "s1.entry:" + key + ".tuning"
            )
            for key in ("vco_1", "vco_2")
        }
        depth_words = {
            key: entry_quantize(
                physical[key + ".mod_depth"], midi_fmt, counters, "s1.entry:" + key + ".mod_depth"
            )
            for key in ("vco_1", "vco_2")
        }
        shape_word = entry_quantize(
            physical["vco_2.shape"], midi_fmt, counters, "s1.entry:vco_2.shape"
        )

        max_pitch_word = apply_policy(
            midi_f0_word + tuning_words["vco_2"] + max(depth_words["vco_2"], 0),
            midi_fmt, OverflowPolicy.SATURATE, counters, "vco_2.partials.sum",
        )
        shadow_tally[SHADOW_PARTIALS] += 1
        max_pitch_real = max_pitch_word / midi_fmt.scale
        max_f0 = 440.0 * math.exp2((max_pitch_real - MIDI_A440) / SEMITONES_PER_OCTAVE)
        if max_f0 <= 0.0:
            raise FixedVoiceError("nonpositive maximum frequency has no log10")
        partials_real = 12000.0 / (max_f0 * math.log10(max_f0))
        partials_scale = float(PI_F32) * partials_real
        partials_fmt = FixedFormat(signed=True, int_bits=14, frac_bits=17)
        partials_word = apply_policy(
            shadow_half_even(partials_scale * partials_fmt.scale),
            partials_fmt, OverflowPolicy.SATURATE, counters, "partials.q",
        )
        shape_real = shape_word / midi_fmt.scale
        one_minus_half_shape = 1.0 - shape_real / 2.0

        table = formats.table
        entry_fmt = table.spec.entry_format
        lut_scale = float(entry_fmt.scale)
        phase_units = formats.phase_units_per_turn
        freq_scale = formats.frequency.scale
        midi_scale = float(midi_fmt.scale)
        clamp_max_word = MIDI_CLAMP_MAX << midi_fmt.frac_bits
        acc1 = PhaseAccumulator(formats.phase_width)
        acc2 = PhaseAccumulator(formats.phase_width)
        for key, acc in (("vco_1", acc1), ("vco_2", acc2)):
            turns = Fraction(float(physical[key + ".initial_phase"])) / (
                2 * Fraction(math.pi)
            )
            acc.inject_turns(turns, site="phase.initial." + key, mode=formats.mode)

        freq_denominator = freq_scale * AUDIO_RATE_HZ
        noise_bytes = self.request.noise["samples"]
        if len(noise_bytes) != SAMPLE_COUNT * 4:
            raise FixedVoiceError(
                "resolved noise must contain exactly %d binary32 samples" % SAMPLE_COUNT
            )
        noise_samples = struct.unpack("<%df" % SAMPLE_COUNT, bytes(noise_bytes))

        evaluate = table.evaluate
        exp2 = math.exp2
        tanh = math.tanh
        mode = formats.mode
        saturate = OverflowPolicy.SATURATE
        v1_out = [0] * SAMPLE_COUNT
        v2_out = [0] * SAMPLE_COUNT
        noise_out = [0] * SAMPLE_COUNT
        post_vca_1 = [0] * SAMPLE_COUNT
        post_vca_2 = [0] * SAMPLE_COUNT
        post_vca_n = [0] * SAMPLE_COUNT
        mix_acc = [0] * SAMPLE_COUNT
        level_words = tuple(
            entry_quantize(physical["mixer." + lane], audio, counters, "mixer.level_q")
            for lane in ("vco_1", "vco_2", "noise")
        )
        level0, level1, level2 = level_words
        audio_scale_f = float(audio.scale)
        partials_scale_f = float(partials_fmt.scale)

        for n in range(SAMPLE_COUNT):
            m1 = apply_policy(
                midi_f0_word + tuning_words["vco_1"]
                + mul(depth_words["vco_1"], midi_fmt, up_pitch["vco_1"][n], audio,
                      midi_fmt, mode, saturate, counters, "vco_1.depth_mod"),
                midi_fmt, saturate, counters, "vco_1.pitch_sum",
            )
            if m1 < MIDI_CLAMP_MIN:
                m1 = MIDI_CLAMP_MIN
            elif m1 > clamp_max_word:
                m1 = clamp_max_word
            shadow_tally[SHADOW_EXP2] += 1
            hz1 = 440.0 * exp2((m1 / midi_scale - MIDI_A440) / SEMITONES_PER_OCTAVE)
            fq1 = shadow_half_even(hz1 * freq_scale)
            k1 = div_round(fq1 * phase_units, freq_denominator, mode)
            p1 = acc1.step(k1)
            v1 = rescale(evaluate(p1), entry_fmt, audio, mode, saturate, counters, "vco_1.S4")
            v1_out[n] = v1

            m2 = apply_policy(
                midi_f0_word + tuning_words["vco_2"]
                + mul(depth_words["vco_2"], midi_fmt, up_pitch["vco_2"][n], audio,
                      midi_fmt, mode, saturate, counters, "vco_2.depth_mod"),
                midi_fmt, saturate, counters, "vco_2.pitch_sum",
            )
            if m2 < MIDI_CLAMP_MIN:
                m2 = MIDI_CLAMP_MIN
            elif m2 > clamp_max_word:
                m2 = clamp_max_word
            shadow_tally[SHADOW_EXP2] += 1
            hz2 = 440.0 * exp2((m2 / midi_scale - MIDI_A440) / SEMITONES_PER_OCTAVE)
            fq2 = shadow_half_even(hz2 * freq_scale)
            k2 = div_round(fq2 * phase_units, freq_denominator, mode)
            p2 = acc2.step(k2)
            cos2 = evaluate(p2)
            sin2 = evaluate((p2 - (phase_units >> 2)) % phase_units)
            driven = mul(partials_word, partials_fmt, sin2, entry_fmt,
                         partials_fmt, mode, saturate, counters, "vco_2.driven")
            shadow_tally[SHADOW_TANH] += 1
            square_real = tanh((driven / partials_scale_f) / 2.0)
            square_q = apply_policy(
                shadow_half_even(square_real * audio_scale_f), audio, saturate,
                counters, "vco_2.tanh_site.S4a",
            )
            left_q = apply_policy(
                shadow_half_even(one_minus_half_shape * square_real * audio_scale_f),
                audio, saturate, counters, "vco_2.left.S4a",
            )
            right_real = 1.0 + shape_real * (cos2 / lut_scale)
            right_q = apply_policy(
                shadow_half_even(right_real * audio_scale_f), audio, saturate,
                counters, "vco_2.right.S4a",
            )
            v2 = mul(left_q, audio, right_q, audio, audio, mode, saturate,
                     counters, "vco_2.S4")
            v2_out[n] = v2

            nq = apply_policy(
                shadow_half_even(noise_samples[n] * audio_scale_f), audio,
                saturate, counters, "noise.source_q",
            )
            noise_out[n] = nq
            post_vca_1[n] = mul(v1, audio, amp_columns[0][n], audio, audio, mode,
                                saturate, counters, "vca_1.S4")
            post_vca_2[n] = mul(v2, audio, amp_columns[1][n], audio, audio, mode,
                                saturate, counters, "vca_2.S4")
            post_vca_n[n] = mul(nq, audio, amp_columns[2][n], audio, audio, mode,
                                saturate, counters, "vca_3.S4")
            mix_acc[n] = (
                post_vca_1[n] * level0 + post_vca_2[n] * level1 + post_vca_n[n] * level2
            )

        product_fmt = FixedFormat(
            signed=True, int_bits=6, frac_bits=2 * audio.frac_bits
        )
        mix_words = [
            rescale(total, product_fmt, audio, mode, saturate, counters, "mixer.S4")
            for total in mix_acc
        ]
        normalized, norm_diag = normalize_words(mix_words, audio, formats.gain, counters)

        composed: Dict[str, List[int]] = dict(control_words)
        composed.update(
            {
                "vco_1.raw": v1_out,
                "vco_2.raw": v2_out,
                "noise.raw": noise_out,
                "vco_1.post_vca": post_vca_1,
                "vco_2.post_vca": post_vca_2,
                "noise.post_vca": post_vca_n,
                "mixer.pre_normalization": mix_words,
                "mixer.peak": [norm_diag["peak_word"]],
                "mixer.gain": [norm_diag["gain_word"]],
                "mixer.output": normalized,
            }
        )
        declared = self.declared_outputs()
        missing = [name for name in declared if name not in composed]
        extra = [name for name in composed if name not in declared]
        if missing or extra:
            raise FixedVoiceError(
                "fixed checkpoint set mismatch; missing="
                + ",".join(missing)
                + " extra="
                + ",".join(extra)
            )
        ordered = {name: composed[name] for name in declared}
        diagnostics = {
            "model_identity": MODEL_IDENTITY,
            "formats": formats.to_json(),
            "counters": counters.as_json(),
            "shadow_sites": dict(shadow_tally),
            "normalized_branch": norm_diag["normalized_branch"],
            "peak_word": norm_diag["peak_word"],
            "gain_word": norm_diag["gain_word"],
            "noise_sha256": self.request.noise["sha256"],
            "sound_index": self.request.identity.sound_index,
        }
        return ordered, diagnostics
