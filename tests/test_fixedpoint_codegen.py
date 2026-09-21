"""Refusal-gated RTL constants emitter over the DR-0008 choice register."""

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

TOOL = ROOT / "tools/generate_rtl_constants.py"
PACKAGE = ROOT / "tb/sv/gf180_rtl_constants_pkg.sv"

EXPECTED_WIDTHS = {
    "C1": 24,  # Q2.21 audio word
    "C2": 32,  # u32 phase
    "C3": 32,  # Q16.15 frequency
    "C4": 32,  # Q10.21 MIDI
    "C9": 23,  # U1.22 reciprocal gain word
}


def proposed_payload() -> dict:
    """A pre-ratification-shaped register (DR-0008 Proposed). Test data
    only — proves the refusal path stays armed behind the gate."""
    payload = choices_module.load_choices()
    payload = copy.deepcopy(payload)
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
            self.result.emitted_ids, ["C%d" % n for n in range(1, 11)]
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

    def test_landed_package_matches_the_register(self):
        # The committed package must be exactly what the register emits.
        self.assertEqual(
            PACKAGE.read_text(encoding="utf-8"), self.result.package_text
        )


class TestRefusalPathStaysArmed(unittest.TestCase):
    """The refusal path is proven on hand-built payloads — the landed
    register is post-ratification, so the pre-ratification shape is a
    test construct."""

    def test_proposed_register_refuses_every_choice(self):
        payload = proposed_payload()
        result = codegen.emit(payload)
        refused = [rid for rid, _ in result.refusals]
        self.assertEqual(refused, ["C%d" % n for n in range(1, 11)])
        self.assertIsNone(result.package_text)
        self.assertTrue(result.refused_all)

    def test_mixed_register_emits_only_accepted_choices(self):
        payload = accepted_payload()
        result = codegen.emit(payload)
        self.assertEqual(result.emitted_ids, ["C1"])
        refused = [rid for rid, _ in result.refusals]
        self.assertEqual(len(refused), 9)
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
        result = codegen.emit(payload)
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
