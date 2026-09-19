"""Generator-emitted hashable LUT tables: determinism, bounds, dense sweep."""

import json
import math
import subprocess
import sys
import unittest
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.fixedpoint.formats import FixedFormat  # noqa: E402
from torchsynth_voice.fixedpoint.lut import (  # noqa: E402
    QuarterWaveSpec,
    QuarterWaveTable,
    evaluate_sin,
    generate_quarter_cos,
    max_error_vs_cos,
)


# Working instantiation used to exercise the generator against the DR-0008
# Section 5 arithmetic (4096 x 24 quarter-wave, 12 index + 18 interp bits of
# the 30 usable phase bits). Widths enter as explicit data here, not as
# library constants; DR-0008 C5 is selected-pending-ratification, not accepted.
SPEC = QuarterWaveSpec(
    n_entries=4096,
    entry_format=FixedFormat(True, 1, 22),  # width 24; LSB 2^-22 per DR arithmetic
    phase_bits=32,
)

PHASE_MODULUS = 1 << 32
SWEEP_POINTS = 1 << 20  # bounded dense sweep, 2^20 phase points
GOLDEN_STRIDE = 2654435761  # odd, coprime with 2^32: quasi-uniform circle tour


class TestGeneration(unittest.TestCase):
    def test_entries_match_exact_half_even_quarter_cosine(self):
        table = generate_quarter_cos(SPEC)
        scale = SPEC.entry_format.scale
        for i in (0, 1, 512, 2048, 4095):
            stored = table.entries[i]
            self.assertLessEqual(
                abs(stored - math.cos(math.pi / 2 * i / 4096) * scale), 0.5 + 1e-9,
                f"entry {i}",
            )

    def test_endpoint_is_quarter_turn_value(self):
        table = generate_quarter_cos(SPEC)
        self.assertEqual(table.endpoint, 0)  # cos(pi/2) == 0 exactly

    def test_first_entry_is_exactly_one(self):
        table = generate_quarter_cos(SPEC)
        self.assertEqual(table.entries[0], SPEC.entry_format.scale)  # cos(0) == 1

    def test_entries_are_nonnegative_and_in_range(self):
        table = generate_quarter_cos(SPEC)
        fmt = SPEC.entry_format
        for entry in table.entries:
            self.assertTrue(fmt.contains(entry))
            self.assertGreaterEqual(entry, 0)

    def test_spec_validation(self):
        with self.assertRaises(ValueError):
            QuarterWaveSpec(4095, SPEC.entry_format, 32)  # not a power of two
        with self.assertRaises(ValueError):
            QuarterWaveSpec(4096, FixedFormat(False, 1, 22), 32)  # unsigned entries
        with self.assertRaises(ValueError):
            QuarterWaveSpec(4096, SPEC.entry_format, 1)  # no quadrant field


class TestHashAndSerializationStability(unittest.TestCase):
    def test_hash_is_stable_across_repeated_generation(self):
        once = generate_quarter_cos(SPEC).sha256()
        twice = generate_quarter_cos(SPEC).sha256()
        self.assertEqual(once, twice)
        self.assertEqual(len(once), 64)

    def test_hash_is_stable_across_processes(self):
        # True cross-process determinism: re-emit in a fresh interpreter and
        # compare the printed hash (decimal generation, no float, no env).
        code = (
            "import sys; sys.path.insert(0, {root!r});"
            "from torchsynth_voice.fixedpoint.formats import FixedFormat;"
            "from torchsynth_voice.fixedpoint.lut import QuarterWaveSpec,"
            " generate_quarter_cos;"
            "spec = QuarterWaveSpec(4096, FixedFormat(True, 1, 22), 32);"
            "print(generate_quarter_cos(spec).sha256())"
        ).format(root=str(ROOT / "src"))
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=300
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(
            out.stdout.strip(),
            generate_quarter_cos(SPEC).sha256(),
            "hash differs across processes: generation is not deterministic",
        )

    def test_json_round_trip_preserves_hash_and_refuses_corruption(self):
        table = generate_quarter_cos(SPEC)
        payload = table.to_json()
        revived = QuarterWaveTable.from_json(json.loads(json.dumps(payload)))
        self.assertEqual(revived.sha256(), table.sha256())
        self.assertEqual(revived.entries, table.entries)
        corrupted = dict(payload)
        corrupted["entries"] = list(payload["entries"])
        corrupted["entries"][17] += 1
        with self.assertRaises(ValueError):
            QuarterWaveTable.from_json(corrupted)

    def test_canonical_bytes_are_stable_and_payload_shaped(self):
        table = generate_quarter_cos(SPEC)
        raw = table.canonical_bytes()
        self.assertEqual(table.canonical_bytes(), raw)
        self.assertTrue(raw.startswith(b"gf180-torchsynth/quarter-wave-table-v1"))

    def test_any_content_change_changes_the_hash(self):
        table = generate_quarter_cos(SPEC)
        entries = list(table.entries)
        entries[4095] += 1
        mutated = QuarterWaveTable(SPEC, tuple(entries), table.endpoint)
        self.assertNotEqual(mutated.sha256(), table.sha256())


