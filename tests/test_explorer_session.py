"""Test-only synthetic protocol fixtures; no DSP, runtime or playback evidence."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from contextlib import contextmanager
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_storage import fixture, save_metadata  # noqa: E402

from torchsynth_voice.artifacts import content_id, loads, validate_artifact  # noqa: E402
from torchsynth_voice.explorer_session import ExplorerSession, SessionError  # noqa: E402
from torchsynth_voice.identity import SoundIdentity  # noqa: E402
from torchsynth_voice.inventory import INVENTORY_PATH, load_json  # noqa: E402
from torchsynth_voice.storage import ArtifactStore  # noqa: E402


def synthetic_fixture(directory, sound_index=6):
    """Adapt only temp files: authentic names, deliberately synthetic values."""
    record = fixture(directory)
    inputs = record["inputs"]["value"]
    names = [row["name"] for row in load_json(INVENTORY_PATH)["parameters"]]
    rename = dict(zip(inputs["parameters"]["normalized_by_name"], names))
    for field, values in inputs["parameters"].items():
        inputs["parameters"][field] = {
            rename[name]: val for name, val in values.items()
        }
    for field, order in record.get("diagnostic_orders", {}).items():
        record["diagnostic_orders"][field] = [rename[name] for name in order]
    identity = SoundIdentity(sound_index)
    coordinates = identity.to_dict()
    inputs["fixture"] = {key: coordinates[key] for key in inputs["fixture"]}
    inputs["noise"]["slot"] = identity.noise_slot
    # Distinct synthetic values exercise name-keyed retention, not conversion.
    parameters = inputs["parameters"]
    parameters["normalized_by_name"] = {name: i / 100 for i, name in enumerate(names)}
    parameters["physical_by_name"] = {name: i + 0.125 for i, name in enumerate(names)}
    parameters["locks_physical"] = {names[0]: parameters["physical_by_name"][names[0]]}
    record["diagnostic_orders"] = {
        "forward_order": names,
        "randomization_order": list(reversed(names)),
    }
    record["artifact_id"] = content_id(inputs)
    validate_artifact(record)
    save_metadata(directory, record)
    return record


class ExplorerSessionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.store = ArtifactStore(self.base / "store")
        self.record = synthetic_fixture(self.base / "source")
        self.stored = self.store.publish(self.base / "source")
        self.renderer = Mock(return_value=self.stored.reference)
        self.rng = Mock()
        self.rng.choice.return_value = 6
        self.player = Mock(return_value=None)
        self.session = ExplorerSession(
            self.store,
            allowed_indices=[6, 31],
            renderer=self.renderer,
            player=self.player,
            rng=self.rng,
        )
        self.bookmark = self.base / "bookmark.json"

    def test_injected_sequence_and_restart_pin_exact_inputs_without_rerender(self):
        with patch.object(self.store, "verify", wraps=self.store.verify) as verify:
            selected = self.session.next()
            shown = self.session.show()
            self.session.audition()
            self.session.save(self.bookmark)
            self.assertEqual(self.session.repeat(), selected)
            restarted = ExplorerSession(
                self.store, renderer=self.renderer, rng=self.rng
            )
            recalled = restarted.recall(self.bookmark)
            self.assertEqual(recalled, selected)
            self.assertEqual(verify.call_count, 6)
        self.assertEqual(recalled.inputs, self.record["inputs"]["value"])
        self.assertEqual(shown["recorded_provenance"]["runtime"]["os"], "synthetic-os")
        self.assertEqual(shown["runtime_qualification"], "not-established")
        self.assertEqual(shown["reference"], self.stored.reference)
        self.renderer.assert_called_once_with(6)
        self.player.assert_called_once_with(self.stored)
        self.rng.choice.assert_not_called()
        self.assertEqual(self.bookmark.read_bytes()[-1:], b"\n")

    def test_random_uses_separate_rng_and_next_exhaustion_preserves_selection(self):
        self.session.random()
        self.rng.choice.assert_called_once_with((6, 31))
        synthetic_fixture(self.base / "second", 31)
        second = self.store.publish(self.base / "second")
        self.renderer.return_value = second.reference
        selected = self.session.next()
        with self.assertRaisesRegex(SessionError, "exhausted"):
            self.session.next()
        self.assertEqual(self.session.selection, selected)
        self.assertEqual(self.renderer.call_count, 2)

    def test_wrong_identity_and_unavailable_renderer_preserve_selection(self):
        selected = self.session.select(6, self.stored.reference)
        with self.assertRaisesRegex(SessionError, "identity"):
            self.session.request(31)
        self.assertEqual(self.session.selection, selected)
        session = ExplorerSession(self.store)
        with self.assertRaisesRegex(SessionError, "renderer unavailable"):
            session.next()
        self.assertIsNone(session.selection)

    def test_holdout_refused_before_renderer_or_store(self):
        with patch.object(self.store, "verify", wraps=self.store.verify) as verify:
            for index in range(96, 128):
                with self.subTest(index=index):
                    with self.assertRaisesRegex(SessionError, "reserved"):
                        self.session.request(index)
                    with self.assertRaisesRegex(SessionError, "reserved"):
                        self.session.select(index, self.stored.reference)
                    self.bookmark.write_text(
                        json.dumps(
                            {
                                "schema_version": 1,
                                "sound_index": index,
                                "artifact": self.stored.reference,
                            }
                        )
                    )
                    with self.assertRaisesRegex(SessionError, "reserved"):
                        self.session.recall(self.bookmark)
            verify.assert_not_called()
        self.renderer.assert_not_called()

    def test_failed_audition_stays_visible_after_successful_save(self):
        self.session.next()
        self.player.side_effect = RuntimeError("test player refused")
        with self.assertRaisesRegex(SessionError, "test player refused"):
            self.session.audition()
        self.session.save(self.bookmark)
        shown = self.session.show()
        self.assertEqual(shown["audition"]["status"], "failed")
        self.assertEqual(shown["last_error"]["operation"], "audition")
        self.assertEqual(shown["audition"]["artifact_id"], self.stored.artifact_id)
        self.assertEqual(
            loads(self.bookmark.read_bytes())["artifact"], self.stored.reference
        )

    def test_default_partition_and_invalid_allowed_sets_never_access_artifacts(self):
        default = ExplorerSession(self.store)
        self.assertEqual(default.allowed_indices, tuple(range(96)))
        with patch.object(self.store, "verify", wraps=self.store.verify) as verify:
            for invalid in (
                [],
                [True],
                [6.0],
                ["6"],
                [-1],
                [2**53],
                [6, 6],
                [96],
                [127],
            ):
                with self.subTest(invalid=invalid), self.assertRaises(SessionError):
                    ExplorerSession(
                        self.store, allowed_indices=invalid, renderer=self.renderer
                    )
            verify.assert_not_called()
        explicit = [39942, 6]
        session = ExplorerSession(self.store, allowed_indices=explicit)
        explicit.append(96)
        self.assertEqual(session.allowed_indices, (6, 39942))
        self.renderer.assert_not_called()

    def test_next_and_random_recheck_identity_before_access_even_on_bad_choice(self):
        with patch.object(self.store, "verify", wraps=self.store.verify) as verify:
            for index in range(96, 128):
                self.rng.choice.return_value = index
                with self.assertRaisesRegex(SessionError, "reserved"):
                    self.session.random()
                # Defense in depth: even corruption of private configuration
                # cannot send a reserved next identity to a backend/store.
                with patch.object(self.session, "_allowed", (index,)):
                    with self.assertRaisesRegex(SessionError, "reserved"):
                        self.session.next()
            for index in (True, 6.0, -1, 32, 2**53):
                self.rng.choice.return_value = index
                with self.assertRaises(SessionError):
                    self.session.random()
            verify.assert_not_called()
        self.renderer.assert_not_called()

    def test_malformed_references_fail_before_store_access_and_preserve_selection(self):
        selected = self.session.select(6, self.stored.reference)
        invalid = [None, {}, {**self.stored.reference, "unknown": 1}]
        for key, values in {
            "artifact_id": [None, "ra1-" + "A" * 64, "../escape", "ra1-123"],
            "sha256": [False, "A" * 64, "0" * 63, "../escape"],
            "ref": [
                None,
                "metadata.json",
                "/tmp/metadata.json",
                "../metadata.json",
                "artifacts//metadata.json",
                "file:///tmp/metadata.json",
            ],
        }.items():
            invalid.extend({**self.stored.reference, key: value} for value in values)
        with patch.object(self.store, "verify", wraps=self.store.verify) as verify:
            for reference in invalid:
                with self.subTest(reference=reference), self.assertRaises(SessionError):
                    self.session.select(6, reference)
                self.assertEqual(self.session.selection, selected)
            verify.assert_not_called()

    def test_authentic_names_checked_beyond_generic_store_validation(self):
        selected = self.session.select(6, self.stored.reference)
        record = synthetic_fixture(self.base / "wrong-names", 31)
        parameters = record["inputs"]["value"]["parameters"]
        for field in ("normalized_by_name", "physical_by_name"):
            values = parameters[field]
            values["synthetic.replacement"] = values.pop(list(values)[1])
        # Keep the generic contract consistent so only the authentic-name gate
        # can reject this otherwise valid published artifact.
        for field, order in record["diagnostic_orders"].items():
            record["diagnostic_orders"][field] = [
                name
                if name in parameters["normalized_by_name"]
                else "synthetic.replacement"
                for name in order
            ]
        record["artifact_id"] = content_id(record["inputs"]["value"])
        save_metadata(self.base / "wrong-names", record)
        other = self.store.publish(self.base / "wrong-names")
        with self.assertRaisesRegex(SessionError, "canonical parameter names"):
            self.session.select(31, other.reference)
        self.assertEqual(self.session.selection, selected)

    def test_missing_and_extra_names_in_either_map_fail(self):
        original = (self.stored.path / "metadata.json").read_bytes()
        for field in ("normalized_by_name", "physical_by_name"):
            for change in ("missing", "extra"):
                record = loads(original)
                values = record["inputs"]["value"]["parameters"][field]
                if change == "missing":
                    values.pop(next(iter(values)))
                else:
                    values["synthetic.extra"] = 0.5
                save_metadata(self.stored.path, record)
                with (
                    self.subTest(field=field, change=change),
                    self.assertRaises(SessionError),
                ):
                    self.session.select(6, self.stored.reference)
        (self.stored.path / "metadata.json").write_bytes(original)

    def test_every_reuse_rehashes_metadata_and_all_payloads(self):
        selected = self.session.select(6, self.stored.reference)
        self.session.save(self.bookmark)
        bookmark_bytes = self.bookmark.read_bytes()
        for relative in (
            "metadata.json",
            "audio.f32le",
            "traces/envelope.bin",
            "traces/oscillator.bin",
        ):
            target = self.stored.path / relative
            original = target.read_bytes()
            for change in ("corrupt", "missing"):
                if change == "missing":
                    target.unlink()
                elif relative == "metadata.json":
                    target.write_bytes(
                        original + b"\n"
                    )  # Valid JSON, stale exact digest.
                else:
                    target.write_bytes(bytes([original[0] ^ 1]) + original[1:])
                for operation in (
                    self.session.repeat,
                    self.session.show,
                    lambda: self.session.recall(self.bookmark),
                    lambda: self.session.save(self.bookmark),
                    self.session.audition,
                ):
                    with (
                        self.subTest(relative=relative, change=change),
                        self.assertRaises(SessionError),
                    ):
                        operation()
                    self.assertEqual(self.session.selection, selected)
                    self.assertEqual(self.bookmark.read_bytes(), bookmark_bytes)
                target.write_bytes(original)
        extra = self.stored.path / "extra.bin"
        extra.write_bytes(b"test-only")
        with self.assertRaisesRegex(SessionError, "extra file"):
            self.session.repeat()
        with self.assertRaisesRegex(SessionError, "extra file"):
            self.session.recall(self.bookmark)
        extra.unlink()
        self.renderer.assert_not_called()
        self.player.assert_not_called()
        self.rng.choice.assert_not_called()

    def test_separate_metadata_read_rehashes_exact_bytes_after_verify(self):
        selected = self.session.select(6, self.stored.reference)
        path = self.stored.path / "metadata.json"
        original = path.read_bytes()
        verify = self.store.verify

        def changed_after_verify(*args, **kwargs):
            stored = verify(*args, **kwargs)
            path.write_bytes(original + b"\n")
            return stored

        with patch.object(self.store, "verify", side_effect=changed_after_verify):
            with self.assertRaisesRegex(SessionError, "SHA-256 mismatch"):
                self.session.repeat()
        self.assertEqual(self.session.selection, selected)
        path.write_bytes(original)

    def test_reuse_keeps_artifact_bytes_inodes_times_and_copied_inputs(self):
        selected = self.session.select(6, self.stored.reference)

        def snapshot():
            return {
                str(path.relative_to(self.stored.path)): (
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                    path.stat().st_ino,
                    path.stat().st_mtime_ns,
                )
                for path in self.stored.path.rglob("*")
                if path.is_file()
            }

        before = snapshot()
        selected.inputs["noise"]["slot"] = 0
        self.session.show()["parameters"]["normalized_by_name"].clear()
        self.session.save(self.bookmark)
        self.session.repeat()
        self.session.recall(self.bookmark)
        self.assertEqual(snapshot(), before)
        self.assertEqual(self.session.selection.inputs, self.record["inputs"]["value"])
        self.assertNotEqual(
            self.stored.sha256,
            hashlib.sha256(json.dumps(self.record).encode()).hexdigest(),
        )

    def test_malformed_stale_and_mismatched_bookmarks_preserve_selection(self):
        selected = self.session.select(6, self.stored.reference)
        self.session.save(self.bookmark)
        valid = loads(self.bookmark.read_bytes())
        bad_records = [[], {}, {**valid, "extra": 1}]
        bad_records.extend(
            {**valid, "schema_version": version} for version in (2, True, 1.0, "1")
        )
        bad_records.extend(
            {**valid, "sound_index": index} for index in (31, True, 6.0, -1, 39942)
        )
        bad_records.extend(
            {**valid, "artifact": reference}
            for reference in (
                {**self.stored.reference, "sha256": "0" * 64},
                {
                    "artifact_id": "ra1-" + "0" * 64,
                    "sha256": "0" * 64,
                    "ref": "artifacts/ra1-" + "0" * 64 + "/metadata.json",
                },
            )
        )
        for record in bad_records:
            self.bookmark.write_text(json.dumps(record))
            with self.subTest(record=record), self.assertRaises(SessionError):
                self.session.recall(self.bookmark)
            self.assertEqual(self.session.selection, selected)
        for data in (b'{"schema_version":1,"schema_version":1}', b"{", b"NaN", b"\xff"):
            self.bookmark.write_bytes(data)
            with self.assertRaises(SessionError):
                self.session.recall(self.bookmark)
        self.bookmark.unlink()
        with self.assertRaises(SessionError):
            self.session.recall(self.bookmark)
        self.renderer.assert_not_called()
        self.rng.choice.assert_not_called()

    def test_precommit_write_failures_preserve_bookmark_and_clean_only_own_temp(self):
        selected = self.session.select(6, self.stored.reference)
        self.session.save(self.bookmark)
        original = self.bookmark.read_bytes()
        unrelated = self.base / ".bookmark.json.unrelated.tmp"
        unrelated.write_bytes(b"another caller")
        for target in ("tempfile.mkstemp", "os.fsync", "os.replace"):
            with patch(
                "torchsynth_voice.explorer_session." + target,
                side_effect=OSError("test disk failure"),
            ):
                with self.assertRaisesRegex(SessionError, "test disk failure"):
                    self.session.save(self.bookmark)
            self.assertEqual(self.bookmark.read_bytes(), original)
            self.assertEqual(self.session.selection, selected)
            self.assertEqual(list(self.base.glob(".bookmark.json.*.tmp")), [unrelated])
            self.assertEqual(self.session.last_error["operation"], "save")
        self.session.save(self.bookmark)
        self.assertIsNone(self.session.last_error)

    def test_bookmarks_never_write_in_store_or_follow_alias_into_store(self):
        self.session.select(6, self.stored.reference)
        alias = self.base / "store-alias"
        alias.symlink_to(self.store.root, target_is_directory=True)
        for target in (
            self.stored.path / "metadata.json",
            self.stored.path / "bookmark.json",
            self.store.root / ".publish.lock",
            alias / "bookmark.json",
        ):
            with self.assertRaisesRegex(SessionError, "outside the artifact store"):
                self.session.save(target)
        self.assertEqual(
            self.store.verify(self.stored.artifact_id).reference, self.stored.reference
        )

    def test_backend_failures_preserve_selection_and_audition_success_requires_return(
        self,
    ):
        selected = self.session.select(6, self.stored.reference)
        self.session.save(self.bookmark)
        original = self.bookmark.read_bytes()
        self.renderer.side_effect = RuntimeError("test renderer failed")
        with self.assertRaisesRegex(SessionError, "test renderer failed"):
            self.session.next()
        self.assertEqual(self.session.selection, selected)
        self.assertEqual(self.bookmark.read_bytes(), original)
        self.session.audition()
        self.player.side_effect = OSError("test playback failure")
        with self.assertRaisesRegex(SessionError, "test playback failure"):
            self.session.audition()
        self.assertEqual(self.session.show()["audition"]["status"], "failed")
        self.session.save(self.bookmark)
        self.assertEqual(self.session.last_error["operation"], "audition")
        self.player.side_effect = None
        self.session.audition()
        self.assertEqual(self.session.show()["audition"]["status"], "succeeded")
        self.assertIsNone(self.session.last_error)

    def test_no_selection_and_missing_player_are_actionable(self):
        for operation in (
            self.session.show,
            self.session.repeat,
            self.session.audition,
            lambda: self.session.save(self.bookmark),
        ):
            with self.assertRaisesRegex(SessionError, "no selection"):
                operation()
        session = ExplorerSession(self.store)
        session.select(6, self.stored.reference)
        with self.assertRaisesRegex(SessionError, "player unavailable"):
            session.audition()
        session.save(self.bookmark)
        self.assertEqual(session.show()["audition"]["status"], "failed")

    def test_successful_show_retry_does_not_return_a_resolved_show_error(self):
        with self.assertRaises(SessionError):
            self.session.show()
        self.session.select(6, self.stored.reference)
        self.assertIsNone(self.session.show()["last_error"])
        self.assertIsNone(self.session.last_error)

    def test_false_player_return_is_a_protocol_error_not_success(self):
        self.session.select(6, self.stored.reference)
        self.player.return_value = False
        with self.assertRaisesRegex(SessionError, "player must return None"):
            self.session.audition()
        self.assertEqual(self.session.show()["audition"]["status"], "failed")

    def test_partial_temporary_write_failure_preserves_previous_bookmark(self):
        self.session.select(6, self.stored.reference)
        self.session.save(self.bookmark)
        before = self.bookmark.read_bytes()
        fdopen = os.fdopen

        @contextmanager
        def fail_write(descriptor, mode):
            with fdopen(descriptor, mode) as stream:
                if mode == "wb":

                    def write(data):
                        stream.write(data[:8])
                        raise OSError("test partial write")

                    wrapped = Mock(wraps=stream)
                    wrapped.write.side_effect = write
                    yield wrapped
                else:
                    yield stream

        with patch(
            "torchsynth_voice.explorer_session.os.fdopen", side_effect=fail_write
        ):
            with self.assertRaisesRegex(SessionError, "test partial write"):
                self.session.save(self.bookmark)
        self.assertEqual(self.bookmark.read_bytes(), before)
        self.assertEqual(list(self.base.glob(".bookmark.json.*.tmp")), [])

    def test_bookmark_relocates_with_store_and_does_not_recover_audition_history(self):
        selected = self.session.select(6, self.stored.reference)
        self.session.audition()
        self.session.save(self.bookmark)
        shutil.copytree(self.store.root, self.base / "relocated-store")
        relocated = ExplorerSession(ArtifactStore(self.base / "relocated-store"))
        recalled = relocated.recall(self.bookmark)
        self.assertEqual(recalled.stored.reference, selected.stored.reference)
        self.assertEqual(recalled.inputs, selected.inputs)
        self.assertIsNone(relocated.show()["audition"])


if __name__ == "__main__":
    unittest.main()
