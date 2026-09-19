"""Read-only evidence board: inventory completeness, byte integrity and freshness.

The registry is policy, results are producer assertions. Validation cannot prove
that a producer ran the named engine. No release verdict or quality scalar exists.
"""

from __future__ import annotations

import copy
import hashlib
import html
import json
import math
import re
import stat
import struct
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

from .artifacts import canonical_bytes, loads, validate_artifact
from .contract import repository_root
from .directed import resolve_patch, validate_manifest
from .scorecard import VERDICTS, validate_row

OUTCOMES = ("PASS", "FAIL", "NO VERDICT", "NOT RUN", "STALE")
PARTITIONS = ("development", "holdout")
SCHEMA_PATH = "spec/schemas/case-registry-v1.schema.json"
# These bytes participate even when a producer forgets to list its evaluator.
# Generated views and result paths never participate: there is no self-hash.
EVALUATOR_INPUTS = (
    SCHEMA_PATH,
    "src/torchsynth_voice/case_registry.py",
    "src/torchsynth_voice/artifacts.py",
    "src/torchsynth_voice/scorecard.py",
    "src/torchsynth_voice/directed.py",
    "src/torchsynth_voice/contract.py",
    "src/torchsynth_voice/identity.py",
    "src/torchsynth_voice/inventory.py",
    "spec/schemas/scorecard-row-v1.schema.json",
    "spec/schemas/render-artifact-v1.schema.json",
)


class RegistryError(ValueError):
    """Invalid registry policy, provenance, or attempted evidence."""


