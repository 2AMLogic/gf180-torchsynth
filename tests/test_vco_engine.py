"""Sine VCO engine golden-vector flow (issue #73).

Model-level checks run everywhere; the RTL simulation is exercised only
when Icarus Verilog is installed (CI's tb-sim job arbitrates on such a
host; an unrun check is never reported as a pass).
"""

import hashlib
import json
import math
import shutil
import subprocess
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

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
RECEIPT_PATH = ROOT / "sim/reference/fixed-voice-golden-v1.json"
SIDECAR_DIR = ROOT / "sim/reference/fixed-voice-golden-v1-traces"
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
