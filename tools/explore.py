#!/usr/bin/env python3
"""Local sound explorer MVP: generate, audition, repeat, inspect, save.

Real rendering runs only through #15's qualified v1 adapter (#12 Docker
backend); `--backend fake` runs clearly labeled synthetic fixtures for
deterministic tests. This MVP keeps bookmarks minimal per spec/EXPLORER-
SESSION.md; rich favorites/locks/variation belong to issue #65 and no
hardware, transport or live-note claim is made here.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import explorer_commands  # noqa: E402
from torchsynth_voice.explorer import build_session, runtime_admission  # noqa: E402

BACKEND_LABELS = {
    "docker": "docker-qualified-release-mkl-compatible-v1",
    "fake": "fake-synthetic-fixtures-no-audio",
    "none": "no-renderer-selected-artifacts-only",
}


def _parser():
    parser = argparse.ArgumentParser(
        description=__doc__, allow_abbrev=False, prog="explore"
    )
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument(
        "--backend",
        choices=("docker", "fake", "none"),
        default="docker",
        help="docker: qualified real render; fake: labeled synthetic fixtures",
    )
    parser.add_argument(
        "--producer-root",
        type=Path,
        help="clean frozen producer checkout for real renders (DR-0006)",
    )
    parser.add_argument(
        "--player",
        nargs="+",
        default=["afplay"],
        help="local player command argument array (no shell)",
    )
    parser.add_argument("--preview-dir", type=Path)
    parser.add_argument("--seed", type=int, help="deterministic random selection")
    parser.add_argument(
        "--fake-fail-render",
        type=int,
        action="append",
        help="fake backend only: fail the render request for this index",
    )
    parser.add_argument(
        "--fake-fail-player",
        action="store_true",
        help="fake backend only: fail the audition attempt",
    )
    initial = parser.add_mutually_exclusive_group()
    initial.add_argument(
        "--selected",
        nargs=3,
        metavar=("SOUND_INDEX", "ARTIFACT_ID", "SHA256"),
        help="select this exact reference before the command",
    )
    initial.add_argument("--bookmark", help="recall this bookmark before the command")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("next", "random", "show", "repeat", "audition"):
        commands.add_parser(name, allow_abbrev=False)
    request = commands.add_parser("request", allow_abbrev=False)
    request.add_argument("sound_index", type=int)
    select = commands.add_parser("select", allow_abbrev=False)
    select.add_argument("sound_index", type=int)
    select.add_argument("artifact_id")
    select.add_argument("sha256")
    for name in ("save", "recall"):
        command = commands.add_parser(name, allow_abbrev=False)
        command.add_argument("path")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        session, probe = build_session(
            args.store,
            backend=args.backend,
            producer_root=args.producer_root,
            player_command=args.player,
            preview_dir=args.preview_dir,
            seed=args.seed,
            fake_fail_render=args.fake_fail_render or (),
            fake_fail_player=args.fake_fail_player,
        )
        if args.selected:
            index, artifact_id, sha256 = args.selected
            session.select(
                int(index),
                {
                    "artifact_id": artifact_id,
                    "sha256": sha256,
                    "ref": f"artifacts/{artifact_id}/metadata.json",
                },
            )
        elif args.bookmark:
            session.recall(args.bookmark)
        command = args.command
        rest = []
        if command == "request":
            rest = [str(args.sound_index)]
        elif command == "select":
            rest = [str(args.sound_index), args.artifact_id, args.sha256]
        elif command in ("save", "recall"):
            rest = [args.path]
        shown = explorer_commands.dispatch(session, [command, *rest])
        envelope = {
            "session": shown,
            "backend": BACKEND_LABELS[args.backend],
            "runtime_admission": (
                runtime_admission(session.selection.inputs)
                if session.selection
                else None
            ),
            "preview": getattr(probe.get("player"), "last_preview", None),
        }
        if args.backend == "fake":
            envelope["fake_renderer_calls_this_process"] = len(probe["renderer"].calls)
        print(json.dumps(envelope, indent=2, sort_keys=True, allow_nan=False))
    except (OSError, ValueError) as error:
        print(f"explore: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
