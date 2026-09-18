"""Version 1 scorecard validation and counts, with no measurement or release verdict.

Artifact references are opaque identities and digests, not verified evidence.
See spec/SCORECARD-CONTRACT.md for producer obligations and JSON Schema limits.
"""

from __future__ import annotations

import copy
import json
import math
import re
import sys
from typing import Any, Iterable

VERDICTS = ("PASS", "FAIL", "NO VERDICT", "MISSING EVIDENCE")
COVERAGE = ("complete", "partial", "none")
PARTITIONS = ("development", "holdout")
ROW_FIELDS = {
    "schema_version",
    "case",
    "trace",
    "property",
    "estimator",
    "rubric",
    "unit",
    "expected",
    "observed",
    "tolerance",
    "validity",
    "coverage",
    "verdict",
    "artifact",
}


class ScorecardError(ValueError):
    """A scorecard violates the versioned structural or semantic contract."""


def _object(value: Any, fields: set[str], path: str) -> None:
    if not isinstance(value, dict):
        raise ScorecardError(f"{path} must be an object")
    if set(value) != fields:
        raise ScorecardError(
            f"{path} requires exactly these fields: {', '.join(sorted(fields))}"
        )


def _text(value: Any, path: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ScorecardError(f"{path} must be nonblank text")


def _choice(value: Any, choices: Iterable[str], path: str) -> None:
    if not isinstance(value, str) or value not in choices:
        raise ScorecardError(f"{path} must be one of {tuple(choices)}")


def _number(value: Any, path: str) -> None:
    # Bound integers too, so the wire contract is portable to binary64 readers.
    if (
        type(value) not in (int, float)
        or not -sys.float_info.max <= value <= sys.float_info.max
        or not math.isfinite(value)
    ):
        raise ScorecardError(
            f"{path} must be a finite JSON number in the binary64 range"
        )


def _version(value: Any) -> None:
    if type(value) is not int or value != 1:
        raise ScorecardError("schema_version must be integer 1")


def _rubric(value: Any) -> None:
    _object(value, {"id", "version"}, "rubric")
    for key in value:
        _text(value[key], f"rubric.{key}")


def validate_row(row: dict[str, Any]) -> None:
    """Reject missing provenance, unavailable results disguised as numbers, or bad states.

    PASS/FAIL are producer assertions under the named rubric. This does not
    execute an estimator, recompute a verdict, or verify artifact bytes.
    """
    _object(row, ROW_FIELDS, "row")
    _version(row["schema_version"])
    _object(row["case"], {"id", "partition"}, "case")
    _text(row["case"]["id"], "case.id")
    _choice(row["case"]["partition"], PARTITIONS, "case.partition")
    for key in ("trace", "property", "unit"):
        _text(row[key], key)
    _object(row["estimator"], {"name", "version"}, "estimator")
    for key in row["estimator"]:
        _text(row["estimator"][key], f"estimator.{key}")
    _rubric(row["rubric"])
    for key in ("expected", "tolerance"):
        _object(row[key], {"value", "source"}, key)
        _text(row[key]["source"], f"{key}.source")
        if row[key]["value"] is not None:
            _number(row[key]["value"], f"{key}.value")
    if row["tolerance"]["value"] is not None and row["tolerance"]["value"] < 0:
        raise ScorecardError("tolerance.value must be nonnegative")
    if row["observed"] is not None:
        _number(row["observed"], "observed")
    _object(row["validity"], {"status", "reason"}, "validity")
    _text(row["validity"]["reason"], "validity.reason")
    _choice(row["coverage"], COVERAGE, "coverage")
    _choice(row["verdict"], VERDICTS, "verdict")
    _object(row["artifact"], {"identity", "sha256"}, "artifact")
    _text(row["artifact"]["identity"], "artifact.identity")
    digest = row["artifact"]["sha256"]
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ScorecardError("artifact.sha256 must be 64 lowercase hexadecimal digits")

    verdict = row["verdict"]
    statuses = {
        "PASS": ("valid",),
        "FAIL": ("valid",),
        "NO VERDICT": ("invalid", "insufficient"),
        "MISSING EVIDENCE": ("missing",),
    }
    _choice(row["validity"]["status"], statuses[verdict], "validity.status")
    if verdict in ("PASS", "FAIL"):
        if row["coverage"] != "complete" or any(
            value is None
            for value in (
                row["observed"],
                row["expected"]["value"],
                row["tolerance"]["value"],
            )
        ):
            raise ScorecardError(
                "PASS/FAIL require complete coverage and measured values"
            )
    elif row["observed"] is not None:
        raise ScorecardError(
            "refused or missing evidence requires observed=null, never zero"
        )
    if verdict == "MISSING EVIDENCE" and row["coverage"] != "none":
        raise ScorecardError("missing evidence requires coverage=none")


def _counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "total_rows": len(rows),
        "verdicts": {
            state: sum(row["verdict"] == state for row in rows) for state in VERDICTS
        },
        "coverage": {
            state: sum(row["coverage"] == state for row in rows) for state in COVERAGE
        },
    }


