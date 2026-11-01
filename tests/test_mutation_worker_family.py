"""Shared signal/timing worker glue (#429). Stdlib-only; renders nothing.

Establishes only the label rule, the case-process control ordering and the
per-family binding. Release-image byte identity of the mutation reports is
not exercised here.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "env/release-era"))

import mutation_worker_family as family  # noqa: E402
import mutation_worker_signal as signal_worker  # noqa: E402
import mutation_worker_timing as timing_worker  # noqa: E402


class FamilyTests(unittest.TestCase):
    def test_file_label_rules(self):
        self.assertEqual(family.file_label("a:b c"), "a__b c")
        self.assertEqual(family.file_label("a:b c", True), "a__b_c")

    def test_family_bindings(self):
        self.assertTrue(signal_worker.SignalAttempt.label_spaces)
        self.assertFalse(timing_worker.TimingAttempt.label_spaces)
        self.assertIs(signal_worker.SignalAttempt.session_class, signal_worker.SignalMutationSession)
        self.assertIs(timing_worker.TimingAttempt.session_class, timing_worker.TimingMutationSession)
        self.assertIs(signal_worker.SignalHarness.schedule_error, signal_worker.SignalScheduleError)
        self.assertIs(timing_worker.TimingHarness.schedule_error, timing_worker.TimingScheduleError)

    def test_case_process_order_and_errors(self):
        calls = []

        class Attempt:
            def __init__(self, name, harness, schedule):
                self.name, self.schedule = name, schedule

            def run(self, output):
                calls.append(self.name)
                return self.name

        class Harness:
            case = {"id": "c"}

        class Err(ValueError):
            pass

        def validate(schedule, *args):
            calls.append("v:" + schedule["campaign"]["attempt"])

        schedules = {n: {"campaign": {"attempt": n}} for n in ("empty", "sham", "f1")}
        records = family.run_case_process(
            Harness(), schedules, None, {}, [], "sha", validate, Attempt, Err
        )
        self.assertEqual(list(records), ["plain", "empty", "sham", "f1", "clean_rerun"])
        self.assertEqual(calls[:3], ["v:empty", "v:sham", "v:f1"])
        with self.assertRaisesRegex(Err, "controls must precede"):
            family.run_case_process(
                Harness(), {"sham": schedules["sham"], "empty": schedules["empty"]},
                None, {}, [], "sha", validate, Attempt, Err,
            )
        bad = {"empty": schedules["empty"], "sham": {"campaign": {"attempt": "x"}}}
        with self.assertRaisesRegex(Err, "schedule label mismatch: sham"):
            family.run_case_process(Harness(), bad, None, {}, [], "sha", validate, Attempt, Err)


if __name__ == "__main__":
    unittest.main()
