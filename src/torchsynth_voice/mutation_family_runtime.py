"""Helpers shared by the signal and timing runtime adapters (#405, #463).

Stdlib-only. Each family adapter keeps module-level wrappers under its own
names; the bodies that were copied between ``mutations_signal_runtime`` and
``mutations_timing_runtime`` live here, parameterised only by the family's
voice-ops module, dataflow map, identity keys, entry selector or request
prefix.

#405 hoisted the bodies that were byte-identical. #463 hoisted the seven that
had drifted into *near*-duplicates (0.79-0.95 similar), which an exact-body
diff could not see. Where those two copies genuinely disagreed the difference
is now an explicit parameter with the reason recorded on the helper, so a
future edit cannot land on one family only and no divergence stays silent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import mutations
from . import paired_metrics
from .artifacts import canonical_bytes
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


def request_identity(manifest: Dict[str, Any], case: Dict[str, Any], ops: Any, *,
                     prefix: str, domain: bytes,
                     extra: Optional[Dict[str, Any]] = None) -> str:
    """Domain-separated identity of the resolved development request.

    ``prefix`` and ``domain`` are the family's own (``srq1-`` / ``trq1-`` and
    the matching request domain), so the two families' identities stay
    distinct. ``extra`` carries the keys only one family binds -- signal adds
    ``family`` and ``realisation``. ``canonical_bytes`` sorts object keys, so
    where a key is inserted does not affect the digest.
    """
    core = {
        "campaign": ops.MANIFEST_IDENTITY,
        "revision": ops.MANIFEST_REVISION,
        "frozen_inventory_sha256": ops.MANIFEST_FROZEN_SHA256,
        "case": case,
        "profile": manifest["target"]["runtime_profile"],
    }
    core.update(extra or {})
    return prefix + sha256(domain + canonical_bytes(core))


def runtime_definitions(ops: Any, *, family_label: str, provenance: str,
                        configure: Optional[Callable[[str, Dict[str, Any], Dict[str, Any]],
                                                     None]] = None,
                        ) -> Dict[str, Dict[str, Any]]:
    """Build the family's runtime operator definitions from ``ops.RUNTIME_OPERATORS``.

    ``configure`` is the family's hook for the trace-seam configuration rows
    only one family declares (timing's ``control_trace`` and route-swap
    ``partner``); it is called with the operator id, its spec and the
    configuration built so far. A parameter-value seam takes no hook, matching
    both original bodies.
    """
    definitions: Dict[str, Dict[str, Any]] = {}
    slot = {"type": "integer", "minimum": 0}
    for operator_id, spec in ops.RUNTIME_OPERATORS.items():
        if spec["seam"] == ops.PARAMETER_VALUE:
            configuration: Dict[str, Any] = {"parameter": list(spec["parameters"]), "slot": slot}
        else:
            configuration = {"trace": list(spec["traces"]), "slot": slot}
            if configure is not None:
                configure(operator_id, spec, configuration)
        composes = [right for left, right in ops.COMPOSITIONS if left == operator_id]
        composes += [left for left, right in ops.COMPOSITIONS if right == operator_id]
        definitions[operator_id] = {
            "version": 1,
            "seam": spec["seam"],
            "sham": operator_id == ops.SHAM,
            "magnitude": dict(spec["magnitude"]),
            "configuration": configuration,
            "composes_with": composes,
            "summary": (
                family_label
                + "-family voice-runtime sham: traverse the family dispatch and return the original slot"
                if operator_id == ops.SHAM
                else family_label
                + "-family voice-runtime realisation of "
                + spec["realises"]
                + " at "
                + spec["seam"]
                + " ("
                + provenance
                + "); bound to runtime-family-campaign-v1 revision 1"
            ),
        }
    return definitions


def expected_attempts(manifest: Dict[str, Any], case_id: str, *,
                      entries: Callable[[Dict[str, Any]], List[Dict[str, Any]]],
                      control_attempts: Sequence[str],
                      precheck: Optional[Callable[[Dict[str, Any], str], Any]] = None,
                      ) -> List[str]:
    """Per (case, repeat) process order: controls, entries in manifest order, clean rerun.

    ``precheck`` is the family's ``case_record`` where that family resolves the
    case before listing attempts, which refuses an unknown or non-development
    case id. Signal passes it and timing does not, so on an unknown case id
    signal raises ``MutationError`` while timing returns the controls plus
    ``clean_rerun``. That asymmetry predates #463 and is preserved here rather
    than normalised silently: it is now one explicit argument instead of two
    copies that happen to differ by a line.
    """
    if precheck is not None:
        precheck(manifest, case_id)
    names = list(control_attempts)
    for entry in entries(manifest):
        if (entry["disposition"] in ("runtime_cell", "composition")
                and case_id in entry["development_cases"]):
            names.append(entry["id"])
    names.append("clean_rerun")
    return names


def process_schedules(manifest: Dict[str, Any], manifest_sha256: str, case_id: str, repeat: int,
                      document: Dict[str, Any], inventory: Dict[str, Any],
                      catalog: Dict[str, Any], *,
                      attempts: Callable[[Dict[str, Any], str], List[str]],
                      plan_for: Callable[..., Dict[str, Any]],
                      resolve: Callable[..., Dict[str, Any]],
                      ) -> Dict[str, Dict[str, Any]]:
    """Every schedule one fresh (case, repeat) worker process executes, in order.

    ``plan_for`` is the family's own control/cell plan router and ``resolve``
    its ``resolve_schedule``; ``plain`` and ``clean_rerun`` carry no schedule.
    """
    schedules: Dict[str, Dict[str, Any]] = {}
    for attempt in attempts(manifest, case_id):
        if attempt in ("plain", "clean_rerun"):
            continue
        plan = plan_for(manifest, attempt, case_id, catalog)
        schedules[attempt] = resolve(plan, manifest, manifest_sha256, attempt, case_id,
                                     repeat, document, inventory, catalog)
    return schedules


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


def blast_radius(realised: List[Tuple[str, Dict[str, str]]], dataflow: Dict[str, List[str]],
                 parameter_roots: Dict[str, str]) -> List[str]:
    """Traces a realisation may change; a replaced seam itself keeps its original.

    A target carrying an optional ``partner`` contributes that trace's
    downstream too. Only timing declares one (the route-destination swap), so
    this is the timing body: on signal realisations, which never carry
    ``partner``, the extra branch cannot fire and the result is unchanged.
    """
    allowed: List[str] = []
    for _, target in realised:
        if "parameter" in target:
            root = parameter_roots[target["parameter"]]
            names = [root] + downstream(root, dataflow)
        else:
            names = downstream(target["trace"], dataflow)
            if "partner" in target:
                names += downstream(target["partner"], dataflow)
        allowed.extend(name for name in names if name not in allowed)
    return sorted(allowed)


def seam_traces(realised: List[Tuple[str, Dict[str, str]]]) -> List[str]:
    """The seam traces a realisation names, in realisation order, deduplicated.

    Collects ``partner`` as well for the same reason ``blast_radius`` does; a
    signal realisation has none, so its order and contents are unchanged.
    """
    names: List[str] = []
    for _, target in realised:
        for key in ("trace", "partner"):
            if key in target and target[key] not in names:
                names.append(target[key])
    return names


def paired_exactness(reference: List[float], candidate: List[float], rate_hz: float,
                     trace: str) -> Dict[str, Any]:
    """Paired exactness contract row: no preparation, no alignment, index zero is the origin.

    Alignment is never applied, because aligning would mask exactly the faults
    the time-locked detectors exist to catch (``det-signal-01/09``,
    ``det-timing``).

    ``mean_error`` is part of this row. Before #463 the signal copy reported it
    and the timing copy did not, from otherwise identical bodies. The omission
    was found to be unintentional, not a timing-family decision: no decision
    record, manifest field, publication schema or test asserted either shape;
    neither family's ``detected`` rule reads it (both use ``verdict``,
    ``framing_match`` and ``max_abs_error``); and ``compare_paired`` computes
    it identically for both, since both call it with the same arguments. So the
    shared row reports it for both families, and the timing report gains the
    key. On an unmeasured pair it is ``None``, as the signal row already was.
    """
    comparison = paired_metrics.compare_paired(
        reference, candidate, reference_rate_hz=rate_hz, candidate_rate_hz=rate_hz,
        unit="1", window_samples=max(1, len(reference)),
    )
    metrics = comparison["metrics"]
    exact = metrics["exact_equal"]["value"]
    return {
        "trace": trace,
        "pair_status": comparison["pair_status"],
        "time_origin": comparison["settings"]["time_origin"],
        "framing_match": metrics["framing_match"]["value"],
        "exact_equal": exact,
        "max_abs_error": metrics["max_abs_error"]["value"],
        "mean_error": metrics["mean_error"]["value"],
        "first_divergence_index": metrics["first_divergence_index"]["value"],
        "verdict": "PASS" if comparison["pair_status"] == "measured" and exact == 1 else "FAIL",
    }


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
