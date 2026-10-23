"""Identity-family runtime adapter: plans, schedules, detectors, validation (#334).

Host-side, stdlib-only half of the identity slice of the runtime family
campaign (``spec/reference/runtime-family-campaign-v1.json``, DR-0012). The
Torch half is the family-owned worker module
``env/release-era/mutation_worker_identity.py``; the bound bridge worker,
the framework modules, the #31 family module and the seam catalog are not
modified.

Scope, taken from the ratified manifest rather than restated: every identity
``runtime_cell`` and ``composition`` entry is executed through family-owned
runtime applicators at the declared writable seam ``apparatus.producer_call``
(``boundary_runtime``: the resolved request payload of a real render is
faulted; the Voice graph is not perturbed). A cell whose seam, operator,
evidence class, truth rule, detector or magnitude this adapter cannot realize
is refused at plan time with its reason and stays unmeasured; no seam is added
here and no producer handoff is assumed (DR-0012 D3 keeps normalization
deferred, and the identity cells need no keyboard-owned value).

Per (case, repeat) one schedule binds each plan (``mp1-`` identity) to the
campaign revision and frozen digest, the resolved development request (case,
global index, batch size/block/slot), the parameter inventory, trace registry
and seam catalog digests, the noise seed, the clean control and the repeat.
Attempts run plain, empty, sham, faults in manifest order, clean_rerun.

Detectors are evaluated offline on retained bytes with the
``case_identity_recomputation`` truth rule: identity, train/test designation
and conversion are recomputed from the case record with the pinned
``SoundIdentity``/``directed.physical_value`` code, and the noise stream truth
is the pinned noise construction at seed 13 for the case's slot. Identity and
conversion refusals happen before any audio/noise row is computed; noise
identity stays separate from the plausible noise statistics, which are kept as
receipts. Report validation is fail-closed (tampered plans, operators, cases or
digests, missing or reordered events, unrestored state, control drift) and
feeds the manifest's accounting interface
(``validate_runtime_family_campaign.runtime_kill_totals``).

Synthetic producers establish adapter mechanics only: their evaluations never
enter campaign outcomes, and nothing here establishes actual-Voice runtime
coverage, host admission or any family kill.
"""

from __future__ import annotations

import base64
import copy
import functools
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import directed
from . import float_sources
from . import mutation_runtime
from . import mutations
from . import mutations_identity as mi
from .float_interfaces import AUDIO_SAMPLES, NOISE_SEED, NOISE_STREAMS, ResolvedRequest
from .identity import SoundIdentity
from .mutations import MutationError

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = "spec/reference/runtime-family-campaign-v1.json"
VALIDATOR_PATH = "tools/validate_runtime_family_campaign.py"
WORKER_PATH = "env/release-era/mutation_worker_identity.py"
INVENTORY_PATH = "spec/reference/parameter-inventory-v1.json"
REGISTRY_PATH = "spec/reference/trace-registry-v1.json"

FAMILY = "identity"
EXECUTABLE_DISPOSITIONS = ("runtime_cell", "composition")
RUNTIME_SEAM = mi.FAMILY_SEAM
EVIDENCE_CLASS = "boundary_runtime"
TRUTH_RULE = "case_identity_recomputation"
EVALUATION_KIND = "mutation-identity-runtime-evaluation-v1"
CLEAN_CONTROL = "plain"
CONTROL_ATTEMPTS = ("plain", "empty", "sham", "clean_rerun")
DETECTOR_PROPERTIES = {
    "det-identity-01": "identity.provenance_match",
    "det-identity-02": "identity.train_test_match",
    "det-identity-03": "parameter.first_wrong_position",
    "det-identity-04": "parameter.first_wrong_position",
    "det-identity-05": "noise.identity",
    "det-identity-06": "noise.identity",
    "det-identity-07": "noise.identity",
}
SEED_REFUSAL = "noise seed must be the declared seed 13"
SLOT_COPY_REFUSAL = "diagnostic batch-1 must copy the resolved noise slot exactly"
RENDER_KEYS = (
    "batch_audio_sha256",
    "slot_sha256",
    "capture_inventory",
    "invocation_counts",
    "rng_state",
    "shapes_ok",
    "labels_all",
)

_MODULES: Dict[str, Any] = {}


_require = mutation_runtime._require
sha256 = mutation_runtime.sha256


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode()


def _load_path(root: Path, relative: str, name: str) -> Any:
    path = (Path(root) / relative).resolve()
    key = str(path)
    if key not in _MODULES:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _MODULES[key] = module
    return _MODULES[key]


