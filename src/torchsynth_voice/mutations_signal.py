"""Oscillator, gain, clipping and normalization fault operators (#33).

The #33 negative-control family of spec/MUTATIONS.md, registered through the
landed public API (``mutations.register_operator``) in this family-owned file.
The ``bridge.*`` voice-runtime operators stay #30-owned test-only proofs and
are never published as this family's qualification; conversely the operators
here are never executed by the pinned release-era worker, whose hosted
mechanics remain the bridge proofs only.

Domain and seams. Every operator binds one declared seam from the landed seam
catalog: parameter-level perturbations (oscillator tune, phase and shape)
declare ``voice.parameter_value`` on named inventory parameters, and
signal-level faults (oscillator mode substitute, +/-dB gain, polarity, DC
offset, quantization rounding, saturation, and the normalization decision
mutations) declare ``voice.post_module`` on the named registry traces. The
normalization decision itself stays non-writable at
``voice.normalization_decision``: a plan declaring a mutation there fails
closed naming the required ``AudioMixer.output`` producer handoff, and this
family's normalization mutations are therefore executable only as declared
mix-chain model faults over directed fixtures (composing with #131's
committed ``float_mix`` mutation semantics - they never re-derive them and
ratify no numeric format; DR-0003 stays Proposed).

Typed magnitudes carry native units: semitones and radians for oscillator
perturbations, dB for gain, amplitude for DC offsets, quantization steps and
saturation ceilings, with affected-sample counts reported per application.
Plan text is never evaluated as code; every magnitude, unit, configuration
key, enum value and declared composition is validated fail-closed by the
landed contract before execution.

Fixtures are directed development fixtures only: deterministic binary32
lanes and directed mix clips whose binary32 peaks sit exactly on the #131
normalization targets (``1.0 + 2**-23`` above, ``1.0 - 2**-24`` below, and
exactly ``1.0``). No holdout case is opened, no automatic level
normalization is applied before any comparison, and no actual-Voice runtime
execution is claimed: that stays recorded as not-run in the family
publication.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any, Dict, List, Optional, Tuple

from . import float_mix
from . import mutations
from . import mutation_runtime

FAMILY_ID = "signal-chain-v1"
FAMILY_VERSION = 1

POST_VCA_TRACES = ("vco_1.post_vca", "vco_2.post_vca", "noise.post_vca")
OSC_OUTPUT_TRACES = ("vco_1.post_vca", "vco_2.post_vca")
MIXER_OUTPUT_TRACES = ("mixer.output",)
TUNING_PARAMETERS = ("vco_1.tuning", "vco_2.tuning")
PHASE_PARAMETERS = ("vco_1.initial_phase", "vco_2.initial_phase")
SHAPE_PARAMETERS = ("vco_2.shape",)

LANE_SAMPLES = 176400  # the pinned #42 mix-chain clip length
SAMPLE_RATE_HZ = 44100.0
VCO_1_HZ = 110.0
VCO_2_HZ = 220.0
LANE_AMPLITUDE = 0.9
VCO_2_SHAPE = 0.5
HARMONICS = 16
NOISE_LANE_PERIOD = 97
FIXTURE_LEVELS = (1.0, 1.0, 0.025)
FIXTURE_AMPS = (0.8, 0.6, 0.3)

PEAK_ABOVE_ONE = 1.0 + 2.0 ** -23
PEAK_BELOW_ONE = 1.0 - 2.0 ** -24
PEAK_AT_ONE = 1.0

SEAM_POST_MODULE = "voice.post_module"
SEAM_PARAMETER = "voice.parameter_value"
SEAM_NORMALIZATION_DECISION = "voice.normalization_decision"

OPERATOR_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "signal.sham": {
        "version": 1,
        "seam": SEAM_POST_MODULE,
        "sham": True,
        "magnitude": {"type": "null", "unit": "none"},
        "configuration": {"trace": [], "slot": {"type": "integer", "minimum": 0}},
        "composes_with": [],
        "summary": (
            "family sham control: traverse dispatch and return the original "
            "lane value; marked sham, never detected, never a family fault"
        ),
    },
    "osc.tuning_shift": {
        "version": 1,
        "seam": SEAM_PARAMETER,
        "sham": False,
        "magnitude": {
            "type": "number",
            "unit": "semitone",
            "minimum": -24,
            "maximum": 24,
        },
        "configuration": {
            "parameter": list(TUNING_PARAMETERS),
            "slot": {"type": "integer", "minimum": 0},
        },
        "composes_with": ["gain.db"],
        "summary": (
            "shift one inventory-named oscillator tuning by the declared "
            "semitone magnitude; must fail the periodic cents row against "
            "the declared reference and the paired exactness rows"
        ),
    },
    "osc.phase_offset": {
        "version": 1,
        "seam": SEAM_PARAMETER,
        "sham": False,
        "magnitude": {"type": "number", "unit": "radian", "minimum": -7, "maximum": 7},
        "configuration": {
            "parameter": list(PHASE_PARAMETERS),
            "slot": {"type": "integer", "minimum": 0},
        },
        "composes_with": [],
        "summary": (
            "offset one inventory-named oscillator initial phase by the "
            "declared radian magnitude; must fail the periodic phase row "
            "against the declared zero-origin fixture"
        ),
    },
    "osc.shape_scale": {
        "version": 1,
        "seam": SEAM_PARAMETER,
        "sham": False,
        "magnitude": {"type": "number", "unit": "ratio", "minimum": 0, "maximum": 1000},
        "configuration": {
            "parameter": list(SHAPE_PARAMETERS),
            "slot": {"type": "integer", "minimum": 0},
        },
        "composes_with": [],
        "summary": (
            "scale one inventory-named square/saw shape value by the "
            "declared ratio (clamped into [0,1]); must fail the paired "
            "exactness rows on the shape-bearing lane"
        ),
    },
    "osc.mode_substitute": {
        "version": 1,
        "seam": SEAM_POST_MODULE,
        "sham": False,
        "magnitude": {"type": "null", "unit": "none"},
        "configuration": {
            "trace": list(OSC_OUTPUT_TRACES),
            "slot": {"type": "integer", "minimum": 0},
        },
        "composes_with": [],
        "summary": (
            "substitute the declared oscillator module output with the other "
            "VCO mode waveform (sine <-> square/saw) at the same frequency, "
            "phase and depth; must fail paired exactness rows at the source "
            "lane and downstream"
        ),
    },
    "gain.db": {
        "version": 1,
        "seam": SEAM_POST_MODULE,
        "sham": False,
        "magnitude": {"type": "number", "unit": "dB", "minimum": -60, "maximum": 60},
        "configuration": {
            "trace": list(POST_VCA_TRACES),
            "slot": {"type": "integer", "minimum": 0},
        },
        "composes_with": ["osc.tuning_shift"],
        "summary": (
            "apply the declared +/-dB gain error to one declared module "
            "output slot; must fail contract rows even when a band-limited "
            "optional observation stays tolerant"
        ),
    },
    "gain.polarity": {
        "version": 1,
        "seam": SEAM_POST_MODULE,
        "sham": False,
        "magnitude": {"type": "null", "unit": "none"},
        "configuration": {
            "trace": list(POST_VCA_TRACES),
            "slot": {"type": "integer", "minimum": 0},
        },
        "composes_with": [],
        "summary": (
            "invert the polarity of one declared module output slot; must "
            "fail contract rows even when a band-limited optional "
            "observation stays tolerant"
        ),
    },
    "gain.dc_offset": {
        "version": 1,
        "seam": SEAM_POST_MODULE,
        "sham": False,
        "magnitude": {"type": "number", "unit": "amplitude", "minimum": -2, "maximum": 2},
        "configuration": {
            "trace": list(POST_VCA_TRACES),
            "slot": {"type": "integer", "minimum": 0},
        },
        "composes_with": [],
        "summary": (
            "add the declared DC offset amplitude to one declared module "
            "output slot; must fail mean/max error rows even when the "
            "high-band spectral observation stays tolerant"
        ),
    },
    "clip.round_step": {
        "version": 1,
        "seam": SEAM_POST_MODULE,
        "sham": False,
        "magnitude": {
            "type": "number",
            "unit": "amplitude_step",
            "minimum": 0,
            "maximum": 1,
        },
        "configuration": {
            "trace": list(POST_VCA_TRACES),
            "slot": {"type": "integer", "minimum": 0},
        },
        "composes_with": ["clip.saturation_ceiling"],
        "summary": (
            "round one declared module output slot to the declared amplitude "
            "step (explicit numeric magnitude); reports the affected sample "
            "count and must fail paired exactness rows"
        ),
    },
    "clip.saturation_ceiling": {
        "version": 1,
        "seam": SEAM_POST_MODULE,
        "sham": False,
        "magnitude": {"type": "number", "unit": "amplitude", "minimum": 0, "maximum": 4},
        "configuration": {
            "trace": list(POST_VCA_TRACES),
            "slot": {"type": "integer", "minimum": 0},
        },
        "composes_with": ["clip.round_step"],
        "summary": (
            "saturate one declared module output slot at the declared +/- "
            "ceiling (explicit numeric magnitude); reports the affected "
            "sample count and must fail paired exactness rows"
        ),
    },
    "norm.always_on": {
        "version": 1,
        "seam": SEAM_POST_MODULE,
        "sham": False,
        "magnitude": {"type": "null", "unit": "none"},
        "configuration": {
            "trace": list(MIXER_OUTPUT_TRACES),
            "slot": {"type": "integer", "minimum": 0},
        },
        "composes_with": [],
        "summary": (
            "force the whole-clip normalization branch on (composes with "
            "#131 float_mix semantics); must fail the bypass decision and "
            "byte-preservation rows on the below/at-one fixtures"
        ),
    },
    "norm.off": {
        "version": 1,
        "seam": SEAM_POST_MODULE,
        "sham": False,
        "magnitude": {"type": "null", "unit": "none"},
        "configuration": {
            "trace": list(MIXER_OUTPUT_TRACES),
            "slot": {"type": "integer", "minimum": 0},
        },
        "composes_with": [],
        "summary": (
            "force the whole-clip normalization branch off (composes with "
            "#131 float_mix semantics); must fail the divided output row on "
            "the above-one fixture"
        ),
    },
    "norm.wrong_peak": {
        "version": 1,
        "seam": SEAM_POST_MODULE,
        "sham": False,
        "magnitude": {"type": "null", "unit": "none"},
        "configuration": {
            "trace": list(MIXER_OUTPUT_TRACES),
            "slot": {"type": "integer", "minimum": 0},
        },
        "composes_with": [],
        "summary": (
            "let the runner-up magnitude drive the branch and division "
            "(composes with #131 float_mix semantics); must fail the output "
            "rule row on the above-one fixture"
        ),
    },
    "norm.wrong_reciprocal": {
        "version": 1,
        "seam": SEAM_POST_MODULE,
        "sham": False,
        "magnitude": {"type": "null", "unit": "none"},
        "configuration": {
            "trace": list(MIXER_OUTPUT_TRACES),
            "slot": {"type": "integer", "minimum": 0},
        },
        "composes_with": [],
        "summary": (
            "report a doubled reciprocal as the derived mixer.gain replay "
            "diagnostic while the audio division stays correct; must fail "
            "the reciprocal gain-error row and pass the audio rows "
            "(complementary detection)"
        ),
    },
}

_REGISTERED = False


def register_family() -> None:
    """Register every family operator once through the landed public API."""
    global _REGISTERED
    for operator_id, definition in OPERATOR_DEFINITIONS.items():
        if operator_id not in mutations.OPERATORS:
            mutations.register_operator(operator_id, dict(definition))
    _REGISTERED = True


def f32(x: float) -> float:
    return float_mix.f32(x)


def _digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def lane_bytes(samples: List[float]) -> bytes:
    return float_mix.f32le_bytes(samples)


def _checked_slot(buffer: List[float], slot: int) -> List[float]:
    if slot != 0:
        raise mutations.MutationError(
            "directed signal fixtures host exactly one slot per lane: " + str(slot)
        )
    return buffer


def render_sine(freq_hz: float, phase_rad: float, amplitude: float, count: int) -> List[float]:
    """Deterministic sine lane rounded once to binary32 per sample."""
    return [
        f32(amplitude * math.sin(2.0 * math.pi * freq_hz * i / SAMPLE_RATE_HZ + phase_rad))
        for i in range(count)
    ]


_RENDER_CACHE: Dict[Tuple[Any, ...], List[float]] = {}


def _cached(key: Tuple[Any, ...], build):
    """Deterministic memoization over pure render inputs (no RNG, no state)."""
    cached = _RENDER_CACHE.get(key)
    if cached is None:
        cached = build()
        _RENDER_CACHE[key] = cached
    return cached


def render_squaresaw(
    shape: float, freq_hz: float, phase_rad: float, amplitude: float, count: int
) -> List[float]:
    """Deterministic band-limited square/saw mix lane; shape in [0, 1].

    Square partials are the odd harmonics 1/k with alternating signs; saw
    partials are all harmonics with alternating signs; the declared shape
    weight mixes them. Directed fixture semantics, not a TorchSynth claim.
    """
    key = ("squaresaw", f32(shape), f32(freq_hz), f32(phase_rad), f32(amplitude), count)
    return _cached(key, lambda: _render_squaresaw(shape, freq_hz, phase_rad, amplitude, count))


def _render_squaresaw(
    shape: float, freq_hz: float, phase_rad: float, amplitude: float, count: int
) -> List[float]:
    weight = min(1.0, max(0.0, shape))
    samples: List[float] = []
    for i in range(count):
        angle = 2.0 * math.pi * freq_hz * i / SAMPLE_RATE_HZ + phase_rad
        square = 0.0
        saw = 0.0
        for k in range(1, HARMONICS + 1):
            sign = -1.0 if k % 2 == 0 else 1.0
            saw += sign * math.sin(k * angle) / k
            if k % 2 == 1:
                square += sign * math.sin(k * angle) / k
        samples.append(f32(amplitude * ((1.0 - weight) * square + weight * saw)))
    return samples


def render_noise_lane(count: int) -> List[float]:
    """Deterministic pseudo-random lane; consumes no RNG state."""
    return [
        f32((((i * NOISE_LANE_PERIOD) % 257) - 128) / 128.0 * LANE_AMPLITUDE * 0.25)
        for i in range(count)
    ]


def constant_envelope(value: float) -> List[float]:
    """Deterministic full-length constant gain envelope for the chain."""
    key = ("envelope", f32(value))
    return _cached(key, lambda: [f32(value)] * LANE_SAMPLES)


ANALYTIC_SAMPLES = 4096
ANALYTIC_HZ = 440.0


def analytic_sine(
    freq_hz: float, phase_rad: float, amplitude: float = 0.5, count: int = ANALYTIC_SAMPLES
) -> List[float]:
    """Binary64-exact directed sine in the landed periodic estimator domain.

    The landed analytic estimator measures supplied analytic samples whose
    binary32 rounding noise would exceed its preregistered residual bound,
    so the oscillator property fixtures are exact binary64 lanes; the
    mix-chain fixtures stay binary32. No transformation is applied to any
    supplied samples.
    """
    return [
        amplitude * math.sin(2.0 * math.pi * freq_hz * i / SAMPLE_RATE_HZ + phase_rad)
        for i in range(count)
    ]


LANE_CONTEXT = {
    "vco_1.post_vca": {
        "mode": "sine",
        "freq_hz": VCO_1_HZ,
        "phase_rad": 0.0,
        "amplitude": LANE_AMPLITUDE,
        "shape": None,
    },
    "vco_2.post_vca": {
        "mode": "squaresaw",
        "freq_hz": VCO_2_HZ,
        "phase_rad": 0.0,
        "amplitude": LANE_AMPLITUDE,
        "shape": VCO_2_SHAPE,
    },
    "noise.post_vca": {
        "mode": "noise",
        "freq_hz": None,
        "phase_rad": None,
        "amplitude": LANE_AMPLITUDE * 0.25,
        "shape": None,
    },
}

PARAMETER_LANE = {
    "vco_1.tuning": "vco_1.post_vca",
    "vco_2.tuning": "vco_2.post_vca",
    "vco_1.initial_phase": "vco_1.post_vca",
    "vco_2.initial_phase": "vco_2.post_vca",
    "vco_2.shape": "vco_2.post_vca",
}


def render_lane(trace: str) -> List[float]:
    return _cached(("lane", trace), lambda: _render_lane_with(trace))


def render_lane_with(
    trace: str,
    *,
    freq_hz: Optional[float] = None,
    phase_rad: Optional[float] = None,
    shape: Optional[float] = None,
) -> List[float]:
    context = dict(LANE_CONTEXT[trace])
    if freq_hz is not None:
        context["freq_hz"] = freq_hz
    if phase_rad is not None:
        context["phase_rad"] = phase_rad
    if shape is not None:
        context["shape"] = shape
    key = (
        "lane_with",
        trace,
        f32(context["freq_hz"]),
        f32(context["phase_rad"]),
        None if context["shape"] is None else f32(context["shape"]),
    )
    return _cached(key, lambda: _render_lane_context(trace, context))


def _render_lane_with(trace: str) -> List[float]:
    return _render_lane_context(trace, LANE_CONTEXT[trace])


def _render_lane_context(trace: str, context: Dict[str, Any]) -> List[float]:
    if context["mode"] == "sine":
        return render_sine(context["freq_hz"], context["phase_rad"], context["amplitude"], LANE_SAMPLES)
    if context["mode"] == "squaresaw":
        return render_squaresaw(
            context["shape"], context["freq_hz"], context["phase_rad"], context["amplitude"], LANE_SAMPLES
        )
    return render_noise_lane(LANE_SAMPLES)


def directed_clip(peak_target: float, freq_hz: float = 110.0) -> List[float]:
    """Directed mix clip whose binary32 peak sits exactly on ``peak_target``.

    The #131 normalization targets are exact binary32 values; the fixture
    scales a deterministic sine toward the target and then pins the maximal
    sample to the target exactly, so the strict ``peak > 1`` branch decision
    is exercised at the directed boundary.
    """
    if float_mix.f32(peak_target) != peak_target:
        raise mutations.MutationError("peak target must be exactly binary32")
    key = ("clip", f32(peak_target), f32(freq_hz))
    return _cached(key, lambda: _build_directed_clip(peak_target, freq_hz))


def _build_directed_clip(peak_target: float, freq_hz: float) -> List[float]:
    samples = render_sine(freq_hz, 0.0, 0.9, float_mix.AUDIO_SAMPLES)
    peak, index = float_mix.peak_of_clip(samples)
    scale = peak_target / peak
    scaled = [f32(value * scale) for value in samples]
    _, pinned = float_mix.peak_of_clip(scaled)
    scaled[pinned] = peak_target if scaled[pinned] >= 0.0 else -peak_target
    # The directed sine advances its phase by the rational 11/4410 turns per
    # sample, so its sample values repeat across cycles and the extremum must
    # be made unique: every other exact-target sample (either sign) is demoted
    # one binary32 step below the target. The wrong-peak control then has a
    # well-defined runner-up strictly below the declared peak.
    step = 2.0 ** (-23 if abs(peak_target) >= 1.0 else -24)
    lowered = f32(abs(peak_target) - step)
    for position, value in enumerate(scaled):
        if position == pinned:
            continue
        if value == peak_target:
            scaled[position] = lowered
        elif value == -peak_target:
            scaled[position] = -lowered
    observed, _ = float_mix.peak_of_clip(scaled)
    extremum_count = sum(1 for value in scaled if value == observed or value == -observed)
    if observed != peak_target or extremum_count != 1:
        raise mutations.MutationError("directed clip peak is not unique on its target")
    return scaled


def apply_gain_db(samples: List[float], db: float) -> Tuple[List[float], Dict[str, Any]]:
    gain = f32(10.0 ** (db / 20.0))
    output = [f32(value * gain) for value in samples]
    affected = sum(1 for a, b in zip(samples, output) if a != b)
    return output, {"gain_linear": gain, "db": db, "affected_samples": affected}


def apply_polarity(samples: List[float]) -> Tuple[List[float], Dict[str, Any]]:
    output = [f32(-value) for value in samples]
    affected = sum(1 for a, b in zip(samples, output) if a != b)
    return output, {"multiplier": -1.0, "affected_samples": affected}


def apply_dc_offset(samples: List[float], offset: float) -> Tuple[List[float], Dict[str, Any]]:
    output = [f32(value + offset) for value in samples]
    affected = sum(1 for a, b in zip(samples, output) if a != b)
    return output, {"offset_amplitude": offset, "affected_samples": affected}


def apply_round_step(samples: List[float], step: float) -> Tuple[List[float], Dict[str, Any]]:
    if step < 0.0:
        raise mutations.MutationError("amplitude step must be nonnegative")
    output = [f32(round(value / step) * step) for value in samples]
    affected = sum(1 for a, b in zip(samples, output) if a != b)
    return output, {"step_amplitude": step, "mode": "round-to-nearest", "affected_samples": affected}


def apply_saturation(samples: List[float], ceiling: float) -> Tuple[List[float], Dict[str, Any]]:
    if ceiling < 0.0:
        raise mutations.MutationError("saturation ceiling must be nonnegative")
    output = [
        ceiling if value > ceiling else (-ceiling if value < -ceiling else value)
        for value in samples
    ]
    affected = sum(1 for a, b in zip(samples, output) if a != b)
    return output, {"ceiling_amplitude": ceiling, "affected_samples": affected}


def apply_mode_substitute(trace: str, samples: List[float]) -> Tuple[List[float], Dict[str, Any]]:
    context = LANE_CONTEXT[trace]
    substitute = "sine" if context["mode"] == "squaresaw" else "squaresaw"
    if substitute == "sine":
        replacement = render_sine(context["freq_hz"], context["phase_rad"], context["amplitude"], len(samples))
    else:
        replacement = render_squaresaw(
            VCO_2_SHAPE, context["freq_hz"], context["phase_rad"], context["amplitude"], len(samples)
        )
    return replacement, {"mode_from": context["mode"], "mode_to": substitute, "trace": trace}


def norm_mutation_set(operator_id: str) -> frozenset:
    mapping = {
        "norm.always_on": frozenset({float_mix.MUTATION_ALWAYS}),
        "norm.off": frozenset({float_mix.MUTATION_NEVER}),
        "norm.wrong_peak": frozenset({float_mix.MUTATION_WRONG_PEAK}),
    }
    if operator_id not in mapping:
        raise mutations.MutationError("operator has no float_mix mutation set: " + operator_id)
    return mapping[operator_id]


def wrong_reciprocal_gain(peak: float, branch: bool) -> float:
    """The declared wrong replay diagnostic: doubled binary32 reciprocal."""
    if branch:
        return f32(2.0 / peak)
    return 1.0


def instance(
    instance_id: str,
    operator_id: str,
    seam: str,
    magnitude: Any = None,
    configuration: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build one family mutation instance bound to its registration."""
    register_family()
    return mutation_runtime.instance(instance_id, operator_id, seam, magnitude, configuration)


