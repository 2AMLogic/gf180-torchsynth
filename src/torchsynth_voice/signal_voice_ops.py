"""Stdlib-only signal-family voice-runtime realisations (#336; Part of #300).

This module defines *what* each signal-family runtime operator does to the
bytes of one declared batch slot at a declared ``voice.*`` seam, independent
of Torch. The pinned release-era worker
(``env/release-era/mutation_worker_signal.py``) reads the original slot bytes,
calls these pure functions and writes the returned bytes back in place; the
host side (``mutations_signal_runtime``) uses the same constants to build
plans and schedules and to validate worker reports. Keeping the realisation in
stdlib code makes it unit-testable on a Torch-free host and keeps every
replacement defined by the declared operator rather than by an incidental
Torch kernel.

Constraints: Python 3.9 compatible and no package-relative imports, because
the worker loads this file by path (the same way it loads
``trace_registry``/``trace_capture``). It imports nothing from the landed
mutation framework and never renders a Voice.

The realisations are the frozen signal runtime cells of
``spec/reference/runtime-family-campaign-v1.json`` revision 1 (DR-0012):

* output replacements at ``voice.post_module`` (the injection site the
  manifest names for ``gain.*``, ``clip.*`` and ``osc.mode_substitute``):
  ``gain.db`` multiplies by ``binary32(10**(dB/20))``, ``gain.polarity``
  negates, ``gain.dc_offset`` adds the declared amplitude, ``clip.round_step``
  rounds to the declared amplitude step, ``clip.saturation_ceiling`` clamps to
  the declared +/- ceiling - the same per-sample arithmetic as the #33 fixture
  applicators in ``mutations_signal``;
* ``osc.mode_substitute`` replaces the sine-mode ``vco_1.post_vca`` slot with a
  square-mode waveform at the same frequency, phase and depth, derived from the
  *same VCA invocation's* inputs: ``sign(vco_1.raw) * control_upsample.vco_1_amp``
  (the non-band-limited square that shares the sine's phase origin). This is a
  declared runtime realisation, not the band-limited ``SquareSawVCO`` formula,
  whose ``sin(argument)`` and partials are not recoverable at this seam;
* source-parameter changes at ``voice.parameter_value`` (the injection site
  the manifest names for ``osc.tuning_shift``/``phase_offset``/``shape_scale``):
  the declared native magnitude is converted to the parameter's physical unit
  through the pinned non-symmetric curve-1 ``ModuleParameterRange`` mapping and
  the conversion is returned for the event detail.

Normalization operators (``norm.*``) are never realised: the decision site
has no writable seam (DR-0012 D3, authority question AQ-1), and a post-mixer
replacement cannot express a decision fault.
"""

import array
import hashlib
import json
import math
import struct
import sys

FAMILY = "signal"
RUNTIME_SUFFIX = "@voice-v1"
REALISATION = "signal-runtime-applicators-v1"
SCHEDULE_KIND = "mutation-voice-schedule-v1"
SCHEDULE_FAMILY = "signal-runtime-v1"
BATCH_SIZE = 32
AUDIO_RATE_HZ = 44100
AUDIO_SAMPLES = 176400

MANIFEST_PATH = "spec/reference/runtime-family-campaign-v1.json"
MANIFEST_IDENTITY = "runtime-family-campaign-v1"
MANIFEST_REVISION = 1
MANIFEST_FROZEN_SHA256 = (
    "1735e03ed40381895febf019da22a16a8af9f12c848c189b99726ddba55625c1"
)

POST_MODULE = "voice.post_module"
PARAMETER_VALUE = "voice.parameter_value"
NORMALIZATION_DECISION = "voice.normalization_decision"

# Operators this adapter refuses before rendering, whatever the schedule says.
REFUSED_OPERATOR_PREFIXES = ("norm.", "bridge.")
NORMALIZATION_REFUSAL = (
    "normalization operators are not realised by the signal runtime adapter: "
    "the AudioMixer.output decision site has no writable seam (DR-0012 D3, "
    "authority question AQ-1; producer handoff required) and a post-mixer "
    "replacement cannot establish decision-site execution"
)

