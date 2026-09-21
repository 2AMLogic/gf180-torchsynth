"""Candidate fixed Voice composition tests (issue #54).

Cheap, bounded checks: accepted-format binding and refusal, the C9
normalization mechanics, checkpoint-set alignment, and the committed
sentinel vector + freeze receipt consistency. No full-clip render runs
here (the generator owns those; its receipts carry the evidence).
"""

import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import golden_vectors as gv  # noqa: E402
from torchsynth_voice.fixed_voice import (  # noqa: E402
    AcceptedFormats,
    normalize_words,
)
from torchsynth_voice.fixedpoint import choices as fp_choices  # noqa: E402
from torchsynth_voice.fixedpoint.counters import StickyCounters  # noqa: E402
from torchsynth_voice.fixedpoint.formats import parse_identity  # noqa: E402

RECEIPT = ROOT / "sim/reference/fixed-voice-golden-v1.json"
SENTINEL = ROOT / "sim/reference/golden-vector-fixed-anchor.json"
TRACES_DIR = ROOT / "sim/reference/fixed-voice-golden-v1-traces"
SWEEP_RECEIPT = ROOT / "sim/candidates/audio-sources-sweep-v1.json"
CONSTANTS_PACKAGE = ROOT / "tb/sv/gf180_rtl_constants_pkg.sv"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestAcceptedFormats(unittest.TestCase):
    def test_accepted_formats_match_register_and_package(self):
        formats = AcceptedFormats()
        self.assertEqual(formats.audio.identity, "Q2.21")
        self.assertEqual(formats.audio.width, 24)
        self.assertEqual(formats.phase_width, 32)
        self.assertEqual(formats.phase_units_per_turn, 2 ** 32)
        self.assertEqual(formats.frequency.identity, "Q16.15")
        self.assertEqual(formats.frequency.width, 32)
        self.assertEqual(formats.midi.identity, "Q10.21")
        self.assertEqual(formats.midi.width, 32)
        self.assertEqual(formats.gain.identity, "U1.22")
        self.assertEqual(formats.gain.width, 23)
        self.assertEqual(formats.reciprocal_frac_bits, 22)
        self.assertEqual(formats.table.spec.n_entries, 4096)
        self.assertEqual(formats.table.spec.entry_format.width, 24)
        self.assertEqual(formats.table.spec.phase_bits, 32)

    def test_lut_table_binds_m2_measured_geometry(self):
        formats = AcceptedFormats()
        sweep = json.loads(SWEEP_RECEIPT.read_bytes())
        recorded = sweep["lut_tables"]["4096"]
        self.assertEqual(formats.table.sha256(), recorded["sha256"])
        self.assertEqual(recorded["entry_format"], "Q1.22")

    def test_model_refuses_not_accepted_register(self):
        payload = fp_choices.load_choices()
        proposed = dict(payload)
        proposed["dr_status"] = "Proposed"
        with patch.object(
            fp_choices, "load_choices", lambda path=None: proposed
        ):
            with self.assertRaises(fp_choices.ChoiceNotAccepted):
                AcceptedFormats()
        selected = dict(payload)
        selected["dr_status"] = "Accepted"
        selected["choices"] = [
            dict(choice, status="selected") for choice in payload["choices"]
        ]
        with patch.object(
            fp_choices, "load_choices", lambda path=None: selected
        ):
            with self.assertRaises(fp_choices.ChoiceNotAccepted):
                AcceptedFormats()


class TestNormalizeWords(unittest.TestCase):
    def test_normalize_words_boundaries(self):
        audio = parse_identity("Q2.21")
        gain = parse_identity("U1.22")
        counters = StickyCounters()
        one = 1 << 21
        mix = [one, -one, 0]
        out, diag = normalize_words(mix, audio, gain, counters)
        self.assertIs(diag["normalized_branch"], False)
        self.assertEqual(diag["gain_word"], 1 << 22)
        self.assertEqual(out, mix)

        peak = one + 1
        mix = [peak, -1, 0]
        out, diag = normalize_words(mix, audio, gain, counters)
        self.assertIs(diag["normalized_branch"], True)
        expected_gain = (1 << (21 + 22)) // peak
        remainder = (1 << 43) - expected_gain * peak
        if remainder * 2 > peak or (remainder * 2 == peak and expected_gain & 1):
            expected_gain += 1
        self.assertEqual(diag["gain_word"], expected_gain)
        self.assertNotEqual(out[0], peak)

    def test_normalize_words_s5_known_answer(self):
        audio = parse_identity("Q2.21")
        gain = parse_identity("U1.22")
        mix = [3 << 20]
        out, diag = normalize_words(mix, audio, gain, StickyCounters())
        self.assertIs(diag["normalized_branch"], True)
        self.assertEqual(diag["gain_word"], 2796203)
        self.assertEqual(out[0], 2097152)


