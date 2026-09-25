"""Golden derivation and host mirror for the audio VCA + mixer lane (#76).

The bit-exactness target is the frozen fixed whole-voice model's audio VCA
and pre-normalization mixer block — the tail of
``FixedVoiceModel.render``'s per-sample loop plus the ``mixer.S4``
narrowing (``src/torchsynth_voice/fixed_voice.py``):

    post_vca_1[n] = mul(v1, C1, amp_1[n], C1, C1, half_even, saturate)
    post_vca_2[n] = mul(v2, C1, amp_2[n], C1, C1, half_even, saturate)
    post_vca_n[n] = mul(nq, C1, amp_n[n], C1, C1, half_even, saturate)
    acc[n]        = post_vca_1[n]*L1 + post_vca_2[n]*L2 + post_vca_n[n]*Ln
    mix[n]        = rescale(acc[n], Q6.42, C1, half_even, saturate)

with ``L1``/``L2``/``Ln`` the S1 entry quantizations of the **observed
physical** ``mixer.vco_1``/``mixer.vco_2``/``mixer.noise`` levels (site
``mixer.level_q``) and ``acc`` the DR-0010 48-bit-class accumulator in the
declared Q6.42 product format. The mixer input curves ``[1.0, 1.0,
0.025]`` are **not** reimplemented here: they are already folded into the
recorded physical levels at the measured-observation seam
(``spec/FLOAT-MIX.md``, :data:`torchsynth_voice.float_mix.MIXER_INPUT_CURVES`),
so the noise lane's gain enters this module exactly as the oscillator
lanes' gains do — through the same entry site — and
:func:`observed_curve_exponent` only *reports* the exponent observable in
a case's own recorded (normalized, physical) pair so a dropped or swapped
curve is visible as evidence rather than as a silent renormalization.
The recorded curve is the upstream range's **reciprocal-exponent**
parameter (``physical = normalized ** (1 / curve)`` over ``[0, 1]``:
linear for both oscillators, power-40 for noise), not a multiplicative
trim.

Everything in this lane is exact integer dataflow: three C1 products with
ONE declared half-even narrowing each and C7 saturation, three level
products accumulated exactly in Q6.42 with no intermediate rounding, one
declared half-even narrowing to the C1 ``mixer.pre_normalization`` word
with C7 saturation, and the pre-normalization peak feed (the exact
magnitude maximum with the earliest maximal index, per
``fixed_voice.normalize_words``). There are **no** declared shadow sites
in this lane: no binary64 value participates anywhere.

Scope boundary: the C9 normalization replay itself (the strict
``peak > 1`` branch, the U1.22 reciprocal, the S5 gain multiply) is
**#77's** owner row and is not implemented here. This module produces the
pre-normalization output and the peak feed #77 consumes, and the running
peak register's compare is reported as #77's declared op, never folded
into this lane's own counts.

Provenance discipline: :func:`derive_case` renders the frozen composed
model (:class:`torchsynth_voice.fixed_voice.FixedVoiceModel`) for a case
and asserts that :func:`mirror_mix_lane` reproduces the model's own
``vco_1.post_vca`` / ``vco_2.post_vca`` / ``noise.post_vca`` /
``mixer.pre_normalization`` / ``mixer.peak`` rows and its per-site sticky
counter records exactly. The mirror is therefore proved to be the model,
not a harness-parallel reimplementation; callers additionally pin the
model's rows to the frozen ``fixed-voice-golden-v1`` receipt digests
wherever the receipt commits them.

This module makes NO RTL claim of any kind: no synthesis, layout,
signoff, hardware playback, or sound-fidelity claim. The RTL engine
(``tb/sv/audio_mix_engine.sv``) reproduces the same integer dataflow
bit-exactly; ``tb/run_tb.py mix`` is the flow that proves it.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import float_interfaces as fi
from . import float_sources as fs
from .fixed_voice import AcceptedFormats, FixedVoiceModel, entry_quantize
from .fixedpoint.counters import ROUNDING, SATURATION, StickyCounters
from .fixedpoint.formats import FixedFormat
from .fixedpoint.ops import OverflowPolicy, mul, rescale
from .float_mix import MIXER_INPUT_CURVES
from .noise_stream_golden import canonical_slot

__all__ = [
    "MixGoldenError",
    "LANES",
    "VCA_SITES",
    "MIXER_SITE",
    "LEVEL_ENTRY_SITE",
    "MIXER_INPUT_CURVES",
    "PRODUCT_INT_BITS",
    "product_format",
    "digest_words",
    "level_words",
    "observed_curve_exponent",
    "mirror_mix_lane",
    "peak_feed",
    "derive_case",
    "mix_traces",
]

#: The mixer's declared source order (the frozen model's accumulation order).
LANES = ("vco_1", "vco_2", "noise")

#: The three audio VCA narrowing sites, in the model's own naming.
VCA_SITES = {"vco_1": "vca_1.S4", "vco_2": "vca_2.S4", "noise": "vca_3.S4"}

#: The mixer's single declared narrowing site.
MIXER_SITE = "mixer.S4"

#: The S1 entry site the three mixer level words are quantized at.
LEVEL_ENTRY_SITE = "mixer.level_q"

#: The accumulator product format's integer bits (the model's own
#: ``FixedFormat(signed=True, int_bits=6, frac_bits=2 * audio.frac_bits)``).
PRODUCT_INT_BITS = 6

#: The lane's declared output traces, in trace-registry order.
MIX_TRACES = (
    "vco_1.post_vca",
    "vco_2.post_vca",
    "noise.post_vca",
    "mixer.pre_normalization",
    "mixer.peak",
)


class MixGoldenError(ValueError):
    """Raised when the VCA/mixer mirror refuses its inputs or drifts."""


def product_format(audio: FixedFormat) -> FixedFormat:
    """The model's declared mixer accumulator format (Q6.42 for C1)."""

    return FixedFormat(
        signed=True, int_bits=PRODUCT_INT_BITS, frac_bits=2 * audio.frac_bits
    )


