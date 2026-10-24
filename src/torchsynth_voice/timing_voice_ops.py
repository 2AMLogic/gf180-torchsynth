"""Stdlib-only timing-family voice-runtime realisations (#335).

This module defines *what* each timing-family runtime operator does to the
bytes of one declared batch slot at a declared ``voice.*`` seam, independent
of Torch. The pinned release-era worker
(``env/release-era/mutation_worker_timing.py``) reads the original slot
bytes, calls these pure functions and writes the returned bytes back in
place; the host side (``mutations_timing_runtime``) uses the same constants
to build plans and schedules and to validate worker reports. Keeping the
realisation in stdlib code makes it unit-testable on a Torch-free host and
keeps the replacement defined by the declared operator rather than by an
incidental Torch kernel.

Constraints: Python 3.9 compatible and no package-relative imports, because
the worker loads this file by path (the same way it loads
``trace_registry``/``trace_capture``). It imports nothing from the landed
mutation framework and never renders a Voice.

The realisations are the frozen runtime cells of
``spec/reference/runtime-family-campaign-v1.json`` revision 1 (DR-0012):

* ``timing.delay_*_sample``: prepend one zero sample, drop the tail
  (frame-preserving, so it can be applied in place).
* ``interp.zoh_control``: zero-order hold of the same invocation's control
  input, index ``floor(j*(N-1)/(M-1))`` (endpoint-preserving).
* ``interp.off_endpoint``: linear interpolation at the dropped-endpoint
  coordinate ``j*(N-1)/M`` instead of the landed ``j*(N-1)/(M-1)``
  (``align_corners=True``). It is not a literal ``align_corners`` toggle;
  that operator is a DR-0012 D4 proposed addition and is not realised here.
* ``modulation.route_sign_flip`` / ``route_depth_shift`` / ``route_swap``:
  negate, scale by ``1+magnitude`` or exchange declared mod-matrix outputs.
* ``envelope.breakpoint_shift`` / ``modulation.lfo_rate_shift``: shift the
  physical value of one parameter slot through the pinned non-symmetric
  ``ModuleParameterRange`` mapping; the conversion is returned for the event.

Length-changing operators (``timing.drop_sample``/``duplicate_sample``) are
never realised at a voice seam: they are host-side frame-preflight refusals.
"""

import array
import hashlib
import json
import math
import struct
import sys

FAMILY = "timing"
RUNTIME_SUFFIX = "@voice-v1"
SCHEDULE_KIND = "mutation-voice-schedule-v1"
SCHEDULE_FAMILY = "timing-runtime-v1"
BATCH_SIZE = 32
CONTROL_RATE_HZ = 441
AUDIO_RATE_HZ = 44100
CONTROL_SAMPLES = 1764
AUDIO_SAMPLES = 176400

MANIFEST_PATH = "spec/reference/runtime-family-campaign-v1.json"
MANIFEST_IDENTITY = "runtime-family-campaign-v1"
MANIFEST_REVISION = 1
MANIFEST_FROZEN_SHA256 = (
    "1735e03ed40381895febf019da22a16a8af9f12c848c189b99726ddba55625c1"
)

POST_MODULE = "voice.post_module"
PARAMETER_VALUE = "voice.parameter_value"

ROUTE_TRACES = (
    "mod_matrix.vco_1_pitch",
    "mod_matrix.vco_1_amp",
    "mod_matrix.vco_2_pitch",
    "mod_matrix.vco_2_amp",
    "mod_matrix.noise_amp",
)

DELAY_CONTROL = "timing.delay_control_sample" + RUNTIME_SUFFIX
DELAY_AUDIO = "timing.delay_audio_sample" + RUNTIME_SUFFIX
ZOH = "interp.zoh_control" + RUNTIME_SUFFIX
OFF_ENDPOINT = "interp.off_endpoint" + RUNTIME_SUFFIX
BREAKPOINT = "envelope.breakpoint_shift" + RUNTIME_SUFFIX
LFO_RATE = "modulation.lfo_rate_shift" + RUNTIME_SUFFIX
ROUTE_SIGN = "modulation.route_sign_flip" + RUNTIME_SUFFIX
ROUTE_DEPTH = "modulation.route_depth_shift" + RUNTIME_SUFFIX
ROUTE_SWAP = "modulation.route_swap" + RUNTIME_SUFFIX
SHAM = "timing.sham_slot" + RUNTIME_SUFFIX

