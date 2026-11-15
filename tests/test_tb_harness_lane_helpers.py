"""Issue #472: the shared lane scaffold in ``tb/tb_harness.py``.

Four helpers replaced eleven copy-per-lane functions in ``tb/run_tb.py``.
A refactor is only safe if the lanes' observable behavior is unchanged, so
this module pins exactly what the duplicated bodies used to guarantee:

- the five ``*_load_vectors`` refusal strings, verbatim, including the
  directory and ``sine_load_vectors``' "run tools/generate_sine_vco_golden.py"
  hint -- a normalized wording would be a silent change to a refusal
  message the #68 anchor flow relies on;
- run-major op-counter row parsing;
- the owner-row budget report's byte-exact printed lines for both engine
  tables, including the ``None``-limit "n/a (reported)" branch and the
  "emission failed" path that must never read as a pass;
- the multi-run simulate wrapper's ``runs.txt`` write, forwarded
  executable/sources, and per-run capture/status parsing.

Nothing here runs a simulator: the compile/run seam is mocked, so this is
a PDK-free, iverilog-free unit check and makes no hardware claim.
"""

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tb"))

import run_tb  # noqa: E402
import tb_harness  # noqa: E402


class LoadGoldenVectorsRefusalTest(unittest.TestCase):
    """Each lane's empty-directory refusal string, verbatim."""

    #: (run_tb wrapper, the ``*_VECTOR_DIR`` global it reads, expected text)
    LANES = (
        ("adsr_load_vectors", "ADSR_VECTOR_DIR",
         "no ADSR golden vectors found in %s"),
        ("lfo_load_vectors", "LFO_VECTOR_DIR",
         "no LFO golden vectors found in %s"),
        ("mm_load_vectors", "MM_VECTOR_DIR",
         "no mod-matrix golden vectors found in %s"),
        ("vco_load_vectors", "VCO_VECTOR_DIR",
         "no square/saw VCO golden vectors found in %s"),
        ("sine_load_vectors", "SINE_VECTOR_DIR",
         "no directed sine VCO golden vectors found in %s; run "
         "tools/generate_sine_vco_golden.py"),
    )

    def test_empty_directory_refuses_with_the_lane_s_own_message(self):
        for wrapper, global_name, template in self.LANES:
            with self.subTest(lane=wrapper), \
                    tempfile.TemporaryDirectory() as tmp:
                empty = Path(tmp)
                with mock.patch.object(run_tb, global_name, empty):
                    with self.assertRaises(SystemExit) as caught:
                        getattr(run_tb, wrapper)()
                self.assertEqual(str(caught.exception), template % empty)

    def test_every_lane_message_is_distinct(self):
        self.assertEqual(
            len({template for _, _, template in self.LANES}), len(self.LANES)
        )


class LoadGoldenVectorsTest(unittest.TestCase):
    def test_sorted_by_stem_and_every_vector_contract_verified(self):
        verified = []
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for stem in ("b_case", "a_case"):
                (directory / (stem + ".json")).write_text("{}", encoding="utf-8")
            (directory / "ignored.txt").write_text("x", encoding="utf-8")
            with mock.patch.object(
                tb_harness.gv, "load_vector", lambda path: {"from": path.stem}
            ), mock.patch.object(
                tb_harness.gv, "verify_accepted_contract", verified.append
            ):
                vectors = tb_harness.load_golden_vectors(directory, "TEST")
        self.assertEqual(list(vectors), ["a_case", "b_case"])
        self.assertEqual(vectors["a_case"], {"from": "a_case"})
        self.assertEqual(verified, [{"from": "a_case"}, {"from": "b_case"}])

    def test_contract_refusal_propagates(self):
        def refuse(vector):
            raise SystemExit("contract refused")

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "case.json").write_text("{}", encoding="utf-8")
            with mock.patch.object(
                tb_harness.gv, "load_vector", lambda path: {}
            ), mock.patch.object(
                tb_harness.gv, "verify_accepted_contract", refuse
            ):
                with self.assertRaises(SystemExit) as caught:
                    tb_harness.load_golden_vectors(directory, "TEST")
        self.assertEqual(str(caught.exception), "contract refused")


class ReadOpsRowsTest(unittest.TestCase):
    def test_run_major_rows_skipping_blank_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            (workdir / "run0_ops.txt").write_text(
                "1 2 3\n\n4 5 6\n", encoding="utf-8"
            )
            (workdir / "run1_ops.txt").write_text("7 8 9\n", encoding="utf-8")
            rows = tb_harness.read_ops_rows(workdir, 2)
        self.assertEqual(rows, [[1, 2, 3], [4, 5, 6], [7, 8, 9]])

    def test_missing_ops_file_is_an_error_not_an_empty_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                tb_harness.read_ops_rows(Path(tmp), 1)


