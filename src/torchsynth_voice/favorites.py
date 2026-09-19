"""Rich favorites: saved sounds, parameter locks, nearby variation, lineage.

Implements issue #65 over the landed verified-selection contracts: favorites
pin `StoredArtifact.reference` objects and recorded name-keyed patches, recall
reverifies the exact pinned artifact with zero renderer calls, and variations
are deterministic seeded perturbations of a parent favorite's physical patch
with locked parameters held fixed. Variations perturb physical values only;
the normalized map is a recorded render output that this layer never
reconstructs (spec/EXPLORER-SESSION.md). See spec/EXPLORER-FAVORITES.md.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import random
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .artifacts import (
    ValidationError,
    canonical_bytes,
    loads,
    validate_document,
    verify_sha256,
)
from .contract import UpstreamContract, repository_root, sha256_file
from .explorer_session import ExplorerSession, Selection, SessionError
from .inventory import INVENTORY_PATH, load_json
from .storage import METADATA, ArtifactStore

SCHEMA_FILE = "favorite-v1.schema.json"
SCHEMA = "torchsynth-favorite"
SCHEMA_VERSION = 1
_CAPTURE_KEYS = {
    "schema",
    "schema_version",
    "kind",
    "favorite_id",
    "parent_id",
    "sound_index",
    "artifact",
    "audio",
    "recorded",
    "parameters",
}
_VARIATION_KEYS = {
    "schema",
    "schema_version",
    "kind",
    "favorite_id",
    "parent_id",
    "sound_index",
    "recorded",
    "parameters",
    "variation",
}
_DRIVE = re.compile(r"^[A-Za-z]:")


class FavoriteError(SessionError):
    """A favorite operation failed; the session's selection is not replaced."""


def favorite_id(document: Mapping) -> str:
    """Content identity of the sound a favorite pins; notes are not identity."""

    stable = {
        key: value
        for key, value in document.items()
        if key not in ("favorite_id", "notes")
    }
    return (
        "fv1-"
        + hashlib.sha256(
            b"torchsynth-favorite-v1\n" + canonical_bytes(stable)
        ).hexdigest()
    )


@dataclass(frozen=True)
class Favorite:
    """A validated favorite document; construct through this module's API."""

    document: dict

    @property
    def favorite_id(self) -> str:
        return self.document["favorite_id"]

    @property
    def kind(self) -> str:
        return self.document["kind"]

    @property
    def parent_id(self) -> str | None:
        return self.document["parent_id"]

    @property
    def sound_index(self) -> int:
        return self.document["sound_index"]

    @property
    def locks(self) -> dict[str, float]:
        return dict(self.document["parameters"]["locks_physical"])

    @property
    def physical(self) -> dict[str, float]:
        return dict(self.document["parameters"]["physical_by_name"])


def _inventory() -> dict[str, tuple[float, float]]:
    document = load_json(INVENTORY_PATH)
    return {
        row["name"]: (row["minimum"], row["maximum"]) for row in document["parameters"]
    }


def _check_index(index) -> None:
    if type(index) is not int or not 0 <= index <= 2**53 - 1:
        raise FavoriteError("sound_index must be an integer in [0, 2^53-1]")
    if 96 <= index < 128:
        raise FavoriteError(f"sound_index {index} is reserved holdout; access refused")


def _check_portable(node, key=None) -> None:
    """Refuse locator-style absolute or traversing paths outside free-text notes."""

    if type(node) is dict:
        for child_key, child in node.items():
            _check_portable(child, child_key)
    elif type(node) is list:
        for child in node:
            _check_portable(child, key)
    elif type(node) is str and key != "notes":
        components = re.split(r"[/\\]", node)
        if (
            node.startswith(("/", "\\"))
            or _DRIVE.match(node) is not None
            or ".." in components
        ):
            raise FavoriteError(
                f"{key}: favorites are portable and must not contain "
                "absolute or traversing paths"
            )


def _check_patch_names(parameters: Mapping, expected: set[str]) -> None:
    if set(parameters["physical_by_name"]) != expected:
        observed = set(parameters["physical_by_name"])
        raise FavoriteError(
            "canonical parameter names changed; "
            f"missing={sorted(expected - observed)}, extra={sorted(observed - expected)}"
        )


