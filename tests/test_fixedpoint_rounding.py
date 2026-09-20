"""Deterministic vectors for the canonical integer rounding scalar.

Written before the fixed-point implementation: at the time of the first run
the ``torchsynth_voice.fixedpoint`` package does not exist and the suite must
fail for that reason (test-first checkpoint for issue #49).
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.fixedpoint.rounding import (  # noqa: E402
    RoundingMode,
    div_round,
    div_round_reported,
)


class TestHalfEvenTies(unittest.TestCase):
    """Exact ties-to-even cases, including negative ties."""

    def test_positive_ties(self):
        cases = [
            (1, 2, 0),   # 0.5 -> 0 (even)
            (3, 2, 2),   # 1.5 -> 2 (even)
            (5, 2, 2),   # 2.5 -> 2 (even)
            (7, 2, 4),   # 3.5 -> 4 (even)
            (9, 2, 4),   # 4.5 -> 4 (even)
            (11, 2, 6),  # 5.5 -> 6 (even)
        ]
        for n, d, want in cases:
            self.assertEqual(
                div_round(n, d, RoundingMode.HALF_EVEN), want,
                f"HALF_EVEN {n}/{d}: got {div_round(n, d, RoundingMode.HALF_EVEN)}, want {want}",
            )

    def test_negative_ties_are_symmetric_to_even(self):
        cases = [
            (-1, 2, 0),   # -0.5 -> 0 (even)
            (-3, 2, -2),  # -1.5 -> -2 (even)
            (-5, 2, -2),  # -2.5 -> -2 (even)
            (-7, 2, -4),  # -3.5 -> -4 (even)
            (-9, 2, -4),  # -4.5 -> -4 (even)
        ]
        for n, d, want in cases:
            self.assertEqual(div_round(n, d, RoundingMode.HALF_EVEN), want)

    def test_non_ties_round_to_nearest(self):
        cases = [
            (7, 3, 2),   # 2.333 -> 2
            (8, 3, 3),   # 2.667 -> 3
            (-7, 3, -2),
            (-8, 3, -3),
            (1, 3, 0),
            (2, 3, 1),
        ]
        for n, d, want in cases:
            self.assertEqual(div_round(n, d, RoundingMode.HALF_EVEN), want)


class TestOtherModes(unittest.TestCase):
    def test_half_away_from_zero(self):
        cases = [
            (1, 2, 1),
            (3, 2, 2),
            (5, 2, 3),
            (-1, 2, -1),
            (-3, 2, -2),
            (-5, 2, -3),
            (7, 3, 2),
            (-7, 3, -2),
        ]
        for n, d, want in cases:
            self.assertEqual(div_round(n, d, RoundingMode.HALF_AWAY_FROM_ZERO), want)

    def test_floor(self):
        cases = [
            (5, 2, 2),
            (7, 2, 3),
            (-5, 2, -3),
            (-7, 2, -4),
            (6, 2, 3),
            (-6, 2, -3),
        ]
        for n, d, want in cases:
            self.assertEqual(div_round(n, d, RoundingMode.FLOOR), want)

    def test_trunc(self):
        cases = [
            (5, 2, 2),
            (7, 3, 2),
            (-5, 2, -2),
            (-7, 3, -2),
            (6, 2, 3),
            (-6, 2, -3),
        ]
        for n, d, want in cases:
            self.assertEqual(div_round(n, d, RoundingMode.TRUNC), want)

    def test_exact_division_never_rounds_in_any_mode(self):
        for mode in RoundingMode:
            self.assertEqual(div_round(6, 2, mode), 3)
            self.assertEqual(div_round(-6, 2, mode), -3)
            q, rounded = div_round_reported(6, 2, mode)
            self.assertEqual((q, rounded), (3, False))
            q, rounded = div_round_reported(-6, 2, mode)
            self.assertEqual((q, rounded), (-3, False))

    def test_zero_numerator(self):
        for mode in RoundingMode:
            self.assertEqual(div_round(0, 7, mode), 0)


class TestReportedRounding(unittest.TestCase):
    def test_report_flag_marks_ties_and_non_ties(self):
        q, rounded = div_round_reported(3, 2, RoundingMode.HALF_EVEN)
        self.assertEqual((q, rounded), (2, True))
        q, rounded = div_round_reported(7, 3, RoundingMode.HALF_EVEN)
        self.assertEqual((q, rounded), (2, True))
        q, rounded = div_round_reported(4, 2, RoundingMode.HALF_EVEN)
        self.assertEqual((q, rounded), (2, False))

    def test_invalid_denominator_rejected(self):
        for bad in (0, -1, -7):
            with self.assertRaises(ValueError):
                div_round(5, bad, RoundingMode.HALF_EVEN)

    def test_default_is_half_even(self):
        from torchsynth_voice.fixedpoint.rounding import DEFAULT_MODE

        self.assertEqual(DEFAULT_MODE, RoundingMode.HALF_EVEN)
        self.assertEqual(div_round(5, 2), 2)


if __name__ == "__main__":
    unittest.main()
