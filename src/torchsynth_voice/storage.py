"""Atomic local POSIX storage for validated v1 artifacts; no renderer imports.

See spec/ARTIFACT-STORAGE.md for layout, exact-byte resume, and filesystem scope.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import shutil
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .artifacts import (
    ValidationError,
    content_id,
    loads,
    validate_artifact,
    verify_sha256,
)

METADATA = "metadata.json"
_ID = re.compile(r"ra1-[a-f0-9]{64}")
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class StorageError(ValidationError):
    """An artifact's files or the store's filesystem layout are invalid."""


class CollisionError(StorageError):
    """One input identity has two different, individually valid byte sets."""

    def __init__(self, artifact_id, existing, candidate):
        self.artifact_id = artifact_id
        self.existing_sha256 = existing[METADATA]
        self.candidate_sha256 = candidate[METADATA]
        self.differences = {
            name: (existing.get(name), candidate.get(name))
            for name in sorted(existing.keys() | candidate.keys())
            if existing.get(name) != candidate.get(name)
        }
        details = "; ".join(
            f"{name}: existing={old or 'missing'} candidate={new or 'missing'}"
            for name, (old, new) in self.differences.items()
        )
        super().__init__(f"content collision for {artifact_id}: {details}")


@dataclass(frozen=True)
class StoredArtifact:
    artifact_id: str
    path: Path
    sha256: str
    resumed: bool = False

    @property
    def reference(self) -> dict[str, str]:
        """Portable reference relative to the store root; hashes exact metadata."""
        return {
            "artifact_id": self.artifact_id,
            "sha256": self.sha256,
            "ref": f"artifacts/{self.artifact_id}/{METADATA}",
        }


@contextmanager
def _directory(path: Path, *, create: bool = False):
    """Walk physical absolute components using directory FDs, never symlinks."""
    path = Path(os.path.abspath(path))
    fd = os.open(path.anchor, _DIRECTORY)
    try:
        for component in path.parts[1:]:
            if create:
                try:
                    os.mkdir(component, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(component, _DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    except OSError as error:
        raise StorageError(f"filesystem operation refused: {error.strerror}") from error
    finally:
        os.close(fd)


def _read_regular(fd: int, name: str) -> bytes:
    # NONBLOCK prevents opening a substituted FIFO from hanging before fstat.
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise StorageError(f"{name}: expected an unaliased regular file")
        data = stream.read()
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) or len(data) != after.st_size:
            raise StorageError(f"{name}: file changed during reading")
        return data


def _inspect(path: Path, destination: Path | None = None):
    """Validate every entry, optionally copying checked bytes into a private stage."""
    with _directory(path) as root_fd:
        metadata = _read_regular(root_fd, METADATA)
        record = loads(metadata)
        validate_artifact(record)
        identity = content_id(record["inputs"]["value"])
        references = [
            record["audio"]["value"]["file"],
            *record["traces"]["value"].values(),
        ]
        expected = {
            METADATA: {
                "size_bytes": len(metadata),
                "sha256": hashlib.sha256(metadata).hexdigest(),
            }
        }
        for reference in references:
            name = reference["ref"]  # Syntax has already passed validate_artifact.
            if name in expected:
                raise StorageError(f"{name}: duplicate or reserved file reference")
            expected[name] = reference
        directories = {
            str(parent)
            for name in expected
            for parent in PurePosixPath(name).parents
            if str(parent) != "."
        }
        if directories & expected.keys():
            raise StorageError("file reference is also a directory")
        hashes = {}

        def walk(fd, prefix=""):
            for name in sorted(os.listdir(fd)):
                relative = prefix + name
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    if relative not in directories:
                        raise StorageError(f"{relative}: extra directory")
                    child = os.open(name, _DIRECTORY, dir_fd=fd)
                    try:
                        if destination is not None:
                            (destination / relative).mkdir()
                        walk(child, relative + "/")
                    finally:
                        os.close(child)
                else:
                    if relative not in expected:
                        raise StorageError(f"{relative}: extra file")
                    data = _read_regular(fd, name)
                    reference = expected[relative]
                    if len(data) != reference["size_bytes"]:
                        raise StorageError(f"{relative}: byte count mismatch")
                    verify_sha256(data, reference["sha256"])
                    hashes[relative] = hashlib.sha256(data).hexdigest()
                    if destination is not None:
                        with (destination / relative).open("xb") as output:
                            output.write(data)
                            output.flush()
                            os.fsync(output.fileno())
            if destination is not None:
                with _directory(destination / prefix) as output_fd:
                    os.fsync(output_fd)

        walk(root_fd)
        if hashes.keys() != expected.keys():
            raise StorageError(
                f"missing files: {', '.join(sorted(expected.keys() - hashes.keys()))}"
            )
        return identity, hashes


class ArtifactStore:
    """A cooperative-writer store on a local POSIX filesystem.

    Roots and source directories must have no symlink components. Caller-owned
    source directories are copied, never renamed, deleted, or modified.
    """

    def __init__(self, root: str | Path):
        self.root = Path(os.path.abspath(root))
        for path in (self.root, self.root / ".staging", self.root / "artifacts"):
            with _directory(path, create=True):
                pass

    @contextmanager
    def _lock(self):
        with _directory(self.root) as root_fd:
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
            try:
                descriptor = os.open(
                    ".publish.lock",
                    flags | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=root_fd,
                )
            except FileExistsError:
                descriptor = os.open(".publish.lock", flags, dir_fd=root_fd)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise StorageError(
                        "publication lock must be an unaliased regular file"
                    )
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            finally:
                # Never unlink: all publishers must keep locking the same inode.
                os.close(descriptor)

    def _verified(self, artifact_id):
        if type(artifact_id) is not str or _ID.fullmatch(artifact_id) is None:
            raise StorageError("invalid artifact ID")
        path = self.root / "artifacts" / artifact_id
        identity, hashes = _inspect(path)
        if identity != artifact_id:
            raise StorageError("directory name disagrees with artifact identity")
        return StoredArtifact(identity, path, hashes[METADATA]), hashes

    def verify(self, artifact_id: str, *, sha256: str | None = None) -> StoredArtifact:
        """Revalidate all bytes; optionally pin exact metadata to an external ref."""
        stored, _ = self._verified(artifact_id)
        if sha256 is not None:
            # Compare the digest from the exact bytes already validated, not a
            # second open that could observe a different metadata revision.
            if (
                type(sha256) is not str
                or re.fullmatch(r"[a-f0-9]{64}", sha256) is None
                or stored.sha256 != sha256
            ):
                raise StorageError("metadata SHA-256 mismatch")
        return stored

    def discover(self) -> tuple[StoredArtifact, ...]:
        """Return validated completed artifacts; corruption raises, never skips."""
        with _directory(self.root / "artifacts") as fd:
            names = sorted(os.listdir(fd))
        return tuple(self.verify(name) for name in names)

    def publish(self, source: str | Path) -> StoredArtifact:
        """Snapshot, validate, atomically publish; exact bytes resume unchanged."""
        staging = self.root / ".staging"
        with _directory(staging), _directory(self.root / "artifacts"):
            stage = Path(tempfile.mkdtemp(prefix="render-", dir=staging))
            try:
                _inspect(Path(source), destination=stage)
                identity, hashes = _inspect(stage)
                target = self.root / "artifacts" / identity
                with self._lock(), _directory(self.root / "artifacts") as completed_fd:
                    with _directory(staging) as staging_fd:
                        if os.path.lexists(target):
                            existing, existing_hashes = self._verified(identity)
                            if existing_hashes != hashes:
                                raise CollisionError(identity, existing_hashes, hashes)
                            return StoredArtifact(
                                identity, target, existing.sha256, resumed=True
                            )
                        # Every compliant writer holds this stable lock across the
                        # existence check and rename. Never replace corrupt entries.
                        os.rename(
                            stage.name,
                            identity,
                            src_dir_fd=staging_fd,
                            dst_dir_fd=completed_fd,
                        )
                        os.fsync(completed_fd)
                        os.fsync(staging_fd)
                return StoredArtifact(identity, target, hashes[METADATA])
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
