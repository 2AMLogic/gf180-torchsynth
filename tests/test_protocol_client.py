"""Host transport client tests: golden round trips and refusal gates (issue 66).

Every mutation demanded by the issue's acceptance criteria — version/profile/
patch-hash mismatch, partial write, timeout, stale state, corrupted audio,
session-state violation — must FAIL here. The independent reference encoder
below re-derives the wire bytes from spec/protocol/FRAMING.md directly so the
golden comparisons do not trust the implementation under test.
"""

from __future__ import annotations

import struct
import sys
import time
import unittest
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.core_protocol import (  # noqa: E402
    CMD_HELLO,
    CMD_PATCH_NAME,
    CMD_READY,
    CMD_RESET,
    KIND_RESPONSE,
    KNOWN_CAPABILITIES,
    NUMERIC_CONTRACT_STALE,
    PROTOCOL_VERSION,
    SYNC,
    ErrorCode,
    PayloadError,
    decode_audio_payload,
    decode_frame,
    decode_ready,
    encode_audio_payload,
    encode_frame,
    numeric_contract_version_bound,
    pack_opaque,
    patch_hash,
)
from torchsynth_voice.core_protocol_mock import MockCore, SessionState  # noqa: E402
from torchsynth_voice.protocol_client import (  # noqa: E402
    ClientError,
    ClientStateError,
    FrameStream,
    HostClient,
    MockTransport,
    NegotiationError,
    ProtocolError,
    ProtocolTimeout,
    TransportEmpty,
)

BOUND = numeric_contract_version_bound()
TABLE_SHA = b"\x11" * 32
NAMES = {"keyboard.midi_f0", "lfo_1.rate", "vco_1.level"}


