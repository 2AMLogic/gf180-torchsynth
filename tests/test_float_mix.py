"""Issue #42 float mix chain: semantics, captured evidence, mutations, replay.

Offline stdlib tests (no Torch). The VCA, mixer, and normalization semantics
are checked against hand-derived binary32 expectations; the normalization
branch fixtures are anchored on the landed measured peaks and the exact
binary32 below/at/above-one neighbors; the captured-evidence checks bind the
model's peak and derived-gain rules to the committed release-era capture
digests in ``sim/reference/trace-capture.json`` (case buffers live in the
operator's store; only digests are committed). Numeric formats stay open:
``numeric_contract`` is ``unbound:#53``, DR-0008 stays Proposed, and DR-0003
stays Proposed - the normalization semantics are tested, never ratified as a
hardware replay architecture, and the limiter/AGC substitutes DR-0003 forbids
are preregistered mutations that must fail.
"""

import hashlib
import json
import struct
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import float_interfaces as fi  # noqa: E402
from torchsynth_voice import float_mix as fm  # noqa: E402

N = fm.AUDIO_SAMPLES
CAPTURE_RECORD_PATH = ROOT / "sim/reference/trace-capture.json"
DIRECTED_PATH = ROOT / "spec/reference/directed-voice-v1.json"
CAPTURE_RECORD = json.loads(CAPTURE_RECORD_PATH.read_bytes())
DIRECTED = json.loads(DIRECTED_PATH.read_bytes())
UNITY_DIGEST = hashlib.sha256(fm.f32le_bytes([1.0])).hexdigest()


def f32(x):
    return fm.f32(x)


def digest_of(values):
    return hashlib.sha256(fm.f32le_bytes(values)).hexdigest()


def capture_case(case_id):
    for case in CAPTURE_RECORD["cases"]:
        if case["case"]["id"] == case_id:
            return case
    raise AssertionError("capture case missing: " + case_id)


def capture_traces(case_id, names):
    inventory = capture_case(case_id)["full_inventory"]
    found = {}
    for entry in inventory:
        if entry["name"] in names:
            found[entry["name"]] = entry["sha256"]
    missing = set(names) - set(found)
    assert not missing, "capture inventory missing: " + ",".join(sorted(missing))
    return found


def directed_case(case_id):
    for case in DIRECTED["cases"]:
        if case["id"] == case_id:
            return case
    raise AssertionError("directed case missing: " + case_id)


def tiled(tile, count=N):
    tile = [f32(value) for value in tile]
    return [tile[index % len(tile)] for index in range(count)]


def with_peaks(base, insertions):
    buffer = list(base)
    for index, value in insertions:
        buffer[index] = f32(value)
    return buffer


class RecordPolicyAndBindings(unittest.TestCase):
    def test_policy_version_is_declared(self):
        self.assertEqual(fm.CALCULATION_POLICY.version, "float-mix-v1")
        self.assertEqual(fm.CALCULATION_POLICY.calculation_dtype, "float32")

    def test_declared_call_sites_match_the_model(self):
        bindings = fm.checkpoint_bindings()
        self.assertEqual(bindings["vca"], [(16, 1), (20, 2), (23, 3)])
        self.assertEqual(
            bindings["normalize_if_clipping"],
            [
                (24, "mixer.pre_normalization"),
                (25, "mixer.peak"),
                (26, "mixer.gain"),
            ],
        )
        self.assertEqual(bindings["mixer"], 27)
        self.assertEqual(len(bindings["traces"]), 7)

    def test_owned_traces_are_registry_traces_with_declared_classes(self):
        registry = fi.load_registry()
        by_name = {trace["name"]: trace for trace in registry["traces"]}
        for name in fm.OWNED_TRACES:
            self.assertIn(name, by_name)
        for name in sorted(set(fm.OWNED_TRACES) - {"mixer.gain"}):
            self.assertEqual(fi.comparison_class(name), "declared-metrics", name)
        self.assertEqual(
            fi.comparison_class("mixer.gain"), "derived-diagnostic"
        )
        self.assertEqual(by_name["mixer.peak"]["shape"], [1])
        self.assertEqual(by_name["mixer.gain"]["shape"], [1])
        self.assertIsNone(by_name["mixer.peak"]["rate_hz"])
        self.assertIsNone(by_name["mixer.gain"]["rate_hz"])
        self.assertEqual(by_name["mixer.pre_normalization"]["shape"], [N])

    def test_upstream_input_curves_are_recorded_not_reimplemented(self):
        self.assertEqual(fm.MIXER_INPUT_CURVES["vco_1"], 1.0)
        self.assertEqual(fm.MIXER_INPUT_CURVES["vco_2"], 1.0)
        self.assertEqual(fm.MIXER_INPUT_CURVES["noise"], 0.025)
        self.assertEqual(fm.MIXER_SOURCES, ("vco_1", "vco_2", "noise"))


