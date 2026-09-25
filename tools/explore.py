#!/usr/bin/env python3
"""Local sound explorer: generate, audition, repeat, inspect, save.

Real rendering runs only through #15's qualified v1 adapter (#12 Docker
backend); `--backend fake` runs clearly labeled synthetic fixtures for
deterministic tests, and `--backend protocol-mock` drives those same fixtures
through the issue #66 host transport client against the behavioral mock core
(spec/protocol/CLIENT.md), printing the negotiated contract identity beside
the backend label. `--transport` picks which software-lane binding model
carries those bytes (loopback/uart/spi/usb); it changes delivery shape only,
never the rendering path, artifact, bookmark or contract identity, which is
the spec/protocol/TRANSPORTS.md substitutability property made visible on the
shipped CLI. Favorites, parameter locks and deterministic nearby
variations (issue #65) ride the same session via `favorite-save`,
`favorite-recall`, `favorite-vary` and `favorite-inspect`. No hardware,
physical transport, RTL or live-note claim is made here: the protocol-mock
backend is software-only behavioral verification.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import explorer_commands, favorites  # noqa: E402
from torchsynth_voice.explorer import build_session, runtime_admission  # noqa: E402
from torchsynth_voice.favorites import FavoriteError  # noqa: E402
from torchsynth_voice.protocol_backend import (  # noqa: E402
    DEFAULT_TRANSPORT_BINDING,
    TRANSPORT_BINDINGS,
    BACKEND_LABEL,
)

BACKEND_LABELS = {
    "docker": "docker-qualified-release-mkl-compatible-v1",
    "fake": "fake-synthetic-fixtures-no-audio",
    "none": "no-renderer-selected-artifacts-only",
    "protocol-mock": BACKEND_LABEL,
}


def _parser():
    parser = argparse.ArgumentParser(
        description=__doc__, allow_abbrev=False, prog="explore"
    )
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument(
        "--backend",
        choices=("docker", "fake", "none", "protocol-mock"),
        default="docker",
        help=(
            "docker: qualified real render; fake: labeled synthetic fixtures; "
            "protocol-mock: the same fixtures driven over the protocol v2 "
            "client against the behavioral mock core (software only)"
        ),
    )
    parser.add_argument(
        "--transport",
        choices=tuple(sorted(TRANSPORT_BINDINGS)),
        default=None,
        help=(
            "protocol backends only: which software-lane binding model carries "
            "the frames (default: "
            f"{DEFAULT_TRANSPORT_BINDING}). A configuration act, not a protocol "
            "change — no physical link, driver or hardware claim is involved"
        ),
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
    favorite_save = commands.add_parser("favorite-save", allow_abbrev=False)
    favorite_save.add_argument("path")
    favorite_save.add_argument("--notes", help="optional free text; never a locator")
    favorite_recall = commands.add_parser("favorite-recall", allow_abbrev=False)
    favorite_recall.add_argument("path")
    favorite_vary = commands.add_parser("favorite-vary", allow_abbrev=False)
    favorite_vary.add_argument("parent", help="parent favorite document path")
    favorite_vary.add_argument("dest", help="destination favorite document path")
    favorite_vary.add_argument("--seed", type=int, required=True)
    favorite_vary.add_argument("--amount", type=float, required=True)
    favorite_vary.add_argument(
        "--lock", action="append", default=[], metavar="NAME=VALUE"
    )
    favorite_vary.add_argument("--notes", help="optional free text; never a locator")
    favorite_inspect = commands.add_parser("favorite-inspect", allow_abbrev=False)
    favorite_inspect.add_argument("path")
    return parser


def _parse_locks(pairs):
    locks = {}
    for pair in pairs:
        name, separator, value = pair.partition("=")
        if not separator or not name or not value:
            raise FavoriteError(f"invalid --lock {pair!r}; expected NAME=VALUE")
        if name in locks:
            raise FavoriteError(f"duplicate lock: {name}")
        try:
            locks[name] = float(value)
        except ValueError:
            raise FavoriteError(f"invalid --lock value for {name}") from None
    return locks


def _favorite_command(args, session, store):
    """Run one favorite command; returns (favorite document, shown session)."""

    if args.command == "favorite-save":
        favorite = favorites.capture(session, notes=args.notes)
        favorites.save(favorite, args.path, store=store)
        return favorite.document, session.show()
    if args.command == "favorite-recall":
        favorites.recall(session, args.path, store=store)
        return favorites.load(args.path).document, session.show()
    if args.command == "favorite-vary":
        child = favorites.vary(
            favorites.load(args.parent),
            seed=args.seed,
            amount=args.amount,
            locks=_parse_locks(args.lock),
            notes=args.notes,
        )
        favorites.save(child, args.dest, store=store)
        return child.document, None
    return favorites.load(args.path).document, None  # favorite-inspect


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
            transport=args.transport,
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
        favorite_document = None
        if command.startswith("favorite"):
            favorite_document, shown = _favorite_command(args, session, probe["store"])
        else:
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
            # The contract identity the backend negotiated, or null for a
            # backend that speaks no wire protocol (issue #66 display AC).
            "contract": probe.get("contract"),
            # The carrier that actually moved the frames, or null for a
            # backend that speaks no wire protocol. Deliberately reported
            # apart from `contract`: the identity does not vary with it.
            "transport": probe.get("transport_identity"),
            "runtime_admission": (
                runtime_admission(session.selection.inputs)
                if session.selection
                else None
            ),
            "preview": getattr(probe.get("player"), "last_preview", None),
        }
        if favorite_document is not None:
            envelope["favorite"] = favorite_document
        if args.backend == "fake":
            envelope["fake_renderer_calls_this_process"] = len(probe["renderer"].calls)
        elif args.backend == "protocol-mock":
            # Named for the renderer that actually ran: the protocol-mock
            # renderer publishes the same fixture but drives the protocol.
            envelope["protocol_mock_renderer_calls_this_process"] = len(
                probe["renderer"].calls
            )
        print(json.dumps(envelope, indent=2, sort_keys=True, allow_nan=False))
    except (OSError, ValueError) as error:
        print(f"explore: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
