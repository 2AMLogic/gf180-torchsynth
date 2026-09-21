"""Choice-config: status wording, vocabulary, and the consumer refusal gate."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.fixedpoint import choices as choices_module  # noqa: E402


ACCEPTED_VIA = "accepted (reviewed merge; 2026-09-21)"


class TestChoiceConfigStatus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = choices_module.load_choices()

    def test_every_choice_carries_the_accepted_status(self):
        for choice in self.payload["choices"]:
            self.assertEqual(
                choice["status"],
                "accepted",
                f"choice {choice['id']} status must be the bare accepted token",
            )
            self.assertEqual(choice["accepted_via"], ACCEPTED_VIA)

    def test_every_choice_is_accepted_under_an_accepted_dr(self):
        for choice in self.payload["choices"]:
            self.assertIs(choice["accepted"], True, f"choice {choice['id']}")
        self.assertEqual(self.payload["dr_status"], "Accepted")
        self.assertEqual(
            set(choices_module.accepted_choices(self.payload)),
            {f"C{i}" for i in range(1, 11)},
        )

    def test_status_vocabulary_follows_dr_0008_section_12(self):
        vocabulary = self.payload["status_vocabulary"]["register_terms"]
        self.assertEqual(vocabulary, ["proposed", "selected", "accepted", "rejected"])
        for choice in self.payload["choices"]:
            self.assertIn(choice["register_term"], vocabulary)
            self.assertEqual(choice["register_term"], "accepted")

    def test_register_matches_dr_0008_ids(self):
        ids = [choice["id"] for choice in self.payload["choices"]]
        self.assertEqual(ids, [f"C{i}" for i in range(1, 11)])

    def test_c9_carries_the_measured_reciprocal_parameters(self):
        c9 = choices_module.get("C9", self.payload)
        self.assertEqual(c9["parameters"]["gain_word"], "U1.22")
        self.assertEqual(c9["parameters"]["width"], 23)
        self.assertEqual(c9["parameters"]["int_bits"], 1)
        self.assertEqual(c9["parameters"]["frac_bits"], 22)
        self.assertEqual(c9["parameters"]["reciprocal_frac_bits"], 22)
        self.assertEqual(c9["parameters"]["rounding_mode"], "half_even")
        self.assertEqual(c9["parameters"]["application_site"], "S5")
        self.assertTrue(c9["instantiable"])

    def test_refusal_gate_admits_every_choice_today(self):
        for choice in self.payload["choices"]:
            admitted = choices_module.require_accepted(choice["id"], self.payload)
            self.assertEqual(admitted["id"], choice["id"])

    def test_refusal_gate_still_refuses_a_proposed_dr_payload(self):
        # The gate is structural, not a blanket admit: a hand-built copy whose
        # DR status is Proposed refuses every entry even with accepted-looking
        # statuses — the consumer rule binds the register and the DR together.
        payload = json.loads(json.dumps(self.payload))
        payload["dr_status"] = "Proposed"
        for choice in payload["choices"]:
            with self.assertRaises(choices_module.ChoiceNotAccepted):
                choices_module.require_accepted(choice["id"], payload)
        self.assertEqual(choices_module.accepted_choices(payload), {})

    def test_refusal_gate_refuses_a_non_accepted_entry_in_an_accepted_dr(self):
        payload = json.loads(json.dumps(self.payload))
        payload["choices"][0]["status"] = "selected (operator ruling 2026-09-19)"
        with self.assertRaises(choices_module.ChoiceNotAccepted):
            choices_module.require_accepted("C1", payload)

    def test_validator_refuses_accepted_choice_while_dr_is_proposed(self):
        payload = json.loads(json.dumps(self.payload))
        payload["dr_status"] = "Proposed"
        payload["choices"][2]["status"] = "accepted"
        payload["choices"][2]["accepted"] = True
        with self.assertRaises(ValueError):
            choices_module.validate_choices(payload)

    def test_validator_refuses_accepted_flag_without_accepted_token(self):
        payload = json.loads(json.dumps(self.payload))
        payload["choices"][2]["status"] = "selected (operator ruling 2026-09-19)"
        payload["choices"][2]["accepted"] = True
        with self.assertRaises(ValueError):
            choices_module.validate_choices(payload)

    def test_validator_refuses_accepted_token_without_accepted_flag(self):
        payload = json.loads(json.dumps(self.payload))
        payload["choices"][2]["accepted"] = False
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
        self.assertEqual(c5["status"], "accepted")
        with self.assertRaises(KeyError):
            choices_module.get("C99", self.payload)

    def test_payload_serialization_is_stable(self):
        once = json.dumps(self.payload, sort_keys=True)
        revived = choices_module.load_choices()
        self.assertEqual(once, json.dumps(revived, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
