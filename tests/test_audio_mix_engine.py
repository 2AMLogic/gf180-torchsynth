"""Audio VCA + pre-normalization mixer lane (issue #76).

Model-level checks run everywhere; the RTL simulation is exercised only
when Icarus Verilog is installed (CI's tb-sim job arbitrates on such a
host; an unrun check is never reported as a pass).

The lane under test is the frozen composed fixed model's three audio VCAs
and pre-normalization mixer. Nothing here claims synthesis, layout,
signoff, hardware playback, or sound fidelity.
"""

import hashlib
import json
import math
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import directed as dr  # noqa: E402
from torchsynth_voice import mix_golden as mx  # noqa: E402
from torchsynth_voice.fixed_voice import AcceptedFormats  # noqa: E402
from torchsynth_voice.fixedpoint.counters import (  # noqa: E402
    ROUNDING,
    SATURATION,
)

TB = ROOT / "tb" / "run_tb.py"
RECEIPT_PATH = ROOT / "sim/reference/fixed-voice-golden-v1.json"
CONSTANTS_PKG = ROOT / "tb/sv/gf180_rtl_constants_pkg.sv"
MIX_DUT = ROOT / "tb/sv/audio_mix_engine.sv"
MIX_TB = ROOT / "tb/sv/tb_audio_mix_engine.sv"

#: The receipt case the model-level row-equality proof runs on: noise is
#: the only audible lane, so the noise VCA and the noise mixer term are
#: both load-bearing in its frozen traces.
MODEL_CASE = "source:noise"
#: Declared directed fixtures the fixture-class checks read.
SILENCE_FIXTURE = "special:silence"
STRESS_FIXTURE = "special:stress"


def has_iverilog() -> bool:
    return shutil.which("iverilog") is not None


def rtl_body(path: Path) -> str:
    """The RTL source with its comments stripped (logic only).

    The module header documents what this lane deliberately does NOT
    implement, so the "no normalization replay here" assertions must read
    the logic, not the prose.
    """

    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        code = line.split("//", 1)[0].rstrip()
        if code:
            lines.append(code)
    return "\n".join(lines)


def load_fixture(case_id: str):
    document = json.loads(dr.MANIFEST_PATH.read_text(encoding="utf-8"))
    dr.validate_manifest(document)
    by_id = {case["id"]: case for case in document["cases"]}
    patch = dr.resolve_patch(document, by_id[case_id])
    return (
        {name: pair["physical"] for name, pair in patch.items()},
        {name: pair["normalized"] for name, pair in patch.items()},
    )


