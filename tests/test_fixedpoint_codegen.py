"""Refusal-gated RTL constants emitter over the DR-0008 choice register."""

import copy
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.fixedpoint import choices as choices_module  # noqa: E402
from torchsynth_voice.fixedpoint import codegen  # noqa: E402

TOOL = ROOT / "tools/generate_rtl_constants.py"


def accepted_payload() -> dict:
    """A post-ratification-shaped register (DR-0008 Accepted, one accepted
    choice). Test data only — the landed register file is never modified."""
    payload = choices_module.load_choices()
    payload = copy.deepcopy(payload)
    payload["dr_status"] = "Accepted"
    choice = payload["choices"][0]
    assert choice["id"] == "C1"
    choice["status"] = "accepted"
    choice["accepted"] = True
    return payload


class TestRefusalToday(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = choices_module.load_choices()
        cls.result = codegen.emit(cls.payload)

    def test_every_entry_is_refused_while_dr0008_is_proposed(self):
        ids = [c["id"] for c in self.payload["choices"]]
        self.assertEqual(ids, ["C%d" % n for n in range(1, 11)])
        refused = [rid for rid, _ in self.result.refusals]
        self.assertEqual(refused, ids)

    def test_no_package_is_emitted(self):
        self.assertIsNone(self.result.package_text)
        self.assertEqual(self.result.emitted_ids, [])
        self.assertTrue(self.result.refused_all)

    def test_require_accepted_raises_for_every_id(self):
        for choice in self.payload["choices"]:
            with self.assertRaises(choices_module.ChoiceNotAccepted):
                choices_module.require_accepted(choice["id"], self.payload)

    def test_refusal_names_the_status_and_the_gate(self):
        for choice_id, reason in self.result.refusals:
            self.assertIn("not accepted", reason)
            self.assertIn("refusing", reason)


class TestFutureAdmitPath(unittest.TestCase):
    """Proves the emitter is alive behind the gate without touching the
    landed register: a hand-built post-ratification payload is emitted with
    widths taken from the payload, never hardcoded."""

    def test_accepted_choice_is_emitted_with_payload_width(self):
        result = codegen.emit(accepted_payload())
        self.assertEqual(result.emitted_ids, ["C1"])
        self.assertNotIn("C1", [rid for rid, _ in result.refusals])
        text = result.package_text
        self.assertIn("package %s;" % codegen.PACKAGE_NAME, text)
        width = accepted_payload()["choices"][0]["parameters"]["width"]
        self.assertIn("C1_WIDTH = %d" % width, text)

    def test_width_comes_from_the_payload_not_the_module(self):
        payload = accepted_payload()
        payload["choices"][0]["parameters"]["width"] = 20
        text = codegen.emit(payload).package_text
        self.assertIn("C1_WIDTH = 20", text)
        self.assertNotIn("C1_WIDTH = 24", text)

    def test_other_choices_still_refuse_in_a_mixed_register(self):
        payload = accepted_payload()
        result = codegen.emit(payload)
        self.assertEqual(result.emitted_ids, ["C1"])
        refused = [rid for rid, _ in result.refusals]
        self.assertEqual(len(refused), 9)
        self.assertNotIn("C1", refused)

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


class TestCliRefusalGate(unittest.TestCase):
    def test_generate_mode_refuses_with_exit_2_today(self):
        proc = subprocess.run(
            [sys.executable, str(TOOL)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("REFUSED", proc.stdout)
        for n in range(1, 11):
            self.assertIn("C%d" % n, proc.stdout)

    def test_check_mode_verifies_the_refusal_gate(self):
        proc = subprocess.run(
            [sys.executable, str(TOOL), "--check"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("CHECK OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