def digest_words(words: Sequence[int]) -> str:
    """The #54 trace-digest convention over an integer word list."""

    blob = json.dumps(
        list(words), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def level_words(
    formats: AcceptedFormats,
    physical: Dict[str, Any],
    counters: Optional[StickyCounters] = None,
) -> Dict[str, int]:
    """The three S1 mixer level entry words, in the model's own order.

    The values consumed are the **observed physical** mixer levels: the
    normalized-to-physical conversion (including the recorded upstream
    input curves) is a measured seam and is never recomputed here.
    """

    tally = counters if counters is not None else StickyCounters()
    words: Dict[str, int] = {}
    for lane in LANES:
        name = "mixer." + lane
        if name not in physical:
            raise MixGoldenError("physical map carries no %r" % name)
        words[lane] = entry_quantize(
            float(physical[name]), formats.audio, tally, LEVEL_ENTRY_SITE
        )
    return words


def observed_curve_exponent(
    physical: Dict[str, Any], normalized: Dict[str, Any], lane: str
) -> Optional[float]:
    """The conversion exponent observable in one lane's recorded pair.

    Reported evidence only. The recorded upstream input curve is the
    range's reciprocal-exponent parameter
    (:data:`torchsynth_voice.float_mix.MIXER_INPUT_CURVES`: ``1.0`` for
    both oscillators, ``0.025`` for noise), so over the ``[0, 1]`` mixer
    ranges ``physical = normalized ** (1 / curve)`` and the observable is
    ``log(physical) / log(normalized)`` — ``1`` for the oscillators and
    ``40`` for noise. A dropped or swapped curve shows up here as an
    exponent that no longer matches the recorded one.

    ``None`` when no exponent is observable from the pair (either value at
    ``0``, or ``normalized`` at exactly ``1``, where every exponent
    agrees).
    """

    if lane not in MIXER_INPUT_CURVES:
        raise MixGoldenError("unknown mixer lane %r" % (lane,))
    name = "mixer." + lane
    value_n = float(normalized.get(name, 0.0))
    value_p = float(physical[name])
    if value_n <= 0.0 or value_n == 1.0 or value_p <= 0.0:
        return None
    return math.log(value_p) / math.log(value_n)


def mirror_mix_lane(
    formats: AcceptedFormats,
    raw: Dict[str, Sequence[int]],
    amp: Dict[str, Sequence[int]],
    levels: Dict[str, int],
    samples: Optional[int] = None,
) -> Tuple[Dict[str, List[int]], Dict[str, Any]]:
    """Exact integer re-walk of the frozen model's VCA + mixer lane.

    ``raw`` and ``amp`` carry the three C1 audio-rate source and
    amplitude-envelope streams keyed by lane; ``levels`` carries the
    three S1 entry level words. Returns the ``post_vca`` streams (keyed by
    lane), the ``mix`` (``mixer.pre_normalization``) stream, the ``abs``
    magnitude feed, and the running ``peak`` stream, plus an auxiliary
    mapping with the per-site sticky counter records, the accumulator's
    observed Q6.42 extremes, and the final peak word/index.

    ``samples`` caps the walk (a prefix); ``None`` walks every supplied
    sample. The walk uses the model's own primitives only.
    """

    audio = formats.audio
    mode = formats.mode
    saturate = OverflowPolicy.SATURATE
    product_fmt = product_format(audio)
    for lane in LANES:
        if lane not in raw or lane not in amp:
            raise MixGoldenError("missing %r source or amplitude stream" % lane)
        if lane not in levels:
            raise MixGoldenError("missing %r mixer level word" % lane)
    total = min(len(raw[lane]) for lane in LANES)
    total = min(total, min(len(amp[lane]) for lane in LANES))
    if samples is not None:
        total = min(total, samples)

    counters = StickyCounters()
    post: Dict[str, List[int]] = {lane: [] for lane in LANES}
    mix: List[int] = []
    magnitudes: List[int] = []
    running: List[int] = []
    peak = 0
    peak_index = -1
    acc_min = 0
    acc_max = 0
    for n in range(total):
        products = []
        for lane in LANES:
            word = mul(
                raw[lane][n], audio, amp[lane][n], audio, audio, mode,
                saturate, counters, VCA_SITES[lane],
            )
            post[lane].append(word)
            products.append(word * levels[lane])
        acc = products[0] + products[1] + products[2]
        if acc < acc_min:
            acc_min = acc
        if acc > acc_max:
            acc_max = acc
        if not product_fmt.contains(acc):
            raise MixGoldenError(
                "mixer accumulator left the declared %s band at sample %d "
                "(%d): the model refuses this, it is never a saturation "
                "event" % (product_fmt.identity, n, acc)
            )
        word = rescale(
            acc, product_fmt, audio, mode, saturate, counters, MIXER_SITE
        )
        mix.append(word)
        magnitude = -word if word < 0 else word
        magnitudes.append(magnitude)
        if magnitude > peak:
            peak = magnitude
            peak_index = n
        running.append(peak)

    tally = counters.as_json()
    per_site = {
        (record["site"], record["kind"]): record["count"]
        for record in tally["records"]
    }
    sites = {}
    for site in list(VCA_SITES.values()) + [MIXER_SITE]:
        sites[site] = {
            SATURATION: per_site.get((site, SATURATION), 0),
            ROUNDING: per_site.get((site, ROUNDING), 0),
        }
    streams = {
        "post_vca": post,
        "mix": mix,
        "abs": magnitudes,
        "peak": running,
    }
    aux = {
        "counters": tally,
        "sites": sites,
        "sats": sum(site[SATURATION] for site in sites.values()),
        "rounds": sum(site[ROUNDING] for site in sites.values()),
        "peak_word": peak,
        "peak_index": peak_index,
        "acc_min": acc_min,
        "acc_max": acc_max,
        "product_format": product_fmt.identity,
        "samples": total,
    }
    return streams, aux


def peak_feed(mix_words: Sequence[int]) -> Tuple[int, int]:
    """The pre-normalization peak feed: magnitude maximum, earliest index.

    Exactly ``fixed_voice.normalize_words``' peak reduction (strict
    ``>``, so ties keep the earliest maximal index). Returns
    ``(peak_word, peak_index)``; the index is ``-1`` for an all-zero
    (silent) clip, which has no maximal sample.
    """

    peak = 0
    index = -1
    for n, word in enumerate(mix_words):
        magnitude = -word if word < 0 else word
        if magnitude > peak:
            peak = magnitude
            index = n
    return peak, index


def mix_traces(
    streams: Dict[str, Any], aux: Dict[str, Any]
) -> Dict[str, List[int]]:
    """The lane's declared traces in the model's own registry naming."""

    return {
        "vco_1.post_vca": streams["post_vca"]["vco_1"],
        "vco_2.post_vca": streams["post_vca"]["vco_2"],
        "noise.post_vca": streams["post_vca"]["noise"],
        "mixer.pre_normalization": streams["mix"],
        "mixer.peak": [aux["peak_word"]],
    }


def render_model(
    formats: AcceptedFormats,
    physical: Dict[str, Any],
    sound_index: int = 0,
    normalized: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, List[int]], Dict[str, Any]]:
    """Render one case through the frozen composed fixed model.

    The noise stream is resolved through the C8 host-feed path for
    ``sound_index`` (seed 13, slot ``sound_index % 32``), exactly as the
    receipt generator resolves it. The normalized map is the float-side
    seam the fixed model never reads; a placeholder is supplied when the
    caller has none.
    """

    noise_bytes = fs.NoiseSource.resolve(sound_index)
    request = fi.ResolvedRequest(
        sound_index,
        dict(normalized) if normalized is not None
        else {name: 0.0 for name in physical},
        {
            "seed": fs.NOISE_SEED,
            "slot": canonical_slot(sound_index),
            "sample_count": fi.AUDIO_SAMPLES,
            "sha256": hashlib.sha256(noise_bytes).hexdigest(),
            "samples": noise_bytes,
        },
        physical=dict(physical),
        execution_status="canonical-batched",
    )
    return FixedVoiceModel(request, formats).render()