class MixLaneContractTest(unittest.TestCase):
    """Declared formats, sites, and fixture availability (no render)."""

    @classmethod
    def setUpClass(cls):
        cls.formats = AcceptedFormats()
        cls.receipt = json.loads(RECEIPT_PATH.read_text(encoding="utf-8"))
        cls.cases = {
            case["id"]: case for case in cls.receipt["cases"]
            if case.get("parameters") is not None
        }

    def test_receipt_bindings_bind_the_live_accepted_contract(self):
        bindings = self.receipt["bindings"]
        self.assertTrue(str(bindings["dr_0008_status"]).startswith("Accepted"))
        self.assertEqual(bindings["lut_sha256"], self.formats.table.sha256())
        self.assertEqual(
            bindings["constants_package_sha256"],
            hashlib.sha256(CONSTANTS_PKG.read_bytes()).hexdigest(),
        )

    def test_accumulator_format_is_the_models_own_product_format(self):
        """The mixer accumulator is the model's declared Q6.42 word."""

        audio = self.formats.audio
        product = mx.product_format(audio)
        self.assertEqual(product.identity, "Q6.42")
        self.assertEqual(product.frac_bits, 2 * audio.frac_bits)
        self.assertEqual(product.width, 49)
        # DR-0010 A8: a 48-bit-class accumulator holds three 24x24 products.
        self.assertGreater(product.max_int, 3 * (1 << (2 * (audio.width - 1))))

    def test_declared_sites_match_the_frozen_models_naming(self):
        self.assertEqual(mx.LANES, ("vco_1", "vco_2", "noise"))
        self.assertEqual(
            [mx.VCA_SITES[lane] for lane in mx.LANES],
            ["vca_1.S4", "vca_2.S4", "vca_3.S4"],
        )
        self.assertEqual(mx.MIXER_SITE, "mixer.S4")
        self.assertEqual(mx.LEVEL_ENTRY_SITE, "mixer.level_q")

    def test_rtl_engine_imports_the_emitted_constants_package(self):
        """The RTL invents no width and implements no normalization."""

        body = rtl_body(MIX_DUT)
        self.assertIn("import gf180_rtl_constants::*;", body)
        # The accumulator's fractional width is derived from the emitted
        # C1 constant, never written out as a literal.
        self.assertIn("2 * C1_FRAC_BITS", body)
        self.assertNotIn("localparam integer C1_FRAC_BITS", body)
        self.assertNotIn("localparam integer C1_WIDTH", body)
        # The C9 normalization replay is #77's owner row: none of its
        # constants or dataflow may appear in this lane's logic.
        for banned in ("C9_", "reciprocal", "gain_word", "peak >"):
            self.assertNotIn(banned, body, banned)
        # The peak feed is exported as a magnitude at the audio width.
        self.assertIn("output reg         [C1_WIDTH-1:0]  peak_word", body)

    def test_declared_fixture_classes_are_available(self):
        """Silence / single-source / all-source / high-depth / stress."""

        physical, _normalized = load_fixture(SILENCE_FIXTURE)
        self.assertEqual(
            [physical["mixer." + lane] for lane in mx.LANES], [0.0, 0.0, 0.0]
        )
        stress_physical, _ = load_fixture(STRESS_FIXTURE)
        self.assertTrue(
            all(stress_physical["mixer." + lane] == 1.0 for lane in mx.LANES)
        )
        for case_id in ("source:vco_1", "source:vco_2", "source:noise"):
            self.assertIn(case_id, self.cases)
            levels = [
                self.cases[case_id]["parameters"]["mixer." + lane]
                for lane in mx.LANES
            ]
            self.assertEqual(sum(1 for value in levels if value != 0.0), 1)
        for case_id in ("boundary:vco_1.mod_depth:upper",
                        "boundary:vco_2.mod_depth:upper",
                        "normalization-stress:anchor-3.9478583336"):
            self.assertIn(case_id, self.cases)

    def test_observed_curve_exponents_are_lane_independent(self):
        """The noise curve and the oscillator curves are read separately."""

        manifest_physical, noise_normalized = load_fixture("source:noise")
        receipt_physical = self.cases["source:noise"]["parameters"]
        # The manifest pair and the receipt's committed parameter are the
        # same observed physical level, so the exponent read below belongs
        # to the case the RTL is actually run on.
        self.assertEqual(
            manifest_physical["mixer.noise"], receipt_physical["mixer.noise"]
        )
        exponent = mx.observed_curve_exponent(
            receipt_physical, noise_normalized, "noise"
        )
        self.assertAlmostEqual(
            exponent, 1.0 / mx.MIXER_INPUT_CURVES["noise"], places=4
        )
        vco2_physical, vco2_normalized = load_fixture("source:vco_2")
        self.assertAlmostEqual(
            mx.observed_curve_exponent(
                vco2_physical, vco2_normalized, "vco_2"
            ),
            1.0 / mx.MIXER_INPUT_CURVES["vco_2"],
            places=12,
        )
        # A pair at 1.0 observes nothing: every exponent agrees there.
        stress_physical, stress_normalized = load_fixture(STRESS_FIXTURE)
        self.assertIsNone(
            mx.observed_curve_exponent(
                stress_physical, stress_normalized, "noise"
            )
        )

    def test_level_words_quantize_the_observed_physical_levels(self):
        words = mx.level_words(
            self.formats, self.cases["source:noise"]["parameters"]
        )
        self.assertEqual(words["vco_1"], 0)
        self.assertEqual(words["vco_2"], 0)
        # 0.24999973304555959 in Q2.21, half-even.
        self.assertEqual(words["noise"], 524287)

    def test_peak_feed_keeps_the_earliest_maximal_index(self):
        self.assertEqual(mx.peak_feed([0, 0, 0]), (0, -1))
        self.assertEqual(mx.peak_feed([1, -3, 3, -2]), (3, 1))
        self.assertEqual(mx.peak_feed([-5]), (5, 0))

    def test_mirror_refuses_a_missing_stream(self):
        with self.assertRaises(mx.MixGoldenError):
            mx.mirror_mix_lane(
                self.formats,
                {"vco_1": [0], "vco_2": [0]},
                {"vco_1": [0], "vco_2": [0], "noise": [0]},
                {"vco_1": 0, "vco_2": 0, "noise": 0},
            )


