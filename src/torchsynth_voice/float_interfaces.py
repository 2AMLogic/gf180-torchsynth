"""Declared float-model interface contracts for the default Voice.

Stdlib-only and Python 3.9 compatible; imports no TorchSynth, Torch or NumPy
and implements no DSP. This module owns the boundary types and validation for
one resolved sound: name-keyed normalized binary32 inputs, the selected noise
stream, separately preserved physical observations, declared buffer/time
metadata, and the versioned ordered checkpoint map bound to the landed trace
registry content identity.

Numeric formats for a later fixed model stay open: the checkpoint map carries
``numeric_contract = "unbound:#53"`` and DR-0008 is Proposed, so no width,
scaling, rounding or approximation choice is accepted or implied here. The
candidate model's own calculation dtype and operation policy must be declared
explicitly and separately versioned; an implicit Python binary64 computation
presented as original binary32 is a contract violation, not a default.

Nothing here executes a render, qualifies a runtime, or measures fidelity.
"""

import hashlib
import json
import math
import re
import struct
from fractions import Fraction
from pathlib import Path

from .identity import SoundIdentity
from .trace_registry import (
    CALLS,
    _structure,
    load_registry,
    registry_token,
)

ROOT = Path(__file__).resolve().parents[2]
CHECKPOINTS_PATH = ROOT / "spec/reference/float-checkpoints-v1.json"
SCHEMA_PATH = ROOT / "spec/schemas/float-interface-v1.schema.json"
INVENTORY_PATH = ROOT / "spec/reference/parameter-inventory-v1.json"

SOURCE_COMMIT = "2b0964d4c6c3d472a2a0d54d91b408caaeffca6d"
PROFILE = "torchsynth-1-voice-default"
NUMERIC_CONTRACT = "unbound:#53"
AUDIO_RATE_HZ = 44100
CONTROL_RATE_HZ = 441
AUDIO_SAMPLES = 176400
CONTROL_SAMPLES = 1764
PARAMETER_COUNT = 78
NOISE_SEED = 13
NOISE_STREAMS = 32
DTYPE = "float32"
ENCODING = "f32le"
ENDPOINT_SOURCE_COORDINATE = "j*1763/176399"
CANONICAL_RUNTIME = "release-mkl-compatible-v1"
EXECUTION_STATUSES = ("canonical-batched", "diagnostic-batch-1")
COMPARISON_CLASSES = {
    "input.normalized": "exact-bytes",
    "input.noise": "exact-bytes",
    "physical.parameters": "measured-observation",
    "mixer.gain": "derived-diagnostic",
}
SHARED_INSTANCES = {"control_vca": 2, "control_upsample": 5, "vca": 3}
NORMALIZATION_ENTRIES = (
    ("python-call", "mixer.pre_normalization"),
    ("python-return-local", "mixer.peak"),
    ("derived-from-observed-peak", "mixer.gain"),
)


class InterfaceError(ValueError):
    """Actionable contract failure: names, finiteness, types or bounds."""


def _check(condition, message):
    if not condition:
        raise InterfaceError(message)


def loads(data):
    def unique_pairs(items):
        result = {}
        for key, value in items:
            _check(key not in result, "duplicate JSON key: " + key)
            result[key] = value
        return result

    def bad_constant(value):
        raise InterfaceError("nonfinite JSON number: " + value)

    return json.loads(data, object_pairs_hook=unique_pairs, parse_constant=bad_constant)


def inventory_names():
    """The exact canonical name set from the landed parameter inventory."""

    document = loads(INVENTORY_PATH.read_bytes())
    rows = document["parameters"]
    names = [row["name"] for row in rows]
    _check(len(names) == PARAMETER_COUNT, "inventory must declare 78 names")
    _check(len(set(names)) == PARAMETER_COUNT, "inventory names must be unique")
    return names


def inventory_sha256():
    return hashlib.sha256(INVENTORY_PATH.read_bytes()).hexdigest()


def _plain_int(value):
    return type(value) is int


