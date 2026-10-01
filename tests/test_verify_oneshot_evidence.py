"""Simulator-free checks for ``tools/verify_oneshot_evidence.py`` (#79).

This tool answers a narrow, mechanical question -- "is this already-produced
evidence record safe to commit as a citation of an exact commit?" -- and
these checks pin every way the answer must be NO, plus the one way it is
YES, against small synthetic records. No simulator is invoked anywhere
here; the long ``tb/run_oneshot.py`` / ``tb/run_voice.py`` flows that
produce real records are out of scope for this suite.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import verify_oneshot_evidence as voe  # noqa: E402

GOOD_HEAD = "a" * 40


def _record(**overrides) -> dict:
    record = {
        "schema": "gf180-torchsynth/oneshot-whole-voice-evidence-v1",
        "result": "PASS",
        "float_tolerance": None,
        "identity": {
            "git_head": GOOD_HEAD,
            "git_tree_dirty": False,
        },
    }
    record.update({k: v for k, v in overrides.items() if k != "identity"})
    if "identity" in overrides:
        record["identity"] = {**record["identity"], **overrides["identity"]}
    return record


class VerifyOneshotEvidenceTest(unittest.TestCase):
    def test_clean_pass_is_committable(self):
        self.assertEqual(voe.verify(_record(), expect_head=None), [])

    def test_clean_pass_matching_expected_head_is_committable(self):
        self.assertEqual(
            voe.verify(_record(), expect_head=GOOD_HEAD), [])

    def test_unknown_schema_is_rejected(self):
        errors = voe.verify(_record(schema="not-a-real-schema"), None)
        self.assertTrue(any("unrecognized schema" in e for e in errors))

    def test_non_pass_result_is_rejected(self):
        errors = voe.verify(_record(result="FAIL"), None)
        self.assertTrue(any("not PASS" in e for e in errors))

    def test_missing_identity_is_rejected_and_stops_early(self):
        record = _record()
        del record["identity"]
        errors = voe.verify(record, None)
        self.assertEqual(errors, ["record carries no identity block"])

    def test_dirty_tree_is_rejected(self):
        errors = voe.verify(_record(identity={"git_tree_dirty": True}), None)
        self.assertTrue(any("git_tree_dirty" in e for e in errors))

    def test_malformed_git_head_is_rejected(self):
        errors = voe.verify(_record(identity={"git_head": "short"}), None)
        self.assertTrue(any("not a 40-character commit SHA" in e
                             for e in errors))

    def test_mismatched_expected_head_is_rejected(self):
        other_head = "b" * 40
        errors = voe.verify(_record(), expect_head=other_head)
        self.assertTrue(any("not the expected" in e for e in errors))

    def test_float_tolerance_set_is_rejected(self):
        errors = voe.verify(_record(float_tolerance=0.001), None)
        self.assertTrue(any("float_tolerance" in e for e in errors))

    def test_every_rejection_reason_is_reported_together(self):
        # A record that fails on every axis at once must report every axis,
        # not stop at the first -- an operator fixing one issue at a time
        # should not have to re-run this tool after each partial fix.
        record = _record(
            schema="bogus",
            result="FAIL",
            float_tolerance=0.5,
            identity={"git_tree_dirty": True, "git_head": "short"},
        )
        errors = voe.verify(record, expect_head=GOOD_HEAD)
        self.assertEqual(len(errors), 5)


if __name__ == "__main__":
    unittest.main()
