#!/usr/bin/env python3
"""Render a manifest selection, explicitly resume, or verify without TorchSynth."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from torchsynth_voice.artifact_renderer import (  # noqa: E402
    DockerBackend,
    render_artifact,
    request_template,
)
from torchsynth_voice.corpus import (  # noqa: E402
    HISTORICAL_RECEIPTS,
    PORTABLE_RECEIPTS,
    run_corpus,
    run_reference,
    verify_run,
)


def main(argv=None):
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
    parser.add_argument(
        "--historical-receipts",
        action="store_true",
        help="with --verify: retained pre-#289 run; skip only receipt portability",
    )
    parser.add_argument("--frozen-rubric", type=Path)
    parser.add_argument("--holdout-audit-root", type=Path)
    args = parser.parse_args(argv)
    if args.historical_receipts and not args.verify:
        parser.error("--historical-receipts applies only to --verify")
    policy = HISTORICAL_RECEIPTS if args.historical_receipts else PORTABLE_RECEIPTS
    if args.verify:
        result = verify_run(
            args.store,
            args.verify,
            allow_holdout=args.holdout_once,
            expected_sha256=args.run_sha256,
            receipt_policy=policy,
        )
    else:
        gate = None
        if args.holdout_once:
            # Issue #55: verify the freeze before any renderer is constructed.
            from check_holdout_seal import make_gate, refusal_report
            from torchsynth_voice.holdout_seal import SealRefusal, sha256_hex

            gate = make_gate(ROOT)
            try:
                gate(
                    dict(
                        indices=args.indices,
                        manifest_sha256=sha256_hex(args.manifest.read_bytes()),
                        frozen_rubric=args.frozen_rubric,
                        audit_root=args.holdout_audit_root,
                        resume=bool(args.resume),
                    )
                )
            except SealRefusal as error:
                print(json.dumps(refusal_report(error), indent=2))
                return 2
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
            seal_gate=gate,
        )
    print(
        json.dumps(
            dict(
                {k: result[k] for k in ("run_id", "status", "counts", "index")},
                run=run_reference(result),
                receipt_policy=policy,
            ),
            indent=2,
        )
    )
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
