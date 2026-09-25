#!/usr/bin/env python3
"""Local tb-flow wrapper around ``tb/run_tb.py`` (issue #182).

``tb/run_tb.py`` is the repo's real, committed, CI-exercised testbench
harness: it takes a single positional ``command`` (lane name) and prints a
lot of diagnostic text, including the SV testbench's own unconditional
completion marker, ``TB-DONE <n>`` (``tb/sv/tb_sine_vco_engine.sv``,
printed by the testbench's own final ``$display`` regardless of whether
any comparison inside the run failed). That marker is NOT a pass/fail
signal -- it only means "the simulation finished running all cases".

This wrapper exists to run one or more ``tb/run_tb.py`` lanes -- optionally
concurrently -- and aggregate PASS/FAIL **strictly from each subprocess's
own exit code** (``0`` == pass, matching ``tb/run_tb.py``'s own contract:
see its ``main()``/lane functions, which ``return 1`` on any failed
comparison and only ``return 0`` when every check in the lane passed).
Aggregation across lanes is AND-of-zero: any non-zero lane makes the whole
run non-zero, and that is *always* determined only after every selected
lane has finished -- never via an early return that could let a later
result silently override an earlier failure.

Usage (per the "Box test invocation" contract in AGENTS.md/CLAUDE.md):

    python3.11 tools/run_fast_tests.py --tb-flow vco --parallel --filter vco

``--tb-flow`` accepts a comma-separated list of lane names (the same
choices ``tb/run_tb.py``'s positional ``command`` argument accepts) or the
literal ``all`` to mean every known lane. ``--filter`` is an optional
case-insensitive substring further narrowing that selection -- handy with
``--tb-flow all`` (e.g. ``--tb-flow all --filter vco`` selects both ``vco``
and ``vco2``), and a no-op when it already matches the exact lane(s) named
by ``--tb-flow`` (as in the invocation above). ``--parallel`` runs the
selected lanes concurrently, one ``tb/run_tb.py`` subprocess per lane.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]

#: Lane names, in the exact order ``tb/run_tb.py``'s ``command`` argument
#: declares them (its ``choices=[...]`` list) -- keep in sync by hand since
#: importing the 6000+ line module just to read one literal is not worth
#: the cost of pulling in its heavy dependency chain (torch, numpy, ...).
LANES: Tuple[str, ...] = (
    "selftest", "anchor", "adsr", "patch", "lfo", "modmatrix",
    "vco", "vco2", "noise", "mix", "normreplay",
)


class LaneResult:
    """One lane's outcome: strictly the subprocess's own exit code."""

    def __init__(self, lane: str, returncode: int, elapsed: float,
                 stdout: str, stderr: str, command: Sequence[str]):
        self.lane = lane
        self.returncode = returncode
        self.elapsed = elapsed
        self.stdout = stdout
        self.stderr = stderr
        self.command = list(command)

    @property
    def passed(self) -> bool:
        return self.returncode == 0


def resolve_lanes(tb_flow: str, filter_pattern: Optional[str]) -> List[str]:
    """Expand ``--tb-flow`` (comma list or ``all``) then ``--filter``."""

    requested = [part.strip() for part in tb_flow.split(",") if part.strip()]
    if not requested:
        raise SystemExit("ERROR: --tb-flow selected no lanes")

    if [item.lower() for item in requested] == ["all"]:
        selected = list(LANES)
    else:
        selected = []
        for name in requested:
            if name not in LANES:
                raise SystemExit(
                    "ERROR: unknown --tb-flow lane %r (choices: %s)"
                    % (name, ", ".join(LANES))
                )
            if name not in selected:
                selected.append(name)

    if filter_pattern:
        needle = filter_pattern.lower()
        selected = [lane for lane in selected if needle in lane.lower()]

    if not selected:
        raise SystemExit(
            "ERROR: --tb-flow %r combined with --filter %r matched no "
            "lanes (choices: %s)"
            % (tb_flow, filter_pattern, ", ".join(LANES))
        )
    return selected


def build_command(tb_script: Path, lane: str, simulator: str,
                   lane_workdir: Optional[Path]) -> List[str]:
    command = [sys.executable, str(tb_script), lane, "--simulator", simulator]
    if lane_workdir is not None:
        command += ["--workdir", str(lane_workdir)]
    return command


def run_lane(tb_script: Path, lane: str, simulator: str,
             lane_workdir: Optional[Path]) -> LaneResult:
    command = build_command(tb_script, lane, simulator, lane_workdir)
    started = time.monotonic()
    completed = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True,
    )
    elapsed = time.monotonic() - started
    return LaneResult(
        lane, completed.returncode, elapsed, completed.stdout,
        completed.stderr, command,
    )


def run_lanes(lanes: Sequence[str], tb_script: Path, simulator: str,
              workdir: Optional[Path], parallel: bool,
              jobs: Optional[int]) -> List[LaneResult]:
    """Run every lane to completion and return ALL results.

    Deliberately never short-circuits on the first failure (sequential or
    parallel): every lane's own subprocess is awaited before the caller
    looks at any result, so a later PASS can never mask an earlier FAIL
    and vice versa (the aggregation bug class this wrapper exists to
    avoid -- see the module docstring).
    """

    def lane_workdir(lane: str) -> Optional[Path]:
        if workdir is None:
            return None
        # Keep per-lane artifacts SHALLOW: iverilog's $readmemh silently
        # truncates paths beyond ~124 chars, producing an x-state instead
        # of a loud error, so we add exactly one short, lane-named path
        # segment -- never nest deeper than that here.
        target = workdir / lane
        target.mkdir(parents=True, exist_ok=True)
        return target

    if not parallel or len(lanes) <= 1:
        return [
            run_lane(tb_script, lane, simulator, lane_workdir(lane))
            for lane in lanes
        ]

    max_workers = jobs if jobs and jobs > 0 else len(lanes)
    results: List[Optional[LaneResult]] = [None] * len(lanes)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_index = {
            pool.submit(
                run_lane, tb_script, lane, simulator, lane_workdir(lane)
            ): index
            for index, lane in enumerate(lanes)
        }
        # Wait for every future -- intentionally not "return on first
        # exception/failure": collecting the full set before any
        # pass/fail judgment is what makes the AND-of-zero aggregation
        # below correct.
        for future in concurrent.futures.as_completed(future_to_index):
            index = future_to_index[future]
            results[index] = future.result()

    return [result for result in results if result is not None]


def report(results: Sequence[LaneResult]) -> int:
    """Print a summary and compute the overall exit code.

    The overall result is AND-of-zero across every lane's own returncode
    -- never derived from stdout text (not "TB-DONE", not a hopeful
    substring match). A lane is only a PASS if its ``tb/run_tb.py``
    subprocess itself exited 0.
    """

    print()
    print("=" * 72)
    print("tools/run_fast_tests.py summary")
    print("=" * 72)
    overall_ok = True
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        if not result.passed:
            overall_ok = False
        print(
            "%-12s %-4s (exit %d, %.1fs) -- %s"
            % (result.lane, status, result.returncode, result.elapsed,
               shlex.join(result.command))
        )
        if not result.passed:
            tail_out = result.stdout[-4000:] if result.stdout else ""
            tail_err = result.stderr[-2000:] if result.stderr else ""
            if tail_out:
                print("  --- stdout tail (%s) ---" % result.lane)
                for line in tail_out.splitlines():
                    print("  " + line)
            if tail_err:
                print("  --- stderr tail (%s) ---" % result.lane)
                for line in tail_err.splitlines():
                    print("  " + line)
    print("-" * 72)
    print(
        "%d/%d lanes passed -- overall: %s"
        % (
            sum(1 for r in results if r.passed), len(results),
            "PASS" if overall_ok else "FAIL",
        )
    )
    return 0 if overall_ok else 1


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tb-flow", dest="tb_flow", required=True,
        help="Comma-separated lane name(s) matching tb/run_tb.py's "
        "'command' choices (%s), or 'all'." % ", ".join(LANES),
    )
    parser.add_argument(
        "--filter", dest="filter_pattern", default=None,
        help="Case-insensitive substring further narrowing the --tb-flow "
        "selection (e.g. --tb-flow all --filter vco selects vco + vco2).",
    )
    parser.add_argument(
        "--parallel", action="store_true",
        help="Run the selected lanes concurrently (one tb/run_tb.py "
        "subprocess per lane) instead of sequentially.",
    )
    parser.add_argument(
        "--jobs", type=int, default=None,
        help="Max concurrent subprocesses when --parallel is set "
        "(default: one per selected lane).",
    )
    parser.add_argument(
        "--simulator", default="iverilog", choices=["iverilog"],
        help="forwarded to tb/run_tb.py (open-source simulator, PDK-free)",
    )
    parser.add_argument(
        "--workdir", type=Path, default=None,
        help="Keep each lane's artifacts under <workdir>/<lane> instead "
        "of tb/run_tb.py's own temp dir. Omit this for the shallowest "
        "possible paths (tb/run_tb.py's default tempdir already lives "
        "directly under the system temp root).",
    )
    parser.add_argument(
        "--tb-script", type=Path, default=ROOT / "tb" / "run_tb.py",
        help=argparse.SUPPRESS,  # testing-only override of the harness
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    if not args.tb_script.exists():
        print("ERROR: tb script not found: %s" % args.tb_script)
        return 2

    lanes = resolve_lanes(args.tb_flow, args.filter_pattern)
    print(
        "tools/run_fast_tests.py: running %d lane(s) %s: %s"
        % (
            len(lanes), "in parallel" if args.parallel else "sequentially",
            ", ".join(lanes),
        )
    )

    if args.workdir is not None:
        args.workdir.mkdir(parents=True, exist_ok=True)

    results = run_lanes(
        lanes, args.tb_script, args.simulator, args.workdir, args.parallel,
        args.jobs,
    )
    return report(results)


if __name__ == "__main__":
    sys.exit(main())