class VcaSemantics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = tiled(
            [0.0, 0.5, -0.25, 1.0, -1.0, 0.125, -2.0, 3.9478583336]
        )
        cls.gain = tiled(
            [1.0, 0.5, 0.0, 0.9999999403953552, 2.0, 0.25, 1.0, 0.0]
        )

    def test_product_is_elementwise_binary32(self):
        rendered = fm.audio_vca(self.source, self.gain)
        expected = [f32(a * b) for a, b in zip(self.source, self.gain)]
        self.assertEqual(fm.f32le_bytes(rendered), fm.f32le_bytes(expected))
        self.assertEqual(rendered[0], 0.0)
        self.assertEqual(rendered[3], f32(1.0 * 0.9999999403953552))
        self.assertEqual(rendered[7], 0.0)

    def test_unity_gain_preserves_bytes_and_zero_gain_silences(self):
        unity = fm.audio_vca(self.source, [1.0] * N)
        self.assertEqual(fm.f32le_bytes(unity), fm.f32le_bytes(self.source))
        silent = fm.audio_vca(self.source, [0.0] * N)
        self.assertEqual(set(silent), {0.0})

    def test_length_and_finiteness_are_refused(self):
        with self.assertRaises(fm.FloatMixError):
            fm.audio_vca(self.source[:-1], self.gain[:-1])
        bad = list(self.source)
        bad[0] = float("inf")
        with self.assertRaises(fm.FloatMixError):
            fm.audio_vca(bad, self.gain)


class MixerSemantics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a = tiled([0.5, -1.5, 2.0, -0.125, 0.0, 0.0, 0.0, 0.0])
        cls.b = tiled([1.0, 0.25, -0.5, 1.0, 0.0, 0.0, 0.0, 0.0])
        cls.c = tiled([0.125, -2.0, 0.75, -0.5, 0.0, 0.0, 0.0, 0.0])
        cls.levels = (0.9, 0.8, 0.7)

    def test_ordered_weighted_sum_with_one_rounding(self):
        merged = fm.mixer_weighted_sum((self.a, self.b, self.c), self.levels)
        expected = [
            f32(0.9 * a + 0.8 * b + 0.7 * c)
            for a, b, c in zip(self.a, self.b, self.c)
        ]
        self.assertEqual(fm.f32le_bytes(merged), fm.f32le_bytes(expected))

    def test_weight_assignment_follows_declared_order(self):
        zero = [0.0] * N
        merged = fm.mixer_weighted_sum((self.a, zero, zero), (1.0, 0.8, 0.7))
        self.assertEqual(fm.f32le_bytes(merged), fm.f32le_bytes(self.a))
        merged = fm.mixer_weighted_sum((zero, zero, self.c), (1.0, 0.8, 0.7))
        expected = [f32(0.7 * value) for value in self.c]
        self.assertEqual(fm.f32le_bytes(merged), fm.f32le_bytes(expected))

    def test_unit_levels_pass_source_through_byte_identically(self):
        merged = fm.mixer_weighted_sum(
            (self.a, [0.0] * N, [0.0] * N), (1.0, 0.0, 0.0)
        )
        self.assertEqual(fm.f32le_bytes(merged), fm.f32le_bytes(self.a))

    def test_wrong_source_count_is_refused(self):
        with self.assertRaises(fm.FloatMixError):
            fm.mixer_weighted_sum((self.a, self.b), self.levels[:2])


