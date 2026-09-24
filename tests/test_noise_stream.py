"""Issue #75 noise-stream host mirror: C8 exactness, identity, mutations.

The accepted noise policy (DR-0003 Accepted + DR-0008 C8 Accepted) is the
HOST-FED EXACT STREAM: the canonical bytes resolve from seed 13 with the
slot rule ``sound_index % 32``; there is NO error metric for noise —
exactness. This suite binds the bit-level convert mirror
(:mod:`torchsynth_voice.noise_stream_golden`, the twin of
``tb/sv/noise_stream_dut.sv``) against the frozen fixed model's own noise
lane and against every committed golden case's ``noise.raw`` digest, and
proves the mutation set fails.

The RTL itself is exercised by ``tb/run_tb.py noise`` (Icarus; runs in CI
and on the remote tb host) — these offline tests never stand in for it.
"""

import json
import math
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import noise_stream_golden as nsg  # noqa: E402
from torchsynth_voice.fixed_voice import (  # noqa: E402
    AcceptedFormats,
    StickyCounters,
    OverflowPolicy,
    apply_policy,
    shadow_half_even,
)
from torchsynth_voice.float_sources import (  # noqa: E402
    AUDIO_SAMPLES,
    NOISE_SEED,
    NOISE_STREAMS,
)

RECEIPT_PATH = ROOT / "sim/reference/fixed-voice-golden-v1.json"
RECEIPT = json.loads(RECEIPT_PATH.read_bytes())


_FORMATS = None


def model_lane_word(x: float) -> int:
    """The frozen fixed model's noise lane (fixed_voice.py noise.source_q)."""

    global _FORMATS
    if _FORMATS is None:
        _FORMATS = AcceptedFormats()
    counters = StickyCounters()
    return apply_policy(
        shadow_half_even(x * float(_FORMATS.audio.scale)),
        _FORMATS.audio,
        OverflowPolicy.SATURATE,
        counters,
        "noise.source_q",
    )


def pack_f32(x: float) -> int:
    return struct.unpack("<I", struct.pack("<f", x))[0]


class TestConvertMirrorVsModel(unittest.TestCase):
    """The bit mirror must equal the model lane on every observable class."""

    def test_structured_edge_patterns(self):
        patterns = [
            0x00000000,  # +0.0
            0x80000000,  # -0.0
            0x00000001,  # smallest subnormal -> deep-zero
            0x00400000,  # largest subnormal / 2 -> still deep-zero
            0x00800000,  # 2^-126 (smallest normal) -> deep-zero
            0x3F000000,  # 0.5
            0x3F000001,  # 0.5 + ulp: tie at .5 below -> round to even (down)
            0x3F800000,  # 1.0
            0xBF800000,  # -1.0
            0x3F800003,  # tie at x.5 with odd integer part -> round up
            0x3FC00000,  # 1.5
            0x40400000,  # 3.0
            0x407FFFFF,  # largest finite below 4 -> saturates high
            0x40800000,  # 4.0 -> saturates high
            0xC0800000,  # -4.0 -> saturates low (exact C1_MIN)
            0x40A00000,  # 5.0 -> saturates high
            0xC2C80000,  # -98.25 -> saturates low
        ]
        for bits in patterns:
            x = struct.unpack("<f", struct.pack("<I", bits))[0]
            with self.subTest(bits=hex(bits), x=x):
                self.assertEqual(nsg.narrow_f32_bits(bits), model_lane_word(x))

    def test_random_bit_patterns(self):
        # Structured random u32 sweep: finite patterns only (non-finite is
        # unreachable from the canonical stream; the mirror refuses it).
        state = 0x1D872B41  # xorshift determinism, stdlib-only
        checked = 0
        for _ in range(20000):
            state ^= (state << 13) & 0xFFFFFFFF
            state ^= state >> 17
            state ^= (state << 5) & 0xFFFFFFFF
            bits = state & 0x7FFFFFFF  # force finite positive exponent span
            if (bits >> 23) & 0xFF == 0xFF:
                continue
            x = struct.unpack("<f", struct.pack("<I", bits))[0]
            if math.isnan(x):
                continue
            self.assertEqual(nsg.narrow_f32_bits(bits), model_lane_word(x))
            checked += 1
        self.assertGreater(checked, 19000)

    def test_nonfinite_refused(self):
        for bits in (0x7F800000, 0xFF800000, 0x7FC00000):
            with self.assertRaises(ValueError):
                nsg.narrow_f32_bits(bits)

    def test_out_of_domain_refused(self):
        for bits in (-1, 0x100000000, 1.5, None):
            with self.assertRaises((ValueError, TypeError)):
                nsg.narrow_f32_bits(bits)


