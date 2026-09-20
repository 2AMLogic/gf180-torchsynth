"""Independent float Voice mix chain: audio VCAs, mixer, normalization.

Issue #42 deliverable. Stdlib-only and TorchSynth/Torch/NumPy import-free DSP
for the audio VCA call sites, the pre-normalization mixer, and the conditional
whole-clip normalization of the default Voice at their declared call sites in
``spec/FLOAT-INTERFACES.md``: ``vca#1``/``vca#2``/``vca#3`` (checkpoint orders
16, 20 and 23), ``normalize_if_clipping`` entry/reduction/decision (orders
24-26) and the ``mixer#1`` return (order 27). The control upsamplers feeding
the VCA gain envelopes belong to #40 and enter as declared audio-rate inputs;
the sources feeding the VCAs belong to #41 and enter as declared buffers or
resolved noise bytes. End-to-end conformance belongs to #43.

The declared calculation policy (``CALCULATION_POLICY``) reproduces the pinned
upstream evaluation order at commit ``2b0964d4c6c3d472a2a0d54d91b408caaeffca6d``
(``torchsynth/module.py`` ``VCA.output`` and ``Mixer.output``,
``torchsynth/util.py`` ``normalize_if_clipping``):

- the audio VCA is the elementwise product ``audio_in * control_in`` of two
  binary32 buffers; each product rounds once to binary32, exactly like the
  pinned elementwise kernel;
- the mixer is the ordered weighted sum over ``vco_1.post_vca``,
  ``vco_2.post_vca`` and ``noise.post_vca`` with the observed physical
  ``mixer.vco_1``/``mixer.vco_2``/``mixer.noise`` levels (measured-observation
  seam: the normalized-to-physical conversion, including the recorded upstream
  input curves ``[1.0, 1.0, 0.025]``, is never reimplemented here). Products
  and the three-term accumulation run in binary64 in declared source order and
  round to binary32 once at the trace output; the pinned batched ``matmul``
  accumulates in binary32 FMA chains and may differ by roughly one ulp, so the
  mix traces are ``declared-metrics`` comparisons, never byte identity;
- the whole-clip peak is the exact binary32 maximum of ``torch.abs(signal)``
  over the complete clip; the recorded peak location is the earliest maximal
  index (pinned ``torch.max`` semantics). Peak 1.0 exactly, silence, and every
  peak at or below one bypass: the branch condition is strict ``peak > 1``;
- on the normalize branch the output is the elementwise binary32 division of
  the mix by the observed peak, exactly like the pinned ``torch.where`` arm.
  ``mixer.gain`` is the derived diagnostic - one when the observed peak is at
  most one, otherwise its binary32 reciprocal - recorded for replay
  experiments but never used to generate audio: upstream applies division
  directly, and a multiply-by-reciprocal implementation is a later numerical
  choice that is not proof of byte-identical division;
- DR-0003 stays Proposed. This module implements and tests the normalization
  *semantics* (strict ``peak > 1`` over the complete clip, bypass at one,
  silence, earliest-index ties, division by the observed peak, unity
  otherwise); it ratifies no replay/buffering architecture and no limiter,
  AGC, or constant-headroom substitute. The preregistered mutations include
  exactly those substitutes, and each must fail the declared match.

Numeric formats for a later fixed model stay open: the checkpoint map carries
``numeric_contract = "unbound:#53"`` and DR-0008 is Proposed, so nothing here
reads, depends on, or ratifies any selected fixed-format value.
"""

from __future__ import annotations

import math
import struct

from . import float_interfaces as fi

SOURCE_COMMIT = fi.SOURCE_COMMIT
AUDIO_SAMPLES = fi.AUDIO_SAMPLES

