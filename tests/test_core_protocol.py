"""Byte-level framing tests for the core/host protocol (version 2, bound)."""

from __future__ import annotations

import hashlib
import sys
import unittest
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.core_protocol import (  # noqa: E402
    AUDIO_FRAC_BITS,
    AUDIO_SAMPLE_BYTES,
    BOUND_FIELDS,
    CMD_ERROR,
    CMD_HELLO,
    CMD_PATCH_ABORT,
    CMD_PATCH_COMMIT,
    CMD_PATCH_NAME,
    CMD_PATCH_OPEN,
    CMD_PATCH_VALUE,
    CMD_NOISE_STREAM,
    CMD_READY,
    CMD_RENDER_TRIGGER,
    CMD_RESET,
    CAP_NAME_KEYED_PATCH_LOAD,
    CAP_RENDER,
    CAP_RESET,
    COMMAND_NAMES,
    KNOWN_CAPABILITIES,
    NOISE_STREAM_CLIP_BYTES,
    NOISE_STREAM_DIGEST_BYTES,
    NoiseChunk,
    PayloadError,
    RenderTrigger,
    decode_noise_stream,
    decode_render_trigger,
    encode_noise_stream,
    encode_render_trigger,
    ErrorCode,
    KIND_COMMAND,
    KIND_RESPONSE,
    NUMERIC_CONTRACT_STALE,
    PARAM_FRAC_BITS,
    PARAM_VALUE_BYTES,
    PATCH_HASH_BYTES,
    PATCH_HASH_DOMAIN,
    PLACEHOLDER_WIDTHS,
    PROTOCOL_VERSION,
    SYNC,
    PayloadReader,
    Ready,
    crc16_ccitt_false,
    decode_audio_payload,
    decode_audio_sample,
    decode_frame,
    decode_hello,
    decode_patch_commit,
    decode_patch_name,
    decode_patch_open,
    decode_patch_value,
    decode_param_word,
    decode_ready,
    encode_audio_payload,
    encode_audio_sample,
    encode_audio_word,
    encode_frame,
    encode_hello,
    encode_patch_commit,
    encode_patch_name,
    encode_patch_open,
    encode_patch_value,
    encode_param_word,
    encode_ready,
    numeric_contract_version_bound,
    pack_opaque,
    patch_hash,
    BoundField,
    PlaceholderWidth,
    PatchOpen,
)


def a_contract() -> bytes:
    """An arbitrary well-formed 32-byte numeric-contract version for layout tests."""
    return bytes(range(32))


class CrcTests(unittest.TestCase):
    def test_known_check_value(self):
        self.assertEqual(crc16_ccitt_false(b"123456789"), 0x29B1)

    def test_zero_input(self):
        self.assertEqual(crc16_ccitt_false(b""), 0xFFFF)


