"""Timing, interpolation, envelope and modulation fault operators (#32).

The #32 fault-operator family of spec/MUTATIONS.md, registered through the
landed #30 public API (``mutations.register_operator``) from this
family-owned file. The landed framework files (``mutations.py``,
``mutation_runtime.py``, the seam catalog) stay untouched: the family adds
operator definitions, declared analytic signal fixtures, applicators, a
harness over the landed ``InjectionSession`` contract, and detector
bindings to the landed #120 property detectors (``paired_metrics``,
``envelope_estimators``, ``periodic_estimators``) plus the landed endpoint
contract (``trace_capture.check_endpoint_bytes``).

Every operator binds the declared writable seam ``apparatus.producer_call``,
where the family harness produces a deterministic, development-only
analytic signal fixture (binary64 lanes, base64 in a canonical JSON
payload). Applicators are explicit trusted code keyed by operator id;
interpolation applicators are pure trace-level remaps computed from the
unchanged control column, so they alter the expected endpoint/control-
derived traces and never a source parameter. Envelope breakpoint and LFO
rate/depth applicators re-derive the declared lane through the same fixture
builder with the faulted construction, mirroring the landed
ENVELOPE-ESTIMATORS/PERIODIC-ESTIMATORS fault construction; the fixture
header keeps the pristine declared parameters as detection truth and the
event detail records the faulted construction, so the blast radius stays
inside the declared lane payload.

Detection binds only landed validators: time-locked paired rows
(``paired_metrics`` with the analytic exactness rubric, never aligned),
direct-envelope and route qualification rows
(``envelope_estimators.qualification_rows``), periodic rows
(``periodic_estimators.score_periodic`` citing the committed
``sim/qualification/periodic-v1.json`` range cells), and the endpoint
contract refusal. Missing/duplicated samples fail the declared-frame
structural preflight before any scoring, and the landed
``compare_paired`` frame mismatch is the second, independent refusal.
Missing NumPy refuses the periodic rows honestly; a refusal is never
counted as detection.

This module never renders the Voice graph, never touches holdout
partitions, and ratifies no numeric format. Actual-Voice runtime injection
of family operators remains DR-0006 gated and is recorded not-run by the
family publication; the landed ``bridge.*`` operators stay #30-owned
test-only proofs and are never published as family qualification.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import struct
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import envelope_estimators
from . import mutations
from . import mutation_runtime
from . import paired_metrics
from . import periodic_estimators
from . import trace_capture
from .mutations import MutationError

FAMILY_SEAM = "apparatus.producer_call"
PAYLOAD_PATH = "attempt/fixture.json"
FIXTURE_SCHEMA = "torchsynth-mutation-timing-fixture-v1"
PERIODIC_LEDGER_PATH = Path("sim/qualification/periodic-v1.json")

CONTROL_RATE_HZ = 441.0
AUDIO_RATE_HZ = 44100.0
ADSAR_EPSILON = 1e-6

LANE_ADSR = "analytic.adsr"
LANE_LFO = "analytic.lfo"
LANE_ROUTE_SOURCE = "analytic.route_source"
LANE_CONTROL_ENDPOINT = "analytic.control_endpoint"
LANE_CONTROL_UPSAMPLE = "analytic.control_upsample"
ROUTE_DESTINATIONS = envelope_estimators.DESTINATIONS

CONTROL_LANES = (LANE_ADSR, LANE_LFO)
AUDIO_LANES = (LANE_CONTROL_UPSAMPLE,)
ALL_LANES = (LANE_ADSR, LANE_LFO, LANE_CONTROL_ENDPOINT, LANE_CONTROL_UPSAMPLE)

ENVELOPE_COORD_BOUND = 0.02
ENVELOPE_AMPLITUDE_BOUND = 2e-5
ROUTE_GAIN_BOUND = 1e-8
LFO_RATE_LIMIT = 0.02
LFO_DEPTH_LIMIT = 0.01
ENVELOPE_LIMIT_SOURCE = (
    "spec/ENVELOPE-ESTIMATORS.md analytic limits: 0.02 control sample "
    "boundaries, 0.00002 absolute amplitude"
)
ROUTE_LIMIT_SOURCE = (
    "spec/ENVELOPE-ESTIMATORS.md isolated-route limit: 0.00000001 "
    "dimensionless gain"
)
LFO_LIMIT_SOURCE = (
    "spec/PERIODIC-ESTIMATORS.md mandatory mutation limits: 0.02 Hz rate, "
    "0.01 native depth"
)
LFO_TRUTH_SOURCE = "declared directed fixture construction (mutations_timing.CASES)"

UPSAMPLE_CONTROL_COUNT = 1764
UPSAMPLE_OUTPUT_COUNT = 176400


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise MutationError(reason)


CASES: Dict[str, Dict[str, Any]] = {
    "adsr-control": {
        "rate_hz": CONTROL_RATE_HZ,
        "alpha": 1.0,
        "attack": 8.0,
        "decay": 44.0,
        "release": 88.0,
        "sustain": 0.75,
        "note_samples": 100.0,
        "observation_samples": 240,
        "lanes": {
            LANE_ADSR: {"rate_hz": CONTROL_RATE_HZ, "unit": "1"},
        },
    },
    "lfo-control": {
        "rate_hz": CONTROL_RATE_HZ,
        "frequency_hz": 4.37,
        "depth": 2.0,
        "phase_rad": 0.0,
        "sample_count": 1764,
        "unit": "1",
        "reference_hz": 4.37,
        "lanes": {
            LANE_LFO: {"rate_hz": CONTROL_RATE_HZ, "unit": "1"},
        },
    },
    "route-control": {
        "rate_hz": CONTROL_RATE_HZ,
        "sample_count": 32,
        "gains": {
            "vco_1_pitch": 0.5,
            "vco_1_amp": 1.0,
            "vco_2_pitch": 0.25,
            "vco_2_amp": 0.75,
            "noise_amp": 0.1,
        },
        "lanes": {
            LANE_ROUTE_SOURCE: {"rate_hz": CONTROL_RATE_HZ, "unit": "1"},
            **{
                "analytic.route_dest." + name: {
                    "rate_hz": CONTROL_RATE_HZ,
                    "unit": "1",
                }
                for name in ROUTE_DESTINATIONS
            },
        },
    },
    "upsample-audio": {
        "control_rate_hz": CONTROL_RATE_HZ,
        "audio_rate_hz": AUDIO_RATE_HZ,
        "control_count": UPSAMPLE_CONTROL_COUNT,
        "output_count": UPSAMPLE_OUTPUT_COUNT,
        "control_omega": 17.0,
        "control_phase": 0.3,
        "control_offset": 0.5,
        "control_amplitude": 0.25,
        "lanes": {
            LANE_CONTROL_ENDPOINT: {"rate_hz": CONTROL_RATE_HZ, "unit": "1"},
            LANE_CONTROL_UPSAMPLE: {"rate_hz": AUDIO_RATE_HZ, "unit": "1"},
        },
    },
}

DEGENERATE_ENV_CASES: Tuple[Tuple[str, Dict[str, Any], str], ...] = (
    (
        "attack-one-sample",
        {"attack": 1.0, "decay": 4.0, "release": 6.0, "note_samples": 12.0},
        "attack_end",
    ),
    (
        "silent-sustain",
        {"attack": 8.0, "decay": 44.0, "release": 88.0, "sustain": 0.0},
        "sustain_amplitude",
    ),
    (
        "flat-sustain",
        {"attack": 8.0, "decay": 44.0, "release": 88.0, "sustain": 1.0},
        "decay_end",
    ),
    (
        "zero-release",
        {"attack": 8.0, "decay": 44.0, "release": 0.0},
        "release_end",
    ),
)


def _ramp(t: float, start: float, duration: float, alpha: float) -> float:
    if duration == 0:
        return 1.0
    progress = min((max(t - start, 0.0) + ADSAR_EPSILON) / duration + ADSAR_EPSILON, 1.0)
    return progress**alpha


def _ramp_inverse(t: float, start: float, duration: float, alpha: float) -> float:
    if duration == 0:
        return 1.0
    progress = min((max(t - start, 0.0) + ADSAR_EPSILON) / duration + ADSAR_EPSILON, 1.0)
    return (1.0 - progress) ** alpha


def _adsr_samples(params: Dict[str, Any]) -> List[float]:
    alpha = params["alpha"]
    attack = params["attack"]
    decay = params["decay"]
    release = params.get("release", 88.0)
    sustain = params.get("sustain", 0.75)
    note = params["note_samples"]
    count = int(params["observation_samples"])
    return [
        _ramp(t, 0.0, attack, alpha)
        * (sustain + (1.0 - sustain) * _ramp_inverse(t, attack, decay, alpha))
        * _ramp_inverse(t, note, release, alpha)
        for t in range(count)
    ]


def _lfo_samples(params: Dict[str, Any], frequency: Optional[float] = None,
                 depth: Optional[float] = None) -> List[float]:
    rate = params["rate_hz"]
    f0 = params["frequency_hz"] if frequency is None else frequency
    excursion = params["depth"] if depth is None else depth
    phase = params["phase_rad"]
    return [
        excursion * (1.0 - math.cos(2.0 * math.pi * f0 * i / rate + phase)) / 2.0
        for i in range(int(params["sample_count"]))
    ]


def _route_samples(params: Dict[str, Any]) -> Dict[str, List[float]]:
    count = int(params["sample_count"])
    source = [math.sin(2.0 * math.pi * 3.0 * i / count) for i in range(count)]
    lanes = {LANE_ROUTE_SOURCE: list(source)}
    for name, gain in params["gains"].items():
        lanes["analytic.route_dest." + name] = [gain * x for x in source]
    return lanes


def _control_column(params: Dict[str, Any]) -> List[float]:
    count = int(params["control_count"])
    omega = params["control_omega"]
    phase = params["control_phase"]
    offset = params["control_offset"]
    amplitude = params["control_amplitude"]
    return [
        offset + amplitude * math.sin(2.0 * math.pi * omega * i / count + phase)
        for i in range(count)
    ]


def _linear_upsample(control: List[float], output_count: int, denominator: int) -> List[float]:
    last = len(control) - 1
    output: List[float] = []
    for j in range(output_count):
        coordinate = j * last / denominator
        low = int(coordinate)
        if low >= last:
            output.append(control[last])
            continue
        frac = coordinate - low
        output.append(control[low] + frac * (control[low + 1] - control[low]))
    return output


def _encode_lane(samples: List[float]) -> str:
    return base64.b64encode(struct.pack("<%dd" % len(samples), *samples)).decode("ascii")


def _decode_lane(payload: str) -> List[float]:
    raw = base64.b64decode(payload)
    return list(struct.unpack("<%dd" % (len(raw) // 8), raw))


def _lane_digest(samples: List[float]) -> str:
    return hashlib.sha256(struct.pack("<%dd" % len(samples), *samples)).hexdigest()


def build_fixture(case: str, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Deterministic declared analytic fixture for one family case."""
    _require(case in CASES, "unknown mutation-timing case: " + case)
    params = dict(CASES[case])
    if overrides:
        params.update(overrides)
    lanes: Dict[str, Dict[str, Any]] = {}
    if case == "adsr-control":
        lanes[LANE_ADSR] = {"rate_hz": CONTROL_RATE_HZ, "unit": "1",
                            "samples_f64le_b64": _encode_lane(_adsr_samples(params))}
    elif case == "lfo-control":
        lanes[LANE_LFO] = {"rate_hz": CONTROL_RATE_HZ, "unit": "1",
                           "samples_f64le_b64": _encode_lane(_lfo_samples(params))}
    elif case == "route-control":
        for name, samples in _route_samples(params).items():
            lanes[name] = {"rate_hz": CONTROL_RATE_HZ, "unit": "1",
                           "samples_f64le_b64": _encode_lane(samples)}
    else:
        control = _control_column(params)
        lanes[LANE_CONTROL_ENDPOINT] = {
            "rate_hz": CONTROL_RATE_HZ, "unit": "1",
            "samples_f64le_b64": _encode_lane(control),
        }
        upsampled = _linear_upsample(control, int(params["output_count"]),
                                     int(params["output_count"]) - 1)
        lanes[LANE_CONTROL_UPSAMPLE] = {
            "rate_hz": AUDIO_RATE_HZ, "unit": "1",
            "samples_f64le_b64": _encode_lane(upsampled),
        }
    for name, declared in params["lanes"].items():
        lane = lanes[name]
        lane["sample_count"] = len(_decode_lane(lane["samples_f64le_b64"]))
        _require(lane["rate_hz"] == declared["rate_hz"] and lane["unit"] == declared["unit"],
                 "fixture lane violates its declared frame: " + name)
    return {
        "schema": FIXTURE_SCHEMA,
        "schema_version": 1,
        "case": case,
        "parameters": params,
        "lanes": lanes,
    }


