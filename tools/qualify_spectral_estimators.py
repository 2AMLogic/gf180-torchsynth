"""Run the preregistered analytic grids; never renders Voice or opens holdout."""

from __future__ import annotations

import argparse
import cmath
import hashlib
import json
import math
import struct
import sys
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import spectral_estimators as se
from torchsynth_voice.paired_metrics import Limit
from torchsynth_voice.scorecard import make_report, validate_report

REPORT = ROOT / "sim/qualification/spectral-noise-mix-v1.json"
TRUTH_SOURCE = (
    "spec/SPECTRAL-ESTIMATORS.md; preregistered independent analytic constructions v1"
)

# Frozen numerical definitions, independent of the implementation under test.
BANDS = (
    ("0_20", 0, 20),
    ("20_200", 20, 200),
    ("200_2000", 200, 2000),
    ("2000_20000", 2000, 20000),
    ("20000_up", 20000, None),
)
TRUTH_LAGS = (1, 2, 8, 32)
BOUNDS = {
    "harmonic.frequency": 1e-9,
    **{f"harmonic.h{h}_ratio": 1e-10 for h in (2, 3, 4, 5)},
    "harmonic.folded_power": 1e-10,
    "harmonic.inharmonic_power": 1e-10,
    **{
        f"band.{band}.{kind}": 1e-10
        for band, _, _ in BANDS
        for kind in ("power", "density", "log_power")
    },
    "signal.dc": 1e-12,
    "signal.rms": 1e-12,
    "noise.identity": 0.0,
    "noise.mean": 1e-12,
    "noise.variance": 1e-12,
    **{f"noise.ac.{lag}": 1e-12 for lag in TRUTH_LAGS},
    **{f"mix.{name}.gain": 1e-12 for name in ("a", "b")},
    **{f"mix.{name}.gain_db": 1e-10 for name in ("a", "b")},
    "mix.dc_offset": 1e-12,
    "mix.output.dc": 1e-12,
    "mix.residual_rms": 1e-12,
    "mix.boundary_count": 0.0,
    **{
        f"normalization.{field}": 0.0
        for field in (
            "peak_abs",
            "peak_value",
            "peak_index",
            "decision_match",
            "reported_peak_error",
            "reported_index_match",
            "reciprocal_error",
        )
    },
    "normalization.gain": 1e-12,
    "normalization.rule_error_max": 1e-12,
}


def encoded(value):
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def f32(value):
    return struct.unpack("<f", struct.pack("<f", float(value)))[0]


def tone(n, frequency, *, rate=4096, phase=0.17):
    return [math.sin(2 * math.pi * frequency * i / rate + phase) for i in range(n)]


def fourier_wave(n, frequency, shape, scale=1, phase=0.17):
    coefficients = (
        [1 / h if h % 2 else 0 for h in range(1, 6)]
        if shape == "square"
        else [(-1) ** (h + 1) / h for h in range(1, 6)]
    )
    # Independent time-domain Fourier synthesis, never uses the FFT under test.
    return [
        scale
        * math.fsum(
            a * math.sin(h * (2 * math.pi * frequency * i / 4096 + phase))
            for h, a in enumerate(coefficients, 1)
        )
        for i in range(n)
    ], coefficients


