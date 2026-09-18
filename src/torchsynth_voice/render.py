"""Pinned TorchSynth adapter with explicit identity and provenance."""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import math
import platform
import subprocess
import sys
import wave
from pathlib import Path
from typing import Any, Mapping

from .contract import UpstreamContract, repository_root, sha256_file
from .identity import BASE_REPRODUCIBLE_BATCH_SIZE, SoundIdentity


def _resolve_source_root(requested: Path | None) -> Path:
    if requested is not None:
        root = requested.expanduser().resolve()
        if (root / "src/torchsynth").is_dir():
            root = root / "src"
        if not (root / "torchsynth").is_dir():
            raise ValueError(f"{root} does not contain a torchsynth package")
        return root

    spec = importlib.util.find_spec("torchsynth")
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError(
            "torchsynth is not installed; install the reference extra or pass --torchsynth-root"
        )
    return Path(next(iter(spec.submodule_search_locations))).resolve().parent


def _version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def _git_state(root: Path) -> dict[str, Any]:
    if not (root / ".git").exists():
        return {"available": False}

    def git(*args: str, text: bool = False) -> subprocess.CompletedProcess[Any]:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            capture_output=True,
            text=text,
        )

    head_result = git("rev-parse", "HEAD", text=True)
    diff_result = git("diff", "--binary", "HEAD")
    untracked_result = git("ls-files", "--others", "--exclude-standard", "-z")
    untracked: list[dict[str, str]] = []
    if untracked_result.returncode == 0:
        for raw_name in untracked_result.stdout.split(b"\0"):
            if not raw_name:
                continue
            relative = raw_name.decode("utf-8", errors="surrogateescape")
            path = root / relative
            untracked.append(
                {
                    "path": relative,
                    "sha256": sha256_file(path) if path.is_file() else "not-a-file",
                }
            )
    state_payload = {
        "diff_sha256": hashlib.sha256(diff_result.stdout).hexdigest(),
        "untracked": untracked,
    }
    encoded = json.dumps(state_payload, sort_keys=True, separators=(",", ":")).encode()
    return {
        "available": True,
        "head": head_result.stdout.strip() if head_result.returncode == 0 else None,
        "dirty": bool(diff_result.stdout or untracked),
        "state_sha256": hashlib.sha256(encoded).hexdigest(),
        **state_payload,
    }


def _canonical_parameter_maps(voice: Any, slot: int) -> tuple[dict[str, float], dict[str, float], dict[int, str]]:
    normalized: dict[str, float] = {}
    physical: dict[str, float] = {}
    names_by_identity: dict[int, str] = {}
    for (module_name, parameter_name), parameter in voice.get_parameters(
        include_frozen=True
    ).items():
        name = f"{module_name}.{parameter_name}"
        normalized[name] = float(parameter.detach().cpu()[slot].item())
        physical[name] = float(parameter.from_0to1().detach().cpu()[slot].item())
        names_by_identity[id(parameter)] = name
    return normalized, physical, names_by_identity


def _write_audition_wav(path: Path, samples: Any, sample_rate: int) -> None:
    import numpy as np

    clipped = np.clip(samples, -1.0, 1.0)
    pcm = np.where(
        clipped < 0.0,
        np.rint(clipped * 32768.0),
        np.rint(clipped * 32767.0),
    ).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())