class FrameLayoutTests(unittest.TestCase):
    def test_wire_layout_is_exact(self):
        frame = encode_frame(KIND_COMMAND, CMD_HELLO, 0x0102, b"\xAB")
        self.assertEqual(frame[:2], SYNC)
        self.assertEqual(frame[0:2], b"\x67\xf1")
        self.assertEqual(frame[2], PROTOCOL_VERSION)
        self.assertEqual(PROTOCOL_VERSION, 2)
        self.assertEqual(frame[3], KIND_COMMAND)
        self.assertEqual(frame[4], CMD_HELLO)
        self.assertEqual(frame[5:7], b"\x02\x01")
        self.assertEqual(frame[7:9], b"\x01\x00")
        self.assertEqual(frame[9], 0xAB)
        self.assertEqual(frame[-2:], crc16_ccitt_false(frame[2:-2]).to_bytes(2, "little"))
        self.assertEqual(len(frame), 12)

    def test_round_trip(self):
        payload = bytes(range(37))
        frame = decode_frame(encode_frame(KIND_RESPONSE, CMD_READY, 65535, payload))
        self.assertEqual(frame.version, PROTOCOL_VERSION)
        self.assertEqual(frame.kind, KIND_RESPONSE)
        self.assertEqual(frame.command, CMD_READY)
        self.assertEqual(frame.sequence, 65535)
        self.assertEqual(frame.payload, payload)

    def test_crc_corruption_rejected(self):
        frame = bytearray(encode_frame(KIND_COMMAND, CMD_RESET, 1))
        frame[-1] ^= 0xFF
        with self.assertRaises(ValueError):
            decode_frame(bytes(frame))

    def test_payload_byte_corruption_rejected(self):
        frame = bytearray(encode_frame(KIND_COMMAND, CMD_PATCH_VALUE, 1, b"\x00" * 4))
        frame[9] ^= 0x01
        with self.assertRaises(ValueError):
            decode_frame(bytes(frame))

    def test_bad_sync_rejected(self):
        frame = bytearray(encode_frame(KIND_COMMAND, CMD_RESET, 1))
        frame[0] = 0x00
        with self.assertRaises(ValueError):
            decode_frame(bytes(frame))

    def test_trailing_bytes_rejected(self):
        frame = encode_frame(KIND_COMMAND, CMD_RESET, 1) + b"\x00"
        with self.assertRaises(ValueError):
            decode_frame(frame)

    def test_short_frame_rejected(self):
        with self.assertRaises(ValueError):
            decode_frame(b"\x67\xf1\x01")


class OpaqueEncodingTests(unittest.TestCase):
    def test_pack_opaque_layout(self):
        self.assertEqual(pack_opaque(b"\xde\xad"), b"\x02\x00\xde\xad")

    def test_reader_round_trip(self):
        payload = pack_opaque(b"abc") + pack_opaque(b"") + b"\x39\x05"
        reader = PayloadReader(payload)
        self.assertEqual(reader.take_opaque(), b"abc")
        self.assertEqual(reader.take_opaque(), b"")
        self.assertEqual(reader.take_u16(), 0x0539)
        reader.require_end()

    def test_reader_truncated(self):
        reader = PayloadReader(b"\x05\x00ab")
        with self.assertRaises(ValueError):
            reader.take_opaque()

    def test_reader_trailing(self):
        reader = PayloadReader(b"\x01\x00a\xff")
        reader.take_opaque()
        with self.assertRaises(ValueError):
            reader.require_end()


