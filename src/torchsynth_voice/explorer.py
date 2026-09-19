"""Parent explorer integration: qualified rendering, admission and audition.

Adapts `artifact_renderer.render_artifact` (#15) to the explorer `Renderer`
protocol and `ExplorerSession` (#105), reports #12 runtime admission from the
ratified publication, and supplies a labeled fake mode plus a local preview
player. See spec/EXPLORER-MVP.md; the session/command contracts remain in
spec/EXPLORER-SESSION.md.
"""

from __future__ import annotations

import math
import os
import random
import struct
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Mapping, Sequence

from .artifact_renderer import (
    RENDERER,
    DockerBackend,
    digest,
    fixture,
    json_bytes,
    qualification,
    render_artifact,
    request_template,
    runtime_descriptor,
)
from .artifacts import content_id
from .contract import UpstreamContract, repository_root, sha256_file
from .explorer_session import ExplorerSession, SessionError
from .inventory import INVENTORY_PATH, load_json
from .storage import ArtifactStore, StoredArtifact

FAKE_RENDERER = "fake-explorer-renderer"
PREVIEW_ENCODING = "wav-pcm16-mono-44100hz"
_ADMITTED_EXECUTION = {
    "mode": "canonical-batch",
    "batch_size": 32,
    "reproducible": True,
}


def _identity(index):
    if type(index) is not int or not 0 <= index <= 2**53 - 1:
        raise SessionError("sound_index must be an integer in [0, 2^53-1]")
    if 96 <= index < 128:
        raise SessionError(f"sound_index {index} is reserved holdout; access refused")


class StoreRenderer:
    """One public v1 render per requested identity, published to the store.

    The single real-render path is #15's `render_artifact` with the #12
    qualified `DockerBackend`; there is no competing renderer/store bridge.
    Runtime admission is enforced inside the adapter before backend access.
    """

    def __init__(self, store: ArtifactStore, backend, *, producer_root=None):
        self.store = store
        self.backend = backend
        self.producer_root = producer_root
        self.last_receipt: Mapping | None = None

    def __call__(self, sound_index: int) -> dict[str, str]:
        _identity(sound_index)
        request = dict(
            request_template(project_root=self.producer_root),
            fixture=fixture(sound_index),
        )
        product = render_artifact(request, self.store, self.backend)
        self.last_receipt = product.receipt
        return product.reference


def runtime_admission(inputs: Mapping) -> dict:
    """Compare verified recorded provenance with the ratified publication.

    This is a display-time provenance comparison against DR-0006's
    `release-mkl-compatible-v1` record, not a new qualification measurement.
    Render-time admission is enforced by the adapter and backend.
    """

    _, runtime = qualification()
    checks = {
        "renderer_version": inputs["renderer_version"] == RENDERER,
        "runtime": inputs["runtime"] == runtime_descriptor(runtime),
        "execution": inputs["execution"] == _ADMITTED_EXECUTION,
    }
    return {
        "profile": "release-mkl-compatible-v1",
        "status": "admitted" if all(checks.values()) else "outside-admitted-profile",
        "checks": checks,
        "basis": "recorded provenance compared with the ratified publication "
        "sim/reference/repeatability-runtime.json per DR-0006",
    }