def ref_crc(data: bytes) -> int:
    """CRC-16/CCITT-FALSE re-derived from FRAMING.md, independent of src."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def ref_frame(version: int, kind: int, command: int, sequence: int, payload: bytes = b"") -> bytes:
    body = (
        struct.pack("<BBBH", version, kind, command, sequence)
        + struct.pack("<H", len(payload))
        + payload
    )
    return SYNC + body + struct.pack("<H", ref_crc(body))


# Golden frame: canonical protocol v2 HELLO for profile/source "golden", the
# bound numeric contract, both known capabilities, sequence 1. Pinned so any
# encoder drift fails here before it can drift the wire.
GOLDEN_HELLO_SEQUENCE = 1
GOLDEN_HELLO_PAYLOAD = (
    pack_opaque(b"golden")
    + pack_opaque(b"golden-src")
    + pack_opaque(BOUND)
    + struct.pack("<H", KNOWN_CAPABILITIES)
)
GOLDEN_HELLO_FRAME = ref_frame(
    PROTOCOL_VERSION, 0x01, CMD_HELLO, GOLDEN_HELLO_SEQUENCE, GOLDEN_HELLO_PAYLOAD
)


class ScriptedTransport:
    """Lossy test transport: one programmed response (or None) per send."""

    def __init__(self, responses, *, recv_chunk: int = 64, max_frame_bytes: int = 256):
        self.responses = list(responses)
        self.recv_chunk = recv_chunk
        self.max_frame_bytes = max_frame_bytes
        self.sent: list[bytes] = []
        self._rx = b""

    def send(self, data: bytes) -> None:
        self.sent.append(bytes(data))
        self._rx = b"" if not self.responses else self.responses.pop(0)
        if self._rx is None:
            self._rx = b""

    def recv(self) -> bytes:
        if not self._rx:
            raise TransportEmpty()
        chunk, self._rx = self._rx[: self.recv_chunk], self._rx[self.recv_chunk :]
        return chunk


class ClientHarness:
    def make(self, *, mock_kwargs=None, client_kwargs=None):
        mock_kwargs = dict(mock_kwargs or {})
        mock_kwargs.setdefault("name_table", NAMES)
        mock_kwargs.setdefault("name_table_sha256", TABLE_SHA)
        mock_kwargs.setdefault("profile_id", b"profile-x")
        core = MockCore(**mock_kwargs)
        transport = MockTransport(core, recv_chunk=5)
        client_kwargs = dict(client_kwargs or {})
        client_kwargs.setdefault("profile_id", b"profile-x")
        client_kwargs.setdefault("source_version", b"source-x")
        client_kwargs.setdefault("name_table_sha256", TABLE_SHA)
        client_kwargs.setdefault("numeric_contract_version", BOUND)
        client_kwargs.setdefault("capabilities", KNOWN_CAPABILITIES)
        client_kwargs.setdefault("expected_profile", b"profile-x")
        client = HostClient(transport, **client_kwargs)
        return core, transport, client


class GoldenRoundTripTests(unittest.TestCase, ClientHarness):
    def test_golden_hello_frame_byte_exact(self):
        client = HostClient(
            MockTransport(MockCore(name_table=NAMES, name_table_sha256=TABLE_SHA)),
            profile_id=b"golden",
            source_version=b"golden-src",
            name_table_sha256=TABLE_SHA,
            numeric_contract_version=BOUND,
            capabilities=KNOWN_CAPABILITIES,
        )
        client.negotiate()
        self.assertEqual(client.frames_sent[0], GOLDEN_HELLO_FRAME)
        self.assertEqual(
            client.frames_sent[0],
            ref_frame(
                PROTOCOL_VERSION,
                0x01,
                CMD_HELLO,
                GOLDEN_HELLO_SEQUENCE,
                GOLDEN_HELLO_PAYLOAD,
            ),
        )

    def test_ready_response_bytes_exact(self):
        core = MockCore(
            name_table=NAMES, name_table_sha256=TABLE_SHA, profile_id=b"profile-x"
        )
        transport = MockTransport(core, recv_chunk=4096)
        hello = ref_frame(
            PROTOCOL_VERSION,
            0x01,
            CMD_HELLO,
            1,
            pack_opaque(b"profile-x")
            + pack_opaque(b"source-x")
            + pack_opaque(BOUND)
            + struct.pack("<H", KNOWN_CAPABILITIES),
        )
        transport.send(hello)
        expected_payload = (
            pack_opaque(b"profile-x")
            + pack_opaque(BOUND)
            + pack_opaque(b"")
            + struct.pack("<HHBH", KNOWN_CAPABILITIES, 1024, 2, 5000)
        )
        # The mock's READY response bytes are exactly the canonical encoding.
        self.assertEqual(
            transport._rx,
            ref_frame(PROTOCOL_VERSION, KIND_RESPONSE, CMD_READY, 1, expected_payload),
        )
        ready = decode_ready(decode_frame(transport._rx).payload)
        self.assertEqual(ready.numeric_contract_version, BOUND)
        self.assertEqual(ready.max_payload, 1024)
        self.assertEqual(ready.rx_queue_depth, 2)
        self.assertEqual(ready.patch_timeout_ms, 5000)
        self.assertIs(core.state, SessionState.READY)

    def test_full_session_golden_frames_round_trip(self):
        core, transport, client = self.make()
        client.negotiate()
        values = {
            "keyboard.midi_f0": struct.pack("<i", 1 << 20),
            "lfo_1.rate": struct.pack("<i", -(1 << 19)),
            "vco_1.level": struct.pack("<i", 3 << 20),
        }
        client.open_patch(b"tx-golden-1", b"identity-golden", len(values))
        for name in sorted(values):
            client.declare_name(name)
        for name, word in values.items():
            client.stage_value(name, word)
        client.commit_patch(values)
        client.reset()
        # Every command frame the client sent is the canonical encoding.
        for index, frame_bytes in enumerate(transport.sent_frames):
            frame = decode_frame(frame_bytes)
            self.assertEqual(frame.version, PROTOCOL_VERSION)
            self.assertEqual(frame.kind, 0x01)
            self.assertEqual(frame.sequence, (index + 1) & 0xFFFF)
        # The mock applied the patch: same transaction id and staged values.
        self.assertIsNotNone(core.active_patch)
        self.assertEqual(core.active_patch["transaction_id"], b"tx-golden-1")
        self.assertEqual(core.active_patch["values"], values)
        self.assertIs(core.state, SessionState.READY)
        # The commit carried the bound, domain-separated hash.
        commit_frame = decode_frame(transport.sent_frames[-2])
        self.assertEqual(
            commit_frame.payload,
            pack_opaque(patch_hash(values, numeric_contract_version=BOUND)),
        )
        # And the byte-level frames replay identically through a fresh pair.
        replay_core = MockCore(
            name_table=NAMES,
            name_table_sha256=TABLE_SHA,
            profile_id=b"profile-x",
        )
        replay = MockTransport(replay_core)
        client2 = HostClient(
            replay,
            profile_id=b"profile-x",
            source_version=b"source-x",
            name_table_sha256=TABLE_SHA,
            numeric_contract_version=BOUND,
            capabilities=KNOWN_CAPABILITIES,
            expected_profile=b"profile-x",
        )
        client2.negotiate()
        for frame_bytes in transport.sent_frames[1:]:
            replay.send(frame_bytes)
            while True:
                try:
                    replay.recv()
                except TransportEmpty:
                    break
        self.assertIsNotNone(replay_core.active_patch)
        self.assertEqual(replay_core.active_patch["values"], values)


class RefusalTests(unittest.TestCase, ClientHarness):
    def test_numeric_contract_mismatch_is_fatal(self):
        core, transport, client = self.make(
            mock_kwargs={"numeric_contract_version": b"\xab" * 32}
        )
        with self.assertRaises(NegotiationError) as caught:
            client.negotiate()
        self.assertIs(caught.exception.code, ErrorCode.PROTOCOL_VERSION)
        self.assertIs(core.state, SessionState.CLOSED)
        self.assertEqual(client.state, "closed")
        # The stale v1 placeholder is refused exactly like any other mismatch.
        core2, _, client2 = self.make(
            client_kwargs={"numeric_contract_version": NUMERIC_CONTRACT_STALE}
        )
        with self.assertRaises(NegotiationError):
            client2.negotiate()
        self.assertIs(core2.state, SessionState.CLOSED)

    def test_wrong_header_version_response_refused(self):
        stale_ready = ref_frame(1, 0x02, CMD_READY, 1, pack_opaque(b"x") * 4)
        transport = ScriptedTransport([stale_ready])
        client = HostClient(
            transport,
            profile_id=b"profile-x",
            source_version=b"source-x",
            name_table_sha256=TABLE_SHA,
            numeric_contract_version=BOUND,
            timeout_s=0.01,
        )
        with self.assertRaises(NegotiationError):
            client.negotiate()
        self.assertEqual(client.state, "closed")

    def test_profile_mismatch_refused(self):
        core, _, client = self.make(client_kwargs={"expected_profile": b"other"})
        with self.assertRaises(NegotiationError):
            client.negotiate()
        self.assertIs(core.state, SessionState.READY)
        self.assertEqual(client.state, "closed")

    def test_name_table_mismatch_refused_at_open(self):
        _, _, client = self.make(client_kwargs={"name_table_sha256": b"\x22" * 32})
        client.negotiate()
        with self.assertRaises(ProtocolError) as caught:
            client.open_patch(b"tx-1", b"id", 1)
        self.assertIs(caught.exception.code, ErrorCode.UNKNOWN_NAME)

    def test_patch_hash_mismatch_discards_transaction(self):
        core, _, client = self.make()
        client.negotiate()
        values = {"keyboard.midi_f0": struct.pack("<i", 5), "lfo_1.rate": struct.pack("<i", 6)}
        client.open_patch(b"tx-h", b"id", len(values))
        for name in values:
            client.declare_name(name)
        for name, word in values.items():
            client.stage_value(name, word)
        tampered = dict(values)
        tampered["keyboard.midi_f0"] = struct.pack("<i", 7)
        with self.assertRaises(ProtocolError) as caught:
            client.commit_patch(tampered)
        self.assertIs(caught.exception.code, ErrorCode.PATCH_HASH_MISMATCH)
        self.assertIs(core.state, SessionState.READY)
        self.assertIsNone(core.active_patch)
        self.assertEqual(client.state, "ready")
        self.assertIsNone(client.transaction_id)

    def test_session_state_violations_fail_on_the_client(self):
        _, _, client = self.make()
        with self.assertRaises(ClientStateError):
            client.declare_name("lfo_1.rate")
        with self.assertRaises(ClientStateError):
            client.commit_patch({})
        with self.assertRaises(ClientStateError):
            client.reset()
        client.negotiate()
        with self.assertRaises(ClientStateError):
            client.declare_name("lfo_1.rate")
        client.open_patch(b"tx-a", b"id", 1)
        with self.assertRaises(ClientStateError):
            client.open_patch(b"tx-c", b"id", 1)
        client.abort_patch()
        with self.assertRaises(ClientStateError):
            client.abort_patch()

    def test_forced_state_violation_refused_by_mock(self):
        core, _, client = self.make()
        client.negotiate()
        with self.assertRaises(ProtocolError) as caught:
            client.transact(CMD_PATCH_NAME, enforce_state=False)
        self.assertIs(caught.exception.code, ErrorCode.BAD_SEQUENCE)
        self.assertIs(core.state, SessionState.READY)

    def test_unallocated_render_command_refused(self):
        # The audio transfer/render command set is unallocated (issue #63).
        _, _, client = self.make()
        with self.assertRaises(ClientStateError):
            client.transact(0x30)

    def test_frame_larger_than_transport_refused(self):
        _, transport, client = self.make()
        transport.max_frame_bytes = 16
        with self.assertRaises(ClientError):
            client.negotiate()


class FramingAndRetryTests(unittest.TestCase, ClientHarness):
    def test_fragmented_delivery_round_trips_byte_exact(self):
        core, transport, client = self.make()
        core2 = MockCore(
            name_table=NAMES,
            name_table_sha256=TABLE_SHA,
            profile_id=b"profile-x",
        )
        transport2 = MockTransport(core2, recv_chunk=1)
        client2 = HostClient(
            transport2,
            profile_id=b"profile-x",
            source_version=b"source-x",
            name_table_sha256=TABLE_SHA,
            numeric_contract_version=BOUND,
            capabilities=KNOWN_CAPABILITIES,
            expected_profile=b"profile-x",
        )
        first = client.negotiate()
        second = client2.negotiate()
        # One-byte chunks: identical session result, byte-identical frames.
        self.assertEqual(first, second)
        self.assertEqual(transport.sent_frames, transport2.sent_frames)

    def test_frame_stream_resyncs_after_corruption(self):
        good = ref_frame(PROTOCOL_VERSION, KIND_RESPONSE, CMD_READY, 1, b"1234")
        garbage = (
            b"\x67\xf1\x02\x02\x02\x01\x05\x05\x00" + b"\xaa" * 5 + b"\xbb\xcc"
        )
        self.assertEqual(len(garbage), 16)
        stream = FrameStream()
        stream.feed(garbage + good)
        frame = stream.next_frame(
            lambda: (_ for _ in ()).throw(TransportEmpty()),
            deadline=time.monotonic() + 1.0,
        )
        self.assertEqual(frame.command, CMD_READY)
        self.assertEqual(frame.sequence, 1)
        self.assertEqual(frame.payload, b"1234")
        self.assertEqual(stream.bad_frames, 1)

    def test_mock_answers_corrupted_frame_with_bad_frame_error(self):
        core, _, client = self.make()
        client.negotiate()
        corrupt = bytes(GOLDEN_HELLO_FRAME[:-1]) + bytes(
            [GOLDEN_HELLO_FRAME[-1] ^ 0xFF]
        )
        response = decode_frame(core.submit(corrupt))
        self.assertEqual(response.kind, 0x03)
        self.assertEqual(response.payload, bytes([ErrorCode.BAD_FRAME]))
        self.assertIs(core.state, SessionState.READY)

    def test_timeout_retries_identical_idempotent_frame_then_fails(self):
        transport = ScriptedTransport([None, None])
        client = HostClient(
            transport,
            profile_id=b"profile-x",
            source_version=b"source-x",
            name_table_sha256=TABLE_SHA,
            numeric_contract_version=BOUND,
            timeout_s=0.01,
        )
        with self.assertRaises(ProtocolTimeout):
            client.negotiate()
        self.assertEqual(len(transport.sent), 2)
        self.assertEqual(transport.sent[0], transport.sent[1])

    def test_timeout_on_non_idempotent_command_never_retries(self):
        ready_payload = (
            pack_opaque(b"profile-x")
            + pack_opaque(BOUND)
            + pack_opaque(b"")
            + struct.pack("<HHBH", KNOWN_CAPABILITIES, 1024, 2, 5000)
        )
        scripted = ScriptedTransport(
            [ref_frame(PROTOCOL_VERSION, KIND_RESPONSE, CMD_READY, 1, ready_payload), None]
        )
        client = HostClient(
            scripted,
            profile_id=b"profile-x",
            source_version=b"source-x",
            name_table_sha256=TABLE_SHA,
            numeric_contract_version=BOUND,
            timeout_s=0.01,
        )
        client.negotiate()
        with self.assertRaises(ProtocolTimeout):
            client.open_patch(b"tx-t", b"id", 1)
        self.assertEqual(len(scripted.sent), 2)
        self.assertEqual(client.state, "ready")
        self.assertIsNone(client.transaction_id)

    def test_busy_retries_identical_frame_then_raises(self):
        core, transport, client = self.make(mock_kwargs={"rx_queue_depth": 1})
        client.negotiate()
        filler = encode_frame(0x01, CMD_RESET, 300)
        core.submit(filler)
        with self.assertRaises(ProtocolError) as caught:
            client.reset()
        self.assertIs(caught.exception.code, ErrorCode.BUSY)
        resets = [
            frame
            for frame in transport.sent_frames
            if decode_frame(frame).sequence not in (1, 300)
        ]
        self.assertEqual(len(resets), client._busy_retries + 1)
        self.assertTrue(all(frame == resets[0] for frame in resets))
        self.assertIsNotNone(core.poll())  # drain the filler; its response
        client.reset()  # queue drained: the retry now succeeds

    def test_stale_sequence_and_transaction_reuse_fail(self):
        core, transport, client = self.make()
        client.negotiate()
        values = {"keyboard.midi_f0": struct.pack("<i", 8)}
        client.open_patch(b"tx-s", b"id", 1)
        client.declare_name("keyboard.midi_f0")
        client.stage_value("keyboard.midi_f0", values["keyboard.midi_f0"])
        commit_sequence = decode_frame(transport.sent_frames[-1]).sequence
        client.commit_patch(values)
        # Replay of a finished transaction's sequence: ERR_BAD_SEQUENCE.
        with self.assertRaises(ProtocolError) as caught:
            client.transact(
                0x13,
                pack_opaque(bytes(32)),
                sequence=commit_sequence,
                enforce_state=False,
            )
        self.assertIs(caught.exception.code, ErrorCode.BAD_SEQUENCE)
        # Reusing a finished transaction id at open: refused, nothing applied.
        with self.assertRaises(ProtocolError) as caught:
            client.open_patch(b"tx-s", b"id", 1)
        self.assertIs(caught.exception.code, ErrorCode.BAD_SEQUENCE)
        # The successful commit is untouched by the refused reuse.
        self.assertEqual(
            core.active_patch,
            {
                "transaction_id": b"tx-s",
                "sound_identity": b"id",
                "values": values,
            },
        )

    def test_expired_transaction_continuation_drops_client_stale_state(self):
        class _Clock:
            now = 0.0

            def __call__(self):
                return self.now

            def advance(self, seconds):
                self.now += seconds

        clock = _Clock()
        core, transport, client = self.make(
            mock_kwargs={"clock": clock, "patch_timeout_s": 1.0}
        )
        client.negotiate()
        client.open_patch(b"tx-exp", b"id", 2)
        client.declare_name("keyboard.midi_f0")
        # Silence past the core's patch_timeout_s expires the transaction (silent).
        clock.advance(2.0)
        # Continuing the expired transaction answers ERR_TX_TIMEOUT ...
        with self.assertRaises(ProtocolError) as caught:
            client.declare_name("lfo_1.rate")
        self.assertIs(caught.exception.code, ErrorCode.TX_TIMEOUT)
        # ... and the client tracks it as transaction-ending: ready, no stale state.
        self.assertEqual(client.state, "ready")
        self.assertIsNone(client.transaction_id)
        # SESSION.md: resume only with a fresh transaction id and a full re-send.
        word = struct.pack("<i", 1 << 20)
        client.open_patch(b"tx-exp2", b"id", 1)
        client.declare_name("keyboard.midi_f0")
        client.stage_value("keyboard.midi_f0", word)
        client.commit_patch({"keyboard.midi_f0": word})
        self.assertEqual(client.state, "ready")
        self.assertEqual(core.active_patch["transaction_id"], b"tx-exp2")
        self.assertEqual(core.active_patch["values"], {"keyboard.midi_f0": word})


class AudioGoldenVectorTests(unittest.TestCase):
    GOLDEN = (
        Fraction(0),
        Fraction(1, 4),
        Fraction(-1, 4),
        Fraction(1),
        Fraction(-1),
        Fraction(5, 2),
        Fraction(4382797, 1 << 21),
        Fraction(-4194304, 1 << 21),
        Fraction(1, 1 << 21),
    )

    def test_mock_sources_audio_from_fixed_golden_vectors(self):
        core = MockCore(name_table=NAMES, name_table_sha256=TABLE_SHA)
        with self.assertRaises(ValueError):
            core.audio_payload_bytes
        core.set_audio_source(self.GOLDEN)
        payload = core.audio_payload_bytes
        self.assertEqual(len(payload), 3 * len(self.GOLDEN))
        self.assertEqual(payload, encode_audio_payload(self.GOLDEN))
        self.assertEqual(decode_audio_payload(payload), list(self.GOLDEN))
        # Re-encoding the decoded golden vectors is byte-exact.
        self.assertEqual(encode_audio_payload(decode_audio_payload(payload)), payload)
        with self.assertRaises(ValueError):
            core.set_audio_source([float("nan")])

    def test_corrupted_audio_payload_fails(self):
        core = MockCore(name_table=NAMES, name_table_sha256=TABLE_SHA)
        core.set_audio_source(self.GOLDEN)
        payload = core.audio_payload_bytes
        with self.assertRaises(PayloadError):
            decode_audio_payload(payload[:-1])
        with self.assertRaises(PayloadError):
            decode_audio_payload(payload + b"\x00")
        # A flipped payload byte decodes to a different sample: integrity at
        # the transport level is the frame CRC's job, not the codec's.
        flipped = payload[:3] + bytes([payload[3] ^ 0x01]) + payload[4:]
        self.assertNotEqual(decode_audio_payload(flipped), list(self.GOLDEN))


if __name__ == "__main__":
    unittest.main()
