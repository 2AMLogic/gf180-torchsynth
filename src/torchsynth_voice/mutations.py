"""Named, composable fault-injection seams: plans, events and evidence.

The #30 apparatus-side contract (spec/MUTATIONS.md). Stdlib-only, lazy, and
strictly additive: no producer or render-path module is imported for mutation
purposes, no producer file is edited, and the landed #22/#23 registry and
capture interfaces stay the sole owners of trace meaning and passive capture.

Domain separation: mutation plan identities are ``mp1-``, attempt artifact
identities and evidence envelope identities are ``mu1-``. They can never
collide with ordinary ``ra1-`` render artifacts or ``cm1-`` measurements, even
when every payload byte is identical, because the hashed preimage binds its
own domain string. Sham plans keep distinct provenance from non-sham plans
even when their outputs match the baseline byte for byte.

Fail-closed rules: unknown seam/operator/version, missing or wrong magnitude
units, boolean or non-finite magnitudes, duplicate instance IDs, undeclared
compositions, non-writable seams, and incomplete/extra/swapped event logs are
refused before or at completion of an attempt. Errors raised at a seam are
recorded in-band as ``errored`` events and re-raised; nothing is swallowed.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List

from .artifacts import canonical_bytes

PLAN_DOMAIN = b"torchsynth-mutation-plan-v1\n"
ARTIFACT_DOMAIN = b"torchsynth-mutation-artifact-v1\n"
EVIDENCE_DOMAIN = b"torchsynth-mutation-evidence-v1\n"

PARTITIONS = ("development", "holdout")
EVENT_STATUSES = ("applied", "ineffective", "errored", "refused")
PLAN_FIELDS = {"schema_version", "plan_id", "source_binding", "mutations"}
SOURCE_BINDING_FIELDS = {"case_id", "partition", "fixture_identity"}
MUTATION_FIELDS = {
    "instance_id",
    "operator",
    "operator_version",
    "seam",
    "magnitude",
    "configuration",
    "sham",
}
EVENT_FIELDS = {
    "instance_id",
    "seam",
    "status",
    "order",
    "sham",
    "detail",
}

SEAM_CATALOG_PATH = "spec/reference/mutation-seams-v1.json"


class MutationError(ValueError):
    """A plan, event log or evidence envelope violates the mutation contract."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise MutationError(reason)


def _nonempty(value: Any, path: str) -> None:
    _require(type(value) is str and bool(value.strip()), path + " must be nonblank text")


def load_seam_catalog(path) -> Dict[str, Dict[str, Any]]:
    """Load and structurally check the machine-readable seam catalog.

    The catalog is a capability overlay, not a second trace registry: it names
    apparatus seams that may host declared mutations and the landed downstream
    consumer that must refuse each fault. It never redefines trace meaning.
    """
    document = json.loads(Path(path).read_bytes())
    _require(type(document) is dict, "seam catalog must be an object")
    _require(document.get("schema_version") == 1, "unsupported seam catalog version")
    _require(type(document.get("seams")) is dict, "seam catalog seams must be an object")
    for name, entry in document["seams"].items():
        _nonempty(name, "seam name")
        _require(
            type(entry) is dict and set(entry) == {"phase", "writable", "summary", "downstream"},
            "seam entry requires exactly phase/writable/summary/downstream: " + name,
        )
        _nonempty(entry["phase"], "seam phase: " + name)
        _require(type(entry["writable"]) is bool, "seam writable must be boolean: " + name)
        _nonempty(entry["summary"], "seam summary: " + name)
        _nonempty(entry["downstream"], "seam downstream: " + name)
    return document


def validate_magnitude_spec(spec: Any) -> None:
    """Check an operator's declared magnitude domain: type, unit and bounds."""
    _require(type(spec) is dict and set(spec) <= {"type", "unit", "minimum", "maximum"}, "magnitude spec fields")
    _require("type" in spec and "unit" in spec, "magnitude spec requires type and unit")
    _require(spec["type"] in ("null", "integer", "number"), "unknown magnitude type: " + str(spec.get("type")))
    _nonempty(spec["unit"], "magnitude unit")
    for bound in ("minimum", "maximum"):
        if bound in spec:
            _require(type(spec[bound]) is int, bound + " must be an integer")