class PreviewPlayer:
    """Audition validated stored samples through a local player process.

    The player receives the just-verified `StoredArtifact`, derives a lossy
    PCM16 WAV preview outside the artifact store, and invokes the command as
    an argument array with no shell interpolation. It never writes inside the
    store, so normative samples and artifact hashes cannot change.
    """

    def __init__(self, command: Sequence[str] = ("afplay",), *, preview_dir=None):
        if not command:
            raise SessionError("player command must not be empty")
        self.command = tuple(command)
        self.preview_dir = Path(preview_dir).resolve() if preview_dir else None
        self.last_preview: dict | None = None

    def _preview_root(self, store_root: Path) -> Path:
        absolute = Path(os.path.abspath(store_root))
        root = Path(
            os.path.abspath(
                self.preview_dir
                or Path(tempfile.gettempdir()) / "torchsynth-explorer-preview"
            )
        )
        if root == absolute or root.is_relative_to(absolute):
            raise SessionError("preview directory must be outside the artifact store")
        return root

    def __call__(self, stored: StoredArtifact) -> None:
        root = self._preview_root(stored.path.parents[1])
        data = (stored.path / "audio.f32le").read_bytes()
        if len(data) != 176400 * 4:
            raise SessionError("stored audio byte count mismatch")
        values = struct.unpack("<176400f", data)
        if not all(math.isfinite(value) for value in values):
            raise SessionError("stored audio contains nonfinite samples")
        frames = struct.pack(
            "<176400h",
            *(max(-32767, min(32767, round(value * 32767))) for value in values),
        )
        root.mkdir(parents=True, exist_ok=True)
        preview = root / f"explorer-preview-{stored.artifact_id}.wav"
        with wave.open(str(preview), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(44100)
            handle.writeframes(frames)
        try:
            result = subprocess.run(
                [*self.command, str(preview)],
                capture_output=True,
                check=False,
            )
        except OSError as error:
            raise SessionError(
                f"player {self.command[0]!r} could not start: {error.strerror}; "
                "pass a supported local player with --player"
            ) from error
        if result.returncode != 0:
            raise SessionError(
                f"player {self.command[0]!r} exited {result.returncode}: "
                + result.stderr.decode(errors="replace")[-500:]
            )
        self.last_preview = {
            "encoding": PREVIEW_ENCODING,
            "derived_from": "validated f32le-mono stored samples",
            "lossy_quantization": True,
            "player": list(self.command),
        }


class FakePlayer:
    """Deterministic labeled double; success never establishes real playback."""

    def __init__(self, *, error: Exception | None = None):
        self.error = error
        self.calls: list[str] = []

    def __call__(self, stored: StoredArtifact) -> None:
        self.calls.append(stored.artifact_id)
        if self.error is not None:
            raise self.error


def _fake_record(sound_index: int) -> tuple[dict, dict[str, bytes]]:
    """A clearly synthetic, schema-valid protocol fixture; never real audio."""

    _identity(sound_index)
    contract = UpstreamContract.load()
    names = [row["name"] for row in load_json(INVENTORY_PATH)["parameters"]]
    inputs = {
        "profile": {
            "name": "torchsynth-1-voice-default",
            "contract_sha256": sha256_file(
                repository_root() / "spec/VOICE-CONTRACT.md"
            ),
            "sample_rate": 44100,
            "control_rate": 441,
            "duration_seconds": 4,
            "expected_sample_count": 176400,
            "channels": 1,
            "dtype": "float32",
            "nebula": "default",
            "normalization": "conditional-whole-clip-peak",
        },
        "source": dict(
            commit=contract.target_commit,
            manifest_sha256=sha256_file(contract.manifest_path),
            files=contract.files,
        ),
        "runtime": {
            "lock_sha256": digest(b"synthetic-lock"),
            "os": "synthetic-os",
            "architecture": "synthetic-arch",
            "cpu": "synthetic-cpu",
            "device": "cpu",
            "versions": {
                "python": "0",
                "torch": "0-fake",
                "lightning": "0-fake",
                "numpy": "0",
                "torchsynth": "fake-none",
            },
        },
        "project_git": {
            "commit": "0" * 40,
            "dirty": True,
            "diff_sha256": digest(b"synthetic-diff"),
            "untracked_sha256": digest(b"synthetic-untracked"),
        },
        "renderer_version": FAKE_RENDERER,
        "fixture": fixture(sound_index),
        "execution": dict(_ADMITTED_EXECUTION),
        "parameters": {
            "normalized_by_name": {name: i / 100 for i, name in enumerate(names)},
            "physical_by_name": {name: i + 0.125 for i, name in enumerate(names)},
            "locks_physical": {},
        },
        "noise": {
            "seed": 13,
            "slot": sound_index % 32,
            "sha256": digest(b"synthetic-noise"),
        },
        "trace_registry_version": "fake-none",
        "requested_traces": [],
    }
    audio = bytes(176400 * 4)
    record = {
        "schema": "torchsynth-render-artifact",
        "schema_version": 1,
        "status": "complete",
        "artifact_id": content_id(inputs),
        "inputs": {"state": "available", "value": inputs},
        "audio": {
            "state": "available",
            "value": {
                "file": {
                    "ref": "audio.f32le",
                    "sha256": digest(audio),
                    "size_bytes": len(audio),
                },
                "encoding": "f32le-mono",
                "observed_sample_count": 176400,
                "peak_abs": 0.0,
                "peak_index": 0,
                "rms": 0.0,
                "dc_mean": 0.0,
                "clipped_sample_count": 0,
                "normalization_gain": 1.0,
            },
        },
        "traces": {"state": "available", "value": {}},
        "warnings": [],
        "failures": [],
    }
    return record, {"audio.f32le": audio}


def publish_fake_artifact(store: ArtifactStore, sound_index: int) -> StoredArtifact:
    """Publish the deterministic synthetic fixture; identical bytes resume."""

    record, files = _fake_record(sound_index)
    with tempfile.TemporaryDirectory() as temporary:
        source = Path(temporary).resolve()
        for name, data in files.items():
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (source / "metadata.json").write_bytes(json_bytes(record))
        return store.publish(source)


class FakeRenderer:
    """Deterministic labeled double over synthetic store fixtures.

    Requested identities publish (once) the clearly synthetic artifact and
    return its portable reference. Injected failures leave no artifact.
    """

    def __init__(
        self, store: ArtifactStore, *, failures: Mapping[int, Exception] | None = None
    ):
        self.store = store
        self.failures = dict(failures or {})
        self.calls: list[int] = []
        self._references: dict[int, dict[str, str]] = {}

    def __call__(self, sound_index: int) -> dict[str, str]:
        _identity(sound_index)
        self.calls.append(sound_index)
        if sound_index in self.failures:
            raise self.failures[sound_index]
        if sound_index not in self._references:
            self._references[sound_index] = publish_fake_artifact(
                self.store, sound_index
            ).reference
        return dict(self._references[sound_index])


def build_session(
    store_root,
    *,
    backend: str = "docker",
    producer_root=None,
    player_command: Sequence[str] = ("afplay",),
    preview_dir=None,
    seed: int | None = None,
    fake_fail_render: Sequence[int] = (),
    fake_fail_player: bool = False,
) -> tuple[ExplorerSession, dict]:
    """Wire the parent integrations into a session; returns (session, probe).

    `backend` selects the honest label: "docker" uses the #12 qualified
    `DockerBackend` through #15's adapter, "fake" uses deterministic synthetic
    fixtures and a labeled fake player, and "none" configures no renderer.
    `probe` carries the injected doubles for test and diagnostic inspection.
    """

    store = ArtifactStore(store_root)
    probe: dict = {"store": store, "backend": backend}
    rng = None if seed is None else random.Random(seed)
    if backend == "docker":
        renderer = StoreRenderer(
            store,
            DockerBackend(project_root=producer_root),
            producer_root=producer_root,
        )
        player = PreviewPlayer(player_command, preview_dir=preview_dir)
        probe.update(renderer=renderer, player=player)
    elif backend == "fake":
        renderer = FakeRenderer(
            store,
            failures={
                int(index): RuntimeError(f"injected fake render failure for {index}")
                for index in fake_fail_render
            },
        )
        player = FakePlayer(
            error=(
                RuntimeError("injected fake player failure")
                if fake_fail_player
                else None
            )
        )
        probe.update(renderer=renderer, player=player)
    elif backend == "none":
        renderer = None
        player = PreviewPlayer(player_command, preview_dir=preview_dir)
        probe["player"] = player
    else:
        raise SessionError(f"unknown backend {backend!r}")
    session = ExplorerSession(store, renderer=renderer, player=player, rng=rng)
    probe["session"] = session
    return session, probe
