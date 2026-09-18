from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.scorecard import (  # noqa: E402
    ScorecardError,
    make_report,
    report_from_json,
    report_to_json,
    row_from_json,
    row_to_json,
    summarize_rows,
    validate_report,
    validate_row,
)


def example_row(verdict: str = "PASS") -> dict:
    """Synthetic contract examples, never measurements of TorchSynth or RTL."""
    statuses = {
        "PASS": "valid",
        "FAIL": "valid",
        "NO VERDICT": "insufficient",
        "MISSING EVIDENCE": "missing",
    }
    measured = verdict in ("PASS", "FAIL")
    return {
        "schema_version": 1,
        "case": {"id": "synthetic-tone", "partition": "development"},
        "trace": "synthetic-output",
        "property": "frequency",
        "estimator": {"name": "synthetic-estimator", "version": "1"},
        "rubric": {"id": "synthetic-only", "version": "1"},
        "unit": "Hz",
        "expected": {"value": 100, "source": "synthetic fixture definition"},
        "observed": (100 if verdict == "PASS" else 102) if measured else None,
        "tolerance": {"value": 1, "source": "synthetic-only test rubric"},
        "validity": {
            "status": statuses[verdict],
            "reason": "Synthetic test of the result contract: " + verdict,
        },
        "coverage": "complete" if measured else "none",
        "verdict": verdict,
        "artifact": {
            "identity": "synthetic-contract-fixture",
            "sha256": hashlib.sha256(
                b"synthetic contract fixture, not audio"
            ).hexdigest(),
        },
    }


def change(row: dict, path: tuple[str, ...], value: object) -> dict:
    result = copy.deepcopy(row)
    target = result
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return result