class TestKnownValues(unittest.TestCase):
    def setUp(self):
        self.table = generate_quarter_cos(SPEC)

    def fixed_to_float(self, value: int) -> float:
        return value / SPEC.entry_format.scale

    def test_quadrant_nodes_match_cos(self):
        for q in range(4):
            for i in (0, 1024, 2048, 3072, 4095):
                phase = (q << 30) + (i << 18)
                got = self.fixed_to_float(self.table.evaluate(phase))
                want = math.cos(2 * math.pi * phase / PHASE_MODULUS)
                self.assertLessEqual(abs(got - want), 2.6e-7, f"q={q} i={i}")

    def test_axis_points_are_exact(self):
        table = self.table
        self.assertEqual(table.evaluate(0), table.entries[0])
        # Quarter turn: reflected r == 0 hits the endpoint exactly.
        self.assertEqual(table.evaluate(1 << 30), table.endpoint)
        self.assertEqual(table.evaluate(2 << 30), -table.entries[0])
        self.assertEqual(table.evaluate(3 << 30), -table.endpoint)

    def test_sin_reuse_matches_cos_of_shifted_phase(self):
        quarter = 1 << 30
        for phase in (0, 1 << 18, quarter, quarter + 12345, (1 << 32) - 1):
            want = self.table.evaluate((phase - quarter) % PHASE_MODULUS)
            self.assertEqual(evaluate_sin(self.table, phase), want)

    def test_sin_known_values(self):
        quarter = 1 << 30
        got = self.fixed_to_float(evaluate_sin(self.table, quarter))
        self.assertLessEqual(abs(got - 1.0), 2.6e-7)
        got = self.fixed_to_float(evaluate_sin(self.table, 0))
        self.assertLessEqual(abs(got - 0.0), 2.6e-7)

    def test_phase_domain_validated(self):
        with self.assertRaises(ValueError):
            self.table.evaluate(-1)
        with self.assertRaises(ValueError):
            self.table.evaluate(PHASE_MODULUS)


class TestDenseSweepErrorBound(unittest.TestCase):
    """Bounded dense sweep: 2^20+ phase points vs exact math.cos."""

    def test_sweep_max_error_within_analytic_and_dr_bounds(self):
        table = generate_quarter_cos(SPEC)
        phases = []
        phase = 0
        for _ in range(SWEEP_POINTS):
            phases.append(phase)
            phase = (phase + GOLDEN_STRIDE) % PHASE_MODULUS
        # all table nodes (t == 0) and their immediate neighbors:
        for i in range(SPEC.n_entries):
            base = i << 18
            phases.append(base)
            phases.append(base + 1)
        # quadrant boundaries and reflection seam:
        phases.extend([0, 1, (1 << 30) - 1, 1 << 30, (1 << 31) - 1, (1 << 32) - 1])
        self.assertGreaterEqual(len(phases), SWEEP_POINTS)

        worst, worst_phase = max_error_vs_cos(table, phases)
        analytic = table.analytic_error_bound()
        dr_bound = 2.6e-7  # DR-0008 Section 5 total for this instantiation shape
        self.assertLessEqual(
            worst, analytic, f"max error {worst:.3e} exceeds analytic bound {analytic:.3e}"
        )
        self.assertLessEqual(worst, dr_bound, f"max error {worst:.3e} exceeds DR bound")
        print(
            f"\nLUT dense sweep: {len(phases)} phases, "
            f"max error {worst:.6e} at phase {worst_phase}, "
            f"analytic bound {analytic:.6e}, DR-0008 bound {dr_bound:.1e}",
            file=sys.stderr,
        )

    def test_error_at_nodes_is_quantization_only(self):
        table = generate_quarter_cos(SPEC)
        half_lsb = 0.5 / SPEC.entry_format.scale
        for i in range(0, SPEC.n_entries, 251):  # deterministic sparse node sample
            phase = i << 18
            self.assertLessEqual(table.error_vs_cos(phase), half_lsb + 1e-12)

    def test_negative_control_corrupted_table_exceeds_bound(self):
        table = generate_quarter_cos(SPEC)
        entries = list(table.entries)
        entries[1234] += 1 << 10  # a plainly wrong LUT entry
        corrupted = QuarterWaveTable(SPEC, tuple(entries), table.endpoint)
        worst, _ = max_error_vs_cos(corrupted, [1234 << 18, (1234 << 18) + (1 << 9)])
        self.assertGreater(worst, table.analytic_error_bound())


if __name__ == "__main__":
    unittest.main()
