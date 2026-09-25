"""Sine VCO engine golden-vector flow (issue #73).

Model-level checks run everywhere; the RTL simulation is exercised only
when Icarus Verilog is installed (CI's tb-sim job arbitrates on such a
host; an unrun check is never reported as a pass).
"""

import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import golden_vectors as gv  # noqa: E402
from torchsynth_voice import mod_matrix_golden as mm  # noqa: E402
from torchsynth_voice import vco_golden as vg  # noqa: E402
from torchsynth_voice.fixed_voice import (  # noqa: E402
    AcceptedFormats,
    entry_quantize,
)
from torchsynth_voice.fixedpoint.counters import StickyCounters  # noqa: E402
from torchsynth_voice.fixedpoint.rounding import RoundingMode  # noqa: E402
from torchsynth_voice.format_sweep import FixedControlPath  # noqa: E402

TB = ROOT / "tb" / "run_tb.py"
GENERATOR = ROOT / "tools" / "generate_sine_vco_golden.py"
RECEIPT_PATH = ROOT / "sim/reference/fixed-voice-golden-v1.json"
SIDECAR_DIR = ROOT / "sim/reference/fixed-voice-golden-v1-traces"
SINE_VECTOR_DIR = ROOT / "sim/reference/sine-vco-golden-v1"
INVENTORY_PATH = ROOT / "spec/reference/parameter-inventory-v1.json"
CONSTANTS_PKG = ROOT / "tb/sv/gf180_rtl_constants_pkg.sv"

#: Committed-parameter cases used by the fast checks (sidecar-backed).
PREFIX_CASE = "boundary:vco_1.tuning:upper"
CLAMP_CASE = "boundary:vco_1.mod_depth:upper"
PHASE_CASE = "boundary:vco_1.initial_phase:upper"
#: The prefix walk used by the fast tests (a fraction of the clip).
PREFIX_SAMPLES = 20000


def has_iverilog() -> bool:
    return shutil.which("iverilog") is not None