CALCULATION_POLICY = fi.CalculationPolicy(
    calculation_dtype="float32",
    operation_policy=(
        "pinned-upstream-op-order-v1: elementwise binary32 VCA products and "
        "normalization divisions at every site; ordered mixer weighted sum "
        "with binary64 products and accumulation in source order (vco_1, "
        "vco_2, noise) rounded once to binary32 (upstream batched matmul FMA "
        "differs ~1 ulp; declared-metrics); exact whole-clip binary32 peak "
        "with earliest maximal index; strict peak > 1 branch; division by the "
        "observed peak applied directly; mixer.gain is the derived diagnostic "
        "reciprocal, never used to generate audio"
    ),
    version="float-mix-v1",
)

MIXER_SOURCES = ("vco_1", "vco_2", "noise")

MIXER_INPUT_CURVES = {"vco_1": 1.0, "vco_2": 1.0, "noise": 0.025}

OWNED_TRACES = (
    "vco_1.post_vca",
    "vco_2.post_vca",
    "noise.post_vca",
    "mixer.pre_normalization",
    "mixer.peak",
    "mixer.gain",
    "mixer.output",
)

MUTATION_LIMITER = "limiter-substitute"
MUTATION_AGC = "agc-substitute"
MUTATION_ALWAYS = "normalization-always-on"
MUTATION_NEVER = "normalization-off"
MUTATION_WRONG_PEAK = "wrong-peak"
MUTATION_WRONG_ORDER = "wrong-mix-order"
MUTATION_PRE_VCA_GAIN = "pre-vca-gain"
MUTATION_LATE_TIE = "latest-index-tie"
MUTATIONS = frozenset(
    {
        MUTATION_LIMITER,
        MUTATION_AGC,
        MUTATION_ALWAYS,
        MUTATION_NEVER,
        MUTATION_WRONG_PEAK,
        MUTATION_WRONG_ORDER,
        MUTATION_PRE_VCA_GAIN,
        MUTATION_LATE_TIE,
    }
)

AGC_HEADROOM = 0.25


class FloatMixError(ValueError):
    """Raised for contract violations in the mix-chain model."""


def f32(x):
    """Round one real to the declared binary32 calculation dtype."""

    return struct.unpack("<f", struct.pack("<f", x))[0]


def f32le_bytes(samples):
    """Encode binary32 samples as the declared little-endian buffer bytes."""

    return struct.pack("<%df" % len(samples), *samples)


