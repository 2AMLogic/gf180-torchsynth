"""Square/saw VCO engine golden-vector flow (issue #74).

Model-level checks run everywhere; the RTL simulation is exercised only
when Icarus Verilog is installed (CI's tb-sim job arbitrates on such a
host; an unrun check is never reported as a pass).
"""

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import golden_vectors as gv  # noqa: E402
from torchsynth_voice import vco2_golden as vc  # noqa: E402
from torchsynth_voice.fixed_voice import AcceptedFormats  # noqa: E402

TOOLS_DIR = ROOT / "tools"
TB = ROOT / "tb" / "run_tb.py"
VECTOR_DIR = ROOT / "sim/reference/square-saw-vco-golden-v1"
FROZEN_PREFIX = "frozen:"

EXPECTED_FROZEN_CASES = {
    "frozen:waveform:vco_2:saw",
    "frozen:boundary:vco_2.mod_depth:upper",
    "frozen:boundary:vco_1.tuning:upper",
    "frozen:boundary:vco_1.mod_depth:upper",
    "frozen:boundary:vco_1.initial_phase:upper",
}

EXPECTED_DEDICATED_CASES = {
    "shape:intermediate-half",
    "shape:intermediate-quarter",
    "shape:three-quarters",
    "shape:lsb-step",
    "shape:lsb-tie-down",
    "shape:lsb-tie-up",
    "shape:one-minus-lsb",
    "mod:depth-max-shape-half",
    "phase:quarter-turn",
    "phase:near-top",
    "phase:near-full-turn",
}

EXPECTED_CASES = EXPECTED_FROZEN_CASES | EXPECTED_DEDICATED_CASES


def has_iverilog() -> bool:
    return shutil.which("iverilog") is not None


def derive(formats, vector):
    """Mirror one case and verify every committed binding."""

    provenance = vector["provenance"]
    derivation = vc.derive_case(formats, vector["parameters"])
    digest = vc.voice_digest(derivation["streams"]["v2"])
    row = provenance["vco_2_raw"]
    assert digest == row["words_sha256"], provenance["case_id"]
    for j_str, word in row["jitter"].items():
        assert derivation["streams"]["v2"][int(j_str)] == word, (
            provenance["case_id"], j_str,
        )
    binding = provenance.get("frozen_binding")
    if binding:
        payload = (ROOT / binding["sidecar"]["file"]).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == binding["sidecar"]["sha256"]
        frozen_words = vc.unpack_words_f32le(payload)
        assert vc.voice_digest(frozen_words) == binding["trace_digest"]
        assert digest == binding["trace_digest"], provenance["case_id"]
    return derivation


class TestCommittedVectors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.formats = AcceptedFormats()
        cls.vectors = {
            path.stem: gv.load_vector(path)
            for path in sorted(VECTOR_DIR.glob("*.json"))
        }

    def test_all_cases_loaded(self):
        self.assertEqual(set(self.vectors), EXPECTED_CASES)

    def test_every_vector_declares_the_shadow_boundary(self):
        for case_id, vector in self.vectors.items():
            text = vector["provenance"]["shadow_boundary"]
            for phrase in (
                "DR-0008/DR-0010",
                "replayed",
                "No RTL transcendental",
            ):
                self.assertIn(phrase, text, case_id)

    def test_frozen_bindings_carry_the_retained_sidecars(self):
        self.assertEqual(
            {name for name in self.vectors if name.startswith(FROZEN_PREFIX)},
            EXPECTED_FROZEN_CASES,
        )
        for case_id in EXPECTED_FROZEN_CASES:
            binding = self.vectors[case_id]["provenance"]["frozen_binding"]
            self.assertTrue(binding["verified"], case_id)
            self.assertTrue(binding["sidecar"]["file"].endswith(".f32le"),
                            case_id)

    def test_model_digest_binds_on_every_case(self):
        """The frozen model is the only executable definition of bits."""

        for case_id, vector in self.vectors.items():
            derive(self.formats, vector)

    def test_selector_regimes_and_half_even_ties_are_covered(self):
        """AC-1's shape regimes: square, saw-boundary, intermediate, ties."""

        shape_words = {}
        for case_id in EXPECTED_DEDICATED_CASES:
            derivation = derive(self.formats, self.vectors[case_id])
            shape_words[case_id] = derivation["words"]["vco_2.shape"]
        self.assertEqual(shape_words["shape:lsb-tie-down"], 0)
        self.assertEqual(shape_words["shape:lsb-step"], 1)
        self.assertEqual(shape_words["shape:lsb-tie-up"], 2)
        self.assertEqual(
            shape_words["shape:one-minus-lsb"], (1 << 21) - 1
        )
        self.assertEqual(
            shape_words["shape:intermediate-half"], 1 << 20
        )
        # The pure square regime comes from the frozen default-shape cases:
        # the committed frozen-binding boundary cases carry shape word 0.
        boundary = derive(
            self.formats, self.vectors["frozen:boundary:vco_2.mod_depth:upper"]
        )
        self.assertEqual(boundary["words"]["vco_2.shape"], 0)
        saw = derive(self.formats, self.vectors["frozen:waveform:vco_2:saw"])
        self.assertEqual(saw["words"]["vco_2.shape"], 1 << 21)

    def test_square_shape_makes_the_tanh_fanout_words_equal(self):
        """At shape word 0 the left branch weight is exactly 1.0."""

        derivation = derive(
            self.formats,
            self.vectors["frozen:boundary:vco_2.mod_depth:upper"],
        )
        self.assertEqual(
            derivation["streams"]["square_q"], derivation["streams"]["left_q"]
        )

    def test_phase_wrap_cases_reach_and_cross_the_top_of_the_circle(self):
        """6.0 rad injects a word just below 2^32; binary32 2*pi (one
        rounding step above the model's binary64 2*pi divisor) wraps past
        the top to a tiny word -- both wrap corners are exact."""

        near_top = derive(self.formats, self.vectors["phase:near-top"])
        self.assertGreater(near_top["init_word"], (1 << 32) - (1 << 28))
        past_top = derive(self.formats,
                          self.vectors["phase:near-full-turn"])
        self.assertLess(past_top["init_word"], 1 << 28)
        self.assertGreater(past_top["init_word"], 0)


class TestGeneratorDeterminism(unittest.TestCase):
    def test_check_mode_passes_against_committed_files(self):
        result = subprocess.run(
            [sys.executable,
             str(TOOLS_DIR / "generate_square_saw_vco_golden.py"), "--check"],
            capture_output=True,
            text=True,
            timeout=2400,
        )
        self.assertEqual(
            result.returncode, 0, result.stdout + "\n" + result.stderr
        )


@unittest.skipUnless(has_iverilog(), "Icarus Verilog is not installed here")
class TestRtlFlow(unittest.TestCase):
    def test_full_vco2_tb_flow(self):
        with tempfile.TemporaryDirectory(prefix="tb-vco2-test-") as tmp:
            result = subprocess.run(
                [sys.executable, str(TB), "vco2", "--workdir", tmp],
                capture_output=True,
                text=True,
                timeout=7200,
            )
        self.assertEqual(
            result.returncode, 0, result.stdout[-4000:] + "\n" + result.stderr[-2000:]
        )
        self.assertIn("SQUARE-SAW VCO RUN PASSED", result.stdout)


if __name__ == "__main__":
    unittest.main()