class SineVcoGoldenTest(unittest.TestCase):
    """Model-level sine-lane checks against the frozen receipt."""

    @classmethod
    def setUpClass(cls):
        cls.formats = AcceptedFormats()
        cls.fcp = FixedControlPath(cls.formats.control_spec)
        cls.receipt = json.loads(RECEIPT_PATH.read_text(encoding="utf-8"))
        cls.cases = {
            case["id"]: case for case in cls.receipt["cases"]
            if case.get("parameters") is not None
        }

    def entry_word(self, case, name: str) -> int:
        counters = StickyCounters()
        return entry_quantize(
            float(case["parameters"][name]), self.formats.midi, counters,
            "test.entry:" + name,
        )

    def derive(self, case_id: str, samples: int = None):
        """Stimulus + mirror for one case (optionally a prefix walk)."""

        case = self.cases[case_id]
        control_words = self.fcp.render_words(case["parameters"])
        midi_f0_word = control_words["keyboard.midi_f0"][0]
        up_pitch = control_words["control_upsample.vco_1_pitch"]
        init_word = vg.initial_phase_word(
            case["parameters"]["vco_1.initial_phase"],
            self.formats.phase_width,
        )
        mirror, aux = vg.mirror_sine_lane(
            self.formats,
            midi_f0_word,
            self.entry_word(case, "vco_1.tuning"),
            self.entry_word(case, "vco_1.mod_depth"),
            init_word,
            up_pitch,
            samples=samples,
        )
        return mirror, aux, case, init_word

    def sidecar_words(self, case_id: str) -> list:
        return mm.unpack_words_f32le(
            (SIDECAR_DIR / (case_id + ".vco_1.raw.f32le")).read_bytes()
        )

    def test_receipt_carries_the_param_committed_cases(self):
        self.assertEqual(self.receipt["schema"],
                         "gf180-torchsynth/fixed-voice-golden-v1")
        self.assertEqual(len(self.cases), 26)
        for case_id in (PREFIX_CASE, CLAMP_CASE, PHASE_CASE, "source:vco_1"):
            self.assertIn(case_id, self.cases)
        corpus = [case for case in self.receipt["cases"]
                  if case.get("parameters") is None]
        self.assertEqual(len(corpus), 8)
        self.assertTrue(
            str(self.receipt["bindings"]["dr_0008_status"]).startswith(
                "Accepted"
            )
        )

    def test_receipt_bindings_bind_the_live_accepted_contract(self):
        bindings = self.receipt["bindings"]
        self.assertEqual(
            bindings["lut_sha256"], self.formats.table.sha256()
        )
        self.assertEqual(
            bindings["constants_package_sha256"],
            hashlib.sha256(CONSTANTS_PKG.read_bytes()).hexdigest(),
        )

    def test_vco_1_sidecar_custody_binds_the_frozen_digests(self):
        """Every committed vco_1.raw sidecar unpacks to the receipt digest."""

        checked = 0
        for case in self.receipt["cases"]:
            path = SIDECAR_DIR / (case["id"] + ".vco_1.raw.f32le")
            if not path.exists():
                continue
            words = self.sidecar_words(case["id"])
            self.assertEqual(len(words), 176400, case["id"])
            self.assertEqual(vg.digest_words(words),
                             case["traces"]["vco_1.raw"], case["id"])
            checked += 1
        self.assertEqual(checked, 4)

    def test_full_mirror_reproduces_the_frozen_trace_digest(self):
        """The live sine lane re-walk reproduces vco_1.raw bit-exactly."""

        mirror, aux, case, _init = self.derive(PREFIX_CASE)
        self.assertEqual(vg.digest_words(mirror["vco"]),
                         case["traces"]["vco_1.raw"])
        self.assertEqual(mirror["vco"], self.sidecar_words(PREFIX_CASE))
        self.assertEqual(len(mirror["phase"]), 176400)
        self.assertEqual(aux["clamps"], 0)

    def test_prefix_mirror_matches_the_committed_sidecar_prefix(self):
        mirror, _aux, _case, _init = self.derive(
            PREFIX_CASE, samples=PREFIX_SAMPLES
        )
        self.assertEqual(
            mirror["vco"], self.sidecar_words(PREFIX_CASE)[:PREFIX_SAMPLES]
        )

    def test_clamp_case_exercises_the_midi_clamp(self):
        mirror, aux, _case, _init = self.derive(
            CLAMP_CASE, samples=PREFIX_SAMPLES
        )
        self.assertGreater(aux["clamps"], 0)
        self.assertEqual(
            mirror["vco"], self.sidecar_words(CLAMP_CASE)[:PREFIX_SAMPLES]
        )

    def test_initial_phase_word_is_the_half_even_turn_word(self):
        """The injection site: half_even(turns * 2^32) mod 2^32, once."""

        case = self.cases[PHASE_CASE]
        value = float(case["parameters"]["vco_1.initial_phase"])
        word = vg.initial_phase_word(value, self.formats.phase_width)
        modulus = 1 << self.formats.phase_width
        # Independent recomputation: exact fraction of the binary64 value
        # over binary64 pi, half-even rounded by round() (also half-even).
        scaled = (
            Fraction(value) / (2 * Fraction(math.pi)) * modulus
        )
        self.assertEqual(word, int(round(scaled)) % modulus)
        self.assertNotEqual(word, 0)
        self.assertLess(word, modulus)

    def test_first_increment_lands_after_the_injected_initial_phase(self):
        """First-increment-first: phase[0] = init + k(fq[0]), wrapping."""

        mirror, _aux, _case, init_word = self.derive(PHASE_CASE, samples=8)
        modulus = 1 << self.formats.phase_width
        frequency_scale = int(self.formats.frequency.scale)
        denominator = frequency_scale * 44100
        from torchsynth_voice.fixedpoint.rounding import div_round

        k0 = div_round(
            mirror["fq"][0] * modulus, denominator, RoundingMode.HALF_EVEN
        )
        self.assertEqual(mirror["phase"][0], (init_word + k0) % modulus)