def f32le_values(data):
    """Decode declared little-endian binary32 buffer bytes."""

    if type(data) is not bytes:
        raise ValueError("buffer bytes required")
    if len(data) % 4:
        raise ValueError("truncated binary32 buffer")
    return struct.unpack("<%df" % (len(data) // 4), data)


def checkpoint_bindings():
    """The declared call sites this module implements, from the landed map."""

    document = fi.load_checkpoints()
    vca_orders = []
    normalization_orders = []
    mixer_order = None
    for entry in document["checkpoints"]:
        module = entry["module"]
        if module == "vca":
            vca_orders.append((entry["order"], entry["occurrence"]))
        elif module == "normalize_if_clipping":
            normalization_orders.append((entry["order"], entry["outputs"][0]))
        elif module == "mixer":
            mixer_order = entry["order"]
    if sorted(vca_orders) != [(16, 1), (20, 2), (23, 3)]:
        raise FloatMixError("audio VCA call sites missing from the declared map")
    if normalization_orders != [
        (24, "mixer.pre_normalization"),
        (25, "mixer.peak"),
        (26, "mixer.gain"),
    ]:
        raise FloatMixError("normalization checkpoints missing from the map")
    if mixer_order != 27:
        raise FloatMixError("mixer return checkpoint missing from the map")
    return {
        "vca": sorted(vca_orders),
        "normalize_if_clipping": normalization_orders,
        "mixer": mixer_order,
        "traces": list(OWNED_TRACES),
    }


def _checked_buffer(values, name):
    if len(values) != AUDIO_SAMPLES:
        raise FloatMixError(
            "%s must contain exactly %d samples" % (name, AUDIO_SAMPLES)
        )
    for value in values:
        if type(value) is not float or not math.isfinite(value):
            raise FloatMixError("nonfinite or non-binary32 sample in " + name)
    return values


def audio_vca(source, gain):
    """Checkpoint ``vca#1``/``#2``/``#3``: the elementwise binary32 product.

    Pinned ``VCA.output`` is ``audio_in * control_in``; each product of two
    binary32 values rounds once to binary32, which is the exact IEEE result
    the pinned elementwise kernel produces.
    """

    _checked_buffer(source, "vca source")
    _checked_buffer(gain, "vca gain envelope")
    return [f32(a * b) for a, b in zip(source, gain)]


def mixer_weighted_sum(sources, levels, mutations=frozenset()):
    """The ordered weighted mix; binary64 products and accumulation.

    ``sources`` is ordered ``vco_1.post_vca``, ``vco_2.post_vca``,
    ``noise.post_vca``; ``levels`` holds the observed physical
    ``mixer.vco_1``/``mixer.vco_2``/``mixer.noise`` values in the same order.
    The pinned ``Mixer.output`` reduces the batched ``matmul`` over the three
    sources in this order; the model forms exact binary64 products, accumulates
    in declared order, and rounds to binary32 once at the output.
    """

    if len(sources) != 3 or len(levels) != 3:
        raise FloatMixError("mixer requires three sources and three levels")
    for index, source in enumerate(sources):
        _checked_buffer(source, "mixer source %d" % index)
    weighted = list(sources)
    if MUTATION_WRONG_ORDER in mutations:
        # Negative control: sources arrive in reversed order against the
        # canonical level order, so noise carries the vco_1 level and vco_1
        # the noise level. Must fail the mix rows.
        weighted = [weighted[2], weighted[1], weighted[0]]
    merged = None
    for level, source in zip(levels, weighted):
        if merged is None:
            merged = [float(level) * value for value in source]
        else:
            merged = [
                total + float(level) * value
                for total, value in zip(merged, source)
            ]
    return [f32(value) for value in merged]


def peak_of_clip(signal, tie="earliest"):
    """Whole-clip absolute maximum and its recorded index.

    Pinned ``torch.max(torch.abs(signal), dim=1)``; the recorded index is the
    earliest maximal index, so tied maxima resolve to the first occurrence and
    a late unique peak records the late index. ``tie="latest"`` is a
    preregistered negative control only and must fail the index row.
    """

    _checked_buffer(signal, "peak signal")
    peak = -1.0
    index = -1
    for position, value in enumerate(signal):
        magnitude = abs(value)
        if magnitude > peak or (tie == "latest" and magnitude == peak):
            peak = magnitude
            index = position
    return peak, index


def _runner_up_peak(signal):
    """Largest absolute value with one occurrence of the maximum removed."""

    peak, _ = peak_of_clip(signal)
    seen_peak = False
    runner = -1.0
    for value in signal:
        magnitude = abs(value)
        if magnitude == peak and not seen_peak:
            seen_peak = True
            continue
        if magnitude > runner:
            runner = magnitude
    return runner


def derived_gain(peak):
    """The ``mixer.gain`` diagnostic: unity at or below one, else reciprocal.

    Recorded for replay experiments and never used to generate audio: the
    pinned upstream applies division by the peak directly on the strict
    ``peak > 1`` branch.
    """

    if peak > 1.0:
        return f32(1.0 / peak)
    return 1.0


def normalize_if_clipping(signal, mutations=frozenset()):
    """Pinned conditional whole-clip normalization semantics.

    Returns ``(output, peak, gain, branch)`` where ``branch`` is the strict
    ``peak > 1`` decision actually taken. The bypass arms - peak exactly one,
    silence, any peak at or below one - return the input bytes unchanged; the
    normalize arm divides elementwise in binary32 by the observed peak. The
    preregistered mutations are negative controls: a hard limiter and a
    constant-headroom AGC substitute (both forbidden by DR-0003), forced
    always-on and forced-off branches, a runner-up wrong peak divisor, and a
    latest-index tie. Each must fail its declared comparison rows.
    """

    _checked_buffer(signal, "mix signal")
    peak, index = peak_of_clip(
        signal, "latest" if MUTATION_LATE_TIE in mutations else "earliest"
    )
    divisor = peak
    if MUTATION_WRONG_PEAK in mutations:
        # Negative control: the runner-up magnitude drives the branch and the
        # division. No-op on silence (no runner-up above -1.0 is impossible;
        # a zero clip keeps divisor zero and bypasses), must fail elsewhere.
        divisor = max(_runner_up_peak(signal), 0.0)
    branch = divisor > 1.0
    if MUTATION_ALWAYS in mutations:
        branch = True
    if MUTATION_NEVER in mutations:
        branch = False
    if MUTATION_LIMITER in mutations:
        # Negative control: hard limiter at +/- 1 (a DR-0003-forbidden
        # substitute). Must fail the above-one output row.
        output = [
            -1.0 if value < -1.0 else (1.0 if value > 1.0 else value)
            for value in signal
        ]
        return output, peak, derived_gain(peak), branch
    if MUTATION_AGC in mutations:
        # Negative control: constant headroom scale (a DR-0003-forbidden
        # substitute). Must fail above-one and below-one output rows alike.
        output = [f32(value * AGC_HEADROOM) for value in signal]
        return output, peak, derived_gain(peak), branch
    if branch:
        output = [f32(value / divisor) for value in signal]
    else:
        output = list(signal)
    return output, peak, derived_gain(peak), branch


def render_mix_chain(sources, amps, levels, mutations=frozenset()):
    """Render every #42-owned trace from declared inputs.

    ``sources`` is ordered ``vco_1.raw``, ``vco_2.raw``, ``noise.raw``;
    ``amps`` is ordered ``control_upsample.vco_1_amp``,
    ``control_upsample.vco_2_amp``, ``control_upsample.noise_amp``; ``levels``
    is ordered ``mixer.vco_1``, ``mixer.vco_2``, ``mixer.noise`` (observed
    physical values). Returns ``(rendered, diagnostics)``: ``rendered`` maps
    the seven owned registry names to their values (``mixer.peak`` and
    ``mixer.gain`` as single-value lists, per the registry shapes), and
    ``diagnostics`` carries the peak index and the branch decision, which are
    not registry traces.
    """

    if len(sources) != 3 or len(amps) != 3:
        raise FloatMixError("mix chain requires three sources and three amps")
    unknown = set(mutations) - MUTATIONS
    if unknown:
        raise FloatMixError("unknown mutations: " + ",".join(sorted(unknown)))
    post_vca = []
    for position, (source, amp) in enumerate(zip(sources, amps)):
        if MUTATION_PRE_VCA_GAIN in mutations:
            # Negative control: the mixer level applied before the VCA
            # instead of at the mixer. Must fail the post-VCA rows and every
            # downstream row.
            scaled = [f32(float(levels[position]) * value) for value in source]
            post_vca.append(audio_vca(scaled, amp))
        else:
            post_vca.append(audio_vca(source, amp))
    unit_levels = [1.0, 1.0, 1.0] if MUTATION_PRE_VCA_GAIN in mutations else levels
    pre_normalization = mixer_weighted_sum(post_vca, unit_levels, mutations)
    output, peak, gain, branch = normalize_if_clipping(
        pre_normalization, mutations
    )
    _, index = peak_of_clip(
        pre_normalization,
        "latest" if MUTATION_LATE_TIE in mutations else "earliest",
    )
    rendered = {
        "vco_1.post_vca": post_vca[0],
        "vco_2.post_vca": post_vca[1],
        "noise.post_vca": post_vca[2],
        "mixer.pre_normalization": pre_normalization,
        "mixer.peak": [peak],
        "mixer.gain": [gain],
        "mixer.output": output,
    }
    diagnostics = {
        "peak_index": index,
        "normalized_branch": branch,
        "levels": list(levels),
    }
    return rendered, diagnostics