def validate_magnitude(spec: Dict[str, Any], magnitude: Any) -> None:
    """Typed magnitude check; booleans and non-finite numbers never pass."""
    validate_magnitude_spec(spec)
    kind = spec["type"]
    if kind == "null":
        _require(magnitude is None, "magnitude must be null for this operator")
        return
    _require(type(magnitude) is dict, "magnitude must be a {value, unit} object")
    _require(set(magnitude) == {"value", "unit"}, "magnitude requires exactly value and unit")
    _require(
        magnitude["unit"] == spec["unit"],
        "magnitude unit must be " + spec["unit"] + ", got " + str(magnitude["unit"]),
    )
    value = magnitude["value"]
    if kind == "integer":
        _require(type(value) is int, "magnitude value must be an integer, never bool")
    elif kind == "number":
        _require(
            type(value) in (int, float) and math.isfinite(value),
            "magnitude value must be a finite number",
        )
    else:
        raise MutationError("unknown magnitude type: " + kind)
    if "minimum" in spec:
        _require(value >= spec["minimum"], "magnitude value below declared minimum")
    if "maximum" in spec:
        _require(value <= spec["maximum"], "magnitude value above declared maximum")


def _validate_definition(operator_id: str, definition: Dict[str, Any]) -> None:
    _nonempty(operator_id, "operator id")
    expected = {
        "version",
        "seam",
        "sham",
        "magnitude",
        "configuration",
        "composes_with",
        "summary",
    }
    _require(
        type(definition) is dict and set(definition) == expected,
        "operator definition requires exactly " + ", ".join(sorted(expected)),
    )
    _require(type(definition["version"]) is int and definition["version"] >= 1, "operator version")
    _require(definition["seam"] is None or type(definition["seam"]) is str, "operator seam")
    _require(type(definition["sham"]) is bool, "operator sham flag must be boolean")
    validate_magnitude_spec(definition["magnitude"])
    _require(type(definition["configuration"]) is dict, "operator configuration schema")
    for key, allowed in definition["configuration"].items():
        _nonempty(key, "configuration key")
        _require(
            type(allowed) is list and all(type(item) is str for item in allowed),
            "configuration schema values must be string lists: " + key,
        )
    _require(type(definition["composes_with"]) is list, "composes_with must be a list")
    _nonempty(definition["summary"], "operator summary")


OPERATORS: Dict[str, Dict[str, Any]] = {
    "crash.producer": {
        "version": 1,
        "seam": "apparatus.producer_call",
        "sham": False,
        "magnitude": {"type": "null", "unit": "none"},
        "configuration": {},
        "composes_with": [],
        "summary": "raise an identified ProducerCrash at the producer call seam",
    },
    "truncate.payload": {
        "version": 1,
        "seam": "apparatus.artifact_write",
        "sham": False,
        "magnitude": {"type": "integer", "unit": "byte", "minimum": 0},
        "configuration": {},
        "composes_with": ["drop.receipt"],
        "summary": "persist only the first magnitude bytes of the payload (partial write)",
    },
    "corrupt.byte": {
        "version": 1,
        "seam": "apparatus.artifact_write",
        "sham": False,
        "magnitude": {"type": "integer", "unit": "byte_offset", "minimum": 0},
        "configuration": {},
        "composes_with": ["drop.receipt", "corrupt.digest"],
        "summary": "flip one bit at the magnitude offset of the stored payload (corrupted artifact)",
    },
    "corrupt.digest": {
        "version": 1,
        "seam": "apparatus.receipt_append",
        "sham": False,
        "magnitude": {"type": "null", "unit": "none"},
        "configuration": {},
        "composes_with": ["corrupt.byte"],
        "summary": "rebind the receipt artifact digest to foreign bytes (corrupted receipt)",
    },
    "drop.receipt": {
        "version": 1,
        "seam": "apparatus.receipt_append",
        "sham": False,
        "magnitude": {"type": "integer", "unit": "receipt", "minimum": 1, "maximum": 1},
        "configuration": {},
        "composes_with": ["truncate.payload", "corrupt.byte"],
        "summary": "omit the receipt row from the attempt inventory (dropped receipt)",
    },
    "swap.capture": {
        "version": 1,
        "seam": "capture.inventory",
        "sham": False,
        "magnitude": {"type": "null", "unit": "none"},
        "configuration": {"left": [], "right": []},
        "composes_with": [],
        "summary": "swap two capture inventory names presented to the capture validator",
    },
    "spoof.partition": {
        "version": 1,
        "seam": "board.partition_access",
        "sham": False,
        "magnitude": {"type": "null", "unit": "none"},
        "configuration": {"claim": list(PARTITIONS)},
        "composes_with": [],
        "summary": "present a relabeled partition claim to the access gate",
    },
    "noop.sham": {
        "version": 1,
        "seam": "apparatus.producer_call",
        "sham": True,
        "magnitude": {"type": "null", "unit": "none"},
        "configuration": {},
        "composes_with": [],
        "summary": "traverse dispatch and return the original value; marked sham, never detected",
    },
}


