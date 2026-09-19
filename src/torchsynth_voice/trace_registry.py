"""Versioned passive Voice trace contract; stdlib-only and Python 3.9 compatible.

The release worker imports this file directly, avoiding the >=3.11 root package.
No Torch imports, source execution, artifact mutation or runtime qualification.
"""

import copy
import hashlib
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "spec/reference/trace-registry-v1.json"
SCHEMA_PATH = ROOT / "spec/schemas/trace-registry-v1.schema.json"
ROUTES = ("vco_1_pitch", "vco_1_amp", "vco_2_pitch", "vco_2_amp", "noise_amp")

# Source-audited call/argument identities in actual Voice.output evaluation order.
CALLS = [
    ["keyboard", [], ["keyboard.midi_f0", "keyboard.duration"]],
    ["lfo_1_rate_adsr", ["keyboard.duration"], ["lfo_1_rate_adsr.output"]],
    ["lfo_2_rate_adsr", ["keyboard.duration"], ["lfo_2_rate_adsr.output"]],
    ["lfo_1_amp_adsr", ["keyboard.duration"], ["lfo_1_amp_adsr.output"]],
    ["lfo_2_amp_adsr", ["keyboard.duration"], ["lfo_2_amp_adsr.output"]],
    ["lfo_1", ["lfo_1_rate_adsr.output"], ["lfo_1.raw"]],
    ["control_vca", ["lfo_1.raw", "lfo_1_amp_adsr.output"], ["lfo_1.post_control_vca"]],
    ["lfo_2", ["lfo_2_rate_adsr.output"], ["lfo_2.raw"]],
    ["control_vca", ["lfo_2.raw", "lfo_2_amp_adsr.output"], ["lfo_2.post_control_vca"]],
    ["adsr_1", ["keyboard.duration"], ["adsr_1.output"]],
    ["adsr_2", ["keyboard.duration"], ["adsr_2.output"]],
    [
        "mod_matrix",
        [
            "adsr_1.output",
            "adsr_2.output",
            "lfo_1.post_control_vca",
            "lfo_2.post_control_vca",
        ],
        [
            "mod_matrix.vco_1_pitch",
            "mod_matrix.vco_1_amp",
            "mod_matrix.vco_2_pitch",
            "mod_matrix.vco_2_amp",
            "mod_matrix.noise_amp",
        ],
    ],
    ["control_upsample", ["mod_matrix.vco_1_pitch"], ["control_upsample.vco_1_pitch"]],
    ["vco_1", ["keyboard.midi_f0", "control_upsample.vco_1_pitch"], ["vco_1.raw"]],
    ["control_upsample", ["mod_matrix.vco_1_amp"], ["control_upsample.vco_1_amp"]],
    ["vca", ["vco_1.raw", "control_upsample.vco_1_amp"], ["vco_1.post_vca"]],
    ["control_upsample", ["mod_matrix.vco_2_pitch"], ["control_upsample.vco_2_pitch"]],
    ["vco_2", ["keyboard.midi_f0", "control_upsample.vco_2_pitch"], ["vco_2.raw"]],
    ["control_upsample", ["mod_matrix.vco_2_amp"], ["control_upsample.vco_2_amp"]],
    ["vca", ["vco_2.raw", "control_upsample.vco_2_amp"], ["vco_2.post_vca"]],
    ["noise", [], ["noise.raw"]],
    ["control_upsample", ["mod_matrix.noise_amp"], ["control_upsample.noise_amp"]],
    ["vca", ["noise.raw", "control_upsample.noise_amp"], ["noise.post_vca"]],
    ["mixer", ["vco_1.post_vca", "vco_2.post_vca", "noise.post_vca"], ["mixer.output"]],
]
# Source/contract invariants: kind, clamp boundary, analytical bounds and units.
_SEAMS = {
    "keyboard.midi_f0": [
        "scalar",
        "physical-keyboard-output",
        0,
        127,
        "MIDI-semitones",
    ],
    "keyboard.duration": ["scalar", "physical-keyboard-output", 0.01, 4, "seconds"],
    "lfo_1_rate_adsr.output": [
        "control",
        "post-envelope-ramp-limits",
        0,
        1,
        "linear-amplitude",
    ],
    "lfo_2_rate_adsr.output": [
        "control",
        "post-envelope-ramp-limits",
        0,
        1,
        "linear-amplitude",
    ],
    "lfo_1_amp_adsr.output": [
        "control",
        "post-envelope-ramp-limits",
        0,
        1,
        "linear-amplitude",
    ],
    "lfo_2_amp_adsr.output": [
        "control",
        "post-envelope-ramp-limits",
        0,
        1,
        "linear-amplitude",
    ],
    "lfo_1.raw": [
        "control",
        "post-lfo-frequency-clamp-pre-control-vca",
        0,
        1,
        "linear-amplitude",
    ],
    "lfo_1.post_control_vca": ["control", "post-control-vca", 0, 1, "linear-amplitude"],
    "lfo_2.raw": [
        "control",
        "post-lfo-frequency-clamp-pre-control-vca",
        0,
        1,
        "linear-amplitude",
    ],
    "lfo_2.post_control_vca": ["control", "post-control-vca", 0, 1, "linear-amplitude"],
    "adsr_1.output": ["control", "post-envelope-ramp-limits", 0, 1, "linear-amplitude"],
    "adsr_2.output": ["control", "post-envelope-ramp-limits", 0, 1, "linear-amplitude"],
    "mod_matrix.vco_1_pitch": [
        "control",
        "post-matrix-pre-upsample",
        0,
        4,
        "linear-amplitude",
    ],
    "mod_matrix.vco_1_amp": [
        "control",
        "post-matrix-pre-upsample",
        0,
        4,
        "linear-amplitude",
    ],
    "mod_matrix.vco_2_pitch": [
        "control",
        "post-matrix-pre-upsample",
        0,
        4,
        "linear-amplitude",
    ],
    "mod_matrix.vco_2_amp": [
        "control",
        "post-matrix-pre-upsample",
        0,
        4,
        "linear-amplitude",
    ],
    "mod_matrix.noise_amp": [
        "control",
        "post-matrix-pre-upsample",
        0,
        4,
        "linear-amplitude",
    ],
    "control_upsample.vco_1_pitch": [
        "audio",
        "pre-vco-depth-and-midi-clamp",
        0,
        4,
        "linear-amplitude",
    ],
    "vco_1.raw": ["audio", "post-pitch-clamp-pre-audio-vca", -1, 1, "linear-amplitude"],
    "control_upsample.vco_1_amp": ["audio", "pre-audio-vca", 0, 4, "linear-amplitude"],
    "vco_1.post_vca": [
        "audio",
        "post-audio-vca-pre-mixer-level",
        -4,
        4,
        "linear-amplitude",
    ],
    "control_upsample.vco_2_pitch": [
        "audio",
        "pre-vco-depth-and-midi-clamp",
        0,
        4,
        "linear-amplitude",
    ],
    "vco_2.raw": [
        "audio",
        "post-pitch-clamp-pre-audio-vca",
        -1.25,
        1.25,
        "linear-amplitude",
    ],
    "control_upsample.vco_2_amp": ["audio", "pre-audio-vca", 0, 4, "linear-amplitude"],
    "vco_2.post_vca": [
        "audio",
        "post-audio-vca-pre-mixer-level",
        -5,
        5,
        "linear-amplitude",
    ],
    "noise.raw": ["audio", "seed13-noise-pre-audio-vca", -1, 1, "linear-amplitude"],
    "control_upsample.noise_amp": ["audio", "pre-audio-vca", 0, 4, "linear-amplitude"],
    "noise.post_vca": [
        "audio",
        "post-audio-vca-pre-mixer-level",
        -4,
        4,
        "linear-amplitude",
    ],
    "mixer.pre_normalization": [
        "audio",
        "post-weighted-mix-pre-normalization",
        -13,
        13,
        "linear-amplitude",
    ],
    "mixer.peak": ["scalar", "original-whole-clip-peak", 0, 13, "linear-amplitude"],
    "mixer.gain": [
        "scalar",
        "derived-reciprocal-not-applied",
        0,
        1,
        "linear-amplitude",
    ],
    "mixer.output": [
        "audio",
        "post-conditional-normalization",
        -1,
        1,
        "linear-amplitude",
    ],
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def loads(data):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON key: " + key)
            result[key] = value
        return result

    def constant(value):
        raise ValueError("nonfinite JSON number: " + value)

    return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)


