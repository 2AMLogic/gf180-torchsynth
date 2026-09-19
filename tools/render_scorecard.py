"""Generate or check scorecard views without writing any evidence records."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.artifacts import loads
from torchsynth_voice.case_registry import _path, encode, evaluate, render_markdown


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--registry", default="spec/reference/case-registry-v1.json")
    parser.add_argument("--output", default="docs")
    parser.add_argument(
        "--check", action="store_true", help="compare bytes; never write"
    )
    parser.add_argument(
        "--partition", choices=("development", "holdout"), default="development"
    )
    parser.add_argument(
        "--allow-holdout",
        metavar="AUDIT_REASON",
        help="explicitly unseal holdout evidence",
    )
    args = parser.parse_args(argv)
    if args.partition == "holdout" and not (
        args.allow_holdout and args.allow_holdout.strip()
    ):
        parser.error("holdout access refused: --allow-holdout AUDIT_REASON is required")
    if args.allow_holdout and args.partition != "holdout":
        parser.error("--allow-holdout requires --partition holdout")
    if args.partition == "holdout":
        print("EXPLICIT HOLDOUT ACCESS: " + args.allow_holdout, file=sys.stderr)
    try:
        root = args.root.resolve()
        if "holdout" in args.registry.split("/") or "evidence" in args.registry.split(
            "/"
        ):
            raise ValueError(
                "registry must be public allocation metadata, outside evidence/holdout"
            )
        registry = loads(_path(root, args.registry).read_bytes())
        output = _path(root, args.output)
        evidence = (root / registry["evidence_root"]).resolve()
        if (
            output == evidence
            or output.is_relative_to(evidence)
            or evidence.is_relative_to(output)
        ):
            raise ValueError("views must be separate from the evidence directory")
        # Stable logical command: check/generate modes must produce identical bytes.
        command = [
            "python3",
            "tools/render_scorecard.py",
            "--registry",
            args.registry,
            "--partition",
            args.partition,
        ]
        if args.allow_holdout:
            command += ["--allow-holdout", args.allow_holdout]
        board = evaluate(
            registry,
            root,
            partition=args.partition,
            holdout_authorization=args.allow_holdout,
            command=command,
        )
        suffix = "-holdout" if args.partition == "holdout" else ""
        outputs = {
            output / f"scorecard{suffix}.json": encode(board),
            output / f"SCORECARD{suffix.upper()}.md": render_markdown(board).encode(),
        }
        if args.check:
            different = [
                path.name
                for path, data in outputs.items()
                if not path.is_file() or path.read_bytes() != data
            ]
            if different:
                print(
                    "Scorecard views differ: " + ", ".join(different), file=sys.stderr
                )
                return 1
        else:
            for path in outputs:
                if path.is_symlink():
                    raise ValueError("refusing symlinked output")
            output.mkdir(parents=True, exist_ok=True)
            for path, data in outputs.items():
                path.write_bytes(data)
        counts = board["partitions"][args.partition]
        print(f"{counts['inspected_cases']} cases inspected: {counts['outcomes']}")
        print("View agreement checked." if args.check else "Generated scorecard views.")
        print("This is evidence accounting, not a hardware PASS.")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
