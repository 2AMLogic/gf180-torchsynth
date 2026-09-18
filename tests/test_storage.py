from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import subprocess
import sys
import tempfile
import traceback
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.artifacts import (  # noqa: E402
    ValidationError,
    canonical_bytes,
    content_id,
    loads,
    validate_artifact,
)
from torchsynth_voice.storage import ArtifactStore, CollisionError  # noqa: E402


def digest(data):
    return hashlib.sha256(data).hexdigest()


def fixture(directory, *, alternate=False, scalar=False):
    """Synthetic protocol fixture; no renderer, runtime, or sound qualification."""
    directory.mkdir()
    record = loads((ROOT / "tests/fixtures/artifacts/complete.json").read_bytes())
    inputs = record["inputs"]["value"]
    inputs["requested_traces"] = ["synthetic-envelope", "synthetic-oscillator"]
    if scalar:
        inputs["execution"] = {
            "mode": "resolved-scalar",
            "batch_size": 1,
            "reproducible": False,
        }
    record["artifact_id"] = content_id(inputs)
    files = {"audio.f32le": bytes(176400 * 4)}
    files["traces/envelope.bin"] = b"changed" if alternate else b"envelope"
    files["traces/oscillator.bin"] = b"oscillator"

    def reference(name):
        return {
            "ref": name,
            "sha256": digest(files[name]),
            "size_bytes": len(files[name]),
        }

    record["audio"]["value"]["file"] = reference("audio.f32le")
    record["traces"]["value"] = {
        "synthetic-envelope": reference("traces/envelope.bin"),
        "synthetic-oscillator": reference("traces/oscillator.bin"),
    }
    validate_artifact(record)
    for name, data in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    save_metadata(directory, record)
    return record


def save_metadata(directory, record):
    (directory / "metadata.json").write_bytes(
        (json.dumps(record, indent=2, allow_nan=False) + "\n").encode()
    )


