#!/usr/bin/env python3
"""PDK-free consistency checks for the pinned contract manifests."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.contract import UpstreamContract  # noqa: E402
from torchsynth_voice.identity import SoundIdentity  # noqa: E402


def check_manifests() -> list[str]:
    errors: list[str] = []
    contract = UpstreamContract.load()
    expected = contract.data["expected"]
    if len(contract.target_commit) != 40:
        errors.append("target commit is not a full 40-character SHA")
    if expected["sample_rate"] * expected["duration_seconds"] != expected["output_samples"]:
        errors.append("duration, sample rate, and sample count disagree")
    if expected["nebula_entry_count"] != 2 * expected["latent_parameter_count"]:
        errors.append("nebula is not curve+symmetric for each latent parameter")
    pinned_files = {**contract.files, **contract.source_checkout_only_files}
    for path, digest in pinned_files.items():
        if not path or len(digest) != 64:
            errors.append(f"invalid source hash entry for {path!r}")

    corpus_path = ROOT / "spec/reference/corpus-v0.json"
    with corpus_path.open(encoding="utf-8") as handle:
        corpus = json.load(handle)
    seen: set[int] = set()
    counts: dict[str, int] = {}
    for group in corpus["cases"]:
        start, stop = group["start_inclusive"], group["stop_exclusive"]
        if start < 0 or stop <= start:
            errors.append(f"invalid corpus interval {start}:{stop}")
            continue
        values = set(range(start, stop))
        if seen & values:
            errors.append(f"overlapping corpus interval {start}:{stop}")
        seen |= values
        counts[group["split"]] = counts.get(group["split"], 0) + len(values)
    rules = corpus["rules"]
    if len(seen) != rules["case_count"]:
        errors.append("corpus case count disagrees with rules")
    for split in ("development", "holdout"):
        if counts.get(split, 0) != rules[f"{split}_count"]:
            errors.append(f"corpus {split} count disagrees with rules")

    example = SoundIdentity.from_batch(312, 6, 128)
    if example.sound_index != 39942 or example.batch_coordinates(32) != (1248, 6):
        errors.append("batch-size-independent quickstart identity is wrong")
    if SoundIdentity(32).noise_slot != 0 or SoundIdentity(31).noise_slot != 31:
        errors.append("noise-slot identity is wrong")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--torchsynth-root", type=Path)
    parser.add_argument("--require-git-commit", action="store_true")
    args = parser.parse_args()
    errors = check_manifests()
    if args.torchsynth_root:
        contract = UpstreamContract.load()
        errors.extend(
            contract.verify_source_tree(
                args.torchsynth_root, require_git_commit=args.require_git_commit
            )
        )
        errors.extend(contract.verify_nebula_shape(args.torchsynth_root))
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("contract manifests are internally consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