# Runtime operator allowlist. ``realises`` names the registered fixture
# operator (the manifest's operator id); ``traces``/``parameters`` are the
# only targets each realisation accepts; ``frame`` is the declared sample
# count of the target trace (registry shape) for in-place realisations.
RUNTIME_OPERATORS = {
    DELAY_CONTROL: {
        "realises": "timing.delay_control_sample",
        "seam": POST_MODULE,
        "traces": ("adsr_1.output",),
        "frame": CONTROL_SAMPLES,
        "magnitude": {"type": "integer", "unit": "sample", "minimum": 1, "maximum": 1},
    },
    DELAY_AUDIO: {
        "realises": "timing.delay_audio_sample",
        "seam": POST_MODULE,
        "traces": ("control_upsample.vco_1_amp",),
        "frame": AUDIO_SAMPLES,
        "magnitude": {"type": "integer", "unit": "sample", "minimum": 1, "maximum": 1},
    },
    ZOH: {
        "realises": "interp.zoh_control",
        "seam": POST_MODULE,
        "traces": ("control_upsample.vco_1_amp",),
        "control_traces": ("mod_matrix.vco_1_amp",),
        "frame": AUDIO_SAMPLES,
        "magnitude": {"type": "null", "unit": "none"},
    },
    OFF_ENDPOINT: {
        "realises": "interp.off_endpoint",
        "seam": POST_MODULE,
        "traces": ("control_upsample.vco_1_amp",),
        "control_traces": ("mod_matrix.vco_1_amp",),
        "frame": AUDIO_SAMPLES,
        "magnitude": {"type": "null", "unit": "none"},
    },
    BREAKPOINT: {
        "realises": "envelope.breakpoint_shift",
        "seam": PARAMETER_VALUE,
        "parameters": ("adsr_1.attack",),
        "physical_unit": "s",
        "magnitude": {"type": "number", "unit": "control_sample", "minimum": -8, "maximum": 8},
    },
    LFO_RATE: {
        "realises": "modulation.lfo_rate_shift",
        "seam": PARAMETER_VALUE,
        "parameters": ("lfo_1.frequency",),
        "physical_unit": "Hz",
        "magnitude": {"type": "number", "unit": "hz", "minimum": -20, "maximum": 20},
    },
    ROUTE_SIGN: {
        "realises": "modulation.route_sign_flip",
        "seam": POST_MODULE,
        "traces": ROUTE_TRACES,
        "frame": CONTROL_SAMPLES,
        "magnitude": {"type": "null", "unit": "none"},
    },
    ROUTE_DEPTH: {
        "realises": "modulation.route_depth_shift",
        "seam": POST_MODULE,
        "traces": ROUTE_TRACES,
        "frame": CONTROL_SAMPLES,
        "magnitude": {"type": "number", "unit": "ratio", "minimum": 0, "maximum": 1000},
    },
    ROUTE_SWAP: {
        "realises": "modulation.route_swap",
        "seam": POST_MODULE,
        "traces": ROUTE_TRACES,
        "frame": CONTROL_SAMPLES,
        "magnitude": {"type": "null", "unit": "none"},
    },
    SHAM: {
        "realises": None,
        "seam": POST_MODULE,
        "traces": ("adsr_1.output",),
        "frame": CONTROL_SAMPLES,
        "magnitude": {"type": "null", "unit": "none"},
    },
}

# The only declared runtime composition (manifest composition entry).
COMPOSITIONS = ((ZOH, DELAY_AUDIO),)


