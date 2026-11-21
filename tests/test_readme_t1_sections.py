"""README T1 content guard (issues #422, #524).

klayout-tools T1 item 10 asks for a README with the block's spec table and
how to reproduce every result. The attestation inventory only checks that
README.md exists; this checks the two sections are present, that the spec
table names every row of ``spec/T1-SPEC.md``, and that the section records
the table's 2026-11-04 ratification (DR-0016 / PR #419) instead of the former
provisional status.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def section(text: str, heading: str) -> str:
    match = re.search(r"^## %s\n(.*?)(?=^## |\Z)" % re.escape(heading), text,
                      re.S | re.M)
    if match is None:
        raise AssertionError("README.md has no '## %s' section" % heading)
    return match.group(1)


class ReadmeT1SectionsTest(unittest.TestCase):
    def setUp(self):
        self.readme = (ROOT / "README.md").read_text(encoding="utf-8")
        spec = (ROOT / "spec" / "T1-SPEC.md").read_text(encoding="utf-8")
        self.row_ids = re.findall(r"^\| ([A-Z]+\d+) \|", spec, re.M)

    def test_spec_rows_parsed(self):
        self.assertEqual(self.row_ids, ["F1", "F2", "C1", "T1", "A1", "PW1"])

    def test_spec_table_section(self):
        body = section(self.readme, "T1 spec table")
        for row_id in self.row_ids:
            self.assertRegex(body, r"(?m)^\| %s \|" % row_id, row_id)
        self.assertIn("Ratified 2026-11-04", body)
        self.assertNotIn("not ratified", body)
        self.assertNotIn("provisional until it lands", body)
        self.assertIn("pull/419", body)
        self.assertIn("0016-ratify-t1-spec.md", body)
        self.assertIn("manifests/gf180-torchsynth.signoff.json", body)
        self.assertIn("spec/T1-SPEC.md", body)

    def test_spec_table_is_outside_capabilities_block(self):
        start = self.readme.index("<!-- CAPABILITIES:BEGIN -->")
        end = self.readme.index("<!-- CAPABILITIES:END -->")
        self.assertNotIn("## T1 spec table", self.readme[start:end])

    def test_reproduction_section(self):
        body = section(self.readme, "Reproducing the results")
        for runner in ("tb/run_tb.py", "tb/run_oneshot.py", "tb/run_voice.py",
                       "tools/synthesize_oneshot.py"):
            self.assertIn(runner, body)
            self.assertTrue((ROOT / runner).is_file(), runner)


if __name__ == "__main__":
    unittest.main()
