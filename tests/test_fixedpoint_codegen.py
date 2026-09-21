"""Refusal-gated RTL constants emitter over the accepted contract sources.

The DR-0008 choice register supplies the numeric formats; the DR-0010
schedule register supplies the ratified cycle-budget constants. Each gates
independently through its own require_accepted refusal gate.
"""

import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.fixedpoint import choices as choices_module  # noqa: E402
from torchsynth_voice.fixedpoint import codegen  # noqa: E402
from torchsynth_voice.fixedpoint import schedule as schedule_module  # noqa: E402

TOOL = ROOT / "tools/generate_rtl_constants.py"
PACKAGE = ROOT / "tb/sv/gf180_rtl_constants_pkg.sv"

EXPECTED_WIDTHS = {
    "C1": 24,  # Q2.21 audio word
    "C2": 32,  # u32 phase
    "C3": 32,  # Q16.15 frequency
    "C4": 32,  # Q10.21 MIDI
    "C9": 23,  # U1.22 reciprocal gain word
}

#: DR-0010 (Accepted) Schedule — ratified cycle-budget constants.
EXPECTED_SCHEDULE = {
    "SCHED_COUNTED_CYCLES_PER_SAMPLE": 145,
    "SCHED_PASSES_PER_CLIP": 2,
    "SCHED_SAMPLES_PER_PASS": 176400,
    "SCHED_CLIP_SAMPLE_SLOTS": 352800,
    "SCHED_PASS2_FOLDED_CYCLES_MAX": 4,
    "SCHED_T_MAX_AT_25MHZ": 138,
    "SCHED_BOUND_CLOCK_MHZ": 25,
}


def proposed_payload() -> dict:
    """A pre-ratification-shaped register (DR-0008 Proposed). Test data
    only — proves the refusal path stays armed behind the gate."""
    payload = choices_module.load_choices()
    payload = copy.deepcopy(payload)
    payload["dr_status"] = "Proposed"
    return payload


def proposed_schedule() -> dict:
    """A pre-ratification-shaped schedule register (DR-0010 not yet
    Accepted). Test data only — the landed register is Accepted, so the
    draft shape is a test construct for the refusal path."""
    payload = copy.deepcopy(schedule_module.load_schedule())
    payload["dr_status"] = "Proposed"
    return payload


def accepted_payload() -> dict:
    """A partially-ratified register shape (DR-0008 Accepted, only C1
    accepted). Test data only — the landed register is fully accepted, so
    the remaining entries are demoted here to exercise the mixed case."""
    payload = choices_module.load_choices()
    payload = copy.deepcopy(payload)
    payload["dr_status"] = "Accepted"
    for index, choice in enumerate(payload["choices"]):
        if index == 0:
            assert choice["id"] == "C1"
            choice["status"] = "accepted"
            choice["accepted"] = True
        else:
            choice["status"] = "selected (operator ruling 2026-09-19)"
            choice["accepted"] = False
    return payload


class TestLiveRegisterAdmits(unittest.TestCase):
    """Since the issue #53 ratification (DR-0008 Accepted by reviewed
    merge, 2026-09-21) the live register is accepted end to end."""

    @classmethod
    def setUpClass(cls):
        cls.payload = choices_module.load_choices()
        cls.result = codegen.emit(cls.payload)

    def test_every_entry_is_accepted(self):
        ids = [c["id"] for c in self.payload["choices"]]
        self.assertEqual(ids, ["C%d" % n for n in range(1, 11)])
        self.assertEqual(self.payload["dr_status"], "Accepted")

    def test_package_is_emitted_with_no_refusals(self):
        self.assertIsNotNone(self.result.package_text)
        self.assertEqual(
            self.result.emitted_ids,
            ["C%d" % n for n in range(1, 11)] + [schedule_module.SCHEDULE_ID],
        )
        self.assertEqual(self.result.refusals, [])
        self.assertFalse(self.result.refused_all)

    def test_require_accepted_admits_every_id(self):
        for choice in self.payload["choices"]:
            admitted = choices_module.require_accepted(choice["id"], self.payload)
            self.assertEqual(admitted["id"], choice["id"])

    def test_emitted_widths_match_the_register(self):
        text = self.result.package_text
        for choice_id, width in EXPECTED_WIDTHS.items():
            self.assertIn("%s_WIDTH = %d" % (choice_id, width), text)
        # The selected sine geometry (C5) and its interface bits:
        self.assertIn("C5_N_ENTRIES = 4096", text)
        self.assertIn("C5_ENTRY_WIDTH = 24", text)
        self.assertIn("C5_INDEX_BITS = 12", text)
        self.assertIn("C5_INTERP_BITS = 18", text)
        # C6/C7 carry DR-0008's names as strings, never truncated:
        self.assertIn('C6_SITES = "S1,S2,S3,S4,S5"', text)
        self.assertIn('C7_NEVER_SATURATE_WORDS = "phase,frequency"', text)
        # Interface/policy choices correctly emit no numeric constants:
        self.assertNotIn("C8_", text)
        self.assertNotIn("C10_", text)

    def test_emitted_schedule_matches_the_dr_0010_register(self):
        text = self.result.package_text
        for ident, value in EXPECTED_SCHEDULE.items():
            self.assertIn("localparam int %s = %d;" % (ident, value), text)
        # Strings: the declared (unselected) candidate clocks and the
        # honest none-selected marker.
        self.assertIn('SCHED_CLOCK_CANDIDATES_MHZ = "25,50,100"', text)
        self.assertIn('SCHED_CLOCK_SELECTED = "none"', text)
        # Every schedule constant cites DR-0010's Accepted section.
        self.assertEqual(text.count("// DR-0010 (Accepted) Schedule"), 9)
        # The T parameterization is stated, and T itself is not a constant:
        self.assertIn("C = C_counted + T", text)
        self.assertNotIn("localparam int SCHED_T ", text)
        self.assertNotIn("localparam int SCHED_T =", text)

    def test_landed_package_matches_the_registers(self):
        # The committed package must be exactly what both accepted
        # registers emit (DR-0008 choices + DR-0010 schedule).
        self.assertEqual(
            PACKAGE.read_text(encoding="utf-8"), self.result.package_text
        )