class AudioSampleBindingTests(unittest.TestCase):
    """C1 (accepted): 24-bit Q2.21, little-endian, saturating, half-even."""

    def test_zero_is_three_zero_bytes(self):
        self.assertEqual(encode_audio_sample(0), b"\x00\x00\x00")
        self.assertEqual(decode_audio_sample(b"\x00\x00\x00"), Fraction(0))

    def test_unity_word_layout(self):
        self.assertEqual(encode_audio_sample(1), b"\x00\x00\x20")
        self.assertEqual(encode_audio_sample(-1), b"\x00\x00\xe0")
        self.assertEqual(decode_audio_sample(b"\x00\x00\x20"), Fraction(1))
        self.assertEqual(decode_audio_sample(b"\x00\x00\xe0"), Fraction(-1))

    def test_lsb_is_exactly_2_pow_minus_21(self):
        data = (1).to_bytes(AUDIO_SAMPLE_BYTES, "little", signed=True)
        self.assertEqual(decode_audio_sample(data), Fraction(1, 1 << AUDIO_FRAC_BITS))

    def test_range_is_half_open_at_plus_four(self):
        self.assertEqual(encode_audio_sample(4), b"\xff\xff\x7f")
        self.assertEqual(
            decode_audio_sample(b"\xff\xff\x7f"),
            Fraction((1 << 23) - 1, 1 << AUDIO_FRAC_BITS),
        )
        self.assertEqual(
            decode_audio_sample(b"\x00\x00\x80"),
            Fraction(-4),
        )

    def test_saturation_at_both_rails(self):
        self.assertEqual(encode_audio_sample(100), b"\xff\xff\x7f")
        self.assertEqual(encode_audio_sample(-100), b"\x00\x00\x80")
        self.assertEqual(decode_audio_sample(b"\xff\xff\x7f"), Fraction((1 << 23) - 1, 1 << 21))
        self.assertEqual(decode_audio_sample(b"\x00\x00\x80"), Fraction(-(1 << 23), 1 << 21))

    def test_rounding_is_half_even(self):
        half_lsb = Fraction(1, 2 * (1 << AUDIO_FRAC_BITS))
        three_half_lsb = Fraction(3, 2 * (1 << AUDIO_FRAC_BITS))
        self.assertEqual(encode_audio_word(half_lsb), 0)
        self.assertEqual(encode_audio_word(three_half_lsb), 2)

    def test_payload_pack_round_trip(self):
        samples = [Fraction(-2), Fraction(0), Fraction(3, 4)]
        packed = encode_audio_payload(samples)
        self.assertEqual(len(packed), 3 * AUDIO_SAMPLE_BYTES)
        self.assertEqual(decode_audio_payload(packed), samples)
        self.assertEqual(packed, b"\x00\x00\xc0" + b"\x00\x00\x00" + b"\x00\x00\x18")

    def test_payload_rejects_non_multiple_of_three(self):
        with self.assertRaises(ValueError):
            decode_audio_payload(b"\x00\x00\x00\x00")


class ParamWordBindingTests(unittest.TestCase):
    """C4 (accepted): uniform host-entry word, 32-bit Q10.21 little-endian."""

    def test_unity_word_layout(self):
        self.assertEqual(encode_param_word(1), b"\x00\x00\x20\x00")
        self.assertEqual(encode_param_word(-1), b"\x00\x00\xe0\xff")
        self.assertEqual(decode_param_word(b"\x00\x00\x20\x00"), Fraction(1))
        self.assertEqual(decode_param_word(b"\x00\x00\xe0\xff"), Fraction(-1))

    def test_lsb_is_exactly_2_pow_minus_21(self):
        self.assertEqual(
            decode_param_word((1).to_bytes(PARAM_VALUE_BYTES, "little", signed=True)),
            Fraction(1, 1 << PARAM_FRAC_BITS),
        )

    def test_saturation_at_both_rails(self):
        self.assertEqual(encode_param_word(100000), b"\xff\xff\xff\x7f")
        self.assertEqual(encode_param_word(-100000), b"\x00\x00\x00\x80")

    def test_rounding_is_half_even(self):
        half_lsb = Fraction(1, 2 * (1 << PARAM_FRAC_BITS))
        self.assertEqual(encode_param_word(half_lsb), b"\x00\x00\x00\x00")
        self.assertEqual(
            encode_param_word(3 * half_lsb),
            (2).to_bytes(PARAM_VALUE_BYTES, "little", signed=True),
        )

    def test_wrong_width_rejected(self):
        with self.assertRaises(ValueError):
            decode_param_word(b"\x00\x00\x00")
        with self.assertRaises(ValueError):
            encode_patch_value("vco_1.level", b"\x01\x02")


