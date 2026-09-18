"""Load and verify the pinned upstream Voice contract."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class UpstreamContract:
    manifest_path: Path
    data: dict[str, Any]

    @classmethod
    def load(cls, path: Path | None = None) -> UpstreamContract:
        manifest = path or repository_root() / "spec/reference/upstream.json"
        with manifest.open(encoding="utf-8") as handle:
            data = json.load(handle)
        return cls(manifest.resolve(), data)

    @property
    def target_commit(self) -> str:
        return str(self.data["target_commit"])

    @property
    def files(self) -> dict[str, str]:
        return dict(self.data["files"])

    @property
    def source_checkout_only_files(self) -> dict[str, str]:
        return dict(self.data.get("source_checkout_only_files", {}))

    def verify_source_tree(self, root: Path, require_git_commit: bool = False) -> list[str]:
        """Return mismatches; an empty list means the pinned files match."""

        root = root.resolve()
        mismatches: list[str] = []
        files = dict(self.files)
        git_dir = root / ".git"
        if git_dir.exists():
            files.update(self.source_checkout_only_files)
        for relative, expected in sorted(files.items()):
            candidate = root / relative
            if not candidate.is_file():
                mismatches.append(f"missing {relative}")
                continue
            observed = sha256_file(candidate)
            if observed != expected:
                mismatches.append(
                    f"hash {relative}: expected {expected}, observed {observed}"
                )

        if git_dir.exists():
            result = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                check=False,
                capture_output=True,
                text=True,
            )
            observed_commit = result.stdout.strip() if result.returncode == 0 else ""
            if observed_commit != self.target_commit:
                mismatches.append(
                    f"commit: expected {self.target_commit}, observed {observed_commit or 'unreadable'}"
                )
        elif require_git_commit:
            mismatches.append("source tree has no .git metadata")

        return mismatches

    def verify_nebula_shape(self, root: Path) -> list[str]:
        expected = self.data["expected"]
        path = root.resolve() / "torchsynth/nebulae/voice/default.json"
        if not path.is_file():
            return [f"missing {path.relative_to(root.resolve())}"]
        with path.open(encoding="utf-8") as handle:
            entries = json.load(handle)

        mismatches: list[str] = []
        if len(entries) != expected["nebula_entry_count"]:
            mismatches.append(
                f"nebula entries: expected {expected['nebula_entry_count']}, observed {len(entries)}"
            )
        parameter_names = {
            tuple(entry["name"][:2])
            for entry in entries
            if isinstance(entry.get("name"), list) and len(entry["name"]) == 3
        }
        if len(parameter_names) != expected["latent_parameter_count"]:
            mismatches.append(
                "nebula parameter pairs: expected "
                f"{expected['latent_parameter_count']}, observed {len(parameter_names)}"
            )
        return mismatches
