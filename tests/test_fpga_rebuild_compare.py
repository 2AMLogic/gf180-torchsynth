"""The #374 FPGA post-synthesis sentinel and clean-checkout rebuild checks.

Two kinds of gate live here, none of which runs a simulator or synthesis:

1. **Unit checks** for the comparison and inventory logic the #374 flow
   added (``fpga/scripts/rebuild_compare.py``, the parsing helpers of
   ``fpga/scripts/run_postsynth_sentinel.py``) -- on synthetic fixtures, so
   they run anywhere.
2. **Committed-evidence checks** against ``fpga/reports/`` as committed:
   the rebuilt ``fpga-build-report.json`` must carry a
   ``clean_checkout_rebuild`` verdict of ``identical`` (any drift must be
   quantified in the record before it may be committed), the
   ``post-synth-sim.json`` record must state a definitive verdict
   (``passed`` or a ``refused`` whose reason is measured, never a silent
   gap), and the DR-0013 -> DR-0017 rebind must actually have happened in
   the three files whose stale hashes the #80 report bound. This is the
   same pattern ``tests/test_committed_oneshot_evidence.py`` uses for the
   RTL evidence records: the record is the claim, so CI re-reads it.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "fpga" / "scripts"))

from rebuild_compare import compare_reports  # noqa: E402
from run_postsynth_sentinel import (  # noqa: E402
    netlist_cells, sim_model_inventory,
)

REPORT = ROOT / "fpga/reports/fpga-build-report.json"
PSIM = ROOT / "fpga/reports/post-synth-sim.json"
BUILD_SH = ROOT / "fpga/build.sh"
REQUIREMENTS = ROOT / "fpga/requirements.txt"
WRAPPER = ROOT / "fpga/rtl/fpga_oneshot_top.sv"


def _report(cells=None, head="a" * 40, engines=None):
    return {
        "git": {"branch": "b", "head": head, "dirty": False},
        "decision_record": "DR-0017",
        "device": "LFE5U-85F CABGA381 speed 6 (nextpnr --85k)",
        "yosys": "Yosys 0.70", "nextpnr_banner": "nextpnr-ecp5 0.11",
        "python_packages_in_build_venv": ["yowasp-yosys==0.70.0.0.post1259"],
        "engines_failed": [],
        "synthesis_flat": {"raw_cell_types": cells or {"LUT4": 10},
                           "cells": {"LUT4": 10, "TRELLIS_FF": 2}},
        "synthesis_per_engine_standalone": engines or {},
        "place_and_route": {"nextpnr_exit": "nextpnr exit code: 1"},
    }


class TestCompareReports(unittest.TestCase):
    def test_identical_reports_are_identical(self):
        record = compare_reports(_report(), _report())
        self.assertEqual(record["verdict"], "identical")
        self.assertEqual(record["drift"], [])

    def test_single_cell_drift_is_named(self):
        new = _report(cells={"LUT4": 11})
        record = compare_reports(_report(), new)
        self.assertEqual(record["verdict"], "drift")
        self.assertIn("flat_cell:LUT4", record["drift"])
        self.assertEqual(
            record["flat_cell_counts"]["LUT4"],
            {"baseline": 10, "rebuild": 11})

    def test_missing_stage_is_drift_not_silence(self):
        new = _report(engines={"adsr_engine": {"LUT4": 1, "instances": 6}})
        record = compare_reports(_report(), new)
        self.assertEqual(record["verdict"], "drift")
        self.assertIn("engine:+adsr_engine", record["drift"])

    def test_pnr_exit_and_tool_pins_are_compared(self):
        new = _report()
        new["place_and_route"]["nextpnr_exit"] = "nextpnr exit code: 0"
        self.assertIn("place_and_route.nextpnr_exit",
                      compare_reports(_report(), new)["drift"])
        new = _report()
        new["python_packages_in_build_venv"] = ["yowasp-yosys==0.71"]
        self.assertIn("python_packages_in_build_venv",
                      compare_reports(_report(), new)["drift"])


class TestCompareCLI(unittest.TestCase):
    """The --check gate must fail on drift and pass on identity."""

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / "fpga/scripts/rebuild_compare.py")]
            + [str(a) for a in args], capture_output=True, text=True)

    def test_check_mode_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            base = tmp / "base.json"
            same = tmp / "same.json"
            drift = tmp / "drift.json"
            base.write_text(json.dumps(_report()))
            same.write_text(json.dumps(_report()))
            drifted = _report(); drifted["synthesis_flat"]["raw_cell_types"]["LUT4"] += 1
            drift.write_text(json.dumps(drifted))
            self.assertEqual(self._run(base, same, "--check").returncode, 0)
            self.assertEqual(self._run(base, drift, "--check").returncode, 1)
            # non-check mode reports drift without failing the command
            self.assertEqual(self._run(base, drift).returncode, 0)
            self.assertIn("drift", self._run(base, drift).stdout)

    def test_check_mode_gates_the_committed_rebuild_report(self):
        """Drifting the committed rebuild's own counts must fail --check."""
        committed = json.loads(REPORT.read_text(encoding="utf-8"))
        self.assertIn("clean_checkout_rebuild", committed,
                      "committed report must be the #374 rebuild")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            same = tmp / "same.json"
            drift = tmp / "drift.json"
            same.write_text(json.dumps(committed))
            committed["synthesis_flat"]["raw_cell_types"]["LUT4"] = (
                committed["synthesis_flat"]["raw_cell_types"].get("LUT4", 0) + 1)
            drift.write_text(json.dumps(committed))
            self.assertEqual(
                self._run(REPORT, same, "--check").returncode, 0)
            self.assertEqual(
                self._run(REPORT, drift, "--check").returncode, 1)


