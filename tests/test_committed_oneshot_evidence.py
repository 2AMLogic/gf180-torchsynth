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
here -- ``verify_oneshot_evidence`` only reads already-produced JSON.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import verify_oneshot_evidence as voe  # noqa: E402

EVIDENCE_DIR = ROOT / "sim/evidence"

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


if __name__ == "__main__":
    unittest.main()
