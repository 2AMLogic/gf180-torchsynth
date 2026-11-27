"""Guards for the ``klt functional-verification`` cocotb lanes (issue #388).

These tests do not run a simulator (``tb/klt/run_lanes.py run`` does, in
``.github/workflows/tb-sim.yml``). They pin the properties that make the
committed record trustworthy: a vacuous or partial run is refused, every lane
has a registered negative control whose mutation site still exists, the
default (non-cocotb) bench behaviour is intact, and the committed record's
hashes match the tree.
"""
import importlib.util
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KLT_DIR = ROOT / "tb" / "klt"
RECORD = ROOT / "sim/evidence/functional-verification-modules-v1.json"

_spec = importlib.util.spec_from_file_location("run_lanes", KLT_DIR / "run_lanes.py")
run_lanes = importlib.util.module_from_spec(_spec)
sys.modules["run_lanes"] = run_lanes
_spec.loader.exec_module(run_lanes)


def _good_report(**overrides):
    report = {
        "status": "pass",
        "test_count": 1,
        "passed_count": 1,
        "failed_count": 0,
        "skipped_count": 0,
        "tests": [{"name": "t", "status": "passed"}],
        "environment": {},
    }
    report.update(overrides)
    return report


class VerdictRefusesAnythingButACleanPassTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.xml = Path(self._dir.name) / "results.xml"
        self.xml.write_text("<testsuites/>", encoding="utf-8")

    def test_a_clean_pass_is_accepted(self):
        run_lanes.verdict(_good_report(), self.xml)

    def test_failed_status_is_refused(self):
        with self.assertRaises(run_lanes.LaneRefused):
            run_lanes.verdict(
                _good_report(status="fail", passed_count=0, failed_count=1,
                             tests=[{"name": "t", "status": "failed"}]),
                self.xml,
            )

    def test_zero_tests_is_not_a_pass(self):
        with self.assertRaises(run_lanes.LaneRefused):
            run_lanes.verdict(_good_report(test_count=0, passed_count=0, tests=[]), self.xml)

    def test_a_skipped_test_is_refused(self):
        with self.assertRaises(run_lanes.LaneRefused):
            run_lanes.verdict(
                _good_report(test_count=2, passed_count=1, skipped_count=1,
                             tests=[{"name": "a", "status": "passed"},
                                    {"name": "b", "status": "skipped"}]),
                self.xml,
            )

    def test_a_klt_error_envelope_is_refused(self):
        with self.assertRaises(run_lanes.LaneRefused):
            run_lanes.verdict({"error": {"message": "build failed"}}, self.xml)

    def test_missing_or_empty_results_xml_is_refused(self):
        with self.assertRaises(run_lanes.LaneRefused):
            run_lanes.verdict(_good_report(), self.xml.with_name("absent.xml"))
        self.xml.write_text("", encoding="utf-8")
        with self.assertRaises(run_lanes.LaneRefused):
            run_lanes.verdict(_good_report(), self.xml)
        with self.assertRaises(run_lanes.LaneRefused):
            run_lanes.verdict(_good_report(), None)


class LaneRegistryTest(unittest.TestCase):
    def test_the_ten_named_lanes_are_present_or_declared(self):
        # `patch` is two lanes (one bench elaboration per NOISE_CLIP_BYTES).
        named = {"anchor", "vco", "vco2", "adsr", "patch", "patch_small", "lfo",
                 "modmatrix", "noise", "mix", "normreplay"}
        self.assertEqual(named - set(run_lanes.lanes_available()), set())

    def test_every_lane_has_module_vectors_and_a_negative_control(self):
        for lane in run_lanes.lanes_available():
            with self.subTest(lane=lane):
                self.assertTrue((KLT_DIR / ("test_%s.py" % lane)).is_file())
                self.assertTrue(run_lanes.vector_files(lane), "no vector inputs")
                self.assertIn(lane, run_lanes.NEGATIVE_CONTROLS)

    def test_every_negative_control_site_exists_exactly_once(self):
        for lane, (source, old, new) in run_lanes.NEGATIVE_CONTROLS.items():
            with self.subTest(lane=lane):
                text = (ROOT / source).read_text(encoding="utf-8")
                self.assertEqual(text.count(old), 1)
                self.assertNotEqual(old, new)
                self.assertIn(
                    (ROOT / source).resolve(), run_lanes.request_sources(lane),
                    "the mutated file is not one of the lane's request sources",
                )

    def test_every_request_defines_the_cocotb_switch_and_a_toplevel(self):
        for lane in run_lanes.lanes_available():
            with self.subTest(lane=lane):
                request = json.loads(
                    (KLT_DIR / ("%s.request.json" % lane)).read_text(encoding="utf-8")
                )
                self.assertEqual(request["engine"], "icarus")
                self.assertEqual(request["options"]["defines"].get("KLT_COCOTB"), "1")
                self.assertEqual(request["testbench"]["module"], "test_%s" % lane)
                self.assertIn(request["hdl_toplevel"], ("tb", "tb_patch_control"))


