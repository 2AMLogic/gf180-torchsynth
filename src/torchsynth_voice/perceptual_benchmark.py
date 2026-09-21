"""Paired perceptual-diagnostics benchmark: candidates vs landed objective rows.

See spec/PERCEPTUAL-BENCHMARK.md. This module benchmarks explicitly
configured candidate perceptual metrics (multi-resolution log-spectral
distance, ITU-R BS.1387 PEAQ, ViSQOL audio mode, CDPAM) against the landed
objective paired diagnostics over the ratified issue 44 corruption ladder.

Doctrine (docs/MEASUREMENT-PLAN.md): these are diagnostic candidates only.
They never establish per-sound identity or implementation correctness, are
never an acceptance oracle, and no candidate becomes mandatory solely for a
good aggregate correlation. No aggregate score is produced anywhere.

Human comparison is an explicit NO VERDICT row until blinded responses
actually exist: listening was not collected (operator declination of
listening sessions, time cost, 2026-09-20). The row is a sibling output,
never merged into candidate statistics.

Dependency policy mirrors population_metrics: candidate implementations are
declared pins, not repo dependencies. Executing a candidate whose tool or
model is absent refuses with an explicit reason; it is never silently
skipped. The statistics machinery is stdlib-only and qualified on tiny
synthetic vectors; the one in-repo candidate (``multires-log-spectral-v1``)
and the declared 44.1-to-48 kHz resampler are numpy-gated like the paired
spectral rows.
"""

from __future__ import annotations

import importlib
import math
import random
import shutil
import subprocess
from collections.abc import Sequence

BENCHMARK_VERSION = "perceptual-benchmark-v1"
ACCUMULATION = "fsum-binary64-v1"
MULTIRES_ACCUMULATION = "numpy-sum-binary64-v1"
RESAMPLER_PIN = "windowed-sinc-kaiser16-w32-v1"
RESAMPLER_KAISER_BETA = 16.0
RESAMPLER_HALF_WIDTH = 32
BOOTSTRAP_ALPHA = 0.05
OBJECTIVE_AXES = ("snr_db", "error_rms", "max_abs_error")

HUMAN_NOT_COLLECTED_REASON = (
    "listening not collected — operator declination of listening sessions "
    "(time cost), 2026-09-20; recorded in issue 44 closure comment 5753825121"
)

MULTIRES_SPECTRAL_PIN = {
    "identity": "multires-log-spectral-v1",
    "class": (
        "multi-resolution log-spectral distance (One Billion Sounds candidate "
        "metric class)"
    ),
    "sources": [
        "arXiv:2104.12922 S4.3 Table 2: multi-scale spectrogram distance was a "
        "candidate metric; L1 often outperformed L2 in its listening comparison",
    ],
    "config": {
        "fft_sizes": [512, 1024, 2048],
        "hop": "half-FFT (N/2)",
        "window": "periodic Hann",
        "spectrogram": "power; phase discarded",
        "log_floor": 1e-10,
        "log_definition": "10*log10(max(power, 1e-10))",
        "frame_distance": "l2-mean-over-frames",
        "scale_aggregation": "sum-over-scales",
        "framing": (
            "identical sample counts required; no alignment, trim, pad, "
            "resample, or gain fit"
        ),
        "identity_value": 0.0,
    },
    "sample_rate_policy": "native; no resampling",
    "dependency": "numpy (the locked metrics extra)",
}

PEAQ_PIN = {
    "identity": "itu-r-bs.1387-2-peaq",
    "class": "PEAQ objective perceptual impairment estimation",
    "sources": [
        "ITU-R BS.1387-2 (2023): https://www.itu.int/rec/R-REC-BS.1387-2-202305-I/en",
    ],
    "implementation_probe_order": [
        {"probe": "module", "name": "peaqb"},
        {"probe": "module", "name": "peaq"},
        {"probe": "gst-element", "name": "peaq", "inspector": "gst-inspect-1.0"},
    ],
    "resampling": (
        "none applied by this adapter; the chosen implementation's declared "
        "input rate governs and is recorded in any produced row"
    ),
    "output": (
        "implementation-declared objective difference grade; pinned per "
        "produced receipt"
    ),
}

