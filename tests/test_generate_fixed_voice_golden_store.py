"""Corpus-store resolution of the fixed Voice golden generator (issue #398)."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools/generate_fixed_voice_golden.py"


class TestCorpusStoreResolution(unittest.TestCase):
    def test_missing_store_names_path_and_regen_command(self):
        sys.path.insert(0, str(ROOT / "tools"))
        try:
            from generate_fixed_voice_golden import build_corpus_requests

            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(SystemExit) as ctx:
                    build_corpus_requests(Path(tmp))
            message = str(ctx.exception)
            self.assertIn(tmp, message)
            self.assertIn("tools/render_corpus.py --store", message)
        finally:
            sys.path.pop(0)

    def test_help_lists_store_and_no_personal_path(self):
        out = subprocess.run(
            [sys.executable, str(TOOL), "--help"], capture_output=True, text=True, check=True
        ).stdout
        self.assertIn("--store", out)
        self.assertNotIn("/Users/", TOOL.read_text())


if __name__ == "__main__":
    unittest.main()
