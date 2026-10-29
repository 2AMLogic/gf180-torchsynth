"""Helpers shared by the signal and timing runtime adapters (#405).

Stdlib-only. Each family adapter keeps module-level wrappers under its own
names; the bodies that were copied verbatim between ``mutations_signal_runtime``
and ``mutations_timing_runtime`` live here, parameterised only by the family's
voice-ops module, dataflow map or identity keys.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import mutations
from .mutation_runtime import _require, sha256
from .mutations import MutationError

IDENTITY_KEYS = (
    "batch_audio_sha256",
    "audio_slot_sha256",
    "slot_sha256",
    "noise_sha256",
    "rng_state",
    "labels_all",
    "invocation_counts",
    "capture_inventory",
    "parameters_before_sha256",
)


def load_manifest(root: Path, ops: Any) -> Tuple[Dict[str, Any], str]:
    """Load the committed manifest; fail closed on revision/digest drift."""
    data = (Path(root) / ops.MANIFEST_PATH).read_bytes()
    manifest = json.loads(data)
    try:
        ops.require_manifest_binding(manifest)
    except ValueError as error:
        raise MutationError(str(error))
    return manifest, sha256(data)


def manifest_binding(manifest: Dict[str, Any], manifest_sha256: str, ops: Any) -> Dict[str, Any]:
    try:
        ops.require_manifest_binding(manifest)
    except ValueError as error:
        raise MutationError(str(error))
    return {
        "identity": ops.MANIFEST_IDENTITY,
        "revision": ops.MANIFEST_REVISION,
        "frozen_inventory_sha256": ops.MANIFEST_FROZEN_SHA256,
        "manifest_sha256": manifest_sha256,
    }


def case_record(manifest: Dict[str, Any], case_id: str, ops: Any) -> Dict[str, Any]:
    for case in manifest["cases"]:
        if case["id"] == case_id:
            holdout = manifest["holdout"]
            _require(case["partition"] == "development", "case outside the development partition")
            _require(
                not holdout["corpus_holdout_first_index"]
                <= case["global_sound_index"]
                <= holdout["corpus_holdout_last_index"],
                "case lies in the sealed holdout",
            )
            _require(case["batch_size"] == ops.BATCH_SIZE, "case batch outside the pinned batch")
            _require(
                divmod(case["global_sound_index"], case["batch_size"])
                == (case["batch_block"], case["batch_slot"]),
                "case coordinates are inconsistent",
            )
            return dict(case)
    raise MutationError("unknown campaign case: " + str(case_id))


def register_runtime_operators(definitions: Dict[str, Any]) -> None:
    """Explicit trusted-code registration; identical re-registration is a no-op."""
    for operator_id, definition in definitions.items():
        registered = mutations.OPERATORS.get(operator_id)
        if registered is not None:
            _require(registered == definition,
                     "operator already registered with a different definition: " + operator_id)
            continue
        mutations.register_operator(operator_id, definition)


def instance(instance_id: str, operator_id: str, magnitude: Optional[Dict[str, Any]],
             configuration: Dict[str, Any], ops: Any) -> Dict[str, Any]:
    spec = ops.RUNTIME_OPERATORS[operator_id]
    return {
        "instance_id": instance_id,
        "operator": operator_id,
        "operator_version": 1,
        "seam": spec["seam"],
        "magnitude": magnitude,
        "configuration": dict(configuration),
        "sham": operator_id == ops.SHAM,
    }


def registry_trace(document: Dict[str, Any], name: str) -> Dict[str, Any]:
    for trace in document["traces"]:
        if trace["name"] == name:
            return trace
    raise MutationError("unknown registry trace: " + str(name))


def inventory_range(inventory: Dict[str, Any], name: str) -> Dict[str, Any]:
    for parameter in inventory["parameters"]:
        if parameter["name"] == name:
            return {
                "minimum": float(parameter["minimum"]),
                "maximum": float(parameter["maximum"]),
                "curve": float(parameter["curve"]),
                "symmetric": parameter["symmetric"],
            }
    raise MutationError("unknown inventory parameter: " + str(name))


def downstream(trace: str, dataflow: Dict[str, List[str]]) -> List[str]:
    seen: List[str] = []
    stack = list(dataflow[trace])
    while stack:
        name = stack.pop()
        if name not in seen:
            seen.append(name)
            stack.extend(dataflow[name])
    return sorted(seen)


def trace_preflight(document: Dict[str, Any], name: str, data: bytes, inventory_digest: str,
                    ops: Any) -> List[float]:
    """Frame and identity preflight; runs before any metric and refuses on mismatch."""
    trace = registry_trace(document, name)
    _require(trace["dtype"] == "float32", "trace dtype is not float32: " + name)
    frame = 1
    for size in trace["shape"]:
        frame *= size
    _require(type(data) is bytes and len(data) == 4 * frame,
             "frame preflight refusal: " + name + " declares " + str(frame) + " samples, observed "
             + str(len(data) // 4 if type(data) is bytes else None))
    _require(sha256(data) == inventory_digest,
             "identity preflight refusal: retained bytes do not match the capture inventory: " + name)
    return list(ops.floats(data, frame))


def inventory_digests(record: Dict[str, Any]) -> Dict[str, str]:
    return {item["name"]: item["sha256"] for item in record["capture_inventory"]}


def require_identical(plain: Dict[str, Any], record: Dict[str, Any], label: str,
                      identity_keys: Sequence[str]) -> None:
    for key in identity_keys:
        _require(record.get(key) == plain.get(key),
                 "control is not byte-identical to plain: " + label + " (" + key + ")")