VISQOL_PIN = {
    "identity": "google-visqol-v3-audio",
    "class": "ViSQOL full-reference similarity-to-quality (audio mode)",
    "sources": [
        "Google ViSQOL documentation: https://github.com/google/visqol",
        "arXiv:2311.01616 S2: ViSQOL usage and limitations in the FAD context",
    ],
    "required_rate_hz": 48000.0,
    "resampler": RESAMPLER_PIN,
    "resampler_note": (
        "ViSQOL audio mode requires 48 kHz; the corpus is 44.1 kHz, so every "
        "ViSQOL row would carry the declared resampler. The resampler's own "
        "contribution is measured separately (resampler_contribution_rows) "
        "and reported beside, never inside, candidate scores."
    ),
    "probe": {"probe": "binary", "name": "visqol"},
    "model": (
        "libsvm_nu_svr_model.txt (audio mode); version pinned per produced receipt"
    ),
}

CDPAM_PIN = {
    "identity": "cdpam-arXiv-2102.05109-pretrained",
    "class": "CDPAM contrastive perceptual audio distance (pretrained checkpoint)",
    "sources": [
        "CDPAM: Contrastive learning for perceptual audio similarity, arXiv:2102.05109",
    ],
    "resampling": (
        "implementation-declared input rate; the adapter records the "
        "checkpoint's declared rate and applies the declared resampler at run "
        "time, reporting the resampler beside the score"
    ),
    "probe": {"probe": "module", "name": "cdpam"},
    "extra_dependency": "torch",
}

CANDIDATE_PINS = {
    MULTIRES_SPECTRAL_PIN["identity"]: MULTIRES_SPECTRAL_PIN,
    PEAQ_PIN["identity"]: PEAQ_PIN,
    VISQOL_PIN["identity"]: VISQOL_PIN,
    CDPAM_PIN["identity"]: CDPAM_PIN,
}

CANDIDATE_LIMITATIONS = [
    {
        "candidate": PEAQ_PIN["identity"],
        "limitation": (
            "designed for perceptual impairment of audio codecs; informative "
            "only once fixed-point errors resemble codec impairment; the "
            "version and implementation must be pinned and validated on our "
            "sounds before any use"
        ),
        "source": "docs/MEASUREMENT-PLAN.md citing ITU-R BS.1387",
    },
    {
        "candidate": VISQOL_PIN["identity"],
        "limitation": (
            "trained around codec-like degradation and requires 48 kHz, "
            "introducing a pinned resampler; its own documentation warns it "
            "can perform poorly outside its training use and that single "
            "scores are not meaningful"
        ),
        "source": "docs/MEASUREMENT-PLAN.md citing Google ViSQOL documentation",
    },
    {
        "candidate": CDPAM_PIN["identity"],
        "limitation": (
            "trained on human judgments of audio perturbations but rooted in "
            "speech-processing data; exploratory until calibrated against "
            "blinded judgments on TorchSynth sounds"
        ),
        "source": "docs/MEASUREMENT-PLAN.md citing arXiv:2102.05109",
    },
    {
        "candidate": MULTIRES_SPECTRAL_PIN["identity"],
        "limitation": (
            "discards phase and depends on declared FFT sizes, hops, windows, "
            "and log floors; it localizes frequency/time-scale impairment and "
            "is not a quality judgment"
        ),
        "source": "docs/MEASUREMENT-PLAN.md, useful secondary diagnostics",
    },
    {
        "candidate": "all",
        "limitation": (
            "audio similarity is unreliable as a proxy for audio quality; "
            "similarity metrics must not be misrepresented as human quality"
        ),
        "source": "arXiv:2206.13411",
    },
]

DOCTRINE = (
    "perceptual diagnostics only: never per-sound identity or implementation "
    "correctness, never an acceptance oracle, never an optimization target; "
    "no candidate becomes mandatory solely for a good aggregate correlation; "
    "no aggregate score is produced"
)