GAIN_DB = "gain.db" + RUNTIME_SUFFIX
POLARITY = "gain.polarity" + RUNTIME_SUFFIX
DC_OFFSET = "gain.dc_offset" + RUNTIME_SUFFIX
ROUND_STEP = "clip.round_step" + RUNTIME_SUFFIX
SATURATION = "clip.saturation_ceiling" + RUNTIME_SUFFIX
MODE_SUBSTITUTE = "osc.mode_substitute" + RUNTIME_SUFFIX
SHAPE_SCALE = "osc.shape_scale" + RUNTIME_SUFFIX
TUNING_SHIFT = "osc.tuning_shift" + RUNTIME_SUFFIX
PHASE_OFFSET = "osc.phase_offset" + RUNTIME_SUFFIX
SHAM = "signal.sham" + RUNTIME_SUFFIX

# Inputs of the audio VCA invocation that produces vco_1.post_vca
# (VCA.output(audio_in, control_in)); used by the mode substitute.
MODE_SUBSTITUTE_INPUTS = ("vco_1.raw", "control_upsample.vco_1_amp")

# Runtime operator allowlist. ``realises`` names the registered #33 fixture
# operator (the manifest's operator id); ``traces``/``parameters`` are the only
# targets each realisation accepts; ``frame`` is the declared sample count of
# the target trace (registry shape) for in-place output replacements. The
# magnitude specs equal the fixture registrations.
RUNTIME_OPERATORS = {
    GAIN_DB: {
        "realises": "gain.db",
        "seam": POST_MODULE,
        "traces": ("vco_1.post_vca",),
        "frame": AUDIO_SAMPLES,
        "magnitude": {"type": "number", "unit": "dB", "minimum": -60, "maximum": 60},
    },
    POLARITY: {
        "realises": "gain.polarity",
        "seam": POST_MODULE,
        "traces": ("vco_1.post_vca",),
        "frame": AUDIO_SAMPLES,
        "magnitude": {"type": "null", "unit": "none"},
    },
    DC_OFFSET: {
        "realises": "gain.dc_offset",
        "seam": POST_MODULE,
        "traces": ("vco_1.post_vca",),
        "frame": AUDIO_SAMPLES,
        "magnitude": {"type": "number", "unit": "amplitude", "minimum": -2, "maximum": 2},
    },
    ROUND_STEP: {
        "realises": "clip.round_step",
        "seam": POST_MODULE,
        "traces": ("vco_1.post_vca",),
        "frame": AUDIO_SAMPLES,
        "magnitude": {"type": "number", "unit": "amplitude_step", "minimum": 0, "maximum": 1},
    },
    SATURATION: {
        "realises": "clip.saturation_ceiling",
        "seam": POST_MODULE,
        "traces": ("vco_1.post_vca", "vco_2.post_vca"),
        "frame": AUDIO_SAMPLES,
        "magnitude": {"type": "number", "unit": "amplitude", "minimum": 0, "maximum": 4},
    },
    MODE_SUBSTITUTE: {
        "realises": "osc.mode_substitute",
        "seam": POST_MODULE,
        "traces": ("vco_1.post_vca",),
        "frame": AUDIO_SAMPLES,
        "magnitude": {"type": "null", "unit": "none"},
    },
    SHAPE_SCALE: {
        "realises": "osc.shape_scale",
        "seam": PARAMETER_VALUE,
        "parameters": ("vco_2.shape",),
        "physical_unit": "dimensionless",
        "magnitude": {"type": "number", "unit": "ratio", "minimum": 0, "maximum": 1000},
    },
    TUNING_SHIFT: {
        "realises": "osc.tuning_shift",
        "seam": PARAMETER_VALUE,
        "parameters": ("vco_1.tuning",),
        "physical_unit": "semitones",
        "magnitude": {"type": "number", "unit": "semitone", "minimum": -24, "maximum": 24},
    },
    PHASE_OFFSET: {
        "realises": "osc.phase_offset",
        "seam": PARAMETER_VALUE,
        "parameters": ("vco_1.initial_phase",),
        "physical_unit": "radians",
        "magnitude": {"type": "number", "unit": "radian", "minimum": -7, "maximum": 7},
    },
    SHAM: {
        "realises": None,
        "seam": POST_MODULE,
        "traces": ("vco_1.post_vca",),
        "frame": AUDIO_SAMPLES,
        "magnitude": {"type": "null", "unit": "none"},
    },
}

# The only declared runtime composition (manifest composition entry).
COMPOSITIONS = ((ROUND_STEP, SATURATION),)


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
            + " with the frozen inventory digest this signal adapter realises"
        )


