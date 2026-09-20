"""Saturation, sticky counters, wrap semantics, accumulators, rescale/mul/add.

Boundary and directed vectors: signs, extrema, overflow, saturation, wrap,
accumulator growth, narrowing/rounding counter emission, and independence
from host integer overflow.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.fixedpoint.counters import (  # noqa: E402
    ROUNDING,
    SATURATION,
    StickyCounters,
)
from torchsynth_voice.fixedpoint.formats import FixedFormat  # noqa: E402
from torchsynth_voice.fixedpoint.ops import (  # noqa: E402
    Accumulator,
    OverflowPolicy,
    add,
    apply_policy,
    mul,
    rescale,
    saturate,
    wrap,
)


Q2_21 = FixedFormat(True, 2, 21)
Q1_22 = FixedFormat(True, 1, 22)
Q1_1 = FixedFormat(True, 1, 1)
Q1_2 = FixedFormat(True, 1, 2)
Q4_0 = FixedFormat(True, 4, 0)
U0_32_MOD = FixedFormat(False, 0, 32, modular=True)


class TestSaturation(unittest.TestCase):
    def test_extrema_both_signs(self):
        counters = StickyCounters()
        self.assertEqual(saturate((1 << 30), Q2_21, counters, "site"), Q2_21.max_int)
        self.assertEqual(saturate(-(1 << 30), Q2_21, counters, "site"), Q2_21.min_int)
        self.assertEqual(counters.count("site", SATURATION), 2)

    def test_in_range_values_pass_through_without_counters(self):
        counters = StickyCounters()
        self.assertEqual(saturate(12345, Q2_21, counters, "site"), 12345)
        self.assertEqual(saturate(-12345, Q2_21, counters, "site"), -12345)
        self.assertEqual(counters.count("site", SATURATION), 0)
        self.assertEqual(len(counters), 0)

    def test_counters_are_sticky_until_explicit_reset(self):
        counters = StickyCounters()
        saturate(1 << 30, Q2_21, counters, "vca_out")
        self.assertEqual(counters.count("vca_out", SATURATION), 1)
        saturate(12345, Q2_21, counters, "vca_out")  # no event: count unchanged
        self.assertEqual(counters.count("vca_out", SATURATION), 1)
        counters.reset("vca_out", SATURATION)
        self.assertEqual(counters.count("vca_out", SATURATION), 0)

    def test_per_site_counters_are_named_and_independent(self):
        counters = StickyCounters()
        saturate(1 << 30, Q2_21, counters, "vco_1.raw")
        saturate(1 << 30, Q2_21, counters, "vco_2.raw")
        saturate(1 << 30, Q2_21, counters, "vco_1.raw")
        self.assertEqual(counters.count("vco_1.raw", SATURATION), 2)
        self.assertEqual(counters.count("vco_2.raw", SATURATION), 1)
        self.assertEqual(counters.sites(), ("vco_1.raw", "vco_2.raw"))

    def test_counter_serialization_is_deterministic(self):
        counters = StickyCounters()
        saturate(1 << 30, Q2_21, counters, "b_site")
        saturate(1 << 30, Q2_21, counters, "a_site")
        once = json.dumps(counters.as_json(), sort_keys=True)
        counters_again = StickyCounters()
        saturate(1 << 30, Q2_21, counters_again, "a_site")
        saturate(1 << 30, Q2_21, counters_again, "b_site")
        self.assertEqual(once, json.dumps(counters_again.as_json(), sort_keys=True))

    def test_saturating_modular_word_is_refused(self):
        with self.assertRaises(ValueError):
            saturate(1 << 40, U0_32_MOD)
        with self.assertRaises(ValueError):
            Accumulator(U0_32_MOD, OverflowPolicy.SATURATE)


class TestWrap(unittest.TestCase):
    def test_wrap_is_modular_two_complement(self):
        self.assertEqual(wrap((1 << 23), Q2_21), -(1 << 23))
        self.assertEqual(wrap((1 << 23) + 3, Q2_21), -(1 << 23) + 3)
        self.assertEqual(wrap(-(1 << 24), Q2_21), 0)
        self.assertEqual(wrap((1 << 32) - 1, U0_32_MOD), (1 << 32) - 1)
        self.assertEqual(wrap(1 << 32, U0_32_MOD), 0)

    def test_wrap_notes_no_error_counters(self):
        counters = StickyCounters()
        wrap(1 << 40, Q2_21)
        apply_policy(1 << 40, Q2_21, OverflowPolicy.WRAP, counters, "site")
        self.assertEqual(len(counters), 0)


class TestRescale(unittest.TestCase):
    def test_widening_is_exact(self):
        self.assertEqual(rescale(5, Q1_2, Q1_22), 5 << 20)
        self.assertEqual(rescale(-5, Q1_2, Q1_22), -(5 << 20))

    def test_narrowing_ties_to_even_vectors(self):
        # Q1.2 -> Q1.1: half-even on exact ties, negative side included.
        self.assertEqual(rescale(3, Q1_2, Q1_1), 2)   # 0.75 -> 1.5 -> 2 (even)
        self.assertEqual(rescale(1, Q1_2, Q1_1), 0)   # 0.25 -> 0.5 -> 0 (even)
        self.assertEqual(rescale(5, Q1_2, Q1_1), 2)   # 1.25 -> 2.5 -> 2 (even)
        self.assertEqual(rescale(-1, Q1_2, Q1_1), 0)  # -0.25 -> -0.5 -> 0
        self.assertEqual(rescale(-3, Q1_2, Q1_1), -2)  # -0.75 -> -1.5 -> -2
        self.assertEqual(rescale(-5, Q1_2, Q1_1), -2)  # -1.25 -> -2.5 -> -2

    def test_narrowing_saturation_and_wrap_paths(self):
        counters_sat = StickyCounters()
        counters_wrap = StickyCounters()
        big = Q4_0.max_int  # real 15 in Q4.0
        # Q1.1 real range is [-2, 1.5]: 15 saturates at the +1.5 rail.
        self.assertEqual(
            rescale(big, Q4_0, Q1_1, counters=counters_sat, site="s"), Q1_1.max_int,
        )
        self.assertEqual(Q1_1.max_int, 3)
        self.assertEqual(counters_sat.count("s", SATURATION), 1)
        wrapped = rescale(
            big, Q4_0, Q1_1, policy=OverflowPolicy.WRAP,
            counters=counters_wrap, site="w",
        )
        # Wrap applies to the *rescaled* integer (Q4.0 15 -> Q1.1 30), i.e.
        # the same real value reduced modularly into the target word.
        self.assertEqual(wrapped, Q1_1.enclose(big << 1))
        self.assertEqual(wrapped, -2)
        self.assertEqual(len(counters_wrap), 0)

    def test_rounding_event_emits_named_counter(self):
        counters = StickyCounters()
        rescale(3, Q1_2, Q1_1, counters=counters, site="S4.vco_1")
        self.assertEqual(counters.count("S4.vco_1", ROUNDING), 1)
        rescale(4, Q1_2, Q1_1, counters=counters, site="S4.vco_1")  # exact
        self.assertEqual(counters.count("S4.vco_1", ROUNDING), 1)

    def test_counters_require_named_site(self):
        with self.assertRaises(ValueError):
            rescale(3, Q1_2, Q1_1, counters=StickyCounters(), site=None)
        with self.assertRaises(ValueError):
            saturate(1 << 30, Q2_21, StickyCounters(), site=None)

    def test_value_outside_source_format_rejected(self):
        with self.assertRaises(ValueError):
            rescale(Q1_2.max_int + 1, Q1_2, Q1_22)


class TestMulAdd(unittest.TestCase):
    def test_mul_single_declared_narrowing_site(self):
        counters = StickyCounters()
        a = 3 << 20  # 1.5 in Q2.21
        b = 3 << 20  # 1.5
        # Exact 2.25 fits Q2.21: no rounding, no saturation.
        self.assertEqual(mul(a, Q2_21, b, Q2_21, Q2_21, counters=counters, site="m"), int(2.25 * (1 << 21)))
        self.assertEqual(len(counters), 0)
        # 1.5 * -1.5 = -2.25
        self.assertEqual(mul(a, Q2_21, -b, Q2_21, Q2_21, site="m"), -int(2.25 * (1 << 21)))

    def test_mul_narrowing_rounds_half_even_with_counter(self):
        counters = StickyCounters()
        # Q1.2 * Q1.2 -> Q1.2: (1 * 1.25): product 1.25 needs rounding at 1.25 -> stays exact? 5*5=25, 25>>2 = 6.25 -> 6 (half-even).
        out = mul(5, Q1_2, 5, Q1_2, Q1_2, counters=counters, site="m")
        self.assertEqual(out, 6)
        self.assertEqual(counters.count("m", ROUNDING), 1)

    def test_mul_saturates_on_overflow_with_sticky_counter(self):
        counters = StickyCounters()
        a = Q2_21.max_int  # ~ +4 - LSB
        out = mul(a, Q2_21, a, Q2_21, Q2_21, counters=counters, site="S4")
        self.assertEqual(out, Q2_21.max_int)
        self.assertEqual(counters.count("S4", SATURATION), 1)

    def test_add_with_both_policies(self):
        counters = StickyCounters()
        a = Q2_21.max_int
        self.assertEqual(add(a, 1, Q2_21, OverflowPolicy.SATURATE, counters, "mix"), Q2_21.max_int)
        self.assertEqual(counters.count("mix", SATURATION), 1)
        self.assertEqual(add(a, 1, Q2_21, OverflowPolicy.WRAP), Q2_21.min_int)

    def test_canonical_equivalence_mul_vs_rescale_of_exact_product(self):
        # Single canonical implementation: mul(a, b) must equal
        # rescale(a * b) from the exact product format.
        for a, b in [(3 << 20, 7 << 19), (-(5 << 20), 3 << 18), (1 << 21, -(1 << 21))]:
            direct = mul(a, Q2_21, b, Q2_21, Q2_21)
            product = a * b  # exact product in Q4.42
            exact_fmt = FixedFormat(True, 4, 42)
            via_rescale = rescale(product, exact_fmt, Q2_21)
            self.assertEqual(direct, via_rescale, f"{a} * {b}")


class TestAccumulator(unittest.TestCase):
    def test_accumulator_growth_until_saturation(self):
        counters = StickyCounters()
        acc = Accumulator(Q2_21, OverflowPolicy.SATURATE, counters)
        step = 1 << 20  # 0.125 in Q2.21
        for i in range(1, 100):
            acc.add(step, "vca.acc")
            if acc.value == Q2_21.max_int:
                break
        self.assertEqual(acc.value, Q2_21.max_int)
        self.assertGreaterEqual(counters.count("vca.acc", SATURATION), 1)
        # Sticky: further adds keep the rail and never decrease the count.
        before = counters.count("vca.acc", SATURATION)
        acc.add(step, "vca.acc")
        self.assertEqual(acc.value, Q2_21.max_int)
        self.assertEqual(counters.count("vca.acc", SATURATION), before + 1)

    def test_accumulator_reset_is_explicit(self):
        acc = Accumulator(Q2_21)
        acc.add(1 << 10, "s")
        acc.reset()
        self.assertEqual(acc.value, 0)

    def test_accumulator_growth_exact_no_float(self):
        acc = Accumulator(Q2_21)
        for _ in range(1000):
            acc.add(1, "s")
        self.assertEqual(acc.value, 1000)

    def test_accumulator_wrap_policy_is_modular(self):
        acc = Accumulator(U0_32_MOD, OverflowPolicy.WRAP)
        acc.add(1, "phase")
        self.assertEqual(acc.value, 1)
        # Operands must be in-format (unsigned); decrement by wrapping max.
        acc.add((1 << 32) - 2, "phase")
        self.assertEqual(acc.value, (1 << 32) - 1)
        acc.add(2, "phase")
        self.assertEqual(acc.value, 1)


class TestHostOverflowIndependence(unittest.TestCase):
    def test_extreme_magnitudes_follow_declared_semantics(self):
        huge = 1 << 200
        self.assertEqual(wrap(huge, Q2_21), Q2_21.enclose(huge))
        self.assertEqual(saturate(huge, Q2_21), Q2_21.max_int)
        self.assertEqual(saturate(-huge, Q2_21), Q2_21.min_int)
        self.assertEqual(wrap(-huge, U0_32_MOD), U0_32_MOD.enclose(-huge))
        counters = StickyCounters()
        wide = FixedFormat(True, 201, 0)  # contains 2^200 exactly
        self.assertEqual(
            mul(huge, wide, huge, wide, Q2_21, counters=counters, site="s"),
            Q2_21.max_int,
        )
        self.assertEqual(counters.count("s", SATURATION), 1)

    def test_accumulator_grows_far_past_host_word_sizes(self):
        acc = Accumulator(FixedFormat(True, 96, 0))
        for _ in range(8):
            acc.add(1 << 90, "s")
        self.assertEqual(acc.value, 8 * (1 << 90))  # 2^93: exact, past 64 bits


if __name__ == "__main__":
    unittest.main()
