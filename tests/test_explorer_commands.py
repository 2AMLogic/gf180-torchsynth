"""Stdlib command tests over temp-only synthetic artifacts, never real audio."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_explorer_session import synthetic_fixture  # noqa: E402

from torchsynth_voice.explorer_commands import dispatch  # noqa: E402
from torchsynth_voice.explorer_session import ExplorerSession, SessionError  # noqa: E402
from torchsynth_voice.storage import ArtifactStore  # noqa: E402


class ExplorerCommandsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.store = ArtifactStore(self.base / "store")
        synthetic_fixture(self.base / "source")
        self.stored = self.store.publish(self.base / "source")
        self.selection_args = ["6", self.stored.artifact_id, self.stored.sha256]
        self.bookmark = self.base / "bookmark.json"

    def cli(self, *args):
        return subprocess.run(
            [
                sys.executable,
                "-S",
                "-m",
                "torchsynth_voice.explorer_commands",
                "--store",
                str(self.store.root),
                *args,
            ],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )

    def test_module_existing_store_select_show_save_restart_recall(self):
        for args in (
            ["select", *self.selection_args],
            ["--selected", *self.selection_args, "show"],
            ["--selected", *self.selection_args, "save", str(self.bookmark)],
            ["recall", str(self.bookmark)],
            ["--bookmark", str(self.bookmark), "repeat"],
        ):
            with self.subTest(args=args):
                result = self.cli(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                shown = json.loads(result.stdout)
                self.assertEqual(shown["reference"], self.stored.reference)
                self.assertEqual(shown["runtime_qualification"], "not-established")

    def test_module_refuses_missing_backends_and_fake_option(self):
        for args, message in (
            (["next"], "renderer unavailable"),
            (["random"], "renderer unavailable"),
            (["request", "6"], "renderer unavailable"),
            (["--selected", *self.selection_args, "audition"], "player unavailable"),
            (["show"], "no selection"),
            (["--fake", "next"], "unrecognized arguments"),
            (["select", "96", *self.selection_args[1:]], "reserved holdout"),
        ):
            with self.subTest(args=args):
                result = self.cli(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertEqual(result.stdout, "")

    def test_dispatch_injected_sequence_and_actionable_failures(self):
        renderer = Mock(return_value=self.stored.reference)
        player = Mock(return_value=None)
        rng = Mock()
        rng.choice.return_value = 6
        session = ExplorerSession(self.store, renderer=renderer, player=player, rng=rng)
        dispatch(session, ["request", "6"])
        dispatch(session, ["random"])
        dispatch(session, ["audition"])
        dispatch(session, ["save", str(self.bookmark)])
        dispatch(session, ["repeat"])
        shown = dispatch(session, ["recall", str(self.bookmark)])
        self.assertEqual(renderer.call_count, 2)
        rng.choice.assert_called_once()
        player.assert_called_once_with(self.stored)
        self.assertEqual(shown["audition"]["status"], "succeeded")
        with self.assertRaisesRegex(SessionError, "invalid int value"):
            dispatch(session, ["request", "banana"])
        with self.assertRaisesRegex(SessionError, "outside the allowed"):
            dispatch(session, ["request", "39942"])


if __name__ == "__main__":
    unittest.main()