def sample_time(index, rate_hz):
    """Exact trigger-relative time of one sample index as a rational."""

    _check(_plain_int(index) and index >= 0, "sample index must be a plain int >= 0")
    _check(
        rate_hz in (CONTROL_RATE_HZ, AUDIO_RATE_HZ),
        "sample_time requires the declared control or audio rate",
    )
    return Fraction(index, rate_hz)


def endpoint_source_coordinate(j):
    """Exact upsample source coordinate of audio index j.

    Endpoint-aligned coordinate contract: output index j reads source
    coordinate j*(1764-1)/(176400-1); the endpoints coincide with control
    indices 0 and 1763. This fixes coordinates only, never the floating
    evaluation order of the interpolator.
    """

    _check(_plain_int(j) and 0 <= j < AUDIO_SAMPLES, "audio index out of range")
    return Fraction(j * (CONTROL_SAMPLES - 1), AUDIO_SAMPLES - 1)


class BufferSpec:
    """Declared shape, rate, unit, dtype and time metadata for one buffer."""

    def __init__(self, name, kind, unit, rate_hz, sample_count, shape, time):
        _check(kind in ("scalar", "control", "audio"), "unknown buffer kind")
        _check(type(unit) is str and unit, "unit must be a nonblank string")
        _check(
            type(shape) is list and all(_plain_int(x) for x in shape),
            "shape must be a list of plain integers",
        )
        if kind == "scalar":
            _check(
                rate_hz is None and sample_count is None,
                "a scalar fact has no invented sampling rate or count",
            )
            _check(
                shape in ([], [1]),
                "scalar shape must be [] (keyboard facts) or [1] (clip facts)",
            )
            _check(time is None, "scalar facts carry no time grid")
        else:
            expected_rate = CONTROL_RATE_HZ if kind == "control" else AUDIO_RATE_HZ
            expected_count = CONTROL_SAMPLES if kind == "control" else AUDIO_SAMPLES
            _check(rate_hz == expected_rate, "declared rate mismatch: " + name)
            _check(sample_count == expected_count, "declared count mismatch: " + name)
            _check(shape == [expected_count], "declared shape mismatch: " + name)
            _check(
                type(time) is dict
                and time.get("origin") == "trigger"
                and time.get("index_time") == "i/" + str(expected_rate)
                and time.get("first") == [0, 1]
                and time.get("last") == [expected_count - 1, expected_rate],
                "declared time grid mismatch: " + name,
            )
        self.name = name
        self.kind = kind
        self.unit = unit
        self.rate_hz = rate_hz
        self.sample_count = sample_count
        self.shape = list(shape)
        self.time = time

    @classmethod
    def from_trace(cls, trace):
        _check(trace["dtype"] == DTYPE, "trace dtype must be the declared float32")
        _check(trace["encoding"] == ENCODING, "trace encoding must be f32le")
        return cls(
            trace["name"],
            trace["kind"],
            trace["unit"],
            trace["rate_hz"],
            trace["sample_count"],
            trace["shape"],
            trace["time"],
        )

    def describe(self):
        return {
            "name": self.name,
            "kind": self.kind,
            "unit": self.unit,
            "rate_hz": self.rate_hz,
            "sample_count": self.sample_count,
            "shape": list(self.shape),
            "dtype": DTYPE,
            "encoding": ENCODING,
            "time": self.time,
        }


class Port:
    """A named port binding one buffer to a direction and argument role."""

    def __init__(self, name, direction, spec, role, argument_position=None):
        _check(direction in ("input", "output"), "unknown port direction")
        self.name = name
        self.direction = direction
        self.spec = spec
        self.role = role
        self.argument_position = argument_position

    def describe(self):
        value = self.spec.describe()
        value.update(
            {
                "direction": self.direction,
                "role": self.role,
                "argument_position": self.argument_position,
            }
        )
        return value


