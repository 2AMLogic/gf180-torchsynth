"""The committed issue-#79 evidence records must stay committable (#79).

``sim/evidence/oneshot-whole-voice-regression-v1.json`` and
``sim/evidence/oneshot-tail-chain-regression-v1.json`` are evidence, not
just documentation: each is a real ``tb/run_oneshot.py`` / ``tb/run_voice.py``
output, verified committable by ``tools/verify_oneshot_evidence.py`` before
being added (see ``spec/ONESHOT-E2E.md`` -> "Evidence identity"). This suite
re-runs that same mechanical check against the files as committed, so a
future edit that quietly turns a citeable record into a non-citeable one
(a dirtied ``git_tree_dirty``, a flipped ``result``, a reintroduced float
tolerance) fails CI instead of going unnoticed. No simulator is invoked
here -- ``verify_oneshot_evidence`` only reads already-produced JSON and
hashes the source files that JSON names.

It also closes the *staleness* hole, which the fields inside a record cannot
close by themselves: a record pins a sha256 for every RTL source and frozen
vector it was produced from, but editing one of those files leaves the record
untouched -- ``result`` stays ``PASS``, ``git_tree_dirty`` stays ``false``,
and the cited ``git_head`` keeps naming a commit whose RTL is no longer the
RTL in the tree. So an engine could be changed with its bit-identity proof
silently inherited from the previous version, which is exactly the claim
``CLAUDE.md`` forbids ("never claim ... without a committed evidence record
that actually establishes it"). ``test_each_record_is_fresh_against_the_tree``
makes that case fail CI, and names the regeneration command; the mutation
check below proves the gate actually bites.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tb"))

import verify_oneshot_evidence as voe  # noqa: E402
from run_tb import EVIDENCE_TREE_SCOPE  # noqa: E402

EVIDENCE_DIR = ROOT / "sim/evidence"

#: How a stale record is made citeable again -- regenerate, never re-pin.
REGENERATE = {
    "gf180-torchsynth/oneshot-tail-chain-evidence-v1": (
        "python3 tb/run_oneshot.py --profile regression --workdir <dir>"
    ),
    "gf180-torchsynth/oneshot-whole-voice-evidence-v1": (
        "python3 tb/run_voice.py --profile regression --workdir <dir>"
    ),
}

#: Each committed record, its declared schema and the lane it certifies.
COMMITTED_RECORDS = {
    "oneshot-whole-voice-regression-v1.json": (
        "gf180-torchsynth/oneshot-whole-voice-evidence-v1"
    ),
    "oneshot-tail-chain-regression-v1.json": (
        "gf180-torchsynth/oneshot-tail-chain-evidence-v1"
    ),
}


class CommittedOneshotEvidenceTest(unittest.TestCase):
    def test_every_declared_record_exists(self):
        for name in COMMITTED_RECORDS:
            self.assertTrue(
                (EVIDENCE_DIR / name).is_file(),
                "%s is declared here but missing from %s" % (name, EVIDENCE_DIR),
            )

    def test_no_undeclared_record_is_silently_ignored(self):
        # A record added to sim/evidence/ without an entry here would pass
        # this suite silently -- refuse that instead.
        on_disk = {p.name for p in EVIDENCE_DIR.glob("*.json")}
        self.assertEqual(on_disk, set(COMMITTED_RECORDS))

    def test_each_record_is_committable(self):
        for name, expected_schema in COMMITTED_RECORDS.items():
            with self.subTest(record=name):
                record = json.loads((EVIDENCE_DIR / name).read_text())
                errors = voe.verify(record, expect_head=None)
                self.assertEqual(
                    errors, [],
                    "%s is no longer committable: %s" % (name, errors),
                )
                self.assertEqual(record.get("schema"), expected_schema)
                self.assertEqual(record.get("result"), "PASS")
                self.assertIsNone(record.get("float_tolerance"))
                self.assertIs(record["identity"]["git_tree_dirty"], False)
                git_head = record["identity"]["git_head"]
                self.assertEqual(len(git_head), 40)

    def test_each_record_is_fresh_against_the_tree(self):
        # The substantive gate: every RTL source and frozen vector the record
        # pins must still be byte-identical here. A failure means the proof
        # was invalidated by a later edit -- regenerate it (the command is in
        # the message), never re-pin the digest by hand.
        for name in COMMITTED_RECORDS:
            with self.subTest(record=name):
                record = json.loads((EVIDENCE_DIR / name).read_text())
                errors = voe.stale_sources(record, ROOT)
                self.assertEqual(
                    errors, [],
                    "%s no longer describes this tree: %s\nRegenerate it on a "
                    "clean, already-landed commit with: %s" % (
                        name, errors,
                        REGENERATE.get(record.get("schema"), "the lane's flow"),
                    ),
                )

    def test_every_digested_source_resolves_to_exactly_one_file(self):
        # Freshness is only meaningful if every digested name maps to one
        # real file: an unresolvable name would otherwise be a silent skip.
        for name in COMMITTED_RECORDS:
            record = json.loads((EVIDENCE_DIR / name).read_text())
            for block in voe.DIGEST_BLOCKS:
                for source in record["identity"][block]:
                    with self.subTest(record=name, source=source):
                        self.assertEqual(
                            len(voe.resolve_source(source, ROOT)), 1,
                            "%s names %s, which does not resolve to exactly "
                            "one file under %s"
                            % (name, source, voe.SOURCE_ROOTS),
                        )

    def test_a_changed_source_is_detected(self):
        # A control that cannot fail is not a control: prove the freshness
        # check bites on a one-byte change, in a throwaway tree (never by
        # touching the real sources).
        record = json.loads(
            (EVIDENCE_DIR / "oneshot-tail-chain-regression-v1.json").read_text()
        )
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp)
            for block in voe.DIGEST_BLOCKS:
                for source in record["identity"][block]:
                    original = voe.resolve_source(source, ROOT)[0]
                    mirror = fake / original.relative_to(ROOT)
                    mirror.parent.mkdir(parents=True, exist_ok=True)
                    mirror.write_bytes(original.read_bytes())
            self.assertEqual(voe.stale_sources(record, fake), [],
                             "the mirrored tree should start out fresh")

            victim = voe.resolve_source("audio_mix_engine.sv", fake)[0]
            victim.write_bytes(victim.read_bytes() + b"\n// drift\n")
            errors = voe.stale_sources(record, fake)
            self.assertTrue(
                any("audio_mix_engine.sv" in e and "changed" in e
                    for e in errors),
                "a changed RTL source went undetected: %s" % errors,
            )
            # And the full committability check must fail too, not just the
            # freshness helper on its own.
            self.assertNotEqual(
                voe.verify(record, expect_head=None, repo_root=fake), [],
                "verify() ignored a changed source",
            )
            self.assertEqual(
                voe.verify(record, expect_head=None), [],
                "verify() without a repo root must stay a self-consistency "
                "check only",
            )

    def test_a_missing_source_is_detected(self):
        # The other way a record stops describing a tree: the file is gone
        # (renamed, moved out of the declared roots). Must error, not skip.
        record = json.loads(
            (EVIDENCE_DIR / "oneshot-tail-chain-regression-v1.json").read_text()
        )
        with tempfile.TemporaryDirectory() as tmp:
            errors = voe.stale_sources(record, Path(tmp))
            self.assertTrue(errors, "an empty tree was reported as fresh")
            self.assertTrue(
                all("does not exist" in e for e in errors),
                "unexpected failure reasons for an empty tree: %s" % errors,
            )

    def test_every_digested_source_is_inside_the_dirty_scope(self):
        # Ties the two halves of evidence identity together: a record's
        # ``git_tree_dirty`` flag is only trustworthy if every input it
        # digests is one of the paths the flows check for uncommitted
        # changes. Adding a digested input outside EVIDENCE_TREE_SCOPE must
        # fail here rather than silently reopening that gap (the gap that
        # left ``sim/reference`` -- home of the frozen receipt -- unwatched).
        scope = tuple(Path(p) for p in EVIDENCE_TREE_SCOPE)
        for name in COMMITTED_RECORDS:
            record = json.loads((EVIDENCE_DIR / name).read_text())
            for block in voe.DIGEST_BLOCKS:
                for source in record["identity"][block]:
                    relative = voe.resolve_source(source, ROOT)[0]
                    relative = relative.relative_to(ROOT)
                    with self.subTest(record=name, source=str(relative)):
                        self.assertTrue(
                            any(relative.is_relative_to(prefix)
                                for prefix in scope),
                            "%s is digested by %s but lies outside the "
                            "evidence dirty scope %s"
                            % (relative, name, EVIDENCE_TREE_SCOPE),
                        )


if __name__ == "__main__":
    unittest.main()
