#!/usr/bin/env python3
"""Run the normalization reciprocal-precision sweep and manage its receipt.

Stdlib-only. The sweep replays every directed Q2.21 normalization case by
every candidate method/precision (direct division; reciprocal-multiply at
fractional widths F in {12, 14, 16, 18, 20, 21, 22, 24}) and compares each
result against the landed float-mix normalization reference on the identical
clip, per the paired primary rows of docs/MEASUREMENT-PLAN.md.

Modes:

- ``--emit``: run the full sweep and write the receipt JSON to
  ``sim/reference/normalization-reciprocal-v1.json``. The receipt carries
  status ``candidate-pending-ratification``: it is decision evidence input
  for DR-0003 acceptance, never the acceptance itself (DR-0003 and DR-0008
  stay Proposed; the operator owns acceptance).
- ``--check`` (default): re-run the full sweep and require the fresh
  receipt's body digest (everything except ``generated_utc``) to equal the
  committed receipt's digest -- byte-stability of the measured tables across
  runs, with the declared timestamp excluded. Exits nonzero on any drift.

``--generated-utc`` declares the receipt timestamp so a regeneration is
byte-stable; production receipts use a declared value, never wall-clock
input, per the repo determinism override convention.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import normalization_replay as nr  # noqa: E402

RECEIPT_PATH = ROOT / "sim/reference/normalization-reciprocal-v1.json"
DECLARED_UTC = "2026-09-21T03:30:00+00:00"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--emit",
        action="store_true",
        help="run the sweep and write the committed receipt",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="re-run the sweep and verify the committed receipt digest",
    )
    parser.add_argument(
        "--generated-utc",
        default=DECLARED_UTC,
        help="declared receipt timestamp (determinism override)",
    )
    args = parser.parse_args()
    if args.emit == args.check:
        parser.error("choose exactly one of --emit / --check")

    if args.emit:
        receipt = nr.build_receipt(args.generated_utc)
        digest = nr.receipt_sha256(receipt)
        payload = dict(receipt)
        payload["receipt_sha256"] = digest
        RECEIPT_PATH.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
        decision = receipt["decision"]
        print(f"wrote {RECEIPT_PATH}")
        print(f"receipt_sha256: {digest}")
        print(
            "recommended reciprocal: F=%s (%s)"
            % (
                decision["recommended_reciprocal_frac_bits"],
                decision["recommended_gain_word"],
            )
        )
        return 0

    committed = json.loads(RECEIPT_PATH.read_text())
    fresh = nr.build_receipt(committed["generated_utc"])
    fresh_digest = nr.receipt_sha256(fresh)
    if fresh_digest != committed.get("receipt_sha256"):
        print("RECEIPT DRIFT: committed %s != fresh %s"
              % (committed.get("receipt_sha256"), fresh_digest))
        return 1
    print("receipt digest stable: %s" % fresh_digest)
    print(
        "recommended reciprocal: F=%s (%s)"
        % (
            fresh["decision"]["recommended_reciprocal_frac_bits"],
            fresh["decision"]["recommended_gain_word"],
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
