"""Issue #52 fixed-path normalization replay: branch coverage and receipt.

Offline stdlib tests (no Torch). The directed Q2.21 case grid covers
below/at/above-one, ties, extrema, silence, late peaks, and the two release
anchors quantized onto the Q2.21 grid; the float reference is the landed
``float-mix-v1`` normalization semantics on the exact binary32
dequantization of the same clip. The committed receipt
``sim/reference/normalization-reciprocal-v1.json`` is
candidate-pending-ratification evidence input for DR-0003 acceptance --
never the acceptance itself: DR-0003 and DR-0008 stay Proposed, and the
limiter/AGC substitutes DR-0003 forbids appear only as negative controls
that must fail. The full sweep is executed by
``tools/measure_normalization_reciprocal.py``; these tests recompute a
bounded subset and bind the rest to the committed receipt by digest.
"""

import json
import sys
import unittest
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import float_mix as fm  # noqa: E402
from torchsynth_voice import normalization_replay as nr  # noqa: E402

RECEIPT_PATH = ROOT / "sim/reference/normalization-reciprocal-v1.json"
RECEIPT = json.loads(RECEIPT_PATH.read_bytes())


def _all_cases():
    cases = dict(nr.directed_cases())
    for name, (clip, _offset) in nr.anchored_cases().items():
        cases[name] = clip
    return cases


class BranchSemantics(unittest.TestCase):
    """Strict peak > 1 on the Q2.21 grid: below/at/above, ties, extrema."""

    def test_directed_case_grid_covers_declared_classes(self):
        cases = _all_cases()
        self.assertEqual(
            sorted(cases),
            [
                "fixed:above-one-min",
                "fixed:anchor-loud",
                "fixed:anchor-quiet",
                "fixed:below-one-max",
                "fixed:extremum",
                "fixed:late-peak",
                "fixed:silence",
                "fixed:tie",
                "fixed:tied-max",
            ],
        )
        peaks = {name: nr.fixed_peak(clip)[0] for name, clip in cases.items()}
        self.assertEqual(peaks["fixed:above-one-min"], nr.UNITY_INT + 1)
        self.assertEqual(peaks["fixed:tie"], nr.UNITY_INT)
        self.assertEqual(peaks["fixed:below-one-max"], nr.UNITY_INT - 1)
        self.assertEqual(peaks["fixed:extremum"], nr.AUDIO_FORMAT.max_int)
        self.assertEqual(peaks["fixed:silence"], 0)
        self.assertTrue(nr.branch_decide(peaks["fixed:above-one-min"]))
        self.assertTrue(nr.branch_decide(peaks["fixed:extremum"]))
        for name in (
            "fixed:tie",
            "fixed:below-one-max",
            "fixed:silence",
            "fixed:anchor-quiet",
        ):
            self.assertFalse(nr.branch_decide(peaks[name]), name)

    def test_bypass_preserves_input_bytes(self):
        for name in (
            "fixed:tie",
            "fixed:below-one-max",
            "fixed:silence",
            "fixed:anchor-quiet",
        ):
            clip = _all_cases()[name]
            replay = nr.replay_fixed(clip)
            self.assertEqual(replay["branch"], "bypass", name)
            self.assertEqual(replay["output"], clip, name)
            self.assertEqual(replay["gain_word"], None, name)
            self.assertEqual(replay["gain_real"], 1.0, name)
            self.assertEqual(replay["saturation_total"], 0, name)

    def test_divide_rows_use_unity_free_gain_words_and_never_saturate(self):
        for name in (
            "fixed:above-one-min",
            "fixed:extremum",
            "fixed:late-peak",
            "fixed:tied-max",
            "fixed:anchor-loud",
        ):
            clip = _all_cases()[name]
            replay = nr.replay_fixed(clip, nr.METHOD_RECIPROCAL, 22)
            self.assertEqual(replay["branch"], "divide", name)
            self.assertEqual(replay["gain_format"], "U1.22", name)
            self.assertEqual(replay["saturation_total"], 0, name)
            # output magnitude never exceeds the unity grid point
            self.assertTrue(all(abs(v) <= nr.UNITY_INT for v in replay["output"]), name)

    def test_earliest_index_wins_and_latest_control_disagrees(self):
        clip = nr.clip_tied_max(nr.UNITY_INT + 777)
        peak, index = nr.fixed_peak(clip)
        self.assertEqual(index, 9)
        _, latest_index = nr.fixed_peak(clip, tie="latest")
        self.assertEqual(latest_index, 40000)
        self.assertNotEqual(index, latest_index)

    def test_late_peak_records_the_last_sample(self):
        clip = nr.clip_with_peak(nr.UNITY_INT + 12345, nr.CLIP_SAMPLES - 1)
        peak, index = nr.fixed_peak(clip)
        self.assertEqual(index, nr.CLIP_SAMPLES - 1)
        self.assertEqual(peak, nr.UNITY_INT + 12345)


