#!/usr/bin/env python3
"""Generate/check the capability view without running evidence-supplied commands."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.capabilities import (  # noqa: E402
    CapabilityError,
    evaluate,
    load_graph,
    render_markdown,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--graph", type=Path, default=Path("spec/capabilities-v1.json"))
    parser.add_argument("--output", type=Path, default=Path("docs/CAPABILITIES.md"))
    parser.add_argument(
        "--check", action="store_true", help="check generated document agreement"
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="check agreement and fail on unhealthy declared evidence",
    )
    args = parser.parse_args()
    try:
        graph = load_graph(args.root / args.graph)
        results = evaluate(graph, args.root)
        expected = render_markdown(graph, results).encode("utf-8")
        output = args.root / args.output
        if args.check or args.strict:
            if not output.is_file() or output.read_bytes() != expected:
                print(
                    "ERROR: generated capability document differs; regenerate it",
                    file=sys.stderr,
                )
                return 1
        else:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(expected)
        print(
            "Capability states: "
            + ", ".join(
                f"{state}={count}"
                for state, count in sorted(
                    Counter(r.state for r in results.values()).items()
                )
            )
        )
        unhealthy = [
            (name, result) for name, result in results.items() if not result.healthy
        ]
        if args.strict and unhealthy:
            for name, result in unhealthy:
                print(
                    f"ERROR: {name}: {result.state}: {'; '.join(result.reasons)}",
                    file=sys.stderr,
                )
            return 1
        print(
            "Evidence health checked; unrun claims remain unrun."
            if args.strict
            else "Graph/document agreement checked."
            if args.check
            else "Generated capability document."
        )
        return 0
    except (CapabilityError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
