#!/usr/bin/env python3
"""Estimator qualification ledger: build, check, or replay (issue #29).

Consumer/publication integration over the landed producer family artifacts.
It runs no estimator algorithm itself:

- ``build``    recompute the ledger from the landed artifacts and the
               reviewed obligations inventory and write it.
- ``check``    three-way stored-evidence validation: recomputed census vs
               the committed ledger vs the reviewed inventory. Stdlib only.
- ``replay``   executed qualification: run the landed family tools from a
               clean checkout and compare recomputed censuses. Requires the
               declared numerical extra; refuses (exit 2) when it is
               missing rather than silently skipping.

Exit 0 means the requested check passed; exit 2 means a NO VERDICT-grade
refusal with a concrete reason on stderr; exit 1 means a hard error.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import estimator_qualification as eq  # noqa: E402

INVENTORY_PATH = ROOT / "spec/reference/estimator-obligations-v1.json"
LEDGER_PATH = ROOT / "sim/qualification/estimators-v1.json"

# Executed-replay surface: each landed producer tool regenerates its own
# artifact exactly as its family CI does. The ledger compares recomputed
# case/row/obligation identity, never cross-host byte equality. Preparation
# receipts are retained-byte evidence validated by the integrate path, not
# regenerable by an analytic run, so replay compares the analytic census only.
REPLAY_TOOLS = {
    "periodic": ["python3", "tools/qualify_periodic_estimators.py"],
    # The committed envelope evidence binds the executed shared-preparation
    # sentinel (status "executed"); with #86 landed in this checkout the
    # replay regenerates it read-only from this same tree
    # (spec/ENVELOPE-ESTIMATORS.md: --preparation-root .).
    "envelope": [
        "python3",
        "tools/qualify_envelope_estimators.py",
        "--preparation-root",
        str(ROOT),
    ],
    "spectral": ["python3", "tools/qualify_spectral_estimators.py"],
    "preparation": ["python3", "tools/qualify_preparation.py", "analytic"],
}
REPLAY_KEYS = {
    "periodic": None,
    "envelope": None,
    "spectral": None,
    # The committed preparation grid/refusals embed retained-byte runtime
    # receipt counts that an analytic regeneration cannot produce; replay
    # reconciles the analytic census and module identity only.
    "preparation": [
        "verdicts",
        "coverage",
        "floors",
        "floor_limited",
        "module_sha256",
        "algorithm_version",
    ],
}


def _replay_view(ledger, family):
    view = eq._ledger_inventory_view(ledger, family)
    keys = REPLAY_KEYS[family]
    if keys is not None:
        missing = [key for key in keys if key not in view]
        if missing:
            raise SystemExit(
                f"hard error: replay view lost keys {missing}; "
                "refusing to compare a shrunk denominator"
            )
        view = {key: view[key] for key in keys}
    return view


def _load_inventory() -> dict:
    if not INVENTORY_PATH.exists():
        print(f"missing reviewed inventory: {INVENTORY_PATH}", file=sys.stderr)
        raise SystemExit(1)
    return eq.load_artifact(INVENTORY_PATH)


def _no_numpy() -> bool:
    try:
        import numpy  # noqa: F401
    except ImportError:
        return True
    return False


def cmd_build(args: argparse.Namespace) -> int:
    inventory = _load_inventory()
    ledger = eq.build_ledger(ROOT)
    eq.check_inventory(ledger, inventory)
    destination = Path(args.output) if args.output else LEDGER_PATH
    destination.write_text(eq.ledger_to_json(ledger), encoding="utf-8")
    print(f"ledger written: {destination}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    del args  # the stored-evidence check has no options by design
    inventory = _load_inventory()
    ledger = eq.build_ledger(ROOT)
    eq.check_inventory(ledger, inventory)
    if not LEDGER_PATH.exists():
        print(f"NO VERDICT: missing committed ledger {LEDGER_PATH}", file=sys.stderr)
        return 2
    committed = eq.ledger_from_json(LEDGER_PATH.read_text(encoding="utf-8"))
    if committed != ledger:
        print(
            "NO VERDICT: committed ledger differs from the recomputed ledger; "
            "refusing to treat stale evidence as qualification",
            file=sys.stderr,
        )
        return 2
    publication = eq.gate_dependent_families(ledger)
    inconsistent = [
        name
        for name, view in publication["families"].items()
        if view["publication"] == "NO VERDICT"
    ]
    if inconsistent:
        print(
            f"NO VERDICT: preparation gating active for {inconsistent}",
            file=sys.stderr,
        )
        return 2
    print(
        "check: ledger, inventory, and recomputed census agree; preparation "
        f"{ledger['preparation']['recomputed_sha256'][:8]} consistent"
    )
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    del args
    if _no_numpy():
        print(
            "NO VERDICT: numerical extra unavailable; executed qualification "
            "refuses rather than silently skipping",
            file=sys.stderr,
        )
        return 2
    inventory = _load_inventory()
    committed = eq.ledger_from_json(LEDGER_PATH.read_text(encoding="utf-8"))
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="estimator-replay-") as workdir:
        workdir = Path(workdir)
        # Stage each replayed artifact at the relative path build_ledger
        # expects (ARTIFACT_PATHS layout under the artifact root), and build
        # and compare once only after all four family artifacts exist.
        for name, command in REPLAY_TOOLS.items():
            output = workdir / eq.ARTIFACT_PATHS[name]
            output.parent.mkdir(parents=True, exist_ok=True)
            run = list(command) + ["--output", str(output)]
            completed = subprocess.run(
                run, cwd=ROOT, capture_output=True, text=True, check=False
            )
            if completed.returncode != 0:
                failures.append(
                    f"{name}: replay tool exited {completed.returncode}: "
                    f"{completed.stderr.strip()[-400:]}"
                )
                continue
        if not failures:
            fresh_ledger = eq.build_ledger(ROOT, artifact_root=workdir)
            # Compare per-family census identity against the committed ledger.
            for family in eq.ARTIFACT_PATHS:
                fresh_view = _replay_view(fresh_ledger, family)
                old_view = _replay_view(committed, family)
                if fresh_view != old_view:
                    failures.append(
                        f"{family}: replayed census identity differs from the "
                        "committed ledger"
                    )
    if failures:
        for failure in failures:
            print(f"replay drift: {failure}", file=sys.stderr)
        print("NO VERDICT: executed replay exposed drift", file=sys.stderr)
        return 2
    eq.check_inventory(committed, inventory)
    print("replay: executed grids reproduce case/row/obligation identity")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="rebuild the committed ledger")
    build.add_argument("--output", default=None)
    sub.add_parser("check", help="stored-evidence three-way validation")
    sub.add_parser("replay", help="executed qualification via landed family tools")
    args = parser.parse_args(argv)
    if args.command == "build":
        return cmd_build(args)
    if args.command == "check":
        return cmd_check(args)
    return cmd_replay(args)


if __name__ == "__main__":
    raise SystemExit(main())