class ScorecardRowTests(unittest.TestCase):
    def test_all_four_states_round_trip(self) -> None:
        for verdict in ("PASS", "FAIL", "NO VERDICT", "MISSING EVIDENCE"):
            with self.subTest(verdict=verdict):
                row = example_row(verdict)
                validate_row(row)
                self.assertEqual(row_from_json(row_to_json(row)), row)

    def test_every_field_is_required_even_when_values_are_unavailable(self) -> None:
        for verdict in ("PASS", "NO VERDICT", "MISSING EVIDENCE"):
            row = example_row(verdict)
            paths = [(key,) for key in row]
            paths += [
                (key, nested)
                for key, value in row.items()
                if isinstance(value, dict)
                for nested in value
            ]
            for path in paths:
                with self.subTest(verdict=verdict, path=path):
                    bad = copy.deepcopy(row)
                    target = bad if len(path) == 1 else bad[path[0]]
                    del target[path[-1]]
                    with self.assertRaises(ScorecardError):
                        validate_row(bad)

    def test_required_text_cannot_be_blank(self) -> None:
        paths = [
            ("unit",),
            ("trace",),
            ("property",),
            ("case", "id"),
            ("estimator", "name"),
            ("estimator", "version"),
            ("rubric", "id"),
            ("rubric", "version"),
            ("expected", "source"),
            ("tolerance", "source"),
            ("validity", "reason"),
            ("artifact", "identity"),
        ]
        for path in paths:
            for value in ("", " \t\n", None, 1):
                with self.subTest(path=path, value=value):
                    with self.assertRaises(ScorecardError):
                        validate_row(change(example_row(), path, value))

    def test_non_finite_numbers_and_boolean_measurements_are_rejected(self) -> None:
        for path in (("observed",), ("expected", "value"), ("tolerance", "value")):
            for value in (
                float("nan"),
                float("inf"),
                -float("inf"),
                True,
                "0",
                10**400,
            ):
                with self.subTest(path=path, value=str(value)):
                    with self.assertRaises(ScorecardError):
                        row_to_json(change(example_row(), path, value))

    def test_nonstandard_json_numbers_and_overflow_are_rejected(self) -> None:
        encoded = row_to_json(example_row())
        for token in ("NaN", "Infinity", "-Infinity", "1e999"):
            with self.subTest(token=token):
                with self.assertRaises(ScorecardError):
                    row_from_json(
                        encoded.replace('"observed": 100', '"observed": ' + token)
                    )

    def test_measured_verdicts_require_valid_complete_evidence(self) -> None:
        for verdict in ("PASS", "FAIL"):
            invalid = [
                (("validity", "status"), status)
                for status in ("invalid", "insufficient", "missing")
            ] + [
                (("coverage",), "partial"),
                (("coverage",), "none"),
                (("observed",), None),
                (("expected", "value"), None),
                (("tolerance", "value"), None),
                (("tolerance", "value"), -1),
            ]
            for path, value in invalid:
                with self.subTest(verdict=verdict, path=path, value=value):
                    with self.assertRaises(ScorecardError):
                        validate_row(change(example_row(verdict), path, value))

    def test_refusals_never_serialize_a_numeric_observation(self) -> None:
        for verdict in ("NO VERDICT", "MISSING EVIDENCE"):
            for value in (0, 0.0, -0.0, 1):
                with self.subTest(verdict=verdict, value=value):
                    with self.assertRaises(ScorecardError):
                        row_to_json(change(example_row(verdict), ("observed",), value))

    def test_unavailable_expected_and_tolerance_are_explicit_nulls(self) -> None:
        for verdict in ("NO VERDICT", "MISSING EVIDENCE"):
            row = example_row(verdict)
            row["expected"] = {
                "value": None,
                "source": "unavailable: fixture not defined",
            }
            row["tolerance"] = {
                "value": None,
                "source": "unavailable: rubric not frozen",
            }
            self.assertEqual(row_from_json(row_to_json(row)), row)
            self.assertIsNone(json.loads(row_to_json(row))["observed"])

    def test_validity_states_are_disjoint(self) -> None:
        for verdict, allowed in (
            ("PASS", {"valid"}),
            ("FAIL", {"valid"}),
            ("NO VERDICT", {"invalid", "insufficient"}),
            ("MISSING EVIDENCE", {"missing"}),
        ):
            for status in ("valid", "invalid", "insufficient", "missing"):
                row = change(example_row(verdict), ("validity", "status"), status)
                with self.subTest(verdict=verdict, status=status):
                    if status in allowed:
                        validate_row(row)
                    else:
                        with self.assertRaises(ScorecardError):
                            validate_row(row)

    def test_missing_evidence_cannot_claim_coverage(self) -> None:
        for coverage in ("partial", "complete"):
            with self.assertRaises(ScorecardError):
                validate_row(
                    change(example_row("MISSING EVIDENCE"), ("coverage",), coverage)
                )

    def test_invalid_estimator_can_have_complete_capture_coverage(self) -> None:
        row = example_row("NO VERDICT")
        row["coverage"] = "complete"
        row["validity"]["status"] = "invalid"
        validate_row(row)

    def test_zero_is_allowed_only_as_a_real_measurement(self) -> None:
        row = example_row()
        row["expected"]["value"] = row["observed"] = row["tolerance"]["value"] = 0
        self.assertEqual(row_from_json(row_to_json(row))["observed"], 0)

    def test_unknown_fields_and_versions_are_rejected(self) -> None:
        for path, value in (
            (("error",), 0),
            (("validity", "pass"), True),
            (("schema_version",), 2),
            (("schema_version",), True),
            (("verdict",), "UNKNOWN"),
            (("coverage",), "unknown"),
            (("case", "partition"), "train"),
        ):
            with self.subTest(path=path):
                with self.assertRaises(ScorecardError):
                    validate_row(change(example_row(), path, value))

    def test_artifact_digest_is_required_and_well_formed(self) -> None:
        for sha256 in (None, "", "a" * 63, "g" * 64, "A" * 64, "a" * 64 + "\n"):
            with self.subTest(sha256=sha256):
                with self.assertRaises(ScorecardError):
                    validate_row(change(example_row(), ("artifact", "sha256"), sha256))

    def test_malformed_json_duplicate_keys_and_nonobjects_are_rejected(self) -> None:
        encoded = row_to_json(example_row())
        duplicate = encoded.replace(
            '"verdict": "PASS"', '"verdict": "FAIL", "verdict": "PASS"'
        )
        for document in ("{", "null", "[]", duplicate):
            with self.subTest(document=document):
                with self.assertRaises(ScorecardError):
                    row_from_json(document)

    def test_nested_objects_cannot_be_replaced_with_scalars_or_arrays(self) -> None:
        for field in (
            "case",
            "estimator",
            "rubric",
            "expected",
            "tolerance",
            "validity",
            "artifact",
        ):
            for value in (None, [], "unknown", 1):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ScorecardError):
                        validate_row(change(example_row(), (field,), value))


