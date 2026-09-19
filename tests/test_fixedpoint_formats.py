"""Format-as-data: explicit widths, deterministic serialization identities."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.fixedpoint.formats import (  # noqa: E402
    FixedFormat,
    parse_identity,
)


class TestFormatConstruction(unittest.TestCase):
    def test_signed_width_per_dr_notation(self):
        # DR-0008 notation: Q<n>.<f> has width 1 sign + n integer + f fractional.
        self.assertEqual(FixedFormat(True, 2, 21).width, 24)
        self.assertEqual(FixedFormat(True, 16, 15).width, 32)
        self.assertEqual(FixedFormat(True, 10, 21).width, 32)
        self.assertEqual(FixedFormat(True, 1, 22).width, 24)

    def test_unsigned_width(self):
        self.assertEqual(FixedFormat(False, 0, 32).width, 32)
        self.assertEqual(FixedFormat(False, 8, 8).width, 16)

    def test_ranges(self):
        signed24 = FixedFormat(True, 2, 21)
        self.assertEqual(signed24.min_int, -(1 << 23))
        self.assertEqual(signed24.max_int, (1 << 23) - 1)
        unsigned32 = FixedFormat(False, 0, 32)
        self.assertEqual(unsigned32.min_int, 0)
        self.assertEqual(unsigned32.max_int, (1 << 32) - 1)

    def test_lsb_is_exact_fraction_not_float(self):
        from fractions import Fraction

        self.assertEqual(FixedFormat(True, 2, 21).lsb, Fraction(1, 1 << 21))

    def test_validation(self):
        with self.assertRaises(ValueError):
            FixedFormat(True, -1, 21)
        with self.assertRaises(ValueError):
            FixedFormat(True, 2, -1)
        with self.assertRaises(ValueError):
            FixedFormat(True, 2, 21, modular=True)  # modular words are unsigned


class TestSerializationIdentities(unittest.TestCase):
    def test_identity_strings(self):
        self.assertEqual(FixedFormat(True, 2, 21).identity, "Q2.21")
        self.assertEqual(FixedFormat(False, 0, 32).identity, "U0.32")
        self.assertEqual(FixedFormat(False, 0, 32, modular=True).identity, "U0.32:modular")

    def test_parse_identity_round_trip(self):
        for identity in ("Q2.21", "U0.32", "U0.32:modular", "Q10.21", "Q16.15", "Q0.0"):
            fmt = parse_identity(identity)
            self.assertEqual(fmt.identity, identity)
            self.assertEqual(parse_identity(fmt.identity), fmt)

    def test_json_round_trip_is_deterministic(self):
        fmt = FixedFormat(True, 2, 21)
        once = fmt.to_json()
        twice = FixedFormat.from_json(once).to_json()
        self.assertEqual(once, twice)
        self.assertEqual(
            json.dumps(once, sort_keys=True),
            json.dumps(FixedFormat(True, 2, 21).to_json(), sort_keys=True),
        )

    def test_json_carries_derived_width_and_identity(self):
        payload = FixedFormat(True, 2, 21).to_json()
        self.assertEqual(payload["width"], 24)
        self.assertEqual(payload["identity"], "Q2.21")
        self.assertEqual(payload["schema"], "gf180-torchsynth/fixed-format-v1")

    def test_json_rejects_width_or_identity_drift(self):
        payload = FixedFormat(True, 2, 21).to_json()
        payload["width"] = 32
        with self.assertRaises(ValueError):
            FixedFormat.from_json(payload)
        payload = FixedFormat(True, 2, 21).to_json()
        payload["identity"] = "Q1.22"
        with self.assertRaises(ValueError):
            FixedFormat.from_json(payload)

    def test_distinct_formats_have_distinct_identities(self):
        identities = {
            FixedFormat(True, 2, 21).identity,
            FixedFormat(True, 10, 21).identity,
            FixedFormat(True, 16, 15).identity,
            FixedFormat(False, 0, 32).identity,
        }
        self.assertEqual(len(identities), 4)

    def test_enclose_is_explicit_two_complement_reduction(self):
        signed24 = FixedFormat(True, 2, 21)
        self.assertEqual(signed24.enclose((1 << 23)), -(1 << 23))
        self.assertEqual(signed24.enclose((1 << 23) + 5), -(1 << 23) + 5)
        self.assertEqual(signed24.enclose(-(1 << 24)), 0)
        unsigned32 = FixedFormat(False, 0, 32)
        self.assertEqual(unsigned32.enclose(-1), (1 << 32) - 1)
        self.assertEqual(unsigned32.enclose(1 << 32), 0)


if __name__ == "__main__":
    unittest.main()
