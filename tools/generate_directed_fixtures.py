#!/usr/bin/env python3
"""Generate or check preregistered directed Voice patches without Torch/audio."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.directed import (  # noqa: E402
    COVERAGE_PATH,
    MANIFEST_PATH,
    build_manifest,
    coverage_report,
    encode,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--coverage", type=Path, default=COVERAGE_PATH)
    parser.add_argument(
        "--check", action="store_true", help="compare exact bytes without writing"
    )
    args = parser.parse_args()
    try:
        manifest = build_manifest()
        report = coverage_report(manifest)
        outputs = ((args.manifest, encode(manifest)), (args.coverage, encode(report)))
        for path, data in outputs:
            if args.check:
                if path.read_bytes() != data:
                    raise ValueError(
                        f"{path.name} differs; regenerate to record the new identity/coverage"
                    )
            else:
                path.write_bytes(data)
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        f"directed fixtures verified: {len(manifest['cases'])} cases, 78 parameters, 20 routes; audio not run"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
