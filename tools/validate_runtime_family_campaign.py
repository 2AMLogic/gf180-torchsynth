#!/usr/bin/env python3
"""Non-writing validator for the runtime family campaign manifest (#333).

Validates ``spec/reference/runtime-family-campaign-v1.json`` against its
declared schema subset and against the committed family publications, the seam
catalog and the corpus/holdout policy. Stdlib only. It never writes a file,
never renders, never launches Docker and never measures anything: a PASS means
the preregistration is internally consistent, not that any fault was detected.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = "spec/reference/runtime-family-campaign-v1.json"
SCHEMA_PATH = "spec/schemas/runtime-family-campaign-v1.schema.json"
SEAM_CATALOG_PATH = "spec/reference/mutation-seams-v1.json"
FAMILIES = ("identity", "timing", "signal")
PUBLICATIONS = {
    family: "sim/reference/mutation-%s-v1.json" % family for family in FAMILIES
}
SUCCESS_DISPOSITIONS = ("runtime_cell",)
NON_EXECUTED_DISPOSITIONS = ("deferred_unmeasured",)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def file_sha256(root: Path, relative: str) -> str:
    return hashlib.sha256((root / relative).read_bytes()).hexdigest()


def load_json(root: Path, relative: str) -> Any:
    return json.loads((root / relative).read_bytes())


def frozen_digest(manifest: Dict[str, Any]) -> str:
    body = {
        "inventory": manifest["inventory"],
        "sensitivity_inventory": manifest["sensitivity_inventory"],
        "supporting": manifest["supporting"],
        "cases": manifest["cases"],
        "detectors": manifest["detectors"],
    }
    return hashlib.sha256(canonical(body)).hexdigest()


# --------------------------------------------------------------------------
# Minimal JSON-Schema subset (the keywords the declared schema actually uses).
# --------------------------------------------------------------------------

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def schema_errors(value: Any, schema: Dict[str, Any], root_schema: Dict[str, Any],
                  path: str = "$") -> List[str]:
    errors: List[str] = []
    if "$ref" in schema:
        target: Any = root_schema
        for part in schema["$ref"].lstrip("#/").split("/"):
            target = target[part]
        return schema_errors(value, target, root_schema, path)
    if "const" in schema and (
        value != schema["const"] or type(value) is not type(schema["const"])
    ):
        errors.append("%s: expected const %r" % (path, schema["const"]))
        return errors
    if "enum" in schema and value not in schema["enum"]:
        errors.append("%s: %r not in enum %r" % (path, value, schema["enum"]))
        return errors
    if "type" in schema:
        wanted = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_TYPES[name](value) for name in wanted):
            errors.append("%s: expected type %s" % (path, "/".join(wanted)))
            return errors
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append("%s: shorter than minLength" % path)
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append("%s: does not match pattern %s" % (path, schema["pattern"]))
    if _TYPES["number"](value):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append("%s: below minimum" % path)
        if "multipleOf" in schema and value % schema["multipleOf"] != 0:
            errors.append("%s: not a multiple of %s" % (path, schema["multipleOf"]))
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append("%s: fewer than minItems" % path)
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append("%s: more than maxItems" % path)
        if "items" in schema:
            for index, item in enumerate(value):
                errors.extend(
                    schema_errors(item, schema["items"], root_schema,
                                  "%s[%d]" % (path, index))
                )
    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                errors.append("%s: missing required %s" % (path, name))
        if "minProperties" in schema and len(value) < schema["minProperties"]:
            errors.append("%s: fewer than minProperties" % path)
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for name, item in value.items():
            if name in properties:
                errors.extend(
                    schema_errors(item, properties[name], root_schema,
                                  "%s.%s" % (path, name))
                )
            elif additional is False:
                errors.append("%s: unexpected property %s" % (path, name))
            elif isinstance(additional, dict):
                errors.extend(
                    schema_errors(item, additional, root_schema,
                                  "%s.%s" % (path, name))
                )
    return errors


# --------------------------------------------------------------------------
# Semantic validation
# --------------------------------------------------------------------------

def _duplicates(values: Iterable[str]) -> List[str]:
    seen: Dict[str, int] = {}
    for value in values:
        seen[value] = seen.get(value, 0) + 1
    return sorted(key for key, count in seen.items() if count > 1)


def _expected_counts(manifest: Dict[str, Any]) -> Dict[str, Any]:
    inventory = manifest["inventory"]
    cases = len(manifest["cases"])
    repeats = manifest["reliability"]["min_independent_executions_per_cell"]
    cells = sum(1 for e in inventory if e["disposition"] == "runtime_cell")
    comps = sum(1 for e in inventory if e["disposition"] == "composition")
    fault_attempts = (cells + comps) * cases * repeats
    control_attempts = len(manifest["controls"]) * cases * repeats
    dispositions = sorted({e["disposition"] for e in inventory})
    return {
        "cases": cases,
        "repeats_per_cell": repeats,
        "worker_processes": cases * repeats,
        "inventory_entries": len(inventory),
        "inventory_source_rows": sum(len(e["source"]["row_indices"]) for e in inventory),
        "by_disposition": {
            d: sum(1 for e in inventory if e["disposition"] == d) for d in dispositions
        },
        "sensitivity_entries": len(manifest["sensitivity_inventory"]),
        "runtime_fault_cells": cells * cases,
        "composition_cells": comps * cases,
        "fault_attempts": fault_attempts,
        "control_attempts": control_attempts,
        "worker_attempts": fault_attempts + control_attempts,
        "expected_refusal_executions": sum(
            len(e["development_cases"]) for e in inventory if e["disposition"] == "refusal"
        ),
        "deferred_unmeasured_entries": sum(
            1 for e in inventory if e["disposition"] == "deferred_unmeasured"
        ) + sum(
            1 for e in manifest["sensitivity_inventory"]
            if e["disposition"] == "deferred_unmeasured"
        ),
    }


def semantic_errors(manifest: Dict[str, Any], root: Path,
                    check_sources: bool = True) -> List[str]:
    errors: List[str] = []
    inventory = manifest["inventory"]
    cases = {c["id"]: c for c in manifest["cases"]}
    detectors = {d["id"] for d in manifest["detectors"]}
    operators = {o["id"]: o for o in manifest["operators"]}
    catalog = load_json(root, SEAM_CATALOG_PATH)["seams"]
    holdout = manifest["holdout"]

    # --- unique identifiers -------------------------------------------------
    groups: List[Tuple[str, List[str]]] = [
        ("inventory id", [e["id"] for e in inventory]),
        ("sensitivity id", [e["id"] for e in manifest["sensitivity_inventory"]]),
        ("supporting id", [e["id"] for e in manifest["supporting"]]),
        ("case id", list(cases) if len(cases) == len(manifest["cases"]) else
         [c["id"] for c in manifest["cases"]]),
        ("detector id", [d["id"] for d in manifest["detectors"]]),
        ("control id", [c["id"] for c in manifest["controls"]]),
        ("operator id", [o["id"] for o in manifest["operators"]]),
        ("proposed id", [p["id"] for p in manifest["proposed_additions"]]),
    ]
    for label, values in groups:
        for duplicate in _duplicates(values):
            errors.append("duplicate %s: %s" % (label, duplicate))
    all_ids = [e["id"] for e in inventory] + [e["id"] for e in manifest["sensitivity_inventory"]]
    for duplicate in _duplicates(all_ids):
        errors.append("duplicate cell/entry id across inventories: %s" % duplicate)

    # --- cases, holdout and batch slot bindings -----------------------------
    for case in manifest["cases"]:
        index = case["global_sound_index"]
        if case["partition"] != "development":
            errors.append("holdout/non-development request: %s" % case["id"])
        if holdout["corpus_holdout_first_index"] <= index <= holdout["corpus_holdout_last_index"]:
            errors.append("sealed-holdout request: %s (index %d)" % (case["id"], index))
        if case["id"] != "global-%d" % index:
            errors.append("case id does not match its index: %s" % case["id"])
        if case["batch_block"] != index // case["batch_size"]:
            errors.append("case batch_block inconsistent with index: %s" % case["id"])
        if case["batch_slot"] != index % case["batch_size"]:
            errors.append("case batch_slot inconsistent with index: %s" % case["id"])

    # --- inventory entry bindings ------------------------------------------
    repeats_min = manifest["reliability"]["min_independent_executions_per_cell"]
    by_id = {e["id"]: e for e in inventory}
    for entry in inventory:
        label = entry["id"]
        for case_id in entry["development_cases"]:
            if case_id not in cases:
                errors.append("%s: unknown or holdout case %s" % (label, case_id))
        for operator in entry["operators"]:
            if operator not in operators:
                errors.append("%s: unknown operator %s" % (label, operator))
            elif operators[operator]["family"] != entry["family"]:
                errors.append("%s: operator %s belongs to another family" % (label, operator))
        for item in entry["magnitude"]:
            if item["operator"] not in entry["operators"]:
                errors.append("%s: magnitude names an operator not in the entry" % label)
        for detector in entry["detector_ids"]:
            if detector not in detectors:
                errors.append("%s: unknown detector %s" % (label, detector))
        seam = entry["runtime_seam"]
        if seam not in catalog:
            errors.append("%s: unknown seam %s" % (label, seam))
            continue
        writable = catalog[seam]["writable"]
        disposition = entry["disposition"]
        if disposition in ("runtime_cell", "composition") and not writable:
            errors.append("%s: executable cell bound to non-writable seam %s" % (label, seam))
        if disposition in ("runtime_cell", "composition"):
            if sorted(entry["development_cases"]) != sorted(cases):
                errors.append("%s: executable cell must bind every campaign case" % label)
            if entry["repeats_required"] < repeats_min:
                errors.append("%s: repeats_required below the ratified minimum" % label)
            if entry["expected_event"] != "applied":
                errors.append("%s: executable cell must expect an applied event" % label)
            if entry["runtime_evidence_class"] == "none":
                errors.append("%s: executable cell lacks a runtime evidence class" % label)
            if not entry["invariance_assertions"]:
                errors.append("%s: executable cell lacks invariance assertions" % label)
            if entry["obligation_open"]:
                errors.append("%s: executable cell cannot carry an open obligation" % label)
            if entry["family"] == "signal" and entry["configuration"] is not None:
                if entry["configuration"].get("slot") not in ("$case.batch_slot", None):
                    errors.append("%s: slot must bind the case batch slot" % label)
        if (entry["counts_toward_runtime_total"]) != (disposition in SUCCESS_DISPOSITIONS):
            errors.append("%s: counts_toward_runtime_total inconsistent with disposition" % label)
        if disposition == "refusal":
            if entry["expected_event"] != "refused":
                errors.append("%s: refusal must expect a refused event" % label)
            if entry["repeats_required"] > 1:
                errors.append("%s: refusal is not repeated as a kill" % label)
        if disposition == "deferred_unmeasured":
            if not entry["obligation_open"]:
                errors.append("%s: deferred entry must keep its obligation open" % label)
            if entry["development_cases"]:
                errors.append("%s: deferred entry must not be bound to executed cases" % label)
            if entry["repeats_required"] != 0:
                errors.append("%s: deferred entry must not require executions" % label)
        if disposition == "second_detector":
            shared = by_id.get(entry.get("shares_cell_with", ""))
            if shared is None or shared["disposition"] != "runtime_cell":
                errors.append("%s: second_detector must share an existing runtime_cell" % label)
            elif shared["operators"] != entry["operators"]:
                errors.append("%s: second_detector operators differ from its shared cell" % label)
            if entry["repeats_required"] != 0 or entry["development_cases"]:
                errors.append("%s: second_detector adds no attempts" % label)

    # --- sensitivity entries never executed or counted ----------------------
    for entry in manifest["sensitivity_inventory"]:
        if entry["executed_in_campaign"] or entry["development_cases"]:
            errors.append("%s: sensitivity entry must not be executed" % entry["id"])

    # --- reconcile against the committed family publications ---------------
    publications = {f: load_json(root, p) for f, p in PUBLICATIONS.items()}
    claimed: Dict[Tuple[str, int], str] = {}
    for entry in inventory:
        if entry["source"]["publication"] != PUBLICATIONS[entry["family"]]:
            errors.append("%s: source publication does not match family" % entry["id"])
            continue
        rows = publications[entry["family"]]["fault_matrix"]
        for index in entry["source"]["row_indices"]:
            if index >= len(rows):
                errors.append("%s: source row %d out of range" % (entry["id"], index))
                continue
            row = rows[index]
            key = (entry["family"], index)
            if key in claimed:
                errors.append("%s: source row %s:%d also claimed by %s" % (
                    entry["id"], entry["family"], index, claimed[key]))
            claimed[key] = entry["id"]
            if row["fault"] != entry["source"]["fault"]:
                errors.append("%s: source row %d is fault %s" % (entry["id"], index, row["fault"]))
            if row["operator"] != entry["source"]["publication_operator"]:
                errors.append("%s: publication operator mismatch at row %d" % (entry["id"], index))
            if row["seam"] != entry["source"]["publication_seam"]:
                errors.append("%s: publication seam mismatch at row %d" % (entry["id"], index))
            if not row.get("tripped", False):
                errors.append("%s: source row %d is not a tripped publication row" % (entry["id"], index))
    for family in FAMILIES:
        for index, row in enumerate(publications[family]["fault_matrix"]):
            if (family, index) not in claimed:
                errors.append("publication fault omitted from inventory: %s row %d (%s)" % (
                    family, index, row["fault"]))
    registered_operators = set()
    for family in FAMILIES:
        for row in publications[family]["fault_matrix"]:
            for operator in re.split(r" \+ ", row["operator"]):
                registered_operators.add(operator)
    for operator in operators:
        if operator not in registered_operators:
            errors.append("operator not present in any publication fault row: %s" % operator)

    expected_sens = (
        len(publications["timing"]["floor_probes"])
        + len(publications["timing"]["coverage"]["envelope_and_high_rate"])
        + len(publications["signal"]["normalization_coverage"])
    )
    if len(manifest["sensitivity_inventory"]) != expected_sens:
        errors.append("sensitivity inventory has %d entries, publications have %d" % (
            len(manifest["sensitivity_inventory"]), expected_sens))
    sens_ids = {e["id"] for e in manifest["sensitivity_inventory"]}
    for probe in publications["timing"]["floor_probes"]:
        if "timing:floor-probe:%s" % probe["probe"] not in sens_ids:
            errors.append("floor probe omitted: %s" % probe["probe"])
    for cell in publications["timing"]["coverage"]["envelope_and_high_rate"]:
        if "timing:coverage:%s" % cell["coverage_case"] not in sens_ids:
            errors.append("coverage row omitted: %s" % cell["coverage_case"])
    for cell in publications["signal"]["normalization_coverage"]:
        identifier = "signal:norm-cell:%s:%s" % (cell["fault"], cell["fixture_class"])
        if identifier not in sens_ids:
            errors.append("normalization cell omitted: %s" % identifier)
            continue
        entry = next(e for e in manifest["sensitivity_inventory"] if e["id"] == identifier)
        if cell["status"] == "ineffective_not_detected" and entry["disposition"] != "sensitivity_not_counted":
            errors.append("%s: ineffective cell must not be counted" % identifier)
        if entry["disposition"] == "deferred_unmeasured" and cell["status"] != "detected":
            errors.append("%s: deferred disposition inconsistent with publication" % identifier)
    random_receipts = len(publications["identity"]["random_receipts"])
    for entry in manifest["supporting"]:
        if entry.get("count") != random_receipts:
            errors.append("%s: supporting count differs from publication" % entry["id"])

    # --- normalization must stay open ---------------------------------------
    for entry in inventory:
        if entry["family"] == "signal" and any(
            operator.startswith("norm.") for operator in entry["operators"]
        ):
            if entry["disposition"] not in ("deferred_unmeasured", "refusal"):
                errors.append("%s: normalization fault must be deferred or a refusal" % entry["id"])
    if manifest["normalization_handoff"]["disposition"] == "deferred_unmeasured":
        if not any("normalization" in item for item in
                   manifest["completion"]["open_obligations_after_completion"]):
            errors.append("deferred normalization must remain an open completion obligation")

    # --- proposed additions stay outside the frozen denominator -------------
    for proposal in manifest["proposed_additions"]:
        if proposal["counts_in_frozen_denominator"]:
            errors.append("%s: proposal counted in the frozen denominator" % proposal["id"])
        if proposal["operator"] in operators or proposal["id"] in by_id:
            errors.append("%s: proposal already present in the frozen inventory" % proposal["id"])
        if proposal["runtime_seam"] not in catalog or not catalog[proposal["runtime_seam"]]["writable"]:
            errors.append("%s: proposal bound to an unknown or non-writable seam" % proposal["id"])

    # --- controls ------------------------------------------------------------
    kinds = {c["kind"] for c in manifest["controls"]}
    for kind in ("plain", "empty_plan", "sham_plan", "clean_rerun"):
        if kind not in kinds:
            errors.append("missing mandatory control: %s" % kind)

    # --- denominators and frozen digest -------------------------------------
    expected = _expected_counts(manifest)
    for key, value in expected.items():
        if manifest["expected"].get(key) != value:
            errors.append("expected count mismatch: %s declared %r, recomputed %r" % (
                key, manifest["expected"].get(key), value))
    if manifest["frozen_inventory_digest"]["sha256"] != frozen_digest(manifest):
        errors.append("frozen inventory digest does not match the inventory")

    # --- no measurement is claimed ------------------------------------------
    if manifest["measurement_status"]["runtime_family_measurement_performed"] is not False:
        errors.append("preregistration must not claim a measurement")
    if manifest["host_admission"]["status"] != "not_admitted":
        errors.append("host admission requires committed qualification evidence")

    # --- source digests ------------------------------------------------------
    if check_sources:
        for relative, digest in manifest["sources"].items():
            path = root / relative
            if not path.is_file():
                errors.append("bound source missing: %s" % relative)
            elif file_sha256(root, relative) != digest:
                errors.append("bound source changed (declare a manifest revision): %s" % relative)
        for pin_name in ("dockerfile", "requirements_lock", "upstream_pin"):
            pin = manifest["pins"][pin_name]
            if file_sha256(root, pin["path"]) != pin["sha256"]:
                errors.append("pin changed: %s" % pin_name)
    return errors


def runtime_kill_totals(manifest: Dict[str, Any],
                        outcomes: Dict[Tuple[str, str, int], str]) -> Dict[str, int]:
    """Count successful runtime kills from hypothetical per-execution outcomes.

    ``outcomes`` maps (entry id, case id, repeat) to a status. Only an entry
    whose disposition is ``runtime_cell`` with the status ``detected`` in every
    required repeat of a case counts. Refusals, compositions, deferred and
    ineffective results never contribute. This is an accounting rule, not a
    measurement.
    """
    cases = [c["id"] for c in manifest["cases"]]
    repeats = manifest["reliability"]["min_independent_executions_per_cell"]
    totals = {"kills": 0, "open": 0, "excluded": 0}
    for entry in manifest["inventory"]:
        if entry["disposition"] != "runtime_cell":
            totals["excluded"] += len(entry["development_cases"])
            continue
        for case in cases:
            statuses = [outcomes.get((entry["id"], case, r)) for r in range(1, repeats + 1)]
            if all(status == "detected" for status in statuses):
                totals["kills"] += 1
            else:
                totals["open"] += 1
    return totals


def validate_manifest(manifest: Dict[str, Any], root: Path = ROOT,
                      check_sources: bool = True) -> List[str]:
    schema = load_json(root, SCHEMA_PATH)
    errors = schema_errors(manifest, schema, schema)
    if errors:
        return errors
    return semantic_errors(manifest, root, check_sources)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=MANIFEST_PATH,
                        help="manifest path relative to the repository root")
    parser.add_argument("--no-source-digests", action="store_true",
                        help="skip bound-source digest checks (structure only)")
    args = parser.parse_args(argv)
    manifest = load_json(ROOT, args.manifest)
    errors = validate_manifest(manifest, ROOT, not args.no_source_digests)
    if errors:
        for error in errors:
            print("FAIL: " + error, file=sys.stderr)
        return 1
    expected = manifest["expected"]
    print(json.dumps({
        "status": "VALID_PREREGISTRATION",
        "measured": False,
        "inventory_entries": expected["inventory_entries"],
        "source_rows": expected["inventory_source_rows"],
        "runtime_fault_cells": expected["runtime_fault_cells"],
        "worker_attempts": expected["worker_attempts"],
        "frozen_inventory_digest": manifest["frozen_inventory_digest"]["sha256"],
        "source_digests_checked": not args.no_source_digests,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