class HelloReadyTests(unittest.TestCase):
    def test_hello_layout_is_exact(self):
        payload = encode_hello(b"p", b"s", b"n" * 32, 0x0003)
        self.assertEqual(
            payload,
            b"\x01\x00p\x01\x00s\x20\x00" + b"n" * 32 + b"\x03\x00",
        )

    def test_hello_round_trip(self):
        hello = decode_hello(encode_hello(b"profile", b"source-v9", a_contract(), 0xFFFF))
        self.assertEqual(hello.profile_id, b"profile")
        self.assertEqual(hello.source_version, b"source-v9")
        self.assertEqual(hello.numeric_contract_version, a_contract())
        self.assertEqual(hello.capabilities, 0xFFFF)

    def test_ready_round_trip(self):
        ready = Ready(
            profile_id=b"torchsynth-1-voice-default",
            numeric_contract_version=numeric_contract_version_bound(),
            locks=b"\x00\x01",
            capabilities=0x0003,
            max_payload=1024,
            rx_queue_depth=2,
            patch_timeout_ms=5000,
        )
        decoded = decode_ready(encode_ready(ready))
        self.assertEqual(decoded, ready)

    def test_patch_open_layout_and_round_trip(self):
        self.assertEqual(encode_patch_open(b"tx", b"identity", b"\x11" * 32, 78)[:4], b"\x02\x00tx")
        opened = decode_patch_open(encode_patch_open(b"tx", b"identity", b"\x11" * 32, 78))
        self.assertEqual(
            opened,
            PatchOpen(b"tx", b"identity", b"\x11" * 32, 78),
        )

    def test_patch_open_transaction_id_bounds(self):
        with self.assertRaises(ValueError):
            encode_patch_open(b"", b"i", b"\x00" * 32, 1)
        with self.assertRaises(ValueError):
            encode_patch_open(b"x" * 17, b"i", b"\x00" * 32, 1)
        with self.assertRaises(ValueError):
            decode_patch_open(pack_opaque(b"") + pack_opaque(b"i") + pack_opaque(b"\x00" * 32) + b"\x01\x00")

    def test_name_and_value_round_trips(self):
        self.assertEqual(encode_patch_name("keyboard.midi_f0"), b"\x10\x00keyboard.midi_f0")
        self.assertEqual(decode_patch_name(encode_patch_name("lfo_1.rate")), "lfo_1.rate")
        name, value = decode_patch_value(
            encode_patch_value("vco_1.level", encode_param_word(Fraction(-1, 32)))
        )
        self.assertEqual((name, value), ("vco_1.level", b"\x00\x00\xff\xff"))
        digest = patch_hash({}, numeric_contract_version=a_contract())
        self.assertEqual(decode_patch_commit(encode_patch_commit(digest)), digest)


class PatchHashTests(unittest.TestCase):
    def test_construction_matches_spec(self):
        contract = a_contract()
        digest = patch_hash({"a.b": b"\x01\x02\x00\x00"}, numeric_contract_version=contract)
        self.assertEqual(
            digest,
            hashlib.sha256(
                PATCH_HASH_DOMAIN + contract + b"a.b\x00\x01\x02\x00\x00\x04\x00"
            ).digest(),
        )

    def test_domain_separation_binds_the_contract(self):
        values = {"a.b": b"\x01\x02\x00\x00"}
        self.assertNotEqual(
            patch_hash(values, numeric_contract_version=a_contract()),
            patch_hash(values, numeric_contract_version=bytes(range(1, 33))),
        )
        self.assertNotEqual(
            patch_hash(values, numeric_contract_version=a_contract()),
            hashlib.sha256(b"a.b\x00\x01\x02\x00\x00\x04\x00").digest(),
        )

    def test_order_independence(self):
        contract = a_contract()
        left = patch_hash({"b.c": b"\x01\x00\x00\x00", "a.a": b"\x02\x03\x00\x00"}, numeric_contract_version=contract)
        right = patch_hash({"a.a": b"\x02\x03\x00\x00", "b.c": b"\x01\x00\x00\x00"}, numeric_contract_version=contract)
        self.assertEqual(left, right)

    def test_digest_is_32_bytes(self):
        self.assertEqual(len(patch_hash({}, numeric_contract_version=a_contract())), PATCH_HASH_BYTES)

    def test_unbound_contract_version_refused(self):
        with self.assertRaises(ValueError):
            patch_hash({}, numeric_contract_version=NUMERIC_CONTRACT_STALE)