class ScorecardReportTests(unittest.TestCase):
    def report(self, rows: list[dict] | None = None) -> dict:
        return make_report(
            rows if rows is not None else [example_row()],
            partition="development",
            rubric={"id": "synthetic-only", "version": "1"},
        )

    def test_raw_rows_and_separate_state_and_coverage_counts_round_trip(self) -> None:
        rows = [
            example_row(state)
            for state in ("PASS", "FAIL", "NO VERDICT", "MISSING EVIDENCE")
        ]
        rows[2]["coverage"] = "partial"
        report = self.report(rows)
        self.assertEqual(report["rows"], rows)
        self.assertEqual(
            report["summary"],
            {
                "total_rows": 4,
                "verdicts": {
                    "PASS": 1,
                    "FAIL": 1,
                    "NO VERDICT": 1,
                    "MISSING EVIDENCE": 1,
                },
                "coverage": {"complete": 2, "partial": 1, "none": 1},
            },
        )
        self.assertEqual(report_from_json(report_to_json(report)), report)
        self.assertNotIn("verdict", report)
        self.assertNotIn("score", report["summary"])

    def test_report_copies_rows_and_rubric(self) -> None:
        rows = [example_row()]
        rubric = copy.deepcopy(rows[0]["rubric"])
        report = make_report(rows, partition="development", rubric=rubric)
        rows[0]["observed"] = None
        rubric["version"] = "2"
        validate_report(report)

    def test_empty_report_has_zero_counts_and_no_product_verdict(self) -> None:
        report = self.report([])
        self.assertEqual(report["summary"]["total_rows"], 0)
        self.assertEqual(sum(report["summary"]["verdicts"].values()), 0)
        self.assertNotIn("verdict", report)
        validate_report(report)

    def test_mixed_or_mislabeled_partitions_are_rejected(self) -> None:
        holdout = change(example_row(), ("case", "partition"), "holdout")
        for rows in ([holdout], [example_row(), holdout]):
            with self.assertRaises(ScorecardError):
                self.report(rows)
        report = make_report([holdout], partition="holdout", rubric=holdout["rubric"])
        self.assertEqual(report_from_json(report_to_json(report)), report)

    def test_mixed_rubrics_are_rejected(self) -> None:
        for key in ("id", "version"):
            row = change(example_row(), ("rubric", key), "different")
            with self.assertRaises(ScorecardError):
                self.report([example_row(), row])

    def test_aggregation_rejects_incompatible_rows(self) -> None:
        for path, value in (
            (("property",), "phase"),
            (("unit",), "cents"),
            (("estimator", "name"), "another-estimator"),
            (("estimator", "version"), "2"),
            (("rubric", "id"), "other-rubric"),
            (("rubric", "version"), "2"),
            (("case", "partition"), "holdout"),
        ):
            with self.subTest(path=path):
                with self.assertRaises(ScorecardError):
                    summarize_rows([example_row(), change(example_row(), path, value)])

    def test_compatible_summary_is_counts_only_across_cases(self) -> None:
        other = change(example_row("FAIL"), ("case", "id"), "another-case")
        summary = summarize_rows([example_row(), other])
        self.assertEqual(summary["total_rows"], 2)
        self.assertEqual(summary["verdicts"]["FAIL"], 1)
        self.assertEqual(set(summary), {"total_rows", "verdicts", "coverage"})

    def test_heterogeneous_report_preserves_properties_without_averaging(self) -> None:
        other = change(example_row("FAIL"), ("property",), "phase")
        other["unit"] = "samples"
        report = self.report([example_row(), other])
        self.assertEqual(report["rows"][1], other)
        self.assertEqual(report["summary"]["verdicts"]["FAIL"], 1)

    def test_tampered_report_is_revalidated_before_serialization(self) -> None:
        mutations = [
            (("summary", "total_rows"), 0),
            (("partition",), "holdout"),
            (("schema_version",), 2),
            (("verdict",), "PASS"),
            (("rubric", "version"), "2"),
        ]
        for path, value in mutations:
            with self.subTest(path=path):
                with self.assertRaises(ScorecardError):
                    report_to_json(change(self.report(), path, value))
        for key, value in (("PASS", 0), ("PASS", True), ("FAIL", -1), ("FAIL", 0.5)):
            report = self.report()
            report["summary"]["verdicts"][key] = value
            with self.assertRaises(ScorecardError):
                report_from_json(json.dumps(report))

    def test_every_report_field_is_required(self) -> None:
        for key in self.report():
            report = self.report()
            del report[key]
            with self.subTest(key=key):
                with self.assertRaises(ScorecardError):
                    validate_report(report)

    def test_summary_requires_all_counts_and_rejects_forged_coverage(self) -> None:
        for group in ("verdicts", "coverage"):
            for key in self.report()["summary"][group]:
                report = self.report()
                del report["summary"][group][key]
                with self.subTest(group=group, key=key):
                    with self.assertRaises(ScorecardError):
                        validate_report(report)
        report = self.report()
        report["summary"]["coverage"]["complete"] = 0
        report["summary"]["coverage"]["none"] = 1
        with self.assertRaises(ScorecardError):
            report_to_json(report)

    def test_report_serialization_checks_raw_evidence_again(self) -> None:
        report = self.report()
        report["rows"][0]["observed"] = None
        with self.assertRaises(ScorecardError):
            report_to_json(report)


