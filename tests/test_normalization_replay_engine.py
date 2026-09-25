"""Normalization replay controller + one-shot top host mirror (issue #77).

Model-level checks run everywhere; the RTL simulation
(``tb/sv/normalization_replay_engine.sv``, driven by ``tb/run_tb.py
normreplay``) is exercised only when Icarus Verilog is installed (CI's
tb-sim job arbitrates on such a host; an unrun check is never reported as
a pass).

:mod:`torchsynth_voice.normalization_replay_golden` adds no reimplemented
normalization algorithm — :func:`mirror_normalize` is
``fixed_voice.normalize_words`` itself — so this suite's job is to prove
the *other* additions: the full-voice case regeneration binds exactly to
the frozen ``fixed-voice-golden-v1`` receipt, and the RTL flow (exercised
below when available) reproduces the mirror sample-exactly.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import normalization_replay as nr  # noqa: E402
from torchsynth_voice import normalization_replay_golden as nrg  # noqa: E402
from torchsynth_voice.fixed_voice import AcceptedFormats, normalize_words  # noqa: E402
from torchsynth_voice.fixedpoint.counters import StickyCounters  # noqa: E402

TB = ROOT / "tb" / "run_tb.py"
RECEIPT_PATH = ROOT / "sim/reference/fixed-voice-golden-v1.json"


def has_iverilog() -> bool:
    return shutil.which("iverilog") is not None


class MirrorIsNormalizeWords(unittest.TestCase):
    """``mirror_normalize`` calls ``normalize_words`` verbatim -- no drift."""

    def test_matches_normalize_words_on_directed_cases(self):
        formats = AcceptedFormats()
        for label, clip in sorted(nr.directed_cases().items()):
            with self.subTest(case=label):
                mirror_out, mirror_diag = nrg.mirror_normalize(clip, formats)
                direct_out, direct_diag = normalize_words(
                    list(clip), formats.audio, formats.gain, StickyCounters()
                )
                self.assertEqual(mirror_out, direct_out)
                self.assertEqual(mirror_diag, direct_diag)

    def test_default_counters_are_optional(self):
        formats = AcceptedFormats()
        clip = nr.directed_cases()["fixed:silence"]
        out, diag = nrg.mirror_normalize(clip, formats)
        self.assertEqual(out, clip)
        self.assertEqual(diag["normalized_branch"], False)
        self.assertEqual(diag["peak_word"], 0)


class DigestConvention(unittest.TestCase):
    """``digest_words`` matches the #54 golden-receipt trace-digest form."""

    def test_matches_a_known_receipt_trace_digest(self):
        receipt = json.loads(RECEIPT_PATH.read_bytes())
        formats = AcceptedFormats()
        fixed, _diag = nrg.resolve_full_voice_case("normalization:tie", formats)
        want = next(
            c for c in receipt["cases"] if c["id"] == "normalization:tie"
        )["traces"]["mixer.output"]
        self.assertEqual(nrg.digest_words(fixed["mixer.output"]), want)


class FullVoiceCaseResolution(unittest.TestCase):
    """Every normalization-family case regenerates byte-identically."""

    @classmethod
    def setUpClass(cls):
        cls.formats = AcceptedFormats()
        cls.receipt = json.loads(RECEIPT_PATH.read_bytes())
        cls.receipt_cases = {c["id"]: c for c in cls.receipt["cases"]}

    def test_every_full_voice_case_binds_to_the_frozen_receipt(self):
        for case_id in nrg.FULL_VOICE_CASES:
            with self.subTest(case=case_id):
                fixed, _diag = nrg.resolve_full_voice_case(case_id, self.formats)
                want = self.receipt_cases[case_id]
                for trace in (
                    "mixer.pre_normalization", "mixer.peak", "mixer.gain",
                    "mixer.output",
                ):
                    self.assertEqual(
                        nrg.digest_words(fixed[trace]), want["traces"][trace],
                        "%s trace %s diverged from the frozen receipt"
                        % (case_id, trace),
                    )

    def test_diagnostics_agree_with_the_receipts_branch_block(self):
        # The frozen receipt records each case's fixed-model branch
        # decision directly (branch.fixed); resolve_full_voice_case's own
        # diagnostics must agree with it exactly.
        for case_id in nrg.FULL_VOICE_CASES:
            with self.subTest(case=case_id):
                _fixed, diag = nrg.resolve_full_voice_case(case_id, self.formats)
                want_branch = self.receipt_cases[case_id]["branch"]["fixed"]
                self.assertEqual(diag["normalized_branch"], want_branch)

    def test_unknown_case_id_refuses(self):
        with self.assertRaises(nrg.NormalizationReplayGoldenError):
            nrg.resolve_full_voice_case("not-a-normalization-case", self.formats)

    def test_mixer_pre_normalization_is_the_declared_rtl_input(self):
        # AC1's fixture-mapping source 1: mixer.pre_normalization is this
        # module's own declared input interface (DR-0010's mixer-output
        # interface), independent of issue #76's RTL landing.
        fixed, _diag = nrg.resolve_full_voice_case(
            "normalization:above", self.formats
        )
        self.assertEqual(len(fixed["mixer.pre_normalization"]), 176400)
        self.assertTrue(
            all(self.formats.audio.contains(v)
                for v in fixed["mixer.pre_normalization"])
        )


@unittest.skipUnless(has_iverilog(), "Icarus Verilog is not installed here")
class TestRtlFlow(unittest.TestCase):
    def test_full_normreplay_tb_flow(self):
        with tempfile.TemporaryDirectory(prefix="tb-normreplay-test-") as tmp:
            result = subprocess.run(
                [sys.executable, str(TB), "normreplay", "--workdir", tmp],
                capture_output=True,
                text=True,
                timeout=7200,
            )
        self.assertEqual(
            result.returncode, 0,
            result.stdout[-4000:] + "\n" + result.stderr[-2000:],
        )
        self.assertIn("NORMREPLAY RUN PASSED", result.stdout)


if __name__ == "__main__":
    unittest.main()
