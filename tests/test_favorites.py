"""Favorite/lock/variation tests over synthetic protocol fixtures.

These tests prove favorite persistence, recall drift detection, deterministic
seeded variation and lock enforcement with labeled doubles and synthetic
fixtures. They never establish real rendering, runtime admission, playback,
fidelity or hardware claims.
"""

from __future__ import annotations

import copy
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
from test_storage import save_metadata  # noqa: E402

from torchsynth_voice import favorites  # noqa: E402
from torchsynth_voice.artifacts import content_id, validate_artifact  # noqa: E402
from torchsynth_voice.explorer_session import ExplorerSession, SessionError  # noqa: E402
from torchsynth_voice.favorites import FavoriteError, favorite_id  # noqa: E402
from torchsynth_voice.inventory import INVENTORY_PATH, load_json  # noqa: E402
from torchsynth_voice.storage import ArtifactStore  # noqa: E402

PITCH = "keyboard.midi_f0"


def ranges():
    return {
        row["name"]: (row["minimum"], row["maximum"])
        for row in load_json(INVENTORY_PATH)["parameters"]
    }


def in_range_fixture(directory, sound_index=6):
    """Synthetic fixture whose recorded physical values are in inventory range."""

    record = synthetic_fixture(directory, sound_index)
    inputs = record["inputs"]["value"]
    inputs["parameters"]["physical_by_name"] = {
        name: low + (high - low) / 2 for name, (low, high) in ranges().items()
    }
    inputs["parameters"]["normalized_by_name"] = dict.fromkeys(ranges(), 0.5)
    inputs["parameters"]["locks_physical"] = {}
    record["artifact_id"] = content_id(inputs)
    validate_artifact(record)
    save_metadata(directory, record)
    return record


def write_document(path, document):
    path.write_text(json.dumps(document))
    return path


class FavoritesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.store = ArtifactStore(self.base / "store")
        self.record = in_range_fixture(self.base / "source")
        self.stored = self.store.publish(self.base / "source")
        self.renderer = Mock(return_value=self.stored.reference)
        self.session = ExplorerSession(
            self.store,
            allowed_indices=[6, 31],
            renderer=self.renderer,
            rng=Mock(),
        )
        self.session.select(6, self.stored.reference)
        self.favorite = favorites.capture(self.session, notes="first")
        self.path = self.base / "favorite.json"

    def rewritten(self, change, name="case.json"):
        """A re-identified document with one change applied."""

        document = copy.deepcopy(self.favorite.document)
        change(document)
        document["favorite_id"] = favorite_id(document)
        return write_document(self.base / name, document)

    def test_capture_stores_recorded_state_and_identity_excludes_notes(self):
        document = self.favorite.document
        inputs = self.record["inputs"]["value"]
        audio = self.record["audio"]["value"]
        self.assertEqual(document["kind"], "capture")
        self.assertIsNone(document["parent_id"])
        self.assertEqual(document["sound_index"], 6)
        self.assertEqual(document["artifact"], self.stored.reference)
        self.assertEqual(
            document["audio"],
            {
                "sha256": audio["file"]["sha256"],
                "size_bytes": audio["file"]["size_bytes"],
                "sample_count": audio["observed_sample_count"],
            },
        )
        self.assertEqual(document["recorded"]["profile"], inputs["profile"])
        self.assertEqual(document["recorded"]["source"], inputs["source"])
        self.assertEqual(document["recorded"]["runtime"], inputs["runtime"])
        self.assertEqual(
            document["parameters"]["normalized_by_name"],
            inputs["parameters"]["normalized_by_name"],
        )
        self.assertEqual(
            document["parameters"]["physical_by_name"],
            inputs["parameters"]["physical_by_name"],
        )
        self.assertEqual(document["parameters"]["locks_physical"], {})
        self.assertEqual(document["notes"], "first")
        # Identity is the sound: notes are excluded from favorite_id.
        document["notes"] = "different text, same sound"
        self.assertEqual(favorite_id(document), self.favorite.favorite_id)

    def test_save_load_round_trip_is_byte_stable_and_guarded(self):
        favorites.save(self.favorite, self.path, store=self.store)
        self.assertEqual(self.path.read_bytes()[-1:], b"\n")
        loaded = favorites.load(self.path)
        self.assertEqual(loaded.document, self.favorite.document)
        first = self.path.read_bytes()
        favorites.save(loaded, self.path, store=self.store)
        self.assertEqual(self.path.read_bytes(), first)
        with self.assertRaisesRegex(FavoriteError, "outside the artifact store"):
            favorites.save(
                self.favorite, self.store.root / "favorite.json", store=self.store
            )
        alias = self.base / "store-alias"
        alias.symlink_to(self.store.root, target_is_directory=True)
        with self.assertRaisesRegex(FavoriteError, "outside the artifact store"):
            favorites.save(self.favorite, alias / "favorite.json", store=self.store)
        self.assertFalse((self.stored.path / "favorite.json").exists())

    def test_load_refuses_tampering_versions_and_absolute_paths(self):
        favorites.save(self.favorite, self.path, store=self.store)
        valid = favorites.load(self.path).document
        tampered = copy.deepcopy(valid)
        tampered["favorite_id"] = "fv1-" + "0" * 64
        with self.assertRaisesRegex(FavoriteError, "favorite_id disagrees"):
            favorites.load(write_document(self.path, tampered))
        self.path.write_bytes(b'{"schema_version":1,"schema_version":1}')
        with self.assertRaisesRegex(SessionError, "duplicate JSON object key"):
            favorites.load(self.path)
        cases = [
            (lambda d: d.update(extra=1), "unexpected field"),
            (lambda d: d.update(schema_version=2), "migration refused"),
            (lambda d: d.update(schema_version="1"), "migration refused"),
            (lambda d: d.update(schema="torchsynth-bookmark"), "incorrect constant"),
            (lambda d: d.update(kind="mixin"), "unknown value"),
            (lambda d: d["artifact"].update(ref="/etc/passwd"), "invalid string"),
            (lambda d: d["artifact"].update(ref="../metadata.json"), "invalid string"),
            (
                lambda d: d["recorded"]["source"]["files"].update(
                    {"../escape/synth.py": "0" * 64}
                ),
                "invalid string",
            ),
            (
                lambda d: d["parameters"]["locks_physical"].update({PITCH: 60.0}),
                "locks must match resolved physical values",
            ),
            (lambda d: d.update(notes="   "), "nonempty"),
        ]
        for change, message in cases:
            with self.subTest(message=message):
                path = self.rewritten(change)
                with self.assertRaisesRegex(FavoriteError, message):
                    favorites.load(path)

    def test_portability_scan_refuses_locator_paths_outside_notes(self):
        for value in ("/etc/passwd", "\\Windows", "C:/escape", "a/../b"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(FavoriteError, "portable"):
                    favorites._check_portable({"artifact": {"ref": value}})
        favorites._check_portable(
            {
                "notes": "see /tmp/scratch or .. for context",
                "audio": {"sha256": "0" * 64},
            }
        )

    def test_recall_reverifies_exact_artifact_with_zero_rerender(self):
        favorites.save(self.favorite, self.path, store=self.store)
        renderer = Mock()
        restarted = ExplorerSession(
            self.store, allowed_indices=[6, 31], renderer=renderer, rng=Mock()
        )
        recalled = favorites.recall(restarted, self.path, store=self.store)
        self.assertEqual(recalled.inputs, self.session.selection.inputs)
        self.assertEqual(restarted.repeat(), recalled)
        self.assertEqual(recalled.stored.reference, self.stored.reference)
        self.assertEqual(renderer.mock_calls, [])
        self.assertEqual(restarted.repeat().inputs, self.session.selection.inputs)
        hashes = {
            path.name: path.read_bytes()
            for path in sorted(self.stored.path.rglob("*"))
            if path.is_file()
        }
        again = favorites.recall(restarted, self.path, store=self.store)
        self.assertEqual(again, recalled)
        self.assertEqual(
            {
                path.name: path.read_bytes()
                for path in sorted(self.stored.path.rglob("*"))
                if path.is_file()
            },
            hashes,
        )

    def test_recall_refuses_drift_before_any_store_access(self):
        with patch.object(self.store, "verify", wraps=self.store.verify) as verify:
            cases = [
                (
                    lambda d: d["recorded"]["profile"].update(contract_sha256="0" * 64),
                    "profile changed",
                ),
                (
                    lambda d: d["recorded"]["source"]["files"].update(
                        {"extra/relocated.py": "0" * 64}
                    ),
                    "source changed",
                ),
                (
                    lambda d: d["recorded"]["source"].update(manifest_sha256="0" * 64),
                    "source changed",
                ),
                (
                    lambda d: d["parameters"]["physical_by_name"].update(
                        {PITCH: 10**6}
                    ),
                    "range changed",
                ),
                (
                    lambda d: d["parameters"]["physical_by_name"].update(
                        {"synthetic.renamed": 0.5}
                    ),
                    "names changed",
                ),
                (
                    lambda d: d["parameters"]["physical_by_name"].pop(PITCH),
                    "names changed",
                ),
            ]
            for change, message in cases:
                path = self.rewritten(change, "drift.json")
                loaded = favorites.load(path)  # Loading is environment-free.
                self.assertEqual(loaded.kind, "capture")
                with self.subTest(message=message):
                    with self.assertRaisesRegex(FavoriteError, message):
                        favorites.recall(self.session, path, store=self.store)
            verify.assert_not_called()
        self.assertEqual(self.session.selection.stored, self.stored)

    def test_recall_refuses_disagreeing_records_and_preserves_selection(self):
        favorites.save(self.favorite, self.path, store=self.store)
        in_range_fixture(self.base / "second", 31)
        second = self.store.publish(self.base / "second")
        self.session.select(31, second.reference)
        cases = [
            (
                lambda d: d["parameters"]["normalized_by_name"].update(
                    {"adsr_1.attack": 0.25}
                ),
                "patch disagrees",
            ),
            (
                lambda d: d["parameters"]["physical_by_name"].update(
                    {"adsr_1.attack": 0.25}
                ),
                "patch disagrees",
            ),
            (lambda d: d["audio"].update(sha256="0" * 64), "audio disagrees"),
            (
                lambda d: d["artifact"].update(sha256="0" * 64),
                "SHA-256 mismatch",
            ),
        ]
        for change, message in cases:
            path = self.rewritten(change, "disagree.json")
            with self.subTest(message=message):
                with self.assertRaisesRegex(FavoriteError, message):
                    favorites.recall(self.session, path, store=self.store)
            self.assertEqual(self.session.selection.stored, second)

    def test_variation_is_deterministic_seeded_and_locks_survive(self):
        parent = favorites.capture(self.session)
        first = favorites.vary(
            parent, seed=7, amount=0.25, locks={PITCH: 60.0}, notes="v"
        )
        second = favorites.vary(
            parent, seed=7, amount=0.25, locks={PITCH: 60.0}, notes="v"
        )
        self.assertEqual(first.document, second.document)
        self.assertEqual(first.favorite_id, second.favorite_id)
        other = favorites.vary(parent, seed=8, amount=0.25, locks={PITCH: 60.0})
        self.assertNotEqual(other.document, first.document)
        self.assertNotEqual(other.physical, parent.physical)
        self.assertEqual(first.physical[PITCH], 60.0)
        self.assertEqual(first.parent_id, parent.favorite_id)
        self.assertEqual(first.document["variation"], {"seed": 7, "amount": 0.25})
        self.assertEqual(first.document["recorded"], parent.document["recorded"])
        self.assertEqual(first.sound_index, parent.sound_index)
        self.assertNotIn("normalized_by_name", first.document["parameters"])
        self.assertNotIn("artifact", first.document)
        relocked = favorites.vary(parent, seed=7, amount=0.0, locks={PITCH: 61.5})
        self.assertEqual(relocked.physical, {**parent.physical, PITCH: 61.5})
        self.assertEqual(relocked.locks, {PITCH: 61.5})
        favorites.save(first, self.base / "variation.json", store=self.store)
        self.assertEqual(
            favorites.load(self.base / "variation.json").document, first.document
        )

    def test_variation_refusals_cover_unknown_duplicate_and_out_of_range_locks(self):
        parent = favorites.capture(self.session)
        locked = favorites.vary(parent, seed=1, amount=0.0, locks={PITCH: 60.0})
        attack_range = ranges()["adsr_1.attack"]
        cases = [
            ({"synthetic.unknown": 1.0}, "unknown parameter lock"),
            ({PITCH: 61.0}, "already locked"),
            ({"adsr_1.attack": attack_range[1] + 1.0}, "lock outside range"),
            ({"adsr_1.attack": attack_range[0] - 1.0}, "lock outside range"),
            ({"adsr_1.attack": float("nan")}, "invalid physical lock"),
        ]
        for locks, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(FavoriteError, message):
                    favorites.vary(locked, seed=1, amount=0.25, locks=locks)
        for kwargs in (
            {"seed": -1},
            {"seed": True},
            {"seed": 1.0},
            {"amount": -0.1},
            {"amount": 1.5},
            {"amount": float("nan")},
            {"amount": True},
        ):
            with self.subTest(kwargs=kwargs):
                parameters = {"seed": 1, "amount": 0.25}
                parameters.update(kwargs)
                with self.assertRaises(FavoriteError):
                    favorites.vary(locked, **parameters)

    def test_variation_lineage_chains_and_never_recalls_as_selection(self):
        child = favorites.vary(self.favorite, seed=5, amount=0.5, locks={PITCH: 60.0})
        grandchild = favorites.vary(child, seed=6, amount=0.5)
        self.assertEqual(grandchild.parent_id, child.favorite_id)
        self.assertEqual(grandchild.locks, {PITCH: 60.0})
        path = self.base / "variation.json"
        favorites.save(child, path, store=self.store)
        selected = self.session.selection
        with self.assertRaisesRegex(FavoriteError, "patch specifications"):
            favorites.recall(self.session, path, store=self.store)
        self.assertEqual(self.session.selection, selected)
        self.assertEqual(favorites.load(path).document, child.document)

    def test_favorites_for_holdout_identities_are_refused(self):
        document = copy.deepcopy(self.favorite.document)
        document["sound_index"] = 96
        document["favorite_id"] = favorite_id(document)
        path = write_document(self.base / "holdout.json", document)
        with self.assertRaisesRegex(FavoriteError, "reserved holdout"):
            favorites.load(path)
        with patch.object(self.store, "verify", wraps=self.store.verify) as verify:
            with self.assertRaisesRegex(FavoriteError, "reserved holdout"):
                favorites.recall(self.session, path, store=self.store)
            verify.assert_not_called()


class FavoriteCLITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.store = self.base / "store"
        self.favorite = self.base / "favorite.json"
        self.variation = self.base / "variation.json"

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

    def saved_reference(self):
        store = ArtifactStore(self.store)
        in_range_fixture(self.base / "source")
        stored = store.publish(self.base / "source")
        self.payload(
            self.cli(
                *self.fake_args(
                    "--selected",
                    "6",
                    stored.artifact_id,
                    stored.sha256,
                    "favorite-save",
                    str(self.favorite),
                    "--notes",
                    "cli favorite",
                )
            )
        )
        return stored.reference

    def test_cli_favorite_workflow_zero_rerender_and_vary_determinism(self):
        reference = self.saved_reference()
        self.assertEqual(json.loads(self.favorite.read_bytes())["kind"], "capture")
        recalled = self.payload(
            self.cli(
                *[
                    "--store",
                    str(self.store),
                    "--backend",
                    "none",
                    "favorite-recall",
                    str(self.favorite),
                ]
            )
        )
        self.assertEqual(recalled["session"]["reference"], reference)
        self.assertEqual(recalled["favorite"]["notes"], "cli favorite")
        self.assertIsNone(recalled["preview"])
        vary_args = [
            "--store",
            str(self.store),
            "--backend",
            "none",
            "favorite-vary",
            str(self.favorite),
            str(self.variation),
            "--seed",
            "7",
            "--amount",
            "0.25",
            "--lock",
            f"{PITCH}=60.5",
        ]
        first = self.payload(self.cli(*vary_args))["favorite"]
        second = self.payload(self.cli(*vary_args))["favorite"]
        self.assertEqual(first, second)
        self.assertEqual(first["parent_id"], recalled["favorite"]["favorite_id"])
        self.assertEqual(first["parameters"]["locks_physical"], {PITCH: 60.5})
        refused = self.cli(
            *[
                "--store",
                str(self.store),
                "--backend",
                "none",
                "favorite-recall",
                str(self.variation),
            ]
        )
        self.assertEqual(refused.returncode, 2)
        self.assertIn("patch specifications", refused.stderr)
        inspected = self.payload(
            self.cli(
                *["--store", str(self.store), "favorite-inspect", str(self.variation)]
            )
        )
        self.assertEqual(inspected["favorite"], first)

    def test_cli_favorite_failures_are_actionable_and_stdout_stays_empty(self):
        reference = self.saved_reference()
        cases = [
            (
                self.fake_args(
                    "--selected",
                    "6",
                    reference["artifact_id"],
                    reference["sha256"],
                    "favorite-save",
                    str(self.favorite),
                    "--lock",
                    f"{PITCH}=60",
                ),
                "unrecognized arguments",
            ),
            (
                self.fake_args(
                    "favorite-vary",
                    str(self.favorite),
                    str(self.variation),
                    "--seed",
                    "1",
                    "--amount",
                    "0.25",
                    "--lock",
                    f"{PITCH}=60",
                    "--lock",
                    f"{PITCH}=61",
                ),
                "duplicate lock",
            ),
            (
                self.fake_args(
                    "favorite-vary",
                    str(self.favorite),
                    str(self.variation),
                    "--seed",
                    "1",
                    "--amount",
                    "0.25",
                    "--lock",
                    "synthetic.unknown=1",
                ),
                "unknown parameter lock",
            ),
            (
                self.fake_args(
                    "favorite-vary",
                    str(self.favorite),
                    str(self.variation),
                    "--seed",
                    "1",
                    "--amount",
                    "0.25",
                    "--lock",
                    f"{PITCH}=999",
                ),
                "lock outside range",
            ),
        ]
        for args, message in cases:
            with self.subTest(message=message):
                failed = self.cli(*args)
                self.assertEqual(failed.returncode, 2)
                self.assertIn(message, failed.stderr)
                self.assertEqual(failed.stdout, "")
        missing = self.cli(*self.fake_args("favorite-save", str(self.favorite)))
        self.assertEqual(missing.returncode, 2)
        self.assertIn("no selection", missing.stderr)
        self.assertEqual(missing.stdout, "")


if __name__ == "__main__":
    unittest.main()
