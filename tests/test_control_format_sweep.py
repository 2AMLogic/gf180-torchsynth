"""Bounded tests for the control-path fixed-format sweep (issue #50).

Fast, deterministic checks of the sweep machinery's declared invariants.
The full sweep run is evidence (``sim/reference/control-format-sweep-v1.json``),
not a test job; the smoke subprocess test only proves the driver runs and
writes a self-consistent document.
"""

import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import unittest

from torchsynth_voice import control_path as float_cp
from torchsynth_voice.format_sweep import (
    SHAPE_FORMAT,
    SHAPE_ONE,
    CandidateSpec,
    FixedControlPath,
    SweepError,
    UndefinedControlState,
    consumed_parameter_names,
    quantize_params,
    quarter_wave_table,
)
from torchsynth_voice.fixedpoint.counters import SATURATION, StickyCounters
from torchsynth_voice.fixedpoint.formats import parse_identity
from torchsynth_voice.fixedpoint.rounding import RoundingMode

DIRECTED = json.loads((ROOT / "spec" / "reference" / "directed-voice-v1.json").read_text())
BASE_PHYSICAL = {k: v["physical"] for k, v in DIRECTED["base"].items()}
BASE_NORMALIZED = {k: v["normalized"] for k, v in DIRECTED["base"].items()}


def baseline_spec(**overrides):
    spec = CandidateSpec(
        midi_format=parse_identity("Q10.21"),
        ctrl_format=parse_identity("Q2.21"),
        pitch_format=parse_identity("Q2.21"),
        up_frac_bits=31,
        lut_entries=4096,
        mode=RoundingMode.HALF_EVEN,
    )
    if overrides:
        from dataclasses import replace

        spec = replace(spec, **overrides)
    return spec


class TestCandidateGrid(unittest.TestCase):
    def test_baseline_identity_is_stable(self):
        spec = baseline_spec()
        self.assertEqual(
            spec.identity,
            "midi=Q10.21 ctrl=Q2.21 pitch=Q2.21 upfrac=uQ.31 lut=4096x24"
            " mode=half_even",
        )

    def test_sweep_grid_is_reproducible_and_unique(self):
        sys.path.insert(0, str(ROOT / "tools"))
        try:
            import importlib

            tools = importlib.import_module("sweep_control_path_formats")
        finally:
            sys.path.pop(0)
        specs = tools.build_candidates()
        identities = [spec.identity for spec in specs]
        self.assertEqual(len(identities), len(set(identities)))
        self.assertEqual(specs[0], baseline_spec())
        # 1 baseline + 4 + 5 + 3 + 2 + 3 + 1 axis members = 19
        self.assertEqual(len(specs), 19)
        for spec in specs:
            self.assertNotEqual(tools.axis_of(spec), "unassigned")
        self.assertEqual(tools.axis_of(baseline_spec()), "baseline")

    def test_consumed_parameter_names_cover_the_control_path(self):
        names = consumed_parameter_names()
        self.assertEqual(len(names), 68)
        self.assertEqual(len(set(names)), 68)
        for name in names:
            self.assertIn(name, BASE_PHYSICAL)


class TestEntrySite(unittest.TestCase):
    def test_s1_quantization_error_is_within_half_lsb(self):
        from torchsynth_voice.format_sweep import (
            LENGTH_FORMAT,
            TIME_PARAM_NAMES,
            entry_format_for,
        )

        fmt = parse_identity("Q9.6")
        counters = StickyCounters()
        words = quantize_params(BASE_PHYSICAL, fmt, RoundingMode.HALF_EVEN, counters)
        for name, word in words.items():
            entry_fmt = entry_format_for(name, fmt)
            error = abs(BASE_PHYSICAL[name] - word / entry_fmt.scale)
            self.assertLessEqual(error, float(entry_fmt.lsb / 2) + 1e-12, name)
            if name in TIME_PARAM_NAMES:
                self.assertIs(entry_fmt, LENGTH_FORMAT, name)
            else:
                self.assertIs(entry_fmt, fmt, name)
        self.assertEqual(counters.total(SATURATION), 0)

    def test_s1_refuses_out_of_range_by_saturation_counter(self):
        fmt = parse_identity("Q2.13")  # range [-4, +4)
        physical = dict(BASE_PHYSICAL)
        physical["keyboard.midi_f0"] = 127.0
        counters = StickyCounters()
        words = quantize_params(physical, fmt, RoundingMode.HALF_EVEN, counters)
        self.assertEqual(words["keyboard.midi_f0"], fmt.max_int)
        self.assertGreaterEqual(counters.total(SATURATION), 1)


class TestFixedControlPath(unittest.TestCase):
    def test_render_returns_every_owned_trace_with_exact_endpoints(self):
        spec = baseline_spec()
        model = FixedControlPath(spec)
        rendered = model.render(BASE_PHYSICAL)
        self.assertEqual(set(rendered), set(float_cp.owned_control_traces()))
        for name, values in rendered.items():
            expected = 1 if name in ("keyboard.midi_f0", "keyboard.duration") else 1764
            if name.startswith("control_upsample."):
                expected = 176400
            self.assertEqual(len(values), expected, name)

    def test_keyboard_traces_hold_the_consumed_words(self):
        spec = baseline_spec()
        model = FixedControlPath(spec)
        rendered = model.render(BASE_PHYSICAL)
        quantized = quantize_params(
            BASE_PHYSICAL, spec.midi_format, spec.mode, StickyCounters()
        )
        self.assertEqual(
            rendered["keyboard.midi_f0"],
            [quantized["keyboard.midi_f0"] / spec.midi_format.scale],
        )
        self.assertLessEqual(
            abs(rendered["keyboard.midi_f0"][0] - BASE_PHYSICAL["keyboard.midi_f0"]),
            float(spec.midi_format.lsb / 2),
        )

    def test_undefined_lfo_state_is_refused(self):
        spec = baseline_spec()
        model = FixedControlPath(spec)
        physical = dict(BASE_PHYSICAL)
        for shape in ("sin", "tri", "saw", "rsaw", "sqr"):
            physical["lfo_1." + shape] = 0.0
        with self.assertRaises(UndefinedControlState):
            model.render(physical)

    def test_saturation_is_counted_sticky(self):
        spec = baseline_spec(ctrl_format=parse_identity("Q2.9"))
        model = FixedControlPath(spec)
        physical = dict(BASE_PHYSICAL)
        physical["mod_matrix.adsr_1->vco_1_amp"] = 50.0
        model.render(physical)
        self.assertGreaterEqual(model.counters.total(SATURATION), 1)