class TestSentinelAndReceipt(unittest.TestCase):
    def test_sentinel_vector_validates_and_binds(self):
        document = gv.load_vector(SENTINEL)
        names = {trace["name"]: trace for trace in document["traces"]}
        self.assertEqual(
            set(names),
            {
                "keyboard.midi_f0",
                "keyboard.duration",
                "mixer.peak",
                "mixer.gain",
                "vco_1.raw",
                "mixer.output",
            },
        )
        for name, trace in names.items():
            if trace["kind"] == "audio":
                self.assertEqual(len(trace["values"]), gv.CANONICAL_SAMPLE_COUNT)
                for value in trace["values"]:
                    self.assertTrue(-(1 << 22) <= value < (1 << 22))
            else:
                self.assertEqual(len(trace["values"]), 1)
        self.assertEqual(
            document["provenance"]["dr_0008_constants_package_sha256"],
            sha256_file(CONSTANTS_PACKAGE),
        )
        self.assertTrue(
            document["provenance"]["dr_0008_status"].startswith("Accepted")
        )

    def test_receipt_consistency(self):
        receipt = json.loads(RECEIPT.read_bytes())
        self.assertEqual(
            receipt["schema"], "gf180-torchsynth/fixed-voice-golden-v1"
        )
        self.assertEqual(receipt["bindings"]["dr_0008_status"], "Accepted")
        self.assertEqual(
            receipt["bindings"]["constants_package_sha256"],
            sha256_file(CONSTANTS_PACKAGE),
        )
        self.assertEqual(
            receipt["custody"]["sentinel_sha256"], sha256_file(SENTINEL)
        )
        pass_count = fail_count = no_verdict = 0
        for case in receipt["cases"]:
            for name, row in case["rows"].items():
                verdict = row["verdict"]
                if "m1" in row:
                    limit = row["m1"]["band_limit"]
                    measured = row["m1"]["measured_max_abs_error"]
                    if limit is None:
                        self.assertEqual(verdict, "NO VERDICT")
                    elif verdict == "PASS":
                        self.assertLessEqual(measured, limit)
                    else:
                        self.assertEqual(verdict, "FAIL")
                        self.assertGreater(measured, limit)
                        self.assertIn("recalibration ledger", row["verdict_reason"])
                elif verdict == "NO VERDICT":
                    self.assertTrue(row.get("verdict_reason"))
                if verdict == "PASS":
                    pass_count += 1
                elif verdict == "FAIL":
                    fail_count += 1
                else:
                    no_verdict += 1
            for trace, entry in case.get("retained_traces", {}).items():
                sidecar = TRACES_DIR / entry["file"]
                self.assertEqual(sha256_file(sidecar), entry["sha256"])
        self.assertEqual(
            receipt["verdict_tally"]["all_rows"]["PASS"], pass_count
        )
        self.assertEqual(
            receipt["verdict_tally"]["all_rows"]["FAIL"], fail_count
        )
        self.assertEqual(
            receipt["verdict_tally"]["all_rows"]["NO VERDICT"], no_verdict
        )

    def test_receipt_vector_manifest_matches_sentinel_words(self):
        receipt = json.loads(RECEIPT.read_bytes())
        document = gv.load_vector(SENTINEL)
        anchor = next(
            case for case in receipt["cases"]
            if case["source"] == "declared-anchor-variant"
        )
        for trace in document["traces"]:
            digest = hashlib.sha256(
                json.dumps(
                    trace["values"], sort_keys=True, separators=(",", ":"),
                    ensure_ascii=True,
                ).encode("utf-8")
            ).hexdigest()
            self.assertEqual(anchor["traces"][trace["name"]], digest)


if __name__ == "__main__":
    unittest.main()
