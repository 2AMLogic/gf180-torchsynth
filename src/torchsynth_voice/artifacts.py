"""Portable artifact contracts: stdlib-only, offline, no renderer imports.

The two repository schemas provide structural validation. The public validators
add identity/count/provenance semantics described in spec/ARTIFACT-CONTRACT.md.
ValidationError is raised on the first violation; success returns None.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from functools import lru_cache
from typing import Any, Mapping

from .contract import UpstreamContract, repository_root, sha256_file
from .identity import SoundIdentity

_SCHEMA_FILES = (
    "render-artifact-v1.schema.json",
    "corpus-index-v1.schema.json",
    "favorite-v1.schema.json",
)


class ValidationError(ValueError):
    """The document is malformed, inconsistent, or insufficiently complete."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _json_value(value: Any) -> None:
    """Reject non-JSON Python values and nonfinite numbers, including overflow."""
    if value is None or type(value) in (bool, int):
        return
    if type(value) is float:
        _require(math.isfinite(value), "numbers must be finite")
    elif type(value) is str:
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise ValidationError(
                "strings must contain Unicode scalar values"
            ) from error
    elif type(value) is list:
        for child in value:
            _json_value(child)
    elif type(value) is dict:
        for key, child in value.items():
            _require(type(key) is str, "object keys must be strings")
            _json_value(key)
            _json_value(child)
    else:
        raise ValidationError("only JSON values are accepted")


def loads(text: str | bytes) -> Any:
    """Read JSON without silently losing duplicate keys or accepting NaN."""

    def pairs(entries: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in entries:
            _require(key not in result, "duplicate JSON object key")
            result[key] = value
        return result

    def constant(_: str) -> None:
        raise ValidationError("nonfinite JSON number")

    try:
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError) as error:
        raise ValidationError(str(error)) from error
    _json_value(value)
    return value


def canonical_bytes(value: Any) -> bytes:
    """Encode the typed canonical JSON tree defined in ARTIFACT-CONTRACT.md.

    Numbers are exact rational pairs after JSON binary64 parsing; integral
    floats and integers coincide, negative zero is zero, and bool is distinct.
    This is a versioned identity encoding, not an implementation of JCS.
    """
    _json_value(value)

    def encode(node: Any) -> list[Any]:
        if node is None:
            return ["null"]
        if type(node) is bool:
            return ["bool", node]
        if type(node) in (int, float):
            numerator, denominator = (
                (node, 1) if type(node) is int else node.as_integer_ratio()
            )
            return ["number", str(numerator), str(denominator)]
        if type(node) is str:
            return ["string", node]
        if type(node) is list:
            return ["array", [encode(child) for child in node]]
        return ["object", [[key, encode(node[key])] for key in sorted(node)]]

    return json.dumps(encode(value), ensure_ascii=True, separators=(",", ":")).encode(
        "ascii"
    )


def content_id(inputs: dict[str, Any]) -> str:
    """Hash render inputs only; this encoding helper does not validate a render."""
    _require(type(inputs) is dict, "content identity requires an inputs object")
    return (
        "ra1-"
        + hashlib.sha256(
            b"torchsynth-render-artifact-v1\n" + canonical_bytes(inputs)
        ).hexdigest()
    )


def verify_sha256(data: bytes, expected: str) -> None:
    """Check exact file/metadata bytes, independently of render content identity."""
    _require(
        type(expected) is str and re.fullmatch(r"[a-f0-9]{64}", expected) is not None,
        "expected SHA-256 must be lowercase hexadecimal",
    )
    _require(hashlib.sha256(data).hexdigest() == expected, "file SHA-256 mismatch")


@lru_cache(maxsize=2)
def _schema_document(filename: str) -> dict[str, Any]:
    # Never resolve URLs or document-supplied paths.
    _require(filename in _SCHEMA_FILES, "unsupported schema reference")
    path = repository_root() / "spec/schemas" / filename
    return loads(path.read_text(encoding="utf-8"))