def refused_operator(operator_id):
    """The refusal reason for an operator this adapter never executes, else None."""
    if type(operator_id) is not str:
        return "operator id is not text"
    if operator_id.startswith("norm."):
        return NORMALIZATION_REFUSAL
    if operator_id.startswith("bridge."):
        return "bridge.* operators are #30 test-only proofs, never signal-family operators"
    if operator_id not in RUNTIME_OPERATORS:
        return "operator outside the signal runtime allowlist: " + operator_id
    return None


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


def to_binary32(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


def numerically_equal(left, right):
    """Value equality of two binary32 slots (0.0 == -0.0), frame included."""
    a, b = floats(left), floats(right)
    return len(a) == len(b) and all(x == y for x, y in zip(a, b))


def affected_samples(left, right):
    return sum(1 for x, y in zip(floats(left), floats(right)) if x != y)


def _finite_number(value, name):
    require(
        type(value) in (int, float) and not isinstance(value, bool) and math.isfinite(value),
        name + " must be a finite number",
    )


def gain_db(data, db, frame=AUDIO_SAMPLES):
    _finite_number(db, "gain dB")
    gain = to_binary32(10.0 ** (db / 20.0))
    return pack(x * gain for x in floats(data, frame)), {
        "gain_linear": gain,
        "db": db,
        "conversion": "gain_linear = binary32(10 ** (dB / 20))",
    }


def polarity(data, frame=AUDIO_SAMPLES):
    return pack(-x for x in floats(data, frame)), {"multiplier": -1.0}


def dc_offset(data, offset, frame=AUDIO_SAMPLES):
    _finite_number(offset, "DC offset")
    return pack(x + offset for x in floats(data, frame)), {"offset_amplitude": offset}


def round_step(data, step, frame=AUDIO_SAMPLES):
    _finite_number(step, "amplitude step")
    require(step > 0.0, "amplitude step must be positive")
    return pack(round(x / step) * step for x in floats(data, frame)), {
        "step_amplitude": step,
        "mode": "round-half-even of x/step (the #33 fixture arithmetic), times step",
    }


def saturation(data, ceiling, frame=AUDIO_SAMPLES):
    _finite_number(ceiling, "saturation ceiling")
    require(ceiling >= 0.0, "saturation ceiling must be nonnegative")
    return pack(
        ceiling if x > ceiling else (-ceiling if x < -ceiling else x)
        for x in floats(data, frame)
    ), {"ceiling_amplitude": ceiling}


def mode_substitute(raw, amp, frame=AUDIO_SAMPLES):
    """Square-mode substitute of the sine VCO at the same frequency/phase/depth.

    ``raw`` is the same VCA invocation's ``vco_1.raw`` slot (``cos`` of the
    phase argument) and ``amp`` its ``control_upsample.vco_1_amp`` slot; the
    replacement is ``binary32(sign(raw) * amp)`` with ``sign(0) = +1``.
    """
    raw_values = floats(raw, frame)
    amp_values = floats(amp, frame)
    return pack(
        (1.0 if r >= 0.0 else -1.0) * a for r, a in zip(raw_values, amp_values)
    ), {
        "mode_from": "sine",
        "mode_to": "square",
        "formula": "binary32(sign(vco_1.raw) * control_upsample.vco_1_amp), sign(0)=+1",
        "note": (
            "non-band-limited square sharing the sine's phase origin; the "
            "SquareSawVCO band-limited formula is not reconstructible at this seam"
        ),
    }


def apply_output(operator_id, original, magnitude, frame=AUDIO_SAMPLES, inputs=None):
    """Dispatch one declared output replacement; returns (bytes, detail)."""
    value = None if magnitude is None else magnitude["value"]
    if operator_id == GAIN_DB:
        return gain_db(original, value, frame)
    if operator_id == POLARITY:
        return polarity(original, frame)
    if operator_id == DC_OFFSET:
        return dc_offset(original, value, frame)
    if operator_id == ROUND_STEP:
        return round_step(original, value, frame)
    if operator_id == SATURATION:
        return saturation(original, value, frame)
    if operator_id == MODE_SUBSTITUTE:
        require(inputs is not None and len(inputs) == 2, "mode substitute requires the VCA inputs")
        floats(original, frame)
        return mode_substitute(inputs[0], inputs[1], frame)
    reason = refused_operator(operator_id) or ("operator has no output realisation: " + operator_id)
    raise RealisationRefused(reason)


def _checked_range(parameter_range):
    require(
        type(parameter_range) is dict
        and set(parameter_range) == {"minimum", "maximum", "curve", "symmetric"},
        "parameter range requires minimum/maximum/curve/symmetric",
    )
    require(parameter_range["symmetric"] is False, "symmetric parameter ranges are not realised")
    require(parameter_range["curve"] == 1, "only curve-1 (linear) parameter ranges are realised")
    for key in ("minimum", "maximum"):
        _finite_number(parameter_range[key], "parameter range " + key)
    require(parameter_range["maximum"] > parameter_range["minimum"], "parameter range is not increasing")


def same_binary32_range(left, right):
    """Two ranges describe the same binary32 parameter mapping."""
    return (
        type(left) is dict
        and type(right) is dict
        and set(left) == set(right) == {"minimum", "maximum", "curve", "symmetric"}
        and to_binary32(left["minimum"]) == to_binary32(right["minimum"])
        and to_binary32(left["maximum"]) == to_binary32(right["maximum"])
        and float(left["curve"]) == float(right["curve"])
        and left["symmetric"] is right["symmetric"]
    )


def from_0to1(normalized, parameter_range):
    """Pinned non-symmetric curve-1 ModuleParameterRange.from_0to1 in binary64."""
    minimum = parameter_range["minimum"]
    return minimum + (parameter_range["maximum"] - minimum) * normalized


def to_0to1(value, parameter_range):
    """Pinned non-symmetric curve-1 ModuleParameterRange.to_0to1 in binary64."""
    minimum = parameter_range["minimum"]
    return (value - minimum) / (parameter_range["maximum"] - minimum)


def convert_parameter(operator_id, normalized, parameter_range, magnitude):
    """Apply one declared source-parameter fault to one normalized slot value.

    Returns the conversion record (declared magnitude -> physical target ->
    binary32 normalized replacement). A target outside the declared range, a
    symmetric or curved range, or a nonfinite value refuses and nothing is
    written. The phase offset wraps by one full turn (2*pi) back into the
    declared [-pi, pi] range, which is the same oscillator phase.
    """
    require(operator_id in (SHAPE_SCALE, TUNING_SHIFT, PHASE_OFFSET),
            refused_operator(operator_id) or ("operator has no parameter realisation: " + operator_id))
    _checked_range(parameter_range)
    require(
        type(normalized) is float and math.isfinite(normalized) and 0.0 <= normalized <= 1.0,
        "original normalized value outside [0, 1]",
    )
    spec = RUNTIME_OPERATORS[operator_id]
    require(type(magnitude) is dict and magnitude.get("unit") == spec["magnitude"]["unit"],
            "magnitude unit differs from the operator registration")
    value = magnitude["value"]
    _finite_number(value, "magnitude")
    original = from_0to1(normalized, parameter_range)
    minimum, maximum = parameter_range["minimum"], parameter_range["maximum"]
    record = {
        "unit": magnitude["unit"],
        "magnitude": value,
        "physical_unit": spec["physical_unit"],
        "original_normalized": normalized,
        "original_physical": original,
        "range": dict(parameter_range),
        "wrapped": False,
        "clamped": False,
    }
    if operator_id == TUNING_SHIFT:
        record["conversion"] = "target_semitones = original_semitones + magnitude_semitones (identity unit)"
        target = original + value
    elif operator_id == PHASE_OFFSET:
        record["conversion"] = (
            "target_radians = original_radians + magnitude_radians, wrapped by 2*pi into the declared range"
        )
        target = original + value
        width = maximum - minimum
        require(abs(width - 2.0 * math.pi) <= 1e-6, "phase range is not one full turn")
        if target > maximum:
            target -= 2.0 * math.pi
            record["wrapped"] = True
        elif target < minimum:
            target += 2.0 * math.pi
            record["wrapped"] = True
    else:
        record["conversion"] = "target = clamp(original * magnitude_ratio, minimum, maximum)"
        require(value >= 0.0, "shape ratio must be nonnegative")
        target = original * value
        if target > maximum or target < minimum:
            target = min(maximum, max(minimum, target))
            record["clamped"] = True
    require(minimum <= target <= maximum, "shifted physical value outside the declared parameter range")
    replacement = to_binary32(to_0to1(target, parameter_range))
    require(0.0 <= replacement <= 1.0, "replacement normalized value outside [0, 1]")
    record.update(
        {
            "target_physical": target,
            "replacement_normalized": replacement,
            "realized_physical": from_0to1(replacement, parameter_range),
            "arithmetic": "binary64 pinned non-symmetric curve-1 mapping; replacement rounded to binary32",
        }
    )
    return record
