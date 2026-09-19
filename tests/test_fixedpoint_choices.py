"""Choice-config: status wording, vocabulary, and the consumer refusal gate."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.fixedpoint import choices as choices_module  # noqa: E402


PENDING_STATUS = "selected (operator ruling 2026-09-19); pending ratification"


class TestChoiceConfigStatus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = choices_module.load_choices()

    def test_every_choice_carries_the_pending_status_wording(self):
        for choice in self.payload["choices"]:
            self.assertEqual(
                choice["status"],
                PENDING_STATUS,
                f"choice {choice['id']} status must be exactly the "
                "selected-pending-ratification wording",
            )

    def test_no_choice_is_accepted(self):
        for choice in self.payload["choices"]:
            self.assertIsNot(choice["accepted"], True, f"choice {choice['id']}")
        self.assertEqual(self.payload["dr_status"], "Proposed")
        self.assertEqual(choices_module.accepted_choices(self.payload), {})

    def test_status_vocabulary_follows_dr_0008_section_12(self):
        vocabulary = self.payload["status_vocabulary"]["register_terms"]
        self.assertEqual(vocabulary, ["proposed", "selected", "accepted", "rejected"])
        for choice in self.payload["choices"]:
            self.assertIn(choice["register_term"], vocabulary)
            self.assertEqual(choice["register_term"], "selected")

    def test_register_matches_dr_0008_ids(self):
        ids = [choice["id"] for choice in self.payload["choices"]]
        self.assertEqual(ids, [f"C{i}" for i in range(1, 11)])

    def test_refusal_gate_rejects_every_choice_today(self):
        for choice in self.payload["choices"]:
            with self.assertRaises(choices_module.ChoiceNotAccepted):
                choices_module.require_accepted(choice["id"], self.payload)

    def test_refusal_gate_would_admit_a_contract_accepted_choice(self):
        # The gate is structural, not a blanket refusal: show the admitted
        # path exists by flipping one entry's status on a copy.
        payload = json.loads(json.dumps(self.payload))
        payload["dr_status"] = "Accepted"
        payload["choices"][0]["status"] = "accepted"
        payload["choices"][0]["accepted"] = True
        admitted = choices_module.require_accepted("C1", payload)
        self.assertEqual(admitted["id"], "C1")
        # ...while a non-accepted entry in an Accepted DR still refuses.
        with self.assertRaises(choices_module.ChoiceNotAccepted):
            choices_module.require_accepted("C2", payload)

    def test_validator_refuses_accepted_choice_while_dr_is_proposed(self):
        payload = json.loads(json.dumps(self.payload))
        payload["choices"][2]["accepted"] = True
        with self.assertRaises(ValueError):
            choices_module.validate_choices(payload)

    def test_validator_refuses_out_of_vocabulary_register_term(self):
        payload = json.loads(json.dumps(self.payload))
        payload["choices"][2]["register_term"] = "ratified"
        with self.assertRaises(ValueError):
            choices_module.validate_choices(payload)

    def test_validator_refuses_unknown_schema_and_duplicates(self):
        with self.assertRaises(ValueError):
            choices_module.validate_choices({"schema": "other/v9", "choices": []})
        payload = json.loads(json.dumps(self.payload))
        payload["choices"].append(dict(payload["choices"][0]))
        with self.assertRaises(ValueError):
            choices_module.validate_choices(payload)

    def test_get_returns_data_without_acceptance_claim(self):
        c5 = choices_module.get("C5", self.payload)
        self.assertEqual(c5["parameters"]["n_entries"], 4096)
        self.assertEqual(c5["parameters"]["entry_width"], 24)
        self.assertEqual(c5["parameters"]["index_bits"], 12)
        self.assertEqual(c5["parameters"]["interp_bits"], 18)
        self.assertEqual(c5["status"], PENDING_STATUS)
        with self.assertRaises(KeyError):
            choices_module.get("C99", self.payload)

    def test_payload_serialization_is_stable(self):
        once = json.dumps(self.payload, sort_keys=True)
        revived = choices_module.load_choices()
        self.assertEqual(once, json.dumps(revived, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