class SineVcoDirectedVectorTest(unittest.TestCase):
    """The directed min/mid/max frequency/phase matrix (issue #73 AC-1).

    The frozen receipt pins the sine lane at keyboard MIDI 69 and only at
    the *upper* ends of tuning / mod_depth / initial_phase, so the band
    extrema live in a dedicated committed vector set whose truth is the
    fixed model's own ``vco_1.raw`` render.
    """

    @classmethod
    def setUpClass(cls):
        cls.formats = AcceptedFormats()
        cls.vectors = {
            path.stem: gv.load_vector(path)
            for path in sorted(SINE_VECTOR_DIR.glob("*.json"))
        }
        inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
        cls.bands = {
            entry["name"]: (entry["minimum"], entry["maximum"])
            for entry in inventory["parameters"]
        }

    def regimes(self, kind: str) -> dict:
        return {
            name: vector["provenance"]["regime"]
            for name, vector in self.vectors.items()
            if vector["provenance"].get("regime", {}).get("class") == kind
        }

    def test_directed_matrix_covers_every_min_mid_max_cell(self):
        """3 frequency x 3 phase x {unmodulated, modulated} = 18 cells."""

        cells = {}
        for name, regime in self.regimes("directed-matrix").items():
            cell = (
                regime["frequency"], regime["phase"], regime["modulation"]
            )
            self.assertNotIn(cell, cells, "duplicate cell %r" % (cell,))
            cells[cell] = name
        for freq in ("min", "mid", "max"):
            for phase in ("min", "mid", "max"):
                for mod in ("unmod", "mod"):
                    self.assertIn((freq, phase, mod), cells)
        self.assertEqual(len(cells), 18)

    def test_directed_extrema_are_the_declared_inventory_bands(self):
        """"min"/"max" name the inventory's own band ends, not a guess."""

        f_min, f_max = self.bands["keyboard.midi_f0"]
        p_min, p_max = self.bands["vco_1.initial_phase"]
        seen = set()
        for name, regime in self.regimes("directed-matrix").items():
            params = self.vectors[name]["parameters"]
            midi = float(params["keyboard.midi_f0"])
            phase = float(params["vco_1.initial_phase"])
            expect_midi = {"min": f_min, "max": f_max,
                           "mid": (f_min + f_max) / 2.0}[regime["frequency"]]
            expect_phase = {"min": p_min, "max": p_max,
                            "mid": 0.0}[regime["phase"]]
            self.assertEqual(midi, expect_midi, name)
            self.assertEqual(phase, expect_phase, name)
            if regime["modulation"] == "mod":
                # Full depth, directed into the band: the pitch column is
                # unipolar, so the top frequency cell takes the signed
                # minimum and the others the maximum.
                self.assertIn(
                    float(params["vco_1.mod_depth"]),
                    self.bands["vco_1.mod_depth"], name,
                )
                self.assertEqual(
                    float(params["vco_1.mod_depth"]),
                    self.bands["vco_1.mod_depth"][
                        0 if regime["frequency"] == "max" else 1
                    ],
                    name,
                )
                self.assertNotEqual(
                    float(params["mod_matrix.adsr_2->vco_1_pitch"]), 0.0, name
                )
            else:
                self.assertEqual(float(params["vco_1.mod_depth"]), 0.0, name)
            seen.add(regime["frequency"])
        self.assertEqual(seen, {"min", "mid", "max"})

    def test_every_modulated_cell_differs_from_its_unmodulated_sibling(self):
        """Non-vacuity: a clamp-degenerate "modulated" fixture proves nothing."""

        digests = {}
        for name, regime in self.regimes("directed-matrix").items():
            digests[
                (regime["frequency"], regime["phase"], regime["modulation"])
            ] = self.vectors[name]["provenance"]["vco_1_raw"]["words_sha256"]
        checked = 0
        for (freq, phase, mod), digest in digests.items():
            if mod != "mod":
                continue
            self.assertNotEqual(
                digest, digests[(freq, phase, "unmod")],
                "modulated cell freq=%s phase=%s is trace-identical to its "
                "unmodulated sibling" % (freq, phase),
            )
            checked += 1
        self.assertEqual(checked, 9)

    def test_directed_corners_carry_the_signed_band_minima(self):
        """The receipt has no negative mod_depth or negative tuning."""

        corners = self.regimes("directed-corner")
        self.assertTrue(corners)
        depths = set()
        tunings = set()
        for name in corners:
            params = self.vectors[name]["parameters"]
            depths.add(float(params["vco_1.mod_depth"]))
            tunings.add(float(params["vco_1.tuning"]))
        self.assertIn(self.bands["vco_1.mod_depth"][0], depths)
        self.assertIn(self.bands["vco_1.tuning"][0], tunings)
        self.assertIn(self.bands["vco_1.tuning"][1], tunings)

    def test_static_clamp_corners_clamp_every_sample(self):
        """Both MIDI clamp arms are exercised, measured and committed."""

        low = self.vectors["corner:tuning-min-freq-min"]
        high = self.vectors["corner:tuning-max-freq-max"]
        # midi_f0 0 + tuning -24 is below the band on every sample; 127 +
        # 24 is above it on every sample. Both are static (depth 0), so
        # the clamp fires on all 176,400 samples.
        self.assertEqual(
            low["provenance"]["measured"]["midi_clamps"], 176400
        )
        self.assertEqual(
            high["provenance"]["measured"]["midi_clamps"], 176400
        )

    def test_every_directed_vector_is_hash_linked_to_the_numeric_dr(self):
        """DR-0008 binding: the live LUT-bearing package and register."""

        package_digest = hashlib.sha256(CONSTANTS_PKG.read_bytes()).hexdigest()
        self.assertTrue(self.vectors)
        for name, vector in self.vectors.items():
            bindings = gv.verify_accepted_contract(vector)
            self.assertTrue(bindings, name)
            provenance = vector["provenance"]
            self.assertEqual(
                provenance["dr_0008_constants_package_sha256"],
                package_digest, name,
            )
            self.assertTrue(
                str(provenance["dr_0008_status"]).startswith("Accepted"), name
            )
            self.assertEqual(
                vector["content_hash"], gv.compute_content_hash(vector), name
            )

    def test_frozen_binding_vectors_bind_the_retained_sidecar_bytes(self):
        frozen = [
            (name, vector) for name, vector in self.vectors.items()
            if vector["provenance"].get("frozen_binding")
        ]
        self.assertEqual(len(frozen), 4)
        for name, vector in frozen:
            binding = vector["provenance"]["frozen_binding"]
            payload = (ROOT / binding["sidecar"]["file"]).read_bytes()
            self.assertEqual(
                hashlib.sha256(payload).hexdigest(),
                binding["sidecar"]["sha256"], name,
            )
            words = mm.unpack_words_f32le(payload)
            self.assertEqual(len(words), 176400, name)
            self.assertEqual(
                vg.digest_words(words), binding["trace_digest"], name
            )
            self.assertEqual(
                binding["trace_digest"],
                vector["provenance"]["vco_1_raw"]["words_sha256"], name,
            )

    def test_mirror_reproduces_the_committed_model_render(self):
        """The host mirror re-walk equals the model's own committed bits.

        Two full-clip walks: the (min frequency, min phase) unmodulated
        cell -- the corner the frozen receipt cannot reach at all -- and
        the statically clamped negative-tuning corner.
        """

        for name in ("freq:min-phase:min-unmod", "corner:tuning-min-freq-min"):
            vector = self.vectors[name]
            derivation = vg.derive_case(self.formats, vector["parameters"])
            row = vector["provenance"]["vco_1_raw"]
            words = derivation["streams"]["vco"]
            self.assertEqual(len(words), row["sample_count"], name)
            self.assertEqual(vg.digest_words(words), row["words_sha256"], name)
            for index, word in row["jitter"].items():
                self.assertEqual(words[int(index)], word, "%s[%s]" % (name, index))
            self.assertEqual(
                derivation["clamps"],
                vector["provenance"]["measured"]["midi_clamps"], name,
            )

    def test_negative_initial_phase_injects_a_modular_turn_word(self):
        """-pi is a half turn *below* zero: the turn word wraps modularly.

        The inventory's band end is the **binary32** pi, which is a hair
        below the binary64 pi the model divides by, so the +pi word is
        ``2^31 + 60`` rather than exactly ``2^31`` -- and the -pi word is
        its exact modular negation, ``2^31 - 60``. Asserting the modular
        relation (not a hand-rounded constant) is what pins the wrap.
        """

        width = self.formats.phase_width
        modulus = 1 << width
        minimum = self.bands["vco_1.initial_phase"][0]
        maximum = self.bands["vco_1.initial_phase"][1]
        word = vg.initial_phase_word(minimum, width)
        upper = vg.initial_phase_word(maximum, width)
        self.assertEqual(word, (modulus - upper) % modulus)
        self.assertGreater(word, 0)
        self.assertLess(word, modulus // 2)
        self.assertGreater(upper, modulus // 2)
        # The binary32 pi is one rounding step above half a turn.
        self.assertEqual(upper - modulus // 2, modulus // 2 - word)
        # And the committed min-phase vectors actually carry it.
        for name, regime in self.regimes("directed-matrix").items():
            if regime["phase"] != "min":
                continue
            self.assertEqual(
                self.vectors[name]["provenance"]["static_words"][
                    "init_phase_word"
                ],
                word, name,
            )

    @unittest.skipUnless(
        os.environ.get("GF180_SLOW_TESTS") == "1",
        "slow: re-renders every directed case through the fixed model "
        "(set GF180_SLOW_TESTS=1)",
    )
    def test_generator_regenerates_the_committed_vectors_exactly(self):
        result = subprocess.run(
            [sys.executable, str(GENERATOR), "--check"],
            cwd=ROOT, capture_output=True, text=True, timeout=3600,
        )
        self.assertEqual(
            result.returncode, 0,
            "%s\n%s" % (result.stdout[-4000:], result.stderr[-2000:]),
        )
        self.assertIn("CHECK OK", result.stdout)


class SineVcoRtlTest(unittest.TestCase):
    """The full tb flow (requires Icarus Verilog)."""

    @unittest.skipUnless(has_iverilog(), "Icarus Verilog not installed")
    def test_full_vco_tb_flow(self):
        with tempfile.TemporaryDirectory(prefix="tb-vco-test-") as tmp:
            result = subprocess.run(
                [sys.executable, str(TB), "vco", "--workdir", tmp],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=5400,
            )
            if result.returncode != 0:
                self.fail(
                    "vco tb flow failed (%d):\n%s\n%s"
                    % (result.returncode, result.stdout[-4000:],
                       result.stderr[-2000:])
                )
            self.assertIn("SINE-VCO RUN PASSED", result.stdout)


if __name__ == "__main__":
    unittest.main()
