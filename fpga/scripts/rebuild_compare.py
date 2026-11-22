#!/usr/bin/env python3
"""Compare two ``fpga-build-report.json`` files for the #374 clean-checkout
rebuild check (DR-0017).

    rebuild_compare.py BASELINE_REPORT NEW_REPORT [--out JSON] [--check]

Compares exactly what the rebuild acceptance criterion names: the flat
synthesis resource counts (every raw cell type plus the summary table), the
per-engine standalone counts, the failed-engine list, the pinned tool
identity (yosys/nextpnr banners and the build-venv package list), the device
string and the pnr exit code. Anything absent in the new report is drift,
not "not run" -- a rebuild that did not reach a stage did not reproduce it.

``--check`` is the check-only drift gate: exit ``0`` only when every compared
field is identical, ``1`` on any drift, ``2`` on a usage/parse error. It
simulates nothing and reads no build outputs; it only compares two committed
reports, so it can run in CI.

No tolerance exists anywhere: the toolchain is pinned
(``fpga/requirements.txt``) and Yosys is deterministic for identical inputs,
so any single-cell difference is real drift and must be quantified, not
absorbed.
"""
import argparse
import json
import sys
from pathlib import Path


def _cells(report: dict) -> dict:
    return dict(report.get("synthesis_flat", {}).get("raw_cell_types", {}))


def _summary(report: dict) -> dict:
    return dict(report.get("synthesis_flat", {}).get("cells", {}))


def _engines(report: dict) -> dict:
    return {
        name: {k: v for k, v in tab.items() if k != "total_for_instances"}
        for name, tab in report.get("synthesis_per_engine_standalone",
                                    {}).items()
    }


def _head(report: dict) -> str:
    g = report.get("git", {})
    return "%s@%s%s" % (g.get("branch"), g.get("head"),
                        "-dirty" if g.get("dirty") else "")


def compare_reports(baseline: dict, new: dict) -> dict:
    """One comparison record; ``drift`` lists every differing field."""

    drift = []

    def num_block(kind, base, cur):
        for key in sorted(set(base) | set(cur)):
            b, c = base.get(key), cur.get(key)
            if b is None:
                drift.append("%s:+%s" % (kind, key))
            elif c is None:
                drift.append("%s:-%s" % (kind, key))
            elif b != c:
                drift.append("%s:%s" % (kind, key))

    num_block("flat_cell", _cells(baseline), _cells(new))
    num_block("summary", _summary(baseline), _summary(new))
    num_block("engine", _engines(baseline), _engines(new))
    for field in ("engines_failed", "device", "yosys", "nextpnr_banner",
                  "python_packages_in_build_venv"):
        if baseline.get(field) != new.get(field):
            drift.append(field)
    b_pnr = baseline.get("place_and_route", {}).get("nextpnr_exit")
    n_pnr = new.get("place_and_route", {}).get("nextpnr_exit")
    if b_pnr != n_pnr:
        drift.append("place_and_route.nextpnr_exit")

    return {
        "baseline": {"git_head": _head(baseline),
                     "decision_record": baseline.get("decision_record")},
        "rebuild": {"git_head": _head(new),
                    "decision_record": new.get("decision_record")},
        "flat_cell_counts": {
            key: {"baseline": _cells(baseline).get(key),
                  "rebuild": _cells(new).get(key)}
            for key in sorted(set(_cells(baseline)) | set(_cells(new)))
        },
        "fields_compared": ["synthesis_flat.raw_cell_types",
                            "synthesis_flat.cells",
                            "synthesis_per_engine_standalone",
                            "engines_failed", "device", "yosys",
                            "nextpnr_banner",
                            "python_packages_in_build_venv",
                            "place_and_route.nextpnr_exit"],
        "drift": drift,
        "verdict": "identical" if not drift else "drift",
        "nondeterminism_sources": (
            "none observed: every compared field is byte-equal; toolchain "
            "pinned by fpga/requirements.txt and installed by build.sh env"
            if not drift else
            "see drift list; quantify before reusing any #80 number"),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("new", type=Path)
    parser.add_argument("--out", type=Path, default=None,
                        help="write the comparison record here (JSON)")
    parser.add_argument("--check", action="store_true",
                        help="exit 1 on any drift (check-only mode)")
    args = parser.parse_args(argv)
    try:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        new = json.loads(args.new.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print("rebuild_compare: cannot parse reports: %s" % error,
              file=sys.stderr)
        return 2
    record = compare_reports(baseline, new)
    text = json.dumps(record, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print("rebuild %s vs %s: %s%s"
          % (record["rebuild"]["git_head"],
             record["baseline"]["git_head"], record["verdict"],
             "" if not record["drift"] else
             " (%s)" % ", ".join(record["drift"][:10])))
    if args.check and record["drift"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
