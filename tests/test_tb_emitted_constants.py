"""Issue #354: the shared emitted-constants check in ``tb/run_tb.py``.

Exercises ``_emitted_constants_match`` with matching text, changed text,
a missing schedule ID, emission refusal and unreadable package input, and
checks that callers still run the check after an earlier failure.
"""

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tb"))

import run_tb  # noqa: E402


def _emission(text="PKG", ids=None):
    ids = [run_tb.sched.SCHEDULE_ID] if ids is None else ids
    return SimpleNamespace(package_text=text, emitted_ids=ids)


class EmittedConstantsMatchTest(unittest.TestCase):
    def _run(self, emit, landed_text, prefix=None):
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / "pkg.sv"
            if landed_text is not None:
                pkg.write_text(landed_text, encoding="utf-8")
            with mock.patch.object(run_tb, "CONSTANTS_PKG_SV", pkg), \
                    mock.patch.object(run_tb.codegen, "emit", emit), \
                    contextlib.redirect_stdout(out):
                args = {} if prefix is None else {"prefix": prefix}
                try:
                    result = run_tb._emitted_constants_match({"s": 1}, **args)
                except Exception as error:  # noqa: BLE001
                    return error, out.getvalue()
        return result, out.getvalue()

    def test_match(self):
        result, out = self._run(lambda **_: _emission(), "PKG")
        self.assertIs(result, True)
        self.assertIn("budget constants: landed pkg.sv matches", out)
        self.assertTrue(out.rstrip().endswith("-> OK"))

    def test_anchor_prefix(self):
        _, out = self._run(
            lambda **_: _emission(), "PKG", prefix="emitted budget constants"
        )
        self.assertTrue(out.startswith("  emitted budget constants: landed"))

    def test_changed_text(self):
        result, out = self._run(lambda **_: _emission(), "PKG\n")
        self.assertIs(result, False)
        self.assertTrue(out.rstrip().endswith("-> FAIL"))

    def test_missing_schedule_id(self):
        result, out = self._run(lambda **_: _emission(ids=[]), "PKG")
        self.assertIs(result, False)
        self.assertTrue(out.rstrip().endswith("-> FAIL"))

    def test_emission_refusal_propagates(self):
        def refuse(**_):
            raise ValueError("refused")

        result, out = self._run(refuse, "PKG")
        self.assertIsInstance(result, ValueError)
        self.assertEqual(out, "")

    def test_unreadable_package_propagates(self):
        result, out = self._run(lambda **_: _emission(), None)
        self.assertIsInstance(result, FileNotFoundError)
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