class CheckOwnerRowBudgetTest(unittest.TestCase):
    #: The real tables and labels ``check_adsr_budget`` / ``check_lfo_budget``
    #: hand the shared loop, with the lines they printed before #472.
    ADSR_TABLE = {
        "multiply-class ops": (24, 30),
        "declared narrowings": (12, 30),
        "`**alpha` shadow words": (18, 18),
        "ramp divisions (declared extra)": (18, None),
    }
    ADSR_LINES = [
        "  budget: 24 multiply-class ops per control tick vs DR-0010 "
        "owner-row cap 30 -> OK",
        "  budget: 12 declared narrowings per control tick vs DR-0010 "
        "owner-row cap 30 -> OK",
        "  budget: 18 `**alpha` shadow words per control tick vs DR-0010 "
        "owner-row cap 18 -> OK",
        "  budget: 18 ramp divisions (declared extra) per control tick vs "
        "DR-0010 owner-row cap None -> n/a (reported)",
    ]
    LFO_TABLE = {
        "multiply-class ops": (12, 14),
        "declared narrowings": (20, None),
        "C5-class LUT interps": (2, 2),
        "phase steps": (2, 2),
    }
    LFO_LINES = [
        "  budget: 12 multiply-class ops per control tick vs DR-0010 #71 "
        "owner-row cap 14 -> OK",
        "  budget: 20 declared narrowings per control tick vs DR-0010 #71 "
        "owner-row cap None -> n/a (reported)",
        "  budget: 2 C5-class LUT interps per control tick vs DR-0010 #71 "
        "owner-row cap 2 -> OK",
        "  budget: 2 phase steps per control tick vs DR-0010 #71 "
        "owner-row cap 2 -> OK",
    ]

    def _run(self, table, label, constants_check):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = tb_harness.check_owner_row_budget(
                {"schedule": 1}, table, label, constants_check
            )
        return result, out.getvalue().splitlines()

    def test_adsr_table_prints_its_pre_refactor_lines(self):
        ok, lines = self._run(self.ADSR_TABLE, "DR-0010", lambda s: True)
        self.assertTrue(ok)
        self.assertEqual(lines, self.ADSR_LINES)

    def test_lfo_table_prints_its_pre_refactor_lines(self):
        ok, lines = self._run(self.LFO_TABLE, "DR-0010 #71", lambda s: True)
        self.assertTrue(ok)
        self.assertEqual(lines, self.LFO_LINES)

    def test_over_cap_counter_fails_and_says_so(self):
        ok, lines = self._run(
            {"multiply-class ops": (31, 30)}, "DR-0010", lambda s: True
        )
        self.assertFalse(ok)
        self.assertEqual(
            lines,
            ["  budget: 31 multiply-class ops per control tick vs DR-0010 "
             "owner-row cap 30 -> FAIL"],
        )

    def test_reported_only_counter_never_fails_the_budget(self):
        ok, _ = self._run(
            {"ramp divisions (declared extra)": (10 ** 9, None)},
            "DR-0010", lambda s: True,
        )
        self.assertTrue(ok)

    def test_failed_emission_check_fails_the_budget(self):
        ok, _ = self._run(self.ADSR_TABLE, "DR-0010", lambda s: False)
        self.assertFalse(ok)

    def test_raising_emission_check_is_reported_never_a_silent_pass(self):
        def explode(schedule):
            raise RuntimeError("codegen refused")

        ok, lines = self._run(self.ADSR_TABLE, "DR-0010", explode)
        self.assertFalse(ok)
        self.assertEqual(
            lines[-1], "  budget constants: emission failed -> codegen refused"
        )

    def test_schedule_is_forwarded_to_the_emission_check(self):
        seen = []
        ok, _ = self._run(
            {"multiply-class ops": (1, 2)}, "DR-0010",
            lambda schedule: seen.append(schedule) or True,
        )
        self.assertTrue(ok)
        self.assertEqual(seen, [{"schedule": 1}])


class SimulateRunsTest(unittest.TestCase):
    def test_runs_file_sources_and_per_run_parsing(self):
        compiled = {}

        def fake_compile(workdir, simulator, executable, sources):
            compiled["args"] = (workdir, simulator, executable, sources)
            return workdir / executable

        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            for run in range(2):
                (workdir / ("run%d_captured.txt" % run)).write_text(
                    "%d\n\n%d\n" % (run, run + 10), encoding="utf-8"
                )
                (workdir / ("run%d_status.txt" % run)).write_text(
                    "%d %d\n" % (run, run + 1), encoding="utf-8"
                )
            with mock.patch.object(
                tb_harness, "_compile_iverilog", fake_compile
            ), mock.patch.object(tb_harness, "_run") as runner:
                captures, statuses = tb_harness.simulate_runs(
                    workdir, "iverilog", 2, "lane.vvp",
                    [Path("pkg.sv"), Path("dut.sv"), Path("tb.sv")],
                )
            self.assertEqual(
                (workdir / "runs.txt").read_text(encoding="utf-8"), "2\n"
            )
            self.assertEqual(
                compiled["args"],
                (workdir, "iverilog", "lane.vvp",
                 [Path("pkg.sv"), Path("dut.sv"), Path("tb.sv")]),
            )
            runner.assert_called_once_with(
                ["vvp", "-n", str(workdir / "lane.vvp")], cwd=workdir
            )
        self.assertEqual(captures, [[0, 10], [1, 11]])
        self.assertEqual(statuses, [[0, 1], [1, 2]])


class ReExportTest(unittest.TestCase):
    def test_run_tb_re_exports_every_shared_lane_helper(self):
        for name in ("load_golden_vectors", "read_ops_rows", "simulate_runs",
                     "check_owner_row_budget"):
            with self.subTest(helper=name):
                self.assertIs(
                    getattr(run_tb, name), getattr(tb_harness, name)
                )


if __name__ == "__main__":
    unittest.main()
