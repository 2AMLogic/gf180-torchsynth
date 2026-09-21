"""Control-path fixed-format sweep model (issue #50).

A parameterized candidate fixed model of the control path, composed from the
landed ``torchsynth_voice.fixedpoint`` primitives, swept against the landed
float reference (``torchsynth_voice.control_path``) to produce per-quantity
error tables. Every width, scale, rounding mode, and table size enters as
explicit data on a :class:`CandidateSpec`; nothing here hardcodes a
candidate instantiation beyond the preregistered sweep grid in
``tools/sweep_control_path_formats.py`` and ``spec/CONTROL-FORMAT-SWEEP.md``.

Declared modeling policy (normative in ``spec/CONTROL-FORMAT-SWEEP.md``):

- Both sides consume identical physical maps; the sweep measures format
  error, never the #40 pinned match, and makes no upstream-fidelity claim.
- Narrowing sites follow the DR-0008 Section 6 site vocabulary: S1 entry
  quantization of every consumed physical scalar, one declared narrowing
  per module output from exact wide products/accumulators (the 48-bit
  accumulator class, site S4), plus the declared ramp-length and
  LFO-increment formation sites. The canonical rounding scalar
  (:mod:`torchsynth_voice.fixedpoint.rounding`) drives every site.
- The ADSR ``** alpha`` exponent and the LFO ``w ** 2.718281828`` shape
  weights are evaluated in binary64 shadow arithmetic on already-quantized
  operands. These are open approximation items (the #74 declaration class),
  not swept format axes; the LFO waveform *table* approximation is swept.
- LFO phase uses the wrapping u32 accumulator (C2 shape) and the sine shape
  the generator-emitted hash-linked quarter-wave table (C5 shape); saw,
  rsaw, tri and sqr derive from the exact phase word.
- Saturation is declared policy with sticky per-site counters (C7 shape);
  the phase word wraps by construction (never-saturate).
- Numeric contract: ``unbound:#53``. Everything this module produces is
  CANDIDATE-pending-#53-ratification, never accepted. No RTL, synthesis,
  layout, signoff, hardware-playback, or sound-fidelity claim is made or
  implied.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Dict, List, Optional, Tuple

from .control_path import (
    CONTROL_RATE_HZ,
    EPS,
    LFO_EXPONENT,
    LFO_SHAPES,
    MOD_MATRIX_INPUTS,
    MOD_MATRIX_OUTPUTS,
    PINNED_TWO_PI,
)
from .float_interfaces import AUDIO_SAMPLES, CONTROL_SAMPLES
from .fixedpoint.counters import ROUNDING, SATURATION, StickyCounters
from .fixedpoint.formats import FixedFormat, parse_identity
from .fixedpoint.lut import QuarterWaveSpec, QuarterWaveTable, generate_quarter_cos
from .fixedpoint.ops import OverflowPolicy, mul
from .fixedpoint.phase import PhaseAccumulator
from .fixedpoint.rounding import DEFAULT_MODE, RoundingMode, div_round_reported

SWEEP_SCHEMA = "gf180-torchsynth/control-format-sweep-v1"
NUMERIC_CONTRACT = "unbound:#53"

CONTROL_RATE_INT = int(CONTROL_RATE_HZ)  # exactly 441

# Wide shape/intermediate domain: Q2.30, [-2, +2), width 32. It represents
# 1.0 exactly (Q1.31 cannot) and holds the ramp/shape range with headroom.
SHAPE_FORMAT = parse_identity("Q2.30")
SHAPE_ONE = 1 << 30
# Ramp-length formation word: durations in fractional control samples,
# declared 48-bit-class intermediate data (Q16.30).
LENGTH_FORMAT = parse_identity("Q16.30")
LUT_ENTRY_FORMAT = parse_identity("Q1.23")
LUT_PHASE_BITS = 32

CONTROL_PREFIXES = (
    "adsr_1.",
    "adsr_2.",
    "lfo_1_rate_adsr.",
    "lfo_2_rate_adsr.",
    "lfo_1_amp_adsr.",
    "lfo_2_amp_adsr.",
)
CONTROL_STAGE_PARAMS = ("attack", "decay", "sustain", "alpha", "release")
LFO_PARAM_NAMES = ("frequency", "mod_depth", "initial_phase") + LFO_SHAPES
PITCH_ROUTES = ("vco_1_pitch", "vco_2_pitch")
# Stage-time parameters are seconds, not MIDI-domain quantities: C4's
# Q10.21 entry governs keyboard/tuning/depth words, while stage times enter
# the declared Q16.30 length word directly (the S2-class formation site, in
# fractional control samples). Near-zero directed extremes (one-ULP attack)
# are representable there and meaningless in a MIDI-domain word.
TIME_PARAM_NAMES = ("keyboard.duration",) + tuple(
    prefix + stage for prefix in CONTROL_PREFIXES for stage in ("attack", "decay", "release")
)


def consumed_parameter_names() -> Tuple[str, ...]:
    """Every physical parameter the control path consumes, in fixed order."""

    names = ["keyboard.midi_f0", "keyboard.duration"]
    for prefix in CONTROL_PREFIXES:
        names.extend(prefix + stage for stage in CONTROL_STAGE_PARAMS)
    for side in ("lfo_1.", "lfo_2."):
        names.extend(side + param for param in LFO_PARAM_NAMES)
    for source in MOD_MATRIX_INPUTS:
        for route in MOD_MATRIX_OUTPUTS:
            names.append("mod_matrix." + source + "->" + route)
    return tuple(names)


@dataclass(frozen=True)
class CandidateSpec:
    """One candidate fixed-format assignment over the swept quantities.

    ``midi_format`` -- S1 entry word for every consumed physical scalar
        (MIDI-domain axis; C4 baseline ``Q10.21``).
    ``ctrl_format`` -- control-rate signal word for envelopes, LFOs, the
        control VCA and the amp/noise mod-matrix columns (C1-shape baseline
        ``Q2.21``).
    ``pitch_format`` -- word for the two pitch-route mod-matrix columns and
        their upsamples (the control-to-VCO pitch-interface axis; C3's
        Hz-valued word formation is downstream #41 scope).
    ``up_frac_bits`` -- fractional bits of the quantized upsample
        interpolation fraction (``remainder / (AUDIO_SAMPLES - 1)``); the
        float reference uses the exact rational.
    ``lut_entries`` -- quarter-wave table size for the LFO sine shape
        (C5 baseline 4096 entries x 24 bits).
    ``mode`` -- rounding mode at every declared narrowing site (C6 baseline
        half-even).
    """

    midi_format: FixedFormat
    ctrl_format: FixedFormat
    pitch_format: FixedFormat
    up_frac_bits: int
    lut_entries: int
    mode: RoundingMode = DEFAULT_MODE

    @property
    def identity(self) -> str:
        return (
            "midi=%s ctrl=%s pitch=%s upfrac=uQ.%d lut=%dx24 mode=%s"
            % (
                self.midi_format.identity,
                self.ctrl_format.identity,
                self.pitch_format.identity,
                self.up_frac_bits,
                self.lut_entries,
                self.mode.value,
            )
        )

    def to_json(self) -> Dict:
        return {
            "midi_format": self.midi_format.to_json(),
            "ctrl_format": self.ctrl_format.to_json(),
            "pitch_format": self.pitch_format.to_json(),
            "up_frac_bits": int(self.up_frac_bits),
            "lut_entries": int(self.lut_entries),
            "lut_entry_format": LUT_ENTRY_FORMAT.identity,
            "lut_phase_bits": LUT_PHASE_BITS,
            "mode": self.mode.value,
            "identity": self.identity,
        }


def trace_word_format(spec: CandidateSpec, trace: str) -> FixedFormat:
    """The word format a candidate's rendered trace is stored in."""

    if trace == "keyboard.midi_f0":
        return spec.midi_format
    if trace == "keyboard.duration":
        return LENGTH_FORMAT
    if trace.endswith("pitch") and (
        trace.startswith("mod_matrix.") or trace.startswith("control_upsample.")
    ):
        return spec.pitch_format
    return spec.ctrl_format


class SweepError(ValueError):
    """A declared sweep invariant failed."""


class UndefinedControlState(SweepError):
    """A declared-undefined numeric state (all-zero LFO shape weights)."""


_LUT_CACHE: Dict[int, QuarterWaveTable] = {}
_UP_FRAC_CACHE: Dict[Tuple[int, RoundingMode], List[int]] = {}


def quarter_wave_table(lut_entries: int) -> QuarterWaveTable:
    """The deterministic hash-linked quarter-wave table for a size."""

    table = _LUT_CACHE.get(lut_entries)
    if table is None:
        spec = QuarterWaveSpec(
            n_entries=lut_entries,
            entry_format=LUT_ENTRY_FORMAT,
            phase_bits=LUT_PHASE_BITS,
        )
        table = generate_quarter_cos(spec)
        _LUT_CACHE[lut_entries] = table
    return table


def up_coordinate_table(up_frac_bits: int, mode: RoundingMode) -> List[Tuple[int, int]]:
    """Per-audio-index ``(low, fraction word)`` for the full-length path.

    ``low`` is the exact floor of the endpoint-aligned source coordinate and
    the fraction word is ``round(remainder / (AUDIO_SAMPLES-1) * 2^bits)``
    under ``mode``; exact-boundary indices carry fraction word 0 (endpoints
    are exact copies by construction).
    """

    key = (up_frac_bits, mode)
    table = _UP_FRAC_CACHE.get(key)
    if table is None:
        numerator_unit = CONTROL_SAMPLES - 1
        denominator = AUDIO_SAMPLES - 1
        table = []
        for j in range(AUDIO_SAMPLES):
            low, remainder = divmod(j * numerator_unit, denominator)
            if remainder == 0:
                table.append((low, 0))
            else:
                word, _rounded = div_round_reported(
                    remainder << up_frac_bits, denominator, mode
                )
                table.append((low, word))
        _UP_FRAC_CACHE[key] = table
    return table


def _decode(word: int, fmt: FixedFormat) -> Fraction:
    return Fraction(word, fmt.scale)


def entry_format_for(name: str, midi_format: FixedFormat) -> FixedFormat:
    """The entry word for one consumed parameter (time words are separate)."""

    if name in TIME_PARAM_NAMES:
        return LENGTH_FORMAT
    return midi_format


def quantize_params(
    physical: Dict[str, float], fmt: FixedFormat, mode: RoundingMode, counters: StickyCounters
) -> Dict[str, int]:
    """S1 entry site: quantize every consumed scalar into its entry word.

    MIDI-domain and unitless scalars take ``fmt`` (the candidate's
    MIDI-domain axis word); stage-time parameters take the declared Q16.30
    length word.
    """

    words = {}
    for index, name in enumerate(consumed_parameter_names()):
        entry_fmt = entry_format_for(name, fmt)
        exact = Fraction(physical[name]) * entry_fmt.scale
        word, _rounded = div_round_reported(exact.numerator, exact.denominator, mode)
        if not entry_fmt.contains(word):
            word = apply_saturation(word, entry_fmt, "s1.entry[%d]:%s" % (index, name), counters)
        words[name] = word
    return words


def apply_saturation(
    word: int, fmt: FixedFormat, site: str, counters: StickyCounters
) -> int:
    """Clamp into ``fmt``'s range, noting the sticky saturation counter."""

    counters.note(site, SATURATION)
    return fmt.max_int if word > 0 else fmt.min_int


class FixedControlPath:
    """The candidate fixed control-path model for one :class:`CandidateSpec`.

    Renders every owned control trace for one physical map, mirroring
    ``torchsynth_voice.control_path`` stage for stage with declared
    fixed-point sites. Traces are returned decoded to exact ``Fraction``
    values so the comparison against the float reference carries no
    comparator float error.
    """

    def __init__(self, spec: CandidateSpec):
        self.spec = spec
        self.table = quarter_wave_table(spec.lut_entries)
        self.entry_fmt = spec.midi_format
        self.ctrl_fmt = spec.ctrl_format
        self.pitch_fmt = spec.pitch_format
        self.mode = spec.mode
        self.counters = StickyCounters()

    # -- narrow and decode helpers ------------------------------------------------

    def _entry(self, words: Dict[str, int], name: str) -> Fraction:
        return _decode(words[name], entry_format_for(name, self.entry_fmt))

    def _narrow(self, exact: Fraction, fmt: FixedFormat, site: str) -> int:
        """One declared narrowing site: round per mode, then policy."""

        scaled = exact * fmt.scale
        word, rounded = div_round_reported(scaled.numerator, scaled.denominator, self.mode)
        if rounded:
            self.counters.note(site, ROUNDING)
        if not fmt.contains(word):
            word = apply_saturation(word, fmt, site, self.counters)
        return word

    def _narrow_num(self, numer: int, denom: int, fmt: FixedFormat, site: str) -> int:
        """``_narrow`` over an exact integer ratio (fast path)."""

        word, rounded = div_round_reported(numer * fmt.scale, denom, self.mode)
        if rounded:
            self.counters.note(site, ROUNDING)
        if not fmt.contains(word):
            word = apply_saturation(word, fmt, site, self.counters)
        return word

    def _to_shape(self, word: int, fmt: FixedFormat) -> int:
        return rescale_to(word, fmt, SHAPE_FORMAT, self.mode)

    # -- ADSR ---------------------------------------------------------------------

    def _quantized_samples(self, samples: Fraction) -> int:
        """Declared length-formation site: fractional control samples in Q16.30."""

        exact = samples * LENGTH_FORMAT.scale
        word, _rounded = div_round_reported(exact.numerator, exact.denominator, self.mode)
        return word

    def _ramp(
        self,
        length_q: int,
        length_exact: Fraction,
        start_q: int,
        inverse: bool,
        alpha: float,
    ) -> List[int]:
        """One ADSR ramp row, computed in a Q2.60 shadow domain.

        Mirrors the float ``_ramp`` policy: tilt by ``start``, clamp at
        zero, ``(x + eps) / length + eps`` with the division through the
        canonical rounding scalar, clamp at one, conditional inversion for
        positive lengths, then the alpha power (binary64 shadow, declared
        open approximation). The zero-length branch is decided on the exact
        pre-quantization length, exactly like the float model's
        ``length == 0.0``; a length that is nonzero but below the length
        word's resolution is the declared degenerate step: every sample
        clamps to one before the conditional inversion (the exact division
        ``(x + eps) / tiny`` exceeds one for every index).

        Declared shadow-domain amendment (run-2 finding): the ramp epsilon
        and intermediate carry Q2.60 words. In Q2.30 the 1e-6 epsilon word
        carried a 2.4e-4 relative error that dominated degenerate-length
        ramps (a modeling-granularity artifact, not a candidate format
        axis); narrowing to the Q2.30 shape domain happens once, after the
        alpha power, through the canonical scalar.
        """

        eps60 = div_round_reported(
            (Fraction(EPS) * (1 << 60)).numerator,
            (Fraction(EPS) * (1 << 60)).denominator,
            self.mode,
        )[0]
        start60 = start_q << 30
        len60 = length_q << 30
        exact_zero = length_exact == 0
        degenerate = (not exact_zero) and length_q == 0
        row = []
        for index in range(CONTROL_SAMPLES):
            x = (index << 60) - start60
            if x < 0:
                x = 0
            if exact_zero or degenerate:
                value60 = 1 << 60
                if degenerate and inverse:
                    value60 = 0
            else:
                value60, rounded = div_round_reported(
                    (x + eps60) << 60, len60, self.mode
                )
                if rounded:
                    self.counters.note("s4.ramp.div", ROUNDING)
                value60 += eps60
                if value60 > (1 << 60):
                    value60 = 1 << 60
                elif value60 < 0:
                    value60 = 0
                if inverse:
                    value60 = (1 << 60) - value60
            powered = (float(value60) * 2.0**-60) ** alpha
            scaled = Fraction(powered) * SHAPE_FORMAT.scale
            word, rounded = div_round_reported(scaled.numerator, scaled.denominator, self.mode)
            if rounded:
                self.counters.note("s4.ramp.pow", ROUNDING)
            if not SHAPE_FORMAT.contains(word):
                word = apply_saturation(word, SHAPE_FORMAT, "s4.ramp.pow", self.counters)
            row.append(word)
        return row

    def _adsr(self, words: Dict[str, int], prefix: str) -> List[int]:
        """One ADSR envelope in the control word, mirroring the float model."""

        duration_seconds = self._entry(words, "keyboard.duration")
        attack_seconds = self._entry(words, prefix + "attack")
        duration_exact = duration_seconds * Fraction(CONTROL_RATE_INT)
        attack_exact = min(attack_seconds, duration_seconds) * Fraction(CONTROL_RATE_INT)
        decay_seconds = max(duration_seconds - attack_seconds, Fraction(0))
        new_decay_exact = (
            min(decay_seconds, self._entry(words, prefix + "decay"))
            * Fraction(CONTROL_RATE_INT)
        )
        release_exact = self._entry(words, prefix + "release") * Fraction(CONTROL_RATE_INT)
        duration_q = self._quantized_samples(duration_exact)
        attack_q = self._quantized_samples(attack_exact)
        new_decay_q = self._quantized_samples(new_decay_exact)
        release_q = self._quantized_samples(release_exact)
        alpha = float(self._entry(words, prefix + "alpha"))
        sustain_q = self._to_shape(words[prefix + "sustain"], self.entry_fmt)

        attack_signal = self._ramp(attack_q, attack_exact, 0, False, alpha)
        decay_signal = self._ramp(new_decay_q, new_decay_exact, attack_q, True, alpha)
        release_signal = self._ramp(release_q, release_exact, duration_q, True, alpha)

        envelope = []
        for attack_value, decay_value, release_value in zip(
            attack_signal, decay_signal, release_signal
        ):
            # decay_factor = (1 - sustain) * decay + sustain: exact in
            # Q4.60, one declared narrowing back to Q2.30.
            factor = div_round_reported(
                (SHAPE_ONE - sustain_q) * decay_value + sustain_q * SHAPE_ONE,
                SHAPE_ONE,
                self.mode,
            )[0]
            if not SHAPE_FORMAT.contains(factor):
                factor = apply_saturation(factor, SHAPE_FORMAT, "s4.adsr.factor", self.counters)
            # envelope = attack * factor * release: exact Q6.90 product, one
            # declared narrowing to the control word (site S4).
            product = attack_value * factor * release_value
            envelope.append(
                self._narrow_num(
                    product,
                    SHAPE_FORMAT.scale**3,
                    self.ctrl_fmt,
                    "s4.adsr." + prefix,
                )
            )
        return envelope

    # -- LFO ----------------------------------------------------------------------

    def _lfo(self, words: Dict[str, int], side: str, rate_envelope: List[int]) -> List[int]:
        """One LFO in the control word; C2 phase accumulator + C5 table."""

        weights_binary64 = []
        for shape in LFO_SHAPES:
            weight_value = float(self._entry(words, side + shape))
            weights_binary64.append(weight_value**LFO_EXPONENT)
        total = math.fsum(weights_binary64)
        if total == 0.0:
            raise UndefinedControlState(
                "undefined LFO shape state: all-zero shape weights for " + side
            )
        weight_q = [
            self._narrow(Fraction(w / total), SHAPE_FORMAT, "s4.lfo.weight." + side)
            for w in weights_binary64
        ]

        initial_turns = self._entry(words, side + "initial_phase") / Fraction(PINNED_TWO_PI)
        init_word, _rounded = div_round_reported(
            initial_turns.numerator * (1 << LUT_PHASE_BITS),
            initial_turns.denominator,
            self.mode,
        )
        init_word %= 1 << LUT_PHASE_BITS

        phase = PhaseAccumulator(LUT_PHASE_BITS)
        # Integer fast paths: exact rational arithmetic over the common
        # denominator entry_scale * ctrl_scale.
        freq_word = words[side + "frequency"]
        depth_word = words[side + "mod_depth"]
        common = self.entry_fmt.scale * self.ctrl_fmt.scale
        length_scale = LENGTH_FORMAT.scale
        values = []
        for index in range(CONTROL_SAMPLES):
            # rate = frequency + mod_depth * rate_envelope: exact integer
            # numerator over `common`, one declared narrowing to Q16.30.
            rate_word = self._narrow_num(
                freq_word * self.ctrl_fmt.scale + depth_word * rate_envelope[index],
                common,
                LENGTH_FORMAT,
                "s4.lfo.rate",
            )
            if rate_word < 0:
                # The modulated rate clamps at zero before accumulation.
                rate_word = 0
                self.counters.note("s4.lfo.rate_clamp", SATURATION)
            # Declared increment site: K = round((rate / 441) * 2^32).
            increment = div_round_reported(
                rate_word << (LUT_PHASE_BITS - LENGTH_FORMAT.frac_bits),
                CONTROL_RATE_INT,
                self.mode,
            )[0]
            phase.step(increment)
            argument = (phase.value + init_word) % (1 << LUT_PHASE_BITS)
            blended = self._lfo_blend(argument, weight_q)
            values.append(
                self._narrow_num(
                    blended, SHAPE_FORMAT.scale, self.ctrl_fmt, "s4.lfo." + side
                )
            )
        return values

    def _lfo_blend(self, phase: int, weight_q: List[int]) -> int:
        """The five pinned shapes at one u32 phase, blended in Q2.30."""

        cos_q23 = self.table.evaluate(phase, self.mode)  # Q1.23
        cos_q30 = cos_q23 << 7  # exact widen Q1.23 -> Q2.30
        sign = (cos_q23 > 0) - (cos_q23 < 0)
        # sin shape = (cos(x + pi) + 1) / 2 = (1 - cos x) / 2.
        sin_q30 = div_round_reported(SHAPE_ONE - cos_q30, 2, self.mode)[0]
        # saw = (x mod 2pi) / 2pi exactly: the u32 phase over 2^32.
        saw_q30 = div_round_reported(phase << 30, 1 << 32, self.mode)[0]
        rsaw_q30 = SHAPE_ONE - saw_q30
        # tri = 2*saw, reflected to 2 - 2*saw above the mid turn; t = 1 kept.
        if phase > (1 << 31):
            tri_q30 = div_round_reported((1 << 32) - phase, 2, self.mode)[0]
        else:
            tri_q30 = div_round_reported(phase, 2, self.mode)[0]
        # sqr = (1 - sign(cos x)) / 2: 1 where cos < 0, 0.5 at exact zero.
        sqr_q30 = div_round_reported(SHAPE_ONE - sign * SHAPE_ONE, 2, self.mode)[0]
        shapes = (sin_q30, tri_q30, saw_q30, rsaw_q30, sqr_q30)
        merged = 0
        for weight, shape in zip(weight_q, shapes):
            merged += weight * shape
        # Continuous blend, exact Q4.60 accumulate, one declared narrowing.
        return div_round_reported(merged, SHAPE_ONE, self.mode)[0]

    # -- VCA / mod matrix ---------------------------------------------------------

    def _control_vca(self, waveform: List[int], gain: List[int]) -> List[int]:
        """Pinned control-rate VCA: one multiply, one declared narrowing."""

        return [
            mul(
                wave,
                self.ctrl_fmt,
                gain_value,
                self.ctrl_fmt,
                self.ctrl_fmt,
                self.mode,
                OverflowPolicy.SATURATE,
                self.counters,
                "s4.control_vca",
            )
            for wave, gain_value in zip(waveform, gain)
        ]

    def _mod_matrix(
        self, words: Dict[str, int], signals: List[List[int]]
    ) -> Dict[str, List[int]]:
        """The pinned 4x5 matrix: exact accumulate, one narrowing per sample.

        Integer fast path: every term contributes ``depth_word * signal_word``
        to an exact numerator over the common denominator
        ``entry_scale * ctrl_scale``; the single declared narrowing per
        output sample divides through the canonical scalar.
        """

        common = self.entry_fmt.scale * self.ctrl_fmt.scale
        outputs: Dict[str, List[int]] = {}
        for route in MOD_MATRIX_OUTPUTS:
            fmt = self.pitch_fmt if route in PITCH_ROUTES else self.ctrl_fmt
            depth_words = [
                words["mod_matrix." + source + "->" + route]
                for source in MOD_MATRIX_INPUTS
            ]
            d0, d1, d2, d3 = depth_words
            c0, c1, c2, c3 = signals
            merged = []
            for index in range(CONTROL_SAMPLES):
                numer = (
                    d0 * c0[index]
                    + d1 * c1[index]
                    + d2 * c2[index]
                    + d3 * c3[index]
                )
                merged.append(
                    self._narrow_num(numer, common, fmt, "s4.mod_matrix." + route)
                )
            outputs[route] = merged
        return outputs

    # -- upsample -----------------------------------------------------------------

    def _upsample(
        self,
        column: List[int],
        fmt: FixedFormat,
        sample_grid: Optional[List[int]] = None,
    ) -> List[int]:
        """Endpoint-aligned upsample with the candidate's fraction word.

        ``low``/``remainder`` come from the exact rational coordinate; the
        fraction is the candidate's quantized ``uQ.<up_frac_bits>`` word;
        the blend is an exact integer weighted average with one declared
        rounding. Endpoints are exact copies by construction and asserted.

        ``sample_grid`` optionally restricts evaluation to a declared index
        list (screening mode); the endpoints are always evaluated. Full
        certification runs use the full length.
        """

        numerator_unit = CONTROL_SAMPLES - 1
        denominator = AUDIO_SAMPLES - 1
        frac_bits = self.spec.up_frac_bits
        frac_scale = 1 << frac_bits
        mode = self.mode
        half_even = mode is RoundingMode.HALF_EVEN
        counters = self.counters
        note = counters.note
        output = []

        def _blend(numer: int) -> int:
            """One declared blend narrowing (canonical scalar semantics)."""

            if half_even and numer >= 0:
                # Inline equivalent of the canonical half-even scalar for
                # non-negative numerators (equivalence test-covered).
                quotient, rem = divmod(numer, frac_scale)
                if rem:
                    doubled = rem * 2
                    if doubled > frac_scale or (doubled == frac_scale and quotient & 1):
                        quotient += 1
                    note("s4.upsample.blend", ROUNDING)
                return quotient
            quotient = div_round_reported(numer, frac_scale, mode)[0]
            note("s4.upsample.blend", ROUNDING)
            return quotient

        if sample_grid is None:
            coordinates = up_coordinate_table(frac_bits, mode)
            for low, frac_word in coordinates:
                if frac_word == 0:
                    output.append(column[low])
                    continue
                left = column[low]
                right = column[low + 1]
                quotient = _blend(left * (frac_scale - frac_word) + right * frac_word)
                if not fmt.contains(quotient):
                    quotient = apply_saturation(quotient, fmt, "s4.upsample", counters)
                output.append(quotient)
        else:
            for j in sample_grid:
                low, remainder = divmod(j * numerator_unit, denominator)
                if remainder == 0:
                    output.append(column[low])
                    continue
                frac_word = div_round_reported(remainder << frac_bits, denominator, mode)[0]
                left = column[low]
                right = column[low + 1]
                quotient = _blend(left * (frac_scale - frac_word) + right * frac_word)
                if not fmt.contains(quotient):
                    quotient = apply_saturation(quotient, fmt, "s4.upsample", counters)
                output.append(quotient)
        # Endpoint contract: exact copies of control indices 0 and 1763 at
        # every evaluated endpoint position.
        if sample_grid is None:
            if output[0] != column[0] or output[-1] != column[-1]:
                raise SweepError("endpoint-aligned upsample endpoints must be exact")
        else:
            for position, j in enumerate(sample_grid):
                if j == 0 and output[position] != column[0]:
                    raise SweepError(
                        "endpoint-aligned upsample endpoints must be exact"
                    )
                if j == AUDIO_SAMPLES - 1 and output[position] != column[-1]:
                    raise SweepError(
                        "endpoint-aligned upsample endpoints must be exact"
                    )
        return output

    # -- top level ----------------------------------------------------------------

    def render_words(
        self,
        physical: Dict[str, float],
        sample_grid: Optional[List[int]] = None,
    ) -> Dict[str, List[int]]:
        """Render every owned control trace as formatted integer words.

        ``sample_grid`` restricts the five upsampled traces to the given
        audio indices (screening mode); endpoints are always included.
        """

        self.counters = StickyCounters()
        words = quantize_params(physical, self.entry_fmt, self.mode, self.counters)

        rate_1 = self._adsr(words, "lfo_1_rate_adsr.")
        rate_2 = self._adsr(words, "lfo_2_rate_adsr.")
        amp_1 = self._adsr(words, "lfo_1_amp_adsr.")
        amp_2 = self._adsr(words, "lfo_2_amp_adsr.")

        lfo_1 = self._lfo(words, "lfo_1.", rate_1)
        lfo_2 = self._lfo(words, "lfo_2.", rate_2)
        post_1 = self._control_vca(lfo_1, amp_1)
        post_2 = self._control_vca(lfo_2, amp_2)

        adsr_1 = self._adsr(words, "adsr_1.")
        adsr_2 = self._adsr(words, "adsr_2.")

        columns = self._mod_matrix(words, (adsr_1, adsr_2, post_1, post_2))

        words_by_trace = {
            "keyboard.midi_f0": [words["keyboard.midi_f0"]],
            "keyboard.duration": [words["keyboard.duration"]],
            "lfo_1_rate_adsr.output": rate_1,
            "lfo_2_rate_adsr.output": rate_2,
            "lfo_1_amp_adsr.output": amp_1,
            "lfo_2_amp_adsr.output": amp_2,
            "lfo_1.raw": lfo_1,
            "lfo_1.post_control_vca": post_1,
            "lfo_2.raw": lfo_2,
            "lfo_2.post_control_vca": post_2,
            "adsr_1.output": adsr_1,
            "adsr_2.output": adsr_2,
        }
        for route in MOD_MATRIX_OUTPUTS:
            words_by_trace["mod_matrix." + route] = columns[route]
        for route in MOD_MATRIX_OUTPUTS:
            fmt = self.pitch_fmt if route in PITCH_ROUTES else self.ctrl_fmt
            words_by_trace["control_upsample." + route] = self._upsample(
                columns[route], fmt, sample_grid
            )
        return words_by_trace

    def render(self, physical: Dict[str, float]) -> Dict[str, List[Fraction]]:
        """Render every owned control trace; values are exact Fractions."""

        rendered = self.render_words(physical)
        return {
            name: [_decode(word, self._format_for(name)) for word in values]
            for name, values in rendered.items()
        }

    def _format_for(self, trace: str) -> FixedFormat:
        return trace_word_format(self.spec, trace)


def rescale_to(
    word: int, src: FixedFormat, dst: FixedFormat, mode: RoundingMode
) -> int:
    """Exact rational rescale between formats through the canonical scalar."""

    if not src.contains(word):
        raise ValueError(f"value {word} outside source format {src.identity}")
    shift = dst.frac_bits - src.frac_bits
    if shift >= 0:
        return word << shift
    scaled, _rounded = div_round_reported(word, 1 << (-shift), mode)
    return scaled