class TestLfoShapes(unittest.TestCase):
    def test_tri_is_one_at_the_mid_turn(self):
        spec = baseline_spec()
        model = FixedControlPath(spec)
        weights = [SHAPE_ONE, 0, 0, 0, 0]
        weights[1] = SHAPE_ONE  # tri weight 1.0
        mid = 1 << 31
        self.assertEqual(model._lfo_blend(mid, [0, SHAPE_ONE, 0, 0, 0]), SHAPE_ONE)

    def test_sqr_is_quarter_at_cosine_zero(self):
        spec = baseline_spec()
        model = FixedControlPath(spec)
        # phase = 3 * 2^30 is exactly one quarter turn before 2 turns:
        # cos at that phase is the table's quarter-turn endpoint (0).
        phase = 3 << 30
        value = model._lfo_blend(phase, [0, 0, 0, 0, SHAPE_ONE])
        self.assertEqual(value, SHAPE_ONE // 2)

    def test_saw_spans_zero_to_one(self):
        spec = baseline_spec()
        model = FixedControlPath(spec)
        zero = model._lfo_blend(0, [0, 0, SHAPE_ONE, 0, 0])
        near_one = model._lfo_blend((1 << 32) - 1, [0, 0, SHAPE_ONE, 0, 0])
        self.assertEqual(zero, 0)
        self.assertLessEqual(near_one, SHAPE_ONE)
        self.assertGreater(near_one, SHAPE_ONE - 4)


class TestUpsample(unittest.TestCase):
    def test_endpoints_are_exact_copies_and_grid_matches_full(self):
        spec = baseline_spec()
        model = FixedControlPath(spec)
        column = [i % 977 for i in range(1764)]
        full = model._upsample(column, spec.ctrl_format)
        self.assertEqual(len(full), 176400)
        self.assertEqual(full[0], column[0])
        self.assertEqual(full[-1], column[-1])
        grid = list(range(0, 176400, 997)) + [176399]
        screened = model._upsample(column, spec.ctrl_format, grid)
        self.assertEqual(len(screened), len(grid))
        for k, j in enumerate(grid):
            self.assertEqual(screened[k], full[j], j)

    def test_half_even_inline_matches_the_canonical_scalar(self):
        import random

        from torchsynth_voice.fixedpoint.rounding import div_round_reported

        rng = random.Random(7)
        spec = baseline_spec()
        model = FixedControlPath(spec)
        column = [rng.randrange(-(2**20), 2**20) for _ in range(1764)]
        scale = 1 << spec.up_frac_bits
        js = [rng.randrange(176400) for _ in range(400)] + [0, 176399]
        got = model._upsample(column, spec.ctrl_format, js)
        for k, j in enumerate(js):
            low, remainder = divmod(j * 1763, 176399)
            if remainder == 0:
                expected = column[low]
            else:
                frac = div_round_reported(
                    remainder << spec.up_frac_bits, 176399, RoundingMode.HALF_EVEN
                )[0]
                numer = column[low] * (scale - frac) + column[low + 1] * frac
                expected = div_round_reported(numer, scale, RoundingMode.HALF_EVEN)[0]
            self.assertEqual(got[k], expected, j)


class TestLutPrimitive(unittest.TestCase):
    def test_table_is_deterministic_and_within_analytic_bound(self):
        table = quarter_wave_table(4096)
        again = quarter_wave_table(4096)
        self.assertEqual(table.sha256(), again.sha256())
        worst = 0.0
        for phase in range(0, 1 << 32, 1 << 20):
            worst = max(worst, table.error_vs_cos(phase))
        self.assertLessEqual(worst, table.analytic_error_bound())


class TestSweepDriverSmoke(unittest.TestCase):
    def test_smoke_run_writes_a_self_consistent_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "smoke-sweep.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "sweep_control_path_formats.py"),
                    "--smoke",
                    "--out",
                    str(out),
                ],
                capture_output=True,
                text=True,
                timeout=600,
            )
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            document = json.loads(out.read_text())
            self.assertEqual(document["schema"], "gf180-torchsynth/control-format-sweep-v1")
            self.assertEqual(document["numeric_contract"], "unbound:#53")
            self.assertEqual(document["issue"], 50)
            self.assertEqual(document["preregistration"]["holdout_reads"], 0)
            self.assertEqual(
                document["preregistration"]["committed_corpus_cases_read"], 0
            )
            self.assertIn(
                "CANDIDATE-pending-#53-ratification",
                document["recommendation_status"],
            )
            baseline = document["candidates"][0]
            self.assertEqual(baseline["axis"], "baseline")
            self.assertEqual(len(baseline["rows"]), 22)
            for row in baseline["rows"].values():
                self.assertIn(row["verdict"], ("PASS", "FAIL"))
                self.assertTrue(row["band_source"])
                self.assertGreaterEqual(row["samples_per_fixture"], 1)


if __name__ == "__main__":
    unittest.main()
