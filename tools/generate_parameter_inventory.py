#!/usr/bin/env python3
"""Generate or check the default Voice inventory from verified upstream sources."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.inventory import (  # noqa: E402
    ANNOTATIONS_PATH,
    INVENTORY_PATH,
    encode_inventory,
    generate_inventory,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--torchsynth-root", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, default=ANNOTATIONS_PATH)
    parser.add_argument("--output", type=Path, default=INVENTORY_PATH)
    parser.add_argument(
        "--check", action="store_true", help="compare bytes without writing"
    )
    args = parser.parse_args()
    try:
        encoded = encode_inventory(
            generate_inventory(args.torchsynth_root, args.annotations)
        )
        if args.check:
            if args.output.read_bytes() != encoded:
                raise ValueError(
                    "committed inventory differs from verified source/annotations; regenerate it"
                )
        else:
            args.output.write_bytes(encoded)
    except (OSError, ValueError, KeyError, StopIteration) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "inventory verified: 78 parameters, 156 nebula entries, both order permutations"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
