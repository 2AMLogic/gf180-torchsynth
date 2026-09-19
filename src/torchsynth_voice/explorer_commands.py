"""Stdlib existing-store CLI and dispatch seam for parent-supplied adapters."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from .explorer_session import ExplorerSession, SessionError
from .storage import ArtifactStore


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise SessionError(message)


def _parser(*, standalone=False):
    parser = _Parser(description=__doc__, allow_abbrev=False)
    if standalone:
        parser.add_argument(
            "--store", required=True, help="physical local ArtifactStore root"
        )
        initial = parser.add_mutually_exclusive_group()
        initial.add_argument(
            "--selected",
            nargs=3,
            metavar=("SOUND_INDEX", "ARTIFACT_ID", "SHA256"),
            help="select this exact reference before the command",
        )
        initial.add_argument(
            "--bookmark", help="recall this bookmark before the command"
        )
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
        command.add_argument("path", help="bookmark path outside the artifact store")
    return parser


def _select(session, index, artifact_id, sha256):
    session.select(
        index,
        {
            "artifact_id": artifact_id,
            "sha256": sha256,
            "ref": f"artifacts/{artifact_id}/metadata.json",
        },
    )


def _dispatch(session, args):
    if args.command == "select":
        _select(session, args.sound_index, args.artifact_id, args.sha256)
    elif args.command == "request":
        session.request(args.sound_index)
    elif args.command in ("save", "recall"):
        getattr(session, args.command)(args.path)
    elif args.command != "show":
        # Names are restricted by argparse; no user-supplied module is imported.
        getattr(session, args.command)()
    return session.show()


def dispatch(session: ExplorerSession, argv: Sequence[str]) -> dict:
    """Execute one parsed command, returning verified display data or raising.

    Parent #64 supplies its configured session here. No rendering/player adapter
    or persisted session status is implicitly created by this command handler.
    """
    return _dispatch(session, _parser().parse_args(argv))


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser(standalone=True).parse_args(argv)
        session = ExplorerSession(ArtifactStore(args.store))
        if args.selected:
            index, artifact_id, sha256 = args.selected
            _select(session, int(index), artifact_id, sha256)
        elif args.bookmark:
            session.recall(args.bookmark)
        result = _dispatch(session, args)
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    except (OSError, ValueError) as error:
        print(f"explorer: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