class PerceptualBenchmarkError(ValueError):
    """Malformed input or an unavailable declared dependency, not a measurement."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise PerceptualBenchmarkError(reason)


def _finite_float(value, name: str) -> float:
    _require(
        type(value) is not bool and isinstance(value, (int, float)),
        "%s must be a finite real number" % name,
    )
    v = float(value)
    _require(math.isfinite(v), "%s must be a finite real number" % name)
    return v


def _sample_list(values, name: str) -> list[float]:
    _require(
        isinstance(values, Sequence) and not isinstance(values, (str, bytes)),
        "%s must be a one-dimensional sequence of finite real samples" % name,
    )
    return [_finite_float(v, "%s[%d]" % (name, i)) for i, v in enumerate(values)]


def _pair(reference, candidate, names=("reference", "candidate")):
    ref = _sample_list(reference, names[0])
    cand = _sample_list(candidate, names[1])
    _require(
        len(ref) > 0 and len(cand) > 0,
        "empty inputs are invalid evidence, never a perfect match",
    )
    _require(
        len(ref) == len(cand),
        "sample count mismatch (%d vs %d): framing failure, never scored"
        % (len(ref), len(cand)),
    )
    return ref, cand


# ---------------------------------------------------------------------------
# Statistics machinery (stdlib; deterministic; qualified on synthetic vectors)
# ---------------------------------------------------------------------------


def _rankdata(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        average = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = average
        i = j + 1
    return ranks


def spearman_rank(x: Sequence[float], y: Sequence[float]) -> float | None:
    """Spearman rank correlation with average tie ranks; None without variance."""

    _require(len(x) == len(y), "spearman needs equally sized x and y")
    _require(len(x) >= 2, "spearman needs at least two paired values")
    rx = _rankdata(list(x))
    ry = _rankdata(list(y))
    n = len(x)
    x_bar = math.fsum(rx) / n
    y_bar = math.fsum(ry) / n
    num = math.fsum((a - x_bar) * (b - y_bar) for a, b in zip(rx, ry))
    den_x = math.sqrt(math.fsum((a - x_bar) ** 2 for a in rx))
    den_y = math.sqrt(math.fsum((b - y_bar) ** 2 for b in ry))
    if den_x == 0.0 or den_y == 0.0:
        return None
    return num / (den_x * den_y)


def bootstrap_spearman_ci(
    x: Sequence[float],
    y: Sequence[float],
    *,
    trials: int,
    seed: int,
    alpha: float = BOOTSTRAP_ALPHA,
) -> dict:
    """Percentile bootstrap CI for Spearman; deterministic under (trials, seed)."""

    _require(type(trials) is int and trials >= 1, "trials must be a positive integer")
    _require(
        type(seed) is int,
        "seed must be an integer",
    )
    _require(0.0 < alpha < 1.0, "alpha must lie strictly between 0 and 1")
    numeric = []
    for a, b in zip(x, y):
        numeric.append((_finite_float(a, "x"), _finite_float(b, "y")))
    _require(len(numeric) == len(x) and len(x) == len(y), "x and y must align")
    point = spearman_rank(x, y)
    rng = random.Random(seed)
    stats: list[float] = []
    for _ in range(trials):
        resample = [numeric[rng.randrange(len(numeric))] for _ in range(len(numeric))]
        value = spearman_rank([a for a, _ in resample], [b for _, b in resample])
        if value is not None:
            stats.append(value)
    if point is None or not stats:
        return {
            "point": point,
            "ci_low": None,
            "ci_high": None,
            "trials": trials,
            "seed": seed,
            "usable_trials": len(stats),
            "status": "undefined" if point is None else "degenerate_resamples",
            "alpha": alpha,
        }
    stats.sort()
    low = stats[int((alpha / 2.0) * (len(stats) - 1))]
    high = stats[int((1.0 - alpha / 2.0) * (len(stats) - 1))]
    return {
        "point": point,
        "ci_low": low,
        "ci_high": high,
        "trials": trials,
        "seed": seed,
        "usable_trials": len(stats),
        "status": "ok",
        "alpha": alpha,
    }


def ladder_monotonicity(values_by_step: Sequence[float | None]) -> dict:
    """Flag a non-monotone ladder as an invalid anchor; never smooth it."""

    present = [(i + 1, v) for i, v in enumerate(values_by_step) if v is not None]
    violations = []
    for (step_a, value_a), (step_b, value_b) in zip(present, present[1:]):
        if value_b < value_a:
            violations.append(
                {
                    "from_step": step_a,
                    "to_step": step_b,
                    "from_value": value_a,
                    "to_value": value_b,
                }
            )
    return {
        "values_by_declared_step": list(values_by_step),
        "evaluated_steps": [step for step, _ in present],
        "monotone": not violations,
        "violations": violations,
        "invalid_anchor": bool(violations),
        "note": (
            "a non-monotone ladder is flagged and repaired in a new protocol "
            "version, never smoothed or reinterpreted"
        ),
    }


def reference_dependence_row(
    candidate: str, measured, *, identity_value: float
) -> dict:
    """A candidate's value on an identical pair must equal its identity value."""

    holds = measured is not None and (measured == identity_value)
    return {
        "check": "reference_dependence",
        "candidate": candidate,
        "identity_value": identity_value,
        "measured": measured,
        "holds": holds,
        "note": (
            "reference dependence: the metric on an identical pair must equal "
            "its declared identity value exactly"
        ),
    }


