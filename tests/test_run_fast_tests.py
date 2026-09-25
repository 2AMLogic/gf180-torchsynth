"""Local tb-flow wrapper aggregation (issue #182).

``tools/run_fast_tests.py`` shells out to ``tb/run_tb.py`` per lane and
must aggregate PASS/FAIL strictly from each subprocess's own return code
-- never from the SV testbench's unconditional ``TB-DONE <n>`` completion
marker (``tb/sv/tb_sine_vco_engine.sv``, printed on every run regardless
of outcome). These tests exercise that aggregation two ways:

1. Against a small stand-in "tb script" (via the wrapper's ``--tb-script``
   test hook) that prints a healthy-looking ``TB-DONE 1`` marker on every
   run but returns a non-zero exit code for a designated "bad" lane --
   proving the wrapper trusts the return code, not the marker text. This
   runs everywhere (no simulator required) and so gets real CI coverage
   even where Icarus Verilog is not installed.
2. Against the real ``vco`` lane through the real ``tb/run_tb.py`` harness
   (gated on Icarus Verilog being installed, matching the pattern in
   ``tests/test_vco_engine.py``) -- the exact regression this issue
   reports: a real, unmutated vco-lane pass must make the wrapper exit 0.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "tools" / "run_fast_tests.py"

sys.path.insert(0, str(ROOT / "tools"))
import run_fast_tests as rft  # noqa: E402


def has_iverilog() -> bool:
    return shutil.which("iverilog") is not None


#: A minimal stand-in for ``tb/run_tb.py``'s CLI contract: a positional
#: ``command`` (lane) argument, ``--simulator``/``--workdir`` flags it
#: accepts and ignores, and a return code that is 0 unless the requested
#: lane is named in the ``FAKE_TB_FAIL_LANES`` env var (comma-separated).
#: Every run -- pass or fail -- prints the same unconditional "TB-DONE 1"
#: marker the real SV testbench prints, mirroring the exact scenario this
#: issue reports: a healthy completion marker that is NOT a pass signal.
FAKE_TB_SCRIPT = textwrap.dedent(
    r'''
    import argparse
    import os
    import sys


    def main():
        parser = argparse.ArgumentParser()
        parser.add_argument("command")
        parser.add_argument("--simulator", default="iverilog")
        parser.add_argument("--workdir", default=None)
        args = parser.parse_args()

        # Mirrors tb/sv/tb_sine_vco_engine.sv's own completion marker:
        # printed unconditionally, on every run, pass or fail.
        print("TB-DONE 1")
        print(
            "tb/sv/tb_sine_vco_engine.sv:180: $finish called at "
            "1764020000 (1ps)"
        )

        fail_lanes = set(
            filter(None, os.environ.get("FAKE_TB_FAIL_LANES", "").split(","))
        )
        if args.command in fail_lanes:
            # Mirrors the wrong-lut-address mutation probe named in the
            # original report: a real detected mismatch, reported as a
            # failure line, well after the healthy TB-DONE marker.
            print(
                "mutation wrong-lut-address (RTL, case source:vco_1): "
                "NOT DETECTED via neither"
            )
            print("SINE-VCO RUN FAILED")
            return 1
        print("SINE-VCO RUN PASSED (fake)")
        return 0


    if __name__ == "__main__":
        sys.exit(main())
    '''
)


class ResolveLanesTest(unittest.TestCase):
    """Pure unit tests for --tb-flow/--filter resolution (no subprocess)."""

    def test_all_expands_to_every_lane(self):
        self.assertEqual(list(rft.LANES), rft.resolve_lanes("all", None))

    def test_all_plus_filter_narrows_to_matching_lanes(self):
        # vco2 legitimately contains the substring "vco".
        self.assertEqual(["vco", "vco2"], rft.resolve_lanes("all", "vco"))

    def test_explicit_list_is_deduplicated_in_order(self):
        self.assertEqual(
            ["vco", "noise"], rft.resolve_lanes("vco,noise,vco", None)
        )

    def test_filter_is_a_noop_when_it_already_matches(self):
        self.assertEqual(["vco"], rft.resolve_lanes("vco", "vco"))

    def test_unknown_lane_raises(self):
        with self.assertRaises(SystemExit):
            rft.resolve_lanes("not-a-real-lane", None)

    def test_filter_matching_nothing_raises(self):
        with self.assertRaises(SystemExit):
            rft.resolve_lanes("vco", "zzz-no-match")


class FakeTbScriptFixture(unittest.TestCase):
    """Shared setup for tests driving the wrapper against a fake tb script."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="fake-tb-")
        self.addCleanup(self._tmpdir.cleanup)
        self.fake_tb_script = Path(self._tmpdir.name) / "fake_run_tb.py"
        self.fake_tb_script.write_text(FAKE_TB_SCRIPT, encoding="utf-8")

    def run_wrapper(self, *extra_args, fail_lanes=""):
        env = dict(os.environ)
        env["FAKE_TB_FAIL_LANES"] = fail_lanes
        return subprocess.run(
            [
                sys.executable, str(WRAPPER),
                "--tb-script", str(self.fake_tb_script),
            ] + list(extra_args),
            cwd=ROOT, capture_output=True, text=True, timeout=120, env=env,
        )


