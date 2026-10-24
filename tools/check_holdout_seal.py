#!/usr/bin/env python3
"""Pre-unseal refusal gate for the one-shot holdout (issue #55).

Read-only: verifies the pinned seal manifest, git ancestry, clean pinned paths,
rubric validation, the frozen-rubric file and the ledger state. It renders
nothing, creates no ledger and reads no holdout artifact. Exit 0 verified,
2 refused (structured JSON on stdout), 1 usage/other failure.

  --generate COMMIT prints a fresh seal manifest computed from that commit's
  git content (never the worktree) for review; it writes no file.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.holdout_seal import (  # noqa: E402
    CORPUS_PATH,
    SEAL_PATH,
    SealRefusal,
    build_seal,
    verify_holdout_seal,
)


def make_gate(root=ROOT, **overrides):
    """Gate callable for ``run_corpus(seal_gate=...)``; takes a context dict."""

    def gate(context):
        return verify_holdout_seal(
            root,
            indices=context["indices"],
            corpus_manifest_sha256=context["manifest_sha256"],
            frozen_rubric=context["frozen_rubric"],
            audit_root=context["audit_root"],
            resume=context["resume"],
            **overrides,
        )

    return gate


def refusal_report(error):
    return {"status": "refused", "error": str(error), "checks": error.checks}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate", metavar="COMMIT")
    parser.add_argument("--frozen-rubric", type=Path)
    parser.add_argument("--holdout-audit-root", type=Path)
    parser.add_argument("--indices", type=int, nargs="+")
    parser.add_argument("--manifest", type=Path, default=ROOT / CORPUS_PATH)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--seal", default=SEAL_PATH)
    args = parser.parse_args(argv)
    if args.generate:
        print(json.dumps(build_seal(ROOT, args.generate), indent=2))
        return 0
    if args.frozen_rubric is None or args.holdout_audit_root is None:
        parser.error("--frozen-rubric and --holdout-audit-root are required")
    try:
        report = verify_holdout_seal(
            ROOT,
            indices=args.indices,
            corpus_manifest_sha256=hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
            frozen_rubric=args.frozen_rubric,
            audit_root=args.holdout_audit_root,
            resume=args.resume,
            seal_path=args.seal,
        )
    except SealRefusal as error:
        print(json.dumps(refusal_report(error), indent=2))
        return 2
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
