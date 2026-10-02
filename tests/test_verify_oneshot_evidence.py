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
import subprocess
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


def _git(root: Path, *args: str) -> str:
    """Run ``git`` in ``root`` with identity/signing pinned, return stdout."""

    completed = subprocess.run(
        ("git", "-C", str(root),
         "-c", "user.email=evidence@example.invalid",
         "-c", "user.name=Evidence Test",
         "-c", "commit.gpgsign=false") + args,
        check=True, capture_output=True, text=True,
    )
    return completed.stdout.strip()


def _git_repo(tmp: str):
    """A throwaway repo with one landed commit and one branch-only commit.

    This is the shape the reachability check exists for: both commits are
    perfectly clean and both produce records that pass every *other* check,
    but only one of them is an ancestor of ``main``. The working tree is
    left on ``main`` with the pristine source content, so freshness checks
    run against it still pass and a rejection is attributable to
    reachability alone.
    """

    root = _tree(tmp)
    _git(root, "init", "--quiet")
    _git(root, "add", "-A")
    _git(root, "commit", "--quiet", "--no-verify", "-m", "landed")
    _git(root, "branch", "-M", "main")
    landed = _git(root, "rev-parse", "HEAD")
    _git(root, "checkout", "--quiet", "-b", "feature/not-landed")
    (root / FAKE_SOURCE).write_bytes(FAKE_BODY + b"// branch only\n")
    _git(root, "add", "-A")
    _git(root, "commit", "--quiet", "--no-verify", "-m", "branch only")
    branch_only = _git(root, "rev-parse", "HEAD")
    _git(root, "checkout", "--quiet", "main")
    return root, landed, branch_only


def _shallow_clone(src: Path, tmp: str, depth: int = 1) -> Path:
    """A genuine ``git clone --depth <depth>`` of ``src``'s default branch.

    Real shallow-ness, not a stand-in for it: ``is_shallow_clone`` on the
    result must say ``True``, and the clone must not contain any commit
    outside its fetched window (in particular, not a commit that only ever
    existed on a non-default branch of ``src``, which a depth-limited clone
    of the default branch never fetches at all).
    """

    dest = Path(tmp) / "shallow-clone"
    # A plain local-path clone takes git's "local" fast path (hardlinks),
    # which silently ignores --depth -- a depth-limited clone of a local
    # repository must go through the file:// transport to actually be
    # shallow, which is what this helper exists to guarantee.
    subprocess.run(
        ("git", "clone", "--quiet", "--depth", str(depth),
         "file://%s" % src, str(dest)),
        check=True, capture_output=True, text=True,
    )
    return dest


