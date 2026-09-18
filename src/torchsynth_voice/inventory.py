"""Source-only inventory of the hash-pinned default Voice; never imports Torch."""

from __future__ import annotations

import ast
import json
import math
import re
import struct
from pathlib import Path
from typing import Any

from .contract import UpstreamContract, repository_root, sha256_file

ROOT = repository_root()
ANNOTATIONS_PATH = ROOT / "spec/reference/parameter-annotations-v1.json"
INVENTORY_PATH = ROOT / "spec/reference/parameter-inventory-v1.json"
SCHEMA_PATH = ROOT / "spec/schemas/parameter-inventory-v1.schema.json"
NEBULA_PATH = "torchsynth/nebulae/voice/default.json"


def load_json(path: Path) -> Any:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)


def _validate(value: Any, schema: dict, definitions: dict, path: str = "$") -> None:
    """Validate the deliberately small JSON Schema vocabulary used by v1.

    Unknown keywords fail closed. This is not a general JSON Schema engine.
    Numeric checks additionally reject Python bools, NaN and infinities.
    """
    supported = {
        "$schema",
        "$defs",
        "$ref",
        "title",
        "description",
        "type",
        "const",
        "enum",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "minItems",
        "maxItems",
        "minLength",
        "pattern",
        "minimum",
        "maximum",
        "exclusiveMinimum",
    }
    if set(schema) - supported:
        raise ValueError(f"unsupported schema keywords: {set(schema) - supported}")
    if "$ref" in schema:
        prefix = "#/$defs/"
        if not schema["$ref"].startswith(prefix):
            raise ValueError("only local schema definitions are supported")
        _validate(value, definitions[schema["$ref"][len(prefix) :]], definitions, path)
    kind = schema.get("type")
    valid = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": type(value) is bool,
        "integer": type(value) is int,
        "number": type(value) in (int, float) and math.isfinite(value),
    }
    if kind is not None and not valid.get(kind, False):
        raise ValueError(f"{path}: expected {kind}")
    if "const" in schema and value != schema["const"]:
        raise ValueError(f"{path}: expected {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: invalid choice {value!r}")
    for key, fails in (
        ("minimum", lambda bound: value < bound),
        ("maximum", lambda bound: value > bound),
        ("exclusiveMinimum", lambda bound: value <= bound),
    ):
        if key in schema and fails(schema[key]):
            raise ValueError(f"{path}: violates {key}")
    if isinstance(value, str):
        if len(value.strip()) < schema.get("minLength", 0):
            raise ValueError(f"{path}: empty text")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise ValueError(f"{path}: invalid string")
    if isinstance(value, list):
        if (
            not schema.get("minItems", 0)
            <= len(value)
            <= schema.get("maxItems", math.inf)
        ):
            raise ValueError(f"{path}: invalid item count {len(value)}")
        for index, item in enumerate(value):
            _validate(item, schema.get("items", {}), definitions, f"{path}[{index}]")
    if isinstance(value, dict):
        if set(schema.get("required", [])) - value.keys():
            raise ValueError(f"{path}: missing required fields")
        properties = schema.get("properties", {})
        extra = schema.get("additionalProperties", {})
        for key, item in value.items():
            if key not in properties and extra is False:
                raise ValueError(f"{path}: unknown field {key}")
            _validate(item, properties.get(key, extra), definitions, f"{path}.{key}")


def validate_annotations(document: dict, names: set[str]) -> None:
    schema = load_json(SCHEMA_PATH)
    _validate(document, schema["$defs"]["annotations"], schema["$defs"])
    observed = set(document["parameters"])
    if observed != names:
        raise ValueError(
            f"annotation names: missing {sorted(names - observed)}, extra {sorted(observed - names)}"
        )


def validate_inventory(document: dict) -> None:
    schema = load_json(SCHEMA_PATH)
    _validate(document, schema, schema["$defs"])
    rows = document["parameters"]
    names = [row["name"] for row in rows]
    if len(set(names)) != 78 or names != sorted(names):
        raise ValueError("expected 78 unique canonical names in display order")
    for row in rows:
        name = f"{row['module']}.{row['parameter']}"
        if (
            row["name"] != name
            or row["torch_name"]
            != f"{row['module']}.torchparameters.{row['parameter']}"
        ):
            raise ValueError(f"inconsistent parameter name: {row['name']}")
        if row["minimum"] >= row["maximum"]:
            raise ValueError(f"invalid range: {name}")
    for field in ("forward_position", "randomization_position"):
        if sorted(row[field] for row in rows) != list(range(78)):
            raise ValueError(f"{field} is not a permutation")
    random_rows = sorted(rows, key=lambda row: row["randomization_position"])
    if random_rows != sorted(rows, key=lambda row: row["torch_name"]):
        raise ValueError("randomization order must sort named_parameters")
    contract = UpstreamContract.load()
    if document["source"] != {
        "commit": contract.target_commit,
        "files": contract.files,
        "manifest_sha256": sha256_file(contract.manifest_path),
    }:
        raise ValueError("inventory source provenance differs from pinned contract")