def enable_schema_checks() -> None:
    """Opt-in parity run: reuse all row mutations with an independent validator.

    Run with `uv run --no-project --with jsonschema python tests/test_scorecard.py
    --schemas -v`. Normal unittest discovery needs only the standard library.
    """
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    from torchsynth_voice import scorecard

    row_schema = json.loads(
        (ROOT / "spec/schemas/scorecard-row-v1.schema.json").read_text()
    )
    report_schema = json.loads(
        (ROOT / "spec/schemas/scorecard-report-v1.schema.json").read_text()
    )
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema))
        for schema in (row_schema, report_schema)
    )
    for schema in (row_schema, report_schema):
        Draft202012Validator.check_schema(schema)
    row_validator = Draft202012Validator(row_schema, registry=registry)
    report_validator = Draft202012Validator(report_schema, registry=registry)
    original_row = scorecard.validate_row
    original_report = scorecard.validate_report

    def checked_row(row: dict) -> None:
        try:
            # JSON Schema operates on JSON values; NaN/Infinity aren't JSON.
            json.dumps(row, allow_nan=False)
            schema_valid = row_validator.is_valid(row)
        except ValueError:
            schema_valid = False
        try:
            original_row(row)
        except ScorecardError:
            if schema_valid:
                raise AssertionError("JSON Schema accepted a row rejected by the API")
            raise
        if not schema_valid:
            raise AssertionError(list(row_validator.iter_errors(row)))

    def checked_report(report: dict) -> None:
        try:
            original_report(report)
        except ScorecardError as error:
            # These relational/canonical-integer checks are explicitly API-only.
            if str(error) not in (
                "report cannot mix or relabel rubrics",
                "summary counts do not match raw rows",
                "summary counts must be nonnegative integers",
            ) and report_validator.is_valid(report):
                raise AssertionError(
                    "JSON Schema accepted a structurally invalid report"
                )
            raise
        report_validator.validate(report)

    scorecard.validate_row = checked_row
    scorecard.validate_report = checked_report
    globals()["validate_row"] = checked_row
    globals()["validate_report"] = checked_report


if __name__ == "__main__":
    if "--schemas" in sys.argv:
        sys.argv.remove("--schemas")
        enable_schema_checks()
    unittest.main()