# ---------------------------------------------------------------------------
# Availability probes and dependency-gated candidate producers
# ---------------------------------------------------------------------------


def _module_probe(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False


def multires_spectral_availability() -> dict:
    ready = _module_probe("numpy")
    return {
        "candidate": MULTIRES_SPECTRAL_PIN["identity"],
        "available": ready,
        "modules": {"numpy": ready},
        "reason": (
            "declared pin ready" if ready else "numpy_unavailable_install_metrics_extra"
        ),
    }


def _require_numpy():
    try:
        import numpy
    except ImportError as error:
        raise PerceptualBenchmarkError(
            "numpy_unavailable_install_metrics_extra"
        ) from error
    return numpy


def _gst_element_probe(element: str, inspector: str) -> bool:
    path = shutil.which(inspector)
    if path is None:
        return False
    try:
        result = subprocess.run(
            [path, element], capture_output=True, text=True, timeout=30
        )
    except Exception:
        return False
    return result.returncode == 0


def peaq_availability() -> dict:
    probes = []
    for spec in PEAQ_PIN["implementation_probe_order"]:
        if spec["probe"] == "module":
            found = _module_probe(spec["name"])
        else:
            found = _gst_element_probe(spec["name"], spec["inspector"])
        probes.append({**spec, "found": found})
    ready = any(entry["found"] for entry in probes)
    return {
        "candidate": PEAQ_PIN["identity"],
        "available": ready,
        "probes": probes,
        "reason": (
            "declared pin ready"
            if ready
            else "peaq_implementation_unavailable: no probed implementation "
            "present (" + ", ".join(entry["name"] for entry in probes) + ")"
        ),
    }


def visqol_availability() -> dict:
    found = shutil.which(VISQOL_PIN["probe"]["name"]) is not None
    return {
        "candidate": VISQOL_PIN["identity"],
        "available": found,
        "probes": [{**VISQOL_PIN["probe"], "found": found}],
        "reason": (
            "declared pin ready"
            if found
            else "visqol_binary_unavailable: 'visqol' not on PATH"
        ),
    }


def cdpam_availability() -> dict:
    modules = {"cdpam": _module_probe("cdpam"), "torch": _module_probe("torch")}
    ready = all(modules.values())
    return {
        "candidate": CDPAM_PIN["identity"],
        "available": ready,
        "modules": modules,
        "reason": (
            "declared pin ready"
            if ready
            else "cdpam_dependency_unavailable: missing modules "
            + ", ".join(name for name, ok in modules.items() if not ok)
        ),
    }


def _gated_score(candidate: str, availability: dict, reference, candidate_samples):
    """Gated candidates refuse until their dependency AND a landed producer exist."""

    if not availability["available"]:
        raise PerceptualBenchmarkError(availability["reason"])
    _pair(reference, candidate_samples)
    raise PerceptualBenchmarkError(
        "producer_not_wired: an available implementation exists but no "
        "validated producer for %r is landed; wiring requires the pinned "
        "implementation present and validated on our sounds" % candidate
    )


def peaq_score(reference, candidate_samples) -> dict:
    _gated_score(
        PEAQ_PIN["identity"], peaq_availability(), reference, candidate_samples
    )


def visqol_score(reference, candidate_samples) -> dict:
    _gated_score(
        VISQOL_PIN["identity"], visqol_availability(), reference, candidate_samples
    )


def cdpam_score(reference, candidate_samples) -> dict:
    _gated_score(
        CDPAM_PIN["identity"], cdpam_availability(), reference, candidate_samples
    )


def _power_frames(samples: list[float], fft_size: int, np):
    hop = fft_size // 2
    count = 1 if len(samples) <= fft_size else 1 + (len(samples) - fft_size) // hop
    window = np.hanning(fft_size + 1)[:-1]
    frames = np.zeros((count, fft_size), dtype=np.float64)
    for i in range(count):
        start = i * hop
        chunk = samples[start : start + fft_size]
        frames[i, : len(chunk)] = chunk
    return np.abs(np.fft.rfft(frames * window, axis=1)) ** 2


def multires_spectral_distance(
    reference, candidate_samples, *, sample_rate_hz=44100.0
) -> dict:
    """The one in-repo candidate: pinned multi-resolution log-spectral distance."""

    _finite_float(sample_rate_hz, "sample_rate_hz")
    ref, cand = _pair(reference, candidate_samples)
    np = _require_numpy()
    pin = MULTIRES_SPECTRAL_PIN["config"]
    total = 0.0
    per_scale = []
    for fft_size in pin["fft_sizes"]:
        spec_r = _power_frames(ref, fft_size, np)
        spec_c = _power_frames(cand, fft_size, np)
        log_r = 10.0 * np.log10(np.maximum(spec_r, pin["log_floor"]))
        log_c = 10.0 * np.log10(np.maximum(spec_c, pin["log_floor"]))
        delta = log_c - log_r
        mean_square = float(np.sum(np.square(delta))) / float(delta.size)
        scale_value = math.sqrt(mean_square)
        per_scale.append({"fft_size": fft_size, "frame_mean_l2_db": scale_value})
        total += scale_value
    return {
        "candidate": MULTIRES_SPECTRAL_PIN["identity"],
        "value": total,
        "unit": "dB (summed-over-scales frame-mean L2 log-spectral distance)",
        "status": "valid",
        "reason": "measured",
        "identity_value": pin["identity_value"],
        "per_scale": per_scale,
        "config": dict(pin),
        "sample_rate_hz": sample_rate_hz,
        "estimator": {
            "name": MULTIRES_SPECTRAL_PIN["identity"],
            "version": BENCHMARK_VERSION,
        },
        "accumulation": MULTIRES_ACCUMULATION,
    }


# ---------------------------------------------------------------------------
# Declared 44.1 -> 48 kHz resampler and its measured contribution
# ---------------------------------------------------------------------------


def _kaiser_continuous(offsets, half_width: int, beta: float, np):
    """Continuous-argument Kaiser window: I0(b*sqrt(1-(d/hw)^2))/I0(b), |d|<=hw."""

    d = np.asarray(offsets, dtype=np.float64)
    inside = np.abs(d) <= half_width
    arg = 1.0 - np.where(inside, (d / half_width) ** 2, 1.0)
    arg = np.maximum(arg, 0.0)
    scaled = beta * np.sqrt(arg)
    series = np.ones_like(scaled)
    term = np.ones_like(scaled)
    k = 0
    while np.any(term > 1e-18):
        term = term * (scaled / (2.0 * (k + 1.0))) ** 2
        series = series + term
        k += 1
        if k > 200:
            break
    values = series
    i0_beta = 1.0
    term = 1.0
    k = 0
    while term > 1e-18:
        k += 1
        term = term * (beta / (2.0 * k)) ** 2
        i0_beta += term
    return np.where(inside, values / i0_beta, 0.0)


def resample_samples(
    samples,
    *,
    src_rate_hz: float,
    dst_rate_hz: float,
    half_width: int = RESAMPLER_HALF_WIDTH,
    kaiser_beta: float = RESAMPLER_KAISER_BETA,
) -> list[float]:
    """Pinned windowed-sinc resampler (``windowed-sinc-kaiser16-w32-v1``).

    Continuous Kaiser-windowed sinc interpolation (beta 16, half-width 32
    input samples) at output positions t*src/dst; zero-padded beyond both
    input edges; declared output length ceil(n*dst/src). Diagnostic
    apparatus for the ViSQOL 48 kHz requirement; its contribution is
    measured and reported, never assumed negligible.
    """

    _finite_float(src_rate_hz, "src_rate_hz")
    _finite_float(dst_rate_hz, "dst_rate_hz")
    _require(src_rate_hz > 0.0 and dst_rate_hz > 0.0, "rates must be positive")
    _require(
        type(half_width) is int and half_width >= 1,
        "half_width must be a positive integer",
    )
    ref = _sample_list(samples, "samples")
    np = _require_numpy()
    if src_rate_hz == dst_rate_hz:
        return list(ref)
    x = np.asarray(ref, dtype=np.float64)
    step = src_rate_hz / dst_rate_hz
    n_out = int(math.ceil(len(x) * dst_rate_hz / src_rate_hz))
    out = np.zeros(n_out, dtype=np.float64)
    offsets = np.arange(-half_width + 1, half_width + 1, dtype=np.int64)
    block = 1 << 15
    for start in range(0, n_out, block):
        stop = min(start + block, n_out)
        pos = np.arange(start, stop, dtype=np.float64) * step
        base = np.floor(pos).astype(np.int64)
        idx = base[:, None] + offsets[None, :]
        distance = pos[:, None] - idx
        weights = np.sinc(distance) * _kaiser_continuous(
            distance, half_width, kaiser_beta, np
        )
        valid = (idx >= 0) & (idx < len(x))
        gathered = np.where(valid, x[np.clip(idx, 0, len(x) - 1)], 0.0)
        out[start:stop] = np.einsum("ij,ij->i", weights * valid, gathered)
    return [float(v) for v in out]


def resampler_contribution_rows(
    reference,
    *,
    sample_rate_hz=44100.0,
    visqol_rate_hz=48000.0,
) -> list[dict]:
    """Measure what the declared 44.1->48->44.1 resampler adds to a clip.

    Primary rows are the repo's unaligned paired diagnostics on the
    round-tripped clip versus the original: group-delay and edge effects are
    part of the resampler's contribution under the no-alignment contract.
    One clearly-labeled delay-aligned diagnostic row is reported beside them
    and never replaces the primary rows.
    """

    from .paired_metrics import compare_paired

    _finite_float(sample_rate_hz, "sample_rate_hz")
    _finite_float(visqol_rate_hz, "visqol_rate_hz")
    ref = _sample_list(reference, "reference")
    availability = multires_spectral_availability()
    if not availability["available"]:
        return [
            {
                "check": "resampler_contribution",
                "resampler": RESAMPLER_PIN,
                "status": "refused",
                "reason": availability["reason"],
                "note": "the resampler is numpy-gated; refusal is explicit",
            }
        ]
    up = resample_samples(ref, src_rate_hz=sample_rate_hz, dst_rate_hz=visqol_rate_hz)
    down = resample_samples(up, src_rate_hz=visqol_rate_hz, dst_rate_hz=sample_rate_hz)
    measurement = compare_paired(
        ref,
        down,
        reference_rate_hz=float(sample_rate_hz),
        candidate_rate_hz=float(sample_rate_hz),
        unit="amplitude",
    )
    metrics = measurement["metrics"]

    def value(name):
        entry = metrics.get(name) or {}
        return entry.get("value")

    import numpy as np

    x = np.asarray(ref, dtype=np.float64)
    y = np.asarray(down, dtype=np.float64)
    span = min(len(x), len(y))
    size = 1 << int(math.ceil(math.log2(2 * span)))
    conv = np.fft.irfft(
        np.fft.rfft(x[:span], size) * np.fft.rfft((y[:span])[::-1], size), size
    )
    delay = (span - 1) - int(np.argmax(np.abs(conv)))
    if delay > 0:
        aligned_x = x[delay : delay + span - delay]
        aligned_y = y[: span - delay]
    else:
        aligned_x = x[: span + delay]
        aligned_y = y[-delay:span]
    err = aligned_y - aligned_x
    ref_rms = float(np.sqrt(np.mean(np.square(aligned_x))))
    err_rms = float(np.sqrt(np.mean(np.square(err))))
    aligned_snr = (
        20.0 * math.log10(ref_rms / err_rms)
        if ref_rms > 0.0 and err_rms > 0.0
        else None
    )
    rows = [
        {
            "check": "resampler_contribution",
            "resampler": RESAMPLER_PIN,
            "roundtrip": "44100 -> 48000 -> 44100",
            "alignment": "none (repo contract)",
            "primary": True,
            "row": "compare_paired",
            "sample_count": len(down),
            "reference_sample_count": len(ref),
            "framing_match": value("framing_match"),
            "snr_db": value("snr_db"),
            "error_rms": value("error_rms"),
            "max_abs_error": value("max_abs_error"),
            "first_divergence_index": value("first_divergence_index"),
            "note": (
                "primary rows: the whole resampler cost, including delay and "
                "edge effects, under the no-alignment paired contract"
            ),
        },
        {
            "check": "resampler_contribution",
            "resampler": RESAMPLER_PIN,
            "alignment": "cross-correlation argmax",
            "primary": False,
            "diagnostic_only": True,
            "estimated_delay_samples": delay,
            "aligned_snr_db": aligned_snr,
            "aligned_error_rms": err_rms,
            "note": (
                "diagnostic only, never a replacement for the primary unaligned rows"
            ),
        },
    ]
    return rows


# ---------------------------------------------------------------------------
# Human-comparison row (explicit NO VERDICT until blinded responses exist)
# ---------------------------------------------------------------------------


def human_comparison_row() -> dict:
    """The explicit lack-of-human-data row; never merged into candidate stats."""

    return {
        "comparison": "candidate-vs-blinded-human-judgment",
        "verdict": "NO VERDICT",
        "status": "not_collected",
        "reason": HUMAN_NOT_COLLECTED_REASON,
        "note": (
            "per the issue 45 acceptance criteria, lack of human data is "
            "explicit; this row is a sibling output and is never merged into "
            "candidate agreement statistics; it may not promote any candidate"
        ),
    }


# ---------------------------------------------------------------------------
# Agreement reporting (rows only; a correlation never promotes a candidate)
# ---------------------------------------------------------------------------


def build_agreement(
    families, candidate_ids, availability, *, trials: int, seed: int
) -> dict:
    """Per-family, per-operator candidate agreement rows.

    ``families`` maps a corruption-family name to a list of point dicts, each
    carrying ``operator``, ``ladder_step`` (the declared rank), ``magnitude``
    (ground-truth ladder magnitude in its native unit), ``objective`` (dict
    of landed objective diagnostic values), and ``metrics`` (dict of
    candidate identity to a metric value or None).

    Correlations are computed WITHIN one operator so every axis stays
    unit-coherent: the rank axis is the config's declared ladder-step
    position (for ``clip.saturation_ceiling`` severity rises as the numeric
    ceiling drops; the position, not the numeric value, is the rank).
    Unavailable candidates get explicit NO VERDICT refusal rows keyed by
    their availability reason. Output is rows only; no aggregate score
    exists anywhere in it.
    """

    agreement = {}
    for family, points in sorted(families.items(), key=lambda kv: str(kv[0])):
        by_operator: dict = {}
        for point in points:
            by_operator.setdefault(point["operator"], []).append(point)
        family_entry = {}
        for operator, op_points in sorted(
            by_operator.items(), key=lambda kv: str(kv[0])
        ):
            operator_entry = {}
            for identity in candidate_ids:
                values = [(p["metrics"] or {}).get(identity) for p in op_points]
                usable = [v for v in values if v is not None]
                candidate_entry = {
                    "n_points": len(op_points),
                    "n_usable": len(usable),
                }
                if not usable:
                    probe = availability[identity]
                    candidate_entry["agreement"] = {
                        "verdict": "NO VERDICT",
                        "reason": (
                            probe["reason"]
                            if not probe["available"]
                            else "no usable candidate values for this operator"
                        ),
                    }
                    operator_entry[identity] = candidate_entry
                    continue
                named_axes = {
                    "declared_step_rank": [p["ladder_step"] for p in op_points]
                }
                for axis in OBJECTIVE_AXES:
                    named_axes[axis] = [p["objective"].get(axis) for p in op_points]
                for axis, other in named_axes.items():
                    finite = [
                        (m, v)
                        for m, v in zip(other, values)
                        if v is not None and m is not None
                    ]
                    if len(finite) < 2:
                        candidate_entry[axis] = {
                            "verdict": "NO VERDICT",
                            "reason": (
                                "fewer than two usable (objective, candidate) pairs"
                            ),
                        }
                        continue
                    xs = [pair[0] for pair in finite]
                    ys = [pair[1] for pair in finite]
                    candidate_entry[axis] = {
                        "n": len(finite),
                        "spearman": bootstrap_spearman_ci(
                            xs, ys, trials=trials, seed=seed
                        ),
                        "note": (
                            "diagnostic agreement only; a correlation never "
                            "promotes a candidate"
                        ),
                    }
                operator_entry[identity] = candidate_entry
            family_entry[operator] = operator_entry
        agreement[family] = family_entry
    return agreement