def _favorite(document) -> Favorite:
    """Validate one favorite document completely; the only constructor."""

    if (
        type(document) is dict
        and "schema_version" in document
        and (document["schema_version"] != SCHEMA_VERSION)
    ):
        raise FavoriteError(
            f"unsupported favorite schema_version "
            f"{document['schema_version']!r}; migration refused"
        )
    try:
        validate_document(document, SCHEMA_FILE)
    except ValidationError as error:
        raise FavoriteError(str(error)) from error
    kind = document["kind"]
    expected = _CAPTURE_KEYS if kind == "capture" else _VARIATION_KEYS
    extra = set(document) - expected - {"notes"}
    if extra or expected - set(document):
        raise FavoriteError(
            f"{kind} favorite requires exactly {sorted(expected)} plus optional notes; "
            f"missing={sorted(expected - set(document))}, "
            f"extra={sorted(extra)}"
        )
    if "notes" in document and (
        type(document["notes"]) is not str or not document["notes"].strip()
    ):
        raise FavoriteError("notes must be nonempty text when present")
    _check_index(document["sound_index"])
    _check_portable(document)
    if kind == "capture":
        if document["parent_id"] is not None:
            raise FavoriteError(
                "captured favorites record no parent; parent_id must be null"
            )
        if set(document["artifact"]) != {"artifact_id", "sha256", "ref"}:
            raise FavoriteError("artifact requires exactly artifact_id, sha256 and ref")
    elif document["parent_id"] is None:
        raise FavoriteError("variation favorites must record their parent")
    parameters = document["parameters"]
    physical = parameters["physical_by_name"]
    locks = parameters["locks_physical"]
    if not set(locks) <= set(physical) or not all(
        physical[name] == value for name, value in locks.items()
    ):
        raise FavoriteError("parameters: locks must match resolved physical values")
    if document["favorite_id"] != favorite_id(document):
        raise FavoriteError("favorite_id disagrees with the recorded favorite content")
    return Favorite(document)


def capture(session: ExplorerSession, *, notes: str | None = None) -> Favorite:
    """Capture the current verified selection as a favorite; renders nothing."""

    selection = session.repeat()  # Reverifies; zero renderer and selection-RNG calls.
    inputs = selection.inputs
    record = loads(selection.metadata_bytes)
    audio = record["audio"]["value"]
    document = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "kind": "capture",
        "favorite_id": None,
        "parent_id": None,
        "sound_index": selection.sound_index,
        "artifact": dict(selection.stored.reference),
        "audio": {
            "sha256": audio["file"]["sha256"],
            "size_bytes": audio["file"]["size_bytes"],
            "sample_count": audio["observed_sample_count"],
        },
        "recorded": {
            "profile": copy.deepcopy(inputs["profile"]),
            "source": copy.deepcopy(inputs["source"]),
            "runtime": copy.deepcopy(inputs["runtime"]),
        },
        "parameters": {
            "normalized_by_name": dict(inputs["parameters"]["normalized_by_name"]),
            "physical_by_name": dict(inputs["parameters"]["physical_by_name"]),
            "locks_physical": dict(inputs["parameters"]["locks_physical"]),
        },
    }
    if notes is not None:
        document["notes"] = notes
    document["favorite_id"] = favorite_id(document)
    return _favorite(document)


def vary(
    parent: Favorite,
    *,
    seed: int,
    amount: float,
    locks: Mapping[str, float] | None = None,
    notes: str | None = None,
) -> Favorite:
    """Derive a deterministic nearby variation; locked parameters survive.

    Unlocked parameters are perturbed by a seeded draw within `amount` of the
    parent's recorded physical value, clamped to the current inventory range.
    The same parent, seed, amount and locks always produce the same document
    and the same favorite identity.
    """

    if type(seed) is not int or seed < 0:
        raise FavoriteError("seed must be a nonnegative integer")
    if (
        type(amount) not in (int, float)
        or not math.isfinite(amount)
        or not 0 <= amount <= 1
    ):
        raise FavoriteError("amount must be a finite number in [0, 1]")
    ranges = _inventory()
    if set(parent.physical) != set(ranges):
        raise FavoriteError(
            "canonical parameter names changed; the parent favorite "
            "does not match the current inventory"
        )
    parent_locks = parent.locks
    additional = dict(locks or {})
    for name, value in additional.items():
        if name not in ranges:
            raise FavoriteError(f"unknown parameter lock: {name}")
        if type(value) not in (int, float) or not math.isfinite(value):
            raise FavoriteError(f"invalid physical lock: {name}")
        if name in parent_locks:
            raise FavoriteError(f"parameter is already locked: {name}")
        low, high = ranges[name]
        if not low <= value <= high:
            raise FavoriteError(
                f"lock outside range: {name}={value} outside [{low}, {high}]"
            )
    final_locks = {**parent_locks, **additional}
    rng = random.Random(seed)
    patch = dict(parent.physical)
    for name in sorted(set(patch) - set(final_locks)):
        low, high = ranges[name]
        half = amount * (high - low)
        value = patch[name] + (2.0 * rng.random() - 1.0) * half
        patch[name] = min(high, max(low, value))
    patch.update(final_locks)
    document = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "kind": "variation",
        "favorite_id": None,
        "parent_id": parent.favorite_id,
        "sound_index": parent.sound_index,
        "recorded": copy.deepcopy(parent.document["recorded"]),
        "parameters": {
            "physical_by_name": patch,
            "locks_physical": final_locks,
        },
        "variation": {"seed": seed, "amount": amount},
    }
    if notes is not None:
        document["notes"] = notes
    document["favorite_id"] = favorite_id(document)
    return _favorite(document)