def _expression(node: ast.AST, context: dict[str, Any]) -> Any:
    """Read only the expressions used by the pinned range declarations."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.Name, ast.Attribute)):
        return context[ast.unparse(node)]
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_expression(item, context) for item in node.elts]
    if isinstance(node, ast.Dict):
        return {
            _expression(k, context): _expression(v, context)
            for k, v in zip(node.keys, node.values)
        }
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_expression(node.operand, context)
    if isinstance(node, ast.BinOp):
        left, right = _expression(node.left, context), _expression(node.right, context)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Mult):
            return left * right
    if isinstance(node, ast.Subscript):
        return _expression(node.value, context)[_expression(node.slice, context)]
    if isinstance(node, ast.JoinedStr):
        return "".join(
            str(_expression(part.value, context))
            if isinstance(part, ast.FormattedValue)
            else part.value
            for part in node.values
        )
    if isinstance(node, ast.IfExp):
        # AudioMixer's named-input branch; Voice always supplies names.
        if (
            ast.unparse(node.test) == "names is None"
            and context.get("names") is not None
        ):
            return _expression(node.orelse, context)
    if isinstance(node, ast.Call) and ast.unparse(node.func) == "ModuleParameterRange":
        fields = ["minimum", "maximum", "curve", "symmetric", "name", "description"]
        result = dict(context["range_defaults"])
        result.update(zip(fields, (_expression(arg, context) for arg in node.args)))
        result.update({kw.arg: _expression(kw.value, context) for kw in node.keywords})
        return result
    raise ValueError(f"unsupported pinned source expression: {ast.dump(node)}")


def _assignment(tree: ast.AST, name: str) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and ast.unparse(node.target) == name:
            return node.value
        if isinstance(node, ast.Assign) and any(
            ast.unparse(target) == name for target in node.targets
        ):
            return node.value
    raise ValueError(f"missing source assignment: {name}")


def _method(cls: ast.ClassDef, name: str) -> ast.FunctionDef:
    return next(
        node
        for node in cls.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _defaults(method: ast.FunctionDef) -> dict[str, Any]:
    return {
        arg.arg: ast.literal_eval(value)
        for arg, value in zip(
            method.args.args[-len(method.args.defaults) :], method.args.defaults
        )
    }


def _extract_facts(root: Path) -> list[dict[str, Any]]:
    module_tree = ast.parse((root / "torchsynth/module.py").read_text(encoding="utf-8"))
    synth_tree = ast.parse((root / "torchsynth/synth.py").read_text(encoding="utf-8"))
    parameter_tree = ast.parse(
        (root / "torchsynth/parameter.py").read_text(encoding="utf-8")
    )
    classes = {
        node.name: node
        for tree in (module_tree, synth_tree, parameter_tree)
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }
    util_tree = ast.parse((root / "torchsynth/util.py").read_text(encoding="utf-8"))
    pi_expression = _assignment(util_tree, "torch.pi")
    expected_pi = ast.parse("torch.acos(torch.zeros(1)).item() * 2", mode="eval").body
    if ast.dump(pi_expression) != ast.dump(expected_pi):
        raise ValueError("unsupported upstream torch.pi definition")
    # util is imported before module range declarations. Its float32 acos(0)
    # is converted to a Python scalar, then doubled; math.pi is not the bound.
    upstream_pi = struct.unpack("<f", struct.pack("<f", math.acos(0.0)))[0] * 2
    context = {
        "torch.pi": upstream_pi,
        "range_defaults": _defaults(
            _method(classes["ModuleParameterRange"], "__init__")
        ),
    }
    frozen = _defaults(_method(classes["ModuleParameter"], "__new__"))["frozen"]
    voice = _method(classes["Voice"], "__init__")
    graph = next(
        node.args[0]
        for node in ast.walk(voice)
        if isinstance(node, ast.Call)
        and ast.unparse(node.func) == "self.add_synth_modules"
    )

    def static_ranges(cls: str, field: str = "default_parameter_ranges") -> list[dict]:
        if cls == "SineVCO":
            return static_ranges("VCO")
        local = dict(context)
        if cls == "SquareSawVCO":
            local["VCO.default_parameter_ranges"] = static_ranges("VCO")
        return _expression(_assignment(classes[cls], field), local)

    rows: list[dict[str, Any]] = []
    for element in graph.elts:
        module, cls = ast.literal_eval(element.elts[0]), element.elts[1].id
        kwargs = ast.literal_eval(element.elts[2]) if len(element.elts) == 3 else {}
        if cls in ("ControlRateVCA", "ControlRateUpsample", "Noise", "VCA"):
            continue  # These pinned classes have no parameter ranges.
        if cls in ("LFO", "ModulationMixer", "AudioMixer"):
            method = _method(classes[cls], "__init__")
            template = next(
                node
                for node in ast.walk(method)
                if isinstance(node, ast.Call)
                and ast.unparse(node.func) == "ModuleParameterRange"
            )
            ranges = []
            if cls == "LFO":
                ranges = static_ranges("LFO", "default_ranges")
                for shape in ast.literal_eval(_assignment(method, "self.lfo_types")):
                    ranges.append(_expression(template, {**context, "lfo": shape}))
            else:
                local = {**context, **kwargs}
                if "curves" not in local:
                    local["curves"] = _expression(_assignment(method, "curves"), local)
                for i in range(kwargs["n_input"]):
                    for j in range(kwargs.get("n_output", 1)):
                        local.update(i=i, j=j)
                        local["name"] = _expression(_assignment(method, "name"), local)
                        if cls == "ModulationMixer":
                            local["description"] = _expression(
                                _assignment(method, "description"), local
                            )
                        ranges.append(_expression(template, local))
        else:
            ranges = static_ranges(cls)
        for parameter in ranges:
            name = parameter["name"]
            rows.append(
                {
                    "name": f"{module}.{name}",
                    "module": module,
                    "module_class": cls,
                    "parameter": name,
                    "torch_name": f"{module}.torchparameters.{name}",
                    "minimum": parameter["minimum"],
                    "maximum": parameter["maximum"],
                    "curve": parameter["curve"],
                    "symmetric": parameter["symmetric"],
                    "source_description": parameter["description"],
                    "frozen_capable": True,
                    "default_frozen": frozen,
                    "forward_position": len(rows),
                }
            )
    if len(rows) != 78 or len({row["name"] for row in rows}) != 78:
        raise ValueError("pinned source must declare exactly 78 unique names")
    for position, row in enumerate(sorted(rows, key=lambda row: row["torch_name"])):
        row["randomization_position"] = position
    return rows


def apply_nebula(rows: list[dict], entries: list[dict]) -> None:
    by_name = {row["name"]: row for row in rows}
    expected = {(name, field) for name in by_name for field in ("curve", "symmetric")}
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        if (
            set(entry) != {"name", "value"}
            or not isinstance(entry["name"], list)
            or len(entry["name"]) != 3
        ):
            raise ValueError("invalid nebula entry")
        module, parameter, field = entry["name"]
        if not all(isinstance(part, str) for part in entry["name"]):
            raise ValueError("invalid nebula name")
        name = f"{module}.{parameter}"
        key = (name, field)
        if key not in expected or key in seen:
            raise ValueError(f"unknown or duplicate nebula entry: {key}")
        value = entry["value"]
        if field == "curve" and (
            type(value) not in (int, float) or not math.isfinite(value) or value <= 0
        ):
            raise ValueError(f"invalid nebula curve: {name}")
        if field == "symmetric" and type(value) is not bool:
            raise ValueError(f"invalid nebula symmetry: {name}")
        by_name[name][field] = value
        seen.add(key)
    if seen != expected:
        raise ValueError(f"missing nebula entries: {sorted(expected - seen)}")


def generate_inventory(root: Path, annotations_path: Path = ANNOTATIONS_PATH) -> dict:
    contract = UpstreamContract.load()
    mismatches = contract.verify_source_tree(root)
    if mismatches:
        raise ValueError("pinned source validation failed:\n" + "\n".join(mismatches))
    # No upstream parsing or interpretation occurs until every pinned hash passes.
    rows = _extract_facts(root)
    entries = load_json(root / NEBULA_PATH)
    apply_nebula(rows, entries)
    annotations = load_json(annotations_path)
    validate_annotations(annotations, {row["name"] for row in rows})
    for row in rows:
        row["annotation"] = annotations["parameters"][row["name"]]
    document = {
        "schema_version": 1,
        "profile": contract.data["profile"],
        "source": {
            "commit": contract.target_commit,
            "files": contract.files,
            "manifest_sha256": sha256_file(contract.manifest_path),
        },
        "annotations_sha256": sha256_file(annotations_path),
        "parameter_count": len(rows),
        "nebula_entry_count": len(entries),
        "parameters": sorted(rows, key=lambda row: row["name"]),
    }
    validate_inventory(document)
    return document


def encode_inventory(document: dict) -> bytes:
    return (
        json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")
