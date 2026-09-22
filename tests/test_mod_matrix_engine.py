"""Modulation-matrix + endpoint-aligned-upsample engine golden-vector flow
(issue #72).

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
from torchsynth_voice import mod_matrix_golden as mm  # noqa: E402
from torchsynth_voice.fixed_voice import AcceptedFormats  # noqa: E402
from torchsynth_voice.format_sweep import (  # noqa: E402
    CONTROL_SAMPLES,
    MOD_MATRIX_INPUTS,
    MOD_MATRIX_OUTPUTS,
    FixedControlPath,
    quantize_params,
)
from torchsynth_voice.fixedpoint.counters import StickyCounters  # noqa: E402

TOOLS_DIR = ROOT / "tools"
TB = ROOT / "tb" / "run_tb.py"
VECTOR_DIR = ROOT / "sim/reference/mod-matrix-golden-v1"
SIDECAR_DIR = ROOT / "sim/reference/mod-matrix-golden-v1-traces"
FROZEN_CASE_ID = "frozen-mod-matrix-receipt"

EXPECTED_CASES = {
    "frozen-mod-matrix-receipt",
    "route-extremes-positive",
    "route-extremes-negative",
    "zero-depth",
    "mixed-sign-routes",
    "shape-sweep-columns",
}

EXPECTED_TRACE_NAMES = ["mod_matrix." + route for route in MOD_MATRIX_OUTPUTS]


def has_iverilog() -> bool:
    return shutil.which("iverilog") is not None


def words_digest(values) -> str:
    blob = json.dumps(
        list(values), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def derive(fcp, formats, vector):
    """Render one case through the model (mirrors asserted against it)."""

    counters = StickyCounters()
    words = quantize_params(vector["parameters"], formats.midi, formats.mode, counters)
    rate_1 = fcp._adsr(words, "lfo_1_rate_adsr.")
    rate_2 = fcp._adsr(words, "lfo_2_rate_adsr.")
    amp_1 = fcp._adsr(words, "lfo_1_amp_adsr.")
    amp_2 = fcp._adsr(words, "lfo_2_amp_adsr.")
    lfo_1 = fcp._lfo(words, "lfo_1.", rate_1)
    lfo_2 = fcp._lfo(words, "lfo_2.", rate_2)
    columns = [
        fcp._adsr(words, "adsr_1."),
        fcp._adsr(words, "adsr_2."),
        fcp._control_vca(lfo_1, amp_1),
        fcp._control_vca(lfo_2, amp_2),
    ]
    matrix, matrix_counters = mm.mirror_mod_matrix(fcp, words, columns)
    audio = {}
    audio_counters = {}
    for route in MOD_MATRIX_OUTPUTS:
        stream, route_counters = mm.mirror_upsample(fcp, matrix[route], route)
        audio[route] = stream
        audio_counters[route] = route_counters
    return words, columns, matrix, audio, matrix_counters, audio_counters


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

    def test_each_vector_carries_the_owned_matrix_traces(self):
        for case_id, vector in self.vectors.items():
            names = [trace["name"] for trace in vector["traces"]]
            self.assertEqual(names, EXPECTED_TRACE_NAMES, case_id)
            for trace in vector["traces"]:
                self.assertEqual(len(trace["values"]), CONTROL_SAMPLES, case_id)

    def test_audio_evidence_binds_the_live_model(self):
        """Digests, endpoints, and jitter words must equal the live model."""

        for case_id, vector in self.vectors.items():
            _words, _columns, matrix, audio, _mc, _ac = derive(
                self.fcp, self.formats, vector
            )
            for route in MOD_MATRIX_OUTPUTS:
                evidence = vector["provenance"]["audio_traces"][
                    "control_upsample." + route
                ]
                stream = audio[route]
                self.assertEqual(len(stream), 176400, (case_id, route))
                self.assertEqual(
                    words_digest(stream), evidence["words_sha256"], (case_id, route)
                )
                self.assertEqual(stream[0], evidence["first_word"], (case_id, route))
                self.assertEqual(stream[-1], evidence["last_word"], (case_id, route))
                for j_str, word in evidence["jitter"].items():
                    self.assertEqual(
                        stream[int(j_str)], word, (case_id, route, j_str)
                    )

    def test_committed_matrix_words_match_the_live_model(self):
        """The frozen model is the only executable definition of bits."""

        for case_id, vector in self.vectors.items():
            _words, _columns, matrix, _audio, _mc, _ac = derive(
                self.fcp, self.formats, vector
            )
            for route in MOD_MATRIX_OUTPUTS:
                committed = next(
                    trace["values"]
                    for trace in vector["traces"]
                    if trace["name"] == "mod_matrix." + route
                )
                self.assertEqual(committed, matrix[route], "%s/%s" % (case_id, route))

    def test_endpoint_contract_holds_on_every_case(self):
        """j=0 and j=176399 are exact copies of control indices 0 and 1763."""

        for case_id in EXPECTED_CASES:
            vector = self.vectors[case_id]
            _words, _columns, matrix, audio, _mc, _ac = derive(
                self.fcp, self.formats, vector
            )
            for route in MOD_MATRIX_OUTPUTS:
                self.assertEqual(audio[route][0], matrix[route][0], (case_id, route))
                self.assertEqual(
                    audio[route][-1], matrix[route][-1], (case_id, route)
                )

    def test_route_extremes_exercise_both_signs_and_the_no_clamp_range(self):
        """The extremes cases push matrix outputs beyond +-1.0 in both signs.

        With all twenty depths at +1.0 the envelope columns (nonnegative)
        plus the signed LFO columns drive outputs past +1.0; at -1.0 they
        drive past -1.0. The declared no-clamp matrix output spans both —
        which is exactly why the upstream +-1.0 clamp mutation must fail.
        """

        positive = [w for route in MOD_MATRIX_OUTPUTS
                    for w in self._matrix_of("route-extremes-positive")[route]]
        negative = [w for route in MOD_MATRIX_OUTPUTS
                    for w in self._matrix_of("route-extremes-negative")[route]]
        self.assertTrue(any(w > (1 << 21) for w in positive))
        self.assertTrue(any(w < -(1 << 21) for w in negative))

    def _matrix_of(self, case_id):
        vector = self.vectors[case_id]
        _words, _columns, matrix, _audio, _mc, _ac = derive(
            self.fcp, self.formats, vector
        )
        return matrix

    def test_zero_depth_case_is_exactly_zero(self):
        vector = self.vectors["zero-depth"]
        _words, _columns, matrix, audio, _mc, _ac = derive(
            self.fcp, self.formats, vector
        )
        for route in MOD_MATRIX_OUTPUTS:
            self.assertFalse(any(matrix[route]), route)
            self.assertFalse(any(audio[route]), route)

    def test_frozen_case_matches_the_frozen_receipt_digests(self):
        vector = self.vectors[FROZEN_CASE_ID]
        binding = vector["provenance"]["frozen_receipt_binding"]
        self.assertTrue(binding["verified"])
        self.assertEqual(binding["case_id"], "boundary:lfo_1.mod_depth:center")
        for name in EXPECTED_TRACE_NAMES:
            declared = binding["trace_digests"][name]
            if name.startswith("mod_matrix."):
                route = name.split(".", 1)[1]
                values = next(
                    trace["values"] for trace in vector["traces"] if trace["name"] == name
                )
            else:
                route = name.split(".", 1)[1]
                values = self._receipt_audio(route)
            self.assertEqual(words_digest(values), declared, name)

    def _receipt_audio(self, route):
        vector = self.vectors[FROZEN_CASE_ID]
        row = vector["provenance"]["audio_traces"]["control_upsample." + route]["sidecar"]
        payload = (ROOT / "sim" / "reference" / row["file"]).read_bytes()
        self.assertEqual(hashlib.sha256(payload).hexdigest(), row["sha256"], route)
        return mm.unpack_words_f32le(payload)

    def test_host_mirrors_are_row_equal_to_the_model(self):
        """The integer mirror re-walks are the model, not parallel copies."""

        for case_id, vector in self.vectors.items():
            _words, columns, matrix, audio, _mc, _ac = derive(
                self.fcp, self.formats, vector
            )
            self.assertEqual(
                self.fcp._mod_matrix(
                    quantize_params(
                        vector["parameters"],
                        self.formats.midi,
                        self.formats.mode,
                        StickyCounters(),
                    ),
                    columns,
                ),
                matrix,
                case_id,
            )
            for route in MOD_MATRIX_OUTPUTS:
                self.assertEqual(
                    self.fcp._upsample(matrix[route], mm.route_format(self.fcp, route)),
                    audio[route],
                    (case_id, route),
                )

    def test_sidecar_bytes_round_trip_word_exactly(self):
        names = sorted(p.name for p in SIDECAR_DIR.glob("*.f32le"))
        self.assertEqual(len(names), 5)
        for name in names:
            payload = (SIDECAR_DIR / name).read_bytes()
            words = mm.unpack_words_f32le(payload)
            repacked = mm.pack_words_f32le(words)
            self.assertEqual(repacked, payload, name)
            self.assertEqual(len(words), 176400, name)


class TestGeneratorDeterminism(unittest.TestCase):
    def test_check_mode_passes_against_committed_files(self):
        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "generate_mod_matrix_golden.py"), "--check"],
            capture_output=True,
            text=True,
            timeout=900,
        )
        self.assertEqual(
            result.returncode, 0, result.stdout + "\n" + result.stderr
        )


@unittest.skipUnless(has_iverilog(), "Icarus Verilog is not installed here")
class TestRtlFlow(unittest.TestCase):
    def test_full_modmatrix_tb_flow(self):
        with tempfile.TemporaryDirectory(prefix="tb-modmatrix-test-") as tmp:
            result = subprocess.run(
                [sys.executable, str(TB), "modmatrix", "--workdir", tmp],
                capture_output=True,
                text=True,
                timeout=3600,
            )
        self.assertEqual(
            result.returncode, 0, result.stdout[-4000:] + "\n" + result.stderr[-2000:]
        )
        self.assertIn("MOD-MATRIX RUN PASSED", result.stdout)


if __name__ == "__main__":
    unittest.main()
