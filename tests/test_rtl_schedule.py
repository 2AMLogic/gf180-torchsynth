"""DR-0010 schedule register: refusal gate, consistency, budget math."""

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.fixedpoint import schedule as sched  # noqa: E402


def load_live():
    return sched.load_schedule()


def demoted(payload):
    payload = copy.deepcopy(payload)
    payload["dr_status"] = "Proposed"
    return payload


class TestRegisterLoadsAndIsConsistent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schedule = load_live()

    def test_declares_the_accepted_source(self):
        self.assertEqual(self.schedule["dr"], "DR-0010")
        self.assertEqual(self.schedule["dr_status"], "Accepted")
        self.assertEqual(
            self.schedule["source"],
            "spec/decision-records/0010-one-shot-rtl-microarchitecture.md",
        )
        self.assertIn("Schedule", self.schedule["source_section"])

    def test_every_constant_cites_dr_0010(self):
        for entry in self.schedule["constants"]:
            self.assertIn("DR-0010", entry["source"])
            self.assertIn("Accepted", entry["source"])

    def test_ratified_values(self):
        expected = {
            "counted_cycles_per_sample": 145,
            "passes_per_clip": 2,
            "samples_per_pass": 176400,
            "clip_sample_slots": 352800,
            "pass2_folded_cycles_max": 4,
            "t_max_at_25mhz": 138,
            "bound_clock_mhz": 25,
        }
        for name, value in expected.items():
            self.assertEqual(sched.constant(self.schedule, name), value)
        self.assertEqual(sched.constant(self.schedule, "clock_selected"), "none")
        self.assertEqual(
            sched.constant(self.schedule, "clock_candidates_mhz"), "25,50,100"
        )

    def test_budget_parameterization(self):
        # C = C_counted + T; N_clip = 352,800 x C.
        self.assertEqual(sched.cycles_per_sample(self.schedule, 0), 145)
        self.assertEqual(sched.cycles_per_sample(self.schedule, 100), 245)
        self.assertEqual(sched.cycles_per_sample(self.schedule, 138), 283)
        self.assertEqual(sched.clip_cycles(self.schedule, 100), 86_436_000)
        self.assertEqual(sched.clip_cycles(self.schedule, 138), 99_842_400)


class TestRefusalGate(unittest.TestCase):
    def test_accepted_register_is_consumable(self):
        admitted = sched.require_accepted_schedule()
        self.assertEqual(admitted["dr_status"], "Accepted")

    def test_proposed_register_is_refused(self):
        with self.assertRaises(sched.ScheduleNotAccepted):
            sched.require_accepted_schedule(demoted(load_live()))

    def test_refusal_names_the_record(self):
        try:
            sched.require_accepted_schedule(demoted(load_live()))
        except sched.ScheduleNotAccepted as error:
            self.assertIn("DR-0010", str(error))
            self.assertIn("Proposed", str(error))
        else:
            self.fail("ScheduleNotAccepted not raised")


class TestConsistencyIsEnforcedAtLoad(unittest.TestCase):
    """A self-inconsistent budget register must be a load error, never a
    silent constant source."""

    @staticmethod
    def set_constant(payload, name, value):
        entry = next(c for c in payload["constants"] if c["name"] == name)
        entry["value"] = value

    def mutate(self, payload, mutate_fn):
        payload = copy.deepcopy(payload)
        mutate_fn(payload)
        with self.assertRaises(ValueError):
            sched.validate_schedule(payload)

    def test_slot_identity_enforced(self):
        self.mutate(
            load_live(),
            lambda p: self.set_constant(p, "clip_sample_slots", 352801),
        )

    def test_realtime_rate_enforced(self):
        self.mutate(
            load_live(),
            lambda p: p["refutable_bound"].__setitem__(
                "slots_per_realtime_second", 44100
            ),
        )

    def test_t_max_bound_enforced(self):
        self.mutate(
            load_live(),
            lambda p: self.set_constant(p, "t_max_at_25mhz", 137),
        )

    def test_worked_example_enforced(self):
        self.mutate(
            load_live(),
            lambda p: p["budget_equation"]["worked_example"].__setitem__(
                "n_clip", 86_400_000
            ),
        )

    def test_source_citation_enforced(self):
        self.mutate(
            load_live(),
            lambda p: p.__setitem__("source_section", "Module interfaces"),
        )

    def test_schema_enforced(self):
        self.mutate(load_live(), lambda p: p.__setitem__("schema", "bogus"))

    def test_missing_constant_citation_enforced(self):
        self.mutate(
            load_live(),
            lambda p: p["constants"][0].pop("source"),
        )


if __name__ == "__main__":
    unittest.main()
