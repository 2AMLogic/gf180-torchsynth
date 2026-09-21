#!/usr/bin/env python3
"""Audio-sources fixed-format sweep (issue #51) — candidate receipts only.

Stdlib-only, deterministic. Sweeps candidate audio word formats, phase
accumulator widths, and quarter-wave LUT geometries of a candidate fixed
model -- built exclusively from the landed
``torchsynth_voice.fixedpoint`` primitives -- against the independent float
references (#41/#42) over the committed directed source fixtures.

Scope and declared boundaries (mirrored into every receipt):

- Frequency-path input formation: the fixed VCO consumes the per-sample
  binary32 frequency formed by the pinned upstream op order (MIDI clamp
  before ``440 * 2^((midi-69)/12)``), formed here by harness binary32
  arithmetic exactly like ``float_sources.pitch_phases``. The sweep
  therefore measures the declared S2/S3 phase-quantization sites, the
  waveform approximation (M2-style intrinsic tables), and S4 word-width /
  narrowing effects. The MIDI-domain fixed arithmetic (Q10.21 + fixed
  exp2, M4) is a separate approximation surface and is NOT covered.
- Noise: the canonical stream is exact-bytes by identity (DR-0008
  Section 7) and is NEVER swept; its digest is asserted per fixture. Only
  the audio word width of the noise lane's post-VCA path sweeps.
- vco_2 tanh site: no fixed tanh approximation has landed (its intrinsic
  declaration belongs to #74); the harness evaluates tanh in binary64 on
  the fixed sine argument and declares that site excluded from the swept
  error budget.
- Normalization: every mandatory gain/error row is computed on
  pre-normalization traces. Normalization rows (strict ``peak > 1``,
  declared-precision reciprocal at S5) are reported separately and never
  precede the mandatory rows.
- Verdicts: per-configuration rows are evaluated against the DR-0008
  Section 10 M1 band limits as *pre-calibration selection bands*. rubric
  v0 is frozen and governs the preregistration policy (measure, then
  preregister smallest power of two >= 2x measured, before RTL freeze);
  final threshold preregistration belongs to #53. Nothing here is
  ratified: every selection is a CANDIDATE pending #53.
- Proxies: storage-bit and operation-count columns are transparent
  operation proxies, clearly NOT PPA: no synthesis, area, timing, power,
  layout, or hardware claim is made or implied anywhere in this tool or
  its receipts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import sys
import time
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import float_mix as fm  # noqa: E402
from torchsynth_voice import float_sources as fs  # noqa: E402
from torchsynth_voice import float_interfaces as fi  # noqa: E402
from torchsynth_voice import control_path as cp  # noqa: E402
from torchsynth_voice import paired_metrics as pm  # noqa: E402
from torchsynth_voice.fixedpoint import counters as fp_counters  # noqa: E402
from torchsynth_voice.fixedpoint import formats as fp_formats  # noqa: E402
from torchsynth_voice.fixedpoint import lut as fp_lut  # noqa: E402
from torchsynth_voice.fixedpoint import ops as fp_ops  # noqa: E402
from torchsynth_voice.fixedpoint import phase as fp_phase  # noqa: E402
from torchsynth_voice.fixedpoint import rounding as fp_rounding  # noqa: E402

RECORD_PATH = ROOT / "sim/reference/float-sources-v1.json"
FIXTURES = ROOT / "tests/fixtures/float-sources"
DIRECTED_PATH = ROOT / "spec/reference/directed-voice-v1.json"
DEFAULT_OUT = ROOT / "sim/candidates/audio-sources-sweep-v1.json"
TRACES_OUT = ROOT / "sim/candidates/audio-sources-sweep-v1-traces"

SCHEMA = "torchsynth-audio-sources-sweep"
SEMVER = "audio-sources-sweep-v1"
FS_HZ = 44100.0
AUDIO_SAMPLES = fs.AUDIO_SAMPLES
COMPARISON_UNIT = "linear-amplitude"

# Selection bands: DR-0008 Section 10 M1 (pre-calibration; final threshold
# preregistration is #53's action under the frozen rubric-v0 policy).
M1_BANDS = (
    {"name": "f0<=1kHz", "max_f0": 1000.0, "max_abs": 2.0**-13, "snr_db": 100.0},
    {"name": "1kHz<f0<=5kHz", "max_f0": 5000.0, "max_abs": 2.0**-10, "snr_db": 80.0},
    {"name": "5kHz<f0<=12.6kHz", "max_f0": 12600.0, "max_abs": 2.0**-4, "snr_db": None},
)
M2_LIMIT = 2.0**-21

# Sweep grids. AUDIO_FORMATS: minimal-format candidates around the selected
# Q2.21 (DR-0008 C1); the two 16-bit variants probe the headroom/precision
# boundary; 32-bit is deliberately excluded (rejected for cost, C1).
AUDIO_FORMATS = ("Q1.14", "Q2.13", "Q2.17", "Q2.21")
PHASE_WIDTHS = (24, 28, 32)
LUT_ENTRIES = (1024, 2048, 4096)
LUT_ORDERS = ("linear", "quadratic")
LUT_ENTRY_FORMAT = "Q1.22"  # 24-bit signed entries, quarter-wave values in [0,1]
LUT_PHASE_BITS = 32  # 2 quadrant + 12 index + 18 interp at 4K linear (DR-0008 S5)

# Declared harness formats (not candidate choices): S2 frequency word is the
# selected Q16.15 (C3); the partials scale carrier is wide enough for
# pi * partials at the minimum fixture pitch.
FREQ_WORD_FRAC = 15
PARTIALS_FMT = fp_formats.parse_identity("Q14.17")

QUICK_SAMPLES = 4000
# Preregistered harness sensitivity bar for the M7-style mutation probes.
MUTATION_RATIO = 5.0
# DR-0006 released anchors used as declared normalization-stress scales.
NORMALIZE_ANCHOR = 3.9478583336


def _half_even_float(x: float) -> int:
    """Round an exact-in-float64 product half-even to an integer.

    ``x * 2^k`` products of binary32 values are exact in binary64 (power-of-two
    scaling only moves the exponent), so ``round`` is the true half-even result.
    """
    return round(x)


def _quantize(value: float, fmt: fp_formats.FixedFormat, c, site: str) -> int:
    """Host float -> fmt integer at half-even (declared entry site)."""
    rounded = _half_even_float(value * fmt.scale)
    return fp_ops.apply_policy(
        rounded, fmt, fp_ops.OverflowPolicy.SATURATE, c, site
    )


def _freq_path(midi_f0, tuning, mod_depth, mod_signal):
    """Per-sample binary32 frequency, pinned upstream op order (declared input).

    Mirrors ``float_sources.pitch_phases`` minus the cumsum: the MIDI clamp
    happens before the ``440 * 2^((midi-69)/12)`` conversion, per sample.
    """
    midi = fs.f32(fs.f32(midi_f0) + fs.f32(tuning))
    depth = fs.f32(mod_depth)
    exp2 = math.exp2
    freqs = []
    append = freqs.append
    for sample in mod_signal:
        control = fs.f32(midi + fs.f32(depth * fs.f32(sample)))
        if control < fs.MIDI_CLAMP_MIN:
            control = fs.MIDI_CLAMP_MIN
        elif control > fs.MIDI_CLAMP_MAX:
            control = fs.MIDI_CLAMP_MAX
        exponent = fs.f32(fs.f32(control - fs.MIDI_A440) / fs.SEMITONES_PER_OCTAVE)
        append(fs.f32(440.0 * fs.f32(exp2(exponent))))
    return freqs


def _mix_reference(sources, levels):
    """Reference mixer arithmetic (binary64 products/accumulate, one f32 round).

    Mirrors ``float_mix.mixer_weighted_sum`` without the release-length gate so
    quick mode can run on truncated fixture views.
    """
    merged = None
    for level, source in zip(levels, sources):
        if merged is None:
            merged = [float(level) * value for value in source]
        else:
            merged = [
                total + float(level) * value
                for total, value in zip(merged, source)
            ]
    return [fs.f32(value) for value in merged]


def _norm_reference(signal):
    """Reference normalization semantics (strict peak > 1, earliest index)."""
    peak = -1.0
    for value in signal:
        magnitude = abs(value)
        if magnitude > peak:
            peak = magnitude
    branch = peak > 1.0
    if branch:
        output = [fs.f32(value / peak) for value in signal]
    else:
        output = list(signal)
    gain = fs.f32(1.0 / peak) if peak > 1.0 else 1.0
    return output, peak, gain, branch


class FixtureData:
    """Committed directed fixture + derived float reference context."""

    def __init__(
        self,
        case_id: str,
        limit_samples: int | None = None,
        mix_scale: float | None = None,
        case_label: str | None = None,
    ):
        self.case_id = case_label or case_id
        self.source_case_id = case_id
        self.mix_scale = fs.f32(mix_scale) if mix_scale else None
        directory = FIXTURES / case_id
        self.params = json.loads((directory / "params.json").read_bytes())
        self.physical = self.params["physical_by_name"]
        record = json.loads(RECORD_PATH.read_bytes())
        self.record = record
        self.buffers = {}
        for name in record["comparison_traces"]:
            values = fs.f32le_values((directory / (name + ".f32le")).read_bytes())
            if limit_samples is not None:
                values = values[:limit_samples]
            self.buffers[name] = values
        self.sample_count = len(self.buffers["vco_1.raw"])
        if limit_samples is None and self.sample_count != AUDIO_SAMPLES:
            raise ValueError(
                f"{case_id}: expected {AUDIO_SAMPLES} samples, got {self.sample_count}"
            )
        recorded = record["fixtures"].get(case_id)
        if recorded:
            for name, entry in recorded["files"].items():
                path = directory / (name + ".f32le")
                if path.exists():
                    got = hashlib.sha256(path.read_bytes()).hexdigest()
                    if got != entry["sha256"]:
                        raise ValueError(
                            f"{case_id}/{name}: fixture digest drift"
                        )
        self.noise_bytes = fs.NoiseSource.resolve(self.params["sound_index"])
        if limit_samples is not None:
            noise_values = fs.f32le_values(
                self.noise_bytes[: limit_samples * 4]
            )
        else:
            noise_values = fs.f32le_values(self.noise_bytes)
        self.noise_values = noise_values
        declared_slot0 = record["noise"]["declared_slot0_sha256"]
        self.noise_digest_ok = (
            self.params["noise_slot"] == record["noise"]["slot"]
            and (
                hashlib.sha256(
                    fs.NoiseSource.resolve(record["sound_index"])
                ).hexdigest()
                == declared_slot0
            )
        )
        self.freq1 = _freq_path(
            self.physical["keyboard.midi_f0"],
            self.physical["vco_1.tuning"],
            self.physical["vco_1.mod_depth"],
            self.buffers["control_upsample.vco_1_pitch"],
        )
        self.freq2 = _freq_path(
            self.physical["keyboard.midi_f0"],
            self.physical["vco_2.tuning"],
            self.physical["vco_2.mod_depth"],
            self.buffers["control_upsample.vco_2_pitch"],
        )
        self.max_f1 = max(abs(f) for f in self.freq1)
        self.max_f2 = max(abs(f) for f in self.freq2)
        self.amps = self._render_amps(limit_samples)
        vco2 = fs.SquareSawVCO(
            tuning=self.physical["vco_2.tuning"],
            mod_depth=self.physical["vco_2.mod_depth"],
            initial_phase=self.physical["vco_2.initial_phase"],
            shape=self.physical["vco_2.shape"],
        )
        self.partials_scale = fs.f32(
            fs.PI_F32 * vco2.partials(self.physical["keyboard.midi_f0"])
        )
        self.wander = self._reference_wander(limit_samples)
        self.shape = fs.f32(self.physical["vco_2.shape"])
        self.one_minus_half_shape = fs.f32(1.0 - fs.f32(self.shape / 2.0))
        self.levels = [
            self.physical["mixer.vco_1"],
            self.physical["mixer.vco_2"],
            self.physical["mixer.noise"],
        ]
        self.reference_phase_error = self._reference_phase_error(limit_samples)

    def _reference_phase_error(self, limit_samples):
        """Measured float-reference phase error vs the true phase (radians).

        Walks the exact rational turns (binary32 frequency values are exact
        data) alongside the pinned binary32-stored cumsum and reports the
        maximum |stored - true| per VCO. This is the reference-side drift
        floor: no fixed candidate's float-vs-fixed E at the fixture can fall
        below approximately this amplitude, per DR-0008 Section 4.
        """
        result = {}
        two_pi = 2.0 * math.pi
        for key in ("vco_1", "vco_2"):
            mod_signal = self.buffers["control_upsample.%s_pitch" % key]
            stored = fs.pitch_phases(
                self.physical["keyboard.midi_f0"],
                self.physical["%s.tuning" % key],
                self.physical["%s.mod_depth" % key],
                mod_signal,
            )
            count = len(mod_signal)
            if limit_samples is not None:
                count = min(count, limit_samples)
            midi = fs.f32(
                fs.f32(self.physical["keyboard.midi_f0"])
                + fs.f32(self.physical["%s.tuning" % key])
            )
            depth = fs.f32(self.physical["%s.mod_depth" % key])
            exact_turns = Fraction(0)
            worst = 0.0
            exp2 = math.exp2
            for index in range(count):
                sample = mod_signal[index]
                control = fs.f32(midi + fs.f32(depth * fs.f32(sample)))
                if control < fs.MIDI_CLAMP_MIN:
                    control = fs.MIDI_CLAMP_MIN
                elif control > fs.MIDI_CLAMP_MAX:
                    control = fs.MIDI_CLAMP_MAX
                exponent = fs.f32(
                    fs.f32(control - fs.MIDI_A440) / fs.SEMITONES_PER_OCTAVE
                )
                hz = fs.f32(440.0 * fs.f32(exp2(exponent)))
                exact_turns += Fraction(hz) / Fraction(int(FS_HZ))
                wander = abs(stored[index] / two_pi - float(exact_turns))
                if wander > worst:
                    worst = wander
            result[key] = worst * two_pi
        return result

    def _reference_wander(self, limit_samples):
        """Measured float-reference phase wander (DR-0008 Section 4 attribution).

        The pinned reference stores a binary32 partial per sample from a
        binary64 cumsum; the stored-vs-accumulator difference is the
        reference's own phase wander at its argument, which the M1 bands
        exist to absorb. For vco_2 the distortion-synthesis partials scale
        amplifies that wander into the square term by ``partials_scale / 2``
        (chain rule through tanh, whose derivative is at most 1).
        """
        result = {}
        for key in ("vco_1", "vco_2"):
            mod_signal = self.buffers["control_upsample.%s_pitch" % key]
            stored = fs.pitch_phases(
                self.physical["keyboard.midi_f0"],
                self.physical["%s.tuning" % key],
                self.physical["%s.mod_depth" % key],
                mod_signal,
            )
            count = len(mod_signal)
            if limit_samples is not None:
                count = min(count, limit_samples)
            midi = fs.f32(
                fs.f32(self.physical["keyboard.midi_f0"])
                + fs.f32(self.physical["%s.tuning" % key])
            )
            depth = fs.f32(self.physical["%s.mod_depth" % key])
            accumulator = 0.0
            worst = 0.0
            exp2 = math.exp2
            for index in range(count):
                sample = mod_signal[index]
                control = fs.f32(midi + fs.f32(depth * fs.f32(sample)))
                if control < fs.MIDI_CLAMP_MIN:
                    control = fs.MIDI_CLAMP_MIN
                elif control > fs.MIDI_CLAMP_MAX:
                    control = fs.MIDI_CLAMP_MAX
                exponent = fs.f32(
                    fs.f32(control - fs.MIDI_A440) / fs.SEMITONES_PER_OCTAVE
                )
                hz = fs.f32(440.0 * fs.f32(exp2(exponent)))
                increment = fs.f32(fs.f32(fs.TWO_PI_F32 * hz) / fs.AUDIO_RATE_F32)
                accumulator += increment
                wander = abs(stored[index] - accumulator)
                if wander > worst:
                    worst = wander
            result[key] = worst
        return result

    def _render_amps(self, limit_samples):
        raw_normalized = self.params.get("normalized_by_name", {})
        if raw_normalized and isinstance(
            next(iter(raw_normalized.values())), dict
        ):
            request_normalized = {
                name: pair["normalized"] for name, pair in raw_normalized.items()
            }
        else:
            request_normalized = dict(raw_normalized)
        if not request_normalized:
            directed = json.loads(DIRECTED_PATH.read_bytes())
            request_normalized = {
                name: pair["normalized"] for name, pair in directed["base"].items()
            }
        noise = struct.pack("<%df" % AUDIO_SAMPLES, *([0.0] * AUDIO_SAMPLES))
        request = fi.ResolvedRequest(
            self.params["sound_index"],
            request_normalized,
            {
                "seed": 13,
                "slot": self.params["noise_slot"],
                "sample_count": AUDIO_SAMPLES,
                "sha256": hashlib.sha256(noise).hexdigest(),
                "samples": noise,
            },
            physical=dict(self.physical),
            execution_status="canonical-batched",
        )
        rendered = cp.ControlPathModel(request).render()
        amps = []
        for name in (
            "control_upsample.vco_1_amp",
            "control_upsample.vco_2_amp",
            "control_upsample.noise_amp",
        ):
            values = rendered[name]
            if limit_samples is not None:
                values = values[:limit_samples]
            amps.append(values)
        return amps


def _band(max_f0: float) -> dict:
    for band in M1_BANDS:
        if max_f0 <= band["max_f0"]:
            return band
    return M1_BANDS[-1]


def _band_verdict(max_abs: float, snr: float | None, band: dict) -> str:
    if not (max_abs <= band["max_abs"]):
        return "FAIL"
    if band["snr_db"] is not None:
        if snr is None or not (snr >= band["snr_db"]):
            return "FAIL"
    return "PASS"


def _paired_row(reference, candidate, band):
    measurement = pm.compare_paired(
        reference,
        candidate,
        reference_rate_hz=FS_HZ,
        candidate_rate_hz=FS_HZ,
        unit=COMPARISON_UNIT,
        window_samples=len(reference),
        spectral=False,
    )
    m = measurement["metrics"]

    def value(key):
        metric = m.get(key)
        return None if metric is None else metric.get("value")

    snr = value("snr_db")
    max_abs = value("max_abs_error")
    row = {
        "max_abs_error": max_abs,
        "max_abs_error_index": value("max_abs_error_index"),
        "first_divergence_index": value("first_divergence_index"),
        "mean_error": value("mean_error"),
        "error_rms": value("error_rms"),
        "snr_db": snr,
        "mismatch_count": value("mismatch_count"),
        "reference_peak": value("reference.peak"),
        "candidate_peak": value("candidate.peak"),
        "estimator": m.get("estimator", {}).get("version"),
    }
    row["band"] = band["name"] if band else None
    row["verdict"] = (
        _band_verdict(max_abs, snr, band) if band is not None else None
    )
    return row


class _InlineLut:
    """Loop-inlined evaluation data for one table + interpolation order.

    Same integer arithmetic as ``QuarterWaveTable.evaluate`` (linear) and a
    3-point integer quadratic interpolation (the DR-0008 Section 5
    pre-authorized fallback shape), kept outside the primitives package
    because the quadratic order is a sweep candidate, not a landed
    primitive.
    """

    def __init__(self, table: fp_lut.QuarterWaveTable, order: str):
        if order not in ("linear", "quadratic"):
            raise ValueError(order)
        self.table = table
        self.order = order
        self.entries = table.entries
        self.endpoint = table.endpoint
        self.n = table.spec.n_entries
        self.quadrant_bits = table.spec.quadrant_bits
        self.interp_bits = table.spec.interp_bits
        self.index_bits = table.spec.index_bits
        self.mask_q = (1 << self.quadrant_bits) - 1
        self.mask_t = (1 << self.interp_bits) - 1
        self.denom = 1 << self.interp_bits
        self.quad_denom = 2 * self.denom * self.denom

    def evaluate(self, phase: int) -> int:
        entries = self.entries
        quadrant = phase >> self.quadrant_bits
        r = phase & self.mask_q
        if quadrant & 1:
            r = self.mask_q - r
        t = r & self.mask_t
        i = r >> self.interp_bits
        if i >= self.n:
            value = self.endpoint
        else:
            a = entries[i]
            b = self.endpoint if i == self.n - 1 else entries[i + 1]
            if self.order == "linear":
                value = fp_rounding.div_round(
                    a * self.denom + (b - a) * t, self.denom
                )
            else:
                p = entries[i - 1] if i >= 1 else entries[1]
                value = fp_rounding.div_round(
                    a * self.quad_denom
                    + 2 * self.denom * (b - a) * t
                    + (p - 2 * a + b) * t * (t - self.denom),
                    self.quad_denom,
                )
        return -value if quadrant in (1, 2) else value


def _build_table(entries: int) -> fp_lut.QuarterWaveTable:
    spec = fp_lut.QuarterWaveSpec(
        n_entries=entries,
        entry_format=fp_formats.parse_identity(LUT_ENTRY_FORMAT),
        phase_bits=LUT_PHASE_BITS,
    )
    return fp_lut.generate_quarter_cos(spec)


def _m2_sweep(inline: _InlineLut, points: int) -> dict:
    worst = -1.0
    worst_phase = -1
    evaluate = inline.evaluate
    scale = inline.table.spec.entry_format.scale
    modulus = 1 << LUT_PHASE_BITS
    for i in range(points):
        phase = (i * 2654435761) % modulus
        turns = phase / float(modulus)
        exact = math.cos(2.0 * math.pi * turns)
        got = evaluate(phase) / scale
        err = abs(got - exact)
        if err > worst:
            worst = err
            worst_phase = phase
    return {
        "points": points,
        "max_abs_error_vs_cos": worst,
        "argmax_phase": worst_phase,
        "analytic_bound": inline.table.analytic_error_bound(),
        "m2_limit": M2_LIMIT,
        "verdict": "PASS" if worst <= M2_LIMIT else "FAIL",
    }


def _phase_resolution_row(width: int, freq: float, samples: int) -> dict:
    """M3-style intrinsic phase drift at constant exact frequency."""
    exact_frac = Fraction(freq) / Fraction(FS_HZ)
    num = exact_frac.numerator * (1 << width)
    den = exact_frac.denominator
    k = fp_rounding.div_round(num, den)
    acc = 0
    worst = 0
    for n in range(1, samples + 1):
        acc = (acc + k) % (1 << width)
        if n % 97 == 0 or n == samples:
            exact_n = fp_rounding.div_round(num * n, den) % (1 << width)
            forward = abs(acc - exact_n)
            drift = min(forward, (1 << width) - forward)
            if drift > worst:
                worst = drift
    turns = Fraction(worst, 1 << width)
    return {
        "phase_width": width,
        "frequency_hz": freq,
        "samples": samples,
        "increment_lsb_hz": FS_HZ / float(1 << width),
        "max_drift_turns": float(turns),
        "max_drift_rad": float(turns * 2 * math.pi),
        "m3_style_bound_turns": float(Fraction(samples, 1 << (width + 1))),
        "note": "M3 as written binds the selected u32 width; other widths are informational",
    }


def _fixed_render(fixture: FixtureData, config: dict, counters) -> dict:
    """One candidate fixed render: both VCOs, noise lane, VCA x3, mix, norm.

    Returns integer-domain words and events; conversion to float for the
    paired metrics happens once, at the end (exact: division by 2^f).
    """
    audio = config["audio_fmt"]
    width = config["phase_width"]
    inline = config["inline"]
    entry_fmt = inline.table.spec.entry_format
    lut_shift = LUT_PHASE_BITS - width
    scale = audio.frac_bits
    sat = fp_ops.OverflowPolicy.SATURATE
    half = fp_rounding.RoundingMode.HALF_EVEN
    div_round = fp_rounding.div_round
    evaluate = inline.evaluate

    freq1 = fixture.freq1
    freq2 = fixture.freq2
    n_samples = fixture.sample_count

    acc1 = fp_phase.PhaseAccumulator(width)
    acc2 = fp_phase.PhaseAccumulator(width)
    turns1 = Fraction(float(fixture.physical["vco_1.initial_phase"])) / (
        2 * Fraction(math.pi)
    )
    turns2 = Fraction(float(fixture.physical["vco_2.initial_phase"])) / (
        2 * Fraction(math.pi)
    )
    acc1.inject_turns(turns1)
    acc2.inject_turns(turns2)

    amp1 = fixture.amps[0]
    amp2 = fixture.amps[1]
    ampn = fixture.amps[2]
    noise_values = fixture.noise_values
    partials_q = _quantize(
        fixture.partials_scale, PARTIALS_FMT, counters, "partials.q"
    )
    shape = fixture.shape
    one_minus_half_shape = fixture.one_minus_half_shape
    driven_one = float(PARTIALS_FMT.scale)
    tanh_f = math.tanh
    quant_scale = float(audio.scale)

    mix_acc = [0] * n_samples
    noise_post = [0] * n_samples
    level_q = [
        _quantize(level, audio, counters, "mixer.level_q") for level in fixture.levels
    ]
    post = [0, 0, 0]
    v1_out = [0] * n_samples
    v2_out = [0] * n_samples

    freq_word_den = (1 << FREQ_WORD_FRAC) * int(FS_HZ)
    for n in range(n_samples):
        fq1 = round(freq1[n] * (1 << FREQ_WORD_FRAC))
        k1 = div_round(fq1 << width, freq_word_den, half)
        p1 = acc1.step(k1)
        lp1 = p1 << lut_shift
        cos1 = evaluate(lp1)
        v1 = fp_ops.rescale(
            cos1, entry_fmt, audio, half, sat, counters, "vco_1.S4"
        )
        v1_out[n] = v1

        fq2 = round(freq2[n] * (1 << FREQ_WORD_FRAC))
        k2 = div_round(fq2 << width, freq_word_den, half)
        p2 = acc2.step(k2)
        lp2 = p2 << lut_shift
        cos2 = evaluate(lp2)
        sin2 = evaluate((lp2 - (1 << (LUT_PHASE_BITS - 2))) % (1 << LUT_PHASE_BITS))
        driven = fp_ops.mul(
            partials_q, PARTIALS_FMT, sin2, entry_fmt, PARTIALS_FMT, half, sat,
            counters, "vco_2.driven",
        )
        square_real = tanh_f((driven / driven_one) / 2.0)
        square_q = fp_ops.apply_policy(
            _half_even_float(square_real * quant_scale), audio, sat, counters,
            "vco_2.tanh_site.S4a",
        )
        left_q = fp_ops.apply_policy(
            _half_even_float(one_minus_half_shape * square_real * quant_scale),
            audio, sat, counters, "vco_2.left.S4a",
        )
        right_real = 1.0 + shape * (cos2 / float(entry_fmt.scale))
        right_q = fp_ops.apply_policy(
            _half_even_float(right_real * quant_scale), audio, sat, counters,
            "vco_2.right.S4a",
        )
        v2 = fp_ops.mul(left_q, audio, right_q, audio, audio, half, sat,
                        counters, "vco_2.S4")
        v2_out[n] = v2

        a1 = fp_ops.apply_policy(
            _half_even_float(amp1[n] * quant_scale), audio, sat, counters,
            "vca.gain_q",
        )
        a2 = fp_ops.apply_policy(
            _half_even_float(amp2[n] * quant_scale), audio, sat, counters,
            "vca.gain_q",
        )
        an = fp_ops.apply_policy(
            _half_even_float(ampn[n] * quant_scale), audio, sat, counters,
            "vca.gain_q",
        )
        nq = fp_ops.apply_policy(
            _half_even_float(noise_values[n] * quant_scale), audio, sat,
            counters, "noise.source_q",
        )
        post[0] = fp_ops.mul(v1, audio, a1, audio, audio, half, sat,
                             counters, "vca_1.S4")
        post[1] = fp_ops.mul(v2, audio, a2, audio, audio, half, sat,
                             counters, "vca_2.S4")
        post[2] = fp_ops.mul(nq, audio, an, audio, audio, half, sat,
                             counters, "vca_3.S4")
        noise_post[n] = post[2]
        total = 0
        for lane in range(3):
            total += post[lane] * level_q[lane]
        mix_acc[n] = total

    product_fmt = fp_formats.FixedFormat(
        signed=True, int_bits=6, frac_bits=2 * audio.frac_bits
    )
    mix_int = [
        fp_ops.rescale(total, product_fmt, audio, half, sat, counters, "mixer.S4")
        for total in mix_acc
    ]

    return {
        "vco_1": v1_out,
        "vco_2": v2_out,
        "noise_post": noise_post,
        "mix_int": mix_int,
        "audio": audio,
    }


def _int_to_float(values, audio) -> list:
    scale = float(audio.scale)
    return [v / scale for v in values]


def _norm_fixed(mix_int, audio, counters):
    """Strict peak>1 branch in the integer domain; S5 declared-precision reciprocal."""
    half = fp_rounding.RoundingMode.HALF_EVEN
    sat = fp_ops.OverflowPolicy.SATURATE
    f = audio.frac_bits
    peak_int = 0
    for v in mix_int:
        a = -v if v < 0 else v
        if a > peak_int:
            peak_int = a
    one = 1 << f
    branch = peak_int > one
    if branch:
        # peak_int is in Q<f> units: the real reciprocal 1/peak maps to
        # g_q = round(2^(2f) / peak_real) = round(2^(3f) / peak_int).
        g_q = fp_rounding.div_round(1 << (3 * f), peak_int, half)
        g_fmt = fp_formats.FixedFormat(signed=True, int_bits=1, frac_bits=2 * f)
        out = [
            fp_ops.mul(v, audio, g_q, g_fmt, audio, half, sat, counters, "norm.S5")
            for v in mix_int
        ]
    else:
        g_q = 1 << (2 * f)
        out = list(mix_int)
    return out, peak_int, g_q / float(1 << (2 * f)), branch


def _run_config_fixture(fixture: FixtureData, config: dict) -> dict:
    counters = fp_counters.StickyCounters()
    rendered = _fixed_render(fixture, config, counters)
    audio = rendered["audio"]
    sat = fp_ops.OverflowPolicy.SATURATE
    rows = {}

    band1 = _band(fixture.max_f1)
    band2 = _band(fixture.max_f2)
    rows["vco_1.raw"] = _paired_row(
        fixture.buffers["vco_1.raw"], _int_to_float(rendered["vco_1"], audio), band1
    )
    rows["vco_1.raw"]["reference_wander_attribution"] = {
        "max_stored_phase_wander_rad": fixture.wander["vco_1"],
        "amplification": 1.0,
        "note": (
            "float-reference drift attribution (DR-0008 Section 4): the "
            "reference stores binary32 phase partials; this wander term is "
            "reference-side, not fixed-model error"
        ),
    }
    rows["vco_2.raw"] = _paired_row(
        fixture.buffers["vco_2.raw"], _int_to_float(rendered["vco_2"], audio), band2
    )
    rows["vco_2.raw"]["reference_wander_attribution"] = {
        "max_stored_phase_wander_rad": fixture.wander["vco_2"],
        "amplification": fixture.partials_scale / 2.0,
        "square_term_attribution": fixture.partials_scale / 2.0 * fixture.wander["vco_2"],
        "note": (
            "float-reference drift attribution (DR-0008 Section 4): the "
            "reference stores binary32 phase partials and the distortion-"
            "synthesis partials scale amplifies the wander into the square "
            "term; the M1 selection bands were derived for the sine VCO, so "
            "vco_2 rows are measured mandatory rows and are EXCLUDED from "
            "the minimal-format selection rule, pending the dedicated vco_2 "
            "band definition in #53"
        ),
    }

    float_post_1 = [
        fs.f32(a * b) for a, b in zip(fixture.buffers["vco_1.raw"], fixture.amps[0])
    ]
    float_post_2 = [
        fs.f32(a * b) for a, b in zip(fixture.buffers["vco_2.raw"], fixture.amps[1])
    ]
    float_noise_post = [
        fs.f32(a * b) for a, b in zip(fixture.noise_values, fixture.amps[2])
    ]
    rows["noise.post_vca"] = _paired_row(
        float_noise_post,
        _int_to_float(rendered["noise_post"], audio),
        None,
    )
    rows["noise.post_vca"]["identity_note"] = (
        "noise stream exact-bytes (never swept); row isolates the audio-width "
        "quantization of the noise lane path"
    )

    float_mix = _mix_reference(
        [float_post_1, float_post_2, float_noise_post],
        fixture.levels,
    )
    mix_int = rendered["mix_int"]
    scale_note = None
    if fixture.mix_scale is not None:
        scale = fixture.mix_scale
        exact = Fraction(scale)
        float_mix = [fs.f32(value * scale) for value in float_mix]
        div_round = fp_rounding.div_round
        half = fp_rounding.RoundingMode.HALF_EVEN
        # s * mix is exact-rational times a Q<f> integer; the product is
        # already in Q<f> units, rounded half-even once at the declared
        # stress site, with saturation counted (word-headroom evidence).
        mix_int = [
            fp_ops.apply_policy(
                div_round(exact.numerator * total, exact.denominator, half),
                audio,
                sat,
                counters,
                "stress.mix_scale.S4",
            )
            for total in rendered["mix_int"]
        ]
        scale_note = (
            "declared harness mix-domain scale to the released DR-0006 "
            "normalize anchor, exact rational, applied once on each side"
        )
    rows["mixer.pre_normalization"] = _paired_row(
        float_mix, _int_to_float(mix_int, audio), None
    )
    if scale_note:
        rows["mixer.pre_normalization"]["scale_note"] = scale_note

    norm_float = _norm_reference(float_mix)
    norm_fixed, peak_int, gain_fixed, branch_fixed = _norm_fixed(
        mix_int, audio, counters
    )
    rows["mixer.output"] = _paired_row(
        norm_float[0], _int_to_float(norm_fixed, audio), None
    )
    rows["mixer.output"]["post_normalization"] = True
    rows["normalization"] = {
        "float_branch": norm_float[3],
        "float_peak": norm_float[1],
        "float_gain": norm_float[2],
        "fixed_branch": branch_fixed,
        "fixed_peak": peak_int / float(audio.scale),
        "fixed_gain": gain_fixed,
        "reported_after_mandatory_rows": True,
    }
    rows["saturation_counters"] = counters.as_json()
    return rows


def _proxies(config: dict) -> dict:
    audio = config["audio_fmt"]
    table = config["inline"].table
    quadratic = config["lut_order"] == "quadratic"
    lut_bits = (table.spec.n_entries + 1) * table.spec.entry_format.width
    mults = 1 + (2 if quadratic else 0)  # interp mult(s)
    mults += 2  # vco_2 driven + product
    mults += 3  # VCA lanes
    mults += 3  # mixer lanes
    adds = 2 + 2 * (0 if quadratic else 1)  # phase + interp adds (approx)
    return {
        "proxy_kind": (
            "operation/storage-count proxy only - NOT a PPA result; no "
            "synthesis, area, timing, power, layout, or hardware claim"
        ),
        "lut_storage_bits": lut_bits,
        "audio_word_bits": audio.width,
        "phase_bits": config["phase_width"],
        "multiply_site_count_per_sample": mults,
        "note": "site counts enumerate declared narrowing/multiply sites, not gates",
    }


def _site_enumeration(config: dict) -> list:
    audio = config["audio_fmt"]
    return [
        {"site": "S2", "where": "K formation", "mode": "half_even",
         "guard": f"Q16.15 -> U{config['phase_width']} product domain"},
        {"site": "S3", "where": "initial_phase turn injection", "mode": "half_even",
         "guard": "exact Fraction -> U%d modular" % config["phase_width"]},
        {"site": "S4", "where": "module output narrowing",
         "mode": "half_even",
         "guard": f"product/accumulator domain -> {audio.identity}"},
        {"site": "S4a", "where": "vco_2 tanh/left/right quantization",
         "mode": "half_even",
         "guard": f"harness binary64 -> {audio.identity}"},
        {"site": "S5", "where": "normalization reciprocal application",
         "mode": "half_even",
         "guard": "Q1.(2f) reciprocal -> %s" % audio.identity},
        {"site": "LUT", "where": "interpolation rounding", "mode": "half_even",
         "guard": "entry-format domain, denom 2^interp_bits"},
        {"site": "mixer.accumulator", "where": "three-lane accumulation",
         "mode": "no intermediate rounding",
         "guard": "exact product domain Q6.(2f) before single S4 narrowing"},
    ]


def _sweep_configs(quick: bool) -> list:
    tables = {entries: _build_table(entries) for entries in LUT_ENTRIES}
    selected = fp_formats.parse_identity("Q2.21")
    configs = []
    if quick:
        grid = [
            (fp_formats.parse_identity("Q2.21"), 32, 4096, "linear"),
            (fp_formats.parse_identity("Q1.14"), 32, 1024, "quadratic"),
        ]
    else:
        grid = []
        # Dimension A: LUT x phase at the selected audio word.
        for entries in LUT_ENTRIES:
            for order in LUT_ORDERS:
                for width in PHASE_WIDTHS:
                    grid.append((selected, width, entries, order))
        # Dimension B: audio word at the selected LUT/phase point.
        for identity in AUDIO_FORMATS:
            fmt = fp_formats.parse_identity(identity)
            if fmt.identity != selected.identity:
                grid.append((fmt, 32, 4096, "linear"))
        # Worst-corner interaction bound: narrowest word, smallest LUT,
        # quadratic, narrowest phase.
        grid.append(
            (fp_formats.parse_identity(AUDIO_FORMATS[0]), PHASE_WIDTHS[0],
             LUT_ENTRIES[0], "quadratic")
        )
    for audio, width, entries, order in grid:
        inline = _InlineLut(tables[entries], order)
        configs.append(
            {
                "config_id": f"{audio.identity}+u{width}+{entries//1024}K-{order}",
                "audio_fmt": audio,
                "phase_width": width,
                "lut_entries": entries,
                "lut_order": order,
                "inline": inline,
            }
        )
    return configs


def _mutations_probe(fixture: FixtureData, config: dict) -> list:
    """M7-style negative controls, ratio-scored against the base render.

    A mutation must move the measured max-abs error by at least
    MUTATION_RATIO (a preregistered harness sensitivity bar) over the
    unmutated base render on the same fixture. A ratio test is used
    instead of a band verdict because the float-vs-fixed base error sits
    at the reference drift floor (see the attribution rows); a mutated
    render that stays at the floor is a metric defect, not a pass.
    """
    results = []
    base = _run_config_fixture(fixture, config)
    base_err = base["vco_1.raw"]["max_abs_error"]
    band1 = _band(fixture.max_f1)

    entries = config["inline"].entries
    shift = 128  # index rotation = a fixed waveform phase offset, not a LSB nudge
    wrong_table = fp_lut.QuarterWaveTable(
        spec=config["inline"].table.spec,
        entries=tuple(entries[(i + shift) % len(entries)] for i in range(len(entries))),
        endpoint=config["inline"].endpoint,
    )
    wrong_config = dict(config)
    wrong_config["inline"] = _InlineLut(wrong_table, config["lut_order"])
    wrong = _run_config_fixture(fixture, wrong_config)
    wrong_err = wrong["vco_1.raw"]["max_abs_error"]
    results.append(
        {
            "mutation": "wrong-lut-entry(+128-entry index rotation)",
            "max_abs_error": wrong_err,
            "base_max_abs_error": base_err,
            "sensitivity_ratio": wrong_err / base_err if base_err else None,
            "sensitivity_bar": MUTATION_RATIO,
            "detected": bool(base_err and wrong_err / base_err >= MUTATION_RATIO),
        }
    )

    dropped = _run_config_fixture(fixture, config)
    # dropped-phase-increment probe: re-render with every 97th K zeroed.
    div_round = fp_rounding.div_round
    audio = config["audio_fmt"]
    inline = config["inline"]
    entry_fmt = inline.table.spec.entry_format
    lut_shift = LUT_PHASE_BITS - config["phase_width"]
    sat = fp_ops.OverflowPolicy.SATURATE
    half = fp_rounding.RoundingMode.HALF_EVEN
    evaluate = inline.evaluate
    freq1 = fixture.freq1
    width = config["phase_width"]
    acc = fp_phase.PhaseAccumulator(width)
    acc.inject_turns(
        Fraction(float(fixture.physical["vco_1.initial_phase"]))
        / (2 * Fraction(math.pi))
    )
    freq_word_den = (1 << FREQ_WORD_FRAC) * int(FS_HZ)
    out = []
    for n in range(fixture.sample_count):
        fq = round(freq1[n] * (1 << FREQ_WORD_FRAC))
        k = div_round(fq << width, freq_word_den, half)
        if n % 97 == 0:
            k = 0
        lp = acc.step(k) << lut_shift
        out.append(
            fp_ops.rescale(evaluate(lp), entry_fmt, audio, half, sat, None, None)
        )
    row = _paired_row(
        fixture.buffers["vco_1.raw"], _int_to_float(out, audio), band1
    )
    mut_err = row["max_abs_error"]
    results.append(
        {
            "mutation": "dropped-phase-increment(every 97th K zeroed)",
            "max_abs_error": mut_err,
            "base_max_abs_error": base_err,
            "sensitivity_ratio": mut_err / base_err if base_err else None,
            "sensitivity_bar": MUTATION_RATIO,
            "detected": bool(base_err and mut_err / base_err >= MUTATION_RATIO),
        }
    )
    return results


def _recommend(cases: dict, configs: list, m2: list, phase_rows: list) -> list:
    """Preregistered selection rule over the measured tables.

    Gates, in measured order:
    - audio word: zero saturation events on the normalization-stress anchor
      case (a word that cannot hold the released 3.9478583336 pre-normal
      peak without clipping is out);
    - phase width: generalized M3-style drift bound at or below the
      DR-0008 M3 number (176400 * 2^-33 turns) - measured per width;
    - LUT geometry: M2 PASS in the intrinsic table.
    The operator-selected point (C1/C2/C5 as ruled 2026-09-19) is reported
    first with its measured gates regardless of minimality; smaller
    passing variants follow as CANDIDATE alternates. Nothing here is
    ratified: every entry is pending #53.
    """
    rec = []
    m3_bound = Fraction(176400, 1 << 33)
    widths_ok = {
        row["phase_width"]
        for row in phase_rows
        if row["frequency_hz"] == 440.0
        and Fraction(row["max_drift_turns"]).limit_denominator(10**12) <= m3_bound
    }
    m2_pass_geoms = {
        (row["n_entries"], row["order"]) for row in m2 if row["verdict"] == "PASS"
    }
    stress = next(
        (label for label in cases if label.startswith("normalization-stress")),
        None,
    )

    def gates(config):
        audio_ok = True
        if stress is not None:
            counters = cases[stress]["configs"][config["config_id"]]["rows"][
                "saturation_counters"
            ]
            audio_ok = counters["total_saturation"] == 0
        width_ok = config["phase_width"] in widths_ok
        lut_ok = (config["lut_entries"], config["lut_order"]) in m2_pass_geoms
        return audio_ok, width_ok, lut_ok

    def entry(role, config, notes):
        return {
            "role": role,
            "config_id": config["config_id"],
            "audio_fmt": config["audio_fmt"],
            "phase_width": config["phase_width"],
            "lut_entries": config["lut_entries"],
            "lut_order": config["lut_order"],
            "gates": {
                "normalization_anchor_saturation_zero": gates(config)[0],
                "m3_style_width_bound_met": gates(config)[1],
                "m2_pass": gates(config)[2],
            },
            "notes": notes,
            "status": "CANDIDATE pending #53 ratification",
        }

    selected = next(
        (c for c in configs if c["config_id"] == "Q2.21+u32+4K-linear"), None
    )
    if selected is not None:
        rec.append(
            entry(
                "operator-selected-point (C1/C2/C5, ruling 2026-09-19)",
                selected,
                "reported first with measured gates; minimality assessed "
                "separately below",
            )
        )
    order = {c["config_id"]: i for i, c in enumerate(configs)}
    passing = [
        cid
        for cid in order
        if all(gates(configs[order[cid]]))
        and cid != (selected or {}).get("config_id")
    ]
    if passing:
        best = min(
            passing,
            key=lambda cid: (
                fp_formats.parse_identity(configs[order[cid]]["audio_fmt"]).frac_bits,
                configs[order[cid]]["lut_entries"],
                configs[order[cid]]["phase_width"],
                0 if configs[order[cid]]["lut_order"] == "linear" else 1,
            ),
        )
        rec.append(
            entry(
                "minimal-variant-meeting-all-gates",
                configs[order[best]],
                "smallest swept word/geometry meeting the measured gates; "
                "the vco_1 float-vs-fixed rows of every candidate sit at the "
                "reference drift floor (see per-fixture "
                "reference_phase_error_rad), so the gates use the intrinsic "
                "M2/M3 tables and the normalization-anchor saturation count",
            )
        )
    m2_pass = [row for row in m2 if row["verdict"] == "PASS"]
    if m2_pass:
        best_m2 = min(
            m2_pass,
            key=lambda row: (row["n_entries"], row["interp_bits"],
                             0 if row["order"] == "linear" else 1),
        )
        rec.append(
            {
                "role": "minimal-M2-geometry",
                "lut_entries": best_m2["n_entries"],
                "interp_order": best_m2["order"],
                "entry_format": LUT_ENTRY_FORMAT,
                "max_abs_error_vs_cos": best_m2["max_abs_error_vs_cos"],
                "notes": "smallest LUT whose intrinsic error meets M2; the DR-0008 C5 pre-authorized fallback shape",
                "status": "CANDIDATE pending #53 ratification",
            }
        )
    return rec


_WORKER_FIXTURES = {}
_WORKER_TABLES = {}
_WORKER_LIMIT = None
_CASE_SPECS = {}


def _build_case_specs(quick: bool, limit) -> dict:
    """Case registry: label -> (source fixture, optional level scale).

    The normalization-stress case reuses the committed saw fixture and
    scales its float pre-normalization mix by the exact rational of the
    released DR-0006 normalize anchor (declared harness scale, applied at
    one declared site on both sides); it is a sweep-directed stress view,
    not a new evidence fixture.
    """
    specs = {}
    if quick:
        specs["boundary:vco_1.initial_phase:upper"] = (
            "boundary:vco_1.initial_phase:upper",
            None,
        )
        return specs
    for case_id in sorted(json.loads(RECORD_PATH.read_bytes())["fixtures"]):
        specs[case_id] = (case_id, None)
    base = FixtureData("waveform:vco_2:saw", limit_samples=limit)
    post = [
        [fs.f32(a * b) for a, b in zip(base.buffers["vco_1.raw"], base.amps[0])],
        [fs.f32(a * b) for a, b in zip(base.buffers["vco_2.raw"], base.amps[1])],
        [fs.f32(a * b) for a, b in zip(base.noise_values, base.amps[2])],
    ]
    mix = _mix_reference(post, base.levels)
    peak = max(abs(value) for value in mix)
    scale = fs.f32(NORMALIZE_ANCHOR / peak)
    label = "normalization-stress:anchor-%s" % NORMALIZE_ANCHOR
    specs[label] = ("waveform:vco_2:saw", scale)
    return specs


def _worker_init(case_labels, limit, case_specs):
    global _WORKER_FIXTURES, _WORKER_TABLES, _WORKER_LIMIT, _CASE_SPECS
    _WORKER_FIXTURES = {label: None for label in case_labels}
    _WORKER_TABLES = {}
    _WORKER_LIMIT = limit
    _CASE_SPECS = dict(case_specs)


def _worker_fixture(label):
    if _WORKER_FIXTURES.get(label) is None:
        source_id, scale = _CASE_SPECS[label]
        _WORKER_FIXTURES[label] = FixtureData(
            source_id,
            limit_samples=_WORKER_LIMIT,
            mix_scale=scale,
            case_label=label,
        )
    return _WORKER_FIXTURES[label]


def _worker_table(entries):
    if entries not in _WORKER_TABLES:
        _WORKER_TABLES[entries] = _build_table(entries)
    return _WORKER_TABLES[entries]


def _config_from_descriptor(descriptor):
    inline = _InlineLut(
        _worker_table(descriptor["lut_entries"]), descriptor["lut_order"]
    )
    return {
        "config_id": descriptor["config_id"],
        "audio_fmt": fp_formats.parse_identity(descriptor["audio_fmt"]),
        "phase_width": descriptor["phase_width"],
        "lut_entries": descriptor["lut_entries"],
        "lut_order": descriptor["lut_order"],
        "inline": inline,
    }


def _worker_job(job):
    case_id, descriptor = job
    fixture = _worker_fixture(case_id)
    config = _config_from_descriptor(descriptor)
    rows = _run_config_fixture(fixture, config)
    return case_id, descriptor["config_id"], rows


def _descriptor(config):
    return {
        "config_id": config["config_id"],
        "audio_fmt": config["audio_fmt"].identity,
        "phase_width": config["phase_width"],
        "lut_entries": config["lut_entries"],
        "lut_order": config["lut_order"],
    }


def _case_summary(fixture):
    return {
        "fixture": {
            "case_id": fixture.case_id,
            "source_fixture": fixture.source_case_id,
            "mix_scale": fixture.mix_scale,
            "sample_count": fixture.sample_count,
            "max_instantaneous_f_vco_1_hz": fixture.max_f1,
            "max_instantaneous_f_vco_2_hz": fixture.max_f2,
            "band_vco_1": _band(fixture.max_f1)["name"],
            "band_vco_2": _band(fixture.max_f2)["name"],
            "alias_exposure": {
                "vco_1_above_nyquist": fixture.max_f1 > FS_HZ / 2,
                "vco_2_above_nyquist": fixture.max_f2 > FS_HZ / 2,
            },
            "noise_bit_exact_identity_ok": fixture.noise_digest_ok,
            "reference_phase_error_rad": fixture.reference_phase_error,
            "levels": fixture.levels,
        },
        "configs": {},
    }


def _render_cases(configs, limit, workers):
    """Render every (case, config) job; deterministic regardless of workers."""
    case_ids = list(_CASE_SPECS)
    descriptors = [_descriptor(config) for config in configs]
    jobs = [(case_id, descriptor) for case_id in case_ids for descriptor in descriptors]
    results = {}
    used_pool = False
    if workers > 1:
        try:
            import multiprocessing as mp

            with mp.Pool(
                processes=min(workers, len(jobs)),
                initializer=_worker_init,
                initargs=(case_ids, limit, _CASE_SPECS),
            ) as pool:
                for case_id, config_id, rows in pool.imap_unordered(
                    _worker_job, jobs
                ):
                    results[(case_id, config_id)] = rows
            used_pool = len(results) == len(jobs)
            if not used_pool:
                print(
                    f"note: pool returned {len(results)}/{len(jobs)} jobs",
                    file=sys.stderr,
                )
        except Exception as exc:  # noqa: BLE001 - surfaced below, serial fallback
            print(
                f"note: pool unavailable ({type(exc).__name__}: {exc}); "
                "falling back to serial",
                file=sys.stderr,
            )
            results = {}
    if not used_pool:
        results = results or {}
        _worker_init(case_ids, limit, _CASE_SPECS)
        for job in jobs:
            case_id, config_id, rows = _worker_job(job)
            results[(case_id, config_id)] = rows

    cases = {}
    for case_id in case_ids:
        fixture = _worker_fixture(case_id)
        cases[case_id] = _case_summary(fixture)
        for descriptor in descriptors:
            config = _config_from_descriptor(descriptor)
            cases[case_id]["configs"][descriptor["config_id"]] = {
                "rows": results[(case_id, descriptor["config_id"])],
                "config": config,
            }
    return cases


def run(quick: bool, out: Path, write_traces: bool, m2_points: int, workers: int) -> dict:
    global _CASE_SPECS
    started = time.time()
    limit = QUICK_SAMPLES if quick else None
    _CASE_SPECS = _build_case_specs(quick, limit)
    case_ids = list(_CASE_SPECS)

    configs = _sweep_configs(quick)
    tables = {}
    tables_meta = {}
    for entries in LUT_ENTRIES:
        table = _build_table(entries)
        tables[entries] = table
        tables_meta[entries] = {
            "n_entries": entries,
            "entry_format": table.spec.entry_format.identity,
            "phase_bits": table.spec.phase_bits,
            "index_bits": table.spec.index_bits,
            "interp_bits": table.spec.interp_bits,
            "sha256": table.sha256(),
        }

    m2_rows = []
    for entries in LUT_ENTRIES:
        for order in LUT_ORDERS:
            inline = _InlineLut(tables[entries], order)
            row = _m2_sweep(inline, m2_points)
            row.update(
                {
                    "n_entries": entries,
                    "order": order,
                    "interp_bits": inline.interp_bits,
                }
            )
            m2_rows.append(row)

    probe_source, probe_scale = _CASE_SPECS[case_ids[0]]
    probe_fixture = FixtureData(
        probe_source,
        limit_samples=limit,
        mix_scale=probe_scale,
        case_label=case_ids[0],
    )
    phase_rows = []
    probe_freqs = [27.5, 440.0, 12543.9]
    for width in PHASE_WIDTHS:
        for freq in probe_freqs:
            phase_rows.append(
                _phase_resolution_row(width, freq, probe_fixture.sample_count)
            )
    del probe_fixture

    cases = _render_cases(configs, limit, workers)

    mutations = []
    if not quick:
        _worker_init(case_ids, limit, _CASE_SPECS)
        driving = _worker_fixture("boundary:vco_1.initial_phase:upper")
        selected_config = next(
            c for c in configs if c["config_id"].startswith("Q2.21+u32+4K-linear")
        )
        mutations = _mutations_probe(driving, selected_config)

    plain_configs = [
        {
            "config_id": c["config_id"],
            "audio_fmt": c["audio_fmt"].identity,
            "phase_width": c["phase_width"],
            "lut_entries": c["lut_entries"],
            "lut_order": c["lut_order"],
        }
        for c in configs
    ]
    cases_plain = {
        cid: {
            "fixture": case["fixture"],
            "configs": {
                config_id: {
                    "rows": entry["rows"],
                    "proxies": _proxies(entry["config"]),
                    "sites": _site_enumeration(entry["config"]),
                }
                for config_id, entry in case["configs"].items()
            },
        }
        for cid, case in cases.items()
    }

    receipt = {
        "schema": SCHEMA,
        "schema_version": 1,
        "semantic_version": SEMVER,
        "issue": 51,
        "status": "candidate-pre-ratification",
        "numeric_contract": "unbound:#53",
        "mode": "quick" if quick else "full",
        "sample_count": cases[case_ids[0]]["fixture"]["sample_count"],
        "policy_notes": [
            "frequency path enters as declared binary32 input (pinned upstream op order); MIDI-domain fixed arithmetic (M4) is out of scope",
            "noise stream exact-bytes by identity (DR-0008 Section 7), never swept; only its post-VCA audio width sweeps",
            "vco_2 tanh site evaluated at harness binary64; fixed tanh is #74's declaration and is excluded from the swept budget",
            "mandatory gain/error rows precede normalization rows; normalization never precedes them",
            "the normalization-stress case reuses the committed saw fixture with the released DR-0006 normalize-anchor as a declared harness level scale; it is a sweep-directed stress view, not a new evidence fixture",
            "MEASURED FINDING: every vco_1/vco_2 float-vs-fixed max_abs_error sits at the per-fixture reference_phase_error_rad floor - the float reference's own binary32 cumsum drift (increment-rounding bias + stored-partial wander) exceeds the M1 band-1/2 limits over the full 176400-sample clip, so no candidate can separate on those rows; band recalibration before preregistration is #53's action under the frozen rubric-v0 policy (thresholds never increase after RTL freeze)",
            "vco_2 band verdicts are reference-drift-dominated (drift amplified by the partials scale); they are reported as mandatory measured rows with a drift-attribution row each and excluded from the minimal-format selection rule",
            "candidate separation therefore runs on the drift-free surfaces: intrinsic M2 (LUT), intrinsic M3-style phase resolution (width), normalization-anchor saturation (word headroom), and the noise/mix quantization rows",
            "every selection below is a CANDIDATE pending #53 ratification; nothing is accepted",
            "proxies are operation/storage counts, explicitly NOT PPA",
        ],
        "grids": {
            "audio_formats": list(AUDIO_FORMATS),
            "phase_widths": list(PHASE_WIDTHS),
            "lut_entries": list(LUT_ENTRIES),
            "lut_orders": list(LUT_ORDERS),
            "lut_entry_format": LUT_ENTRY_FORMAT,
            "lut_phase_bits": LUT_PHASE_BITS,
        },
        "lut_tables": tables_meta,
        "fixtures": {cid: cases_plain[cid]["fixture"] for cid in case_ids},
        "intrinsic": {
            "m2_lut_vs_cos": m2_rows,
            "phase_resolution_m3_style": phase_rows,
        },
        "configs": plain_configs,
        "cases": {
            cid: {
                "fixture": case["fixture"],
                "configs": case["configs"],
            }
            for cid, case in cases_plain.items()
        },
        "mutations": mutations,
        "executed_checks": [],
    }
    receipt["recommendations"] = _recommend(
        receipt["cases"], plain_configs, m2_rows, phase_rows
    )

    if write_traces and not quick:
        _worker_init(case_ids, limit, _CASE_SPECS)
        _write_retained_traces(case_ids, configs, receipt)

    elapsed = time.time() - started
    receipt["executed_checks"].append(
        {
            "check": "sweep execution",
            "mode": receipt["mode"],
            "configs_rendered": len(configs) * len(case_ids),
            "bounded": True,
            "timing_note": (
                "wall-clock time is printed at run time and deliberately "
                "omitted here so receipts are byte-identical across runs"
            ),
        }
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(f"elapsed: {elapsed:.1f}s")
    return receipt


def _write_retained_traces(case_ids, configs, receipt) -> None:
    """Retain raw traces for Pareto candidates (AC: candidates keep raw traces)."""
    roles = {rec.get("config_id") for rec in receipt["recommendations"]}
    roles.add("Q2.21+u32+4K-linear")  # the operator-selected default point
    TRACES_OUT.mkdir(parents=True, exist_ok=True)
    retained = []
    for config in configs:
        if config["config_id"] not in roles:
            continue
        worst_case, worst_err = None, -1.0
        for case_id in case_ids:
            rows = receipt["cases"][case_id]["configs"][config["config_id"]]["rows"]
            err = rows["vco_1.raw"]["max_abs_error"] or 0.0
            if err > worst_err:
                worst_err, worst_case = err, case_id
        fixture = _worker_fixture(worst_case)
        counters = fp_counters.StickyCounters()
        rendered = _fixed_render(fixture, config, counters)
        audio = rendered["audio"]
        target = TRACES_OUT / config["config_id"]
        target.mkdir(parents=True, exist_ok=True)
        for name, values in (
            ("vco_1.raw", rendered["vco_1"]),
            ("mixer.pre_normalization", rendered["mix_int"]),
        ):
            payload = fs.f32le_bytes(_int_to_float(values, audio))
            path = target / f"{worst_case}.{name}.f32le"
            path.write_bytes(payload)
            retained.append(
                {
                    "config_id": config["config_id"],
                    "fixture": worst_case,
                    "trace": name,
                    "file": str(path.relative_to(ROOT)),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            )
    receipt["retained_raw_traces"] = {
        "role": "Pareto-candidate raw traces (fixed render, float32 view); regenerable deterministically by this tool",
        "files": retained,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--quick", action="store_true", help="bounded schema/determinism mode")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--no-traces", action="store_true")
    parser.add_argument("--m2-points", type=int, default=1 << 19)
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    receipt = run(
        quick=args.quick,
        out=args.out,
        write_traces=not args.no_traces,
        m2_points=min(args.m2_points, 1 << 20),
        workers=1 if args.quick else max(1, args.jobs),
    )
    recommendations = receipt["recommendations"]
    print(
        f"wrote {args.out} "
        f"({len(receipt['cases'])} case(s), {len(receipt['configs'])} config(s), "
        f"{len(recommendations)} recommendation(s))"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