class AggregationUsesReturnCodeTest(FakeTbScriptFixture):
    """The core regression: TB-DONE must never stand in for pass/fail."""

    def test_healthy_marker_with_nonzero_exit_is_reported_as_failure(self):
        result = self.run_wrapper(
            "--tb-flow", "vco", "--parallel", "--filter", "vco",
            fail_lanes="vco",
        )
        self.assertIn("TB-DONE 1", result.stdout)
        self.assertNotEqual(
            0, result.returncode,
            "wrapper exited 0 despite a failing lane subprocess:\n%s"
            % result.stdout,
        )
        self.assertIn("FAIL", result.stdout)

    def test_healthy_marker_with_zero_exit_is_reported_as_pass(self):
        result = self.run_wrapper(
            "--tb-flow", "vco", "--parallel", "--filter", "vco",
            fail_lanes="",
        )
        self.assertEqual(
            0, result.returncode,
            "wrapper exited non-zero on an all-passing lane:\n%s"
            % result.stdout,
        )
        self.assertIn("overall: PASS", result.stdout)


class ParallelAggregationDoesNotMaskFailureTest(FakeTbScriptFixture):
    """Guards the exact bug class the issue names: OR/early-exit masking.

    Runs two distinct lanes concurrently (--parallel), one passing and one
    failing, and requires the overall result to stay non-zero regardless
    of which subprocess happens to finish (and get aggregated) last.
    """

    def test_one_failing_lane_among_several_fails_the_whole_run(self):
        result = self.run_wrapper(
            "--tb-flow", "vco,vco2", "--parallel",
            fail_lanes="vco2",
        )
        self.assertNotEqual(0, result.returncode)
        self.assertRegex(result.stdout, r"(?m)^vco\s+PASS\b")
        self.assertRegex(result.stdout, r"(?m)^vco2\s+FAIL\b")
        self.assertIn("1/2 lanes passed -- overall: FAIL", result.stdout)

    def test_all_passing_lanes_in_parallel_is_a_pass(self):
        result = self.run_wrapper(
            "--tb-flow", "vco,vco2", "--parallel", fail_lanes="",
        )
        self.assertEqual(0, result.returncode)
        self.assertIn("2/2 lanes passed -- overall: PASS", result.stdout)


class RealVcoLaneTest(unittest.TestCase):
    """The exact regression this issue reports, against the real harness."""

    @unittest.skipUnless(has_iverilog(), "Icarus Verilog not installed")
    def test_wrapper_exits_zero_for_a_real_unmutated_vco_pass(self):
        result = subprocess.run(
            [
                sys.executable, str(WRAPPER),
                "--tb-flow", "vco", "--parallel", "--filter", "vco",
            ],
            cwd=ROOT, capture_output=True, text=True, timeout=5400,
        )
        if result.returncode != 0:
            self.fail(
                "tools/run_fast_tests.py --tb-flow vco --parallel "
                "--filter vco exited %d on a real (unmutated) vco lane "
                "(the exact regression this issue reports):\n%s\n%s"
                % (result.returncode, result.stdout[-4000:],
                   result.stderr[-2000:])
            )
        self.assertIn("overall: PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
