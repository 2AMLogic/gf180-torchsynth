"""Wrapping modular phase arithmetic: wrap-around, injection, exact drift."""

import sys
import unittest
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.fixedpoint.phase import PhaseAccumulator  # noqa: E402
from torchsynth_voice.fixedpoint.rounding import RoundingMode  # noqa: E402


class TestWrapAround(unittest.TestCase):
    def test_wrap_is_modular_never_saturating(self):
        acc = PhaseAccumulator(32)
        self.assertEqual(acc.step(1), 1)
        self.assertEqual(acc.step(-1), 0)
        self.assertEqual(acc.step((1 << 32)), 0)
        self.assertEqual(acc.step((1 << 32) + 7), 7)
        # value is 7; 7 - 2^32 - 7 = -2^32 wraps to 0.
        self.assertEqual(acc.step(-(1 << 32) - 7), 0)

    def test_wraparound_crossing_is_exact(self):
        acc = PhaseAccumulator(32)
        acc.step((1 << 32) - 1)
        self.assertEqual(acc.value, (1 << 32) - 1)
        acc.step(2)
        self.assertEqual(acc.value, 1)
        self.assertEqual(acc.turns, Fraction(1, 1 << 32))

    def test_single_bit_and_small_widths(self):
        acc = PhaseAccumulator(4)
        self.assertEqual(acc.modulus, 16)
        self.assertEqual(acc.units_per_turn, 16)
        acc.step(15)
        self.assertEqual(acc.value, 15)
        acc.step(1)
        self.assertEqual(acc.value, 0)

    def test_independent_instances_do_not_share_state(self):
        a = PhaseAccumulator(32)
        b = PhaseAccumulator(32)
        a.step(12345)
        self.assertEqual(b.value, 0)


class TestInjection(unittest.TestCase):
    def test_initial_phase_injection_half_even_ties(self):
        # 2^32 * 1/2^33 = 0.5 -> half-even -> 0
        acc = PhaseAccumulator(32)
        self.assertEqual(acc.inject_turns(Fraction(1, 1 << 33)), 0)
        # 2^32 * 3/2^33 = 1.5 -> half-even -> 2
        acc = PhaseAccumulator(32)
        self.assertEqual(acc.inject_turns(Fraction(3, 1 << 33)), 2)
        # Negative initial phase wraps into the unsigned domain.
        acc = PhaseAccumulator(32)
        acc.inject_turns(Fraction(-1, 2))  # -0.5 turn
        self.assertEqual(acc.value, (1 << 32) // 2)

    def test_injection_respects_explicit_mode(self):
        acc = PhaseAccumulator(32)
        acc.inject_turns(Fraction(1, 1 << 33), mode=RoundingMode.HALF_AWAY_FROM_ZERO)
        self.assertEqual(acc.value, 1)

    def test_quarter_turn_injection(self):
        acc = PhaseAccumulator(32)
        acc.inject_turns(Fraction(1, 4))
        self.assertEqual(acc.value, 1 << 30)


class TestConstantFrequencyDrift(unittest.TestCase):
    def test_increment_site_is_half_even_of_exact_value(self):
        acc = PhaseAccumulator(32)
        f_over_fs = Fraction(1000, 44100)
        k = acc.increment_from_frequency(f_over_fs)
        exact = Fraction(f_over_fs.numerator * (1 << 32), f_over_fs.denominator)
        self.assertLessEqual(abs(Fraction(k) - exact), Fraction(1, 2))

    def test_drift_bound_over_a_whole_clip(self):
        # M3-style intrinsic bound: |dphi| <= steps * 2^-33 turns for a
        # 32-bit accumulator (half-even increment quantization, 0.5 LSB).
        width = 32
        steps = 176400
        for f_over_fs in (
            Fraction(440, 44100),
            Fraction(12543900, 44100 * 1000),  # 12.5439 kHz -> wraps many times
            Fraction(50174, 44100),            # above-Nyquist class
            Fraction(1, 44100),
        ):
            acc = PhaseAccumulator(width)
            trace = acc.run_constant_frequency(f_over_fs, steps)
            final = Fraction(trace[-1], 1 << width)
            exact = (f_over_fs * steps) % 1
            drift = abs(final - exact)
            # Modular distance: fold into [-1/2, 1/2].
            if drift > Fraction(1, 2):
                drift = 1 - drift
            self.assertLessEqual(
                drift,
                steps * Fraction(1, 1 << (width + 1)),
                f"drift {drift} for f/fs {f_over_fs}",
            )

    def test_drift_of_injected_plus_incremented_phase(self):
        width = 32
        steps = 176400
        initial = Fraction(-91, 256)  # between -1/4 and -1/2 turn
        f_over_fs = Fraction(1000, 44100)
        acc = PhaseAccumulator(width)
        acc.inject_turns(initial)
        trace = acc.run_constant_frequency(f_over_fs, steps)
        final = Fraction(trace[-1], 1 << width)
        exact = (initial + f_over_fs * steps) % 1
        drift = abs(final - exact)
        if drift > Fraction(1, 2):
            drift = 1 - drift
        self.assertLessEqual(drift, (steps + 1) * Fraction(1, 1 << (width + 1)))

    def test_other_widths_satisfy_the_same_bound_shape(self):
        for width in (16, 24, 40):
            acc = PhaseAccumulator(width)
            f_over_fs = Fraction(440, 44100)
            steps = 1000
            trace = acc.run_constant_frequency(f_over_fs, steps)
            final = Fraction(trace[-1], 1 << width)
            exact = (f_over_fs * steps) % 1
            drift = abs(final - exact)
            if drift > Fraction(1, 2):
                drift = 1 - drift
            self.assertLessEqual(drift, steps * Fraction(1, 1 << (width + 1)))


if __name__ == "__main__":
    unittest.main()