def _structure(
    value: Any, schema: dict[str, Any], filename: str, path: str = "$"
) -> None:
    """The deliberately small Draft 2020-12 subset used by our two schemas."""
    if "$ref" in schema:
        target_file, pointer = schema["$ref"].split("#", 1)
        target_file = target_file or filename
        target = _schema_document(target_file)
        for part in pointer.lstrip("/").split("/"):
            target = target[part]
        _structure(value, target, target_file, path)
        return
    if "oneOf" in schema:
        matches = 0
        for option in schema["oneOf"]:
            try:
                _structure(value, option, filename, path)
                matches += 1
            except ValidationError:
                pass
        _require(matches == 1, f"{path}: must match exactly one allowed form")
        return
    kinds = {
        "object": type(value) is dict,
        "array": type(value) is list,
        "string": type(value) is str,
        "integer": type(value) is int,
        "number": type(value) in (int, float),
        "boolean": type(value) is bool,
        "null": value is None,
    }
    if "type" in schema:
        _require(kinds[schema["type"]], f"{path}: expected {schema['type']}")
    if "const" in schema:
        _require(
            canonical_bytes(value) == canonical_bytes(schema["const"]),
            f"{path}: incorrect constant",
        )
    if "enum" in schema:
        _require(
            any(
                canonical_bytes(value) == canonical_bytes(item)
                for item in schema["enum"]
            ),
            f"{path}: unknown value",
        )
    if type(value) is dict:
        _require(
            set(schema.get("required", [])) <= value.keys(),
            f"{path}: missing required field",
        )
        _require(
            len(value) >= schema.get("minProperties", 0), f"{path}: too few fields"
        )
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for name, child in value.items():
            if "propertyNames" in schema:
                _structure(name, schema["propertyNames"], filename, path + ".<key>")
            if name in properties:
                _structure(child, properties[name], filename, path + "." + name)
            elif additional is False:
                raise ValidationError(f"{path}: unexpected field")
            elif isinstance(additional, dict):
                _structure(child, additional, filename, path + "." + name)
    if type(value) is list:
        _require(len(value) >= schema.get("minItems", 0), f"{path}: too few items")
        if schema.get("uniqueItems"):
            _require(
                len({canonical_bytes(item) for item in value}) == len(value),
                f"{path}: duplicate items",
            )
        for child in value:
            _structure(child, schema.get("items", {}), filename, path + "[]")
    if type(value) is str and "pattern" in schema:
        _require(
            re.fullmatch(schema["pattern"], value) is not None,
            f"{path}: invalid string",
        )
    if type(value) in (int, float):
        if "minimum" in schema:
            _require(value >= schema["minimum"], f"{path}: below minimum")
        if "maximum" in schema:
            _require(value <= schema["maximum"], f"{path}: above maximum")
        if "exclusiveMinimum" in schema:
            _require(
                value > schema["exclusiveMinimum"], f"{path}: below exclusive minimum"
            )


def _validate_structure(value: Any, filename: str) -> None:
    _json_value(value)
    _structure(value, _schema_document(filename), filename)


def validate_document(value: Any, filename: str) -> None:
    """Structural validation against one committed schema document.

    The filename must be in the committed `_SCHEMA_FILES` allowlist. Semantic
    rules (cross-field presence, name-set coverage, identity recomputation)
    belong to the feature validators built on top of this check.
    """
    _require(filename in _SCHEMA_FILES, "unsupported schema reference")
    _validate_structure(value, filename)


def _fixture(value: dict[str, Any]) -> None:
    identity = SoundIdentity(value["sound_index"])
    batch, slot = identity.batch_coordinates(128)
    _require(
        value["upstream_batch_index"] == batch and value["upstream_slot"] == slot,
        "fixture: inconsistent upstream batch coordinates",
    )
    _require(
        value["upstream_name"] == identity.upstream_name,
        "fixture: inconsistent upstream name",
    )
    _require(
        value["is_train"] == identity.is_train,
        "fixture: inconsistent train/test identity",
    )


def _status(value: dict[str, Any], require_complete: bool) -> None:
    complete = value["status"] == "complete"
    _require(complete == (not value["failures"]), "status and failure codes disagree")
    _require(not require_complete or complete, "complete record required")


def _inputs(value: dict[str, Any]) -> None:
    contract = UpstreamContract.load()
    _require(
        value["profile"]["contract_sha256"]
        == sha256_file(repository_root() / "spec/VOICE-CONTRACT.md"),
        "profile: contract hash does not match the pinned profile document",
    )
    source = value["source"]
    _require(
        source["manifest_sha256"] == sha256_file(contract.manifest_path),
        "source: upstream manifest hash does not match the pinned contract",
    )
    _require(
        source["files"] == contract.files,
        "source: file hashes do not match the pinned source",
    )
    _fixture(value["fixture"])
    execution = value["execution"]
    if execution["mode"] == "resolved-scalar":
        _require(
            execution["batch_size"] == 1 and not execution["reproducible"],
            "resolved scalar execution requires batch_size=1 and reproducible=false",
        )
    else:
        _require(
            execution["batch_size"] % 32 == 0 and execution["reproducible"],
            "canonical batch execution requires a reproducible multiple of 32",
        )
        _require(
            value["runtime"]["device"] == "cpu",
            "canonical fixture execution requires CPU",
        )
    parameters = value["parameters"]
    normalized = parameters["normalized_by_name"]
    physical = parameters["physical_by_name"]
    _require(
        len(normalized) == len(physical) == 78, "parameters: expected 78 named values"
    )
    _require(normalized.keys() == physical.keys(), "parameters: map keys disagree")
    locks = parameters["locks_physical"]
    _require(locks.keys() <= physical.keys(), "parameters: unknown lock")
    _require(
        all(physical[name] == number for name, number in locks.items()),
        "parameters: lock does not match resolved physical value",
    )
    _require(
        value["noise"]["slot"] == value["fixture"]["sound_index"] % 32,
        "noise: slot disagrees with global sound identity",
    )
    git = value["project_git"]
    empty_diff = hashlib.sha256(b"").hexdigest()
    empty_untracked = hashlib.sha256(canonical_bytes({})).hexdigest()
    clean = (
        git["diff_sha256"] == empty_diff and git["untracked_sha256"] == empty_untracked
    )
    _require(
        git["dirty"] != clean,
        "project_git: dirty flag and empty-state digests disagree",
    )


