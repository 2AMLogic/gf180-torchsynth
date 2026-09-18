"""Command-line entry point for identity inspection and reference rendering."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .contract import UpstreamContract
from .identity import SoundIdentity
from .render import render_sound


def _lock(value: str) -> tuple[str, float]:
    try:
        name, raw = value.split("=", 1)
        return name, float(raw)
    except ValueError as error:
        raise argparse.ArgumentTypeError("lock must be NAME=PHYSICAL_VALUE") from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gf180-torchsynth",
        description="Inspect and render the pinned TorchSynth Voice profile",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect = subparsers.add_parser("inspect", help="show a global sound identity")
    inspect.add_argument("sound_index", type=int)
    inspect.add_argument("--batch-size", type=int, default=32)

    verify = subparsers.add_parser("verify-source", help="verify a TorchSynth source tree")
    verify.add_argument("torchsynth_root", type=Path)
    verify.add_argument("--require-git-commit", action="store_true")

    render = subparsers.add_parser("render", help="render one canonical reference sound")
    render.add_argument("sound_index", type=int)
    render.add_argument("--torchsynth-root", type=Path)
    render.add_argument("--out", type=Path, required=True)
    render.add_argument(
        "--lock",
        action="append",
        default=[],
        type=_lock,
        metavar="NAME=VALUE",
        help="lock one canonical parameter to a physical value",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "inspect":
        print(json.dumps(SoundIdentity(args.sound_index).to_dict(args.batch_size), indent=2))
        return 0
    if args.command == "verify-source":
        contract = UpstreamContract.load()
        errors = contract.verify_source_tree(
            args.torchsynth_root, require_git_commit=args.require_git_commit
        )
        errors.extend(contract.verify_nebula_shape(args.torchsynth_root))
        if errors:
            print("\n".join(f"ERROR: {error}" for error in errors))
            return 1
        print(f"verified TorchSynth {contract.target_commit}")
        return 0
    if args.command == "render":
        locks = dict(args.lock)
        if len(locks) != len(args.lock):
            raise SystemExit("each --lock parameter may appear only once")
        metadata = render_sound(
            args.sound_index,
            args.out,
            torchsynth_root=args.torchsynth_root,
            locks=locks,
        )
        print(json.dumps(metadata["audio"], indent=2))
        return 0
    raise AssertionError(f"unhandled command {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())