def direct_power(samples):
    """Quadratic complex DFT truth, independent of NumPy FFT/estimator code."""
    n = len(samples)
    result = []
    for k in range(n // 2 + 1):
        terms = [
            x * cmath.exp(-2j * math.pi * k * i / n) for i, x in enumerate(samples)
        ]
        coefficient = (
            complex(math.fsum(t.real for t in terms), math.fsum(t.imag for t in terms))
            / n
        )
        weight = 1 if k == 0 or 2 * k == n else 2
        result.append(weight * abs(coefficient) ** 2)
    return result


def noise_words(n, seed):
    """Explicit xorshift32 integer construction, not Torch canonical noise."""
    state, words = seed, []
    for _ in range(n):
        state ^= (state << 13) & 0xFFFFFFFF
        state ^= state >> 17
        state ^= (state << 5) & 0xFFFFFFFF
        state &= 0xFFFFFFFF
        words.append(state - 2**31)
    return words


def rational_statistics(words):
    x = [Fraction(w, 2**31) for w in words]
    mean = sum(x) / len(x)
    centered = [v - mean for v in x]
    denominator = sum(v * v for v in centered)
    return {
        "noise.mean": float(mean),
        "noise.variance": float(denominator / len(x)),
        **{
            f"noise.ac.{lag}": float(
                sum(a * b for a, b in zip(centered, centered[lag:])) / denominator
            )
            for lag in TRUTH_LAGS
        },
    }


class Trials:
    def __init__(self):
        self.rows, self.records, self.cases, self.mutations = [], {}, [], []
        self.errors = defaultdict(list)

    def add(
        self,
        case,
        result,
        expected=None,
        *,
        domain,
        refusal=None,
        detector=None,
        magnitude=None,
    ):
        expected = expected or {}
        limits = {
            key: Limit(
                value,
                BOUNDS[key],
                result["metrics"][key]["unit"],
                TRUTH_SOURCE,
            )
            for key, value in expected.items()
        }
        rows, record = se.scorecard_rows(
            result, case_id=case, trace=f"synthetic:{domain}", limits=limits
        )
        self.rows.extend(rows)
        self.records[rows[0]["artifact"]["identity"]] = record.decode()
        indexed = {r["property"]: r for r in rows}
        checks = []
        for key in expected:
            row = indexed[key]
            if (
                detector is None
                and key not in (refusal or {})
                and row["verdict"] != "PASS"
            ):
                raise AssertionError(
                    f"{case}/{key}: expected PASS, got {row['verdict']} {row['validity']}; {row['observed']} vs {expected[key]}"
                )
            if detector is None and row["observed"] is not None:
                error = abs(row["observed"] - expected[key])
                self.errors[(domain, key, row["unit"])].append(error)
                checks.append(
                    {
                        "property": key,
                        "error": error,
                        "bound": row["tolerance"]["value"],
                    }
                )
        if refusal:
            for key, reason in refusal.items():
                row = indexed[key]
                if (
                    row["verdict"] not in ("NO VERDICT", "MISSING EVIDENCE")
                    or reason not in row["validity"]["reason"]
                ):
                    raise AssertionError(
                        f"{case}/{key}: expected refusal {reason}, got {row}"
                    )
        if detector:
            detected = all(indexed[key]["verdict"] == "FAIL" for key in detector)
            self.mutations.append(
                {
                    "case": case,
                    "expected_detectors": detector,
                    "magnitude": magnitude,
                    "observed": {k: indexed[k]["verdict"] for k in detector},
                    "detected": detected,
                }
            )
            if not detected:
                raise AssertionError(f"mutation not detected: {self.mutations[-1]}")
        self.cases.append(
            {
                "id": case,
                "domain": domain,
                "checks": checks,
                "expected_refusals": refusal or {},
                "diagnostic": rows[0]["artifact"],
            }
        )


def spectral_grid(trials):
    for n in (1024, 2048):
        for frequency in (128, 900):
            for phase in (0.17, 1.1):
                for scale in (0.25, 1.0):
                    for shape in ("square", "saw"):
                        x, coefficients = fourier_wave(
                            n, frequency, shape, scale, phase
                        )
                        expected = {
                            f"harmonic.h{h}_ratio": abs(coefficients[h - 1])
                            for h in range(2, 6)
                        }
                        expected.update(
                            {
                                "harmonic.frequency": frequency,
                                "harmonic.folded_power": scale**2
                                / 2
                                * math.fsum(
                                    c * c
                                    for h, c in enumerate(coefficients, 1)
                                    if h * frequency > 2048
                                ),
                                "harmonic.inharmonic_power": 0.0,
                                "signal.dc": 0.0,
                                "signal.rms": scale
                                * math.sqrt(math.fsum(c * c for c in coefficients) / 2),
                            }
                        )
                        trials.add(
                            f"fourier-{shape}-{n}-{frequency}-{phase}-{scale}",
                            se.spectrum(
                                x,
                                sample_rate_hz=4096,
                                fundamental_band_hz=(frequency - 16, frequency + 16),
                            ),
                            expected,
                            domain="coherent-fourier",
                        )
    for n in (128, 129):
        fixtures = {
            "dc": [0.25] * n,
            "nyquist": [(-1.0) ** i for i in range(n)],
            "coherent": tone(n, 4096 * 7 / n),
            "off-bin": tone(n, 4096 * 7.3 / n),
            "edge-first": [1.0] + [0.0] * (n - 1),
            "edge-last": [0.0] * (n - 1) + [-1.0],
            "sampled-square": [1.0 if i % 16 < 8 else -1.0 for i in range(n)],
            "sampled-saw": [(i % 16) / 8 - 1 for i in range(n)],
        }
        for name, x in fixtures.items():
            power = direct_power(x)
            expected, refused = {}, {}
            for band, lo, hi in BANDS:
                bins = [
                    k
                    for k in range(len(power))
                    if lo <= k * 4096 / n and (hi is None or k * 4096 / n < hi)
                ]
                key = f"band.{band}.power"
                if not bins:
                    refused[key] = "no_fft_bins_in_band"
                    continue
                p = math.fsum(power[k] for k in bins)
                expected[key] = p
                expected[f"band.{band}.density"] = p / (len(bins) * 4096 / n)
                if p > 1e-20:  # Avoid logarithms of independent DFT roundoff.
                    expected[f"band.{band}.log_power"] = 10 * math.log10(p)
            trials.add(
                f"direct-dft-{n}-{name}",
                se.band_statistics(x, sample_rate_hz=4096),
                expected,
                domain="direct-dft",
                refusal=refused,
            )
    refusals = {
        "off-bin": (tone(1024, 128.5), (112, 144), "off_bin_or_ambiguous_fundamental"),
        "two-tones": (
            [a + b for a, b in zip(tone(1024, 128), tone(1024, 136))],
            (112, 152),
            "off_bin_or_ambiguous_fundamental",
        ),
        "short": (tone(32, 128), (112, 144), "insufficient_duration"),
        "silence": ([0.0] * 1024, (112, 144), "fundamental_at_or_below_floor"),
        "near-silence": (
            [v * 1e-12 for v in tone(1024, 128)],
            (112, 144),
            "fundamental_at_or_below_floor",
        ),
        "collision": (tone(1024, 1024), (1008, 1040), "ambiguous_folded_components"),
        "window-edge-transient": (
            [1.0] + tone(1024, 128)[1:],
            (112, 144),
            "off_bin_or_ambiguous_fundamental",
        ),
    }
    for name, (x, band, reason) in refusals.items():
        trials.add(
            f"spectral-refuse-{name}",
            se.spectrum(x, sample_rate_hz=4096, fundamental_band_hz=band),
            domain="spectral-refusal",
            refusal={"harmonic.h3_ratio": reason},
        )
    # Tuning moves the measured component; it must not fabricate ratio defects.
    for frequency in (124, 132):
        x, _ = fourier_wave(1024, frequency, "square")
        trials.add(
            f"measured-tuning-{frequency}",
            se.spectrum(x, sample_rate_hz=4096, fundamental_band_hz=(112, 144)),
            {"harmonic.frequency": frequency, "harmonic.h3_ratio": 1 / 3},
            domain="actual-frequency",
        )
    for amplitude in (1e-10, 2e-10):
        x = [amplitude * v for v in tone(1024, 128)]
        result = se.spectrum(x, sample_rate_hz=4096, fundamental_band_hz=(112, 144))
        trials.add(
            f"spectral-floor-{amplitude}",
            result,
            {"harmonic.h3_ratio": 0} if amplitude == 2e-10 else {},
            domain="spectral-floor",
            refusal=None
            if amplitude == 2e-10
            else {"harmonic.h3_ratio": "fundamental_at_or_below_floor"},
        )


def noise_grid(trials):
    for n in (256, 1024):
        for seed in (13, 29):
            words = noise_words(n, seed)
            x = [w / 2**31 for w in words]
            trials.add(
                f"rational-noise-{n}-{seed}",
                se.noise_statistics(x, sample_rate_hz=4096),
                rational_statistics(words),
                domain="rational-noise",
            )
    words = noise_words(128, 13)
    x = [w / 2**31 for w in words]
    powers = direct_power(x)
    expected = {
        f"band.{name}.power": math.fsum(
            p
            for k, p in enumerate(powers)
            if low <= k * 32 and (high is None or k * 32 < high)
        )
        for name, low, high in BANDS
        if low <= 2048
    }
    trials.add(
        "noise-direct-psd",
        se.noise_statistics(x, sample_rate_hz=4096, spectral=True),
        expected,
        domain="noise-psd",
    )
    alternative = x[1:] + x[:1]
    metrics = se.noise_statistics(alternative, sample_rate_hz=4096, spectral=True)
    trials.add(
        "cyclic-alternative-statistics",
        metrics,
        {
            **expected,
            "noise.mean": rational_statistics(words)["noise.mean"],
            "noise.variance": rational_statistics(words)["noise.variance"],
        },
        domain="statistically-plausible-wrong-noise",
    )
    raw = struct.pack("<128d", *x)
    identity = se.NoiseRecord(raw, (128,), "<f8", 13, 0)
    trials.add(
        "noise-original-identity",
        se.noise_identity(identity, identity),
        {"noise.identity": 1},
        domain="exact-noise",
    )
    for name, record in (
        (
            "wrong-bytes",
            se.NoiseRecord(struct.pack("<128d", *alternative), (128,), "<f8", 13, 0),
        ),
        (
            "wrong-seed",
            se.NoiseRecord(
                struct.pack("<128d", *(w / 2**31 for w in noise_words(128, 29))),
                (128,),
                "<f8",
                29,
                0,
            ),
        ),
        ("wrong-slot", se.NoiseRecord(raw, (128,), "<f8", 13, 1)),
    ):
        trials.add(
            name,
            se.noise_identity(identity, record),
            {"noise.identity": 1},
            domain="exact-noise",
            detector=["noise.identity"],
            magnitude="different exact bytes or selected stream",
        )
    for amplitude in (0.0, 1e-12, 0.5e-10, 2e-10):
        result = se.noise_statistics([-amplitude, amplitude] * 32, sample_rate_hz=4096)
        expected = {"noise.mean": 0, "noise.variance": amplitude**2}
        if amplitude == 2e-10:
            expected["noise.ac.1"] = -63 / 64
        trials.add(
            f"noise-floor-{amplitude}",
            result,
            expected,
            domain="noise-floor",
            refusal=None
            if amplitude == 2e-10
            else {"noise.ac.1": "variance_at_or_below_floor"},
        )
    trials.add(
        "noise-short",
        se.noise_statistics([-1, 1], sample_rate_hz=4096),
        domain="noise-refusal",
        refusal={"noise.ac.1": "insufficient_duration"},
    )
    ensemble = [rational_statistics(noise_words(1024, seed)) for seed in range(1, 17)]
    for seed, truth in enumerate(ensemble, 1):
        trials.add(
            f"ensemble-{seed}",
            se.noise_statistics(
                [w / 2**31 for w in noise_words(1024, seed)], sample_rate_hz=4096
            ),
            truth,
            domain="finite-ensemble",
        )
    return {
        key: {
            "minimum": min(r[key] for r in ensemble),
            "maximum": max(r[key] for r in ensemble),
            "mean": math.fsum(r[key] for r in ensemble) / len(ensemble),
        }
        for key in ensemble[0]
    }


def mixer_grid(trials):
    a = [v + 0.125 for v in tone(256, 128)]
    b = [v - 0.25 for v in tone(256, 256, phase=math.pi / 2)]
    inputs = {"a": a, "b": b}
    for gain in (0.25, 1.0, -0.5):
        for offset in (0, 0.125):
            y = [gain * v + 0.25 * w + offset for v, w in zip(a, b)]
            trials.add(
                f"mix-{gain}-{offset}",
                se.mix(inputs, y, sample_rate_hz=4096),
                {
                    "mix.a.gain": gain,
                    "mix.b.gain": 0.25,
                    "mix.a.gain_db": 20 * math.log10(abs(gain)),
                    "mix.b.gain_db": 20 * math.log10(0.25),
                    "mix.dc_offset": offset,
                    "mix.output.dc": gain * 0.125 - 0.25 * 0.25 + offset,
                    "mix.residual_rms": 0,
                },
                domain="linear-mix",
            )
    for name, channels, output, reason in (
        ("missing", {"a": None}, a, "original_mix_seam_missing"),
        ("short", {"a": a[:32]}, a[:32], "insufficient_duration"),
        ("correlated", {"a": a, "b": a}, a, "ambiguous_or_ill_conditioned_inputs"),
        ("constant", {"a": [1.0] * 256}, a, "input_variance_at_or_below_floor"),
        (
            "near-silent",
            {"a": [v * 1e-12 for v in a]},
            a,
            "input_variance_at_or_below_floor",
        ),
        ("unequal", {"a": a[:-1]}, a, "frame_mismatch"),
    ):
        trials.add(
            f"mix-refuse-{name}",
            se.mix(channels, output, sample_rate_hz=4096),
            domain="mix-refusal",
            refusal={"mix.a.gain": reason},
        )
    for delta in (10 ** (1 / 20) - 1, 10 ** (-1 / 20) - 1, 1e-6, 2e-12):
        trials.add(
            f"mutant-gain-{delta}",
            se.mix({"a": a}, [(1 + delta) * v for v in a], sample_rate_hz=4096),
            {"mix.a.gain": 1},
            domain="mix-mutation",
            detector=["mix.a.gain"],
            magnitude=delta,
        )
    for delta in (1e-6, 2e-12):
        trials.add(
            f"mutant-dc-{delta}",
            se.mix({"a": a}, [v + delta for v in a], sample_rate_hz=4096),
            {"mix.dc_offset": 0},
            domain="mix-mutation",
            detector=["mix.dc_offset"],
            magnitude=delta,
        )
    clipped = [max(-0.4, min(0.4, v)) for v in a]
    trials.add(
        "mutant-clipping",
        se.mix({"a": a}, clipped, sample_rate_hz=4096, clip_bounds=(-0.4, 0.4)),
        {"mix.residual_rms": 0},
        domain="mix-mutation",
        detector=["mix.residual_rms"],
        magnitude=0.4,
    )
    trials.add(
        "clipping-boundary-count",
        se.mix({"a": a}, clipped, sample_rate_hz=4096, clip_bounds=(-0.4, 0.4)),
        {"mix.boundary_count": sum(abs(v) >= 0.4 for v in a)},
        domain="clipping-count",
    )


def normalization_grid(trials):
    n = 176400
    for peak in (0.0, f32(1e-12), 1 - 2**-24, 1.0, 1 + 2**-23, 2.0):
        for sign in (-1, 1):
            for late in (False, True):
                index = n - 1 if late else 7
                x = [0.0] * n
                x[index] = sign * peak
                x[3] = f32(sign * peak * 0.375)
                x[5] = f32(-sign * peak * 0.625)
                if not late:
                    x[11] = -sign * peak  # equal-magnitude opposite-sign tie
                # Rational truth with one dtype rounding, independent of estimator.
                divisor = Fraction(peak) if peak > 1 else Fraction(1)
                replacements = {v: f32(Fraction(v) / divisor) for v in set(x)}
                y = [replacements[v] for v in x]
                index = index if peak else 0
                reciprocal = f32(1 / divisor)
                expected = {
                    "normalization.peak_abs": peak,
                    "normalization.peak_value": sign * peak,
                    "normalization.peak_index": index,
                    "normalization.decision_match": 1,
                    "normalization.reported_peak_error": 0,
                    "normalization.reported_index_match": 1,
                    "normalization.reciprocal_error": 0,
                    "normalization.rule_error_max": 0,
                }
                if peak:
                    expected["normalization.gain"] = float(1 / divisor)
                trials.add(
                    f"normalization-{peak}-{sign}-{late}",
                    se.normalization(
                        x,
                        y,
                        sample_rate_hz=44100,
                        complete_samples=n,
                        reported_peak=peak,
                        reported_index=index,
                        reported_applied=peak > 1,
                        reported_gain=reciprocal,
                    ),
                    expected,
                    domain="complete-normalization",
                    refusal=None
                    if peak
                    else {"normalization.gain": "silent_input_gain_unidentifiable"},
                )
    for name, pre, post, count, reason in (
        ("missing-pre", None, [0, 1], 2, "complete_pre_post_clip_missing"),
        ("missing-post", [0, 2], None, 2, "complete_pre_post_clip_missing"),
        ("partial", [0, 1], [0, 1], n, "incomplete_clip"),
        (
            "dtype",
            [0, 1 + 2**-24],
            [0, 1],
            2,
            "sample_not_representable_in_declared_dtype",
        ),
    ):
        trials.add(
            f"normalization-refuse-{name}",
            se.normalization(pre, post, sample_rate_hz=44100, complete_samples=count),
            domain="normalization-refusal",
            refusal={"normalization.gain": reason},
        )
    trials.add(
        "normalization-no-decision-metadata",
        se.normalization([0, 1], [0, 1], sample_rate_hz=44100, complete_samples=2),
        domain="normalization-refusal",
        refusal={"normalization.decision_match": "applied_decision_metadata_missing"},
    )
    for (
        name,
        peak,
        ypeak,
        decision,
        reported_peak,
        reported_index,
        gain,
        detector,
        magnitude,
    ) in (
        (
            "always-on",
            0.5,
            1.0,
            True,
            0.5,
            2,
            2.0,
            [
                "normalization.decision_match",
                "normalization.gain",
                "normalization.rule_error_max",
            ],
            0.5,
        ),
        (
            "always-off",
            2.0,
            2.0,
            False,
            2.0,
            2,
            1.0,
            [
                "normalization.decision_match",
                "normalization.gain",
                "normalization.rule_error_max",
            ],
            2.0,
        ),
        (
            "wrong-peak",
            2.0,
            1.0,
            True,
            0.5,
            0,
            0.5,
            ["normalization.reported_peak_error", "normalization.reported_index_match"],
            1.5,
        ),
        (
            "wrong-reciprocal",
            2.0,
            f32(1 + 2e-6),
            True,
            2.0,
            2,
            f32(0.5 + 1e-6),
            ["normalization.reciprocal_error", "normalization.rule_error_max"],
            1e-6,
        ),
        (
            "one-ulp-above-always-off",
            1 + 2**-23,
            1 + 2**-23,
            False,
            1 + 2**-23,
            2,
            1.0,
            ["normalization.decision_match", "normalization.rule_error_max"],
            2**-23,
        ),
        (
            "at-one-always-on",
            1.0,
            1.0,
            True,
            1.0,
            2,
            1.0,
            ["normalization.decision_match"],
            0.0,
        ),
    ):
        x = [0.0, 0.0, peak]
        y = [0.0, 0.0, ypeak]
        expected = {
            "normalization.decision_match": 1,
            "normalization.gain": 1 / peak if peak > 1 else 1,
            "normalization.rule_error_max": 0,
            "normalization.reported_peak_error": 0,
            "normalization.reported_index_match": 1,
            "normalization.reciprocal_error": 0,
        }
        trials.add(
            f"mutant-normalization-{name}",
            se.normalization(
                x,
                y,
                sample_rate_hz=44100,
                complete_samples=3,
                reported_peak=reported_peak,
                reported_index=reported_index,
                reported_applied=decision,
                reported_gain=gain,
            ),
            expected,
            domain="normalization-mutation",
            detector=detector,
            magnitude=magnitude,
        )


def spectral_mutations(trials):
    for (
        name,
        frequency,
        shape,
        harmonic_delta,
        off_component,
        detector,
        expected,
        magnitude,
    ) in (
        ("square-to-saw", 128, "saw", 0, 0, "harmonic.h2_ratio", 0, "waveform-mode"),
        ("h3", 128, "square", 1e-6, 0, "harmonic.h3_ratio", 1 / 3, 1e-6),
        ("h3-boundary", 128, "square", 2e-10, 0, "harmonic.h3_ratio", 1 / 3, 2e-10),
        (
            "folded-h3",
            900,
            "square",
            0.01,
            0,
            "harmonic.folded_power",
            (1 / 9 + 1 / 25) / 2,
            0.01,
        ),
        ("inharmonic", 128, "square", 0, 0.01, "harmonic.inharmonic_power", 0, 0.01),
    ):
        x, _ = fourier_wave(1024, frequency, shape)
        h3 = tone(1024, 3 * frequency, phase=3 * 0.17)
        other = tone(1024, 772)
        x = [
            v + harmonic_delta * a + off_component * b for v, a, b in zip(x, h3, other)
        ]
        trials.add(
            f"mutant-spectral-{name}",
            se.spectrum(
                x,
                sample_rate_hz=4096,
                fundamental_band_hz=(frequency - 16, frequency + 16),
            ),
            {detector: expected},
            domain="spectral-mutation",
            detector=[detector],
            magnitude=magnitude,
        )


def preparation_probe(source):
    x = [1.0] + tone(256, 128)
    pair = se.estimate_prepared_pair(
        x,
        [2 * v for v in x],
        estimator=se.noise_statistics,
        sample_rate_hz=4096,
        source=source,
        window=(1, 257),
    )
    if pair["status"] != "valid":
        raise AssertionError(pair)
    for multiplier, prepared, measured in zip(
        (1, 2), pair["preparation"], pair["measurements"]
    ):
        expected = [multiplier * v for v in x[1:]]
        if prepared["prepared_samples"] != expected or prepared[
            "prepared_sha256"
        ] != digest(struct.pack("<256d", *expected)):
            raise AssertionError("shared preparation changed native samples")
        if measured["preparation"]["config_sha256"] != prepared["config_sha256"]:
            raise AssertionError("preparation provenance lost")
    refused = se.estimate_prepared_pair(
        x,
        x,
        estimator=se.noise_statistics,
        sample_rate_hz=4096,
        source=source,
        operations=("normalize",),
    )
    if refused["status"] != "refused" or refused["measurements"] is not None:
        raise AssertionError("prohibited preparation accepted")
    dependency = pair["preparation"][0]["dependency"]
    if any(
        p["dependency"] != dependency
        for p in (*pair["preparation"], *refused["preparation"])
    ):
        raise AssertionError(
            "preparation source changed during integration probe; rerun"
        )
    return {
        "status": "measured",
        **dependency,
        "checks": [
            "symmetric explicit window",
            "original gain preserved",
            "prepared bytes verified",
            "normalization refused",
        ],
        "prepared_sha256": [p["prepared_sha256"] for p in pair["preparation"]],
        "config_sha256": [p["config_sha256"] for p in pair["preparation"]],
    }


def qualify(preparation_source=None):
    np = se.require_numpy()
    trials = Trials()
    spectral_grid(trials)
    ensemble = noise_grid(trials)
    mixer_grid(trials)
    normalization_grid(trials)
    spectral_mutations(trials)
    prep_available = (
        preparation_source is not None
        or (ROOT / "src/torchsynth_voice/preparation.py").exists()
    )
    result = {
        "schema": "spectral-noise-mix-qualification",
        "schema_version": 1,
        "version": se.VERSION,
        "scope": "synthetic analytic apparatus only; no Voice, hardware, tolerance ratification or holdout",
        "upstream_commit": "2b0964d4c6c3d472a2a0d54d91b408caaeffca6d",
        "preregistration_git_blob": "51169df6467c0bba55f4ff59f941a6874bada9e8",
        "preregistration_sha256": "141c0b13fd788c60d41e75890a56eb07b2d6f3d723d6e1c354903f48868bd4f1",
        "truth_source": TRUTH_SOURCE,
        "source_sha256": {
            name: digest((ROOT / name).read_bytes())
            for name in (
                "src/torchsynth_voice/spectral_estimators.py",
                "tools/qualify_spectral_estimators.py",
                "spec/SPECTRAL-ESTIMATORS.md",
            )
        },
        "runtime": {"python": sys.version.split()[0], "numpy": np.__version__},
        "preparation": preparation_probe(preparation_source)
        if prep_available
        else {
            "status": "missing",
            "reason": "shared #86 API not present in this checkout",
        },
        "cases": trials.cases,
        "mutations": trials.mutations,
        "finite_ensemble": {
            "construction": "xorshift32",
            "seeds": list(range(1, 17)),
            "samples": 1024,
            "uncertainty": "finite-record observed spread; no population confidence or law asserted",
            "statistics": ensemble,
        },
        "floors": [
            {
                "domain": domain,
                "property": key,
                "unit": unit,
                "trials": len(errors),
                "max_measured_absolute_error": max(errors),
                "preregistered_bound": BOUNDS[key],
            }
            for (domain, key, unit), errors in sorted(trials.errors.items())
        ],
        "refusals": dict(
            Counter(
                r["validity"]["reason"]
                for r in trials.rows
                if r["verdict"] in ("NO VERDICT", "MISSING EVIDENCE")
            )
        ),
        "diagnostic_records": trials.records,
        "scorecard": make_report(
            trials.rows, partition="development", rubric=se.RUBRIC
        ),
        "production": {
            "status": "MISSING EVIDENCE",
            "directed_voice_peak_targets": "unmeasured",
            "canonical_torch_noise": "not captured by this analytic qualification",
            "holdout": "sealed",
        },
    }
    verify(result)
    return result


def verify(report):
    """Validate exact diagnostic bytes, provenance, decisions and detection rows."""
    if (
        report.get("schema") != "spectral-noise-mix-qualification"
        or report.get("schema_version") != 1
    ):
        raise ValueError("unsupported qualification report")
    validate_report(report["scorecard"])
    if report["scorecard"]["partition"] != "development":
        raise ValueError("holdout not permitted")
    if len(report["cases"]) != 150 or len(report["mutations"]) != 21:
        raise ValueError("preregistered trial or mutation coverage missing")
    rows_by_case = defaultdict(dict)
    for row in report["scorecard"]["rows"]:
        data = report["diagnostic_records"][row["artifact"]["identity"]].encode()
        if digest(data) != row["artifact"]["sha256"] or row["artifact"][
            "identity"
        ] != "pm1-" + digest(data):
            raise ValueError("diagnostic record byte digest mismatch")
        record = json.loads(data)
        if record["case"] != row["case"] or record["trace"] != row["trace"]:
            raise ValueError("diagnostic case/trace mismatch")
        measured = record["comparison"]["metrics"][row["property"]]
        measurement = record["comparison"]
        settings_data = (
            json.dumps(
                {
                    "settings": measurement["settings"],
                    "implementation": measurement["implementation"],
                },
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        ).encode()
        if row["estimator"] != {
            "name": measurement["estimator"]["name"],
            "version": measurement["estimator"]["version"]
            + "+"
            + digest(settings_data),
        }:
            raise ValueError("estimator configuration/runtime digest mismatch")
        if (
            row["unit"] != measured["unit"]
            or row["coverage"] != measured["coverage"]
            or row["rubric"] != {k: record["rubric"][k] for k in ("id", "version")}
        ):
            raise ValueError("diagnostic unit/coverage/rubric mismatch")
        if row["verdict"] in ("PASS", "FAIL"):
            limit = record["rubric"]["limits"][row["property"]]
            if (
                measured["status"] != "valid"
                or row["observed"] != measured["value"]
                or row["expected"]
                != {"value": limit["expected"], "source": limit["source"]}
                or row["tolerance"]
                != {"value": limit["tolerance"], "source": limit["source"]}
            ):
                raise ValueError("diagnostic observation/limit mismatch")
            passes = abs(
                Fraction(row["observed"]) - Fraction(row["expected"]["value"])
            ) <= Fraction(row["tolerance"]["value"])
            if (row["verdict"] == "PASS") != passes:
                raise ValueError("incorrect row verdict")
        elif (
            measured["status"] == "valid"
            and row["property"] in record["rubric"]["limits"]
        ):
            raise ValueError("available qualified observation mislabeled as refused")
        if row["property"] in rows_by_case[row["case"]["id"]]:
            raise ValueError("duplicate case/property")
        rows_by_case[row["case"]["id"]][row["property"]] = row
    if len(report["cases"]) != len(rows_by_case) or {
        c["id"] for c in report["cases"]
    } != set(rows_by_case):
        raise ValueError("case coverage mismatch")
    for mutation in report["mutations"]:
        if not mutation["detected"] or any(
            rows_by_case[mutation["case"]][k]["verdict"] != "FAIL"
            for k in mutation["expected_detectors"]
        ):
            raise ValueError("mutation did not fail its required row")
        if mutation["observed"] != {
            k: rows_by_case[mutation["case"]][k]["verdict"]
            for k in mutation["expected_detectors"]
        }:
            raise ValueError("mutation summary mismatch")
    errors = defaultdict(list)
    mutant_cases = {m["case"] for m in report["mutations"]}
    for case in report["cases"]:
        rows = rows_by_case[case["id"]]
        for key, reason in case["expected_refusals"].items():
            if (
                rows[key]["verdict"] not in ("NO VERDICT", "MISSING EVIDENCE")
                or reason not in rows[key]["validity"]["reason"]
            ):
                raise ValueError("expected refusal missing")
        if case["id"] in mutant_cases:
            continue
        for row in rows.values():
            if row["verdict"] == "FAIL":
                raise ValueError("positive control failed")
            if row["verdict"] == "PASS":
                errors[(case["domain"], row["property"], row["unit"])].append(
                    abs(row["observed"] - row["expected"]["value"])
                )
    recomputed = [
        {
            "domain": d,
            "property": k,
            "unit": u,
            "trials": len(e),
            "max_measured_absolute_error": max(e),
            "preregistered_bound": BOUNDS[k],
        }
        for (d, k, u), e in sorted(errors.items())
    ]
    if report["floors"] != recomputed:
        raise ValueError("floor summary differs from raw observations")
    refusals = dict(
        Counter(
            r["validity"]["reason"]
            for r in report["scorecard"]["rows"]
            if r["verdict"] in ("NO VERDICT", "MISSING EVIDENCE")
        )
    )
    if report["refusals"] != refusals:
        raise ValueError("refusal counts differ from raw rows")
    for floor in recomputed:
        if floor["max_measured_absolute_error"] > floor["preregistered_bound"]:
            raise ValueError("apparatus error exceeds preregistered bound")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        help="write measured JSON; omitted prints concise result only",
    )
    parser.add_argument(
        "--verify",
        type=Path,
        help="validate an existing record without claiming a new measurement",
    )
    parser.add_argument(
        "--preparation-source",
        type=Path,
        help="read-only #86 source until dependency lands",
    )
    args = parser.parse_args()
    if args.verify:
        report = json.loads(args.verify.read_bytes())
        verify(report)
        print(
            f"Validated stored evidence: {len(report['cases'])} cases; no new measurements run"
        )
        return
    report = qualify(args.preparation_source)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(encoded(report))
    print(
        json.dumps(
            {
                "cases": len(report["cases"]),
                "mutation_controls": len(report["mutations"]),
                "scorecard_counts": report["scorecard"]["summary"],
                "preparation": report["preparation"]["status"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