class ModuleInterface:
    """One declared module call site with named, role-bound ports."""

    def __init__(self, module, occurrence, inputs, outputs, mechanism):
        self.module = module
        self.occurrence = occurrence
        self.inputs = tuple(inputs)
        self.outputs = tuple(outputs)
        self.mechanism = mechanism

    @property
    def shared_count(self):
        return SHARED_INSTANCES.get(self.module)

    def describe(self):
        return {
            "module": self.module,
            "occurrence": self.occurrence,
            "mechanism": self.mechanism,
            "inputs": [port.describe() for port in self.inputs],
            "outputs": [port.describe() for port in self.outputs],
        }


def module_interfaces(registry=None):
    """Every Voice module call site in pinned evaluation order.

    Shared instances stay distinct occurrences: 2 control VCAs, 5 control
    upsamplers, 3 audio VCAs. The three normalize_if_clipping observations
    sit between the vca#3 output and the mixer return, exactly as in the
    landed registry's trace-array order.
    """

    document = registry if registry is not None else load_registry()
    specs = {
        trace["name"]: BufferSpec.from_trace(trace) for trace in document["traces"]
    }
    interfaces = []
    counts = {}
    for module, inputs, outputs in CALLS:
        counts[module] = counts.get(module, 0) + 1
        ports_in = [
            Port(name, "input", specs[name], "argument-" + str(position), position)
            for position, name in enumerate(inputs)
        ]
        ports_out = [
            Port(name, "output", specs[name], "trace", None) for name in outputs
        ]
        interfaces.append(
            ModuleInterface(module, counts[module], ports_in, ports_out, "forward-hook")
        )
    for mechanism, name in NORMALIZATION_ENTRIES:
        interfaces.append(
            ModuleInterface(
                "normalize_if_clipping",
                1,
                (),
                (Port(name, "output", specs[name], "observation", None),),
                mechanism,
            )
        )
    return interfaces


class CalculationPolicy:
    """The candidate model's explicitly declared calculation policy."""

    def __init__(self, calculation_dtype, operation_policy, version):
        for field, value in (
            ("calculation_dtype", calculation_dtype),
            ("operation_policy", operation_policy),
            ("version", version),
        ):
            _check(type(value) is str and value, field + " must be a nonblank string")
        self.calculation_dtype = calculation_dtype
        self.operation_policy = operation_policy
        self.version = version

    def describe(self):
        return {
            "calculation_dtype": self.calculation_dtype,
            "operation_policy": self.operation_policy,
            "version": self.version,
        }