class RealisationRefused(ValueError):
    """A declared realisation precondition does not hold; nothing is written."""


def require(condition, reason):
    if not condition:
        raise RealisationRefused(reason)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def frozen_inventory_digest(manifest):
    """The manifest's frozen digest, recomputed exactly as its validator does."""
    body = {
        "inventory": manifest["inventory"],
        "sensitivity_inventory": manifest["sensitivity_inventory"],
        "supporting": manifest["supporting"],
        "cases": manifest["cases"],
        "detectors": manifest["detectors"],
    }
    return sha256(canonical(body))


def require_manifest_binding(manifest):
    """Fail closed unless the manifest is the frozen revision this adapter realises."""
    if (
        type(manifest) is not dict
        or manifest.get("identity") != MANIFEST_IDENTITY
        or manifest.get("revision") != MANIFEST_REVISION
        or manifest.get("frozen_inventory_digest", {}).get("sha256")
        != MANIFEST_FROZEN_SHA256
        or frozen_inventory_digest(manifest) != MANIFEST_FROZEN_SHA256
    ):
        raise ValueError(
            "campaign manifest is not "
            + MANIFEST_IDENTITY
            + " revision "
            + str(MANIFEST_REVISION)
            + " with the frozen inventory digest this timing adapter realises"
        )


def _require_little_endian():
    require(sys.byteorder == "little", "realisations require little-endian binary32")