class NormalizationSemantics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.below_one = f32(1.0 - 2.0**-24)
        cls.above_one = f32(1.0 + 2.0**-23)
        cls.measured_below = f32(0.7895715833)
        cls.measured_above = f32(3.9478583336)
        cls.body = tiled(
            [
                0.4,
                -0.7,
                0.3,
                -0.0625,
                0.21,
                -0.9,
                0.55,
                -0.35,
            ]
        )

    def test_bypass_below_one_preserves_bytes(self):
        signal = with_peaks(self.body, [(7, self.below_one), (N - 1, -0.5)])
        output, peak, gain, branch = fm.normalize_if_clipping(signal)
        self.assertFalse(branch)
        self.assertEqual(peak, self.below_one)
        self.assertEqual(gain, 1.0)
        self.assertEqual(fm.f32le_bytes(output), fm.f32le_bytes(signal))

    def test_bypass_at_exactly_one(self):
        signal = with_peaks(self.body, [(3, 1.0), (N - 1, -1.0)])
        output, peak, gain, branch = fm.normalize_if_clipping(signal)
        self.assertFalse(branch)
        self.assertEqual(peak, 1.0)
        self.assertEqual(gain, 1.0)
        self.assertEqual(fm.f32le_bytes(output), fm.f32le_bytes(signal))

    def test_silence_stays_silence_with_unity_gain(self):
        output, peak, gain, branch = fm.normalize_if_clipping([0.0] * N)
        self.assertFalse(branch)
        self.assertEqual(peak, 0.0)
        self.assertEqual(gain, 1.0)
        self.assertEqual(fm.f32le_bytes(output), fm.f32le_bytes([0.0] * N))

    def test_strict_above_one_divides_by_the_observed_peak(self):
        signal = with_peaks(self.body, [(11, self.above_one), (N - 2, -1.0)])
        output, peak, gain, branch = fm.normalize_if_clipping(signal)
        self.assertTrue(branch)
        self.assertEqual(peak, self.above_one)
        self.assertEqual(gain, f32(1.0 / self.above_one))
        expected = [f32(value / self.above_one) for value in signal]
        self.assertEqual(fm.f32le_bytes(output), fm.f32le_bytes(expected))

    def test_division_is_not_multiply_by_reciprocal(self):
        signal = with_peaks(self.body, [(5, self.measured_above)])
        output, peak, _, _ = fm.normalize_if_clipping(signal)
        divided = [f32(value / peak) for value in signal]
        reciprocal = [f32(value * f32(1.0 / peak)) for value in signal]
        self.assertEqual(fm.f32le_bytes(output), fm.f32le_bytes(divided))
        differing = sum(
            1
            for a, b in zip(divided, reciprocal)
            if struct.pack("<f", a) != struct.pack("<f", b)
        )
        self.assertGreater(differing, 0)

    def test_derived_gain_is_the_reciprocal_diagnostic(self):
        self.assertEqual(fm.derived_gain(1.0), 1.0)
        self.assertEqual(fm.derived_gain(0.0), 1.0)
        self.assertEqual(fm.derived_gain(self.below_one), 1.0)
        self.assertEqual(
            fm.derived_gain(self.measured_above), f32(1.0 / self.measured_above)
        )
        self.assertEqual(
            fm.derived_gain(self.above_one), f32(1.0 / self.above_one)
        )
        # The exact f32 neighbors sit one division apart: the reciprocal of
        # the below-one neighbor rounds up onto the above-one neighbor.
        self.assertEqual(f32(1.0 / self.below_one), self.above_one)

    def test_dc_is_not_normalized_away_below_one_and_is_scaled_above(self):
        quiet = [0.5] * N
        output, peak, gain, branch = fm.normalize_if_clipping(quiet)
        self.assertFalse(branch)
        self.assertEqual(fm.f32le_bytes(output), fm.f32le_bytes(quiet))
        loud = [2.0] * N
        output, peak, gain, branch = fm.normalize_if_clipping(loud)
        self.assertTrue(branch)
        self.assertEqual(peak, 2.0)
        self.assertEqual(set(output), {f32(2.0 / 2.0)})

    def test_clipping_is_scaled_not_left_clipped(self):
        clipped = f32(1.9)
        signal = with_peaks(self.body, [(9, clipped), (17, -clipped)])
        output, peak, _, branch = fm.normalize_if_clipping(signal)
        self.assertTrue(branch)
        self.assertEqual(peak, clipped)
        self.assertEqual(output[9], 1.0)
        self.assertEqual(output[17], -1.0)

    def test_tied_maximum_records_earliest_index(self):
        signal = with_peaks(self.body, [(10, 1.5), (N - 1, -1.5)])
        peak, index = fm.peak_of_clip(signal)
        self.assertEqual(peak, 1.5)
        self.assertEqual(index, 10)

    def test_late_unique_peak_records_the_late_index(self):
        signal = with_peaks(self.body, [(N - 1, -2.5), (100, 1.5)])
        peak, index = fm.peak_of_clip(signal)
        self.assertEqual(peak, 2.5)
        self.assertEqual(index, N - 1)

    def test_measured_release_anchors_keep_their_branches(self):
        quiet = tiled(
            [0.25, -0.5, 0.125, -0.0625, 0.03125, -0.015625, 0.125, -0.25]
        )
        below = with_peaks(quiet, [(21, self.measured_below)])
        output, peak, gain, branch = fm.normalize_if_clipping(below)
        self.assertFalse(branch)
        self.assertEqual(peak, self.measured_below)
        self.assertEqual(fm.f32le_bytes(output), fm.f32le_bytes(below))
        above = with_peaks(self.body, [(33, self.measured_above)])
        output, peak, gain, branch = fm.normalize_if_clipping(above)
        self.assertTrue(branch)
        self.assertEqual(peak, self.measured_above)
        expected = [f32(value / self.measured_above) for value in above]
        self.assertEqual(fm.f32le_bytes(output), fm.f32le_bytes(expected))


