"""Verified local artifact selection; injected rendering/audition, never DSP.

See spec/EXPLORER-SESSION.md for the cooperative local filesystem boundary.
"""

from __future__ import annotations

import json
import os
import random
import re
import stat
import tempfile
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Callable, Iterable, Mapping, Protocol

from .artifacts import loads, validate_artifact, verify_sha256
from .contract import repository_root
from .inventory import INVENTORY_PATH, load_json, validate_inventory
from .storage import METADATA, ArtifactStore, StoredArtifact


class SessionError(ValueError):
    """An action failed; its previous valid selection has not been replaced."""


class SelectionRNG(Protocol):
    def choice(self, indices: tuple[int, ...]) -> int: ...


Renderer = Callable[[int], Mapping[str, str]]
Player = Callable[[StoredArtifact], None]


def _identity(index: int) -> None:
    if type(index) is not int or not 0 <= index <= 2**53 - 1:
        raise SessionError("sound_index must be an integer in [0, 2^53-1]")
    if 96 <= index < 128:
        raise SessionError(f"sound_index {index} is reserved holdout; access refused")


def _reference(reference: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(reference, Mapping) or set(reference) != {
        "artifact_id",
        "sha256",
        "ref",
    }:
        raise SessionError("reference requires exactly artifact_id, sha256 and ref")
    reference = dict(reference)
    for field, pattern in (
        ("artifact_id", r"ra1-[a-f0-9]{64}"),
        ("sha256", r"[a-f0-9]{64}"),
    ):
        if (
            type(reference[field]) is not str
            or re.fullmatch(pattern, reference[field]) is None
        ):
            raise SessionError(f"invalid reference {field}")
    expected = f"artifacts/{reference['artifact_id']}/{METADATA}"
    if reference["ref"] != expected:
        raise SessionError("ref must be the canonical store-relative metadata path")
    return reference


def _read_file(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise SessionError(f"{path.name}: expected an unaliased regular file")
        return stream.read()


@dataclass(frozen=True)
class Selection:
    """Immutable checked snapshot, not permission to skip subsequent verification."""

    sound_index: int
    stored: StoredArtifact
    metadata_bytes: bytes

    @property
    def inputs(self) -> dict:
        """A fresh copy of the recorded, name-keyed resolved inputs."""
        return loads(self.metadata_bytes)["inputs"]["value"]


def _action(method):
    """Record actionable failures without treating an unrelated success as repair."""

    @wraps(method)
    def run(self, *args, **kwargs):
        try:
            result = method(self, *args, **kwargs)
        except Exception as error:
            message = str(error) or type(error).__name__
            self._last_error = {"operation": method.__name__, "message": message}
            if method.__name__ == "audition":
                self._audition = self._audition_record("failed", message)
            raise SessionError(f"{method.__name__}: {message}") from error
        if self._last_error and self._last_error["operation"] == method.__name__:
            self._last_error = None
        if method.__name__ == "show":
            result["last_error"] = self.last_error
        return result

    return run


class ExplorerSession:
    """Select through ArtifactStore; no runtime or player is implicitly available.

    Allowed identities are copied and sorted. Next starts at the first identity,
    then selects the first greater than the current one; exhaustion never wraps.
    Random draws with replacement using an independent, injectable choice source.
    Only a successful request/select/recall replaces the selected artifact.
    """

    def __init__(
        self,
        store: ArtifactStore,
        *,
        allowed_indices: Iterable[int] | None = None,
        renderer: Renderer | None = None,
        player: Player | None = None,
        rng: SelectionRNG | None = None,
    ):
        if allowed_indices is None:
            corpus = loads(
                (repository_root() / "spec/reference/corpus-v0.json").read_bytes()
            )
            allowed_indices = [
                index
                for case in corpus["cases"]
                if case["split"] == "development"
                for index in range(case["start_inclusive"], case["stop_exclusive"])
            ]
        try:
            indices = tuple(allowed_indices)
            for index in indices:
                _identity(index)
            if not indices or len(set(indices)) != len(indices):
                raise SessionError("allowed identities must be nonempty and unique")
        except (TypeError, ValueError) as error:
            raise SessionError(f"invalid allowed identities: {error}") from error
        inventory = load_json(INVENTORY_PATH)
        validate_inventory(inventory)
        self._names = frozenset(row["name"] for row in inventory["parameters"])
        self._allowed = tuple(sorted(indices))
        self._store = store
        self._renderer = renderer
        self._player = player
        self._rng = random.Random() if rng is None else rng
        self._selection: Selection | None = None
        self._last_error: dict | None = None
        self._audition: dict | None = None

    @property
    def allowed_indices(self) -> tuple[int, ...]:
        return self._allowed

    @property
    def selection(self) -> Selection | None:
        return self._selection

    @property
    def last_error(self) -> dict | None:
        return dict(self._last_error) if self._last_error else None

    def _check_identity(self, index):
        _identity(index)  # Holdout refusal precedes every backend/store access.
        if index not in self._allowed:
            raise SessionError(f"sound_index {index} is outside the allowed identities")

    def _verify(self, index, reference):
        self._check_identity(index)
        reference = _reference(reference)
        stored = self._store.verify(
            reference["artifact_id"], sha256=reference["sha256"]
        )
        if stored.reference != reference:
            raise SessionError(
                "verified store reference disagrees with requested reference"
            )
        data = _read_file(stored.path / METADATA)
        # StoredArtifact carries no parsed metadata. Bind this separate read to
        # the exact external pin, including whitespace and the trailing newline.
        verify_sha256(data, reference["sha256"])
        record = loads(data)
        validate_artifact(record)
        if record["artifact_id"] != stored.artifact_id:
            raise SessionError("metadata artifact identity disagrees with store")
        inputs = record["inputs"]["value"]
        if inputs["fixture"]["sound_index"] != index:
            raise SessionError(
                "requested sound identity disagrees with verified metadata"
            )
        for field in ("normalized_by_name", "physical_by_name"):
            names = set(inputs["parameters"][field])
            if names != self._names:
                raise SessionError(
                    f"{field}: canonical parameter names disagree; "
                    f"missing={sorted(self._names - names)}, extra={sorted(names - self._names)}"
                )
        return Selection(index, stored, data)

    def _current(self):
        if self._selection is None:
            raise SessionError(
                "no selection; select a pinned artifact or recall a bookmark"
            )
        return self._verify(
            self._selection.sound_index, self._selection.stored.reference
        )

    def _request(self, index):
        self._check_identity(index)
        if self._renderer is None:
            raise SessionError(
                "renderer unavailable; select an existing artifact or supply a renderer"
            )
        candidate = self._verify(index, self._renderer(index))
        self._selection = candidate
        return candidate

    @_action
    def request(self, sound_index: int) -> Selection:
        """Ask the injected renderer for one allowed global identity."""
        return self._request(sound_index)

    @_action
    def next(self) -> Selection:
        previous = -1 if self._selection is None else self._selection.sound_index
        for index in self._allowed:
            if index > previous:
                return self._request(index)
        raise SessionError(
            "allowed identities exhausted; select explicitly or use random"
        )

    @_action
    def random(self) -> Selection:
        return self._request(self._rng.choice(self._allowed))

    @_action
    def select(self, sound_index: int, reference: Mapping[str, str]) -> Selection:
        candidate = self._verify(sound_index, reference)
        self._selection = candidate
        return candidate

    @_action
    def repeat(self) -> Selection:
        """Reverify the exact selection; no selection RNG or renderer calls."""
        return self._current()

    @_action
    def show(self) -> dict:
        selected = self._current()
        inputs = selected.inputs
        return {
            "sound_index": selected.sound_index,
            "reference": selected.stored.reference,
            "profile": inputs["profile"],
            "recorded_provenance": {
                field: inputs[field]
                for field in ("runtime", "execution", "renderer_version")
            },
            "parameters": inputs["parameters"],
            "noise": inputs["noise"],
            "artifact_bytes": "verified",
            "runtime_qualification": "not-established",
            "audition": dict(self._audition) if self._audition else None,
            "last_error": self.last_error,
        }

    def _audition_record(self, status, message=None):
        selected = self._selection
        return {
            "status": status,
            "message": message,
            "sound_index": selected.sound_index if selected else None,
            "artifact_id": selected.stored.artifact_id if selected else None,
            "sha256": selected.stored.sha256 if selected else None,
        }

    @_action
    def audition(self) -> None:
        selected = self._current()
        if self._player is None:
            raise SessionError("player unavailable; supply a player to audition")
        if self._player(selected.stored) is not None:
            raise SessionError("player must return None on success or raise on failure")
        self._audition = self._audition_record("succeeded")

    def _bookmark_path(self, path):
        path = Path(os.path.abspath(path))
        # Keep state out of this store's entire namespace, including its staging
        # and lock files. A parent symlink cannot redirect writes into the store.
        if path.is_relative_to(self._store.root) or path.resolve().is_relative_to(
            self._store.root.resolve()
        ):
            raise SessionError("bookmark must be outside the artifact store")
        if path.is_symlink():
            raise SessionError("bookmark must not be a symlink")
        return path

    @_action
    def save(self, path: str | Path) -> None:
        path = self._bookmark_path(path)
        selected = self._current()
        data = (
            json.dumps(
                {
                    "schema_version": 1,
                    "sound_index": selected.sound_index,
                    "artifact": selected.stored.reference,
                },
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)  # Commit point; no rollback after success.
        finally:
            Path(temporary).unlink(missing_ok=True)

    @_action
    def recall(self, path: str | Path) -> Selection:
        record = loads(_read_file(self._bookmark_path(path)))
        if type(record) is not dict or set(record) != {
            "schema_version",
            "sound_index",
            "artifact",
        }:
            raise SessionError(
                "bookmark requires exactly schema_version, sound_index and artifact"
            )
        if type(record["schema_version"]) is not int or record["schema_version"] != 1:
            raise SessionError("unsupported bookmark schema_version; expected 1")
        candidate = self._verify(record["sound_index"], record["artifact"])
        self._selection = candidate
        return candidate