def render_sound(
    sound_index: int,
    output_directory: Path,
    torchsynth_root: Path | None = None,
    locks: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Render one indexed sound and return the committed metadata structure."""

    source_root = _resolve_source_root(torchsynth_root)
    contract = UpstreamContract.load()
    mismatches = contract.verify_source_tree(source_root)
    mismatches.extend(contract.verify_nebula_shape(source_root))
    if mismatches:
        raise RuntimeError("pinned TorchSynth validation failed:\n- " + "\n- ".join(mismatches))

    source_text = str(source_root)
    if source_text not in sys.path:
        sys.path.insert(0, source_text)

    import numpy as np
    import torch
    from torchsynth.config import SynthConfig
    from torchsynth.synth import Voice

    identity = SoundIdentity(sound_index)
    batch_size = BASE_REPRODUCIBLE_BATCH_SIZE
    batch_index, slot = identity.batch_coordinates(batch_size)
    config = SynthConfig(
        batch_size=batch_size,
        sample_rate=contract.data["expected"]["sample_rate"],
        buffer_size_seconds=contract.data["expected"]["duration_seconds"],
        control_rate=contract.data["expected"]["control_rate"],
        reproducible=True,
        no_grad=True,
    )
    voice = Voice(synthconfig=config, nebula="default").cpu().eval()

    requested_locks = dict(locks or {})
    available = {f"{module}.{name}" for module, name in voice.get_parameters(include_frozen=True)}
    unknown = sorted(set(requested_locks) - available)
    if unknown:
        raise ValueError("unknown parameter locks: " + ", ".join(unknown))
    for name, value in sorted(requested_locks.items()):
        if not math.isfinite(value):
            raise ValueError(f"lock {name} must be finite")
        module, parameter = name.split(".", 1)
        voice.set_parameters(
            {(module, parameter): torch.full((batch_size,), value, dtype=torch.float32)},
            freeze=True,
        )

    with torch.inference_mode():
        audio_batch, forward_parameter_tensor, is_train = voice(batch_index)

    samples = (
        audio_batch[slot]
        .detach()
        .cpu()
        .contiguous()
        .numpy()
        .astype("<f4", copy=False)
    )
    expected_samples = int(contract.data["expected"]["output_samples"])
    if samples.shape != (expected_samples,):
        raise RuntimeError(
            f"expected {expected_samples} output samples, observed {samples.shape}"
        )

    normalized, physical, names_by_identity = _canonical_parameter_maps(voice, slot)
    forward_order = [names_by_identity[id(parameter)] for parameter in voice.parameters()]
    randomization_order = [
        names_by_identity[id(parameter)] for _, parameter in sorted(voice.named_parameters())
    ]
    forward_values = [
        float(value)
        for value in forward_parameter_tensor[slot].detach().cpu().tolist()
    ]
    if len(forward_order) != contract.data["expected"]["latent_parameter_count"]:
        raise RuntimeError(f"expected 78 Voice parameters, observed {len(forward_order)}")
    for name, observed in zip(forward_order, forward_values, strict=True):
        if observed != normalized[name]:
            raise RuntimeError(f"parameter tensor/name mismatch at {name}")

    output_directory = output_directory.expanduser().resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    float_path = output_directory / "audio.f32le"
    wav_path = output_directory / "audition.wav"
    metadata_path = output_directory / "metadata.json"
    float_path.write_bytes(samples.tobytes())
    _write_audition_wav(
        wav_path, samples, int(contract.data["expected"]["sample_rate"])
    )

    abs_samples = np.abs(samples)
    peak_index = int(abs_samples.argmax())
    metadata: dict[str, Any] = {
        "schema_version": 1,
        "profile": contract.data["profile"],
        "identity": identity.to_dict(batch_size),
        "upstream": {
            "repository": contract.data["repository"],
            "commit": contract.target_commit,
            "manifest_sha256": sha256_file(contract.manifest_path),
            "source_layout": "git-checkout" if (source_root / ".git").exists() else "installed-package",
            "git": _git_state(source_root),
        },
        "project": {"git": _git_state(repository_root())},
        "runtime": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python": platform.python_version(),
            "torch": torch.__version__,
            "numpy": np.__version__,
            "lightning": _version("lightning"),
            "torchsynth_distribution": _version("torchsynth"),
            "device": "cpu",
            "dtype": "float32",
        },
        "configuration": {
            "sample_rate": int(config.sample_rate.item()),
            "control_rate": int(config.control_rate.item()),
            "duration_seconds": float(config.buffer_size_seconds.item()),
            "expected_sample_count": expected_samples,
            "observed_sample_count": int(samples.size),
            "nebula": "default",
            "locks_physical": requested_locks,
        },
        "parameters": {
            "normalized_by_name": normalized,
            "physical_by_name": physical,
            "randomization_order": randomization_order,
            "forward_order": forward_order,
            "forward_values_normalized": forward_values,
        },
        "train_test": {
            "adapter": bool(is_train[slot].detach().cpu().item()),
            "identity": identity.is_train,
        },
        "audio": {
            "normative_file": float_path.name,
            "normative_encoding": "little-endian IEEE-754 float32 mono",
            "sha256": sha256_file(float_path),
            "audition_file": wav_path.name,
            "audition_sha256": sha256_file(wav_path),
            "peak_abs": float(abs_samples[peak_index]),
            "peak_index": peak_index,
            "rms": float(np.sqrt(np.mean(np.square(samples, dtype=np.float64)))),
            "dc_mean": float(np.mean(samples, dtype=np.float64)),
            "clipped_sample_count": int(np.count_nonzero(abs_samples > 1.0)),
        },
    }
    if metadata["train_test"]["adapter"] != metadata["train_test"]["identity"]:
        raise RuntimeError("train/test identity mismatch")

    metadata_path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return metadata