class BenchesKeepTheirDefaultFinishTest(unittest.TestCase):
    def test_done_flag_is_confined_to_the_cocotb_define(self):
        # Without KLT_COCOTB every bench must still end in $finish; the flag
        # declaration and the replaced finish both sit inside `ifdef blocks.
        for path in sorted((ROOT / "tb/sv").glob("tb_*.sv")):
            text = path.read_text(encoding="utf-8")
            if "klt_done" not in text:
                continue
            if path.name == "tb_patch_control.sv":
                # The multi-run variant keeps the whole original initial
                # block verbatim in the `else branch (see the bench).
                with self.subTest(bench=path.name):
                    self.assertIn("`ifdef KLT_COCOTB", text)
                    self.assertIn("`else", text)
                continue
            with self.subTest(bench=path.name):
                self.assertRegex(
                    text,
                    r"`ifdef KLT_COCOTB\s*\n\s*reg klt_done = 1'b0;\s*\n`endif|"
                    r"`ifdef KLT_COCOTB[^`]*reg klt_done[^`]*`endif",
                )
                self.assertRegex(
                    text,
                    r"`ifdef KLT_COCOTB\s*\n\s*klt_done = 1'b1;\s*\n`else\s*\n\s*\$finish;\s*\n`endif",
                )

    def test_every_lane_bench_has_a_done_flag(self):
        benches = {
            "adsr": "tb_adsr_engine", "anchor": "tb_lut_sine_dut",
            "lfo": "tb_lfo_vca_engine", "modmatrix": "tb_mod_matrix_upsample",
            "mix": "tb_audio_mix_engine", "noise": "tb_noise_stream_dut",
            "normreplay": "tb_normalization_replay_engine",
            "vco": "tb_sine_vco_engine", "vco2": "tb_square_saw_vco",
            "patch": "tb_patch_control",
        }
        for lane, bench in benches.items():
            with self.subTest(lane=lane):
                self.assertIn(
                    "klt_done",
                    (ROOT / "tb/sv" / (bench + ".sv")).read_text(encoding="utf-8"),
                )


class CommittedRecordTest(unittest.TestCase):
    def test_record_exists_and_is_fresh_against_the_tree(self):
        self.assertTrue(RECORD.is_file(), "%s is missing" % RECORD)
        self.assertEqual(run_lanes.check_record(RECORD), [])

    def test_record_declares_gaps_and_makes_no_synthesis_claim(self):
        record = json.loads(RECORD.read_text(encoding="utf-8"))
        gaps = " ".join(g["item"] for g in record["declared_gaps"])
        for needle in ("whole-voice", "tail", "render binding", "synthesis"):
            self.assertIn(needle, gaps)
        self.assertRegex(record["scope"], r"No synthesis, SDF")
        self.assertEqual(record["result"], "pass")

    def test_record_carries_provenance_and_negative_controls(self):
        record = json.loads(RECORD.read_text(encoding="utf-8"))
        tools = record["identity"]["tools"]
        for key in ("klt", "cocotb", "iverilog"):
            self.assertTrue(tools.get(key), key)
        self.assertFalse(record["identity"]["git_tree_dirty"])
        self.assertRegex(record["identity"]["git_head"], r"^[0-9a-f]{40}$")
        controlled = {c["lane"] for c in record["negative_controls"]}
        self.assertEqual(controlled, {l["lane"] for l in record["lanes"]})
        for control in record["negative_controls"]:
            self.assertTrue(control["detected"], control["lane"])
            self.assertEqual(control["observed"], "fail")
        for lane in record["lanes"]:
            self.assertRegex(lane["results_xml_sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue(lane["rtl_sha256"] and lane["vector_sha256"])


if __name__ == "__main__":
    unittest.main()