def _read_file(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise FavoriteError(f"{path.name}: expected an unaliased regular file")
        return stream.read()


def _favorite_path(path, *, store: ArtifactStore) -> Path:
    """Favorites live outside the entire store namespace, like bookmarks."""

    path = Path(os.path.abspath(path))
    root = Path(os.path.abspath(store.root))
    if path.is_relative_to(root) or path.resolve().is_relative_to(root.resolve()):
        raise FavoriteError("favorite must be outside the artifact store")
    if path.is_symlink():
        raise FavoriteError("favorite must not be a symlink")
    return path


def load(path) -> Favorite:
    """Strictly read and validate one favorite document (environment-free)."""

    try:
        document = loads(_read_file(Path(os.path.abspath(path))))
    except ValidationError as error:
        raise FavoriteError(f"invalid favorite document: {error}") from error
    return _favorite(document)


def save(favorite: Favorite, path, *, store: ArtifactStore) -> None:
    """Atomically write the schema-validated favorite; portable by construction."""

    path = _favorite_path(path, store=store)
    _favorite(favorite.document)  # Revalidate the exact document being written.
    data = (
        json.dumps(favorite.document, indent=2, sort_keys=True, allow_nan=False) + "\n"
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


def _check_environment(favorite: Favorite) -> None:
    """Recall-time drift detection: profile, source, names and ranges."""

    contract = UpstreamContract.load()
    profile = favorite.document["recorded"]["profile"]
    if (
        profile["name"] != contract.data["profile"]
        or profile["contract_sha256"]
        != sha256_file(repository_root() / "spec/VOICE-CONTRACT.md")
        or profile["sample_rate"] != 44100
        or profile["duration_seconds"] != 4
        or profile.get("expected_sample_count", 176400) != 176400
    ):
        raise FavoriteError("profile changed since the favorite was written")
    source = favorite.document["recorded"]["source"]
    if source != {
        "commit": contract.target_commit,
        "files": contract.files,
        "manifest_sha256": sha256_file(contract.manifest_path),
    }:
        raise FavoriteError("source changed since the favorite was written")
    ranges = _inventory()
    patch = favorite.physical
    if set(patch) != set(ranges):
        raise FavoriteError(
            "canonical parameter names changed; "
            f"missing={sorted(set(ranges) - set(patch))}, "
            f"extra={sorted(set(patch) - set(ranges))}"
        )
    for name, value in sorted(patch.items()):
        low, high = ranges[name]
        if not low <= value <= high:
            raise FavoriteError(
                f"range changed for {name}: recorded {value} "
                f"is outside the current [{low}, {high}]"
            )


def recall(session: ExplorerSession, path, *, store: ArtifactStore) -> Selection:
    """Recall a captured favorite: verify its exact artifact, then select it.

    All compatibility checks run before selection, so a refused recall leaves
    the session's previous selection unchanged, and the whole path makes zero
    renderer and zero selection-RNG calls.
    """

    path = _favorite_path(path, store=store)
    favorite = load(path)
    if favorite.kind != "capture":
        raise FavoriteError(
            "variation favorites are patch specifications; "
            "recall selects only captured favorites"
        )
    _check_environment(favorite)
    document = favorite.document
    try:
        stored = store.verify(
            document["artifact"]["artifact_id"], sha256=document["artifact"]["sha256"]
        )
    except ValidationError as error:
        raise FavoriteError(
            f"favorite artifact verification failed: {error}"
        ) from error
    if stored.reference != document["artifact"]:
        raise FavoriteError(
            "verified store reference disagrees with the favorite artifact pin"
        )
    data = _read_file(stored.path / METADATA)
    verify_sha256(data, document["artifact"]["sha256"])
    record = loads(data)
    inputs = record["inputs"]["value"]
    if (
        record["artifact_id"] != document["artifact"]["artifact_id"]
        or inputs["fixture"]["sound_index"] != document["sound_index"]
    ):
        raise FavoriteError(
            "recorded favorite identity disagrees with verified artifact"
        )
    if (
        inputs["profile"] != document["recorded"]["profile"]
        or inputs["source"] != document["recorded"]["source"]
    ):
        raise FavoriteError(
            "recorded favorite provenance disagrees with verified artifact"
        )
    parameters = inputs["parameters"]
    if (
        parameters["normalized_by_name"] != document["parameters"]["normalized_by_name"]
        or parameters["physical_by_name"] != document["parameters"]["physical_by_name"]
        or parameters["locks_physical"] != document["parameters"]["locks_physical"]
    ):
        raise FavoriteError("recorded favorite patch disagrees with verified artifact")
    if record["audio"]["value"]["file"] != {
        "ref": "audio.f32le",
        "sha256": document["audio"]["sha256"],
        "size_bytes": document["audio"]["size_bytes"],
    }:
        raise FavoriteError("recorded favorite audio disagrees with verified artifact")
    if (
        record["audio"]["value"]["observed_sample_count"]
        != document["audio"]["sample_count"]
    ):
        raise FavoriteError("recorded favorite audio disagrees with verified artifact")
    return session.select(document["sound_index"], document["artifact"])
