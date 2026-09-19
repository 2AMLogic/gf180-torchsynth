#!/usr/bin/env python3
"""Render a manifest selection, explicitly resume, or verify without TorchSynth."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.artifact_renderer import (  # noqa: E402
    DockerBackend,
    render_artifact,
    request_template,
)
from torchsynth_voice.corpus import run_corpus, run_reference, verify_run  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument(
        "--manifest", type=Path, default=ROOT / "spec/reference/corpus-v0.json"
    )
    parser.add_argument("--indices", type=int, nargs="+")
    parser.add_argument(
        "--resume", help="Continue this exact run ID; no changed inputs"
    )
    parser.add_argument(
        "--verify", metavar="RUN_ID", help="Read-only exact-byte/schema verification"
    )
    parser.add_argument(
        "--run-sha256", help="Expected exact digest of the latest run envelope"
    )
    parser.add_argument(
        "--holdout-once",
        action="store_true",
        help="ONE-SHOT HOLDOUT EXPOSURE AFTER RUBRIC FREEZE",
    )
    parser.add_argument("--frozen-rubric", type=Path)
    parser.add_argument("--holdout-audit-root", type=Path)
    args = parser.parse_args()
    if args.verify:
        result = verify_run(
            args.store,
            args.verify,
            allow_holdout=args.holdout_once,
            expected_sha256=args.run_sha256,
        )
    else:
        backend = DockerBackend()
        result = run_corpus(
            args.store,
            args.manifest,
            request_template(),
            lambda request, store: render_artifact(request, store, backend),
            indices=args.indices,
            mode="holdout" if args.holdout_once else "development",
            resume=args.resume,
            frozen_rubric=args.frozen_rubric,
            audit_root=args.holdout_audit_root,
        )
    print(
        json.dumps(
            dict(
                {k: result[k] for k in ("run_id", "status", "counts", "index")},
                run=run_reference(result),
            ),
            indent=2,
        )
    )
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
