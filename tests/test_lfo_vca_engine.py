"""LFO + control-VCA engine golden-vector flow (issue #71).

Model-level checks run everywhere; the RTL simulation is exercised only
when Icarus Verilog is installed (CI's tb-sim job arbitrates on such a
host; an unrun check is never reported as a pass).
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import adsr_golden as ag  # noqa: E402
from torchsynth_voice import golden_vectors as gv  # noqa: E402
from torchsynth_voice import lfo_golden as lg  # noqa: E402
from torchsynth_voice.fixed_voice import AcceptedFormats  # noqa: E402
from torchsynth_voice.format_sweep import FixedControlPath  # noqa: E402
from torchsynth_voice.fixedpoint.counters import StickyCounters  # noqa: E402

TOOLS_DIR = ROOT / "tools"
TB = ROOT / "tb" / "run_tb.py"
VECTOR_DIR = ROOT / "sim/reference/lfo-vca-golden-v1"
FROZEN_CASE_ID = "frozen-lfo-receipt"

EXPECTED_CASES = {
    "frozen-lfo-receipt",
    "shape-single-sweep",
    "shape-second-sweep",
    "shape-square-equal-blend",
    "shape-blend-boundary",
    "shape-blend-mixed",
    "shape-rsaw-dominant",
    "depth-extremes",
    "depth-negative-clamp",
    "frequency-phase-extremes",
    "phase-boundary",
    "rate-amp-modulation",
    "min-max-degenerate",
}


def has_iverilog() -> bool:
    return shutil.which("iverilog") is not None


class TestCommittedVectors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.formats = AcceptedFormats()
        cls.fcp = FixedControlPath(cls.formats.control_spec)
        cls.vectors = {
            path.stem: gv.load_vector(path)
            for path in sorted(VECTOR_DIR.glob("*.json"))
        }

    def test_all_cases_loaded(self):
        self.assertEqual(set(self.vectors), EXPECTED_CASES)

    def test_each_vector_carries_the_owned_traces(self):
        expected_names = []
        for side in lg.LFO_SIDES:
            expected_names.append(lg.raw_trace(side))
            expected_names.append(lg.vca_trace(side))
        for case_id, vector in self.vectors.items():
            names = [trace["name"] for trace in vector["traces"]]
            self.assertEqual(names, expected_names, case_id)
            for trace in vector["traces"]:
                self.assertEqual(len(trace["values"]), 1764, case_id)

    def test_committed_words_match_the_live_model(self):
        """The frozen model is the only executable definition of bits."""

        for case_id, vector in self.vectors.items():
            counters = StickyCounters()
            words = ag.quantize_entries(
                vector["parameters"], self.formats.midi, self.formats.mode, counters
            )
            for side in lg.LFO_SIDES:
                rate_env = self.fcp._adsr(words, side[:-1] + "_rate_adsr.")
                amp_env = self.fcp._adsr(words, side[:-1] + "_amp_adsr.")
                raw = self.fcp._lfo(words, side, rate_env)
                post = self.fcp._control_vca(raw, amp_env)
                for trace_name, live in (
                    (lg.raw_trace(side), raw),
                    (lg.vca_trace(side), post),
                ):
                    committed = next(
                        trace["values"]
                        for trace in vector["traces"]
                        if trace["name"] == trace_name
                    )
                    self.assertEqual(
                        committed, live, "%s/%s" % (case_id, trace_name)
                    )

    def test_frozen_case_matches_the_frozen_receipt_digests(self):
        import hashlib
        import json

        vector = self.vectors[FROZEN_CASE_ID]
        binding = vector["provenance"]["frozen_receipt_binding"]
        self.assertTrue(binding["verified"])
        self.assertEqual(binding["case_id"], "boundary:lfo_1.mod_depth:center")
        for trace in vector["traces"]:
            declared = binding["trace_digests"][trace["name"]]
            blob = json.dumps(
                trace["values"],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
            self.assertEqual(
                hashlib.sha256(blob).hexdigest(),
                declared,
                trace["name"],
            )

    def test_host_mirror_equals_the_model(self):
        """The weight/init shadow replay and integer mirror are the model."""

        for case_id, vector in self.vectors.items():
            counters = StickyCounters()
            words = ag.quantize_entries(
                vector["parameters"], self.formats.midi, self.formats.mode, counters
            )
            for side in lg.LFO_SIDES:
                rate_env = self.fcp._adsr(words, side[:-1] + "_rate_adsr.")
                weight_q = lg.weight_shadow(self.fcp, words, side)
                mirror, clamps = lg.mirror_lfo(
                    self.fcp, words, side, rate_env, weight_q
                )
                self.assertEqual(
                    mirror, self.fcp._lfo(words, side, rate_env), case_id
                )
                self.assertIsInstance(clamps, int)
                amp_env = self.fcp._adsr(words, side[:-1] + "_amp_adsr.")
                raw = self.fcp._lfo(words, side, rate_env)
                self.assertEqual(
                    lg.mirror_vca(self.fcp, raw, amp_env),
                    self.fcp._control_vca(raw, amp_env),
                    case_id,
                )
                self.assertEqual(
                    lg.init_word(self.fcp, words, side),
                    self._model_init_word(words, side),
                    case_id,
                )

    def _model_init_word(self, words, side):
        from fractions import Fraction

        from torchsynth_voice.format_sweep import LUT_PHASE_BITS
        from torchsynth_voice.control_path import PINNED_TWO_PI
        from torchsynth_voice.fixedpoint.rounding import div_round_reported

        initial_turns = self.fcp._entry(words, side + "initial_phase") / Fraction(
            PINNED_TWO_PI
        )
        word, _rounded = div_round_reported(
            initial_turns.numerator * (1 << LUT_PHASE_BITS),
            initial_turns.denominator,
            self.fcp.mode,
        )
        return word % (1 << LUT_PHASE_BITS)

    def test_all_zero_shape_weights_are_refused_not_repaired(self):
        """The declared undefined state raises; it is never silently fixed."""

        counters = StickyCounters()
        physical = {
            "keyboard.duration": 4.0,
            "lfo_1.frequency": 2.0,
            "lfo_1.mod_depth": 0.0,
            "lfo_1.initial_phase": 0.0,
        }
        for side in lg.LFO_SIDES:
            for shape in ("sin", "tri", "saw", "rsaw", "sqr"):
                physical[side + shape] = 0.0
        physical["lfo_2.frequency"] = 2.0
        physical["lfo_2.mod_depth"] = 0.0
        physical["lfo_2.initial_phase"] = 0.0
        words = ag.quantize_entries(
            physical, self.formats.midi, self.formats.mode, counters
        )
        with self.assertRaises(lg.UndefinedLFOState):
            lg.weight_shadow(self.fcp, words, "lfo_1.")

    def test_rate_clamp_case_actually_clamps(self):
        """The zero-clamp coverage case exercises the declared clamp."""

        vector = self.vectors["depth-negative-clamp"]
        counters = StickyCounters()
        words = ag.quantize_entries(
            vector["parameters"], self.formats.midi, self.formats.mode, counters
        )
        clamped = 0
        for side in lg.LFO_SIDES:
            rate_env = self.fcp._adsr(words, side[:-1] + "_rate_adsr.")
            weight_q = lg.weight_shadow(self.fcp, words, side)
            _mirror, clamps = lg.mirror_lfo(self.fcp, words, side, rate_env, weight_q)
            clamped += clamps
        self.assertGreater(clamped, 0)


class TestGeneratorDeterminism(unittest.TestCase):
    def test_check_mode_passes_against_committed_files(self):
        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "generate_lfo_vca_golden.py"), "check"],
            capture_output=True,
            text=True,
            timeout=600,
        )
        self.assertEqual(
            result.returncode, 0, result.stdout + "\n" + result.stderr
        )


@unittest.skipUnless(has_iverilog(), "Icarus Verilog is not installed here")
class TestRtlFlow(unittest.TestCase):
    def test_full_lfo_tb_flow(self):
        with tempfile.TemporaryDirectory(prefix="tb-lfo-test-") as tmp:
            result = subprocess.run(
                [sys.executable, str(TB), "lfo", "--workdir", tmp],
                capture_output=True,
                text=True,
                timeout=600,
            )
        self.assertEqual(
            result.returncode, 0, result.stdout[-4000:] + "\n" + result.stderr[-2000:]
        )
        self.assertIn("LFO RUN PASSED", result.stdout)


if __name__ == "__main__":
    unittest.main()