class NumericContractBindingTests(unittest.TestCase):
    def test_bound_version_is_the_register_sha256(self):
        register = ROOT / "spec" / "reference" / "fixedpoint-choices-v1.json"
        self.assertEqual(
            numeric_contract_version_bound(),
            hashlib.sha256(register.read_bytes()).digest(),
        )

    def test_stale_marker_is_not_the_bound_value(self):
        self.assertNotEqual(NUMERIC_CONTRACT_STALE, numeric_contract_version_bound())
        self.assertNotEqual(len(NUMERIC_CONTRACT_STALE), PATCH_HASH_BYTES)


class RegistryTests(unittest.TestCase):
    def test_defined_commands_are_exactly_the_subset(self):
        self.assertEqual(
            set(COMMAND_NAMES),
            {
                CMD_HELLO,
                CMD_READY,
                CMD_PATCH_OPEN,
                CMD_PATCH_NAME,
                CMD_PATCH_VALUE,
                CMD_PATCH_COMMIT,
                CMD_PATCH_ABORT,
                CMD_RENDER_TRIGGER,
                CMD_NOISE_STREAM,
                CMD_RESET,
                CMD_ERROR,
            },
        )

    def test_render_codes_are_pinned(self):
        # spec/protocol/RENDER-TRIGGER.md (issue #188).
        self.assertEqual(CMD_RENDER_TRIGGER, 0x15)
        self.assertEqual(CMD_NOISE_STREAM, 0x16)
        self.assertEqual(COMMAND_NAMES[0x15], "RENDER_TRIGGER")
        self.assertEqual(COMMAND_NAMES[0x16], "NOISE_STREAM")
        self.assertEqual(CAP_RENDER, 0x0004)
        self.assertEqual(
            KNOWN_CAPABILITIES, CAP_NAME_KEYED_PATCH_LOAD | CAP_RESET | CAP_RENDER
        )

    def test_reserved_codes_stay_unallocated(self):
        for code in list(range(0x17, 0x20)) + list(range(0x21, 0xFE)) + [0xFF]:
            self.assertNotIn(code, COMMAND_NAMES)

    def test_no_live_note_commands_exist(self):
        for name in COMMAND_NAMES.values():
            self.assertNotIn("NOTE", name)

    def test_error_codes_are_pinned(self):
        self.assertEqual(ErrorCode.UNSUPPORTED_COMMAND, 0x01)
        self.assertEqual(ErrorCode.PROTOCOL_VERSION, 0x02)
        self.assertEqual(ErrorCode.BAD_FRAME, 0x03)
        self.assertEqual(ErrorCode.BAD_SEQUENCE, 0x04)
        self.assertEqual(ErrorCode.BUSY, 0x05)
        self.assertEqual(ErrorCode.BAD_STATE, 0x06)
        self.assertEqual(ErrorCode.PATCH_TX_ACTIVE, 0x07)
        self.assertEqual(ErrorCode.PATCH_INCOMPLETE, 0x08)
        self.assertEqual(ErrorCode.PATCH_HASH_MISMATCH, 0x09)
        self.assertEqual(ErrorCode.UNKNOWN_NAME, 0x0A)
        self.assertEqual(ErrorCode.DUPLICATE_NAME, 0x0B)
        self.assertEqual(ErrorCode.TX_TIMEOUT, 0x0C)
        self.assertEqual(ErrorCode.PAYLOAD_LENGTH, 0x0D)
        self.assertEqual(ErrorCode.RENDER_BINDING, 0x0E)
        self.assertEqual(ErrorCode.NOISE_STREAM, 0x0F)

    def test_remaining_placeholders_are_exactly_the_unbound_three(self):
        by_name = {entry.name: entry for entry in PLACEHOLDER_WIDTHS}
        self.assertEqual(
            set(by_name), {"sound_identity", "profile_id", "locks"}
        )
        for entry in by_name.values():
            self.assertIsInstance(entry, PlaceholderWidth)

    def test_bound_fields_are_exactly_the_binding_set(self):
        by_name = {entry.name: entry for entry in BOUND_FIELDS}
        self.assertEqual(
            set(by_name),
            {"numeric_contract_version", "patch_value", "patch_hash", "audio_sample"},
        )
        for entry in by_name.values():
            self.assertIsInstance(entry, BoundField)
        self.assertIn("accepted 2026-09-21", by_name["patch_value"].authority)
        self.assertIn("accepted 2026-09-21", by_name["audio_sample"].authority)
        self.assertIn("Accepted 2026-09-21", by_name["numeric_contract_version"].authority)

    def test_bound_widths_are_pinned(self):
        self.assertEqual(AUDIO_SAMPLE_BYTES, 3)
        self.assertEqual(PARAM_VALUE_BYTES, 4)
        self.assertEqual(PATCH_HASH_BYTES, 32)