def load_worker(root: Path = ROOT) -> Any:
    """The family-owned worker module (stdlib at import; Torch only in-image)."""
    return _load_path(root, WORKER_PATH, "mutation_worker_identity")


def load_validator(root: Path = ROOT) -> Any:
    return _load_path(root, VALIDATOR_PATH, "validate_runtime_family_campaign")


def load_campaign(root: Path = ROOT, manifest: Optional[Dict[str, Any]] = None,
                  check_sources: bool = True) -> Dict[str, Any]:
    """The ratified manifest, refused unless the #333 validator accepts it."""
    root = Path(root)
    if manifest is None:
        manifest = json.loads((root / MANIFEST_PATH).read_bytes())
    errors = load_validator(root).validate_manifest(manifest, root, check_sources)
    _require(not errors, "campaign manifest refused: " + "; ".join(errors[:3]))
    return manifest


def campaign_binding(manifest: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "identity": manifest["identity"],
        "revision": manifest["revision"],
        "frozen_inventory_digest": manifest["frozen_inventory_digest"]["sha256"],
        "manifest_sha256": sha256(canonical(manifest)),
    }


def identity_entries(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Executable identity entries in manifest order (the attempt order)."""
    return [
        entry for entry in manifest["inventory"]
        if entry["family"] == FAMILY and entry["disposition"] in EXECUTABLE_DISPOSITIONS
    ]


def resolve_request(manifest: Dict[str, Any], case_id: str) -> Dict[str, Any]:
    """The resolved development request for a manifest case; holdout refused."""
    cases = {case["id"]: case for case in manifest["cases"]}
    _require(case_id in cases, "unknown campaign case: " + str(case_id))
    case = cases[case_id]
    holdout = manifest["holdout"]
    index = case["global_sound_index"]
    _require(case["partition"] == "development",
             "only development cases may be requested: " + case_id)
    _require(
        not holdout["corpus_holdout_first_index"] <= index
        <= holdout["corpus_holdout_last_index"],
        "holdout identity refused before any render: " + case_id,
    )
    _require(
        SoundIdentity.from_batch(case["batch_block"], case["batch_slot"],
                                 case["batch_size"]).sound_index == index,
        "case coordinates do not resolve to the declared global index: " + case_id,
    )
    return {
        "case_id": case_id,
        "global_sound_index": index,
        "batch_size": case["batch_size"],
        "batch_block": case["batch_block"],
        "batch_slot": case["batch_slot"],
        "partition": case["partition"],
    }


def cell_refusal(entry: Dict[str, Any], catalog: Dict[str, Any],
                 worker: Any) -> Optional[str]:
    """Why this adapter cannot realize a cell, or None when it can."""
    seam = entry["runtime_seam"]
    seam_entry = catalog["seams"].get(seam)
    if seam_entry is None or seam_entry["writable"] is not True:
        return ("runtime seam %s is not a declared writable seam; a producer "
                "handoff is required and none is ratified" % seam)
    if seam != RUNTIME_SEAM:
        return ("no identity runtime realization at seam %s; adding one requires "
                "a named producer handoff" % seam)
    if entry["runtime_evidence_class"] != EVIDENCE_CLASS:
        return "unsupported runtime evidence class: " + str(entry["runtime_evidence_class"])
    if entry["runtime_truth"]["rule"] != TRUTH_RULE:
        return "unsupported runtime truth rule: " + str(entry["runtime_truth"]["rule"])
    for detector_id in entry["detector_ids"]:
        if detector_id not in DETECTOR_PROPERTIES:
            return "no runtime binding for detector " + str(detector_id)
    if entry["gated_detectors"]:
        return "identity adapter has no admissibility-gated detector binding"
    operators = entry["operators"]
    magnitudes = entry["magnitude"]
    if [m["operator"] for m in magnitudes] != operators:
        return "magnitude list does not follow the operator order"
    for operator_id, magnitude in zip(operators, magnitudes):
        if operator_id not in worker.RUNTIME_APPLICATORS:
            return "operator has no identity runtime applicator: " + operator_id
        definition = mutations.OPERATORS.get(operator_id)
        if definition is None or definition["seam"] != RUNTIME_SEAM:
            return "operator not registered at the identity seam: " + operator_id
        if definition["magnitude"]["unit"] != magnitude["unit"]:
            return ("declared magnitude unit %s differs from the registered unit %s "
                    "for %s" % (magnitude["unit"], definition["magnitude"]["unit"],
                                operator_id))
    if len(operators) > 1 and entry["configuration"] is not None:
        return "composition configuration cannot be attributed to one operator"
    return None


def _attempt_name(entry_id: str) -> str:
    return "cell-" + entry_id.split(":", 1)[1]


def instances_for(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    suffix = entry["id"].split(":", 1)[1]
    return [
        mutation_runtime.instance(
            "rti-%s-%d" % (suffix, position),
            operator_id,
            RUNTIME_SEAM,
            magnitude["value"],
            entry["configuration"] if len(entry["operators"]) == 1 else None,
        )
        for position, (operator_id, magnitude) in enumerate(
            zip(entry["operators"], entry["magnitude"])
        )
    ]


def schedule_bindings(root: Path, manifest: Dict[str, Any],
                      request: Dict[str, Any]) -> Dict[str, Any]:
    root = Path(root)
    return {
        "campaign": campaign_binding(manifest),
        "parameter_inventory_sha256": sha256((root / INVENTORY_PATH).read_bytes()),
        "trace_registry_sha256": sha256((root / REGISTRY_PATH).read_bytes()),
        "seam_catalog_sha256": sha256((root / mutations.SEAM_CATALOG_PATH).read_bytes()),
        "noise_seed": NOISE_SEED,
        "batch_slot": request["batch_slot"],
        "clean_control": CLEAN_CONTROL,
    }


def source_binding(request: Dict[str, Any], repeat: int, attempt: str,
                   bindings: Dict[str, Any]) -> Dict[str, Any]:
    body = {"request": request, "repeat": repeat, "attempt": attempt,
            "bindings": bindings}
    return {
        "case_id": "%s:%s:%s:r%d:%s" % (
            bindings["campaign"]["identity"], FAMILY, request["case_id"], repeat, attempt
        ),
        "partition": request["partition"],
        "fixture_identity": "rfi1-" + sha256(canonical(body)),
    }


def resolve_identity_schedule(case_id: str, repeat: int, root: Path = ROOT,
                              manifest: Optional[Dict[str, Any]] = None,
                              check_sources: bool = True) -> Dict[str, Any]:
    """Host-resolved identity schedule for one fresh (case, repeat) process."""
    root = Path(root)
    _require(type(repeat) is int and repeat >= 1, "repeat must be a positive integer")
    mi.register_family()
    manifest = load_campaign(root, manifest, check_sources)
    catalog = mutations.load_seam_catalog(root / mutations.SEAM_CATALOG_PATH)
    worker = load_worker(root)
    request = resolve_request(manifest, case_id)
    bindings = schedule_bindings(root, manifest, request)

    def plan_for(attempt: str, instances: List[Dict[str, Any]]) -> Dict[str, Any]:
        return mutations.make_plan(
            source_binding(request, repeat, attempt, bindings), instances, catalog
        )

    def control(name: str, kind: str, plan: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "attempt": name,
            "kind": kind,
            "cell_id": None,
            "plan": plan,
            "plan_id": None if plan is None else plan["plan_id"],
            "declared_order": [] if plan is None
            else [i["instance_id"] for i in plan["mutations"]],
            "expected_statuses": [] if plan is None
            else ["ineffective"] * len(plan["mutations"]),
            "detector_ids": [],
        }

    attempts = [
        control("plain", "plain", None),
        control("empty", "empty_plan", plan_for("empty", [])),
        control("sham", "sham_plan", plan_for(
            "sham", [mutation_runtime.instance("rti-sham", "noop.sham", RUNTIME_SEAM)]
        )),
    ]
    refused: List[Dict[str, Any]] = []
    for entry in identity_entries(manifest):
        reason = None
        if request["case_id"] not in entry["development_cases"]:
            continue
        reason = cell_refusal(entry, catalog, worker)
        plan = None
        if reason is None:
            try:
                plan = plan_for(_attempt_name(entry["id"]), instances_for(entry))
            except MutationError as error:
                reason = "plan refused: " + str(error)
        if reason is not None:
            refused.append({"cell_id": entry["id"], "reason": reason,
                            "status": "refused_unmeasured"})
            continue
        attempts.append({
            "attempt": _attempt_name(entry["id"]),
            "kind": "fault" if entry["disposition"] == "runtime_cell" else "composition",
            "cell_id": entry["id"],
            "plan": plan,
            "plan_id": plan["plan_id"],
            "declared_order": [i["instance_id"] for i in plan["mutations"]],
            "expected_statuses": [entry["expected_event"]] * len(plan["mutations"]),
            "detector_ids": list(entry["detector_ids"]),
        })
    attempts.append(control("clean_rerun", "clean_rerun", None))
    holdout = manifest["holdout"]
    return {
        "kind": worker.SCHEDULE_KIND,
        "schema_version": 1,
        "adapter_version": worker.ADAPTER_VERSION,
        "realization": worker.REALIZATION,
        "family": FAMILY,
        "evidence_class": EVIDENCE_CLASS,
        "campaign": bindings["campaign"],
        "request": request,
        "repeat": repeat,
        "holdout": {"first": holdout["corpus_holdout_first_index"],
                    "last": holdout["corpus_holdout_last_index"]},
        "bindings": bindings,
        "attempts": attempts,
        "refused_cells": refused,
    }


def expected_attempt_inventory(schedule: Dict[str, Any]) -> List[str]:
    return [attempt["attempt"] for attempt in schedule["attempts"]]


# --- offline detectors (case_identity_recomputation) ------------------------


@functools.lru_cache(maxsize=64)
def truth_noise(seed: int, slot: int) -> bytes:
    """The pinned noise construction for one slot (full clip)."""
    _require(0 <= slot < NOISE_STREAMS, "noise slot outside [0, 32)")
    return float_sources.canonical_noise_slot_bytes(seed, slot, NOISE_STREAMS, AUDIO_SAMPLES)


def _fixture_shaped(request: Dict[str, Any], document: Dict[str, Any],
                    noise_samples: bytes) -> Dict[str, Any]:
    """Presented values under the case-record truth keys mi's detectors read.

    ``sound_index`` and ``identity_batch_size`` come from the resolved request
    (the case record), never from the presented payload, so the recomputed
    truth is the case's own.
    """
    noise = document["noise"]
    return {
        "sound_index": request["global_sound_index"],
        "identity_batch_size": request["batch_size"],
        "execution_status": document["execution_status"],
        "identity": document["identity"],
        "parameters": document["parameters"],
        "noise": {
            "seed": noise["seed"],
            "slot": noise["slot"],
            "sample_count": noise["sample_count"],
            "sha256": noise["sha256"],
            "samples_f32le_b64": base64.b64encode(noise_samples).decode("ascii"),
        },
    }


def _truth_document(request: Dict[str, Any], presented: Dict[str, Any]) -> Dict[str, Any]:
    identity = SoundIdentity(request["global_sound_index"])
    samples = truth_noise(NOISE_SEED, identity.noise_slot)
    return {
        "sound_index": request["global_sound_index"],
        "identity_batch_size": request["batch_size"],
        "identity": identity.to_dict(request["batch_size"]),
        # Parameter truth is the pinned conversion of the presented normalized
        # vector (checked by the gate before this is ever read).
        "parameters": presented["parameters"],
        "noise": {
            "seed": NOISE_SEED,
            "slot": identity.noise_slot,
            "sample_count": len(samples) // 4,
            "sha256": sha256(samples),
            "samples_f32le_b64": base64.b64encode(samples).decode("ascii"),
        },
    }


def _witness(document: Dict[str, Any], execution_status: str) -> Dict[str, Any]:
    shaped = dict(document, execution_status=execution_status)
    return mi._resolved_request_witness(shaped)


def plausibility_properties() -> Tuple[str, ...]:
    """The declared plausibility rows (mi's band), separate from noise identity."""
    return ("noise.mean", "noise.variance") + tuple(
        "noise.ac.%d" % lag for lag in mi.NOISE_LAGS
    )


def expected_blast(operators: List[str]) -> Tuple[str, ...]:
    lanes: List[str] = []
    for operator_id in operators:
        for lane in mi.EXPECTED_BLAST[operator_id]:
            if lane not in lanes:
                lanes.append(lane)
    return tuple(lanes)


def evaluate_payload(request: Dict[str, Any], document: Dict[str, Any],
                     noise_samples: bytes,
                     blast: Tuple[str, ...] = ()) -> Dict[str, Any]:
    """Ordered identity detectors on one presented payload.

    The case binding, provenance and conversion gates run first; a refusal
    there stops before any noise (audio-lane) row is computed.
    """
    case_id = "runtime:%s:%s" % (FAMILY, request["case_id"])
    binding_ok = (
        document.get("schema") == load_worker().PAYLOAD_SCHEMA
        and document.get("case") == request
        and document.get("sound_index") == request["global_sound_index"]
        and document.get("identity_batch_size") == request["batch_size"]
    )
    if not binding_ok:
        return {"verdicts": {}, "rows": [], "perceptual_rows_reached": False,
                "gate_reason": "case binding refusal: payload is not bound to the "
                "resolved request", "statistics_plausible": None,
                "witness": None, "diagnostic_witness": None}
    names = [entry["name"] for entry in document["parameters"]]
    if names != mi.canonical_names():
        position = next(
            (i for i, pair in enumerate(zip(names, mi.canonical_names()))
             if pair[0] != pair[1]),
            min(len(names), len(mi.canonical_names())),
        )
        return {"verdicts": {"parameter.first_wrong_position": "FAIL"}, "rows": [],
                "perceptual_rows_reached": False,
                "gate_reason": "parameter order refusal at position %d" % position,
                "statistics_plausible": None, "witness": None,
                "diagnostic_witness": None}
    shaped = _fixture_shaped(request, document, noise_samples)
    truth = _truth_document(request, document)
    rows, gate = mi.detector_pipeline(shaped, truth, case_id, tuple(blast))
    verdicts = {row["property"]: row["verdict"] for row in rows}
    plausible = plausibility_properties()
    statistics = [row for row in rows if row["property"] in plausible]
    result = {
        "verdicts": verdicts,
        "rows": mi._receipts(rows),
        "perceptual_rows_reached": gate["perceptual_rows_reached"],
        "gate_reason": gate["gate_reason"],
        "first_wrong_identity_field": gate["provenance"]["first_wrong_identity_field"],
        "first_wrong_canonical_parameter":
            gate["parameter"]["first_wrong_canonical_parameter"],
        "statistics_plausible": (
            len(statistics) == len(plausible)
            and all(row["verdict"] == "PASS" for row in statistics)
            if gate["perceptual_rows_reached"] else None
        ),
        "blast_confined": (verdicts.get("blast.exact_match") == "PASS"
                           if gate["perceptual_rows_reached"] else None),
        "witness": None,
        "diagnostic_witness": None,
    }
    if gate["perceptual_rows_reached"]:
        result["witness"] = _witness(shaped, document["execution_status"])
        result["diagnostic_witness"] = _witness(shaped, "diagnostic-batch-1")
        result["diverged_lanes"] = gate["diverged_lanes"]
    return result


def control_accepted(evaluation: Dict[str, Any]) -> bool:
    verdicts = evaluation["verdicts"]
    return (
        evaluation["perceptual_rows_reached"] is True
        and all(verdicts.get(p) == "PASS" for p in set(DETECTOR_PROPERTIES.values()))
        and evaluation["statistics_plausible"] is True
        and evaluation["blast_confined"] is True
        and evaluation["witness"]["accepted"] is True
        and evaluation["diagnostic_witness"]["accepted"] is True
    )


def detector_status(detector_id: str, evaluation: Dict[str, Any]) -> str:
    """``detected`` only when the bound runtime-admissible detector refuses."""
    prop = DETECTOR_PROPERTIES[detector_id]
    verdict = evaluation["verdicts"].get(prop)
    if prop in ("identity.provenance_match", "identity.train_test_match",
                "parameter.first_wrong_position"):
        return ("detected" if verdict == "FAIL"
                and evaluation["perceptual_rows_reached"] is False else "not_detected")
    if not evaluation["perceptual_rows_reached"]:
        return "not_reached"
    if (verdict != "FAIL" or evaluation["statistics_plausible"] is not True
            or evaluation["blast_confined"] is not True):
        return "not_detected"
    if detector_id == "det-identity-05":
        refusal = evaluation["diagnostic_witness"]["refusal"] or ""
        return "detected" if SLOT_COPY_REFUSAL in refusal else "not_detected"
    if detector_id == "det-identity-06":
        refusal = evaluation["witness"]["refusal"] or ""
        return "detected" if SEED_REFUSAL in refusal else "not_detected"
    return "detected"


# --- fail-closed report validation ------------------------------------------


def _read_retained(output: Path, record: Dict[str, Any], errors: List[str],
                   label: str) -> Optional[bytes]:
    if type(record) is not dict or set(record) != {"file", "sha256", "bytes"}:
        errors.append(label + ": malformed retained-file record")
        return None
    path = Path(output) / record["file"]
    if Path(record["file"]).name != record["file"] or not path.is_file():
        errors.append(label + ": retained file missing")
        return None
    data = path.read_bytes()
    if sha256(data) != record["sha256"] or len(data) != record["bytes"]:
        errors.append(label + ": retained file does not re-hash to its receipt")
        return None
    return data


def _read_payload(output: Path, record: Any, errors: List[str],
                  label: str) -> Optional[Tuple[Dict[str, Any], bytes, str]]:
    if type(record) is not dict or set(record) != {"sha256", "document", "noise"}:
        errors.append(label + ": malformed payload record")
        return None
    raw = _read_retained(output, record["document"], errors, label + " document")
    noise = _read_retained(output, record["noise"], errors, label + " noise")
    if raw is None or noise is None:
        return None
    if sha256(raw) != record["sha256"]:
        errors.append(label + ": payload digest does not bind the retained document")
        return None
    document = json.loads(raw)
    if canonical(document) != raw:
        errors.append(label + ": retained payload is not canonical")
        return None
    if document.get("noise", {}).get("sha256") != sha256(noise):
        errors.append(label + ": payload noise digest does not bind the noise bytes")
        return None
    return document, noise, record["sha256"]


def _check_event_chain(events: List[Dict[str, Any]], original: str, presented: str,
                       realization: str, label: str, errors: List[str]) -> None:
    current = original
    for event in events:
        detail = event["detail"]
        if detail.get("realization") != realization:
            errors.append(label + ": event realization mismatch")
        if detail.get("source_payload_sha256") != current:
            errors.append(label + ": event source digest breaks the payload chain")
            return
        if event["status"] in ("applied", "ineffective"):
            current = detail.get("replacement_payload_sha256")
    if current != presented:
        errors.append(label + ": presented payload is not the last replacement")


def evaluate_report(report: Dict[str, Any], schedule: Dict[str, Any], output: Path,
                    root: Path = ROOT,
                    manifest: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Offline, non-writing evaluation of one worker report; fail closed.

    The given schedule is first re-derived with ``resolve_identity_schedule``
    from the ratified manifest (``manifest`` if given, else the committed one,
    always validated) and refused unless canonically identical. ``status`` is
    PASS only when that binding and every structural, integrity, control,
    restoration and invariance check holds. Cell results are reported per
    cell; an ineffective or undetected cell stays open, never a kill.
    """
    root = Path(root)
    output = Path(output)
    mi.register_family()
    worker = load_worker(root)
    catalog = mutations.load_seam_catalog(root / mutations.SEAM_CATALOG_PATH)
    errors: List[str] = []
    request = schedule["request"]
    evaluation: Dict[str, Any] = {
        "kind": EVALUATION_KIND,
        "status": "FAIL",
        "errors": errors,
        "case": request["case_id"],
        "repeat": schedule["repeat"],
        "campaign": dict(schedule["campaign"]),
        "evidence_class": EVIDENCE_CLASS,
        "counts_toward_graph_runtime_kills": False,
        "producer_kind": None,
        "adapter_mechanics_only": True,
        "controls": {},
        "cells": {},
        "refused_cells": copy.deepcopy(schedule["refused_cells"]),
        "expected_attempts": expected_attempt_inventory(schedule),
    }
    # The schedule is untrusted input: a self-consistent schedule (plan ids
    # re-hashed after a tamper) must still be the exact schedule the ratified
    # manifest, inventory, registry and catalog resolve for (case, repeat).
    try:
        resolved = resolve_identity_schedule(request["case_id"], schedule["repeat"], root,
                                             manifest)
    except (MutationError, ValueError, KeyError, TypeError) as error:
        errors.append("schedule refused: cannot re-resolve from the manifest: " + str(error))
        return evaluation
    if canonical(resolved) != canonical(schedule):
        errors.append("schedule refused: not the schedule the ratified manifest resolves "
                      "for this case and repeat")
        return evaluation
    try:
        worker.validate_schedule(schedule)
    except (ValueError, KeyError, TypeError) as error:
        errors.append("schedule refused: " + str(error))
        return evaluation
    header = (
        ("kind", worker.REPORT_KIND), ("schema_version", 1),
        ("adapter_version", worker.ADAPTER_VERSION),
        ("realization", worker.REALIZATION), ("status", "EXECUTED"),
    )
    for key, wanted in header:
        if report.get(key) != wanted:
            errors.append("report %s mismatch" % key)
    if report.get("schedule_sha256") != sha256(canonical(schedule)):
        errors.append("report is not bound to this schedule")
    for key in ("campaign", "request", "repeat"):
        if report.get(key) != schedule[key]:
            errors.append("report %s does not match the schedule" % key)
    producer = report.get("producer") or {}
    evaluation["producer_kind"] = producer.get("kind")
    evaluation["adapter_mechanics_only"] = (
        producer.get("kind") != worker.ACTUAL_VOICE_PRODUCER
    )
    expected = expected_attempt_inventory(schedule)
    if report.get("expected_attempts") != expected:
        errors.append("expected attempt inventory differs from the schedule")
    records = report.get("attempts")
    if type(records) is not list or [r.get("attempt") for r in records] != expected:
        errors.append("attempt inventory is not exactly the scheduled attempts in order")
        return evaluation
    by_name: Dict[str, Dict[str, Any]] = {}
    for attempt, record in zip(schedule["attempts"], records):
        name = attempt["attempt"]
        label = "attempt " + name
        for key in ("kind", "cell_id", "plan_id"):
            if record.get(key) != attempt[key]:
                errors.append(label + ": %s does not match the schedule" % key)
        plan = attempt["plan"]
        if plan is not None:
            try:
                mutations.validate_plan(plan, catalog)
            except (MutationError, KeyError, TypeError) as error:
                errors.append(label + ": plan refused: " + str(error))
        if record.get("errored") is not None:
            errors.append(label + ": errored: " + str(record["errored"]))
        if record.get("state_restored") is not True:
            errors.append(label + ": state not restored")
        if record.get("seam_restored") is not True:
            errors.append(label + ": seam wrapper not removed")
        events = record.get("events")
        if plan is None:
            if events != []:
                errors.append(label + ": control without a plan recorded events")
        else:
            try:
                mutations.validate_events(plan, events)
            except (MutationError, KeyError, TypeError) as error:
                errors.append(label + ": event log refused: " + str(error))
                events = None
            if events is not None and [e["status"] for e in events] != attempt[
                    "expected_statuses"] and attempt["kind"] not in ("fault", "composition"):
                errors.append(label + ": control event statuses differ from the plan")
        render = record.get("render")
        if type(render) is not dict or any(k not in render for k in RENDER_KEYS):
            errors.append(label + ": render evidence missing")
            continue
        audio = _read_retained(output, render.get("audio_slot"), errors, label + " audio")
        original = _read_payload(output, record.get("original_payload"), errors,
                                 label + " original payload")
        presented = _read_payload(output, record.get("presented_payload"), errors,
                                  label + " presented payload")
        if audio is None or original is None or presented is None:
            continue
        if render["slot_sha256"][request["batch_slot"]] != sha256(audio):
            errors.append(label + ": retained slot audio is not the declared slot")
        if events:
            _check_event_chain(events, original[2], presented[2], worker.REALIZATION,
                               label, errors)
        elif original[2] != presented[2]:
            errors.append(label + ": presented payload differs without any event")
        by_name[name] = {"record": record, "events": events, "original": original,
                         "presented": presented}
    if errors or "plain" not in by_name:
        return evaluation
    plain = by_name["plain"]
    plain_render = plain["record"]["render"]

    def render_equal(name: str) -> bool:
        render = by_name[name]["record"]["render"]
        return all(render[key] == plain_render[key] for key in RENDER_KEYS) and (
            render["audio_slot"]["sha256"] == plain_render["audio_slot"]["sha256"]
        )

    if not (plain_render["shapes_ok"] is True and plain_render["labels_all"] is True):
        errors.append("plain attempt render malformed")
    plain_eval = evaluate_payload(request, plain["presented"][0], plain["presented"][1])
    controls = {"plain_detectors_pass": control_accepted(plain_eval)}
    for name in CONTROL_ATTEMPTS[1:]:
        entry = by_name[name]
        same = render_equal(name) and entry["presented"][2] == plain["presented"][2] and (
            entry["original"][2] == plain["original"][2]
        )
        controls[name + "_byte_identical_to_plain"] = same
    controls["empty_zero_events"] = by_name["empty"]["events"] == []
    sham_events = by_name["sham"]["events"] or []
    controls["sham_marked_ineffective"] = (
        len(sham_events) == 1 and sham_events[0]["sham"] is True
        and sham_events[0]["status"] == "ineffective"
    )
    evaluation["controls"] = controls
    for key, value in controls.items():
        if value is not True:
            errors.append("control failed: " + key)
    plain_digest = plain["presented"][2]
    for attempt in schedule["attempts"]:
        if attempt["kind"] not in ("fault", "composition"):
            continue
        entry = by_name[attempt["attempt"]]
        statuses = [event["status"] for event in entry["events"]]
        invariance = {
            "render_bytes_identical_to_plain": render_equal(attempt["attempt"]),
            "undeclared_slots_identical_to_plain":
                entry["record"]["render"]["slot_sha256"] == plain_render["slot_sha256"],
            "sibling_traces_identical_to_plain":
                entry["record"]["render"]["capture_inventory"]
                == plain_render["capture_inventory"],
            "passive_original_identical_to_plain": entry["original"][2] == plain_digest,
        }
        for key, value in invariance.items():
            if value is not True:
                errors.append("attempt %s: invariance failed: %s" % (attempt["attempt"], key))
        operators = [i["operator"] for i in attempt["plan"]["mutations"]]
        payload_eval = evaluate_payload(request, entry["presented"][0], entry["presented"][1],
                                        expected_blast(operators))
        detectors = {d: detector_status(d, payload_eval) for d in attempt["detector_ids"]}
        if statuses != attempt["expected_statuses"]:
            status = "ineffective" if "ineffective" in statuses else "unexpected_events"
        elif entry["presented"][2] == plain_digest:
            status = "ineffective"
        elif all(value == "detected" for value in detectors.values()):
            status = "detected"
        else:
            status = "not_detected"
        evaluation["cells"][attempt["cell_id"]] = {
            "attempt": attempt["attempt"],
            "kind": attempt["kind"],
            "status": status,
            "events": statuses,
            "detectors": detectors,
            "gate_reason": payload_eval["gate_reason"],
            "perceptual_rows_reached": payload_eval["perceptual_rows_reached"],
            "statistics_plausible": payload_eval["statistics_plausible"],
            "blast_confined": payload_eval.get("blast_confined"),
            "first_wrong_identity_field": payload_eval.get("first_wrong_identity_field"),
            "first_wrong_canonical_parameter":
                payload_eval.get("first_wrong_canonical_parameter"),
            "presented_payload_sha256": entry["presented"][2],
            "replacement_retained": entry["record"]["presented_payload"]["document"]["file"],
            "invariance": invariance,
            "rows": payload_eval["rows"],
        }
    if not errors:
        evaluation["status"] = "PASS"
    return evaluation


# --- campaign accounting interface (#333) -----------------------------------


def campaign_outcomes(manifest: Dict[str, Any],
                      evaluations: List[Dict[str, Any]]) -> Dict[Tuple[str, str, int], str]:
    """Map evaluations onto the manifest's (entry, case, repeat) outcome keys.

    Only evaluations of the actual-Voice producer bound to this manifest are
    accepted; synthetic adapter evidence is refused outright. A PASS
    evaluation contributes its per-cell statuses; every cell of a FAIL
    evaluation is recorded as ``execution_failed`` (never a kill). A cell whose
    required repeats presented different fault bytes is downgraded in every
    repeat (no retry-to-pass).
    """
    binding = campaign_binding(manifest)
    worker = load_worker()
    outcomes: Dict[Tuple[str, str, int], str] = {}
    digests: Dict[Tuple[str, str], Dict[int, str]] = {}
    for evaluation in evaluations:
        _require(evaluation.get("kind") == EVALUATION_KIND, "not an identity evaluation")
        _require(evaluation["producer_kind"] == worker.ACTUAL_VOICE_PRODUCER
                 and evaluation["adapter_mechanics_only"] is False,
                 "synthetic adapter evidence never enters campaign outcomes")
        _require(evaluation["campaign"] == binding,
                 "evaluation is bound to a different campaign revision")
        case, repeat = evaluation["case"], evaluation["repeat"]
        for cell_id, cell in evaluation["cells"].items():
            key = (cell_id, case, repeat)
            _require(key not in outcomes, "duplicate execution for %s" % (key,))
            outcomes[key] = (cell["status"] if evaluation["status"] == "PASS"
                             else "execution_failed")
            digests.setdefault((cell_id, case), {})[repeat] = cell["presented_payload_sha256"]
    for (cell_id, case), by_repeat in digests.items():
        if len(set(by_repeat.values())) > 1:
            for repeat in by_repeat:
                outcomes[(cell_id, case, repeat)] = "repeat_bytes_differ"
    return outcomes


def identity_totals(manifest: Dict[str, Any],
                    outcomes: Dict[Tuple[str, str, int], str],
                    root: Path = ROOT) -> Dict[str, Any]:
    """Identity boundary-class totals through the manifest accounting rule.

    Reported as ``boundary_runtime`` and never merged into graph-perturbation
    totals; compositions are listed separately and never count.
    """
    validator = load_validator(root)
    scoped = dict(manifest)
    scoped["inventory"] = [e for e in manifest["inventory"] if e["family"] == FAMILY]
    totals = validator.runtime_kill_totals(scoped, outcomes)
    compositions = {}
    for entry in scoped["inventory"]:
        if entry["disposition"] != "composition":
            continue
        repeats = validator.required_repeats(manifest, entry)
        for case in entry["development_cases"]:
            compositions["%s@%s" % (entry["id"], case)] = (
                "detected" if all(outcomes.get((entry["id"], case, r)) == "detected"
                                  for r in range(1, repeats + 1)) else "open"
            )
    return {
        "evidence_class": EVIDENCE_CLASS,
        "counts_toward_graph_runtime_kills": False,
        "boundary_runtime": totals,
        "compositions_reported_separately": compositions,
    }
