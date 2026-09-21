#!/usr/bin/env python3
"""Generate the RTL numeric-constants package from the DR-0008 choice register.

Refusal-gated (issue #68): every choice is consumed through
``torchsynth_voice.fixedpoint.choices.require_accepted``. While DR-0008 is
Proposed the register refuses all of C1-C10, so the generate mode writes
nothing and exits 2 after naming every refusal; ``--check`` treats exactly
that state as success (gate verified) so CI can assert the refusal without
requiring ratification.

Since the issue #53 ratification (DR-0008 Accepted by reviewed merge,
2026-09-21) the live register admits every entry: generate mode writes the
package (default ``tb/sv/gf180_rtl_constants_pkg.sv``) and ``--check``
verifies the landed package still matches the register byte-for-byte, so a
stale package after any register edit is a CI failure.

No synthesis or PDK step is involved anywhere: this tool emits plain
SystemVerilog source text or nothing.

Usage:
    python3 tools/generate_rtl_constants.py [--check] [--output PATH]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.fixedpoint import codegen  # noqa: E402

DEFAULT_OUTPUT = ROOT / "tb/sv/gf180_rtl_constants_pkg.sv"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the gate; succeed when the honest state is reproduced",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="package output path (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    result = codegen.emit()

    if result.refused_all:
        print(
            "REFUSED: the choice register emitted no constants "
            "(DR-0008 is Proposed; require_accepted refuses every entry)."
        )
        for choice_id, reason in result.refusals:
            print("  %s: %s" % (choice_id, reason))
        if args.check:
            print(
                "CHECK OK: refusal gate verified against the live register."
            )
            return 0
        print("No package written to %s." % args.output)
        return 2

    if args.check:
        text = args.output.read_text(encoding="utf-8") if args.output.exists() else ""
        if text != (result.package_text or ""):
            print(
                "CHECK FAILED: %s is stale; regenerate with "
                "tools/generate_rtl_constants.py" % args.output
            )
            return 1
        print("CHECK OK: %s matches the accepted register." % args.output)
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(result.package_text or "", encoding="utf-8")
    print(
        "Wrote %s (accepted choices: %s)"
        % (args.output, ", ".join(result.emitted_ids))
    )
    for choice_id, reason in result.refusals:
        print("  not emitted %s: %s" % (choice_id, reason))
    return 0


if __name__ == "__main__":
    sys.exit(main())