class CapturedEvidence(unittest.TestCase):
    """Bind peak and gain rules to the committed release-era capture digests.

    The captured case buffers live in the operator's store; the committed
    record carries their SHA-256 digests. The directed normalization cases
    targeted the exact binary32 below/at/above-one neighbors, so the
    committed peak digests must equal the digest of exactly those values, and
    the committed gain digests must equal the digest the model's derived-gain
    rule produces from them.
    """

    def test_captured_peaks_are_the_exact_binary32_targets(self):
        for case_id in (
            "normalization:above",
            "normalization:below",
            "normalization:tie",
        ):
            target = f32(
                directed_case(case_id)["normalization_target"]["target_peak"]
            )
            recorded = capture_traces(case_id, {"mixer.peak"})["mixer.peak"]
            self.assertEqual(
                digest_of([target]),
                recorded,
                case_id + " captured peak is not the exact directed target",
            )

    def test_captured_gain_matches_the_derived_reciprocal_rule(self):
        for case_id in (
            "normalization:above",
            "normalization:below",
            "normalization:tie",
        ):
            target = f32(
                directed_case(case_id)["normalization_target"]["target_peak"]
            )
            recorded = capture_traces(case_id, {"mixer.gain"})["mixer.gain"]
            self.assertEqual(
                digest_of([fm.derived_gain(target)]),
                recorded,
                case_id + " captured gain does not match the derived rule",
            )

    def test_captured_bypass_cases_preserve_the_mix_bytes(self):
        for case_id in ("normalization:below", "normalization:tie", "global-6"):
            digests = capture_traces(
                case_id, {"mixer.pre_normalization", "mixer.output"}
            )
            self.assertEqual(
                digests["mixer.pre_normalization"],
                digests["mixer.output"],
                case_id + " bypass must not alter the mix bytes",
            )

    def test_captured_above_one_cases_apply_division(self):
        for case_id in ("normalization:above", "global-0"):
            digests = capture_traces(
                case_id,
                {"mixer.pre_normalization", "mixer.output", "mixer.gain"},
            )
            self.assertNotEqual(
                digests["mixer.pre_normalization"],
                digests["mixer.output"],
                case_id + " normalize branch must alter the mix bytes",
            )
            self.assertNotEqual(digests["mixer.gain"], UNITY_DIGEST)

    def test_captured_tie_case_mix_is_the_vco_1_lane(self):
        digests = capture_traces(
            "normalization:tie",
            {"mixer.pre_normalization", "vco_1.post_vca", "mixer.peak"},
        )
        self.assertEqual(
            digests["mixer.pre_normalization"],
            digests["vco_1.post_vca"],
            "the tie capture recorded a nontrivial mix for a (1, 0, 0) lane",
        )
        self.assertEqual(digests["mixer.peak"], UNITY_DIGEST)

    def test_recorded_branch_decisions_are_strictly_above_one(self):
        dividing = {"normalization:above", "global-0"}
        for case in CAPTURE_RECORD["cases"]:
            case_id = case["case"]["id"]
            self.assertEqual(
                case["normalization_branch"]["peak_gt_one"],
                case_id in dividing,
                case_id + " recorded branch disagrees with strict peak > 1",
            )
            self.assertTrue(case["passive_capture_equal_bytes"])

    def test_release_exercise_covers_the_declared_branches(self):
        exercise = CAPTURE_RECORD["normalization_exercise"]
        self.assertEqual(
            set(exercise),
            {
                "above",
                "at-boundary",
                "below",
                "late-peak",
                "silence",
                "tied-maximum",
            },
        )
        for name, entry in exercise.items():
            self.assertTrue(entry["derived_gain_matches_rule"], name)
            self.assertTrue(entry["output_matches_original_branch"], name)
            self.assertEqual(
                entry["division_applied"],
                name in ("above", "late-peak"),
                name + " division decision disagrees with strict peak > 1",
            )


