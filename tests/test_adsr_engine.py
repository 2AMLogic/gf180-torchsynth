"""ADSR engine golden-vector flow (issue #70).

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
from torchsynth_voice.fixed_voice import AcceptedFormats  # noqa: E402
from torchsynth_voice.format_sweep import FixedControlPath  # noqa: E402
from torchsynth_voice.fixedpoint.counters import StickyCounters  # noqa: E402

TOOLS_DIR = ROOT / "tools"
TB = ROOT / "tb" / "run_tb.py"
VECTOR_DIR = ROOT / "sim/reference/adsr-golden-v1"
FROZEN_CASE_ID = "frozen-envelope-receipt"


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

    def test_all_six_cases_loaded(self):
        expected = {
            "frozen-envelope-receipt",
            "attack-sweep",
            "decay-release-sweep",
            "sustain-alpha-sweep",
            "boundary-tie",
            "min-max-degenerate",
        }
        self.assertEqual(set(self.vectors), expected)

    def test_each_vector_carries_the_six_envelope_traces(self):
        for case_id, vector in self.vectors.items():
            names = [trace["name"] for trace in vector["traces"]]
            self.assertEqual(
                names,
                [ag.trace_name(prefix) for prefix in ag.ADSR_PREFIXES],
                case_id,
            )
            for trace in vector["traces"]:
                self.assertEqual(len(trace["values"]), 1764, case_id)

    def test_committed_words_match_the_live_model(self):
        """The frozen model is the only executable definition of bits."""

        for case_id, vector in self.vectors.items():
            counters = StickyCounters()
            words = ag.quantize_entries(
                vector["parameters"], self.formats.midi, self.formats.mode, counters
            )
            for prefix in ag.ADSR_PREFIXES:
                live = self.fcp._adsr(words, prefix)
                committed = next(
                    trace["values"]
                    for trace in vector["traces"]
                    if trace["name"] == ag.trace_name(prefix)
                )
                self.assertEqual(committed, live, "%s/%s" % (case_id, prefix))

    def test_frozen_case_matches_the_frozen_receipt_digests(self):
        import hashlib
        import json

        vector = self.vectors[FROZEN_CASE_ID]
        binding = vector["provenance"]["frozen_receipt_binding"]
        self.assertTrue(binding["verified"])
        self.assertEqual(binding["case_id"], "boundary:adsr_1.alpha:lower")
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

    def test_host_mirror_ramp_equals_the_model(self):
        for case_id, vector in self.vectors.items():
            counters = StickyCounters()
            words = ag.quantize_entries(
                vector["parameters"], self.formats.midi, self.formats.mode, counters
            )
            for prefix in ag.ADSR_PREFIXES:
                formation = ag.derive_formation(self.fcp, words, prefix)
                for stage in ("attack", "decay", "release"):
                    start_q = (
                        formation["attack_q"] if stage == "decay"
                        else (formation["duration_q"] if stage == "release" else 0)
                    )
                    _values, shape_row = ag.mirror_ramp(
                        self.fcp,
                        formation[stage + "_q"],
                        formation[stage + "_exact"],
                        start_q,
                        stage != "attack",
                        formation["alpha"],
                    )
                    self.assertEqual(
                        shape_row,
                        self.fcp._ramp(
                            formation[stage + "_q"],
                            formation[stage + "_exact"],
                            start_q,
                            stage != "attack",
                            formation["alpha"],
                        ),
                        "%s/%s/%s" % (case_id, prefix, stage),
                    )


class TestGeneratorDeterminism(unittest.TestCase):
    def test_check_mode_passes_against_committed_files(self):
        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "generate_adsr_golden.py"), "check"],
            capture_output=True,
            text=True,
            timeout=600,
        )
        self.assertEqual(
            result.returncode, 0, result.stdout + "\n" + result.stderr
        )


@unittest.skipUnless(has_iverilog(), "Icarus Verilog is not installed here")
class TestRtlFlow(unittest.TestCase):
    def test_full_adsr_tb_flow(self):
        with tempfile.TemporaryDirectory(prefix="tb-adsr-test-") as tmp:
            result = subprocess.run(
                [sys.executable, str(TB), "adsr", "--workdir", tmp],
                capture_output=True,
                text=True,
                timeout=600,
            )
        self.assertEqual(
            result.returncode, 0, result.stdout[-4000:] + "\n" + result.stderr[-2000:]
        )
        self.assertIn("ADSR RUN PASSED", result.stdout)


if __name__ == "__main__":
    unittest.main()
