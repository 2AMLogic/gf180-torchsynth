#!/usr/bin/env python3
"""Generate/check all capability views without running evidence-supplied commands."""

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
    _path,
)
from torchsynth_voice.capability_views import (  # noqa: E402
    render_json,
    render_readme,
    replace_readme,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--graph", type=Path, default=Path("spec/capabilities-v1.json"))
    parser.add_argument("--output", type=Path, default=Path("docs/CAPABILITIES.md"))
    parser.add_argument(
        "--json-output", type=Path, default=Path("docs/capabilities.json")
    )
    parser.add_argument("--readme", type=Path, default=Path("README.md"))
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
        graph_path, output, json_output, readme = (
            _path(args.root, path.as_posix())
            for path in (args.graph, args.output, args.json_output, args.readme)
        )
        if len({graph_path, output, json_output, readme}) != 4:
            raise CapabilityError("graph and generated view paths must be distinct")
        graph = load_graph(graph_path)
        results = evaluate(graph, args.root)
        # Compute/validate every view before writing any, including README markers.
        expected = {
            output: render_markdown(graph, results).encode("utf-8"),
            json_output: render_json(graph, results).encode("utf-8"),
            readme: replace_readme(readme.read_bytes(), render_readme(graph, results)),
        }
        different = [
            path
            for path, data in expected.items()
            if not path.is_file() or path.read_bytes() != data
        ]
        if args.check or args.strict:
            for path in different:
                print(
                    f"ERROR: generated view differs: {path.relative_to(args.root.resolve())}; regenerate it",
                    file=sys.stderr,
                )
            if different:
                return 1
        else:
            for path in different:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(expected[path])
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
            else "Graph/generated-view agreement checked."
            if args.check
            else "Generated capability views."
        )
        return 0
    except (CapabilityError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