class ReciprocalWord(unittest.TestCase):
    """The declared-precision gain word (DR-0008 C9, site S5)."""

    def test_minimal_divisor_rounds_to_exactly_unity_at_narrow_widths(self):
        # At F=16 the minimal divisor's reciprocal rounds up to exactly 2^F:
        # the gain word says 1.0 even though the branch divides.
        gain = nr.reciprocal_word(nr.UNITY_INT + 1, 16)
        self.assertEqual(gain, 1 << 16)
        self.assertEqual(
            float(Fraction(gain, 1 << 16)),
            1.0,
        )

    def test_gain_word_fits_the_u1f_format_on_every_candidate_width(self):
        for frac_bits in nr.RECIPROCAL_FRAC_WIDTHS:
            fmt = nr.gain_format(frac_bits)
            self.assertEqual(fmt.identity, "U1.%d" % frac_bits)
            gain = nr.reciprocal_word(nr.AUDIO_FORMAT.max_int, frac_bits)
            self.assertTrue(fmt.contains(gain), frac_bits)

    def test_reciprocal_word_refuses_the_bypass_branch(self):
        with self.assertRaises(nr.NormalizationReplayError):
            nr.reciprocal_word(nr.UNITY_INT, 22)
        with self.assertRaises(nr.NormalizationReplayError):
            nr.reciprocal_word(0, 22)

    def test_replay_rejects_mismatched_method_arguments(self):
        clip = _all_cases()["fixed:tie"]
        with self.assertRaises(nr.NormalizationReplayError):
            nr.replay_fixed(clip, nr.METHOD_RECIPROCAL, None)
        with self.assertRaises(nr.NormalizationReplayError):
            nr.replay_fixed(clip, nr.METHOD_DIRECT, 22)
        with self.assertRaises(nr.NormalizationReplayError):
            nr.replay_fixed(clip, "limiter")

    def test_determinism_of_repeated_replay(self):
        clip = _all_cases()["fixed:extremum"]
        first = nr.replay_fixed(clip, nr.METHOD_RECIPROCAL, 22)
        second = nr.replay_fixed(clip, nr.METHOD_RECIPROCAL, 22)
        self.assertEqual(first["output"], second["output"])
        self.assertEqual(first["gain_word"], second["gain_word"])
        self.assertEqual(nr.gain_word_rows(first["peak"], 22), nr.gain_word_rows(second["peak"], 22))


class AnchorCases(unittest.TestCase):
    """The release anchors quantized onto the Q2.21 grid, one per branch."""

    def test_anchor_branches_match_the_declared_scales(self):
        anchored = nr.anchored_cases()
        quiet_peak, _ = nr.fixed_peak(anchored["fixed:anchor-quiet"][0])
        loud_peak, _ = nr.fixed_peak(anchored["fixed:anchor-loud"][0])
        self.assertFalse(nr.branch_decide(quiet_peak))
        self.assertTrue(nr.branch_decide(loud_peak))

    def test_anchor_quantization_offsets_are_exact_and_reported(self):
        quiet_int, quiet_offset = nr.anchor_peak_int(nr.ANCHOR_QUIET)
        loud_int, loud_offset = nr.anchor_peak_int(nr.ANCHOR_LOUD)
        self.assertEqual(quiet_int, 1655852)
        self.assertEqual(loud_int, 8279259)
        self.assertEqual(
            quiet_offset,
            Fraction(-915379, 5120000000000),
        )
        self.assertEqual(loud_offset, Fraction(253, 20480000000000))
        # the loud anchor lands within one part in 1e10 of the DR-0006 record
        self.assertLess(abs(float(loud_offset)), 1e-10)

    def test_float_reference_gain_matches_the_float_diagnostic_rule(self):
        clip, _ = nr.anchored_cases()["fixed:anchor-loud"]
        reference = nr.reference_float(clip)
        self.assertTrue(reference["branch"])
        self.assertEqual(reference["gain"], fm.f32(1.0 / reference["peak"]))