def register_operator(operator_id: str, definition: Dict[str, Any]) -> None:
    """Explicit trusted-code registration; executable plan text is never evaluated."""
    _require(operator_id not in OPERATORS, "operator already registered: " + operator_id)
    _validate_definition(operator_id, definition)
    OPERATORS[operator_id] = dict(definition)


def _validate_configuration(definition: Dict[str, Any], configuration: Any) -> None:
    _require(type(configuration) is dict, "configuration must be an object")
    for key, value in configuration.items():
        _require(
            key in definition["configuration"],
            "undeclared configuration key: " + str(key),
        )
        allowed = definition["configuration"][key]
        if allowed:
            _require(
                value in allowed,
                "configuration value not allowed for " + key + ": " + str(value),
            )
        else:
            _nonempty(value, "configuration value: " + key)


def plan_core(plan: Dict[str, Any]) -> Dict[str, Any]:
    _require(type(plan) is dict, "plan must be an object")
    _require(set(plan) == PLAN_FIELDS, "plan requires exactly " + ", ".join(sorted(PLAN_FIELDS)))
    return {key: plan[key] for key in ("schema_version", "source_binding", "mutations")}


def plan_identity(plan: Dict[str, Any]) -> str:
    """Domain-separated ``mp1-`` identity over the plan core (plan_id excluded)."""
    return "mp1-" + hashlib.sha256(PLAN_DOMAIN + canonical_bytes(plan_core(plan))).hexdigest()


def validate_plan(plan: Dict[str, Any], catalog: Dict[str, Any]) -> None:
    """Fail closed on unknown identities, bad magnitudes and undeclared composition."""
    _require(plan.get("schema_version") == 1, "unsupported plan schema_version")
    binding = plan["source_binding"]
    _require(
        type(binding) is dict and set(binding) == SOURCE_BINDING_FIELDS,
        "source_binding requires exactly case_id/partition/fixture_identity",
    )
    _nonempty(binding["case_id"], "source_binding.case_id")
    _require(binding["partition"] in PARTITIONS, "source_binding.partition must be development or holdout")
    _nonempty(binding["fixture_identity"], "source_binding.fixture_identity")
    _require(type(plan["mutations"]) is list, "plan mutations must be a list")
    seen = set()
    for instance in plan["mutations"]:
        _require(type(instance) is dict and set(instance) == MUTATION_FIELDS, "mutation instance fields")
        instance_id = instance["instance_id"]
        _nonempty(instance_id, "instance_id")
        _require(instance_id not in seen, "duplicate instance ID: " + instance_id)
        seen.add(instance_id)
        operator_id = instance["operator"]
        _require(operator_id in OPERATORS, "unknown operator: " + str(operator_id))
        definition = OPERATORS[operator_id]
        _require(
            instance["operator_version"] == definition["version"],
            "operator version mismatch: " + operator_id,
        )
        seam = instance["seam"]
        if seam not in catalog["seams"]:
            raise MutationError(
                "unknown seam: "
                + str(seam)
                + " (see "
                + SEAM_CATALOG_PATH
                + "; adding a writable seam requires a producer handoff)"
            )
        _require(
            definition["seam"] == seam,
            "operator " + operator_id + " does not support seam " + str(seam),
        )
        _require(
            catalog["seams"][seam]["writable"] is True,
            "seam is not a writable injection seam: " + str(seam),
        )
        _require(
            instance["sham"] is definition["sham"],
            "sham flag must match the operator registration: " + operator_id,
        )
        validate_magnitude(definition["magnitude"], instance["magnitude"])
        _validate_configuration(definition, instance["configuration"])
    for left in plan["mutations"]:
        for right in plan["mutations"]:
            if left is right or left["operator"] == right["operator"]:
                continue
            pair_allowed = (
                right["operator"] in OPERATORS[left["operator"]]["composes_with"]
                and left["operator"] in OPERATORS[right["operator"]]["composes_with"]
            )
            _require(
                pair_allowed,
                "undeclared combination: "
                + left["operator"]
                + " + "
                + right["operator"],
            )
    _require(plan["plan_id"] == plan_identity(plan), "plan_id does not match the plan core")


