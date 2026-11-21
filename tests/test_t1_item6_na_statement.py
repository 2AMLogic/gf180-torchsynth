"""T1 item 6 ``not_applicable`` statement guard (issue #524).

``evidence/item6-na-statement.json`` is the committed claim that no row of
the ratified ``spec/T1-SPEC.md`` table is statistical; ``klt signoff`` binds
it and the spec by content hash and re-scans both. This test keeps the
statement in lockstep with the spec table itself: exactly one statement row
per spec row, ``class`` and ``reason`` quoted from the table's own
``row class`` and ``item-6 statement`` columns, and
``reviewed_not_statistical`` waivers only on rows whose spec cells trip the
grader's conservative statistical text scan (so the waiver set can never
grow past the rows klt actually refuses).
"""

import re
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: The statistical words klt's text scan flags (klayout-tools
#: ``signoff_na._NA_STATISTICAL_TEXT_RE``): a row whose spec cells mention
#: one needs a ``reviewed_not_statistical`` waiver on its statement row.
STATISTICAL_TEXT_RE = re.compile(
    r"σ|\bsigma\b|\bcpk\b|\bppk\b|\byield\b|\bmonte[ _-]?carlo\b"
    r"|\bmismatch\b|\bmatching\b|\boffset\b|\baccuracy\b",
    re.IGNORECASE,
)


def spec_rows(text: str) -> list[dict]:
    """Rows of the spec's first pipe table with an ``id`` header cell."""
    lines = text.splitlines()
    for index, line in enumerate(lines[:-1]):
        if not line.lstrip().startswith("|"):
            continue
        header = [c.strip() for c in line.strip().strip("|").split("|")]
        if "id" not in header:
            continue
        alignment = lines[index + 1].strip().strip("|").split("|")
        if not all(re.fullmatch(r":?-+:?", c.strip()) for c in alignment):
            continue
        rows = []
        for body in lines[index + 2:]:
            if not body.lstrip().startswith("|"):
                break
            cells = [c.strip().replace("\\|", "|")
                     for c in re.split(r"(?<!\\)\|", body.strip().strip("|"))]
            rows.append(dict(zip(header, cells)))
        return rows
    raise AssertionError("spec/T1-SPEC.md has no pipe table with an 'id' header")


class T1Item6NaStatementTest(unittest.TestCase):
    def setUp(self):
        self.spec_rows = spec_rows(
            (ROOT / "spec" / "T1-SPEC.md").read_text(encoding="utf-8"))
        self.statement = json.loads(
            (ROOT / "evidence" / "item6-na-statement.json")
            .read_text(encoding="utf-8"))

    def test_spec_rows_parsed(self):
        self.assertEqual([row["id"] for row in self.spec_rows],
                         ["F1", "F2", "C1", "T1", "A1", "PW1"])

    def test_one_statement_row_per_spec_row(self):
        self.assertEqual([row["id"] for row in self.statement["rows"]],
                         [row["id"] for row in self.spec_rows])

    def test_statement_quotes_the_table(self):
        for spec_row, row in zip(self.spec_rows, self.statement["rows"]):
            self.assertEqual(row["class"], spec_row["row class"], row["id"])
            self.assertEqual(row["reason"], spec_row["item-6 statement"],
                             row["id"])

    def test_waivers_only_on_text_scan_hits(self):
        for spec_row, row in zip(self.spec_rows, self.statement["rows"]):
            hits = any(STATISTICAL_TEXT_RE.search(str(cell))
                       for cell in spec_row.values())
            if row.get("reviewed_not_statistical") is True:
                self.assertTrue(hits, "waived row %s has no text hit"
                                % row["id"])
            else:
                self.assertFalse(hits, "row %s trips the text scan but "
                                "carries no waiver" % row["id"])


if __name__ == "__main__":
    unittest.main()