def summarize_rows(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Count a compatible comparison family; never average measured values.

    Properties, units, estimators (including version), rubrics (including
    version), and partitions must agree. Cases and traces may differ.
    """
    rows = list(rows)
    for row in rows:
        validate_row(row)
    if rows:
        first = rows[0]
        for row in rows[1:]:
            if row["case"]["partition"] != first["case"]["partition"] or any(
                row[key] != first[key]
                for key in ("property", "unit", "estimator", "rubric")
            ):
                raise ScorecardError(
                    "cannot summarize incompatible comparison families"
                )
    return _counts(rows)


def make_report(
    rows: Iterable[dict[str, Any]], *, partition: str, rubric: dict[str, str]
) -> dict[str, Any]:
    """Copy raw rows into a single partition/rubric report with independent counts.

    A report may inventory different properties and units, but performs no
    numeric aggregation or whole-product verdict. Empty reports count nothing.
    """
    rows = copy.deepcopy(list(rows))
    for row in rows:
        validate_row(row)
    report = {
        "schema_version": 1,
        "partition": partition,
        "rubric": copy.deepcopy(rubric),
        "rows": rows,
        "summary": _counts(rows),
    }
    validate_report(report)
    return report


def validate_report(report: dict[str, Any]) -> None:
    """Validate raw rows, partition/rubric isolation, and exact summary counts."""
    _object(
        report, {"schema_version", "partition", "rubric", "rows", "summary"}, "report"
    )
    _version(report["schema_version"])
    _choice(report["partition"], PARTITIONS, "partition")
    _rubric(report["rubric"])
    if not isinstance(report["rows"], list):
        raise ScorecardError("rows must be an array")
    for row in report["rows"]:
        validate_row(row)
        if row["case"]["partition"] != report["partition"]:
            raise ScorecardError(
                "report cannot mix or relabel development/holdout partitions"
            )
        if row["rubric"] != report["rubric"]:
            raise ScorecardError("report cannot mix or relabel rubrics")
    summary = report["summary"]
    _object(summary, {"total_rows", "verdicts", "coverage"}, "summary")
    _object(summary["verdicts"], set(VERDICTS), "summary.verdicts")
    _object(summary["coverage"], set(COVERAGE), "summary.coverage")
    counts = [
        summary["total_rows"],
        *summary["verdicts"].values(),
        *summary["coverage"].values(),
    ]
    if any(type(value) is not int or value < 0 for value in counts):
        raise ScorecardError("summary counts must be nonnegative integers")
    if summary != _counts(report["rows"]):
        raise ScorecardError("summary counts do not match raw rows")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ScorecardError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ScorecardError(f"non-finite JSON number: {value}")


def _from_json(document: str) -> Any:
    try:
        return json.loads(
            document, parse_constant=_reject_constant, object_pairs_hook=_unique_object
        )
    except (ValueError, TypeError) as error:
        raise ScorecardError(str(error)) from error


def row_from_json(document: str) -> dict[str, Any]:
    row = _from_json(document)
    validate_row(row)
    return row


def row_to_json(row: dict[str, Any]) -> str:
    validate_row(row)
    return json.dumps(row, allow_nan=False, sort_keys=True, indent=2)


def report_from_json(document: str) -> dict[str, Any]:
    report = _from_json(document)
    validate_report(report)
    return report


def report_to_json(report: dict[str, Any]) -> str:
    validate_report(report)
    return json.dumps(report, allow_nan=False, sort_keys=True, indent=2)