def make_plan(
    source_binding: Dict[str, Any],
    mutations: List[Dict[str, Any]],
    catalog: Dict[str, Any],
) -> Dict[str, Any]:
    """Build, validate and identity-bind an ordered mutation plan."""
    plan = {
        "schema_version": 1,
        "plan_id": "",
        "source_binding": dict(source_binding),
        "mutations": [dict(instance) for instance in mutations],
    }
    plan["plan_id"] = plan_identity(plan)
    validate_plan(plan, catalog)
    return plan


def validate_events(plan: Dict[str, Any], events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Fail closed on missing, extra, duplicate or reordered events; count statuses."""
    _require(type(events) is list, "event log must be a list")
    declared = [instance["instance_id"] for instance in plan["mutations"]]
    by_id = {instance["instance_id"]: instance for instance in plan["mutations"]}
    observed: List[str] = []
    for position, event in enumerate(events):
        _require(
            type(event) is dict and set(event) == EVENT_FIELDS,
            "event fields require exactly " + ", ".join(sorted(EVENT_FIELDS)),
        )
        _require(event["instance_id"] in by_id, "extra event for unknown instance: " + str(event["instance_id"]))
        _require(
            event["instance_id"] not in observed,
            "duplicate event for instance: " + str(event["instance_id"]),
        )
        observed.append(event["instance_id"])
        instance = by_id[event["instance_id"]]
        _require(event["seam"] == instance["seam"], "event seam mismatch: " + str(event["instance_id"]))
        _require(event["status"] in EVENT_STATUSES, "unknown event status: " + str(event["status"]))
        _require(type(event["sham"]) is bool and event["sham"] == instance["sham"], "event sham flag mismatch")
        _require(event["order"] == position, "event order does not match the declared plan order")
        detail = event["detail"]
        _require(type(detail) is dict, "event detail must be an object")
        if event["status"] == "errored":
            _nonempty(detail.get("error"), "errored event detail.error")
    missing = [instance_id for instance_id in declared if instance_id not in observed]
    _require(not missing, "missing events for instances: " + ", ".join(missing))
    _require(
        observed == declared,
        "actual event order does not match the declared plan order: "
        + ", ".join(observed)
        + " vs "
        + ", ".join(declared),
    )
    counts = {status: 0 for status in EVENT_STATUSES}
    for event in events:
        counts[event["status"]] += 1
    counts["expected"] = len(declared)
    counts["observed"] = len(events)
    counts["complete"] = True
    return counts


def require_complete_inventory(expected: Dict[str, int], observed: Dict[str, int]) -> None:
    """The expected attempt denominator never shrinks to observed evidence."""
    _require(
        type(expected) is dict and type(observed) is dict,
        "inventory counts must be objects",
    )
    for kind, wanted in expected.items():
        _require(type(wanted) is int and wanted >= 0, "expected count must be a nonnegative integer")
        got = observed.get(kind)
        _require(
            type(got) is int and got >= 0,
            "observed count missing or invalid for " + kind,
        )
        _require(
            got == wanted,
            "incomplete attempt inventory for "
            + kind
            + ": expected "
            + str(wanted)
            + ", observed "
            + str(got),
        )


def attempt_artifact_identity(payload: bytes) -> str:
    """``mu1-`` identity for mutation-attempt artifact bytes; never an ``ra1-`` form."""
    _require(type(payload) is bytes, "attempt artifact identity requires bytes")
    return "mu1-" + hashlib.sha256(ARTIFACT_DOMAIN + payload).hexdigest()


ENVELOPE_FIELDS = {
    "schema_version",
    "kind",
    "envelope_id",
    "plan_id",
    "source_binding",
    "events_summary",
    "artifacts",
    "controls",
    "fault_matrix",
    "runtime_scope",
    "not_run",
}


def envelope_core(envelope: Dict[str, Any]) -> Dict[str, Any]:
    _require(type(envelope) is dict, "envelope must be an object")
    return {key: envelope[key] for key in sorted(ENVELOPE_FIELDS - {"envelope_id"})}


def envelope_identity(envelope: Dict[str, Any]) -> str:
    """External ``mu1-`` hash over the envelope without its own identity field."""
    return "mu1-" + hashlib.sha256(EVIDENCE_DOMAIN + canonical_bytes(envelope_core(envelope))).hexdigest()


def validate_envelope(envelope: Dict[str, Any]) -> str:
    """Structural check and identity verification; returns the verified identity."""
    _require(
        type(envelope) is dict and set(envelope) == ENVELOPE_FIELDS,
        "envelope requires exactly " + ", ".join(sorted(ENVELOPE_FIELDS)),
    )
    _require(envelope["schema_version"] == 1, "unsupported envelope schema_version")
    _require(envelope["kind"] == "mutation-evidence-v1", "unknown envelope kind")
    _nonempty(envelope["plan_id"], "envelope plan_id")
    _require(envelope["plan_id"].startswith("mp1-"), "envelope plan_id must be an mp1- identity")
    _nonempty(envelope["runtime_scope"], "envelope runtime_scope")
    _require(type(envelope["not_run"]) is list, "not_run must be a list of undeclared scopes")
    _require(type(envelope["artifacts"]) is list, "envelope artifacts must be a list")
    for artifact in envelope["artifacts"]:
        _require(
            type(artifact) is dict and set(artifact) == {"path", "sha256", "size_bytes"},
            "artifact entry requires exactly path/sha256/size_bytes",
        )
        _nonempty(artifact["path"], "artifact path")
        digest = artifact["sha256"]
        _require(
            type(digest) is str and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest),
            "artifact sha256 must be 64 lowercase hexadecimal digits",
        )
        _require(
            type(artifact["size_bytes"]) is int and artifact["size_bytes"] >= 0,
            "artifact size_bytes must be a nonnegative integer",
        )
    _require(type(envelope["fault_matrix"]) is list, "fault_matrix must be a list")
    for entry in envelope["fault_matrix"]:
        _require(
            type(entry) is dict
            and set(entry)
            == {
                "fault",
                "operator",
                "seam",
                "downstream",
                "expected_refusal",
                "observed_refusal",
                "tripped",
                "control_accepted",
            },
            "fault matrix entry fields",
        )
        _require(type(entry["tripped"]) is bool, "fault matrix tripped must be boolean")
        _require(
            type(entry["control_accepted"]) is bool,
            "fault matrix control_accepted must be boolean",
        )
    _require(type(envelope["controls"]) is dict, "controls must be an object")
    identity = envelope_identity(envelope)
    _require(
        envelope["envelope_id"] == identity,
        "envelope identity mismatch: declared " + str(envelope["envelope_id"]),
    )
    return identity


def make_envelope(
    *,
    plan: Dict[str, Any],
    events_summary: Dict[str, Any],
    artifacts: List[Dict[str, Any]],
    controls: Dict[str, Any],
    fault_matrix: List[Dict[str, Any]],
    runtime_scope: str,
    not_run: List[str],
) -> Dict[str, Any]:
    envelope = {
        "schema_version": 1,
        "kind": "mutation-evidence-v1",
        "envelope_id": "",
        "plan_id": plan["plan_id"],
        "source_binding": dict(plan["source_binding"]),
        "events_summary": dict(events_summary),
        "artifacts": [dict(artifact) for artifact in artifacts],
        "controls": dict(controls),
        "fault_matrix": [dict(entry) for entry in fault_matrix],
        "runtime_scope": runtime_scope,
        "not_run": list(not_run),
    }
    envelope["envelope_id"] = envelope_identity(envelope)
    validate_envelope(envelope)
    return envelope