def derive_case(
    formats: AcceptedFormats,
    physical: Dict[str, Any],
    sound_index: int = 0,
    normalized: Optional[Dict[str, Any]] = None,
    samples: Optional[int] = None,
) -> Dict[str, Any]:
    """Stimulus + truth for one case, with the mirror proved against the model.

    Renders the frozen composed model, takes the lane's stimulus from the
    model's own rows (the three raw source streams, the three
    endpoint-aligned amplitude columns, the three S1 level words), walks
    the mirror, and REFUSES unless the mirror reproduces the model's
    ``*.post_vca`` / ``mixer.pre_normalization`` / ``mixer.peak`` rows and
    the model's own per-site sticky counter records for this lane's four
    declared sites.

    ``samples`` caps the mirror walk for a prefix demonstration; the
    row-equality proof then covers the walked prefix only and the returned
    case is marked ``prefix=True``.
    """

    traces, diagnostics = render_model(
        formats, physical, sound_index, normalized
    )
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
    counters = StickyCounters()
    levels = level_words(formats, physical, counters)
    streams, aux = mirror_mix_lane(formats, raw, amp, levels, samples=samples)
    walked = aux["samples"]
    prefix = samples is not None and walked < len(traces["mixer.pre_normalization"])

    for lane in LANES:
        name = ("noise" if lane == "noise" else lane) + ".post_vca"
        model_row = traces[name][:walked]
        if streams["post_vca"][lane] != model_row:
            raise MixGoldenError(
                "VCA/mixer mirror diverges from the composed model on %s "
                "(first %d samples)" % (name, walked)
            )
    if streams["mix"] != traces["mixer.pre_normalization"][:walked]:
        raise MixGoldenError(
            "VCA/mixer mirror diverges from the composed model on "
            "mixer.pre_normalization (first %d samples)" % walked
        )
    if not prefix:
        if [aux["peak_word"]] != traces["mixer.peak"]:
            raise MixGoldenError(
                "peak feed %d disagrees with the composed model's "
                "mixer.peak %r" % (aux["peak_word"], traces["mixer.peak"])
            )
        model_sites = {
            (record["site"], record["kind"]): record["count"]
            for record in diagnostics["counters"]["records"]
        }
        for site, kinds in aux["sites"].items():
            for kind, count in kinds.items():
                expected = model_sites.get((site, kind), 0)
                if count != expected:
                    raise MixGoldenError(
                        "mirror counter %s/%s is %d but the composed model "
                        "records %d" % (site, kind, count, expected)
                    )
    return {
        "levels": levels,
        "raw": raw,
        "amp": amp,
        "streams": streams,
        "aux": aux,
        "traces": mix_traces(streams, aux),
        "model_traces": {name: traces[name] for name in MIX_TRACES},
        "diagnostics": diagnostics,
        "prefix": prefix,
        "samples": walked,
    }
