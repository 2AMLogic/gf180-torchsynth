#!/usr/bin/env python3
"""Verify an issue-#79 evidence record is actually committable.

``tb/run_oneshot.py`` and ``tb/run_voice.py`` each write their own evidence
record (``oneshot-evidence.json`` / ``voice-evidence.json``) on every run,
whether it passed or not -- an unrun or failed check is never silently
upgraded to a pass. But "the flow produced a record" and "that record is
safe to commit as a citation of the exact commit under review" are different
questions. Per ``spec/ONESHOT-E2E.md`` ("Evidence identity"), a record is
citeable only if:

1. it reports ``result: PASS`` (a failed run proves nothing and must never
   be committed as if it had);
2. its ``identity.git_tree_dirty`` is ``false`` (a record generated from an
   uncommitted-change tree cites a tree state that no commit actually has);
3. its ``identity.git_head`` names the commit the caller intends to cite --
   normally the clean commit the flow was run on, checked with
   ``--expect-head``.

This script is the mechanical form of that checklist, so "is this record
committable" is answered by an exit code rather than by eyeballing JSON.
It does not run any simulation itself and is simulator-free -- it only reads
an already-produced evidence file.

Usage::

    python3 tools/verify_oneshot_evidence.py <evidence.json> [--expect-head SHA]

Exit 0 only if every check above passes (and, when given, ``--expect-head``
matches exactly). Exit 1 otherwise, with every failing reason printed --
never partial credit for a record that fails any one check.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

#: Recognized issue-#79 evidence schemas. A record under any other schema
#: name is refused rather than guessed at -- this tool must never silently
#: pass something it was not written to understand.
KNOWN_SCHEMAS = (
    "gf180-torchsynth/oneshot-tail-chain-evidence-v1",
    "gf180-torchsynth/oneshot-whole-voice-evidence-v1",
)


def verify(record: dict, expect_head: str | None) -> list[str]:
    """Return every reason ``record`` is not committable (empty = committable)."""

    errors: list[str] = []
    schema = record.get("schema")
    if schema not in KNOWN_SCHEMAS:
        errors.append(
            "unrecognized schema %r (expected one of %s)"
            % (schema, ", ".join(KNOWN_SCHEMAS))
        )
    if record.get("result") != "PASS":
        errors.append(
            "result is %r, not PASS -- a failed or unrun check is never "
            "committable as evidence" % record.get("result")
        )
    identity = record.get("identity")
    if not isinstance(identity, dict):
        errors.append("record carries no identity block")
        return errors
    if identity.get("git_tree_dirty") is not False:
        errors.append(
            "identity.git_tree_dirty is %r, not False -- a record produced "
            "from an uncommitted-change tree cites a tree state no commit "
            "actually has" % identity.get("git_tree_dirty")
        )
    git_head = identity.get("git_head")
    if not isinstance(git_head, str) or len(git_head) != 40:
        errors.append("identity.git_head is not a 40-character commit SHA: %r"
                       % git_head)
    elif expect_head is not None and git_head != expect_head:
        errors.append(
            "identity.git_head is %s, not the expected %s -- this record "
            "does not cite the commit you intend to commit it against"
            % (git_head, expect_head)
        )
    if record.get("float_tolerance") is not None:
        errors.append(
            "float_tolerance is %r, not null -- this verifier only accepts "
            "exact-comparison evidence" % record.get("float_tolerance")
        )
    return errors


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path,
                         help="path to an oneshot-evidence.json or "
                              "voice-evidence.json file")
    parser.add_argument("--expect-head", default=None,
                         help="require identity.git_head to equal this "
                              "40-character commit SHA")
    args = parser.parse_args(argv)

    if not args.evidence.is_file():
        print("ERROR: %s does not exist -- nothing to verify" % args.evidence)
        return 1
    record = json.loads(args.evidence.read_text(encoding="utf-8"))
    errors = verify(record, args.expect_head)
    if errors:
        print("NOT COMMITTABLE: %s" % args.evidence)
        for error in errors:
            print("  - %s" % error)
        return 1
    print("COMMITTABLE: %s (schema %s, git_head %s)"
          % (args.evidence, record["schema"], record["identity"]["git_head"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
