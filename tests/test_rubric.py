"""Consistency controls for the frozen verification rubric v0 (issue #48).

Asserts the rubric's own contract: every assertion cites a landed source
digest, every digest matches the landed file, no row lacks a source, no
aggregate score exists, the holdout seal holds, and open rows are explicit.
Stdlib only; reads the committed artifacts, never holdout data.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import validate_rubric  # noqa: E402

RUBRIC_REL = "spec/reference/rubric-v0.json"
RUBRIC_ID = "R-L2-MM-COVERAGE"


def load_rubric() -> dict:
    with (ROOT / RUBRIC_REL).open(encoding="utf-8") as fh:
        return json.load(fh)


def row(rubric: dict, row_id: str) -> dict:
    for candidate in rubric["rows"]:
        if candidate["id"] == row_id:
            return candidate
    raise AssertionError(f"row {row_id} not found")


def validated_copy(mutate) -> tuple[list[str], Path]:
    """Validate a mutated copy of the repo in a temp directory."""
    tmp = Path(tempfile.mkdtemp(prefix="rubric-test-"))
    work = tmp / "repo"
    shutil.copytree(
        ROOT,
        work,
        ignore=shutil.ignore_patterns(".git", ".loom", ".github", "__pycache__"),
    )
    try:
        path = work / RUBRIC_REL
        document = json.loads(path.read_text(encoding="utf-8"))
        mutate(document)
        path.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
        return validate_rubric.validate_rubric(work, RUBRIC_REL), tmp
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


class TestRubricInternalConsistency(unittest.TestCase):
    def test_committed_rubric_validates(self) -> None:
        self.assertEqual(validate_rubric.validate_rubric(ROOT, RUBRIC_REL), [])

    def test_identity_and_freeze(self) -> None:
        document = load_rubric()
        self.assertEqual(document["schema"], "torchsynth-verification-rubric")
        self.assertEqual(document["semantic_version"], "rubric-v0")
        self.assertEqual(document["status"], "frozen")
        self.assertEqual(document["numeric_contract"], "unbound:#53")

    def test_levels_zero_through_six_present(self) -> None:
        document = load_rubric()
        levels = {row_["level"] for row_ in document["rows"]}
        self.assertEqual(levels, set(range(7)))

    def test_every_row_has_units_and_semantics(self) -> None:
        for row_ in load_rubric()["rows"]:
            for field in ("claim", "unit", "applicability", "verdict_rule", "failure_semantics"):
                self.assertTrue(str(row_.get(field, "")).strip(), f"{row_['id']} lacks {field}")
            self.assertTrue(row_["estimator"].get("name"))
            self.assertTrue(row_["estimator"].get("version"))

    def test_mutation_matrix_requires_zero_missed_faults(self) -> None:
        matrix = row(load_rubric(), RUBRIC_ID)
        missed = next(a for a in matrix["assertions"] if a["metric"] == "missed_faults")
        self.assertEqual(missed["value"], 0)

    def test_release_rule_is_a_conjunction(self) -> None:
        rule = load_rubric()["release_rule"]
        self.assertEqual(rule["form"], "conjunction")
        self.assertFalse(rule["omnibus_scalar"])
        self.assertIn("independent_judge", rule)

    def test_development_and_holdout_separated(self) -> None:
        document = load_rubric()
        seal = document["holdout_seal"]
        self.assertEqual(seal["holdout_artifacts_read"], [])
        self.assertEqual(seal["policy"], "sealed-until-thresholds-frozen")
        for source in document["sources"]:
            self.assertNotIn("holdout", source["path"])

    def test_appendix_rows_are_non_gating(self) -> None:
        document = load_rubric()
        appendix = set(document["perceptual_bounded_role"]["appendix_rows"])
        for row_ in document["rows"]:
            if row_["mandatory"] is False:
                self.assertIn(row_["id"], appendix)
                self.assertEqual(row_["role"], "appendix-auxiliary-flag-only")
            if row_["level"] == 5:
                self.assertFalse(row_["mandatory"])

    def test_open_rows_declare_no_invented_thresholds(self) -> None:
        landed = 0
        for row_ in load_rubric()["rows"]:
            if row_["status"] == "open":
                self.assertNotIn("assertions", row_)
                self.assertIn("NO VERDICT", row_["verdict_rule"])
                self.assertTrue(row_["open_reason"])
                self.assertTrue(row_["threshold_policy"])
            else:
                landed += 1
                self.assertTrue(row_.get("assertions"), f"{row_['id']} lacks a source-backed assertion")
        self.assertGreater(landed, 0)

    def test_summary_counts_recompute(self) -> None:
        document = load_rubric()
        rows = document["rows"]
        self.assertEqual(document["summary"]["rows_total"], len(rows))
        self.assertEqual(
            document["summary"]["rows_open"],
            sum(1 for r in rows if r["status"] == "open"),
        )


class TestValidatorCatchesBreakage(unittest.TestCase):
    def test_tampered_threshold_is_caught(self) -> None:
        def mutate(document: dict) -> None:
            for row_ in document["rows"]:
                if row_["id"].startswith("R-L2-CP-adsr_1"):
                    row_["assertions"][0]["value"] = 99
                    return
            raise AssertionError("target row missing")

        errors, tmp = validated_copy(mutate)
        try:
            self.assertTrue(any("does not match landed source" in e for e in errors))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_tampered_source_digest_is_caught(self) -> None:
        def mutate(document: dict) -> None:
            document["sources"][0]["sha256"] = "0" * 64

        errors, tmp = validated_copy(mutate)
        try:
            self.assertTrue(any("sha256 does not match" in e for e in errors))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_row_without_source_is_caught(self) -> None:
        def mutate(document: dict) -> None:
            document["rows"][0]["source_id"] = "nonexistent"

        errors, tmp = validated_copy(mutate)
        try:
            self.assertTrue(any("not a declared source" in e for e in errors))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_aggregate_score_is_caught(self) -> None:
        def mutate(document: dict) -> None:
            document["aggregate_score"] = 0.5

        errors, tmp = validated_copy(mutate)
        try:
            self.assertTrue(any("forbidden aggregate key" in e for e in errors))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_landed_row_without_assertion_is_caught(self) -> None:
        def mutate(document: dict) -> None:
            for row_ in document["rows"]:
                if row_["status"] == "open":
                    row_["status"] = "landed"
                    return
            raise AssertionError("no open row found")

        errors, tmp = validated_copy(mutate)
        try:
            self.assertTrue(any("landed row has no assertion" in e for e in errors))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_holdout_read_is_caught(self) -> None:
        def mutate(document: dict) -> None:
            document["holdout_seal"]["holdout_artifacts_read"] = ["sim/reference/holdout.json"]

        errors, tmp = validated_copy(mutate)
        try:
            self.assertTrue(any("holdout_artifacts_read is not empty" in e for e in errors))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
