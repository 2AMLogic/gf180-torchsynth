"""Fake-backed explorer MVP tests; deterministic, stdlib, never real audio.

These tests prove session/UI behavior with labeled doubles and synthetic
protocol fixtures. They never establish real rendering, runtime admission,
playback, fidelity or hardware claims; the bounded real smoke lives in
sim/reference/explorer-smoke.json.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_explorer_session import synthetic_fixture  # noqa: E402

from torchsynth_voice.explorer import (  # noqa: E402
    FAKE_RENDERER,
    PREVIEW_ENCODING,
    FakePlayer,
    FakeRenderer,
    PreviewPlayer,
    StoreRenderer,
    build_session,
    runtime_admission,
)
from torchsynth_voice.explorer_session import ExplorerSession, SessionError  # noqa: E402
from torchsynth_voice.storage import ArtifactStore  # noqa: E402


def file_hashes(directory):
    return {
        str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


class ExplorerMVPTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.store = ArtifactStore(self.base / "store")
        self.record = synthetic_fixture(self.base / "source")
        self.stored = self.store.publish(self.base / "source")
        self.bookmark = self.base / "bookmark.json"
        self.preview = self.base / "preview"

    def fake_session(self, *, failures=None, player=None):
        renderer = FakeRenderer(self.store, failures=failures)
        rng = Mock()
        session = ExplorerSession(
            self.store,
            renderer=renderer,
            player=player or FakePlayer(),
            rng=rng,
        )
        return session, renderer, rng

    def test_fake_sequence_deterministic_and_restart_reuses_saved_identity(self):
        session, renderer, rng = self.fake_session()
        rng.choice.return_value = 6
        selected = session.random()
        self.assertEqual(renderer.calls, [6])
        shown = session.show()
        self.assertEqual(shown["reference"], selected.stored.reference)
        self.assertEqual(shown["sound_index"], 6)
        session.audition()
        session.save(self.bookmark)
        self.assertEqual(session.repeat(), selected)
        self.assertEqual(renderer.calls, [6])
        restarted, restarted_renderer, first_rng = self.fake_session()
        rng.choice.assert_called_once()
        first_rng.choice.assert_not_called()
        recalled = restarted.recall(self.bookmark)
        self.assertEqual(recalled.stored.reference, selected.stored.reference)
        self.assertEqual(recalled.inputs, selected.inputs)
        self.assertEqual(restarted_renderer.calls, [])
        self.assertEqual(restarted.repeat(), recalled)
        self.assertEqual(restarted_renderer.calls, [])

    def test_render_failure_preserves_selection_and_never_saves_false_success(self):
        session, renderer, _ = self.fake_session(failures={31: RuntimeError("boom")})
        selected = session.select(6, self.stored.reference)
        with self.assertRaisesRegex(SessionError, "boom"):
            session.request(31)
        self.assertEqual(session.selection, selected)
        self.assertEqual(session.last_error["operation"], "request")
        self.assertEqual(renderer.calls, [31])
        session.save(self.bookmark)
        saved = json.loads(self.bookmark.read_bytes())
        self.assertEqual(saved["artifact"], self.stored.reference)
        self.assertEqual(saved["sound_index"], 6)

    def test_failed_audition_stays_visible_after_successful_save(self):
        session, _, _ = self.fake_session(
            player=FakePlayer(error=RuntimeError("no device"))
        )
        selected = session.select(6, self.stored.reference)
        with self.assertRaisesRegex(SessionError, "no device"):
            session.audition()
        session.save(self.bookmark)
        shown = session.show()
        self.assertEqual(shown["audition"]["status"], "failed")
        self.assertEqual(shown["last_error"]["operation"], "audition")
        self.assertEqual(shown["audition"]["artifact_id"], self.stored.artifact_id)
        self.assertEqual(session.selection, selected)
        self.assertEqual(
            json.loads(self.bookmark.read_bytes())["artifact"],
            self.stored.reference,
        )

    def test_holdout_refused_before_renderer_or_store_access(self):
        session, renderer, rng = self.fake_session()
        with patch.object(self.store, "verify", wraps=self.store.verify) as verify:
            for index in range(96, 128):
                rng.choice.return_value = index
                with self.assertRaisesRegex(SessionError, "reserved"):
                    session.request(index)
                with self.assertRaisesRegex(SessionError, "reserved"):
                    session.random()
            verify.assert_not_called()
        self.assertEqual(renderer.calls, [])
        self.assertEqual(self.store.discover(), (self.stored,))

    def test_corrupt_store_content_refuses_and_preserves_selection(self):
        session, _, _ = self.fake_session()
        selected = session.select(6, self.stored.reference)
        session.save(self.bookmark)
        audio = self.stored.path / "audio.f32le"
        data = bytearray(audio.read_bytes())
        data[0] ^= 0xFF
        audio.write_bytes(bytes(data))
        with self.assertRaisesRegex(SessionError, "SHA-256 mismatch"):
            session.repeat()
        self.assertEqual(session.selection, selected)
        self.assertEqual(session.last_error["operation"], "repeat")
        with self.assertRaisesRegex(SessionError, "SHA-256 mismatch"):
            session.recall(self.bookmark)
        with self.assertRaisesRegex(SessionError, "SHA-256 mismatch"):
            session.show()

    def test_identity_binding_and_honest_admission_on_synthetic_artifacts(self):
        session, _, _ = self.fake_session()
        selected = session.request(6)
        shown = session.show()
        self.assertEqual(shown["reference"], selected.stored.reference)
        self.assertEqual(
            shown["sound_index"], selected.inputs["fixture"]["sound_index"]
        )
        self.assertEqual(shown["profile"]["name"], "torchsynth-1-voice-default")
        self.assertEqual(
            shown["recorded_provenance"]["runtime"], selected.inputs["runtime"]
        )
        admission = runtime_admission(selected.inputs)
        self.assertEqual(admission["status"], "outside-admitted-profile")
        self.assertFalse(admission["checks"]["renderer_version"])
        self.assertEqual(selected.inputs["renderer_version"], FAKE_RENDERER)

    def test_name_keyed_metadata_survives_bookmark_round_trip(self):
        session, _, _ = self.fake_session()
        session.select(6, self.stored.reference)
        session.save(self.bookmark)
        restarted, _, _ = self.fake_session()
        recalled = restarted.recall(self.bookmark)
        expected = self.record["inputs"]["value"]
        self.assertEqual(
            recalled.inputs["parameters"]["normalized_by_name"],
            expected["parameters"]["normalized_by_name"],
        )
        self.assertEqual(
            recalled.inputs["parameters"]["physical_by_name"],
            expected["parameters"]["physical_by_name"],
        )
        self.assertEqual(recalled.inputs["noise"], expected["noise"])
        self.assertEqual(
            recalled.inputs["parameters"]["locks_physical"],
            expected["parameters"]["locks_physical"],
        )

    def test_preview_player_succeeds_outside_store_without_touching_artifacts(self):
        before = file_hashes(self.stored.path)
        player = PreviewPlayer([sys.executable, "-c", "pass"], preview_dir=self.preview)
        self.assertIsNone(player(self.stored))
        wavs = list(self.preview.glob("*.wav"))
        self.assertEqual(len(wavs), 1)
        self.assertFalse(wavs[0].is_relative_to(self.store.root))
        self.assertEqual(player.last_preview["encoding"], PREVIEW_ENCODING)
        self.assertTrue(player.last_preview["lossy_quantization"])
        self.assertEqual(file_hashes(self.stored.path), before)
        self.assertIn(
            "metadata.json", {path.name for path in self.stored.path.iterdir()}
        )
        self.assertEqual(list(self.stored.path.rglob("*.wav")), [])

    def test_preview_player_failures_are_actionable_and_leave_no_success(self):
        for command, message in (
            ([sys.executable, "-c", "import sys; sys.exit(3)"], "exited 3"),
            (["no-such-player-binary-424242"], "could not start"),
        ):
            with self.subTest(command=command):
                player = PreviewPlayer(command, preview_dir=self.preview)
                with self.assertRaisesRegex(SessionError, message):
                    player(self.stored)
                self.assertIsNone(player.last_preview)
        with self.assertRaisesRegex(SessionError, "outside the artifact store"):
            PreviewPlayer(
                [sys.executable, "-c", "pass"], preview_dir=self.store.root / "p"
            )(self.stored)
        with self.assertRaisesRegex(SessionError, "empty"):
            PreviewPlayer([])

    def test_bookmark_atomic_failures_preserve_previous_state(self):
        session, _, _ = self.fake_session()
        selected = session.select(6, self.stored.reference)
        with self.assertRaisesRegex(SessionError, "outside the artifact store"):
            session.save(self.store.root / "bookmark.json")
        self.assertEqual(session.selection, selected)
        self.bookmark.write_bytes(b"prior\n")
        with patch(
            "torchsynth_voice.explorer_session.os.replace", side_effect=OSError("no")
        ):
            with self.assertRaisesRegex(SessionError, "no"):
                session.save(self.bookmark)
        self.assertEqual(self.bookmark.read_bytes(), b"prior\n")
        self.assertEqual(
            [
                path.name
                for path in self.base.iterdir()
                if path.name.startswith(".bookmark")
            ],
            [],
        )

    def test_store_renderer_uses_public_adapter_and_refuses_holdout(self):
        backend = object()
        renderer = StoreRenderer(self.store, backend, producer_root=None)
        product = Mock()
        product.reference = self.stored.reference
        with patch(
            "torchsynth_voice.explorer.render_artifact", return_value=product
        ) as render:
            reference = renderer(6)
        self.assertEqual(reference, self.stored.reference)
        self.assertIs(renderer.last_receipt, product.receipt)
        request = render.call_args.args[0]
        self.assertIs(render.call_args.args[1], self.store)
        self.assertIs(render.call_args.args[2], backend)
        self.assertEqual(request["fixture"]["sound_index"], 6)
        with patch("torchsynth_voice.explorer.render_artifact") as render:
            with self.assertRaisesRegex(SessionError, "reserved"):
                renderer(96)
            render.assert_not_called()

    def test_build_session_backends_are_labeled_and_separated(self):
        _, probe = build_session(self.store.root, backend="fake", seed=1)
        self.assertIsInstance(probe["renderer"], FakeRenderer)
        self.assertIsInstance(probe["player"], FakePlayer)
        _, probe = build_session(self.store.root, backend="none")
        self.assertIsNone(probe["session"]._renderer)
        self.assertIsInstance(probe["player"], PreviewPlayer)
        _, probe = build_session(self.store.root, backend="docker")
        self.assertIsInstance(probe["renderer"], StoreRenderer)
        with self.assertRaisesRegex(SessionError, "unknown backend"):
            build_session(self.store.root, backend="native")

    def test_fake_renderer_failure_injection_publishes_nothing(self):
        renderer = FakeRenderer(self.store, failures={6: RuntimeError("no render")})
        session = ExplorerSession(self.store, renderer=renderer)
        with self.assertRaisesRegex(SessionError, "no render"):
            session.request(6)
        self.assertEqual(renderer.calls, [6])
        self.assertIsNone(session.selection)
        self.assertEqual(self.store.discover(), (self.stored,))


class ExplorerCLITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.store = self.base / "store"
        self.bookmark = self.base / "bookmark.json"

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / "tools" / "explore.py"), *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )

    def payload(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def fake_args(self, *extra):
        return ["--store", str(self.store), "--backend", "fake", *extra]

    def test_fake_cli_workflow_restart_repeat_save_reload_zero_rerender(self):
        first = self.payload(self.cli(*self.fake_args("--seed", "3", "random")))
        self.assertEqual(first["backend"], "fake-synthetic-fixtures-no-audio")
        self.assertEqual(first["fake_renderer_calls_this_process"], 1)
        self.assertEqual(
            first["runtime_admission"]["status"], "outside-admitted-profile"
        )
        reference = first["session"]["reference"]
        index = str(first["session"]["sound_index"])
        saved = self.payload(
            self.cli(
                *self.fake_args(
                    "--selected",
                    index,
                    reference["artifact_id"],
                    reference["sha256"],
                    "save",
                    str(self.bookmark),
                )
            )
        )
        self.assertEqual(saved["fake_renderer_calls_this_process"], 0)
        self.assertEqual(json.loads(self.bookmark.read_bytes())["artifact"], reference)
        repeated = self.payload(
            self.cli(*self.fake_args("--bookmark", str(self.bookmark), "repeat"))
        )
        self.assertEqual(repeated["session"]["reference"], reference)
        self.assertEqual(repeated["fake_renderer_calls_this_process"], 0)
        auditioned = self.payload(
            self.cli(*self.fake_args("--bookmark", str(self.bookmark), "audition"))
        )
        self.assertEqual(auditioned["session"]["audition"]["status"], "succeeded")
        self.assertEqual(auditioned["fake_renderer_calls_this_process"], 0)
        shown = self.payload(
            self.cli(*self.fake_args("--bookmark", str(self.bookmark), "show"))
        )
        self.assertEqual(shown["session"]["sound_index"], int(index))
        self.assertEqual(shown["session"]["reference"], reference)

    def test_cli_seed_makes_fake_random_deterministic(self):
        first = self.payload(self.cli(*self.fake_args("--seed", "11", "random")))
        second = self.payload(self.cli(*self.fake_args("--seed", "11", "random")))
        self.assertEqual(first["session"]["reference"], second["session"]["reference"])
        self.assertEqual(
            first["session"]["sound_index"], second["session"]["sound_index"]
        )

    def test_cli_failures_are_actionable_and_stdout_stays_empty(self):
        failed = self.cli(*self.fake_args("--fake-fail-render", "6", "request", "6"))
        self.assertEqual(failed.returncode, 2)
        self.assertIn("injected fake render failure", failed.stderr)
        self.assertEqual(failed.stdout, "")
        unplayed = self.cli(
            *self.fake_args(
                "--fake-fail-render", "6", "--fake-fail-player", "request", "6"
            )
        )
        self.assertEqual(unplayed.returncode, 2)
        rendered = self.payload(self.cli(*self.fake_args("request", "6")))
        reference = rendered["session"]["reference"]
        audition = self.cli(
            *self.fake_args(
                "--selected",
                "6",
                reference["artifact_id"],
                reference["sha256"],
                "--fake-fail-player",
                "audition",
            )
        )
        self.assertEqual(audition.returncode, 2)
        self.assertIn("injected fake player failure", audition.stderr)
        self.assertEqual(audition.stdout, "")

    def test_cli_refuses_holdout_before_any_artifact_access(self):
        refused = self.cli(*self.fake_args("request", "96"))
        self.assertEqual(refused.returncode, 2)
        self.assertIn("reserved holdout", refused.stderr)
        self.assertEqual(refused.stdout, "")
        self.assertEqual(list((self.store / "artifacts").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
