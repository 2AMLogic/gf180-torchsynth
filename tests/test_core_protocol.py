"""Byte-level framing tests for the core/host protocol subset."""

from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.core_protocol import (  # noqa: E402
    CMD_ERROR,
    CMD_HELLO,
    CMD_PATCH_ABORT,
    CMD_PATCH_COMMIT,
    CMD_PATCH_NAME,
    CMD_PATCH_OPEN,
    CMD_PATCH_VALUE,
    CMD_READY,
    CMD_RESET,
    COMMAND_NAMES,
    ErrorCode,
    KIND_COMMAND,
    KIND_ERROR,
    KIND_RESPONSE,
    PLACEHOLDER_WIDTHS,
    PROTOCOL_VERSION,
    SYNC,
    PayloadReader,
    Ready,
    crc16_ccitt_false,
    decode_frame,
    decode_hello,
    decode_patch_commit,
    decode_patch_name,
    decode_patch_open,
    decode_patch_value,
    decode_ready,
    encode_frame,
    encode_hello,
    encode_patch_commit,
    encode_patch_name,
    encode_patch_open,
    encode_patch_value,
    encode_ready,
    pack_opaque,
    patch_hash,
    PlaceholderWidth,
    PatchOpen,
)


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
        frame = bytearray(encode_frame(KIND_COMMAND, CMD_PATCH_VALUE, 1, b"\x00\x01"))
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


class HelloReadyTests(unittest.TestCase):
    def test_hello_layout_is_exact(self):
        payload = encode_hello(b"p", b"s", b"n", 0x0003)
        self.assertEqual(payload, b"\x01\x00p\x01\x00s\x01\x00n\x03\x00")

    def test_hello_round_trip(self):
        hello = decode_hello(encode_hello(b"profile", b"source-v9", b"\x00\x01", 0xFFFF))
        self.assertEqual(hello.profile_id, b"profile")
        self.assertEqual(hello.source_version, b"source-v9")
        self.assertEqual(hello.numeric_contract_version, b"\x00\x01")
        self.assertEqual(hello.capabilities, 0xFFFF)

    def test_ready_round_trip(self):
        ready = Ready(
            profile_id=b"torchsynth-1-voice-default",
            numeric_contract_version=b"unbound:#53",
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
        name, value = decode_patch_value(encode_patch_value("vco_1.level", b"\xfe\x00"))
        self.assertEqual((name, value), ("vco_1.level", b"\xfe\x00"))
        self.assertEqual(decode_patch_commit(encode_patch_commit(b"\x22" * 32)), b"\x22" * 32)


class PatchHashTests(unittest.TestCase):
    def test_construction_matches_spec(self):
        digest = patch_hash({"a.b": b"\x01\x02"})
        self.assertEqual(digest, hashlib.sha256(b"a.b\x00\x01\x02\x02\x00").digest())

    def test_order_independence(self):
        left = patch_hash({"b.c": b"\x01", "a.a": b"\x02\x03"})
        right = patch_hash({"a.a": b"\x02\x03", "b.c": b"\x01"})
        self.assertEqual(left, right)


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
                CMD_RESET,
                CMD_ERROR,
            },
        )

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

    def test_placeholder_widths_are_named_and_deferred(self):
        by_name = {entry.name: entry for entry in PLACEHOLDER_WIDTHS}
        for required in (
            "patch_value",
            "numeric_contract_version",
            "patch_hash",
            "sound_identity",
            "profile_id",
            "locks",
        ):
            self.assertIsInstance(by_name[required], PlaceholderWidth)
        self.assertEqual(by_name["patch_value"].deferred_to, "#53")
        self.assertEqual(by_name["numeric_contract_version"].deferred_to, "#53")
        self.assertEqual(by_name["patch_hash"].deferred_to, "#53")


if __name__ == "__main__":
    unittest.main()