def _require(condition, reason):
    if not condition:
        raise RegistryError(reason)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encode(value) -> bytes:
    canonical_bytes(value)  # Reject non-JSON and nonfinite Python values as well.
    return (
        json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode()


@lru_cache(maxsize=1)
def _schema():
    return loads((repository_root() / SCHEMA_PATH).read_bytes())


def _structure(value, schema, where="$"):
    """The small, tested Draft 2020-12 subset used by our local schema."""
    if "$ref" in schema:
        if schema["$ref"] == "urn:gf180-torchsynth:scorecard-row:v1":
            validate_row(value)
            return
        _structure(value, _schema()["$defs"][schema["$ref"].split("/")[-1]], where)
        return
    kinds = {
        "object": (dict,),
        "array": (list,),
        "string": (str,),
        "number": (int, float),
        "integer": (int,),
        "null": (type(None),),
    }
    if "type" in schema:
        names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        _require(
            any(type(value) in kinds[name] for name in names), f"{where}: wrong type"
        )
    if "const" in schema:
        _require(value == schema["const"], f"{where}: wrong constant")
    if "enum" in schema:
        _require(value in schema["enum"], f"{where}: unknown value")
    if isinstance(value, dict):
        _require(
            set(schema.get("required", [])) <= value.keys(), f"{where}: missing field"
        )
        _require(len(value) >= schema.get("minProperties", 0), f"{where}: empty object")
        for key, child in value.items():
            if "propertyNames" in schema:
                _structure(key, schema["propertyNames"], where)
            definition = schema.get("properties", {}).get(
                key, schema.get("additionalProperties", {})
            )
            _require(definition is not False, f"{where}: unknown field {key}")
            _structure(child, definition, where + "." + key)
    if isinstance(value, list):
        _require(len(value) >= schema.get("minItems", 0), f"{where}: too few items")
        if schema.get("uniqueItems"):
            _require(
                len({canonical_bytes(v) for v in value}) == len(value),
                f"{where}: duplicates",
            )
        for child in value:
            _structure(child, schema.get("items", {}), where + "[]")
    if isinstance(value, str) and "pattern" in schema:
        pattern = schema["pattern"]
        matches = (
            re.search(pattern, value)
            if pattern == r"\S"
            else re.fullmatch(pattern, value)
        )
        _require(matches is not None, f"{where}: invalid text")
    if type(value) in (int, float):
        for key, okay in (
            ("minimum", lambda bound: value >= bound),
            ("maximum", lambda bound: value <= bound),
            ("exclusiveMinimum", lambda bound: value > bound),
        ):
            if key in schema:
                _require(okay(schema[key]), f"{where}: out of range")


def validate_document(value, kind="registry"):
    canonical_bytes(value)
    _structure(value, _schema() if kind == "registry" else _schema()["$defs"][kind])


def _path(root, relative):
    _structure(relative, _schema()["$defs"]["path"])
    path = Path(root).resolve()
    for part in relative.split("/"):
        path /= part
        _require(not path.is_symlink(), "symlink reference refused")
    return path


def _read(root, relative):
    path = _path(root, relative)
    info = path.stat()
    _require(
        stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "nonregular or aliased file"
    )
    return path.read_bytes()


def _key(row):
    return (
        row["trace"],
        row["property"],
        row["unit"],
        row["estimator"]["name"],
        row["estimator"]["version"],
    )


def validate_registry(registry):
    validate_document(registry)
    keys = [_key(row) for row in registry["required_rows"]]
    _require(len(set(keys)) == len(keys), "duplicate required row")
    for row in registry["required_rows"]:
        tolerance = row["tolerance"]["value"]
        _require(tolerance is None or tolerance >= 0, "negative tolerance")
    for group in registry["inputs"].values():
        for path in group["files"]:
            _require(
                not path.startswith(registry["evidence_root"] + "/")
                and path not in ("docs/scorecard.json", "docs/SCORECARD.md")
                and "holdout" not in path.split("/"),
                "provenance cannot read results, views or holdout",
            )
    for source in registry["sources"]:
        path = source["manifest"]["path"]
        _require(
            not path.startswith(registry["evidence_root"] + "/")
            and "holdout" not in path.split("/"),
            "manifest must be public allocation metadata",
        )


def expand_registry(registry, root: Path) -> list[dict]:
    """Expand ALL preregistered cases; never select by existing result files."""
    validate_registry(registry)
    cases = []
    for source in registry["sources"]:
        reference, family = source["manifest"], source["family"]
        data = _read(root, reference["path"])
        _require(digest(data) == reference["sha256"], "manifest hash mismatch")
        manifest = loads(data)
        _require(
            isinstance(manifest, dict) and type(manifest.get("schema_version")) is int,
            "manifest version must be an integer",
        )
        if family == "random":
            _require(
                manifest["name"] == reference["identity"], "random identity mismatch"
            )
            _require(
                manifest["schema_version"] == 1
                and manifest["identity"] == "global_sound_index",
                "unsupported random manifest",
            )
            allocation = []
            for span in manifest["cases"]:
                start, stop = span["start_inclusive"], span["stop_exclusive"]
                _require(
                    type(start) is int and type(stop) is int and 0 <= start < stop,
                    "invalid random range",
                )
                allocation.extend(
                    (f"random-{n:06d}", span["split"], {"global_sound_index": n})
                    for n in range(start, stop)
                )
            rules = manifest["rules"]
            _require(
                len(allocation) == rules["case_count"]
                and all(
                    sum(p == partition for _, p, _ in allocation)
                    == rules[f"{partition}_count"]
                    for partition in PARTITIONS
                ),
                "random allocation counts disagree",
            )
        elif family == "directed":
            validate_manifest(manifest)
            _require(
                manifest["identity"]["version"] == reference["identity"],
                "directed identity mismatch",
            )
            allocation = [
                (case["id"], "development", resolve_patch(manifest, case))
                for case in manifest["cases"]
            ]
        else:
            _require(
                registry["engine"] == "synthetic",
                "synthetic fixtures require synthetic engine",
            )
            _require(
                set(manifest) == {"schema_version", "cases"}
                and manifest["schema_version"] == 1,
                "invalid synthetic allocation",
            )
            allocation = []
            for case in manifest["cases"]:
                _require(set(case) == {"id", "partition"}, "invalid synthetic case")
                allocation.append((case["id"], case["partition"], case))
        for name, partition, fixture in allocation:
            case = {
                "id": name,
                "partition": partition,
                "family": family,
                "fixture_identity": digest(
                    canonical_bytes(
                        {
                            "manifest": reference,
                            "id": name,
                            "partition": partition,
                            "fixture": fixture,
                        }
                    )
                ),
            }
            validate_document(case, "case")
            cases.append(case)
    _require(len({case["id"] for case in cases}) == len(cases), "duplicate case ID")
    _require(bool(cases), "empty registry")
    return sorted(
        cases, key=lambda case: (case["partition"], case["family"], case["id"])
    )


def _access(partition, authorization):
    _require(partition in PARTITIONS, "unknown partition")
    if partition == "holdout":
        _require(
            isinstance(authorization, str) and bool(authorization.strip()),
            "holdout refused: explicit authorization/audit reason required",
        )


def case_directory(registry, case, root):
    """Portable locator for an opaque case ID (including directed colons/arrows)."""
    return (
        Path(root)
        / registry["evidence_root"]
        / case["partition"]
        / ("case-" + digest(case["id"].encode()))
    )


def covered_inputs(registry, case, root, *, holdout_authorization=None):
    """Fingerprint exact covered bytes and policy, excluding evidence and views."""
    _access(case["partition"], holdout_authorization)
    validate_registry(registry)
    covered = {}
    for group in registry["inputs"].values():
        _require(bool(group["files"]), "provenance not available: " + group["identity"])
        for path in group["files"]:
            covered["project/" + path] = digest(_read(root, path))
    # Evaluator files live beside this module, including with temporary control roots.
    for path in EVALUATOR_INPUTS:
        covered["evaluator/" + path] = digest(_read(repository_root(), path))
    for source in registry["sources"]:
        path = source["manifest"]["path"]
        covered["project/" + path] = digest(_read(root, path))
    policy = {
        key: registry[key]
        for key in (
            "schema_version",
            "id",
            "rubric",
            "engine",
            "inputs",
            "configuration",
            "required_rows",
        )
    }
    fingerprint = digest(
        b"torchsynth-case-result-v1\n"
        + canonical_bytes({"case": case, "policy": policy, "covered_inputs": covered})
    )
    return fingerprint, dict(sorted(covered.items()))


def row_outcome(rows):
    """Refusal outranks failure, then failure outranks success; retain raw counts."""
    states = {row["verdict"] for row in rows}
    if not rows or states & {"NO VERDICT", "MISSING EVIDENCE"}:
        return "NO VERDICT"
    return "FAIL" if "FAIL" in states else "PASS"


def measurement_rows(record, registry, reference):
    """Apply only declared absolute-interval limits; no default numeric tolerances."""
    validate_registry(registry)
    validate_document(record, "measurement")
    expected = {_key(row): row for row in registry["required_rows"]}
    rows, seen = [], set()
    for metric in record["measurements"]:
        key = _key(metric)
        _require(key in expected and key not in seen, "extra or duplicate measurement")
        seen.add(key)
        requirement = expected[key]
        measured = metric["status"] == "valid"
        _require(
            (metric["value"] is not None) == measured,
            "invalid measurement value/status",
        )
        _require(
            metric["status"] != "missing" or metric["coverage"] == "none",
            "missing coverage",
        )
        limits = (requirement["expected"]["value"], requirement["tolerance"]["value"])
        decidable = measured and metric["coverage"] == "complete" and None not in limits
        if decidable:
            verdict = (
                "PASS"
                if abs(Fraction(metric["value"]) - Fraction(limits[0]))
                <= Fraction(limits[1])
                else "FAIL"
            )
            validity = {"status": "valid", "reason": metric["reason"]}
        else:
            verdict = (
                "MISSING EVIDENCE" if metric["status"] == "missing" else "NO VERDICT"
            )
            validity = {
                "status": "insufficient" if measured else metric["status"],
                "reason": "unfrozen_rubric_or_incomplete_coverage"
                if measured
                else metric["reason"],
            }
        row = {
            "schema_version": 1,
            "case": record["case"],
            **copy.deepcopy(requirement),
            "rubric": registry["rubric"],
            "observed": metric["value"] if decidable else None,
            "validity": validity,
            "coverage": metric["coverage"],
            "verdict": verdict,
            "artifact": {
                "identity": reference["identity"],
                "sha256": reference["sha256"],
            },
        }
        validate_row(row)
        rows.append(row)
    return rows


def render_reference(directory, reference):
    """Validate exact metadata and every render payload, without creating a store."""
    data = _read(directory, reference["ref"])
    _require(digest(data) == reference["sha256"], "render metadata hash mismatch")
    record = loads(data)
    validate_artifact(record)
    _require(record["artifact_id"] == reference["identity"], "render identity mismatch")
    base = _path(directory, reference["ref"]).parent
    for file in [record["audio"]["value"]["file"], *record["traces"]["value"].values()]:
        payload = _read(base, file["ref"])
        _require(
            len(payload) == file["size_bytes"] and digest(payload) == file["sha256"],
            "render payload size/hash mismatch",
        )
    return {"identity": record["artifact_id"], "sha256": digest(data)}


def _captures(record, directory, synthetic):
    roles = set()
    for capture in record["captures"]:
        key = (capture["trace"], capture["role"])
        _require(key not in roles, "duplicate capture trace/role")
        roles.add(key)
        payload = _read(directory, capture["ref"])
        width, code = (4, "<f") if capture["encoding"] == "f32le" else (8, "<d")
        _require(
            len(payload) == capture["size_bytes"] == width * capture["sample_count"]
            and digest(payload) == capture["sha256"],
            "capture size/hash mismatch",
        )
        _require(
            all(math.isfinite(value) for (value,) in struct.iter_unpack(code, payload)),
            "nonfinite capture",
        )
    if not synthetic:
        for measurement in record["measurements"]:
            if measurement["status"] == "valid":
                _require(
                    all(
                        (measurement["trace"], role) in roles
                        for role in ("reference", "candidate")
                    ),
                    "measured project evidence requires both captures for its trace",
                )


def _evidence(result, registry, case, directory):
    indexed, verified_rows = {}, []
    for reference in result["artifacts"]:
        identity = reference["identity"]
        _require(identity not in indexed, "duplicate artifact identity")
        indexed[identity] = reference
        if reference["kind"] == "render":
            render_reference(directory, reference)
            continue
        data = _read(directory, reference["ref"])
        _require(
            digest(data) == reference["sha256"] and identity == "cm1-" + digest(data),
            "measurement hash/identity mismatch",
        )
        record = loads(data)
        validate_document(record, "measurement")
        _require(
            record["case"] == {k: case[k] for k in ("id", "partition")},
            "measurement case mismatch",
        )
        _require(
            record["fingerprint"] == result["fingerprint"],
            "measurement input binding mismatch",
        )
        _captures(record, directory, case["family"] == "synthetic")
        verified_rows.extend(measurement_rows(record, registry, reference))
    # No dict overwrite: duplicates across records are also rejected.
    _require(
        len({_key(row) for row in verified_rows}) == len(verified_rows),
        "conflicting measurement records",
    )
    _require(
        sorted(verified_rows, key=_key) == sorted(result["rows"], key=_key),
        "rows do not match verified measurement records",
    )


def _case_result(registry, case, root, authorization):
    expected = {_key(row) for row in registry["required_rows"]}
    board = {
        **case,
        "access": "inspected",
        "engine": registry["engine"],
        "outcome": "NO VERDICT",
        "reason": "unusable_result",
        "expected_rows": len(expected),
        "present_rows": 0,
        "missing_rows": len(expected),
        "coverage_complete": False,
        "row_counts": dict.fromkeys(VERDICTS, 0),
        "rows": [],
        "measured": False,
        "submitted_rows": [],
        "result_sha256": None,
    }
    try:
        # Access is checked before even stat-ing the partition's evidence directory.
        _access(case["partition"], authorization)
        relative = str(case_directory(registry, case, Path(".")))
        directory = _path(root, relative)
        try:
            info = directory.stat()
        except FileNotFoundError:
            board.update(outcome="NOT RUN", reason="no_attempt_directory")
            return board
        _require(stat.S_ISDIR(info.st_mode), "attempt path is not a directory")
        data = _read(directory, "result.json")
        board["result_sha256"] = digest(data)
        result = loads(data)
        validate_document(result, "result")
        _require(
            result["configuration_sha256"]
            == digest(canonical_bytes(result["configuration"])),
            "configuration hash mismatch",
        )
        _require(
            result["case"]["id"] == case["id"]
            and result["case"]["partition"] == case["partition"]
            and result["case"]["family"] == case["family"],
            "wrong result case/partition/family",
        )
        _require(
            result["access"]["partition"] == case["partition"],
            "wrong result access partition",
        )
        _access(case["partition"], result["access"]["holdout_authorization"])
        rows = result["rows"]
        for row in rows:
            _require(
                row["case"] == {k: case[k] for k in ("id", "partition")},
                "wrong row case/partition",
            )
            _require(row["rubric"] == result["rubric"], "conflicting row rubric")
        keys = [_key(row) for row in rows]
        board.update(
            submitted_rows=rows,
            row_counts={
                state: sum(row["verdict"] == state for row in rows)
                for state in VERDICTS
            },
            present_rows=len(expected & set(keys)),
            missing_rows=len(expected - set(keys)),
        )
        _require(len(keys) == len(set(keys)), "duplicate/conflicting row")
        fingerprint, covered = covered_inputs(
            registry, case, root, holdout_authorization=authorization
        )
        current = (
            result["fingerprint"] == fingerprint
            and result["covered_inputs"] == covered
            and result["case"] == case
            and result["rubric"] == registry["rubric"]
            and result["reference"] == registry["inputs"]["reference"]["identity"]
            and result["implementation"]
            == {
                "engine": registry["engine"],
                "identity": registry["inputs"]["implementation"]["identity"],
            }
            and result["configuration"] == registry["configuration"]
        )
        if not current:
            board.update(outcome="STALE", reason="covered_inputs_changed")
            return board
        _require(set(keys) == expected, "missing or extra required row")
        _evidence(result, registry, case, directory)
        outcome = row_outcome(rows)
        _require(result["outcome"] == outcome, "declared outcome disagrees with rows")
        board.update(
            rows=rows,
            outcome=outcome,
            reason="validated_result",
            coverage_complete=all(row["coverage"] == "complete" for row in rows),
            measured=any(row["verdict"] in ("PASS", "FAIL") for row in rows),
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        # Stable reasons, never absolute host paths from OS exceptions.
        board["reason"] = (
            str(error) if isinstance(error, RegistryError) else type(error).__name__
        )
    return board


def evaluate(
    registry, root, *, partition="development", holdout_authorization=None, command=None
):
    """Generate a partition-isolated board. Never infer uninspected outcomes."""
    _access(partition, holdout_authorization)
    cases = expand_registry(registry, root)
    board_cases = []
    for case in cases:
        if case["partition"] == partition:
            board_cases.append(
                _case_result(registry, case, root, holdout_authorization)
            )
        else:
            board_cases.append(
                {
                    **case,
                    "access": "sealed"
                    if case["partition"] == "holdout"
                    else "not-selected",
                    "engine": registry["engine"],
                    "outcome": None,
                    "reason": "partition_not_inspected",
                    "expected_rows": len(registry["required_rows"]),
                    "present_rows": None,
                    "missing_rows": None,
                    "coverage_complete": None,
                    "row_counts": None,
                    "rows": [],
                    "submitted_rows": [],
                    "measured": None,
                    "result_sha256": None,
                }
            )
    partitions = {}
    for name in PARTITIONS:
        allocated = [case for case in board_cases if case["partition"] == name]
        inspected = [case for case in allocated if case["access"] == "inspected"]
        partitions[name] = {
            "allocated_cases": len(allocated),
            "inspected_cases": len(inspected),
            "sealed_cases": sum(case["access"] == "sealed" for case in allocated),
            "expected_rows": sum(case["expected_rows"] for case in allocated),
            "inspected_expected_rows": sum(case["expected_rows"] for case in inspected),
            "outcomes": {
                state: sum(case["outcome"] == state for case in inspected)
                for state in OUTCOMES
            },
            "raw_row_counts": {
                state: sum(case["row_counts"][state] for case in inspected)
                for state in VERDICTS
            },
            "actual_measured_cases": sum(
                bool(case["measured"]) and case["family"] != "synthetic"
                for case in inspected
            ),
            "synthetic_measured_cases": sum(
                bool(case["measured"]) and case["family"] == "synthetic"
                for case in inspected
            ),
        }
    hardware = {
        engine: sum(
            bool(case["measured"])
            and case["family"] != "synthetic"
            and case["engine"] == engine
            for case in board_cases
        )
        for engine in ("integrated-rtl", "board", "silicon")
    }
    return {
        "schema_version": 1,
        "registry_id": registry["id"],
        "registry_sha256": digest(encode(registry)),
        "audit": {
            "partition": partition,
            "holdout_authorization": holdout_authorization,
            "command": command or ["case_registry.evaluate", "partition=" + partition],
        },
        "required_rows": registry["required_rows"],
        "rubric": registry["rubric"],
        "partitions": partitions,
        "hardware_measured_cases_in_inspected_partition": hardware,
        "cases": board_cases,
    }


def render_markdown(board):
    def cell(value):
        return (
            html.escape("null" if value is None else str(value))
            .replace("|", "\\|")
            .replace("\n", " ")
            .replace("\r", " ")
        )

    lines = [
        "# Evidence scorecard",
        "",
        "Generated from the versioned case registry and validated per-case results.",
        "Do not edit this view. No product verdict or omnibus quality score is computed.",
        "NOT RUN is not a hardware PASS. Prepared fixtures are not rendered signal coverage.",
        "",
        f"Inspected partition: **{board['audit']['partition']}**. Other partitions were not read.",
        "Sealed holdout allocations carry no inferred measurement outcome.",
        "",
    ]
    if board["audit"]["holdout_authorization"]:
        lines += [
            "**EXPLICIT HOLDOUT ACCESS:** "
            + cell(board["audit"]["holdout_authorization"]),
            "",
        ]
    for engine, count in board[
        "hardware_measured_cases_in_inspected_partition"
    ].items():
        lines.append(
            f"{engine}: {count} measured cases in the inspected partition; "
            + (
                "no measured evidence is registered here."
                if not count
                else "producer evidence, not physical signoff."
            )
        )
    lines += [
        "",
        "| Partition | Allocated | Inspected | Sealed | Required rows | PASS | FAIL | NO VERDICT | NOT RUN | STALE |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, counts in board["partitions"].items():
        values = [
            name,
            counts["allocated_cases"],
            counts["inspected_cases"],
            counts["sealed_cases"],
            counts["expected_rows"],
            *[counts["outcomes"][state] for state in OUTCOMES],
        ]
        lines.append("| " + " | ".join(map(cell, values)) + " |")
    lines += [
        "",
        "Raw row verdicts remain in JSON, including MISSING EVIDENCE. Refusals have null observations.",
        "Synthetic controls are excluded from actual Voice and hardware measurement counts.",
        "",
        "| Required trace | Property | Unit | Estimator | Rubric |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in board["required_rows"]:
        values = [
            row["trace"],
            row["property"],
            row["unit"],
            row["estimator"]["name"] + "@" + row["estimator"]["version"],
            board["rubric"]["id"] + "@" + board["rubric"]["version"],
        ]
        lines.append("| " + " | ".join(map(cell, values)) + " |")
    lines += [
        "",
        "| Case | Family | Partition | Outcome / access | Present / required rows | Reason |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for case in board["cases"]:
        values = [
            case["id"],
            case["family"],
            case["partition"],
            case["outcome"] or case["access"],
            f"{case['present_rows'] if case['present_rows'] is not None else '?'} / {case['expected_rows']}",
            case["reason"],
        ]
        lines.append("| " + " | ".join(map(cell, values)) + " |")
    accepted = [(case["id"], row) for case in board["cases"] for row in case["rows"]]
    if accepted:
        lines += [
            "",
            "Current validated rows (native units; null means no accepted observation):",
            "",
            "| Case | Trace | Property | Unit | Observed | Expected | Tolerance | Row verdict |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | --- |",
        ]
        for name, row in accepted:
            values = [
                name,
                row["trace"],
                row["property"],
                row["unit"],
                row["observed"],
                row["expected"]["value"],
                row["tolerance"]["value"],
                row["verdict"],
            ]
            lines.append("| " + " | ".join(map(cell, values)) + " |")
    return "\n".join(lines) + "\n"