def encode_fixture(document: Dict[str, Any]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode()


def decode_fixture(payload: bytes) -> Dict[str, Any]:
    document = json.loads(payload)
    _require(document.get("schema") == FIXTURE_SCHEMA,
             "payload is not a mutation-timing fixture")
    return document


def lane_samples(document: Dict[str, Any], name: str) -> List[float]:
    _require(name in document["lanes"], "fixture has no lane: " + name)
    return _decode_lane(document["lanes"][name]["samples_f64le_b64"])


def write_lane(document: Dict[str, Any], name: str, samples: List[float]) -> None:
    _require(name in document["lanes"], "fixture has no lane: " + name)
    lane = document["lanes"][name]
    lane["samples_f64le_b64"] = _encode_lane(samples)
    lane["sample_count"] = len(samples)


def _replace_params(params: Dict[str, Any], **updates: Any) -> Dict[str, Any]:
    merged = dict(params)
    merged.update(updates)
    return merged


def _applicator_delay(instance: Dict[str, Any], document: Dict[str, Any]):
    lane_name = instance["configuration"]["lane"]
    if lane_name not in document["lanes"]:
        return None, {"reason": "selector did not match", "lane": lane_name}
    shift = instance["magnitude"]["value"]
    samples = lane_samples(document, lane_name)
    if shift > len(samples):
        return None, {"reason": "shift exceeds declared frame", "lane": lane_name}
    delayed = [0.0] * shift + samples[: len(samples) - shift]
    detail = {
        "lane": lane_name,
        "delayed_by_samples": shift,
        "original_lane_sha256": _lane_digest(samples),
        "replacement_lane_sha256": _lane_digest(delayed),
    }
    write_lane(document, lane_name, delayed)
    return document, detail


def _applicator_drop(instance: Dict[str, Any], document: Dict[str, Any]):
    lane_name = instance["configuration"]["lane"]
    if lane_name not in document["lanes"]:
        return None, {"reason": "selector did not match", "lane": lane_name}
    index = instance["configuration"]["index"]
    samples = lane_samples(document, lane_name)
    if index >= len(samples):
        return None, {"reason": "index outside declared frame", "lane": lane_name}
    dropped = samples[:index] + samples[index + 1 :]
    detail = {
        "lane": lane_name,
        "dropped_index": index,
        "declared_count": len(samples),
        "observed_count": len(dropped),
        "original_lane_sha256": _lane_digest(samples),
        "replacement_lane_sha256": _lane_digest(dropped),
    }
    write_lane(document, lane_name, dropped)
    return document, detail


def _applicator_duplicate(instance: Dict[str, Any], document: Dict[str, Any]):
    lane_name = instance["configuration"]["lane"]
    if lane_name not in document["lanes"]:
        return None, {"reason": "selector did not match", "lane": lane_name}
    index = instance["configuration"]["index"]
    samples = lane_samples(document, lane_name)
    if index >= len(samples):
        return None, {"reason": "index outside declared frame", "lane": lane_name}
    duplicated = samples[: index + 1] + [samples[index]] + samples[index + 1 :]
    detail = {
        "lane": lane_name,
        "duplicated_index": index,
        "declared_count": len(samples),
        "observed_count": len(duplicated),
        "original_lane_sha256": _lane_digest(samples),
        "replacement_lane_sha256": _lane_digest(duplicated),
    }
    write_lane(document, lane_name, duplicated)
    return document, detail


def _interp_configuration(instance: Dict[str, Any], document: Dict[str, Any]):
    control_lane = instance["configuration"]["control_lane"]
    upsampled_lane = instance["configuration"]["upsampled_lane"]
    for name in (control_lane, upsampled_lane):
        if name not in document["lanes"]:
            return None, None, None
    control = lane_samples(document, control_lane)
    output_count = len(lane_samples(document, upsampled_lane))
    return control, control_lane, (upsampled_lane, output_count)


def _applicator_zoh(instance: Dict[str, Any], document: Dict[str, Any]):
    resolved = _interp_configuration(instance, document)
    if resolved[0] is None:
        return None, {"reason": "selector did not match"}
    control, control_lane, (upsampled_lane, output_count) = resolved
    last = len(control) - 1
    held = [control[min(int(j * last / (output_count - 1)), last)]
            for j in range(output_count)]
    detail = {
        "lane": upsampled_lane,
        "control_lane": control_lane,
        "control_lane_sha256": _lane_digest(control),
        "original_lane_sha256": _lane_digest(lane_samples(document, upsampled_lane)),
        "replacement_lane_sha256": _lane_digest(held),
        "endpoints": "held from the unchanged control column",
    }
    write_lane(document, upsampled_lane, held)
    return document, detail


def _applicator_off_endpoint(instance: Dict[str, Any], document: Dict[str, Any]):
    resolved = _interp_configuration(instance, document)
    if resolved[0] is None:
        return None, {"reason": "selector did not match"}
    control, control_lane, (upsampled_lane, output_count) = resolved
    shifted = _linear_upsample(control, output_count, output_count)
    detail = {
        "lane": upsampled_lane,
        "control_lane": control_lane,
        "control_lane_sha256": _lane_digest(control),
        "original_lane_sha256": _lane_digest(lane_samples(document, upsampled_lane)),
        "replacement_lane_sha256": _lane_digest(shifted),
        "coordinate": "j*(N-1)/M (dropped-endpoint convention)",
    }
    write_lane(document, upsampled_lane, shifted)
    return document, detail


def _applicator_breakpoint(instance: Dict[str, Any], document: Dict[str, Any]):
    breakpoint = instance["configuration"]["breakpoint"]
    params = document["parameters"]
    shift = instance["magnitude"]["value"]
    faulted = _replace_params(params, **{breakpoint: params[breakpoint] + shift})
    rebuilt = _adsr_samples(faulted)
    lane_samples_original = lane_samples(document, LANE_ADSR)
    detail = {
        "lane": LANE_ADSR,
        "breakpoint": breakpoint,
        "shift_control_samples": shift,
        "faulted_construction": {breakpoint: faulted[breakpoint]},
        "declared_truth": {breakpoint: params[breakpoint]},
        "original_lane_sha256": _lane_digest(lane_samples_original),
        "replacement_lane_sha256": _lane_digest(rebuilt),
    }
    write_lane(document, LANE_ADSR, rebuilt)
    return document, detail


def _applicator_lfo_rate(instance: Dict[str, Any], document: Dict[str, Any]):
    params = document["parameters"]
    shift = instance["magnitude"]["value"]
    faulted_rate = params["frequency_hz"] + shift
    rebuilt = _lfo_samples(params, frequency=faulted_rate)
    detail = {
        "lane": LANE_LFO,
        "faulted_rate_hz": faulted_rate,
        "declared_truth_hz": params["frequency_hz"],
        "original_lane_sha256": _lane_digest(lane_samples(document, LANE_LFO)),
        "replacement_lane_sha256": _lane_digest(rebuilt),
    }
    write_lane(document, LANE_LFO, rebuilt)
    return document, detail


def _applicator_lfo_depth(instance: Dict[str, Any], document: Dict[str, Any]):
    params = document["parameters"]
    shift = instance["magnitude"]["value"]
    faulted_depth = params["depth"] + shift
    rebuilt = _lfo_samples(params, depth=faulted_depth)
    detail = {
        "lane": LANE_LFO,
        "faulted_depth_native": faulted_depth,
        "declared_truth_native": params["depth"],
        "original_lane_sha256": _lane_digest(lane_samples(document, LANE_LFO)),
        "replacement_lane_sha256": _lane_digest(rebuilt),
    }
    write_lane(document, LANE_LFO, rebuilt)
    return document, detail


def _applicator_route(instance: Dict[str, Any], document: Dict[str, Any]) -> Tuple[str, List[float]]:
    destination = instance["configuration"]["destination"]
    lane_name = "analytic.route_dest." + destination
    if lane_name not in document["lanes"]:
        return None, []
    return lane_name, lane_samples(document, lane_name)


def _applicator_route_sign(instance: Dict[str, Any], document: Dict[str, Any]):
    lane_name, samples = _applicator_route(instance, document)
    if lane_name is None:
        return None, {"reason": "selector did not match"}
    flipped = [-x for x in samples]
    detail = {
        "lane": lane_name,
        "fault": "route sign flip",
        "original_lane_sha256": _lane_digest(samples),
        "replacement_lane_sha256": _lane_digest(flipped),
    }
    write_lane(document, lane_name, flipped)
    return document, detail


def _applicator_route_depth(instance: Dict[str, Any], document: Dict[str, Any]):
    lane_name, samples = _applicator_route(instance, document)
    if lane_name is None:
        return None, {"reason": "selector did not match"}
    scale = 1.0 + instance["magnitude"]["value"]
    scaled = [scale * x for x in samples]
    detail = {
        "lane": lane_name,
        "fault": "route depth scale",
        "scale_ratio": scale,
        "original_lane_sha256": _lane_digest(samples),
        "replacement_lane_sha256": _lane_digest(scaled),
    }
    write_lane(document, lane_name, scaled)
    return document, detail


def _applicator_route_swap(instance: Dict[str, Any], document: Dict[str, Any]):
    left = "analytic.route_dest." + instance["configuration"]["left"]
    right = "analytic.route_dest." + instance["configuration"]["right"]
    if left not in document["lanes"] or right not in document["lanes"]:
        return None, {"reason": "selector did not match"}
    left_samples = lane_samples(document, left)
    right_samples = lane_samples(document, right)
    detail = {
        "lanes": [left, right],
        "fault": "route destination swap",
        "original_lane_sha256": [_lane_digest(left_samples), _lane_digest(right_samples)],
        "replacement_lane_sha256": [_lane_digest(right_samples), _lane_digest(left_samples)],
    }
    write_lane(document, left, right_samples)
    write_lane(document, right, left_samples)
    return document, detail


FAMILY_APPLICATORS: Dict[str, Callable[[Dict[str, Any], Dict[str, Any]],
                                       Tuple[Optional[Dict[str, Any]], Dict[str, Any]]]] = {
    "timing.delay_control_sample": _applicator_delay,
    "timing.delay_audio_sample": _applicator_delay,
    "timing.drop_sample": _applicator_drop,
    "timing.duplicate_sample": _applicator_duplicate,
    "interp.zoh_control": _applicator_zoh,
    "interp.off_endpoint": _applicator_off_endpoint,
    "envelope.breakpoint_shift": _applicator_breakpoint,
    "modulation.lfo_rate_shift": _applicator_lfo_rate,
    "modulation.lfo_depth_shift": _applicator_lfo_depth,
    "modulation.route_sign_flip": _applicator_route_sign,
    "modulation.route_depth_shift": _applicator_route_depth,
    "modulation.route_swap": _applicator_route_swap,
}


def _delay_definition(lanes: Tuple[str, ...], summary: str) -> Dict[str, Any]:
    return {
        "version": 1,
        "seam": FAMILY_SEAM,
        "sham": False,
        "magnitude": {"type": "integer", "unit": "sample", "minimum": 1, "maximum": 1},
        "configuration": {"lane": list(lanes)},
        "composes_with": ["interp.zoh_control"] if lanes == AUDIO_LANES else [],
        "summary": summary,
    }


FAMILY_OPERATORS: Dict[str, Dict[str, Any]] = {
    "timing.delay_audio_sample": _delay_definition(
        AUDIO_LANES,
        "delay the declared audio-rate fixture lane by exactly one audio sample "
        "(prepend zero, drop the tail); remains visible in the unaligned "
        "time-locked paired row",
    ),
    "timing.delay_control_sample": _delay_definition(
        CONTROL_LANES,
        "delay the declared control-rate fixture lane by exactly one control "
        "sample (prepend zero, drop the tail); remains visible in the "
        "unaligned time-locked paired row",
    ),
    "timing.drop_sample": {
        "version": 1,
        "seam": FAMILY_SEAM,
        "sham": False,
        "magnitude": {"type": "integer", "unit": "sample", "minimum": 1, "maximum": 1},
        "configuration": {"lane": list(ALL_LANES), "index": {"type": "integer", "minimum": 0}},
        "composes_with": [],
        "summary": "delete one declared sample from the declared lane; the "
        "shrunken frame must fail the structural preflight",
    },
    "timing.duplicate_sample": {
        "version": 1,
        "seam": FAMILY_SEAM,
        "sham": False,
        "magnitude": {"type": "integer", "unit": "sample", "minimum": 1, "maximum": 1},
        "configuration": {"lane": list(ALL_LANES), "index": {"type": "integer", "minimum": 0}},
        "composes_with": [],
        "summary": "duplicate one declared sample in the declared lane; the "
        "grown frame must fail the structural preflight",
    },
    "interp.zoh_control": {
        "version": 1,
        "seam": FAMILY_SEAM,
        "sham": False,
        "magnitude": {"type": "null", "unit": "none"},
        "configuration": {
            "control_lane": [LANE_CONTROL_ENDPOINT],
            "upsampled_lane": [LANE_CONTROL_UPSAMPLE],
        },
        "composes_with": ["timing.delay_audio_sample"],
        "summary": "re-derive the upsampled control-derived lane with zero-order "
        "hold from the unchanged control column (endpoint-preserving); the "
        "source control lane and declared parameters stay byte-identical",
    },
    "interp.off_endpoint": {
        "version": 1,
        "seam": FAMILY_SEAM,
        "sham": False,
        "magnitude": {"type": "null", "unit": "none"},
        "configuration": {
            "control_lane": [LANE_CONTROL_ENDPOINT],
            "upsampled_lane": [LANE_CONTROL_UPSAMPLE],
        },
        "composes_with": [],
        "summary": "re-derive the upsampled lane with the dropped-endpoint "
        "coordinate j*(N-1)/M instead of the landed align_corners=False "
        "convention; must fail the exact endpoint contract",
    },
    "envelope.breakpoint_shift": {
        "version": 1,
        "seam": FAMILY_SEAM,
        "sham": False,
        "magnitude": {"type": "number", "unit": "control_sample",
                      "minimum": -8, "maximum": 8},
        "configuration": {"breakpoint": ["attack", "decay", "release"]},
        "composes_with": ["modulation.route_depth_shift"],
        "summary": "shift one declared ADSR breakpoint by the declared control-"
        "sample magnitude through the fixture builder; the direct envelope "
        "detector must report the shifted coordinate against the declared truth",
    },
    "modulation.lfo_rate_shift": {
        "version": 1,
        "seam": FAMILY_SEAM,
        "sham": False,
        "magnitude": {"type": "number", "unit": "hz", "minimum": -20, "maximum": 20},
        "configuration": {},
        "composes_with": [],
        "summary": "shift the declared LFO rate by the declared hz magnitude "
        "through the fixture builder; the periodic detector must identify the "
        "intended rate defect against the committed qualified range cell",
    },
    "modulation.lfo_depth_shift": {
        "version": 1,
        "seam": FAMILY_SEAM,
        "sham": False,
        "magnitude": {"type": "number", "unit": "native", "minimum": -1,
                      "maximum": 2},
        "configuration": {},
        "composes_with": [],
        "summary": "shift the declared LFO peak-to-peak depth by the declared "
        "native magnitude; the periodic depth detector must identify the defect",
    },
    "modulation.route_sign_flip": {
        "version": 1,
        "seam": FAMILY_SEAM,
        "sham": False,
        "magnitude": {"type": "null", "unit": "none"},
        "configuration": {"destination": list(ROUTE_DESTINATIONS)},
        "composes_with": [],
        "summary": "flip the sign of one declared route destination lane; the "
        "isolated-route detector must identify the sign defect",
    },
    "modulation.route_depth_shift": {
        "version": 1,
        "seam": FAMILY_SEAM,
        "sham": False,
        "magnitude": {"type": "number", "unit": "ratio", "minimum": 0,
                      "maximum": 1000},
        "configuration": {"destination": list(ROUTE_DESTINATIONS)},
        "composes_with": ["envelope.breakpoint_shift"],
        "summary": "scale one declared route destination depth by 1+magnitude; "
        "the isolated-route detector must identify the depth defect",
    },
    "modulation.route_swap": {
        "version": 1,
        "seam": FAMILY_SEAM,
        "sham": False,
        "magnitude": {"type": "null", "unit": "none"},
        "configuration": {"left": list(ROUTE_DESTINATIONS),
                          "right": list(ROUTE_DESTINATIONS)},
        "composes_with": [],
        "summary": "exchange two declared route destination lane payloads; the "
        "isolated-route detector must see each destination carrying the other's "
        "gain while the expected route labels stay unchanged",
    },
}


def register_family() -> None:
    """Explicit trusted-code registration of the #32 family operators.

    Re-registration with an identical definition is a no-op; any drift
    between the registered and declared definition refuses.
    """
    for operator_id, definition in FAMILY_OPERATORS.items():
        registered = mutations.OPERATORS.get(operator_id)
        if registered is not None:
            _require(registered == definition,
                     "operator already registered with a different definition: "
                     + operator_id)
            continue
        mutations.register_operator(operator_id, definition)


class FamilySession(mutation_runtime.InjectionSession):
    """Landed injection session with the #32 family applicators installed."""

    def _apply(self, seam: str, instance: Dict[str, Any], value: Any) -> Any:
        applicator = FAMILY_APPLICATORS.get(instance["operator"])
        if applicator is None:
            return super()._apply(seam, instance, value)
        try:
            replacement, detail = applicator(instance, decode_fixture(value))
        except BaseException as error:
            self._record(
                instance,
                seam,
                "errored",
                {"error": type(error).__name__, "message": str(error)},
            )
            raise
        if replacement is None:
            self._record(instance, seam, "ineffective", dict(detail))
            return value
        payload = encode_fixture(replacement)
        if payload == value:
            self._record(
                instance,
                seam,
                "ineffective",
                dict(detail, reason="replacement equals original"),
            )
            return value
        self._record(instance, seam, "applied", detail)
        return payload


class TimingSignalHarness:
    """Deterministic analytic fixture producer over the landed seam contract."""

    def __init__(self, root: Path, case: str):
        self.root = Path(root)
        self.case = case
        self.catalog = mutations.load_seam_catalog(self.root / mutations.SEAM_CATALOG_PATH)
        self.document = build_fixture(case)
        self.payload = encode_fixture(self.document)

    def binding(self) -> Dict[str, Any]:
        return {
            "case_id": "directed:mutation-timing:" + self.case,
            "partition": "development",
            "fixture_identity": mutation_runtime.sha256(self.payload),
        }

    def _produce(self) -> bytes:
        return self.payload

    def plain_attempt(self) -> mutation_runtime.AttemptResult:
        result = mutation_runtime.AttemptResult()
        result.store[PAYLOAD_PATH] = self.payload
        return result

    def attempt(self, plan: Dict[str, Any]) -> mutation_runtime.AttemptResult:
        result = mutation_runtime.AttemptResult()
        session = FamilySession(plan, {FAMILY_SEAM: self._produce})
        try:
            with session:
                result.store[PAYLOAD_PATH] = session.targets[FAMILY_SEAM]()
        except BaseException as error:
            result.errored = type(error).__name__ + ": " + str(error)
        result.events = session.events
        result.events_summary = session.finish()
        return result

    def empty_plan(self) -> Dict[str, Any]:
        return mutations.make_plan(self.binding(), [], self.catalog)

    def sham_plan(self) -> Dict[str, Any]:
        return mutations.make_plan(
            self.binding(),
            [mutation_runtime.instance("mti-sham", "noop.sham", FAMILY_SEAM)],
            self.catalog,
        )

    def fault(self, instance_id: str, operator_id: str, magnitude: Any = None,
              configuration: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return mutations.make_plan(
            self.binding(),
            [mutation_runtime.instance(instance_id, operator_id, FAMILY_SEAM,
                                       magnitude, configuration)],
            self.catalog,
        )


def _attempt_document(result: mutation_runtime.AttemptResult) -> Dict[str, Any]:
    _require(PAYLOAD_PATH in result.store, "attempt stored no fixture payload")
    return decode_fixture(result.store[PAYLOAD_PATH])


def frame_preflight(document: Dict[str, Any], case: str) -> List[str]:
    """Declared-frame structural preflight, run before any scoring."""
    refusals: List[str] = []
    declared = CASES[case]["lanes"]
    for name, frame in declared.items():
        lane = document["lanes"].get(name)
        if lane is None:
            refusals.append("declared lane missing: " + name)
        elif lane.get("sample_count") != _declared_count(case, name):
            refusals.append(
                "declared frame preflight refusal: "
                + name
                + " declares "
                + str(_declared_count(case, name))
                + " samples at "
                + str(frame["rate_hz"])
                + " Hz, observed "
                + str(lane.get("sample_count"))
            )
        elif lane.get("rate_hz") != frame["rate_hz"]:
            refusals.append(
                "declared frame preflight refusal: " + name + " rate_hz mismatch"
            )
    for name in document["lanes"]:
        if name not in declared:
            refusals.append("undeclared lane present: " + name)
    return refusals


def _declared_count(case: str, lane_name: str) -> int:
    document = build_fixture(case)
    return document["lanes"][lane_name]["sample_count"]


def paired_rows(expected: List[float], observed: List[float], rate_hz: float,
                unit: str, case_id: str, lane: str):
    comparison = paired_metrics.compare_paired(
        expected,
        observed,
        reference_rate_hz=rate_hz,
        candidate_rate_hz=rate_hz,
        unit=unit,
        window_samples=max(1, len(expected)),
    )
    rows, _ = paired_metrics.scorecard_rows(
        comparison,
        case_id=case_id,
        partition="development",
        trace=lane,
        rubric=paired_metrics.analytic_exactness_rubric(),
    )
    return rows, comparison


def verdict_of(rows: List[Dict[str, Any]], property_name: str) -> str:
    for row in rows:
        if row["property"] == property_name:
            return row["verdict"]
    raise MutationError("rows carry no property: " + property_name)


def envelope_truth(params: Dict[str, Any]) -> Dict[str, float]:
    return {
        "attack_end": params["attack"],
        "decay_end": params["attack"] + params["decay"],
        "release_end": params["note_samples"] + params.get("release", 88.0),
        "peak_amplitude": 1.0,
        "sustain_amplitude": params.get("sustain", 0.75),
    }


def envelope_rows(samples: List[float], params: Dict[str, Any], case_id: str):
    result = envelope_estimators.estimate_envelope(
        samples,
        rate_hz=CONTROL_RATE_HZ,
        note_on_seconds=params["note_samples"] / CONTROL_RATE_HZ,
        alpha=params["alpha"],
    )
    truth = envelope_truth(params)
    limits = {
        "attack_end": paired_metrics.Limit(truth["attack_end"], ENVELOPE_COORD_BOUND,
                                           "control_sample", ENVELOPE_LIMIT_SOURCE),
        "decay_end": paired_metrics.Limit(truth["decay_end"], ENVELOPE_COORD_BOUND,
                                          "control_sample", ENVELOPE_LIMIT_SOURCE),
        "release_end": paired_metrics.Limit(truth["release_end"], ENVELOPE_COORD_BOUND,
                                            "control_sample", ENVELOPE_LIMIT_SOURCE),
        "peak_amplitude": paired_metrics.Limit(truth["peak_amplitude"],
                                               ENVELOPE_AMPLITUDE_BOUND, "1",
                                               ENVELOPE_LIMIT_SOURCE),
        "sustain_amplitude": paired_metrics.Limit(truth["sustain_amplitude"],
                                                  ENVELOPE_AMPLITUDE_BOUND, "1",
                                                  ENVELOPE_LIMIT_SOURCE),
    }
    rows, _ = envelope_estimators.qualification_rows(result, case_id=case_id,
                                                     limits=limits)
    return rows, result


def load_periodic_cell(root: Path, measurement: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    ledger = json.loads((Path(root) / PERIODIC_LEDGER_PATH).read_bytes())
    cell = ledger["range_cells"].get(measurement.get("range_key"))
    if cell is None or cell.get("qualified") is not True:
        return None
    if cell.get("algorithm") != measurement.get("algorithm"):
        return None
    return cell


def lfo_rows(samples: List[float], params: Dict[str, Any], case_id: str, root: Path):
    rate_hz = int(params["rate_hz"])
    preparation = periodic_estimators.identity_metadata(
        len(samples), rate_hz, params["unit"], time_origin=0.0
    )
    measurement = periodic_estimators.estimate_periodic(
        samples,
        sample_rate_hz=rate_hz,
        unit=params["unit"],
        family="lfo",
        reference_hz=params["reference_hz"],
        weights=(1, 0, 0, 0, 0),
        time_origin=0.0,
        preparation=preparation,
        fundamental_in_band=True,
    )
    cell = load_periodic_cell(root, measurement)
    rows, _ = periodic_estimators.score_periodic(
        measurement,
        case_id=case_id,
        expected={
            "frequency_hz": params["frequency_hz"],
            "depth_peak_to_peak": params["depth"],
        },
        limits={"frequency_hz": LFO_RATE_LIMIT, "depth_peak_to_peak": LFO_DEPTH_LIMIT},
        qualification=cell,
        truth_source=LFO_TRUTH_SOURCE,
        limit_source=LFO_LIMIT_SOURCE,
    )
    return rows, measurement


def route_rows(document: Dict[str, Any], params: Dict[str, Any], case_id: str):
    source = lane_samples(document, LANE_ROUTE_SOURCE)
    outputs = {
        name: lane_samples(document, "analytic.route_dest." + name)
        for name in ROUTE_DESTINATIONS
    }
    result = envelope_estimators.estimate_routes(source, outputs,
                                                 rate_hz=CONTROL_RATE_HZ)
    limits = {
        "gain." + name: paired_metrics.Limit(gain, ROUTE_GAIN_BOUND, "1",
                                             ROUTE_LIMIT_SOURCE)
        for name, gain in params["gains"].items()
    }
    rows, _ = envelope_estimators.qualification_rows(result, case_id=case_id,
                                                     limits=limits)
    return rows, result


def _row(fault: str, operator: str, seam: str, downstream: str, expected: str,
         control_ok: bool, observed: Optional[str]) -> Dict[str, Any]:
    return {
        "fault": fault,
        "operator": operator,
        "seam": seam,
        "downstream": downstream,
        "expected_refusal": expected,
        "observed_refusal": observed,
        "tripped": bool(control_ok and observed is not None),
        "control_accepted": bool(control_ok),
    }


def _control_verdict_ok(rows: List[Dict[str, Any]], property_name: str) -> bool:
    return verdict_of(rows, property_name) == "PASS"


def fault_matrix(root: Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Run every family fault against its landed detector, with clean controls."""
    root = Path(root)
    register_family()
    entries: List[Dict[str, Any]] = []
    case_records: List[Dict[str, Any]] = []

    def detector_refusal(rows: List[Dict[str, Any]], property_name: str,
                         expected: str, control_ok: bool) -> Optional[str]:
        verdict = verdict_of(rows, property_name)
        if verdict == "FAIL":
            return expected + " (verdict FAIL)"
        if control_ok and verdict == "PASS":
            return None
        return "detector verdict " + verdict + " for " + property_name

    adsr = TimingSignalHarness(root, "adsr-control")
    adsr_params = CASES["adsr-control"]
    clean_env_rows, _ = envelope_rows(lane_samples(adsr.document, LANE_ADSR),
                                      adsr_params, "directed:mutation-timing-control")
    control_env_ok = all(
        _control_verdict_ok(clean_env_rows, name)
        for name in ("attack_end", "decay_end", "release_end", "peak_amplitude",
                     "sustain_amplitude")
    )

    shift_plan = adsr.fault("mti-adsr-attack-plus2", "envelope.breakpoint_shift", 2.0,
                            {"breakpoint": "attack"})
    shift_attempt = adsr.attempt(shift_plan)
    shift_rows, _ = envelope_rows(
        lane_samples(_attempt_document(shift_attempt), LANE_ADSR), adsr_params,
        "directed:mutation-timing:adsr-control")
    entries.append(_row(
        "attack-breakpoint-shift-plus2",
        "envelope.breakpoint_shift",
        FAMILY_SEAM,
        "envelope_estimators.estimate_envelope + qualification_rows (attack_end)",
        "breakpoint coordinate FAIL beyond the 0.02 control-sample bound",
        control_env_ok,
        detector_refusal(shift_rows, "attack_end",
                         "attack_end measured near 10.0 against declared truth 8.0",
                         control_env_ok),
    ))
    case_records.append({"case": "adsr-control", "plan": shift_plan,
                         "attempt": shift_attempt, "rows": shift_rows,
                         "faults": ["attack-breakpoint-shift-plus2"]})

    delay_plan = adsr.fault("mti-adsr-delay1", "timing.delay_control_sample", 1,
                            {"lane": LANE_ADSR})
    delay_attempt = adsr.attempt(delay_plan)
    delay_rows, _ = paired_rows(
        lane_samples(adsr.document, LANE_ADSR),
        lane_samples(_attempt_document(delay_attempt), LANE_ADSR),
        CONTROL_RATE_HZ, "1", "directed:mutation-timing:adsr-control", LANE_ADSR)
    entries.append(_row(
        "one-control-sample-delay",
        "timing.delay_control_sample",
        FAMILY_SEAM,
        "paired_metrics time-locked primary row (analytic exactness rubric)",
        "unaligned primary row FAIL: exact_equal 0 with nonzero max_abs_error",
        _control_verdict_ok(paired_rows(
            lane_samples(adsr.document, LANE_ADSR),
            lane_samples(adsr.document, LANE_ADSR),
            CONTROL_RATE_HZ, "1", "directed:mutation-timing-control", LANE_ADSR)[0],
            "exact_equal"),
        detector_refusal(delay_rows, "exact_equal",
                         "delayed lane diverges from the unpaired expected lane",
                         True),
    ))

    for operator_id, fault_name in (("timing.drop_sample", "missing-sample"),
                                    ("timing.duplicate_sample", "duplicated-sample")):
        control_refusals = frame_preflight(decode_fixture(adsr.payload),
                                           "adsr-control")
        control_frame_rows, _ = paired_rows(
            lane_samples(adsr.document, LANE_ADSR),
            lane_samples(adsr.document, LANE_ADSR),
            CONTROL_RATE_HZ, "1", "directed:mutation-timing-control", LANE_ADSR)
        control_frame_ok = bool(
            control_refusals == []
            and verdict_of(control_frame_rows, "exact_equal") == "PASS")
        frame_plan = adsr.fault("mti-adsr-" + fault_name, operator_id, 1,
                                {"lane": LANE_ADSR, "index": 40})
        frame_attempt = adsr.attempt(frame_plan)
        document = _attempt_document(frame_attempt)
        refusals = frame_preflight(document, "adsr-control")
        landed_rows, comparison = paired_rows(
            lane_samples(adsr.document, LANE_ADSR),
            lane_samples(document, LANE_ADSR),
            CONTROL_RATE_HZ, "1", "directed:mutation-timing:adsr-control", LANE_ADSR)
        landed_refusal = None
        if comparison["pair_status"] != "measured":
            landed_refusal = "compare_paired refuses the frame: " + comparison["pair_status"]
        observed = "; ".join(r for r in refusals if r) + (
            "; " + landed_refusal if landed_refusal else "")
        entries.append(_row(
            fault_name,
            operator_id,
            FAMILY_SEAM,
            "mutations_timing.frame_preflight + compare_paired frame gate",
            "declared frame preflight refusal before scoring + landed "
            "frame_mismatch refusal",
            control_frame_ok,
            observed if (refusals and landed_refusal) else None,
        ))

    lfo = TimingSignalHarness(root, "lfo-control")
    lfo_params = CASES["lfo-control"]
    clean_lfo_rows, clean_measurement = lfo_rows(
        lane_samples(lfo.document, LANE_LFO), lfo_params,
        "directed:mutation-timing-control", root)
    control_lfo_ok = clean_measurement["status"] == "valid" and all(
        _control_verdict_ok(clean_lfo_rows, name)
        for name in ("frequency_hz", "depth_peak_to_peak")
    )

    for operator_id, fault_name, magnitude, expected_note in (
        ("modulation.lfo_rate_shift", "lfo-rate-shift-plus-half-hz", 0.5,
         "frequency_hz measured near 4.87 Hz against declared truth 4.37 Hz "
         "beyond the 0.02 Hz mandatory limit"),
        ("modulation.lfo_depth_shift", "lfo-depth-shift-plus-0.1", 0.1,
         "depth_peak_to_peak measured near 2.1 against declared truth 2.0 "
         "beyond the 0.01 native mandatory limit"),
    ):
        fault_plan = lfo.fault("mti-" + fault_name, operator_id, magnitude)
        fault_attempt = lfo.attempt(fault_plan)
        fault_rows, _ = lfo_rows(
            lane_samples(_attempt_document(fault_attempt), LANE_LFO), lfo_params,
            "directed:mutation-timing:lfo-control", root)
        property_name = "frequency_hz" if "rate" in operator_id else "depth_peak_to_peak"
        numpy_missing = clean_measurement["status"] == "invalid" and "numpy" in \
            clean_measurement["reason"]
        entries.append(_row(
            fault_name,
            operator_id,
            FAMILY_SEAM,
            "periodic_estimators.score_periodic against the committed "
            "sim/qualification/periodic-v1.json range cell",
            expected_note,
            control_lfo_ok,
            detector_refusal(fault_rows, property_name, expected_note, control_lfo_ok)
            if not numpy_missing
            else "periodic detector unavailable: " + clean_measurement["reason"],
        ))
        case_records.append({"case": "lfo-control", "plan": fault_plan,
                             "attempt": fault_attempt, "rows": fault_rows,
                             "faults": [fault_name]})

    route = TimingSignalHarness(root, "route-control")
    route_params = CASES["route-control"]
    clean_route_rows, _ = route_rows(route.document, route_params,
                                     "directed:mutation-timing-control")
    control_route_ok = all(
        _control_verdict_ok(clean_route_rows, "gain." + name)
        for name in ROUTE_DESTINATIONS
    )

    sign_plan = route.fault("mti-route-sign-flip", "modulation.route_sign_flip",
                            configuration={"destination": "vco_1_pitch"})
    sign_attempt = route.attempt(sign_plan)
    sign_rows, _ = route_rows(_attempt_document(sign_attempt), route_params,
                              "directed:mutation-timing:route-control")
    entries.append(_row(
        "route-sign-flip",
        "modulation.route_sign_flip",
        FAMILY_SEAM,
        "envelope_estimators.estimate_routes + qualification_rows (gain.",
        "gain.vco_1_pitch measured near -0.5 against declared truth 0.5 "
        "beyond the 1e-8 gain bound",
        control_route_ok,
        detector_refusal(sign_rows, "gain.vco_1_pitch",
                         "gain.vco_1_pitch sign flipped", control_route_ok),
    ))
    case_records.append({"case": "route-control", "plan": sign_plan,
                         "attempt": sign_attempt, "rows": sign_rows,
                         "faults": ["route-sign-flip", "route-depth-shift-plus25",
                                    "route-destination-swap"]})

    depth_plan = route.fault("mti-route-depth-plus25", "modulation.route_depth_shift",
                             0.25, {"destination": "vco_1_amp"})
    depth_attempt = route.attempt(depth_plan)
    depth_rows, _ = route_rows(_attempt_document(depth_attempt), route_params,
                               "directed:mutation-timing:route-control")
    entries.append(_row(
        "route-depth-shift-plus25",
        "modulation.route_depth_shift",
        FAMILY_SEAM,
        "envelope_estimators.estimate_routes + qualification_rows (gain.",
        "gain.vco_1_amp measured near 1.25 against declared truth 1.0 beyond "
        "the 1e-8 gain bound",
        control_route_ok,
        detector_refusal(depth_rows, "gain.vco_1_amp",
                         "gain.vco_1_amp depth scaled", control_route_ok),
    ))

    swap_plan = route.fault(
        "mti-route-swap", "modulation.route_swap",
        configuration={"left": "vco_2_pitch", "right": "vco_2_amp"})
    swap_attempt = route.attempt(swap_plan)
    swap_rows, _ = route_rows(_attempt_document(swap_attempt), route_params,
                              "directed:mutation-timing:route-control")
    swap_observed = None
    if (verdict_of(swap_rows, "gain.vco_2_pitch") == "FAIL"
            and verdict_of(swap_rows, "gain.vco_2_amp") == "FAIL"):
        swap_observed = ("each swapped destination carries the other's measured "
                         "gain; expected route labels unchanged")
    entries.append(_row(
        "route-destination-swap",
        "modulation.route_swap",
        FAMILY_SEAM,
        "envelope_estimators.estimate_routes + qualification_rows (gain.",
        "gain.vco_2_pitch and gain.vco_2_amp both FAIL against their declared "
        "truths",
        control_route_ok,
        swap_observed,
    ))

    ups = TimingSignalHarness(root, "upsample-audio")
    pristine_upsampled = lane_samples(ups.document, LANE_CONTROL_UPSAMPLE)
    pristine_control = lane_samples(ups.document, LANE_CONTROL_ENDPOINT)
    control_pair_rows, _ = paired_rows(
        pristine_upsampled, pristine_upsampled, AUDIO_RATE_HZ, "1",
        "directed:mutation-timing-control", LANE_CONTROL_UPSAMPLE)
    control_pair_ok = _control_verdict_ok(control_pair_rows, "exact_equal")

    zoh_plan = ups.fault("mti-zoh-control", "interp.zoh_control",
                         configuration={"control_lane": LANE_CONTROL_ENDPOINT,
                                        "upsampled_lane": LANE_CONTROL_UPSAMPLE})
    zoh_attempt = ups.attempt(zoh_plan)
    zoh_document = _attempt_document(zoh_attempt)
    zoh_rows, _ = paired_rows(
        pristine_upsampled, lane_samples(zoh_document, LANE_CONTROL_UPSAMPLE),
        AUDIO_RATE_HZ, "1", "directed:mutation-timing:upsample-audio",
        LANE_CONTROL_UPSAMPLE)
    source_unchanged = (
        _lane_digest(lane_samples(zoh_document, LANE_CONTROL_ENDPOINT))
        == _lane_digest(pristine_control)
        and zoh_document["parameters"] == ups.document["parameters"]
    )
    _require(source_unchanged,
             "interpolation mutation altered the source control lane or declared "
             "parameters")
    entries.append(_row(
        "zero-order-hold-control",
        "interp.zoh_control",
        FAMILY_SEAM,
        "paired_metrics time-locked primary row (analytic exactness rubric); "
        "source lane and parameter-map digest equality",
        "control-derived trace FAIL: exact_equal 0 while the source control "
        "lane and declared parameters stay byte-identical",
        control_pair_ok,
        detector_refusal(zoh_rows, "exact_equal",
                         "ZOH re-derivation diverges from the landed "
                         "align_corners=False interpolation", control_pair_ok)
        if source_unchanged else "source parameters were altered",
    ))
    case_records.append({"case": "upsample-audio", "plan": zoh_plan,
                         "attempt": zoh_attempt, "rows": zoh_rows,
                         "faults": ["zero-order-hold-control",
                                    "dropped-endpoint-coordinate",
                                    "one-audio-sample-delay",
                                    "composed-zoh-then-audio-delay"]})

    endpoint_plan = ups.fault("mti-off-endpoint", "interp.off_endpoint",
                              configuration={"control_lane": LANE_CONTROL_ENDPOINT,
                                             "upsampled_lane": LANE_CONTROL_UPSAMPLE})
    endpoint_attempt = ups.attempt(endpoint_plan)
    endpoint_document = _attempt_document(endpoint_attempt)
    endpoint_refusal = None
    try:
        trace_capture.check_endpoint_bytes(
            struct.pack("<%df" % len(pristine_control), *pristine_control),
            struct.pack(
                "<%df" % len(lane_samples(endpoint_document, LANE_CONTROL_UPSAMPLE)),
                *lane_samples(endpoint_document, LANE_CONTROL_UPSAMPLE)),
            input_count=int(ups.document["parameters"]["control_count"]),
            output_count=int(ups.document["parameters"]["output_count"]),
        )
    except ValueError as error:
        endpoint_refusal = "trace_capture.check_endpoint_bytes: " + str(error)
    endpoint_rows, _ = paired_rows(
        pristine_upsampled, lane_samples(endpoint_document, LANE_CONTROL_UPSAMPLE),
        AUDIO_RATE_HZ, "1", "directed:mutation-timing:upsample-audio",
        LANE_CONTROL_UPSAMPLE)
    entries.append(_row(
        "dropped-endpoint-coordinate",
        "interp.off_endpoint",
        FAMILY_SEAM,
        "trace_capture.check_endpoint_bytes + paired exactness row",
        "endpoint loss refusal: upsampled first/last sample bytes differ from "
        "the control column",
        control_pair_ok,
        endpoint_refusal if (
            endpoint_refusal
            and verdict_of(endpoint_rows, "exact_equal") == "FAIL") else None,
    ))

    audio_delay_plan = ups.fault("mti-audio-delay1", "timing.delay_audio_sample", 1,
                                 {"lane": LANE_CONTROL_UPSAMPLE})
    audio_delay_attempt = ups.attempt(audio_delay_plan)
    audio_delay_rows, audio_comparison = paired_rows(
        pristine_upsampled,
        lane_samples(_attempt_document(audio_delay_attempt), LANE_CONTROL_UPSAMPLE),
        AUDIO_RATE_HZ, "1", "directed:mutation-timing:upsample-audio",
        LANE_CONTROL_UPSAMPLE)
    entries.append(_row(
        "one-audio-sample-delay",
        "timing.delay_audio_sample",
        FAMILY_SEAM,
        "paired_metrics time-locked primary row (analytic exactness rubric)",
        "unaligned high-rate primary row FAIL: exact_equal 0 with measured "
        "max_abs_error "
        + str(audio_comparison["metrics"]["max_abs_error"]["value"]),
        control_pair_ok,
        detector_refusal(audio_delay_rows, "exact_equal",
                         "one-sample audio delay stays visible in the unaligned "
                         "row", control_pair_ok),
    ))

    composed_plan = mutations.make_plan(
        ups.binding(),
        [
            mutation_runtime.instance(
                "mti-zoh", "interp.zoh_control", FAMILY_SEAM,
                configuration={"control_lane": LANE_CONTROL_ENDPOINT,
                               "upsampled_lane": LANE_CONTROL_UPSAMPLE}),
            mutation_runtime.instance("mti-delay", "timing.delay_audio_sample",
                                      FAMILY_SEAM, 1,
                                      {"lane": LANE_CONTROL_UPSAMPLE}),
        ],
        ups.catalog,
    )
    composed_attempt = ups.attempt(composed_plan)
    composed_rows, _ = paired_rows(
        pristine_upsampled,
        lane_samples(_attempt_document(composed_attempt), LANE_CONTROL_UPSAMPLE),
        AUDIO_RATE_HZ, "1", "directed:mutation-timing:upsample-audio",
        LANE_CONTROL_UPSAMPLE)
    composed_observed = None
    if ([event["status"] for event in composed_attempt.events] == ["applied", "applied"]
            and verdict_of(composed_rows, "exact_equal") == "FAIL"):
        composed_observed = ("declared-order events applied then applied; the "
                             "composed fault diverges from the expected trace")
    entries.append(_row(
        "composed-zoh-then-audio-delay",
        "interp.zoh_control + timing.delay_audio_sample",
        FAMILY_SEAM,
        "in-band declared-order events + paired exactness row",
        "declared-order applied events; composed fault FAIL on the unaligned row",
        control_pair_ok,
        composed_observed,
    ))
    return entries, case_records


def sensitivity_floor(root: Path) -> List[Dict[str, Any]]:
    """Measured estimator sensitivity at the declared 0.02-sample resolution."""
    root = Path(root)
    register_family()
    harness = TimingSignalHarness(root, "adsr-control")
    params = CASES["adsr-control"]
    probes = []
    for magnitude, expected_detected in ((0.03, True), (0.005, False)):
        plan = harness.fault("mti-floor-probe-" + str(magnitude),
                             "envelope.breakpoint_shift", magnitude,
                             {"breakpoint": "attack"})
        attempt = harness.attempt(plan)
        rows, _ = envelope_rows(
            lane_samples(_attempt_document(attempt), LANE_ADSR), params,
            "directed:mutation-timing:floor-probe")
        measured = None
        for row in rows:
            if row["property"] == "attack_end":
                measured = row["observed"]
        detected = verdict_of(rows, "attack_end") == "FAIL"
        probes.append({
            "probe": "attack-breakpoint-shift-" + str(magnitude),
            "operator": "envelope.breakpoint_shift",
            "seam": FAMILY_SEAM,
            "magnitude_control_samples": magnitude,
            "declared_truth_control_samples": params["attack"],
            "measured_attack_end": measured,
            "measured_error": None if measured is None
            else abs(measured - params["attack"]),
            "qualified_resolution_control_samples": ENVELOPE_COORD_BOUND,
            "detected": detected,
            "expected_detected": expected_detected,
        })
    return probes


def coverage_cases(root: Path) -> Dict[str, List[Dict[str, Any]]]:
    """Short/degenerate envelope and high-rate control coverage rows."""
    root = Path(root)
    register_family()
    envelope: List[Dict[str, Any]] = []
    base = CASES["adsr-control"]
    for name, overrides, property_name in DEGENERATE_ENV_CASES:
        params = _replace_params(base, **overrides)
        rows, _ = envelope_rows(_adsr_samples(params), params,
                                "directed:mutation-timing:degenerate-" + name)
        envelope.append({
            "coverage_case": name,
            "domain": "short-degenerate-envelope",
            "fixture_parameters": overrides,
            "property": property_name,
            "verdict": verdict_of(rows, property_name),
            "reason": next(row["validity"]["reason"] for row in rows
                           if row["property"] == property_name),
        })
    lfo_params = _replace_params(CASES["lfo-control"], frequency_hz=20.0)
    rows, measurement = lfo_rows(
        _lfo_samples(lfo_params), lfo_params,
        "directed:mutation-timing:high-rate-lfo", root)
    envelope.append({
        "coverage_case": "high-rate-lfo-20hz",
        "domain": "high-rate-control",
        "fixture_parameters": {"frequency_hz": 20.0, "rate_hz": CONTROL_RATE_HZ},
        "property": "frequency_hz",
        "verdict": verdict_of(rows, "frequency_hz"),
        "reason": measurement["status"],
        "range_cell": measurement.get("range_key"),
    })
    ups = TimingSignalHarness(root, "upsample-audio")
    plan = ups.fault("mti-coverage-audio-delay", "timing.delay_audio_sample", 1,
                     {"lane": LANE_CONTROL_UPSAMPLE})
    rows, comparison = paired_rows(
        lane_samples(ups.document, LANE_CONTROL_UPSAMPLE),
        lane_samples(_attempt_document(ups.attempt(plan)), LANE_CONTROL_UPSAMPLE),
        AUDIO_RATE_HZ, "1", "directed:mutation-timing:coverage", LANE_CONTROL_UPSAMPLE)
    envelope.append({
        "coverage_case": "one-audio-sample-delay-visibility",
        "domain": "high-rate-control",
        "fixture_parameters": {"rate_hz": AUDIO_RATE_HZ,
                               "sample_count": len(lane_samples(ups.document,
                                                                LANE_CONTROL_UPSAMPLE))},
        "property": "exact_equal",
        "verdict": verdict_of(rows, "exact_equal"),
        "reason": "measured max_abs_error "
        + str(comparison["metrics"]["max_abs_error"]["value"])
        + " at 44100 Hz in the unaligned primary row",
    })
    return {"envelope_and_high_rate": envelope}


def qualification_controls(root: Path) -> Dict[str, Any]:
    """Ordinary/empty/sham controls and cleanup guarantees for the family."""
    import random

    root = Path(root)
    register_family()
    harness = TimingSignalHarness(root, "adsr-control")
    plain = harness.plain_attempt()
    empty = harness.attempt(harness.empty_plan())
    sham = harness.attempt(harness.sham_plan())
    rng_before = random.getstate()
    harness.attempt(harness.sham_plan())
    plain_again = harness.plain_attempt()
    rng_untouched = random.getstate() == rng_before

    targets = {FAMILY_SEAM: harness._produce}
    crash_plan = mutations.make_plan(
        harness.binding(),
        [mutation_runtime.instance("mti-crash", "crash.producer", FAMILY_SEAM)],
        harness.catalog,
    )
    crashed = False
    session = FamilySession(crash_plan, targets)
    try:
        with session:
            targets[FAMILY_SEAM]()
    except mutation_runtime.ProducerCrash:
        crashed = True
    clean_after_crash = targets[FAMILY_SEAM] == harness._produce

    faulted_case = TimingSignalHarness(root, "lfo-control")
    sibling = TimingSignalHarness(root, "route-control")
    sibling_before = sibling.payload
    depth_plan = faulted_case.fault("mti-depth-shift", "modulation.lfo_depth_shift",
                                    0.1)
    faulted_case.attempt(depth_plan)
    sibling_unchanged = TimingSignalHarness(root, "route-control").payload == sibling_before

    small = harness.fault("mti-small", "envelope.breakpoint_shift", 0.5,
                          {"breakpoint": "attack"})
    other_breakpoint = harness.fault("mti-small", "envelope.breakpoint_shift", 0.5,
                                     {"breakpoint": "decay"})
    return {
        "plain_vs_empty_plan_bytes_identical": plain.store == empty.store,
        "plain_vs_sham_bytes_identical": plain.store == sham.store,
        "plain_rerun_deterministic": plain.store == plain_again.store,
        "empty_plan_events_complete": empty.events_summary.get("complete") is True
        and empty.events_summary.get("observed") == 0,
        "sham_events_marked_and_ineffective": bool(sham.events)
        and all(event["sham"] for event in sham.events)
        and all(event["status"] == "ineffective" for event in sham.events),
        "sham_plan_identity_distinct": harness.sham_plan()["plan_id"]
        != harness.empty_plan()["plan_id"],
        "plan_identity_tracks_magnitude_and_configuration": len(
            {harness.fault("mti-x", "envelope.breakpoint_shift", m,
                           {"breakpoint": "attack"})["plan_id"]
             for m in (0.5, 1.0)}) == 2
        and small["plan_id"] != other_breakpoint["plan_id"],
        "global_rng_untouched_by_attempts": rng_untouched,
        "cleanup_after_producer_crash": crashed and clean_after_crash,
        "no_errored_events_in_controls": plain.errored is None
        and empty.errored is None
        and sham.errored is None,
        "sibling_case_bytes_unchanged_by_faulted_case": sibling_unchanged,
    }