class MixMirrorEdgeTest(unittest.TestCase):
    """Structural edge classes through the mirror (no model render)."""

    @classmethod
    def setUpClass(cls):
        cls.formats = AcceptedFormats()
        cls.audio = cls.formats.audio
        cls.one = 1 << cls.audio.frac_bits

    def walk(self, raw_rows, amp_rows, levels):
        raw = {lane: [row[i] for row in raw_rows]
               for i, lane in enumerate(mx.LANES)}
        amp = {lane: [row[i] for row in amp_rows]
               for i, lane in enumerate(mx.LANES)}
        return mx.mirror_mix_lane(self.formats, raw, amp, levels)

    def test_unity_gain_passes_the_sources_through_unchanged(self):
        rows = [(self.one, -self.one, 0), (0, self.one, self.one // 2)]
        streams, aux = self.walk(
            rows, [(self.one, self.one, self.one)] * 2,
            {"vco_1": self.one, "vco_2": 0, "noise": 0},
        )
        self.assertEqual(streams["post_vca"]["vco_1"], [self.one, 0])
        self.assertEqual(streams["post_vca"]["vco_2"], [-self.one, self.one])
        self.assertEqual(streams["mix"], [self.one, 0])
        self.assertEqual(aux["sats"], 0)
        self.assertEqual(aux["rounds"], 0)

    def test_saturation_clamps_and_counts_at_both_rails(self):
        """Every lane at full scale saturates the mixer narrowing (C7)."""

        max_word = self.audio.max_int
        min_word = self.audio.min_int
        rows = [(max_word, max_word, max_word), (min_word, min_word, max_word)]
        streams, aux = self.walk(
            rows, [(self.one, self.one, self.one)] * 2,
            {"vco_1": self.one, "vco_2": self.one, "noise": self.one},
        )
        self.assertEqual(streams["mix"], [max_word, min_word])
        self.assertEqual(aux["sites"]["mixer.S4"][SATURATION], 2)
        self.assertEqual(aux["peak_word"], -min_word)

    def test_half_even_ties_round_to_even_at_the_vca_site(self):
        """A tie at the VCA narrowing rounds to even, not away from zero."""

        half = 1 << (self.audio.frac_bits - 1)  # 0.5 in Q2.21
        # 3 * 0.5 = 1.5 ulp -> tie, rounds to even (2); 1 * 0.5 -> 0.5 ulp
        # tie, rounds to even (0).
        rows = [(3, 0, 0), (1, 0, 0)]
        streams, aux = self.walk(
            rows, [(half, 0, 0)] * 2,
            {"vco_1": self.one, "vco_2": 0, "noise": 0},
        )
        self.assertEqual(streams["post_vca"]["vco_1"], [2, 0])
        self.assertEqual(aux["sites"]["vca_1.S4"][ROUNDING], 2)

    def test_sign_products_carry_the_model_signs(self):
        rows = [(-self.one, 0, 0), (self.one, 0, 0)]
        streams, _aux = self.walk(
            rows, [(self.one, 0, 0), (-self.one, 0, 0)],
            {"vco_1": self.one, "vco_2": 0, "noise": 0},
        )
        self.assertEqual(streams["mix"], [-self.one, -self.one])
        self.assertEqual(streams["abs"], [self.one, self.one])

    def test_negative_level_word_inverts_the_lane(self):
        rows = [(self.one, 0, 0)]
        streams, _aux = self.walk(
            rows, [(self.one, 0, 0)],
            {"vco_1": -self.one, "vco_2": 0, "noise": 0},
        )
        self.assertEqual(streams["mix"], [-self.one])

    def test_accumulator_never_leaves_the_declared_band(self):
        """The Q6.42 band is a contract: the extremes still fit."""

        max_word = self.audio.max_int
        _streams, aux = self.walk(
            [(max_word, max_word, max_word)],
            [(self.audio.max_int, self.audio.max_int, self.audio.max_int)],
            {"vco_1": self.audio.max_int, "vco_2": self.audio.max_int,
             "noise": self.audio.max_int},
        )
        product = mx.product_format(self.audio)
        self.assertTrue(product.contains(aux["acc_max"]))
        self.assertTrue(product.contains(aux["acc_min"]))


class MixModelRowEqualityTest(unittest.TestCase):
    """The mirror IS the composed model, pinned to the frozen receipt."""

    @classmethod
    def setUpClass(cls):
        cls.formats = AcceptedFormats()
        cls.receipt = json.loads(RECEIPT_PATH.read_text(encoding="utf-8"))
        cls.case_meta = next(
            case for case in cls.receipt["cases"]
            if case["id"] == MODEL_CASE
        )
        # derive_case REFUSES unless the mirror reproduces the composed
        # model's own rows and per-site counter records.
        cls.case = mx.derive_case(
            cls.formats, cls.case_meta["parameters"], cls.case_meta["sound_index"]
        )

    def test_mirror_rows_reproduce_the_frozen_receipt_digests(self):
        for name in mx.MIX_TRACES:
            self.assertEqual(
                mx.digest_words(self.case["traces"][name]),
                self.case_meta["traces"][name],
                name,
            )

    def test_the_lane_walks_the_complete_clip(self):
        self.assertEqual(self.case["samples"], 176400)
        self.assertFalse(self.case["prefix"])
        for lane in mx.LANES:
            self.assertEqual(len(self.case["streams"]["post_vca"][lane]), 176400)

    def test_peak_feed_matches_the_models_mixer_peak(self):
        peak, index = mx.peak_feed(self.case["streams"]["mix"])
        self.assertEqual([peak], self.case["model_traces"]["mixer.peak"])
        self.assertEqual(index, self.case["aux"]["peak_index"])
        self.assertEqual(
            self.case["streams"]["peak"][-1], self.case["aux"]["peak_word"]
        )

    def test_running_peak_is_monotone_and_bounds_every_magnitude(self):
        running = self.case["streams"]["peak"]
        magnitudes = self.case["streams"]["abs"]
        self.assertEqual(len(running), len(magnitudes))
        previous = 0
        for index in (0, 1, 995, 88200, 176399):
            self.assertGreaterEqual(running[index], magnitudes[index])
        for index in range(1, len(running), 4409):
            self.assertGreaterEqual(running[index], running[index - 1])
            previous = running[index]
        self.assertLessEqual(previous, self.case["aux"]["peak_word"])

    def test_per_site_counters_match_the_composed_models_records(self):
        records = {
            (record["site"], record["kind"]): record["count"]
            for record in self.case["diagnostics"]["counters"]["records"]
        }
        for site, kinds in self.case["aux"]["sites"].items():
            for kind, count in kinds.items():
                self.assertEqual(count, records.get((site, kind), 0),
                                 "%s/%s" % (site, kind))
        # The noise lane is the audible one here: its VCA and the mixer
        # narrowing both round; the silent lanes do not.
        sites = self.case["aux"]["sites"]
        self.assertGreater(sites["vca_3.S4"][ROUNDING], 0)
        self.assertGreater(sites["mixer.S4"][ROUNDING], 0)

    def test_prefix_walk_matches_the_full_walk_prefix(self):
        prefix = mx.mirror_mix_lane(
            self.formats, self.case["raw"], self.case["amp"],
            self.case["levels"], samples=4096,
        )[0]
        self.assertEqual(
            prefix["mix"], self.case["streams"]["mix"][:4096]
        )
        for lane in mx.LANES:
            self.assertEqual(
                prefix["post_vca"][lane],
                self.case["streams"]["post_vca"][lane][:4096],
            )


class MixSilenceFixtureTest(unittest.TestCase):
    """The declared silence fixture: zero mixer, zero mix, zero peak."""

    @classmethod
    def setUpClass(cls):
        cls.formats = AcceptedFormats()
        physical, normalized = load_fixture(SILENCE_FIXTURE)
        cls.case = mx.derive_case(cls.formats, physical, 0, normalized)

    def test_silence_produces_an_identically_zero_mix_and_no_peak(self):
        self.assertEqual(
            [self.case["levels"][lane] for lane in mx.LANES], [0, 0, 0]
        )
        self.assertTrue(all(word == 0 for word in self.case["streams"]["mix"]))
        self.assertEqual(self.case["aux"]["peak_word"], 0)
        self.assertEqual(self.case["aux"]["peak_index"], -1)
        self.assertEqual(self.case["aux"]["sats"], 0)

    def test_the_vcas_still_run_under_a_silent_mixer(self):
        """A zero mixer level gates the SUM, never the VCA itself."""

        self.assertTrue(
            any(word != 0 for word in self.case["streams"]["post_vca"]["vco_1"])
        )
        self.assertGreater(self.case["aux"]["sites"]["vca_1.S4"][ROUNDING], 0)


class MixRtlTest(unittest.TestCase):
    """The full tb flow (requires Icarus Verilog)."""

    @unittest.skipUnless(has_iverilog(), "Icarus Verilog not installed")
    def test_full_mix_tb_flow(self):
        with tempfile.TemporaryDirectory(prefix="tb-mix-test-") as tmp:
            result = subprocess.run(
                [sys.executable, str(TB), "mix", "--workdir", tmp],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=10800,
            )
            if result.returncode != 0:
                self.fail(
                    "mix tb flow failed (%d):\n%s\n%s"
                    % (result.returncode, result.stdout[-6000:],
                       result.stderr[-2000:])
                )
            self.assertIn("AUDIO-MIX RUN PASSED", result.stdout)


if __name__ == "__main__":
    unittest.main()