class FloatReferenceAgreement(unittest.TestCase):
    """Branch agreement with the landed float-mix semantics on shared clips."""

    def test_every_directed_case_agrees_on_the_branch(self):
        for name, clip in sorted(_all_cases().items()):
            replay = nr.replay_fixed(clip)
            reference = nr.reference_float(clip)
            expected = "divide" if reference["branch"] else "bypass"
            self.assertEqual(
                replay["branch"],
                expected,
                name + " branch disagrees with the float reference",
            )
            self.assertEqual(
                replay["peak"],
                int(round(reference["peak"] * nr.AUDIO_SCALE)),
                name + " peak disagrees on the shared Q2.21 grid",
            )


class CommittedReceipt(unittest.TestCase):
    """Bind the measurement to the committed candidate-pending receipt."""

    def test_receipt_schema_and_status(self):
        self.assertEqual(
            RECEIPT["schema"], "gf180-torchsynth/normalization-reciprocal-v1"
        )
        self.assertEqual(RECEIPT["schema_version"], 1)
        self.assertEqual(RECEIPT["status"], "candidate-pending-ratification")
        self.assertEqual(RECEIPT["issue"], 52)
        self.assertEqual(RECEIPT["producer"]["dr_0003_status"], "Proposed")
        self.assertEqual(RECEIPT["producer"]["dr_0008_status"], "Proposed")
        self.assertTrue(
            any("operator" in line for line in RECEIPT["disclaimers"]),
            "the receipt must state that acceptance belongs to the operator",
        )

    def test_receipt_body_digest_is_stable(self):
        body = {
            key: value
            for key, value in RECEIPT.items()
            if key != "generated_utc" and key != "receipt_sha256"
        }
        self.assertEqual(nr.receipt_sha256(body), RECEIPT["receipt_sha256"])

    def test_coverage_block_matches_direct_recomputation(self):
        for name, clip in sorted(_all_cases().items()):
            peak, index = nr.fixed_peak(clip)
            recorded = RECEIPT["coverage"][name]
            self.assertEqual(recorded["peak_int"], peak, name)
            self.assertEqual(recorded["peak_index"], index, name)
            self.assertEqual(
                recorded["branch"],
                "divide" if nr.branch_decide(peak) else "bypass",
                name,
            )

    def test_recommended_precision_is_the_measured_knee(self):
        decision = RECEIPT["decision"]
        self.assertEqual(decision["status"], "candidate-pending-ratification")
        self.assertEqual(decision["recommended_reciprocal_frac_bits"], 22)
        self.assertEqual(decision["recommended_gain_word"], "unsigned U1.22 (width 23 bits)")
        band_check = decision["band_check"]
        # the narrowest candidate fails the draft band outright
        self.assertFalse(band_check["reciprocal-multiply F=12"]["meets_draft_band_with_margin"])
        # intermediate widths fit the band but stay gain-word dominated
        for label in ("reciprocal-multiply F=14", "reciprocal-multiply F=16", "reciprocal-multiply F=18", "reciprocal-multiply F=20", "reciprocal-multiply F=21"):
            self.assertTrue(band_check[label]["meets_draft_band_with_margin"], label)
            self.assertFalse(band_check[label]["within_half_lsb_of_floor"], label)
        # the knee and beyond: band met and the output word dominates
        for label in ("reciprocal-multiply F=22", "reciprocal-multiply F=24", "direct-division"):
            self.assertTrue(band_check[label]["meets_draft_band_with_margin"], label)
            self.assertTrue(band_check[label]["within_half_lsb_of_floor"], label)

    def test_error_is_monotonically_non_increasing_in_gain_width(self):
        band_check = RECEIPT["decision"]["band_check"]
        widths = sorted(nr.RECIPROCAL_FRAC_WIDTHS)
        measured = [
            Fraction(
                band_check["reciprocal-multiply F=%d" % frac_bits][
                    "measured_worst_case_exact"
                ]
            )
            for frac_bits in widths
        ]
        for narrower, wider in zip(measured, measured[1:]):
            self.assertLessEqual(wider, narrower)

    def test_f24_worst_case_equals_the_direct_division_floor(self):
        band_check = RECEIPT["decision"]["band_check"]
        self.assertEqual(
            band_check["reciprocal-multiply F=24"]["measured_worst_case_exact"],
            band_check["direct-division"]["measured_worst_case_exact"],
        )

    def test_gain_word_lands_within_a_quarter_binary32_ulp_of_f32_gain(self):
        # anchor-loud at F=22: the committed exact row is -1/2^25 -- the gain
        # word is closer to the float mixer.gain diagnostic than half of the
        # binary32 LSB at unity.
        loud_peak = RECEIPT["coverage"]["fixed:anchor-loud"]["peak_int"]
        rows = {
            row["frac_bits"]: row
            for row in RECEIPT["gain_word_rows"]
            if row["peak_int"] == loud_peak
        }
        self.assertEqual(
            rows[22]["error_vs_float_gain_exact"], "-1/33554432"
        )
        recomputed = nr.gain_word_rows(loud_peak, 22)
        self.assertEqual(recomputed, rows[22])


