"""Issue #278: ``anchor``/``lfo`` lanes are independent of workdir path length.

Both DUTs read their table through ``reg [1023:0] lut_file`` (128
characters). Passing an absolute ``+lut=`` path longer than that buffer
truncates the plusarg, so ``$readmemh`` cannot open the file and the ROM
stays ``x``. These regressions run the real simulator in deep workdirs
(absolute LUT path > 128 characters, asserted) and in deep directories
containing spaces, and require exact capture lengths and word identity
against the model-derived expectations.

The RTL simulation runs only when Icarus Verilog is installed; otherwise
every test here is reported as a skip (never as a pass).
"""

import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import golden_vectors as gv  # noqa: E402
from torchsynth_voice.fixed_voice import AcceptedFormats  # noqa: E402
from torchsynth_voice.format_sweep import FixedControlPath  # noqa: E402

#: Character width of both DUTs' ``lut_file`` plusarg buffer
#: (``reg [1023:0]`` in tb/sv/lut_sine_dut.sv and tb/sv/lfo_vca_engine.sv).
LUT_PLUSARG_CHARS = 1024 // 8

#: Short anchor walk: enough samples to exercise the table, cheap to run.
ANCHOR_WALK = 256

#: One existing directed LFO vector (the full control-rate walk is used).
LFO_CASE = "shape-blend-mixed"

#: A directory component kept well within filesystem name limits.
DEEP_COMPONENT = "d" * 60
SPACED_COMPONENT = "deep dir with spaces " + "s" * 40


def has_iverilog() -> bool:
    return (shutil.which("iverilog") is not None
            and shutil.which("vvp") is not None)


def load_run_tb():
    if str(ROOT / "tb") not in sys.path:
        sys.path.insert(0, str(ROOT / "tb"))
    import run_tb  # noqa: E402

    return run_tb


def deep_dir(base: Path, component: str, leaf: str) -> Path:
    """An absolute nested workdir (two long components plus a leaf)."""

    return base.resolve() / component / component / leaf


class _LutPathMixin:
    def assert_overflows_buffer(self, lut_path: Path):
        self.assertTrue(lut_path.is_absolute())
        self.assertGreater(len(str(lut_path)), LUT_PLUSARG_CHARS)
        for part in lut_path.parts:
            self.assertLessEqual(len(part), 255)


class AnchorLutPathRtlTest(_LutPathMixin, unittest.TestCase):
    """``simulate_anchor`` reproduces the mirror wherever the workdir lives."""

    @classmethod
    def setUpClass(cls):
        if not has_iverilog():
            raise unittest.SkipTest("Icarus Verilog not installed")
        cls.rt = load_run_tb()
        cls.formats = AcceptedFormats()
        vector = gv.load_vector(gv.SENTINEL_VECTOR_PATH)
        cls.phase_step, cls.level_word = cls.rt.derive_anchor_control_words(
            vector, cls.formats)
        cls.mirror_vco, cls.mirror_mix = cls.rt.anchor_mirror(
            cls.formats, cls.phase_step, cls.level_word, ANCHOR_WALK)

    def simulate_in(self, workdir: Path, expect_overflow: bool):
        workdir.mkdir(parents=True, exist_ok=True)
        lut_memh = workdir / "lut_quarter_cos.memh"
        self.rt.write_lut_memh(self.formats.table, lut_memh)
        if expect_overflow:
            self.assert_overflows_buffer(lut_memh)
        with contextlib.redirect_stdout(io.StringIO()):
            return self.rt.simulate_anchor(
                workdir, "iverilog", self.phase_step, self.level_word,
                ANCHOR_WALK, lut_memh)

    def assert_exact(self, result):
        vco, mix, _cycles = result
        self.assertEqual(len(vco), ANCHOR_WALK)
        self.assertEqual(len(mix), ANCHOR_WALK)
        self.assertEqual(vco, self.mirror_vco)
        self.assertEqual(mix, self.mirror_mix)

    def test_short_workdir(self):
        with tempfile.TemporaryDirectory(prefix="tb-anc-") as tmp:
            self.assert_exact(
                self.simulate_in(Path(tmp).resolve() / "c", False))

    def test_lut_path_longer_than_the_plusarg_buffer(self):
        with tempfile.TemporaryDirectory(prefix="tb-anc-long-") as tmp:
            workdir = deep_dir(Path(tmp), DEEP_COMPONENT, "anchor-case")
            self.assert_exact(self.simulate_in(workdir, True))

    def test_deep_workdir_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix="tb anc space ") as tmp:
            workdir = deep_dir(Path(tmp), SPACED_COMPONENT, "anchor case")
            self.assertIn(" ", str(workdir))
            self.assert_exact(self.simulate_in(workdir, True))


class LfoLutPathRtlTest(_LutPathMixin, unittest.TestCase):
    """``lfo_simulate`` reproduces both instances' raw/post-VCA words."""

    @classmethod
    def setUpClass(cls):
        if not has_iverilog():
            raise unittest.SkipTest("Icarus Verilog not installed")
        cls.rt = load_run_tb()
        formats = AcceptedFormats()
        cls.fcp = FixedControlPath(formats.control_spec)
        vector = gv.load_vector(cls.rt.LFO_VECTOR_DIR / (LFO_CASE + ".json"))
        cls.cases = cls.rt.lfo_derive_case(cls.fcp, formats, vector)

    def simulate_in(self, workdir: Path, expect_overflow: bool):
        workdir.mkdir(parents=True, exist_ok=True)
        lut_memh = workdir / "lut.memh"
        self.rt.write_lut_memh(self.fcp.table, lut_memh)
        if expect_overflow:
            self.assert_overflows_buffer(lut_memh)
        sides = self.rt.lfo_write_case(workdir, 0, self.cases)
        with contextlib.redirect_stdout(io.StringIO()):
            captures = self.rt.lfo_simulate(
                workdir, "iverilog", 1, self.rt.LFO_DUT_SV)
        return sides, captures

    def assert_exact(self, result):
        sides, captures = result
        self.assertEqual(len(captures), 1)
        self.assertEqual(len(captures[0]), 2)
        self.assertEqual(len(sides), 2)
        for index, side in enumerate(sides):
            pairs = captures[0][index]
            expected = self.cases[side]
            self.assertEqual(len(pairs), gv.CANONICAL_CONTROL_COUNT, side)
            self.assertEqual([raw for raw, _ in pairs], expected["raw"], side)
            self.assertEqual([post for _, post in pairs], expected["post"],
                             side)

    def test_short_workdir(self):
        with tempfile.TemporaryDirectory(prefix="tb-lfo-") as tmp:
            self.assert_exact(
                self.simulate_in(Path(tmp).resolve() / "c", False))

    def test_lut_path_longer_than_the_plusarg_buffer(self):
        with tempfile.TemporaryDirectory(prefix="tb-lfo-long-") as tmp:
            workdir = deep_dir(Path(tmp), DEEP_COMPONENT, "case-" + LFO_CASE)
            self.assert_exact(self.simulate_in(workdir, True))

    def test_deep_workdir_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix="tb lfo space ") as tmp:
            workdir = deep_dir(Path(tmp), SPACED_COMPONENT,
                               "case " + LFO_CASE)
            self.assertIn(" ", str(workdir))
            self.assert_exact(self.simulate_in(workdir, True))


if __name__ == "__main__":
    unittest.main()