def _structure(value, schema, document, path="$"):
    if "$ref" in schema:
        target = document
        for part in schema["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        return _structure(value, target, document, path)
    if "oneOf" in schema:
        matches = 0
        for option in schema["oneOf"]:
            try:
                _structure(value, option, document, path)
                matches += 1
            except ValueError:
                pass
        require(matches == 1, path + ": expected one allowed form")
        return
    types = {
        "object": type(value) is dict,
        "array": type(value) is list,
        "integer": type(value) is int,
        "number": type(value) in (int, float),
        "string": type(value) is str,
        "boolean": type(value) is bool,
        "null": value is None,
    }
    if "type" in schema:
        allowed = schema["type"]
        allowed = [allowed] if type(allowed) is str else allowed
        require(any(types[t] for t in allowed), path + ": wrong type")
    if "const" in schema:
        require(
            type(value) is type(schema["const"]) and value == schema["const"],
            path + ": wrong constant",
        )
    if "enum" in schema:
        require(value in schema["enum"], path + ": unknown value")
    if type(value) is dict:
        require(set(schema.get("required", [])) <= set(value), path + ": missing field")
        props = schema.get("properties", {})
        for key, child in value.items():
            require(type(key) is str, path + ": non-string key")
            additional = schema.get("additionalProperties", True)
            require(
                key in props or additional is not False,
                path + ": unexpected field " + key,
            )
            sub = props.get(key, additional if type(additional) is dict else {})
            _structure(child, sub, document, path + "." + key)
    elif type(value) is list:
        require(len(value) >= schema.get("minItems", 0), path + ": too few items")
        require(
            len(value) <= schema.get("maxItems", len(value)), path + ": too many items"
        )
        if schema.get("uniqueItems"):
            require(
                len({json.dumps(x, sort_keys=True) for x in value}) == len(value),
                path + ": duplicate items",
            )
        for child in value:
            _structure(child, schema.get("items", {}), document, path + "[]")
    elif type(value) is str:
        require(len(value) >= schema.get("minLength", 0), path + ": empty string")
        require(
            re.fullmatch(schema.get("pattern", r"[\s\S]*"), value) is not None,
            path + ": invalid string",
        )
    elif type(value) in (int, float):
        require(math.isfinite(value), path + ": nonfinite number")
        require(value >= schema.get("minimum", value), path + ": below minimum")
        require(value <= schema.get("maximum", value), path + ": above maximum")


def _graph():
    producers, consumers, counts = {}, {}, {}
    for module, inputs, outputs in CALLS:
        counts[module] = counts.get(module, 0) + 1
        for index, name in enumerate(inputs):
            consumers.setdefault(name, []).append(
                module + "#" + str(counts[module]) + ".arg" + str(index)
            )
        for index, name in enumerate(outputs):
            producers[name] = {
                "module": module,
                "occurrence": counts[module],
                "output_index": index if len(outputs) > 1 else None,
                "mechanism": "forward-hook",
            }
    for name, mechanism in (
        ("mixer.pre_normalization", "python-call"),
        ("mixer.peak", "python-return-local"),
        ("mixer.gain", "derived-from-observed-peak"),
    ):
        producers[name] = {
            "module": "normalize_if_clipping",
            "occurrence": 1,
            "output_index": None,
            "mechanism": mechanism,
        }
    consumers.update(
        {
            "mixer.pre_normalization": ["normalize_if_clipping.signal"],
            "mixer.peak": ["normalize_if_clipping.divisor-and-condition", "mixer.gain"],
            "mixer.gain": ["diagnostics-only"],
            "mixer.output": ["Voice.output.return"],
        }
    )
    names = [n for _, _, outputs in CALLS for n in outputs]
    names[-1:-1] = ["mixer.pre_normalization", "mixer.peak", "mixer.gain"]
    return names, producers, consumers


def validate_registry(document):
    """Validate schema plus pinned graph/rate/boundary/alias invariants."""
    schema = loads(SCHEMA_PATH.read_bytes())
    # Coverage precedes schema cardinality so negative controls retain a precise reason.
    traces = document.get("traces", []) if type(document) is dict else []
    names, producers, consumers = _graph()
    require(
        [t.get("name") for t in traces if type(t) is dict] == names,
        "trace coverage/order mismatch: missing, extra or swapped trace",
    )
    _structure(document, schema, schema)
    require(
        document["schema_sha256"]
        == hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest(),
        "schema identity mismatch",
    )
    require(
        document["source_manifest_sha256"]
        == hashlib.sha256(
            (ROOT / "spec/reference/upstream.json").read_bytes()
        ).hexdigest(),
        "source manifest identity mismatch",
    )
    for trace in traces:
        name = trace["name"]
        kind, boundary, lower, upper, unit = _SEAMS[name]
        require(
            trace["producer"] == producers[name], "call association mismatch: " + name
        )
        require(
            trace["consumers"] == consumers[name],
            "consumer association mismatch: " + name,
        )
        require(trace["boundary"] == boundary, "boundary mismatch: " + name)
        require(
            trace["kind"] == kind and trace["unit"] == unit,
            "trace meaning mismatch: " + name,
        )
        require(
            (trace["range"]["minimum"], trace["range"]["maximum"]) == (lower, upper),
            "analytical range mismatch: " + name,
        )
        rate = {"scalar": None, "control": 441, "audio": 44100}[kind]
        count = {"scalar": None, "control": 1764, "audio": 176400}[kind]
        shape = (
            ([] if name.startswith("keyboard.") else [1])
            if kind == "scalar"
            else [count]
        )
        require(
            trace["rate_hz"] == rate and trace["sample_count"] == count,
            "rate/count mismatch: " + name,
        )
        require(
            trace["shape"] == shape and trace["batch_shape"] == ["B", *shape],
            "shape mismatch: " + name,
        )
        time = (
            None
            if kind == "scalar"
            else {
                "origin": "trigger",
                "first": [0, 1],
                "last": [count - 1, rate],
                "index_time": "i/" + str(rate),
            }
        )
        require(trace["time"] == time, "time grid mismatch: " + name)
        interpolation = None
        if name.startswith("control_upsample."):
            interpolation = {
                "mode": "linear",
                "align_corners": True,
                "input_count": 1764,
                "output_count": 176400,
                "input_coordinate": "i*1763/176399",
                "input_first_time": [0, 1],
                "input_last_time": [1763, 441],
            }
        require(
            trace["interpolation"] == interpolation,
            "upsample endpoint mismatch: " + name,
        )
        require(
            trace["observation"] == ("derived" if name == "mixer.gain" else "observed"),
            "observed/derived mismatch: " + name,
        )
    checkpoints = {"input.normalized", "input.noise", "physical.parameters"}
    scalar = {n: n for n in names + sorted(checkpoints)}
    scalar["audio.final"] = "mixer.output"
    repeat = {
        n: n + (".output" if n.startswith("adsr") else ".raw")
        for n in ("adsr_1", "adsr_2", "lfo_1", "lfo_2", "vco_1", "vco_2", "noise")
    }
    repeat.update(
        {
            "audio": "mixer.output",
            "pre_normalization": "mixer.pre_normalization",
            "normalized": "input.normalized",
            "physical": "physical.parameters",
        }
    )
    for version, expected in (
        ("scalar-capture-v1", scalar),
        ("repeatability-v1", repeat),
    ):
        actual = document["aliases"][version]
        require(
            {k: v["target"] for k, v in actual.items()} == expected,
            "alias mapping mismatch: " + version,
        )
        for entry in actual.values():
            require(
                entry["kind"]
                == ("input-checkpoint" if entry["target"] in checkpoints else "trace"),
                "alias kind mismatch",
            )


def load_registry():
    document = loads(REGISTRY_PATH.read_bytes())
    validate_registry(document)
    return document


def registry_token():
    """Externally computed exact-file identity; schema hash is inside its preimage."""
    raw = REGISTRY_PATH.read_bytes()
    validate_registry(loads(raw))
    return "tr1-" + hashlib.sha256(b"torchsynth-trace-registry-v1\n" + raw).hexdigest()


def requested_names(document, names=None):
    validate_registry(document)
    ordered = [t["name"] for t in document["traces"]]
    if names is None:
        return ordered
    require(
        type(names) in (list, tuple) and all(type(n) is str for n in names),
        "requested traces must be a sequence of names",
    )
    require(len(set(names)) == len(names), "duplicate requested name")
    require(set(names) <= set(ordered), "unknown requested name")
    return [n for n in ordered if n in names]


def validate_capture(document, inventory, batch_size):
    validate_registry(document)
    require(
        type(batch_size) is int and batch_size > 0 and batch_size % 32 == 0,
        "prototype requires supported reproducible batching",
    )
    require(
        [t["name"] for t in inventory] == requested_names(document),
        "capture inventory mismatch",
    )
    for expected, actual in zip(document["traces"], inventory):
        name = expected["name"]
        require(
            actual["shape"] == expected["shape"]
            and actual["batch_shape"]
            == [batch_size if n == "B" else n for n in expected["batch_shape"]],
            "capture shape mismatch: " + name,
        )
        for key in ("dtype", "rate_hz", "boundary"):
            require(
                actual[key] == expected[key], "capture " + key + " mismatch: " + name
            )
        require(
            re.fullmatch(r"[0-9a-f]{64}", actual["sha256"]) is not None,
            "capture digest mismatch: " + name,
        )


class CallTracker:
    """Fail closed on event order and identity, even for numerically equal inputs."""

    def __init__(self):
        self.position, self.values, self.counts = 0, {}, {}

    def observe(self, module, inputs, outputs):
        require(self.position < len(CALLS), "extra invocation: " + module)
        expected_module, names, result_names = CALLS[self.position]
        require(
            module == expected_module,
            "call order mismatch: expected " + expected_module + ", observed " + module,
        )
        require(
            len(inputs) == len(names)
            and all(value is self.values[name] for name, value in zip(names, inputs)),
            "argument association mismatch: " + module,
        )
        require(len(outputs) == len(result_names), "output arity mismatch: " + module)
        for name, value in zip(result_names, outputs):
            self.values[name] = value
        self.counts[module] = self.counts.get(module, 0) + 1
        self.position += 1
        return result_names

    def finish(self):
        require(self.position == len(CALLS), "missing invocations")


def negative_controls(document):
    """Mutate an accepted registry, require each defect to turn its gate red."""
    validate_registry(document)
    controls = {}
    for name in (
        "missing-trace",
        "swapped-call-labels",
        "wrong-rate",
        "wrong-shape",
        "wrong-boundary",
    ):
        bad = copy.deepcopy(document)
        by_name = {t["name"]: t for t in bad["traces"]}
        if name == "missing-trace":
            bad["traces"].pop()
        elif name == "swapped-call-labels":
            left = by_name["lfo_1.post_control_vca"]
            right = by_name["lfo_2.post_control_vca"]
            left["producer"], right["producer"] = right["producer"], left["producer"]
        elif name == "wrong-rate":
            by_name["adsr_1.output"]["rate_hz"] = 44100
        elif name == "wrong-shape":
            by_name["adsr_2.output"]["shape"] = [176400]
        else:
            by_name["control_upsample.vco_1_pitch"]["boundary"] = "post-midi-clamp"
        try:
            validate_registry(bad)
        except ValueError as error:
            controls[name] = {"status": "rejected", "reason": str(error)}
        else:
            raise ValueError("negative control accepted: " + name)
    return controls