class RenderCodecTests(unittest.TestCase):
    """RENDER_TRIGGER / NOISE_STREAM payloads (RENDER-TRIGGER.md)."""

    DIGEST = hashlib.sha256(b"noise").digest()

    def test_clip_length_is_the_profile_noise_length(self):
        from torchsynth_voice import noise_stream_golden as nsg
        from torchsynth_voice.fixedpoint import schedule as sched

        schedule = sched.require_accepted_schedule()
        self.assertEqual(NOISE_STREAM_CLIP_BYTES, nsg.EXPECTED_NOISE_BYTES)
        self.assertEqual(
            NOISE_STREAM_CLIP_BYTES, 4 * sched.constant(schedule, "samples_per_pass")
        )
        self.assertEqual(NOISE_STREAM_DIGEST_BYTES, 32)

    def test_render_trigger_wire_bytes(self):
        payload = encode_render_trigger(2, b"sound-7", self.DIGEST)
        self.assertEqual(
            payload,
            b"\x02" + b"\x07\x00sound-7" + b"\x20\x00" + self.DIGEST,
        )
        self.assertEqual(
            decode_render_trigger(payload), RenderTrigger(2, b"sound-7", self.DIGEST)
        )

    def test_render_trigger_rejects_bad_pass_and_digest_width(self):
        with self.assertRaises(ValueError):
            encode_render_trigger(3, b"s", self.DIGEST)
        with self.assertRaises(ValueError):
            encode_render_trigger(1, b"s", self.DIGEST[:31])
        good = encode_render_trigger(1, b"s", self.DIGEST)
        for bad in (
            b"\x00" + good[1:],          # pass 0
            b"\x03" + good[1:],          # pass 3
            good[:-1],                   # truncated digest
            good + b"\x00",              # trailing byte
            b"\x01\x01\x00s\x1f\x00" + self.DIGEST[:31],  # 31-byte digest
        ):
            with self.assertRaises(PayloadError):
                decode_render_trigger(bad)

    def test_noise_stream_wire_bytes(self):
        payload = encode_noise_stream(1, 0x01020304, b"\xaa\xbb")
        self.assertEqual(payload, b"\x01\x04\x03\x02\x01\x02\x00\xaa\xbb")
        self.assertEqual(
            decode_noise_stream(payload), NoiseChunk(1, 0x01020304, b"\xaa\xbb")
        )

    def test_noise_stream_rejects_malformed(self):
        with self.assertRaises(ValueError):
            encode_noise_stream(1, 0, b"")
        with self.assertRaises(ValueError):
            encode_noise_stream(0, 0, b"x")
        good = encode_noise_stream(2, 5, b"xy")
        for bad in (
            b"\x04" + good[1:],          # pass 4
            good[:-1],                   # truncated data
            good + b"\x00",              # trailing byte
            b"\x01\x00\x00\x00\x00\x00\x00",  # empty data region
        ):
            with self.assertRaises(PayloadError):
                decode_noise_stream(bad)


if __name__ == "__main__":
    unittest.main()