def make_plan(source_binding: Dict[str, Any], mutations_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    register_family()
    catalog = mutations.load_seam_catalog(_catalog_path())
    return mutations.make_plan(source_binding, list(mutations_list), catalog)


def _catalog_path():
    from pathlib import Path

    return Path(__file__).resolve().parents[2] / mutations.SEAM_CATALOG_PATH


class SignalFixtureSession:
    """Apply a declared family plan to the directed signal fixture voice.

    Execution follows the fixture graph order - parameter swaps at render,
    then declared module-output replacements on the rendered lanes, then the
    mix chain, then the declared normalization-stage mutation - so observed
    event order equals declared plan order and the landed completeness gate
    accepts the log. Every application records an ordered event with
    original/replacement slot digests; nothing is silently swallowed.
    """

    def __init__(self, plan: Dict[str, Any]):
        self.plan = plan
        self.events: List[Dict[str, Any]] = []

    def _run_normalization_clip(
        self,
        norm_clip: List[float],
        norm_instances: List[Dict[str, Any]],
        wrong_reciprocal: bool,
        slot: int,
    ) -> Dict[str, Any]:
        """Apply the declared normalization-stage faults to the class clip.

        The directed clip is the declared pre-normalization mix: ``pre`` and
        ``post`` of every comparison are the same material, and the clip's
        binary32 peak sits exactly on the declared #131 class target.
        """
        norm_set = frozenset()
        for norm_instance in norm_instances:
            operator_id = norm_instance["operator"]
            if operator_id == "norm.wrong_reciprocal":
                continue
            norm_set = norm_set | norm_mutation_set(operator_id)
        post, peak, gain, branch = float_mix.normalize_if_clipping(norm_clip, norm_set)
        original_gain = gain
        detail: Dict[str, Any] = {
            "trace": "mixer.output",
            "slot": slot,
            "stage": "normalization",
            "mutation": sorted(norm_set),
            "branch": branch,
            "peak": peak,
            "reported_gain": gain,
        }
        if wrong_reciprocal:
            gain = wrong_reciprocal_gain(peak, branch)
            detail["reported_gain"] = gain
            detail["audio_division"] = "correct observed peak"
        for norm_instance in norm_instances:
            self._record(norm_instance, SEAM_POST_MODULE, "applied", dict(detail))
        rendered = {
            "mixer.peak": [peak],
            "mixer.gain": [gain],
            "mixer.output": post,
        }
        diagnostics = {
            "normalized_branch": branch,
            "peak_index": float_mix.peak_of_clip(norm_clip)[1],
            "original_reported_gain": original_gain,
        }
        return {
            "lanes": {"mixer.output.pre": norm_clip},
            "rendered": rendered,
            "diagnostics": diagnostics,
            "events": self.events,
            "events_summary": mutations.validate_events(self.plan, self.events),
        }

    def _record(self, plan_instance: Dict[str, Any], seam: str, status: str, detail: Dict[str, Any]) -> None:
        for event in self.events:
            if event["instance_id"] == plan_instance["instance_id"]:
                raise mutations.MutationError(
                    "duplicate event for instance: " + plan_instance["instance_id"]
                )
        self.events.append(
            {
                "instance_id": plan_instance["instance_id"],
                "seam": seam,
                "status": status,
                "order": len(self.events),
                "sham": plan_instance["sham"],
                "detail": detail,
            }
        )

    def run(self, slot: int = 0, norm_clip: Optional[List[float]] = None) -> Dict[str, Any]:
        plan_instances = self.plan["mutations"]
        positions = {entry["instance_id"]: position for position, entry in enumerate(plan_instances)}
        stages = {"parameter": [], "lane": [], "norm": []}
        for plan_instance in plan_instances:
            if plan_instance["seam"] == SEAM_PARAMETER:
                stages["parameter"].append(plan_instance)
            elif plan_instance["seam"] == SEAM_POST_MODULE:
                trace = plan_instance["configuration"]["trace"]
                if trace in MIXER_OUTPUT_TRACES:
                    if plan_instance["operator"] == "signal.sham":
                        stages["lane"].append(plan_instance)
                    else:
                        stages["norm"].append(plan_instance)
                else:
                    stages["lane"].append(plan_instance)
            else:
                raise mutations.MutationError(
                    "seam has no signal fixture target: " + str(plan_instance["seam"])
                )
        for later_stage, earlier_stage in (("lane", "parameter"), ("norm", "lane"), ("norm", "parameter")):
            for later_instance in stages[later_stage]:
                for earlier_instance in stages[earlier_stage]:
                    if (
                        positions[earlier_instance["instance_id"]]
                        > positions[later_instance["instance_id"]]
                    ):
                        raise mutations.MutationError(
                            "declared order violates fixture graph causality "
                            "(parameter, then module lane, then normalization stage)"
                        )

        parameter_lane = {}
        for swap in stages["parameter"]:
            trace = PARAMETER_LANE[swap["configuration"]["parameter"]]
            if trace in parameter_lane:
                raise mutations.MutationError(
                    "directed fixture hosts at most one parameter swap per lane per attempt: " + trace
                )
            parameter_lane[trace] = swap

        lanes = {trace: render_lane(trace) for trace in ("vco_1.post_vca", "vco_2.post_vca", "noise.post_vca")}
        for trace in ("vco_1.post_vca", "vco_2.post_vca"):
            swap = parameter_lane.get(trace)
            if swap is None:
                continue
            original = lanes[trace]
            context = LANE_CONTEXT[trace]
            freq_hz = context["freq_hz"]
            phase_rad = context["phase_rad"]
            shape = context["shape"]
            parameter = swap["configuration"]["parameter"]
            magnitude = swap["magnitude"]["value"] if swap["magnitude"] is not None else None
            if parameter.endswith(".tuning"):
                freq_hz = freq_hz * (2.0 ** (magnitude / 12.0))
            elif parameter.endswith(".initial_phase"):
                phase_rad = phase_rad + magnitude
            elif parameter.endswith(".shape"):
                shape = min(1.0, max(0.0, shape * magnitude))
            lanes[trace] = render_lane_with(trace, freq_hz=freq_hz, phase_rad=phase_rad, shape=shape)
            self._record(
                swap,
                SEAM_PARAMETER,
                "applied",
                {
                    "parameter": parameter,
                    "slot": slot,
                    "original_slot_sha256": _digest_bytes(lane_bytes(original)),
                    "replacement_slot_sha256": _digest_bytes(lane_bytes(lanes[trace])),
                },
            )

        norm_set = frozenset()
        wrong_reciprocal = False
        for lane_instance in stages["lane"]:
            trace = lane_instance["configuration"]["trace"]
            operator_id = lane_instance["operator"]
            _checked_slot(lanes[trace], lane_instance["configuration"]["slot"])
            original = lanes[trace]
            original_digest = _digest_bytes(lane_bytes(original))
            if operator_id == "signal.sham":
                self._record(
                    lane_instance,
                    SEAM_POST_MODULE,
                    "ineffective",
                    {
                        "trace": trace,
                        "slot": lane_instance["configuration"]["slot"],
                        "note": "sham returns the original value",
                        "original_slot_sha256": original_digest,
                        "replacement_slot_sha256": original_digest,
                    },
                )
                continue
            magnitude = lane_instance["magnitude"]["value"] if lane_instance["magnitude"] is not None else None
            if operator_id == "gain.db":
                replacement, detail = apply_gain_db(original, magnitude)
            elif operator_id == "gain.polarity":
                replacement, detail = apply_polarity(original)
            elif operator_id == "gain.dc_offset":
                replacement, detail = apply_dc_offset(original, magnitude)
            elif operator_id == "clip.round_step":
                replacement, detail = apply_round_step(original, magnitude)
            elif operator_id == "clip.saturation_ceiling":
                replacement, detail = apply_saturation(original, magnitude)
            elif operator_id == "osc.mode_substitute":
                replacement, detail = apply_mode_substitute(trace, original)
            else:
                raise mutations.MutationError("operator has no lane application: " + operator_id)
            replacement_digest = _digest_bytes(lane_bytes(replacement))
            detail.update(
                {
                    "trace": trace,
                    "slot": lane_instance["configuration"]["slot"],
                    "original_slot_sha256": original_digest,
                    "replacement_slot_sha256": replacement_digest,
                }
            )
            if replacement == original:
                self._record(lane_instance, SEAM_POST_MODULE, "ineffective", detail)
                continue
            lanes[trace] = replacement
            self._record(lane_instance, SEAM_POST_MODULE, "applied", detail)

        for norm_instance in stages["norm"]:
            operator_id = norm_instance["operator"]
            if operator_id == "norm.wrong_reciprocal":
                wrong_reciprocal = True
                continue
            norm_set = norm_set | norm_mutation_set(operator_id)

        if stages["norm"]:
            if norm_clip is None:
                raise mutations.MutationError(
                    "normalization-stage plan requires the declared directed clip fixture"
                )
            if stages["parameter"] or stages["lane"]:
                raise mutations.MutationError(
                    "the directed normalization clip fixture hosts the "
                    "normalization stage only"
                )
            return self._run_normalization_clip(norm_clip, stages["norm"], wrong_reciprocal, slot)

        for norm_instance in stages["norm"]:
            if norm_instance["operator"] == "norm.wrong_reciprocal":
                continue
            self._record(
                norm_instance,
                SEAM_POST_MODULE,
                "applied",
                {
                    "trace": norm_instance["configuration"]["trace"],
                    "slot": norm_instance["configuration"]["slot"],
                    "mutation": sorted(norm_set),
                    "stage": "normalization",
                },
            )

        sources = (lanes["vco_1.post_vca"], lanes["vco_2.post_vca"], lanes["noise.post_vca"])
        amps = tuple(constant_envelope(value) for value in FIXTURE_AMPS)
        rendered, diagnostics = float_mix.render_mix_chain(
            sources, amps, FIXTURE_LEVELS, norm_set
        )
        return {
            "lanes": lanes,
            "rendered": rendered,
            "diagnostics": diagnostics,
            "events": self.events,
            "events_summary": mutations.validate_events(self.plan, self.events),
        }


def fixture_binding(fixture_identity: str) -> Dict[str, Any]:
    return {
        "case_id": "directed:signal-family-0",
        "partition": "development",
        "fixture_identity": fixture_identity,
    }


def fixture_identity() -> str:
    """Deterministic identity over the declared fixture generation inputs."""
    declared = {
        "family": FAMILY_ID,
        "lane_samples": LANE_SAMPLES,
        "sample_rate_hz": SAMPLE_RATE_HZ,
        "vco_1_hz": VCO_1_HZ,
        "vco_2_hz": VCO_2_HZ,
        "amplitude": LANE_AMPLITUDE,
        "shape": VCO_2_SHAPE,
        "harmonics": HARMONICS,
        "levels": list(FIXTURE_LEVELS),
        "amps": list(FIXTURE_AMPS),
        "peaks": [PEAK_ABOVE_ONE, PEAK_BELOW_ONE, PEAK_AT_ONE],
        "operators": sorted(OPERATOR_DEFINITIONS),
    }
    return _digest_bytes(mutations.canonical_bytes(declared))