class CalibrationRuleDirection(unittest.TestCase):
    """C10 thresholds are the smallest power of two >= 2x the measured max.

    Pins the DR-0008 Section 10 example: a measured maximum of 2^-22
    calibrates to 2^-21 (at least 2x headroom), never 2^-22 -- the largest
    power of two at or below 2x measured is the wrong, one-octave-tight
    direction.
    """

    def test_dr0008_section10_example_measured_2_pow_neg22(self):
        threshold = nr._calibrated_threshold(Fraction(1, 1 << 22))
        self.assertEqual(threshold, Fraction(1, 1 << 21))
        self.assertGreaterEqual(threshold, 2 * Fraction(1, 1 << 22))

    def test_non_power_measured_calibrates_up_not_down(self):
        # the direct-division floor 524287/2^41: 2^-22 is below 2x measured
        threshold = nr._calibrated_threshold(Fraction(524287, 1 << 41))
        self.assertEqual(threshold, Fraction(1, 1 << 21))

    def test_exact_power_measured_stays_at_twice_itself(self):
        # measured exactly 2^-20: smallest power of two >= 2^-19 is 2^-19
        threshold = nr._calibrated_threshold(Fraction(1, 1 << 20))
        self.assertEqual(threshold, Fraction(1, 1 << 19))

    def test_every_committed_threshold_has_at_least_twice_the_headroom(self):
        band_check = RECEIPT["decision"]["band_check"]
        for label, row in band_check.items():
            measured = Fraction(row["measured_worst_case_exact"])
            threshold = Fraction(row["calibrated_threshold_exact"])
            self.assertGreaterEqual(
                threshold,
                2 * measured,
                label + " threshold below 2x measured (wrong direction)",
            )


