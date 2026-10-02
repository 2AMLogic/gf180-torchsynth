"""Simulator-free checks for ``tools/verify_oneshot_evidence.py`` (#79).

This tool answers a narrow, mechanical question -- "is this already-produced
evidence record safe to commit as a citation of an exact commit?" -- and
these checks pin every way the answer must be NO, plus the one way it is
YES, against small synthetic records. No simulator is invoked anywhere
here; the long ``tb/run_oneshot.py`` / ``tb/run_voice.py`` flows that
produce real records are out of scope for this suite.
"""

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import verify_oneshot_evidence as voe  # noqa: E402

GOOD_HEAD = "a" * 40

#: A one-file synthetic "tree" the freshness checks below are run against.
FAKE_SOURCE = "tb/sv/synthetic_engine.sv"
FAKE_BODY = b"module synthetic_engine; endmodule\n"
FAKE_DIGEST = hashlib.sha256(FAKE_BODY).hexdigest()


def _tree(tmp: str, body: bytes = FAKE_BODY) -> Path:
    """Write the synthetic source into a throwaway repo root."""

    root = Path(tmp)
    path = root / FAKE_SOURCE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return root


def _record(**overrides) -> dict:
    record = {
        "schema": "gf180-torchsynth/oneshot-whole-voice-evidence-v1",
        "result": "PASS",
        "float_tolerance": None,
        "identity": {
            "git_head": GOOD_HEAD,
            "git_tree_dirty": False,
            "rtl_sha256": {"synthetic_engine.sv": FAKE_DIGEST},
            "fixed_vector_sha256": {"synthetic_engine.sv": FAKE_DIGEST},
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


class TreeFreshnessTest(unittest.TestCase):
    """The digests a record pins must still describe the tree it is read in."""

    def test_matching_tree_is_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tree(tmp)
            self.assertEqual(voe.stale_sources(_record(), root), [])
            self.assertEqual(
                voe.verify(_record(), expect_head=None, repo_root=root), [])

    def test_changed_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tree(tmp, FAKE_BODY + b"// drift\n")
            errors = voe.stale_sources(_record(), root)
            self.assertTrue(any("changed since this record was produced" in e
                                 for e in errors), errors)

    def test_absent_source_is_rejected_not_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            errors = voe.stale_sources(_record(), Path(tmp))
            self.assertTrue(errors)
            self.assertTrue(all("does not exist" in e for e in errors), errors)

    def test_record_pinning_no_source_is_rejected(self):
        # A record with an empty (or missing) digest block pins nothing, so
        # "fresh" would be vacuously true -- refuse it instead.
        with tempfile.TemporaryDirectory() as tmp:
            root = _tree(tmp)
            record = _record()
            record["identity"]["rtl_sha256"] = {}
            del record["identity"]["fixed_vector_sha256"]
            errors = voe.stale_sources(record, root)
            self.assertEqual(len(errors), 2, errors)
            self.assertTrue(all("not a non-empty" in e for e in errors), errors)

    def test_malformed_digest_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tree(tmp)
            record = _record()
            record["identity"]["rtl_sha256"] = {"synthetic_engine.sv": "abc"}
            errors = voe.stale_sources(record, root)
            self.assertTrue(any("not a 64-character sha256" in e
                                 for e in errors), errors)

    def test_ambiguous_name_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tree(tmp)
            twin = root / "spec/reference/synthetic_engine.sv"
            twin.parent.mkdir(parents=True, exist_ok=True)
            twin.write_bytes(FAKE_BODY)
            errors = voe.stale_sources(_record(), root)
            self.assertTrue(any("resolves ambiguously" in e for e in errors),
                             errors)

    def test_resolution_only_searches_declared_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stray = root / "elsewhere/synthetic_engine.sv"
            stray.parent.mkdir(parents=True, exist_ok=True)
            stray.write_bytes(FAKE_BODY)
            self.assertEqual(voe.resolve_source("synthetic_engine.sv", root),
                             [])


class CliTreeCheckTest(unittest.TestCase):
    """The CLI checks the tree by default; skipping it must be explicit."""

    def _written(self, tmp: str, record: dict) -> Path:
        path = Path(tmp) / "evidence.json"
        path.write_text(json.dumps(record), encoding="utf-8")
        return path

    def _main(self, argv) -> int:
        # The CLI's own report is checked by the exit code here; keep its
        # stdout out of the test log.
        with contextlib.redirect_stdout(io.StringIO()):
            return voe.main(argv)

    def test_default_tree_is_this_checkout(self):
        # With no --against-tree, the record is checked against the repository
        # the tool itself lives in -- the tree a reader is about to trust.
        self.assertEqual(voe.REPO_ROOT, ROOT)

    def test_default_run_checks_the_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tree(tmp, FAKE_BODY + b"// drift\n")
            evidence = self._written(tmp, _record())
            self.assertEqual(
                self._main([str(evidence), "--against-tree", str(root)]), 1)

    def test_skip_tree_check_passes_the_same_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tree(tmp, FAKE_BODY + b"// drift\n")
            evidence = self._written(tmp, _record())
            self.assertEqual(
                self._main([str(evidence), "--against-tree", str(root),
                           "--skip-tree-check"]), 0)

    def test_fresh_tree_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tree(tmp)
            evidence = self._written(tmp, _record())
            self.assertEqual(
                self._main([str(evidence), "--against-tree", str(root),
                           "--expect-head", GOOD_HEAD]), 0)


if __name__ == "__main__":
    unittest.main()