def mutation_fixtures():
    sources = (
        with_peaks(
            tiled([0.5, -1.5, 2.0, -0.25, 1.0, -0.75, 0.125, -2.0]),
            [(12345, 3.9478583336)],
        ),
        tiled([1.0, 0.25, -0.5, 1.5, -1.0, 0.5, -0.25, 0.75]),
        tiled([0.125, -0.375, 0.625, -0.875, 0.25, -0.5, 0.375, -0.75]),
    )
    amps = (
        tiled([1.0, 0.75, 0.5, 0.875, 1.0, 0.625, 0.75, 0.5]),
        tiled([0.875, 1.0, 0.625, 0.75, 0.5, 1.0, 0.875, 0.625]),
        tiled([0.75, 0.5, 1.0, 0.625, 0.875, 0.75, 1.0, 0.5]),
    )
    levels = (0.9, 0.8, 0.7)
    quiet_sources = (
        tiled([0.25, -0.5, 0.125, -0.25, 0.5, -0.125, 0.25, -0.5]),
        tiled([0.5, 0.25, -0.125, 0.5, -0.25, 0.125, -0.5, 0.25]),
        tiled([0.125, 0.0625, -0.03125, 0.125, -0.0625, 0.03125, -0.125, 0.0625]),
    )
    quiet_levels = (0.2, 0.1, 0.05)
    return sources, amps, levels, quiet_sources, quiet_levels