class ResolvedRequest:
    """One resolved sound; immutable after construction.

    A resolved request preserves the exact 78 canonical names from the landed
    inventory with normalized binary32 values, the selected noise stream with
    seed/slot/hash/count, separately preserved observed physical values, and
    explicit execution provenance. It never mutates after a reset or render,
    never regenerates a nonzero noise slot from a fresh seed, and never
    substitutes batched physical values or analytic fixture conversions.
    """

    def __init__(
        self,
        sound_index,
        normalized,
        noise,
        physical=None,
        source_commit=SOURCE_COMMIT,
        profile=PROFILE,
        runtime=CANONICAL_RUNTIME,
        execution_status=None,
        calculation_policy=None,
    ):
        if isinstance(sound_index, SoundIdentity):
            identity = sound_index
        elif _plain_int(sound_index) and sound_index >= 0:
            identity = SoundIdentity(sound_index)
        else:
            raise InterfaceError(
                "sound_index must be a SoundIdentity or a plain non-negative int"
            )
        self.identity = identity
        self.normalized = self._checked_normalized(normalized)
        self.noise = self._checked_noise(noise)
        self.physical = self._checked_physical(physical)
        _check(source_commit == SOURCE_COMMIT, "unknown source commit")
        _check(profile == PROFILE, "unknown profile")
        _check(runtime == CANONICAL_RUNTIME, "unknown runtime profile")
        _check(
            execution_status in EXECUTION_STATUSES,
            "unknown execution status; declare canonical-batched or diagnostic-batch-1",
        )
        if execution_status == "diagnostic-batch-1":
            _check(
                self.noise["slot"] == self.identity.noise_slot,
                "diagnostic batch-1 must copy the resolved noise slot exactly",
            )
        self.execution_status = execution_status
        if calculation_policy is not None:
            _check(
                isinstance(calculation_policy, CalculationPolicy),
                "calculation_policy must be a CalculationPolicy",
            )
        self.calculation_policy = calculation_policy

    @staticmethod
    def _checked_normalized(normalized):
        _check(
            type(normalized) is dict,
            "normalized parameters must be a name-keyed mapping; positional "
            "interchange is refused",
        )
        expected = inventory_names()
        missing = sorted(set(expected) - set(normalized))
        extra = sorted(set(normalized) - set(expected))
        _check(
            not missing and not extra,
            "normalized name set must equal the 78 canonical inventory names:"
            " missing=" + ",".join(missing[:3]) + " extra=" + ",".join(extra[:3]),
        )
        values = {}
        for name, value in normalized.items():
            _check(type(value) is not bool, "bool is not a parameter value: " + name)
            _check(
                type(value) in (int, float),
                "parameter value must be a real number: " + name,
            )
            _check(math.isfinite(value), "nonfinite normalized value: " + name)
            _check(
                0.0 <= value <= 1.0,
                "normalized value outside the declared [0,1] domain: " + name,
            )
            decoded = struct.unpack("<f", struct.pack("<f", value))[0]
            _check(
                decoded == value,
                "value is not exactly representable as declared binary32: " + name,
            )
            values[name] = decoded
        return values

    @staticmethod
    def _checked_noise(noise):
        _check(type(noise) is dict, "noise must be a declared mapping")
        for field in ("seed", "slot", "sample_count", "sha256", "samples"):
            _check(field in noise, "noise declaration missing field: " + field)
        _check(
            _plain_int(noise["seed"]) and noise["seed"] == NOISE_SEED,
            "noise seed must be the declared seed 13",
        )
        _check(
            _plain_int(noise["slot"]) and 0 <= noise["slot"] < NOISE_STREAMS,
            "noise slot must be an integer in [0, 32)",
        )
        _check(
            _plain_int(noise["sample_count"])
            and noise["sample_count"] == AUDIO_SAMPLES,
            "noise stream must contain exactly 176400 samples",
        )
        _check(
            type(noise["sha256"]) is str
            and re.fullmatch(r"[0-9a-f]{64}", noise["sha256"]) is not None,
            "noise sha256 must be a lowercase hex digest",
        )
        samples = noise["samples"]
        _check(
            type(samples) is bytes and len(samples) == AUDIO_SAMPLES * 4,
            "noise samples must be 176400 little-endian binary32 bytes",
        )
        _check(
            hashlib.sha256(samples).hexdigest() == noise["sha256"],
            "noise digest does not match the supplied samples",
        )
        for offset in range(0, len(samples), 4):
            value = struct.unpack("<f", samples[offset : offset + 4])[0]
            _check(
                math.isfinite(value),
                "noise stream must be finite binary32 at sample " + str(offset // 4),
            )
        declared = dict(noise)
        declared["samples"] = samples
        return declared

    @staticmethod
    def _checked_physical(physical):
        if physical is None:
            return None
        _check(
            type(physical) is dict,
            "observed physical values must be a name-keyed mapping",
        )
        expected = set(inventory_names())
        _check(
            set(physical) == expected,
            "observed physical map must be keyed by the same 78 canonical names",
        )
        for name, value in physical.items():
            _check(type(value) is not bool, "bool is not a physical value: " + name)
            _check(
                type(value) in (int, float) and math.isfinite(value),
                "physical observation must be finite: " + name,
            )
        return dict(physical)


def load_checkpoints(registry=None):
    document = loads(CHECKPOINTS_PATH.read_bytes())
    validate_checkpoints(document, registry)
    return document


def validate_checkpoints(document, registry=None):
    """Schema, fingerprint, coverage and association checks; fail closed."""

    _check(type(document) is dict, "checkpoint document must be an object")
    schema = loads(SCHEMA_PATH.read_bytes())
    _structure(document, schema, schema)
    _check(
        document["schema_sha256"]
        == hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest(),
        "schema identity mismatch",
    )
    fresh_token = registry_token()
    _check(
        document["registry_token"] == fresh_token,
        "stale checkpoint map: registry fingerprint moved to " + fresh_token,
    )
    _check(
        document["inventory_sha256"] == inventory_sha256(),
        "stale checkpoint map: parameter inventory fingerprint mismatch",
    )
    _check(
        document["numeric_contract"] == NUMERIC_CONTRACT,
        "checkpoint map must leave the numeric contract unbound",
    )
    call_counts = {}
    call_sites = []
    for module, inputs, outputs in CALLS:
        call_counts[module] = call_counts.get(module, 0) + 1
        call_sites.append(
            (
                module,
                call_counts[module],
                "forward-hook",
                tuple(inputs),
                tuple(outputs),
            )
        )
    held_mixer = [site for site in call_sites if site[0] == "mixer"]
    graph_sites = [site for site in call_sites if site[0] != "mixer"]
    graph_sites += [
        ("normalize_if_clipping", 1, mechanism, (), (name,))
        for mechanism, name in NORMALIZATION_ENTRIES
    ]
    expected_sequence = graph_sites + held_mixer
    _check(
        len(document["checkpoints"]) == len(expected_sequence),
        "checkpoint count mismatch",
    )
    produced = set()
    produced_order = []
    for position, entry in enumerate(document["checkpoints"]):
        module, occurrence, mechanism, inputs, outputs = expected_sequence[position]
        _check(
            entry["order"] == position + 1,
            "checkpoint order must be the contiguous 1..27 sequence",
        )
        _check(
            entry["module"] == module and entry["mechanism"] == mechanism,
            "checkpoint sequence mismatch at order " + str(entry["order"]),
        )
        _check(
            entry["occurrence"] == occurrence,
            "checkpoint occurrence mismatch at order " + str(entry["order"]),
        )
        _check(
            [binding["source"] for binding in entry["inputs"]] == list(inputs),
            "argument association mismatch at order " + str(entry["order"]),
        )
        _check(
            all(source in produced for source in inputs),
            "argument source must be produced by an earlier checkpoint at order "
            + str(entry["order"]),
        )
        _check(
            len(entry["roles"]) == len(entry["inputs"]),
            "every argument position must carry a declared role at order "
            + str(entry["order"]),
        )
        _check(
            tuple(entry["outputs"]) == outputs,
            "output coverage mismatch at order " + str(entry["order"]),
        )
        produced.update(outputs)
        produced_order.extend(entry["outputs"])
    counts = {}
    for entry in document["checkpoints"]:
        key = (entry["module"], entry["occurrence"])
        counts[key] = counts.get(key, 0) + 1
    for module, expected in SHARED_INSTANCES.items():
        _check(
            counts.get((module, expected)) == 1,
            "missing shared-instance call site: " + module + "#" + str(expected),
        )
    document_registry = registry if registry is not None else load_registry()
    registry_order = [trace["name"] for trace in document_registry["traces"]]
    _check(
        produced_order == registry_order,
        "checkpoint outputs must cover the registry one-to-one in its array order",
    )
    for entry in document["resolved_inputs"]:
        _check(
            entry["comparison"] == COMPARISON_CLASSES[entry["name"]],
            "input comparison class mismatch: " + entry["name"],
        )


def comparison_class(name, registry=None):
    """Exact-byte, measured-observation, declared-metrics or derived class."""

    if name in COMPARISON_CLASSES:
        return COMPARISON_CLASSES[name]
    document = registry if registry is not None else load_registry()
    by_name = {trace["name"]: trace for trace in document["traces"]}
    _check(name in by_name, "unknown checkpoint name: " + name)
    _check(
        by_name[name]["observation"] == "observed",
        "unexpected derived trace without a declared class: " + name,
    )
    return "declared-metrics"