class TestSimModelInventory(unittest.TestCase):
    def _share(self, tmp: Path):
        share = tmp / "ecp5"
        share.mkdir()
        (share / "cells_sim.v").write_text(
            '`include "common_sim.vh"\n'
            "module TRELLIS_FF(input CLK);\n"
            "endmodule\n"
            "(* blackbox *)\n"
            "module DP16KD(input DIA0);\n"
            "endmodule\n")
        (share / "common_sim.vh").write_text(
            "module LUT4(input A, output Z);\n"
            "endmodule\n")
        (share / "cells_bb.v").write_text(
            "(* blackbox *)\n"
            "module MULT18X18D(A0, B0, P0);\n"
            "endmodule\n")
        return share

    def test_behavioral_blackbox_and_absent_are_classified(self):
        with tempfile.TemporaryDirectory() as tmp:
            share = self._share(Path(tmp))
            inv = sim_model_inventory(share)
            modules = inv["modules"]
            self.assertEqual(modules["TRELLIS_FF"], "behavioral")
            self.assertEqual(modules["LUT4"], "behavioral")
            self.assertEqual(modules["DP16KD"], "blackbox")
            self.assertEqual(modules["MULT18X18D"],
                             "blackbox-only (cells_bb.v)")
            self.assertNotIn("PFUMX", modules)
            self.assertEqual(inv["files"], ["cells_sim.v", "common_sim.vh"])

    def test_netlist_cells_counts_every_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            nj = Path(tmp) / "n.json"
            nj.write_text(json.dumps({"modules": {
                "top": {"cells": {"c1": {"type": "LUT4"},
                                  "c2": {"type": "LUT4"}}},
                "sub": {"cells": {"c3": {"type": "MULT18X18D"}}},
            }}))
            counted = netlist_cells(nj)
            self.assertEqual(counted, {"LUT4": 2, "MULT18X18D": 1})


class TestCommittedEvidence(unittest.TestCase):
    """The records committed by #374 are claims; CI re-reads them."""

    def test_post_synth_sim_record_is_definitive(self):
        record = json.loads(PSIM.read_text(encoding="utf-8"))
        self.assertIn(record["verdict"], ("passed", "refused", "mismatch"))
        self.assertTrue(record.get("reason"))
        # whatever the verdict, the reason is measured, not vibes: a refusal
        # names its evidence, and no record ever claims a run that did not
        # happen
        self.assertTrue(record.get("reasons"))
        self.assertEqual(record.get("issue"), 374)
        self.assertEqual(record.get("decision_record"), "DR-0017")
        self.assertIn("no gf180mcu", record["claim_scope"])
        self.assertTrue(record["netlist"]["cell_total"] > 0)
        self.assertIn("MULT18X18D", record["netlist"]["cell_counts"],
                      "the measured netlist must still contain the DSPs; "
                      "if it does not, the design was shrunk")
        for key in ("json_sha256", "verilog_sha256"):
            digest = record["netlist"][key]
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertTrue(record["sentinel"]["stimulus_files_sha256"])
        # a run that never simulated must say refused, not passed
        if record["verdict"] == "refused":
            self.assertIn("gap",
                          (record["reason"] + record["reasons"][0]).lower())

    def test_rebuilt_report_reproduces_and_records_the_baseline(self):
        report = json.loads(REPORT.read_text(encoding="utf-8"))
        rebuild = report.get("clean_checkout_rebuild")
        self.assertIsNotNone(
            rebuild,
            "committed fpga-build-report.json must be the #374 "
            "clean-checkout rebuild (run build.sh report in a clean "
            "checkout of the code commit)")
        self.assertEqual(rebuild["verdict"], "identical",
                         "drift must be quantified in the record before "
                         "committing: %s" % rebuild["drift"])
        self.assertEqual(rebuild["rebuild_issue"], 374)
        self.assertTrue(rebuild["baseline"]["git_head"].startswith(
            "feature/issue-80@"))
        self.assertEqual(report["post_synth_sim"]["verdict"],
                         json.loads(PSIM.read_text(encoding="utf-8"))
                         ["verdict"])
        self.assertRegex(report["hashes_sha256"]["voice_netlist_json"],
                         r"^[0-9a-f]{64}$")
        self.assertRegex(report["hashes_sha256"]["voice_netlist_verilog"],
                         r"^[0-9a-f]{64}$")

    def test_dr0013_rebind_happened_in_the_hash_bound_files(self):
        for path in (BUILD_SH, REQUIREMENTS, WRAPPER):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("DR-0013", text,
                             "%s must say DR-0017 (the #374 rebuild "
                             "regenerated the hash bindings)" % path)
            self.assertIn("DR-0017", text)


if __name__ == "__main__":
    unittest.main()