def writer(root, source, barrier, result):
    # Spawned processes synchronize immediately before the publication rename.
    from torchsynth_voice import storage

    original = storage._inspect

    def inspect(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        if Path(path).parent.name == ".staging":
            result.put(("ready", value[0]))
            barrier.wait(timeout=15)
        return value

    try:
        with patch.object(storage, "_inspect", inspect):
            stored = ArtifactStore(root).publish(source)
        result.put(("ok", stored.resumed, stored.sha256))
    except CollisionError as error:
        result.put(("collision", error.existing_sha256, error.candidate_sha256))
    except BaseException as error:
        result.put(("error", repr(error), traceback.format_exc()))


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        # macOS system temp ancestors can be aliases; choose the physical base.
        self.base = Path(self.temporary.name).resolve()
        self.source = self.base / "source"
        self.record = fixture(self.source)
        self.store = ArtifactStore(self.base / "store")

    def assert_empty(self):
        self.assertEqual(self.store.discover(), ())
        self.assertEqual(list((self.store.root / ".staging").iterdir()), [])

    def test_publish_verify_reference_and_exact_resume_without_rewrite(self):
        first = self.store.publish(self.source)
        before = {
            path.relative_to(first.path): (
                path.read_bytes(),
                path.stat().st_ino,
                path.stat().st_mtime_ns,
            )
            for path in first.path.rglob("*")
            if path.is_file()
        }
        second = self.store.publish(self.source)
        self.assertFalse(first.resumed)
        self.assertTrue(second.resumed)
        self.assertEqual(first.artifact_id, self.record["artifact_id"])
        self.assertEqual(
            first.sha256, digest((self.source / "metadata.json").read_bytes())
        )
        self.assertNotEqual(first.sha256, digest(canonical_bytes(self.record)))
        self.assertEqual(
            first.reference,
            {
                "artifact_id": first.artifact_id,
                "sha256": first.sha256,
                "ref": f"artifacts/{first.artifact_id}/metadata.json",
            },
        )
        self.assertEqual(
            (self.store.root / first.reference["ref"]).read_bytes(),
            (self.source / "metadata.json").read_bytes(),
        )
        self.assertEqual(
            self.store.verify(first.artifact_id, sha256=first.sha256).sha256,
            first.sha256,
        )
        self.assertEqual(len(self.store.discover()), 1)
        for name, expected in before.items():
            path = first.path / name
            self.assertEqual(
                (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns),
                expected,
            )
        with self.assertRaises(ValidationError):
            self.store.verify(first.artifact_id, sha256="0" * 64)

    def test_scalar_execution_remains_distinct_from_batch_fixture(self):
        scalar = self.base / "scalar"
        fixture(scalar, scalar=True)
        self.assertNotEqual(
            self.store.publish(scalar).artifact_id,
            self.store.publish(self.source).artifact_id,
        )

    def test_collision_reports_both_metadata_and_changed_output_hashes(self):
        first = self.store.publish(self.source)
        other = self.base / "other"
        fixture(other, alternate=True)
        with self.assertRaises(CollisionError) as caught:
            self.store.publish(other)
        error = caught.exception
        self.assertEqual(error.artifact_id, first.artifact_id)
        self.assertEqual(error.existing_sha256, first.sha256)
        self.assertEqual(
            error.candidate_sha256, digest((other / "metadata.json").read_bytes())
        )
        for data in (b"envelope", b"changed"):
            self.assertIn(digest(data), str(error))
        self.assertIn(first.sha256, str(error))
        self.assertIn(error.candidate_sha256, str(error))
        self.assertEqual(self.store.verify(first.artifact_id).sha256, first.sha256)

    def test_metadata_whitespace_only_change_is_a_collision(self):
        self.store.publish(self.source)
        metadata = self.source / "metadata.json"
        metadata.write_bytes(metadata.read_bytes() + b"\n")
        with self.assertRaises(CollisionError):
            self.store.publish(self.source)

    def test_invalid_metadata_and_duplicate_keys_fail_before_publication(self):
        metadata = self.source / "metadata.json"
        original = metadata.read_bytes()
        for data in (
            b"{",
            original.replace(
                b'"status": "complete"', b'"status": "complete", "status": "complete"'
            ),
            original.replace(b'"schema_version": 1', b'"schema_version": 2'),
            original.replace(b'"status": "complete"', b'"status": "failed"'),
        ):
            with self.subTest(data=data[:50]):
                metadata.write_bytes(data)
                with self.assertRaises(ValidationError):
                    self.store.publish(self.source)
                self.assert_empty()

    def test_missing_extra_truncated_corrupt_files_and_empty_directories(self):
        for name in (
            "metadata.json",
            "audio.f32le",
            "traces/envelope.bin",
            "traces/oscillator.bin",
        ):
            for damage in ("missing", "truncated", "corrupt"):
                with self.subTest(name=name, damage=damage):
                    target = self.source / name
                    original = target.read_bytes()
                    if damage == "missing":
                        target.unlink()
                    elif damage == "truncated":
                        target.write_bytes(original[:-1])
                    else:
                        target.write_bytes(b"X" + original[1:])
                    # A removed JSON trailing newline is still valid metadata.
                    if name == "metadata.json" and damage == "truncated":
                        target.write_bytes(original[:10])
                    with self.assertRaises(ValidationError):
                        self.store.publish(self.source)
                    target.write_bytes(original)
                    self.assert_empty()
        for extra in ("extra.bin", "traces/extra.bin", "empty"):
            path = self.source / extra
            path.mkdir() if extra == "empty" else path.write_bytes(b"extra")
            with self.assertRaises(ValidationError):
                self.store.publish(self.source)
            path.rmdir() if extra == "empty" else path.unlink()
            self.assert_empty()

    def test_corrupt_existing_artifact_never_resumes_or_gets_replaced(self):
        stored = self.store.publish(self.source)
        for name in ("metadata.json", "audio.f32le", "traces/oscillator.bin"):
            target = stored.path / name
            original = target.read_bytes()
            target.write_bytes(b"corrupt")
            for action in (
                lambda: self.store.publish(self.source),
                lambda: self.store.verify(stored.artifact_id),
                self.store.discover,
            ):
                with self.assertRaises(ValidationError):
                    action()
                self.assertEqual(target.read_bytes(), b"corrupt")
            target.write_bytes(original)

    def test_path_traversal_aliases_and_reserved_metadata_are_rejected(self):
        reference = self.record["traces"]["value"]["synthetic-envelope"]
        for name in (
            "../escape",
            "/escape",
            "C:/escape",
            "traces//envelope.bin",
            "traces/../audio.f32le",
            "traces\\envelope.bin",
            "metadata.json",
            "metadata.json/child",
            "audio.f32le",
        ):
            with self.subTest(name=name):
                reference["ref"] = name
                save_metadata(self.source, self.record)
                with self.assertRaises(ValidationError):
                    self.store.publish(self.source)
                self.assert_empty()
        for identity in (
            "../source",
            self.record["artifact_id"] + "/..",
            "ra1-" + "a" * 64 + "\n",
        ):
            with self.assertRaises(ValidationError):
                self.store.verify(identity)

    def test_symlinks_hardlinks_and_fifo_are_rejected_on_publish_and_verify(self):
        stored = self.store.publish(self.source)
        for directory in (self.source, stored.path):
            for name in (
                "metadata.json",
                "audio.f32le",
                "traces/envelope.bin",
                "traces",
            ):
                with self.subTest(directory=directory.name, name=name):
                    path = directory / name
                    outside = self.base / "outside"
                    path.rename(outside)
                    path.symlink_to(outside)
                    with self.assertRaises(ValidationError):
                        if directory == self.source:
                            self.store.publish(directory)
                        else:
                            self.store.verify(stored.artifact_id)
                    path.unlink()
                    outside.rename(path)
            path = directory / "traces/envelope.bin"
            original = path.read_bytes()
            alias = self.base / "hardlink"
            os.link(path, alias)  # Bytes/hash/size stay correct; only aliasing changes.
            with self.assertRaises(ValidationError):
                self.store.publish(
                    directory
                ) if directory == self.source else self.store.verify(stored.artifact_id)
            alias.unlink()
            path.unlink()
            os.mkfifo(path)
            with self.assertRaises(ValidationError):
                self.store.publish(
                    directory
                ) if directory == self.source else self.store.verify(stored.artifact_id)
            path.unlink()
            path.write_bytes(original)

    def test_root_ancestor_and_store_component_symlinks_are_rejected(self):
        alias = self.base / "alias"
        alias.symlink_to(self.base, target_is_directory=True)
        for source in (alias / "source",):
            with self.assertRaises(ValidationError):
                self.store.publish(source)
        with self.assertRaises(ValidationError):
            ArtifactStore(alias / "new-store")
        for name in (".staging", "artifacts", ".publish.lock"):
            path = self.store.root / name
            original = self.base / "original"
            if path.exists():
                path.rename(original)
            else:
                original.write_bytes(b"")
            path.symlink_to(original)
            with self.assertRaises(ValidationError):
                self.store.publish(self.source)
            path.unlink()
            original.rename(path)
        stored = self.store.publish(self.source)
        moved = self.base / "completed"
        stored.path.rename(moved)
        stored.path.symlink_to(moved)
        with self.assertRaises(ValidationError):
            self.store.verify(stored.artifact_id)

    def test_exception_cleanup_only_removes_own_staging(self):
        peer = self.store.root / ".staging/peer"
        peer.mkdir()
        (peer / "keep").write_bytes(b"peer")
        with patch("torchsynth_voice.storage.os.rename", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.store.publish(self.source)
        self.assertEqual(self.store.discover(), ())
        self.assertEqual(list(peer.parent.iterdir()), [peer])
        self.assertEqual((peer / "keep").read_bytes(), b"peer")
        self.assertFalse(self.store.publish(self.source).resumed)

    def test_process_exit_before_rename_is_not_discoverable_and_retry_succeeds(self):
        script = """
import os, sys
from unittest.mock import patch
from torchsynth_voice.storage import ArtifactStore
with patch('torchsynth_voice.storage.os.rename', side_effect=lambda *a, **k: os._exit(73)):
    ArtifactStore(sys.argv[1]).publish(sys.argv[2])
"""
        result = subprocess.run(
            [sys.executable, "-c", script, str(self.store.root), str(self.source)],
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
            timeout=20,
        )
        self.assertEqual(result.returncode, 73)
        self.assertEqual(self.store.discover(), ())
        abandoned = set((self.store.root / ".staging").iterdir())
        self.assertEqual(len(abandoned), 1)
        self.assertFalse(self.store.publish(self.source).resumed)
        self.assertEqual(set((self.store.root / ".staging").iterdir()), abandoned)

    def test_process_exit_during_copy_leaves_only_incomplete_private_stage(self):
        script = """
import os, sys
from unittest.mock import patch
from torchsynth_voice import storage
read = storage._read_regular
def interrupted(fd, name):
    if name == 'envelope.bin':
        os._exit(74)
    return read(fd, name)
with patch.object(storage, '_read_regular', interrupted):
    storage.ArtifactStore(sys.argv[1]).publish(sys.argv[2])
"""
        result = subprocess.run(
            [sys.executable, "-c", script, str(self.store.root), str(self.source)],
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
            timeout=20,
        )
        self.assertEqual(result.returncode, 74)
        self.assertEqual(self.store.discover(), ())
        stages = list((self.store.root / ".staging").iterdir())
        self.assertEqual(len(stages), 1)
        self.assertTrue((stages[0] / "audio.f32le").is_file())
        self.assertFalse((stages[0] / "traces/envelope.bin").exists())
        self.assertFalse(self.store.publish(self.source).resumed)

    def test_failure_after_atomic_rename_can_resume_complete_bytes(self):
        original = os.rename

        def interrupted(*args, **kwargs):
            original(*args, **kwargs)
            raise KeyboardInterrupt

        with patch("torchsynth_voice.storage.os.rename", interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.store.publish(self.source)
        self.assertEqual(len(self.store.discover()), 1)
        self.assertTrue(self.store.publish(self.source).resumed)
        self.assertEqual(list((self.store.root / ".staging").iterdir()), [])

    def test_two_process_writers_converge_or_report_collision(self):
        context = multiprocessing.get_context("spawn")
        for conflicting in (False, True):
            with self.subTest(conflicting=conflicting):
                root = self.base / f"concurrent-{conflicting}"
                other = self.base / f"other-{conflicting}"
                fixture(other, alternate=conflicting)
                barrier, results = context.Barrier(3), context.Queue()
                processes = [
                    context.Process(
                        target=writer, args=(root, source, barrier, results)
                    )
                    for source in (self.source, other)
                ]
                try:
                    for process in processes:
                        process.start()
                    ready = [results.get(timeout=25) for _ in processes]
                    self.assertEqual(
                        [item[0] for item in ready], ["ready", "ready"], ready
                    )
                    self.assertEqual(ArtifactStore(root).discover(), ())
                    barrier.wait(timeout=15)
                    outcomes = [results.get(timeout=25) for _ in processes]
                    for process in processes:
                        process.join(timeout=10)
                        self.assertEqual(process.exitcode, 0)
                    self.assertEqual(
                        sorted(item[0] for item in outcomes),
                        ["collision", "ok"] if conflicting else ["ok", "ok"],
                        outcomes,
                    )
                    if not conflicting:
                        self.assertEqual(
                            sorted(item[1] for item in outcomes), [False, True]
                        )
                    self.assertEqual(len(ArtifactStore(root).discover()), 1)
                    self.assertEqual(list((root / ".staging").iterdir()), [])
                finally:
                    for process in processes:
                        if process.is_alive():
                            process.terminate()
                            process.join(timeout=10)
                    results.close()
                    results.join_thread()

    def test_storage_operates_without_site_packages_or_renderer_imports(self):
        script = """
import sys
sys.path.insert(0, sys.argv[1])
from torchsynth_voice.storage import ArtifactStore
store = ArtifactStore(sys.argv[2])
result = store.publish(sys.argv[3])
store.verify(result.artifact_id, sha256=result.sha256)
assert not {'torch', 'numpy', 'torchsynth', 'lightning', 'torchsynth_voice.render'} & sys.modules.keys()
"""
        result = subprocess.run(
            [
                sys.executable,
                "-S",
                "-c",
                script,
                str(ROOT / "src"),
                str(self.store.root),
                str(self.source),
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