class MutationControls(unittest.TestCase):
    """Every preregistered mutation must fail exactly its named rows."""

    @classmethod
    def setUpClass(cls):
        cls.fixtures = mutation_fixtures()
        cls.reference, cls.reference_diagnostics = fm.render_mix_chain(
            cls.fixtures[0], cls.fixtures[1], cls.fixtures[2]
        )
        cls.quiet_reference, _ = fm.render_mix_chain(
            cls.fixtures[3], cls.fixtures[1], cls.fixtures[4]
        )

    def rows(self, rendered, diagnostics):
        rows = {name: fm.f32le_bytes(rendered[name]) for name in fm.OWNED_TRACES}
        rows["diagnostics.peak_index"] = struct.pack(
            "<q", diagnostics["peak_index"]
        )
        rows["diagnostics.branch"] = (
            b"\x01" if diagnostics["normalized_branch"] else b"\x00"
        )
        return rows

    def differing_rows(self, rendered, diagnostics):
        reference = self.rows(self.reference, self.reference_diagnostics)
        mutated = self.rows(rendered, diagnostics)
        return sorted(
            name for name, value in mutated.items() if value != reference[name]
        )

    def render(self, mutations, quiet=False):
        sources, amps, levels = self.fixtures[0:3]
        if quiet:
            sources, levels = self.fixtures[3], self.fixtures[4]
        return fm.render_mix_chain(sources, amps, levels, mutations=mutations)

    def test_limiter_substitute_fails_the_output_row(self):
        mutated, diagnostics = self.render({fm.MUTATION_LIMITER})
        failing = self.differing_rows(mutated, diagnostics)
        self.assertIn("mixer.output", failing)
        self.assertNotIn("mixer.pre_normalization", failing)
        self.assertNotIn("vco_1.post_vca", failing)
        self.assertNotIn("mixer.peak", failing)
        self.assertNotIn("mixer.gain", failing)

    def test_agc_substitute_fails_output_rows_on_both_branches(self):
        mutated, diagnostics = self.render({fm.MUTATION_AGC})
        failing = self.differing_rows(mutated, diagnostics)
        self.assertIn("mixer.output", failing)
        quiet_mutated, _ = self.render({fm.MUTATION_AGC}, quiet=True)
        self.assertNotEqual(
            fm.f32le_bytes(quiet_mutated["mixer.output"]),
            fm.f32le_bytes(self.quiet_reference["mixer.output"]),
            "constant headroom must not pass a below-one clip unchanged",
        )

    def test_always_on_fails_the_bypass_rows(self):
        quiet_mutated, _ = self.render({fm.MUTATION_ALWAYS}, quiet=True)
        self.assertNotEqual(
            fm.f32le_bytes(quiet_mutated["mixer.output"]),
            fm.f32le_bytes(self.quiet_reference["mixer.output"]),
            "always-on normalization must not pass a below-one clip unchanged",
        )
        silence = [0.0] * N
        with self.assertRaises(ZeroDivisionError):
            fm.normalize_if_clipping(silence, mutations={fm.MUTATION_ALWAYS})

    def test_normalization_off_fails_the_above_one_row(self):
        mutated, diagnostics = self.render({fm.MUTATION_NEVER})
        failing = self.differing_rows(mutated, diagnostics)
        self.assertIn("mixer.output", failing)
        self.assertNotIn("mixer.pre_normalization", failing)

    def test_wrong_peak_fails_the_output_row(self):
        mutated, diagnostics = self.render({fm.MUTATION_WRONG_PEAK})
        failing = self.differing_rows(mutated, diagnostics)
        self.assertIn("mixer.output", failing)
        self.assertNotIn("vco_1.post_vca", failing)

    def test_wrong_mix_order_fails_the_mix_rows_only(self):
        mutated, diagnostics = self.render({fm.MUTATION_WRONG_ORDER})
        failing = self.differing_rows(mutated, diagnostics)
        for name in ("mixer.pre_normalization", "mixer.output"):
            self.assertIn(name, failing)
        for name in ("vco_1.post_vca", "vco_2.post_vca", "noise.post_vca"):
            self.assertNotIn(name, failing)

    def test_pre_vca_gain_fails_the_post_vca_rows_and_downstream(self):
        mutated, diagnostics = self.render({fm.MUTATION_PRE_VCA_GAIN})
        failing = self.differing_rows(mutated, diagnostics)
        for name in ("vco_1.post_vca", "vco_2.post_vca", "noise.post_vca"):
            self.assertIn(name, failing)
        self.assertIn("mixer.output", failing)

    def test_latest_index_tie_fails_the_index_row_only(self):
        base_sources = (
            with_peaks(tiled([0.25]), [(0, 1.5), (N - 1, -1.5)]),
            [0.0] * N,
            [0.0] * N,
        )
        base_amps = ([1.0] * N, [0.0] * N, [0.0] * N)
        base_levels = (1.0, 0.0, 0.0)
        faithful, faithful_diagnostics = fm.render_mix_chain(
            base_sources, base_amps, base_levels
        )
        self.assertEqual(faithful_diagnostics["peak_index"], 0)
        mutated, mutated_diagnostics = fm.render_mix_chain(
            base_sources,
            base_amps,
            base_levels,
            mutations={fm.MUTATION_LATE_TIE},
        )
        self.assertEqual(mutated_diagnostics["peak_index"], N - 1)
        reference_rows = self.rows(faithful, faithful_diagnostics)
        mutated_rows = self.rows(mutated, mutated_diagnostics)
        self.assertNotEqual(
            mutated_rows["diagnostics.peak_index"],
            reference_rows["diagnostics.peak_index"],
        )
        self.assertEqual(mutated_rows["mixer.peak"], reference_rows["mixer.peak"])
        self.assertEqual(
            mutated_rows["mixer.output"], reference_rows["mixer.output"]
        )

    def test_unknown_mutation_is_refused(self):
        with self.assertRaises(fm.FloatMixError):
            self.render({"not-a-mutation"})