def floats(data, frame=None):
    """Decode little-endian binary32 slot bytes; optional exact frame check."""
    _require_little_endian()
    require(type(data) is bytes and len(data) % 4 == 0, "slot bytes are not binary32")
    if frame is not None:
        require(
            len(data) == 4 * frame,
            "slot frame mismatch: declared "
            + str(frame)
            + " samples, observed "
            + str(len(data) // 4),
        )
    values = array.array("f")
    values.frombytes(data)
    require(all(math.isfinite(x) for x in values), "nonfinite slot value")
    return values


def pack(values):
    """Round binary64 values to binary32 (nearest) and return the slot bytes."""
    _require_little_endian()
    out = array.array("f", values)
    require(all(math.isfinite(x) for x in out), "nonfinite replacement value")
    return out.tobytes()


def numerically_equal(left, right):
    """Value equality of two binary32 slots (0.0 == -0.0), frame included."""
    a, b = floats(left), floats(right)
    return len(a) == len(b) and all(x == y for x, y in zip(a, b))


def delay_one(data, frame):
    """Delay by exactly one sample: prepend zero, drop the tail sample."""
    floats(data, frame)
    return b"\x00\x00\x00\x00" + data[:-4]


def zero_order_hold(control, output_frame=AUDIO_SAMPLES):
    """Endpoint-preserving zero-order hold of the same invocation's control slot."""
    values = floats(control, CONTROL_SAMPLES)
    last = len(values) - 1
    denominator = output_frame - 1
    return pack(
        values[min((j * last) // denominator, last)] for j in range(output_frame)
    )


def off_endpoint(control, output_frame=AUDIO_SAMPLES):
    """Linear interpolation at the dropped-endpoint coordinate j*(N-1)/M."""
    values = floats(control, CONTROL_SAMPLES)
    last = len(values) - 1
    out = []
    for j in range(output_frame):
        numerator = j * last
        low = numerator // output_frame
        if low >= last:
            out.append(values[last])
            continue
        frac = (numerator % output_frame) / output_frame
        out.append(values[low] + frac * (values[low + 1] - values[low]))
    return pack(out)


def linear_align_corners(control, output_frame=AUDIO_SAMPLES):
    """Reference of the landed coordinate j*(N-1)/(M-1) (tests only; not a fault)."""
    values = floats(control, CONTROL_SAMPLES)
    last = len(values) - 1
    denominator = output_frame - 1
    out = []
    for j in range(output_frame):
        numerator = j * last
        low = numerator // denominator
        if low >= last:
            out.append(values[last])
            continue
        frac = (numerator % denominator) / denominator
        out.append(values[low] + frac * (values[low + 1] - values[low]))
    return pack(out)


def sign_flip(data, frame=CONTROL_SAMPLES):
    """Negate a route; ``0.0 - x`` keeps zero samples at +0.0."""
    return pack(0.0 - x for x in floats(data, frame))


def scale(data, factor, frame=CONTROL_SAMPLES):
    require(type(factor) in (int, float) and math.isfinite(factor), "scale factor")
    return pack(x * factor for x in floats(data, frame))


def to_binary32(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


def physical_shift(magnitude, unit):
    """Declared magnitude -> physical parameter shift, with the conversion text."""
    require(type(magnitude) in (int, float) and math.isfinite(magnitude), "magnitude")
    if unit == "control_sample":
        return magnitude / CONTROL_RATE_HZ, (
            "shift_s = magnitude_control_samples / "
            + str(CONTROL_RATE_HZ)
            + " Hz control rate"
        )
    if unit == "hz":
        return float(magnitude), "shift_hz = magnitude_hz (identity)"
    raise RealisationRefused("no physical conversion for magnitude unit: " + str(unit))


def from_0to1(normalized, parameter_range):
    """Pinned non-symmetric ModuleParameterRange.from_0to1 in binary64."""
    minimum = parameter_range["minimum"]
    maximum = parameter_range["maximum"]
    curve = parameter_range["curve"]
    if curve != 1.0:
        normalized = 0.0 if normalized == 0.0 else 2.0 ** (math.log2(normalized) / curve)
    return minimum + (maximum - minimum) * normalized


def to_0to1(value, parameter_range):
    """Pinned non-symmetric ModuleParameterRange.to_0to1 in binary64."""
    minimum = parameter_range["minimum"]
    maximum = parameter_range["maximum"]
    curve = parameter_range["curve"]
    normalized = (value - minimum) / (maximum - minimum)
    if curve != 1:
        normalized = normalized ** curve
    return normalized


def shift_parameter(normalized, parameter_range, magnitude, unit):
    """Shift one normalized parameter slot by a declared physical magnitude.

    Only non-symmetric ranges are realised (the two declared targets,
    ``adsr_1.attack`` and ``lfo_1.frequency``, are non-symmetric in the
    pinned inventory); a symmetric range, a target outside the declared
    range or a nonfinite value refuses and nothing is written.
    """
    require(
        type(parameter_range) is dict
        and set(parameter_range) == {"minimum", "maximum", "curve", "symmetric"},
        "parameter range requires minimum/maximum/curve/symmetric",
    )
    require(parameter_range["symmetric"] is False, "symmetric parameter ranges are not realised")
    require(
        all(
            type(parameter_range[key]) in (int, float)
            and math.isfinite(parameter_range[key])
            for key in ("minimum", "maximum", "curve")
        )
        and parameter_range["maximum"] > parameter_range["minimum"]
        and parameter_range["curve"] > 0,
        "parameter range is not a finite increasing range",
    )
    require(
        type(normalized) is float and math.isfinite(normalized) and 0.0 <= normalized <= 1.0,
        "original normalized value outside [0, 1]",
    )
    shift, conversion = physical_shift(magnitude, unit)
    original = from_0to1(normalized, parameter_range)
    target = original + shift
    require(
        parameter_range["minimum"] <= target <= parameter_range["maximum"],
        "shifted physical value outside the declared parameter range",
    )
    replacement = to_binary32(to_0to1(target, parameter_range))
    require(0.0 <= replacement <= 1.0, "replacement normalized value outside [0, 1]")
    return {
        "unit": unit,
        "magnitude": magnitude,
        "conversion": conversion,
        "physical_shift": shift,
        "original_normalized": normalized,
        "original_physical": original,
        "target_physical": target,
        "replacement_normalized": replacement,
        "realized_physical": from_0to1(replacement, parameter_range),
        "range": dict(parameter_range),
        "arithmetic": "binary64 pinned non-symmetric mapping; replacement rounded to binary32",
    }