class TestGoldenReceiptBinding(unittest.TestCase):
    """Every committed golden case binds byte digest, slot rule, and lane."""

    def test_all_cases_bit_exact(self):
        self.assertEqual(RECEIPT["kind"], "fixed-voice-golden-release-receipt")
        self.assertGreaterEqual(len(RECEIPT["cases"]), 32)
        slots_seen = set()
        for case in RECEIPT["cases"]:
            sound_index = case["sound_index"]
            meta = case["noise"]
            with self.subTest(case=case["id"], sound_index=sound_index):
                self.assertEqual(meta["seed"], NOISE_SEED)
                self.assertEqual(meta["slot"], sound_index % NOISE_STREAMS)
                self.assertEqual(meta["slot"], nsg.canonical_slot(sound_index))
                raw = nsg.resolve_canonical_bytes(sound_index)
                self.assertEqual(len(raw), nsg.EXPECTED_NOISE_BYTES)
                self.assertEqual(
                    nsg.noise_bytes_sha256(raw), meta["sha256"]
                )
                mirror = nsg.mirror_stream(raw)
                self.assertEqual(len(mirror), AUDIO_SAMPLES)
                self.assertEqual(
                    nsg.trace_digest(mirror), case["traces"]["noise.raw"]
                )
                slots_seen.add(meta["slot"])
        # 32-stream repetition: the 34 cases exercise repeated slots.
        self.assertLess(len(slots_seen), len(RECEIPT["cases"]))

    def test_slot_rule_properties(self):
        self.assertEqual(nsg.canonical_slot(0), 0)
        self.assertEqual(nsg.canonical_slot(31), 31)
        self.assertEqual(nsg.canonical_slot(32), 0)
        self.assertEqual(nsg.canonical_slot(732), 732 % 32)
        with self.assertRaises(ValueError):
            nsg.canonical_slot(-1)
        with self.assertRaises(ValueError):
            nsg.canonical_slot(True)

    def test_seed_is_canonical(self):
        with self.assertRaises(ValueError):
            nsg.resolve_canonical_bytes(0, seed=14)

    def test_32_stream_repetition(self):
        # Larger reproducible batches repeat the 32 canonical streams:
        # sound_index k and k + 32 resolve to identical bytes.
        for k in (0, 5, 731):
            self.assertEqual(
                nsg.resolve_canonical_bytes(k),
                nsg.resolve_canonical_bytes(k + NOISE_STREAMS),
            )


class TestFramingValidation(unittest.TestCase):
    """Length/hash/framing validation fails before any render (AC-4/AC-5)."""

    def setUp(self):
        self.raw = nsg.resolve_canonical_bytes(0)

    def test_dropped_byte_fails(self):
        with self.assertRaises(ValueError):
            nsg.mirror_stream(self.raw[:-1])

    def test_duplicated_byte_fails(self):
        with self.assertRaises(ValueError):
            nsg.mirror_stream(self.raw + b"\x00")

    def test_wrong_slot_fails(self):
        with self.assertRaises(ValueError):
            nsg.check_fed_bytes(self.raw, sound_index=0, declared_slot=1)
        with self.assertRaises(ValueError):
            nsg.check_fed_bytes(self.raw, sound_index=33, declared_slot=0)
        # The correct binding passes.
        nsg.check_fed_bytes(self.raw, sound_index=0, declared_slot=0)
        nsg.check_fed_bytes(self.raw, sound_index=32, declared_slot=0)

    def test_non_bytes_fails(self):
        with self.assertRaises(ValueError):
            nsg.mirror_stream("not bytes")
        with self.assertRaises(ValueError):
            nsg.mirror_stream(None)


class TestMutationsFail(unittest.TestCase):
    """The mutation set must fail against the golden lane (AC-5/M7 rule)."""

    def test_lsb_truncation_diverges(self):
        raw = nsg.resolve_canonical_bytes(0)
        mirror = nsg.mirror_stream(raw)
        mutant = nsg.truncate_stream(raw)
        self.assertNotEqual(mirror, mutant)
        differing = sum(1 for a, b in zip(mirror, mutant) if a != b)
        # Truncation must corrupt a substantial fraction of the clip, so the
        # golden digest can never pass under the mutant lane.
        self.assertGreater(differing, AUDIO_SAMPLES // 4)
        with self.assertRaises(AssertionError):
            self.assertEqual(
                nsg.trace_digest(mutant),
                RECEIPT["cases"][0]["traces"]["noise.raw"],
            )

    def test_truncation_mutant_bit_semantics(self):
        # Half-up remainder dropped: value * 2^21 = 2097153.5 exactly (tie
        # with an odd integer part) rounds up under half-even (2097154, to
        # even) but truncates (2097153).
        bits = 0x3F800006  # 1.0 + 6 * 2^-23
        x = struct.unpack("<f", struct.pack("<I", bits))[0]
        self.assertEqual(x * 2.0 ** 21, 2097153.5)
        self.assertEqual(nsg.narrow_f32_bits(bits), round(x * 2.0 ** 21))
        self.assertEqual(nsg.truncate_f32_bits(bits), 2097153)
        self.assertEqual(nsg.narrow_f32_bits(bits), 2097154)

    def test_identity_mutations_raise(self):
        raw = nsg.resolve_canonical_bytes(0)
        with self.assertRaises(ValueError):
            nsg.check_fed_bytes(raw[:-4], sound_index=0, declared_slot=0)
        with self.assertRaises(ValueError):
            nsg.check_fed_bytes(raw, sound_index=64, declared_slot=1)


class TestTraceDigestBinding(unittest.TestCase):
    """trace_digest must match the receipt generator's serialization."""

    def test_matches_tool_implementation(self):
        sys.path.insert(0, str(ROOT / "tools"))
        try:
            from generate_fixed_voice_golden import sha256_json

            for words in ([0, 1, -1, 2**22, -(2**22)], [123456] * 7):
                self.assertEqual(nsg.trace_digest(words), sha256_json(words))
        finally:
            sys.path.pop(0)


if __name__ == "__main__":
    unittest.main()