class ReachabilityTest(unittest.TestCase):
    """``identity.git_head`` must name a commit a reader can resolve.

    Checks 1-4 of the tool are all satisfied by a record produced on a
    pristine pull-request branch commit, and this repository squash-merges,
    so such a commit never reaches ``main``: the record cites a SHA that
    resolves for nobody. That is not hypothetical -- issue #79's
    ``directed``-profile record was held back for exactly this reason, with
    ``spec/ONESHOT-E2E.md`` able only to tell a reader to check by hand.

    Issue #268 adds a second axis on top of plain reachable/not: a commit
    that is simply absent is ``UNREACHABLE`` (an established failure -- the
    commit never existed under that SHA) only when the checkout is *not*
    shallow. When it *is* shallow, "absent" does not mean "never existed" --
    it may just be outside the fetched window -- so the same absence must
    report ``UNDECIDABLE`` instead, distinct from both a pass and a failure.
    """

    def test_a_landed_commit_is_reachable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, landed, _ = _git_repo(tmp)
            record = _record(identity={"git_head": landed})
            self.assertEqual(voe.reachability_errors(record, root), [])
            self.assertEqual(
                voe.reachability_status(record, root), (voe.REACHABLE, []))

    def test_a_branch_only_commit_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _, branch_only = _git_repo(tmp)
            record = _record(identity={"git_head": branch_only})
            errors = voe.reachability_errors(record, root)
            self.assertEqual(len(errors), 1, errors)
            self.assertIn("not reachable", errors[0])
            self.assertIn(branch_only, errors[0])
            self.assertIn("squash-merge", errors[0])
            status, messages = voe.reachability_status(record, root)
            self.assertEqual(status, voe.UNREACHABLE)
            self.assertEqual(messages, errors)

    def test_the_branch_only_record_passes_every_other_check(self):
        # Proof the gate is load-bearing rather than redundant: the record
        # the check above refuses is otherwise flawless, so without this
        # check it would be committed as a citation to nowhere.
        with tempfile.TemporaryDirectory() as tmp:
            root, _, branch_only = _git_repo(tmp)
            record = _record(identity={"git_head": branch_only})
            self.assertEqual(voe.verify(record, None, repo_root=root), [])

    def test_a_nonexistent_commit_in_a_full_clone_is_unreachable(self):
        # `root` is a plain `git init` repo -- not shallow -- so an absent
        # commit here was never created under that SHA at all. This must be
        # a definite UNREACHABLE failure, not an "ask me again with more
        # history" undecidable -- no amount of fetching ever produces a
        # commit that was never made.
        with tempfile.TemporaryDirectory() as tmp:
            root, _, _ = _git_repo(tmp)
            self.assertFalse(voe.is_shallow_clone(root))
            record = _record(identity={"git_head": "b" * 40})
            status, errors = voe.reachability_status(record, root)
            self.assertEqual(status, voe.UNREACHABLE)
            self.assertEqual(len(errors), 1, errors)
            self.assertIn("does not contain commit", errors[0])
            self.assertIn("never created", errors[0])
            self.assertNotIn("fetch-depth", errors[0])
            self.assertEqual(voe.reachability_errors(record, root), errors)

    def test_a_commit_missing_from_a_shallow_clone_is_undecidable(self):
        # The commit that `_git_repo` only ever put on the non-default
        # branch is, by construction, outside a depth-1 clone of the
        # default branch -- genuinely absent from this checkout's history,
        # but not provably nonexistent the way the case above is. That
        # distinction is the entire point of issue #268: this must read as
        # "cannot tell", never as either a pass or the same failure a truly
        # nonexistent commit gets.
        with tempfile.TemporaryDirectory() as tmp:
            root, _, branch_only = _git_repo(tmp)
            shallow = _shallow_clone(root, tmp)
            self.assertTrue(voe.is_shallow_clone(shallow))
            record = _record(identity={"git_head": branch_only})
            status, messages = voe.reachability_status(record, shallow)
            self.assertEqual(status, voe.UNDECIDABLE)
            self.assertEqual(len(messages), 1, messages)
            self.assertIn("shallow clone", messages[0])
            self.assertIn(branch_only, messages[0])
            self.assertIn("fetch-depth: 0", messages[0])
            # reachability_errors() still reports this as non-committable --
            # it is the flat-list view that cannot distinguish the two
            # failure-shaped outcomes, by design (status is what does).
            self.assertEqual(voe.reachability_errors(record, shallow),
                             messages)

    def test_a_landed_commit_is_still_reachable_from_a_shallow_clone(self):
        # Shallow-ness alone must not make an otherwise-decidable answer
        # undecidable: the commit this depth-1 clone's single commit *is*
        # must still read REACHABLE, not UNDECIDABLE just because the clone
        # happens to be shallow.
        with tempfile.TemporaryDirectory() as tmp:
            root, landed, _ = _git_repo(tmp)
            shallow = _shallow_clone(root, tmp)
            self.assertTrue(voe.is_shallow_clone(shallow))
            record = _record(identity={"git_head": landed})
            self.assertEqual(
                voe.reachability_status(record, shallow),
                (voe.REACHABLE, []))

    def test_a_tree_with_no_default_branch_ref_is_undecidable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, landed, _ = _git_repo(tmp)
            _git(root, "branch", "-M", "main", "trunk")
            record = _record(identity={"git_head": landed})
            errors = voe.reachability_errors(record, root)
            self.assertEqual(len(errors), 1, errors)
            self.assertIn("no default-branch ref resolved", errors[0])
            self.assertIn("fetch-depth: 0", errors[0])
            status, messages = voe.reachability_status(record, root)
            self.assertEqual(status, voe.UNDECIDABLE)
            self.assertEqual(messages, errors)

    def test_an_explicit_ref_overrides_the_candidate_list(self):
        # The escape hatch for a checkout whose default branch is spelled
        # some other way -- and proof the candidate list is not hardcoded
        # into the ancestry question itself.
        with tempfile.TemporaryDirectory() as tmp:
            root, landed, branch_only = _git_repo(tmp)
            _git(root, "branch", "-M", "main", "trunk")
            self.assertEqual(
                voe.reachability_errors(
                    _record(identity={"git_head": landed}), root, "trunk"),
                [])
            self.assertEqual(
                len(voe.reachability_errors(
                    _record(identity={"git_head": branch_only}), root,
                    "trunk")),
                1)

    def test_a_non_repository_is_undecidable_not_a_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = _record(identity={"git_head": GOOD_HEAD})
            errors = voe.reachability_errors(record, Path(tmp))
            self.assertNotEqual(
                errors, [],
                "a directory that cannot answer the question was accepted",
            )
            status, messages = voe.reachability_status(record, Path(tmp))
            self.assertEqual(status, voe.UNDECIDABLE)
            self.assertEqual(messages, errors)

    def test_a_malformed_head_is_rejected_before_git_is_consulted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _, _ = _git_repo(tmp)
            errors = voe.reachability_errors(
                _record(identity={"git_head": "not-a-sha"}), root)
            self.assertEqual(len(errors), 1, errors)
            self.assertIn("40-character", errors[0])
            status, _messages = voe.reachability_status(
                _record(identity={"git_head": "not-a-sha"}), root)
            self.assertEqual(status, voe.UNREACHABLE)

    def test_a_record_with_no_identity_block_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _, _ = _git_repo(tmp)
            record = _record()
            del record["identity"]
            self.assertNotEqual(voe.reachability_errors(record, root), [])

    def test_this_repository_resolves_a_default_branch(self):
        # The candidate list must actually cover this checkout, or the gate
        # in tests/test_committed_oneshot_evidence.py could only ever fail.
        self.assertIsNotNone(voe.resolve_default_branch(ROOT))


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

    def test_require_reachable_rejects_a_branch_only_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _, branch_only = _git_repo(tmp)
            evidence = self._written(tmp, _record(
                identity={"git_head": branch_only}))
            self.assertEqual(
                self._main([str(evidence), "--against-tree", str(root),
                            "--require-reachable"]), 1)

    def test_require_reachable_accepts_a_landed_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, landed, _ = _git_repo(tmp)
            evidence = self._written(tmp, _record(
                identity={"git_head": landed}))
            self.assertEqual(
                self._main([str(evidence), "--against-tree", str(root),
                            "--require-reachable",
                            "--expect-head", landed]), 0)

    def test_without_the_flag_the_branch_only_record_still_passes(self):
        # The flag is opt-in, so the default must be documented as a gap
        # rather than mistaken for a check. This is the behavior the
        # COMMITTABLE line now prints a NOTE about.
        with tempfile.TemporaryDirectory() as tmp:
            root, _, branch_only = _git_repo(tmp)
            evidence = self._written(tmp, _record(
                identity={"git_head": branch_only}))
            with contextlib.redirect_stdout(io.StringIO()) as out:
                status = voe.main([str(evidence), "--against-tree", str(root)])
            self.assertEqual(status, 0)
            self.assertIn("reachability NOT checked", out.getvalue())
            self.assertIn("--require-reachable", out.getvalue())

    def test_a_passing_reachability_run_says_which_ref_it_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, landed, _ = _git_repo(tmp)
            evidence = self._written(tmp, _record(
                identity={"git_head": landed}))
            with contextlib.redirect_stdout(io.StringIO()) as out:
                status = voe.main([str(evidence), "--against-tree", str(root),
                                   "--require-reachable"])
            self.assertEqual(status, 0)
            self.assertIn("git_head reachable on main", out.getvalue())

    def test_undecidable_reachability_exits_2_not_0_or_1(self):
        # The exit code the whole point of issue #268 rests on: a shallow
        # clone that cannot decide reachability must be its own outcome,
        # distinguishable from both COMMITTABLE (0) and the plain failure
        # NOT COMMITTABLE already uses (1) -- never read as a pass, and
        # never silently identical to an established failure either.
        with tempfile.TemporaryDirectory() as tmp:
            root, _, branch_only = _git_repo(tmp)
            shallow = _shallow_clone(root, tmp)
            evidence = self._written(tmp, _record(
                identity={"git_head": branch_only}))
            with contextlib.redirect_stdout(io.StringIO()) as out:
                status = voe.main([str(evidence), "--against-tree",
                                   str(shallow), "--require-reachable"])
            self.assertEqual(status, 2)
            printed = out.getvalue()
            self.assertIn("REACHABILITY UNDECIDABLE", printed)
            self.assertNotIn("NOT COMMITTABLE", printed)
            self.assertNotIn("\nCOMMITTABLE", printed)
            self.assertIn("shallow clone", printed)


if __name__ == "__main__":
    unittest.main()