class RecomputedRows(unittest.TestCase):
    """Recompute a bounded subset of full-clip rows against the receipt."""

    def test_extremum_direct_division_row_matches_the_receipt(self):
        clip = _all_cases()["fixed:extremum"]
        reference = nr.reference_float(clip)
        replay = nr.replay_fixed(clip, nr.METHOD_DIRECT)
        row = nr.compare_rows("fixed:extremum[direct-division]", replay["output"], reference, replay)
        committed = next(
            candidate
            for candidate in RECEIPT["rows"]
            if candidate["case"] == "fixed:extremum[direct-division]"
        )
        self.assertEqual(row["max_abs_error_exact"], committed["max_abs_error_exact"])
        self.assertEqual(row["max_abs_error_index"], committed["max_abs_error_index"])
        self.assertEqual(row["first_divergent_sample"], committed["first_divergent_sample"])
        self.assertEqual(row["sample_count"], 176400)

    def test_extremum_f22_row_matches_the_receipt(self):
        clip = _all_cases()["fixed:extremum"]
        reference = nr.reference_float(clip)
        replay = nr.replay_fixed(clip, nr.METHOD_RECIPROCAL, 22)
        row = nr.compare_rows(
            "fixed:extremum[reciprocal-multiply F=22]", replay["output"], reference, replay
        )
        committed = next(
            candidate
            for candidate in RECEIPT["rows"]
            if candidate["case"] == "fixed:extremum[reciprocal-multiply F=22]"
        )
        self.assertEqual(row["max_abs_error_exact"], committed["max_abs_error_exact"])
        self.assertEqual(
            row["max_abs_error_exact"],
            "524289/2199023255552",
        )

    def test_bypass_row_is_byte_exact_against_the_reference(self):
        clip = _all_cases()["fixed:anchor-quiet"]
        reference = nr.reference_float(clip)
        replay = nr.replay_fixed(clip, nr.METHOD_DIRECT)
        row = nr.compare_rows("fixed:anchor-quiet[bypass]", replay["output"], reference, replay)
        self.assertEqual(row["first_divergent_sample"], None)
        self.assertEqual(row["max_abs_error_exact"], "0/1")
        self.assertEqual(row["snr_db"], "infinite" if row["rmse"] == 0.0 else row["snr_db"])


class ForbiddenSubstitutesMustFail(unittest.TestCase):
    """DR-0003-forbidden normalization substitutes are negative controls.

    The substitutes are built from the canonical primitives with the branch
    guard deliberately bypassed -- that is what makes them mutations of the
    declared semantics -- and each must produce output that differs from the
    correct replay on its named case.
    """

    @staticmethod
    def _raw_divide(clip, peak_int):
        return [
            nr.saturate(
                nr.div_round(value * nr.AUDIO_SCALE, peak_int, nr.S5_MODE),
                nr.AUDIO_FORMAT,
            )
            for value in clip
        ]

    def test_limiter_substitute_fails_the_extremum_row(self):
        clip = _all_cases()["fixed:extremum"]
        replay = nr.replay_fixed(clip, nr.METHOD_DIRECT)
        limiter = [max(-nr.UNITY_INT, min(nr.UNITY_INT, v)) for v in clip]
        self.assertNotEqual(limiter, replay["output"])
        diverged = sum(
            1 for a, b in zip(limiter, replay["output"]) if a != b
        )
        self.assertGreater(diverged, 0)

    def test_constant_headroom_substitute_fails_the_anchor_loud_row(self):
        clip, _ = nr.anchored_cases()["fixed:anchor-loud"]
        replay = nr.replay_fixed(clip, nr.METHOD_RECIPROCAL, 22)
        # constant headroom 0.25 in Q2.21 (fm.AGC_HEADROOM analogue)
        agc = [v // 4 for v in clip]
        self.assertNotEqual(agc, replay["output"])

    def test_always_on_substitute_fails_the_below_one_row(self):
        clip = _all_cases()["fixed:below-one-max"]
        replay = nr.replay_fixed(clip, nr.METHOD_DIRECT)
        peak, _ = nr.fixed_peak(clip)
        self.assertLessEqual(peak, nr.UNITY_INT)
        forced = self._raw_divide(clip, peak)
        self.assertNotEqual(forced, replay["output"])

    def test_runner_up_wrong_peak_fails_the_above_one_row(self):
        clip = _all_cases()["fixed:above-one-min"]
        replay = nr.replay_fixed(clip, nr.METHOD_DIRECT)
        wrong = self._raw_divide(clip, 42)
        self.assertNotEqual(wrong, replay["output"])
        # the wrong divisor saturates hard: gain 2^21/42 overflows Q2.21
        self.assertGreaterEqual(
            max(abs(v) for v in wrong), nr.AUDIO_FORMAT.max_int
        )


if __name__ == "__main__":
    unittest.main()