class ReplayAndPurity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = mutation_fixtures()
        cls.sources, cls.amps, cls.levels = cls.fixtures[0:3]

    def snapshot(self, sources, amps, levels):
        return [
            [fm.f32le_bytes(buffer) for buffer in group]
            for group in (sources, amps)
        ] + [tuple(levels)]

    def test_repeated_renders_are_byte_identical(self):
        first, first_diag = fm.render_mix_chain(
            self.sources, self.amps, self.levels
        )
        second, second_diag = fm.render_mix_chain(
            self.sources, self.amps, self.levels
        )
        for name in fm.OWNED_TRACES:
            self.assertEqual(
                fm.f32le_bytes(first[name]), fm.f32le_bytes(second[name]), name
            )
        self.assertEqual(first_diag, second_diag)

    def test_changed_inputs_leave_no_residue(self):
        baseline = self.snapshot(self.sources, self.amps, self.levels)
        first, _ = fm.render_mix_chain(self.sources, self.amps, self.levels)
        other_sources = ([2.0] * N, [0.0] * N, [0.0] * N)
        fm.render_mix_chain(other_sources, self.amps, self.levels)
        restored, _ = fm.render_mix_chain(self.sources, self.amps, self.levels)
        for name in fm.OWNED_TRACES:
            self.assertEqual(
                fm.f32le_bytes(first[name]), fm.f32le_bytes(restored[name])
            )
        self.assertEqual(
            self.snapshot(self.sources, self.amps, self.levels), baseline
        )

    def test_render_does_not_mutate_declared_inputs(self):
        baseline = self.snapshot(self.sources, self.amps, self.levels)
        fm.render_mix_chain(self.sources, self.amps, self.levels)
        self.assertEqual(
            self.snapshot(self.sources, self.amps, self.levels), baseline
        )

    def test_module_imports_no_torchsynth_torch_or_numpy(self):
        code = (
            "import sys; sys.path.insert(0, %r); "
            "import torchsynth_voice.float_mix; "
            "print(sorted(set(sys.modules) & {'torch', 'numpy', 'torchsynth'}))"
            % str(ROOT / "src")
        )
        observed = subprocess.check_output(
            [sys.executable, "-S", "-c", code], text=True
        ).strip()
        self.assertEqual(observed, "[]")
        source = (ROOT / "src/torchsynth_voice/float_mix.py").read_text()
        for forbidden in ("import torch", "import numpy", "from torch", "from numpy"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