class TestRefusalPathStaysArmed(unittest.TestCase):
    """The refusal path is proven on hand-built payloads — the landed
    registers are post-ratification, so pre-ratification shapes are test
    constructs. Each source (choices, schedule) refuses independently."""

    def test_both_sources_proposed_refuse_everything(self):
        result = codegen.emit(
            proposed_payload(), schedule_payload=proposed_schedule()
        )
        refused = [rid for rid, _ in result.refusals]
        self.assertEqual(
            refused, ["C%d" % n for n in range(1, 11)] + [schedule_module.SCHEDULE_ID]
        )
        self.assertIsNone(result.package_text)
        self.assertTrue(result.refused_all)

    def test_proposed_choices_still_emit_the_accepted_schedule(self):
        # Sources gate independently: a DR-0008 refusal names every choice
        # while the accepted DR-0010 schedule still emits.
        result = codegen.emit(proposed_payload())
        refused = [rid for rid, _ in result.refusals]
        self.assertEqual(refused, ["C%d" % n for n in range(1, 11)])
        self.assertEqual(result.emitted_ids, [schedule_module.SCHEDULE_ID])
        self.assertIn("SCHED_CLIP_SAMPLE_SLOTS = 352800", result.package_text)

    def test_proposed_schedule_refuses_by_name(self):
        result = codegen.emit(schedule_payload=proposed_schedule())
        self.assertIn(schedule_module.SCHEDULE_ID, [rid for rid, _ in result.refusals])
        self.assertNotIn("SCHED_", result.package_text)
        self.assertIn("C1_WIDTH = 24", result.package_text)

    def test_mixed_register_emits_only_accepted_choices(self):
        payload = accepted_payload()
        result = codegen.emit(payload, schedule_payload=proposed_schedule())
        self.assertEqual(result.emitted_ids, ["C1"])
        refused = [rid for rid, _ in result.refusals]
        self.assertEqual(len(refused), 10)  # C2..C10 + the schedule
        self.assertNotIn("C1", refused)

    def test_width_comes_from_the_payload_not_the_module(self):
        payload = accepted_payload()
        payload["choices"][0]["parameters"]["width"] = 20
        text = codegen.emit(payload).package_text
        self.assertIn("C1_WIDTH = 20", text)
        self.assertNotIn("C1_WIDTH = 24", text)

    def test_unrepresentable_parameter_refuses_that_choice(self):
        payload = accepted_payload()
        payload["choices"][0]["parameters"]["sites"] = ["S1", "S2"]
        result = codegen.emit(payload, schedule_payload=proposed_schedule())
        self.assertIsNone(result.package_text)
        self.assertTrue(any(rid == "C1" for rid, _ in result.refusals))
        self.assertIn(
            "explicit mapping decision",
            [reason for rid, reason in result.refusals if rid == "C1"][0],
        )


class TestCliAcceptedGate(unittest.TestCase):
    def test_generate_mode_writes_the_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "pkg.sv"
            proc = subprocess.run(
                [sys.executable, str(TOOL), "--output", str(out)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0)
            self.assertIn("Wrote", proc.stdout)
            for n in range(1, 11):
                self.assertIn("C%d" % n, proc.stdout)
            self.assertIn("SCHED", proc.stdout)
            self.assertEqual(
                out.read_text(encoding="utf-8"),
                PACKAGE.read_text(encoding="utf-8"),
            )

    def test_check_mode_verifies_the_landed_package(self):
        proc = subprocess.run(
            [sys.executable, str(TOOL), "--check"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("CHECK OK", proc.stdout)

    def test_check_mode_fails_on_a_stale_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "stale.sv"
            out.write_text("package gf180_rtl_constants;\nendpackage\n")
            proc = subprocess.run(
                [sys.executable, str(TOOL), "--check", "--output", str(out)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 1)
            self.assertIn("CHECK FAILED", proc.stdout)


if __name__ == "__main__":
    unittest.main()