def validate_artifact(value: Any, *, require_complete: bool = True) -> None:
    """Validate an artifact; failed records require explicit inspection mode.

    File hashes are checked for syntax here. Supply actual bytes separately to
    verify_sha256; validation alone never asserts that a render actually ran.
    """
    _validate_structure(value, _SCHEMA_FILES[0])
    _status(value, require_complete)
    available = value["inputs"]["state"] == "available"
    complete = value["status"] == "complete"
    _require(not complete or available, "complete artifact requires available inputs")
    if not available:
        _require(
            value["artifact_id"] is None, "unavailable inputs cannot carry a content ID"
        )
        _require(
            "diagnostic_orders" not in value, "orders require available parameters"
        )
    else:
        inputs = value["inputs"]["value"]
        _inputs(inputs)
        _require(
            value["artifact_id"] == content_id(inputs), "artifact content ID mismatch"
        )
        if "diagnostic_orders" in value:
            names = inputs["parameters"]["normalized_by_name"].keys()
            for order in value["diagnostic_orders"].values():
                _require(
                    set(order) == names,
                    "diagnostic order must enumerate every parameter",
                )
    for field in ("audio", "traces"):
        _require(
            not complete or value[field]["state"] == "available",
            f"complete artifact requires available {field}",
        )
    if value["audio"]["state"] == "available":
        audio = value["audio"]["value"]
        _require(
            audio["observed_sample_count"] == 176400, "audio: sample count mismatch"
        )
        _require(
            audio["file"]["size_bytes"] == 176400 * 4, "audio: byte count mismatch"
        )
        _require(audio["peak_index"] < 176400, "audio: peak index outside clip")
        _require(
            audio["clipped_sample_count"] <= 176400,
            "audio: clipping count outside clip",
        )
    if available and value["traces"]["state"] == "available":
        _require(
            set(value["traces"]["value"]) == set(inputs["requested_traces"]),
            "traces: captured names differ from requested registry entries",
        )


def validate_corpus_index(
    value: Any,
    *,
    require_complete: bool = True,
    artifacts: Mapping[str, Any] | None = None,
) -> None:
    """Validate declared cases/counts and, optionally, their loaded artifacts.

    With artifacts supplied, every complete case must resolve to a complete
    artifact with matching fixture/profile/runtime. Reference hashes cover exact
    metadata file bytes; consumers must check those bytes with verify_sha256.
    """
    _validate_structure(value, _SCHEMA_FILES[1])
    _status(value, require_complete)
    cases = value["cases"]
    _require(
        value["expected_case_count"] == len(cases), "corpus: expected count mismatch"
    )
    observed = sum(case["status"] == "complete" for case in cases)
    _require(
        value["observed_case_count"] == observed, "corpus: observed count mismatch"
    )
    _require(
        value["status"] != "complete" or observed == len(cases),
        "corpus: incomplete cases",
    )
    for field in ("case_id", "sound_index", "artifact_id"):
        if field == "sound_index":
            identities = [case["fixture"][field] for case in cases]
        elif field == "artifact_id":
            identities = [
                case["artifact"][field]
                for case in cases
                if case["artifact"] is not None
            ]
        else:
            identities = [case[field] for case in cases]
        _require(len(set(identities)) == len(identities), f"corpus: duplicate {field}")
    for case in cases:
        _fixture(case["fixture"])
        _status(case, False)
        complete = case["status"] == "complete"
        _require(
            complete == (case["artifact"] is not None),
            "case: artifact and status disagree",
        )
        if artifacts is not None and complete:
            artifact_id = case["artifact"]["artifact_id"]
            _require(artifact_id in artifacts, "case: referenced artifact is missing")
            record = artifacts[artifact_id]
            validate_artifact(record)
            _require(record["artifact_id"] == artifact_id, "case: artifact ID mismatch")
            inputs = record["inputs"]["value"]
            _require(
                inputs["fixture"] == case["fixture"],
                "case: fixture disagrees with artifact",
            )
            _require(
                inputs["profile"]["name"] == value["profile"], "case: profile mismatch"
            )
            _require(
                inputs["runtime"]["lock_sha256"] == value["runtime_lock_sha256"],
                "case: runtime lock mismatch",
            )
