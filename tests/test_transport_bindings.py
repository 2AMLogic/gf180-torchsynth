"""Transport binding-framing substitutability tests (issue #66, transport AC).

TRANSPORTS.md: "which carrier is chosen is a configuration decision, not a
protocol change," and the binding sections define exactly how each carrier
shapes delivery — UART as a continuous byte stream, SPI as half-duplex CS
periods, USB as bulk packet quantization. These tests model each binding
shape in-process (torchsynth_voice.transport_binding_models) and assert the
product model is invariant to the choice: one canonical session (negotiation
plus the full 78-name patch transaction and reset) run over every delivery
shape produces identical client frame streams, identical core response
streams, and identical final core state. No hardware, RTL, fidelity or
playback claim is made or testable here; physical bindings are issue #81.
"""

from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.core_protocol import (  # noqa: E402
    CMD_PATCH_NAME,
    CMD_PATCH_OPEN,
    CMD_RESET,
    KIND_COMMAND,
    KIND_RESPONSE,
    MIN_FRAME_LEN,
    SYNC,
    encode_frame,
    encode_param_word,
    encode_patch_name,
    encode_patch_open,
)
from torchsynth_voice.core_protocol_mock import SessionState  # noqa: E402
from torchsynth_voice.protocol_backend import (  # noqa: E402
    build_mock_client,
    build_mock_core,
    name_table_reference,
)
from torchsynth_voice.protocol_client import (  # noqa: E402
    ClientError,
    FrameStream,
    MockTransport,
    TransportWriteError,
)
from torchsynth_voice.transport_binding_models import (  # noqa: E402
    SpiBindingTransport,
    UartBindingTransport,
    UsbBindingTransport,
)

# Wire noise that includes the first sync byte (0x67) to force the
# split-sync-prefix retention path on resynchronization.
NOISE = b"\xde\xad\x67\x00\x01"


def run_canonical_session(transport_factory) -> tuple:
    """Drive the full canonical protocol session over one transport.

    Returns (core, client, transport).
    """
    core = build_mock_core()
    transport = transport_factory(core)
    client = build_mock_client(core, transport)
    client.negotiate()
    names, _ = name_table_reference()
    values = {
        name: encode_param_word(float(i) / 79.0 - 1.0) for i, name in enumerate(names)
    }
    tx = hashlib.sha256(b"binding-parity").digest()[:16]
    client.open_patch(tx, b"binding-parity-sound", len(values))
    for name in names:
        client.declare_name(name)
    for name, word in values.items():
        client.stage_value(name, word)
    client.commit_patch(values)
    client.reset()
    return core, client, transport


def frame_spans(stream: bytes) -> list[tuple[int, int]]:
    """(start, end) spans of every frame in a clean core→host stream."""
    spans = []
    offset = 0
    while offset < len(stream):
        assert stream[offset : offset + 2] == SYNC
        length = int.from_bytes(stream[offset + 7 : offset + 9], "little")
        total = MIN_FRAME_LEN + length
        spans.append((offset, offset + total))
        offset += total
    assert offset == len(stream)
    return spans


def reference_command_frame(command: int, sequence: int, payload: bytes) -> bytes:
    """A command frame built directly, for driving the binding models."""
    return encode_frame(KIND_COMMAND, command, sequence, payload)


def open_patch_payload(sound_identity: bytes, names: list[str]) -> bytes:
    _, table_sha256 = name_table_reference()
    return encode_patch_open(
        hashlib.sha256(b"binding-parity").digest()[:16],
        sound_identity,
        table_sha256,
        len(names),
    )


class RecordingMockTransport(MockTransport):
    """MockTransport that also records the clean core→host byte stream."""

    def __init__(self, core):
        super().__init__(core)
        self._delivered = bytearray()

    def recv(self) -> bytes:
        chunk = super().recv()
        self._delivered += chunk
        return chunk

    @property
    def delivered_response(self) -> bytes:
        return bytes(self._delivered)


class BindingSubstitutabilityTests(unittest.TestCase):
    def test_same_session_identical_across_all_bindings(self):
        runs = {}
        for label in ("mock", "uart", "spi", "usb"):
            factory = {
                "mock": RecordingMockTransport,
                "uart": lambda core: UartBindingTransport(core),
                "spi": lambda core: SpiBindingTransport(core),
                "usb": lambda core: UsbBindingTransport(core),
            }[label]
            runs[label] = run_canonical_session(factory)

        reference_core, reference_client, reference_transport = runs["mock"]
        # The canonical session really ran: 1 HELLO + 1 PATCH_OPEN + 78 name
        # declarations + 78 staged values + 1 PATCH_COMMIT + 1 RESET = 160.
        self.assertEqual(len(reference_transport.sent_frames), 160)
        self.assertIsNotNone(reference_core.active_patch)
        self.assertEqual(len(reference_core.active_patch["values"]), 78)

        for label, (core, client, transport) in runs.items():
            self.assertIs(core.state, SessionState.READY, label)
            self.assertEqual(client.state, "ready", label)
            self.assertIsNone(client.transaction_id, label)
            # The product model is the bytes: identical frames both ways.
            self.assertEqual(
                transport.sent_frames, reference_transport.sent_frames, label
            )
            self.assertEqual(
                bytes(transport.delivered_response),
                bytes(reference_transport.delivered_response),
                label,
            )
            # Identical final core state: the applied patch is byte-identical.
            self.assertEqual(core.active_patch, reference_core.active_patch, label)

    def test_usb_packet_boundaries_are_not_frame_boundaries(self):
        # Deliver a full session over 13-byte IN packets: no packet aligns
        # with the 82-byte READY or with the uniform ack frames, so some IN
        # transfers cut frames mid-byte.
        core, client, transport = run_canonical_session(
            lambda c: UsbBindingTransport(c, packet_bytes=13)
        )
        self.assertEqual(client.state, "ready")
        self.assertIsNotNone(core.active_patch)

        stream = bytes(transport.delivered_response)
        spans = frame_spans(stream)
        boundaries = []
        for size in transport.recv_chunk_sizes:
            boundaries.append(boundaries[-1] + size if boundaries else size)
        split = any(
            start < boundary < end
            for boundary in boundaries
            for start, end in spans
        )
        self.assertTrue(split, "expected at least one IN packet to cut a frame mid-byte")
        # The session still completed over the unaligned packets.
        self.assertEqual(len(spans), len(transport.sent_frames))

    def test_usb_single_in_transfer_carries_several_frames(self):
        # Model level: with a 31-byte bulk-IN endpoint and 11-byte
        # acknowledgements, one IN transfer hands the host several complete
        # frames at once. USB packet boundary is not framing.
        core = build_mock_core()
        transport = UsbBindingTransport(core, packet_bytes=31)
        client = build_mock_client(core, transport)
        client.negotiate()  # consumed sequence 1; the model frames below use 2,3
        names, _ = name_table_reference()
        transport.send(
            reference_command_frame(
                CMD_PATCH_OPEN, 2, open_patch_payload(b"binding-parity-sound", names)
            )
        )
        transport.send(
            reference_command_frame(CMD_PATCH_NAME, 3, encode_patch_name(names[0]))
        )
        chunk = transport.recv()
        self.assertLessEqual(len(chunk), 31)
        stream = FrameStream()
        stream.feed(chunk)
        first = stream.next_complete_frame()
        second = stream.next_complete_frame()
        third = stream.next_complete_frame()
        self.assertIsNone(third)
        self.assertEqual(first.kind, KIND_RESPONSE)
        self.assertEqual(first.command, CMD_PATCH_OPEN)
        self.assertEqual(second.kind, KIND_RESPONSE)
        self.assertEqual(second.command, CMD_PATCH_NAME)

    def test_spi_single_cs_period_carries_several_frames(self):
        # Model level: a CS period SHOULD carry whole or multiple frames
        # (TRANSPORTS.md); one 24-byte window hands both ack frames whole.
        core = build_mock_core()
        transport = SpiBindingTransport(core, cs_period_bytes=24)
        client = build_mock_client(core, transport)
        client.negotiate()  # consumed sequence 1; the model frames below use 2,3
        names, _ = name_table_reference()
        transport.send(
            reference_command_frame(
                CMD_PATCH_OPEN, 2, open_patch_payload(b"binding-parity-sound", names)
            )
        )
        transport.send(
            reference_command_frame(CMD_PATCH_NAME, 3, encode_patch_name(names[0]))
        )
        chunk = transport.recv()
        self.assertEqual(len(chunk), 22)  # two 11-byte acknowledgements
        stream = FrameStream()
        stream.feed(chunk)
        self.assertEqual(stream.next_complete_frame().command, CMD_PATCH_OPEN)
        self.assertEqual(stream.next_complete_frame().command, CMD_PATCH_NAME)
        self.assertIsNone(stream.next_complete_frame())

    def test_spi_cs_periods_are_not_frame_boundaries(self):
        # Deliver a full session over 7-byte CS windows against 11-byte
        # acknowledgements: frames span multiple windows and most window
        # boundaries fall mid-frame.
        core, client, transport = run_canonical_session(
            lambda c: SpiBindingTransport(c, cs_period_bytes=7)
        )
        self.assertEqual(client.state, "ready")
        self.assertIsNotNone(core.active_patch)

        stream = bytes(transport.delivered_response)
        spans = frame_spans(stream)
        ends = {end for _, end in spans}
        boundaries = []
        for size in transport.recv_chunk_sizes:
            boundaries.append(boundaries[-1] + size if boundaries else size)

        split = any(
            start < boundary < end
            for boundary in boundaries
            for start, end in spans
        )
        mid_frame = sum(1 for boundary in boundaries if boundary not in ends)
        self.assertTrue(split, "expected a CS window to cut a frame")
        self.assertGreater(mid_frame, len(boundaries) // 2)
        self.assertEqual(len(spans), len(transport.sent_frames))

    def test_uart_noise_between_frames_resyncs_end_to_end(self):
        clean_core, clean_client, clean_transport = run_canonical_session(
            lambda c: UartBindingTransport(c, chunk_bytes=9)
        )

        core = build_mock_core()
        transport = UartBindingTransport(core, chunk_bytes=9)
        client = build_mock_client(core, transport)
        client.negotiate()
        transport.interleave_rx_noise(NOISE)
        names, _ = name_table_reference()
        values = {
            name: encode_param_word(float(i) / 79.0 - 1.0) for i, name in enumerate(names)
        }
        tx = hashlib.sha256(b"binding-parity").digest()[:16]
        client.open_patch(tx, b"binding-parity-sound", len(values))
        transport.interleave_rx_noise(NOISE)
        for i, name in enumerate(names):
            client.declare_name(name)
            if i % 7 == 3:
                transport.interleave_rx_noise(NOISE)
        for i, (name, word) in enumerate(values.items()):
            client.stage_value(name, word)
            if i % 9 == 5:
                transport.interleave_rx_noise(NOISE)
        client.commit_patch(values)
        transport.interleave_rx_noise(NOISE)
        client.reset()

        self.assertEqual(client.state, "ready")
        self.assertIsNotNone(core.active_patch)
        # Noise on the wire changed nothing about the product model.
        self.assertEqual(transport.sent_frames, clean_transport.sent_frames)
        self.assertEqual(bytes(transport.delivered_response), bytes(clean_transport.delivered_response))
        self.assertEqual(core.active_patch, clean_core.active_patch)

    def test_partial_write_on_every_binding_leaves_the_product_model_intact(self):
        """A short write on any binding is dropped whole and then resynced.

        TRANSPORTS.md forbids a conforming ``send`` from reporting a partial
        write as success. Every binding can short-write (a UART stops
        mid-stream, a CS period ends early, a bulk OUT stalls): the accepted
        prefix goes on the wire, the receiver must drop it by the sync/CRC
        rule, and the identical idempotent frame re-sent afterwards must leave
        the session exactly where the clean run left it.
        """
        clean_core, _, clean_transport = run_canonical_session(
            lambda c: UartBindingTransport(c)
        )
        # A session with no short write gives the core-side receiver nothing to
        # resynchronize past: this is the comparator for the per-binding count.
        self.assertEqual(clean_transport.core_bad_frames, 0)
        for label, factory in (
            ("uart", lambda c: UartBindingTransport(c)),
            ("spi", lambda c: SpiBindingTransport(c)),
            ("usb", lambda c: UsbBindingTransport(c)),
        ):
            core = build_mock_core()
            transport = factory(core)
            client = build_mock_client(core, transport)
            client.negotiate()
            names, _ = name_table_reference()
            values = {
                name: encode_param_word(float(i) / 79.0 - 1.0)
                for i, name in enumerate(names)
            }
            tx = hashlib.sha256(b"binding-parity").digest()[:16]
            client.open_patch(tx, b"binding-parity-sound", len(values))
            for name in names:
                client.declare_name(name)
            for name, word in values.items():
                client.stage_value(name, word)
            client.commit_patch(values)
            # RESET is idempotent: the truncated attempt is re-sent verbatim.
            transport.fail_next_write_after(6)
            client.reset()

            self.assertEqual(len(transport.truncated_writes), 1, label)
            self.assertEqual(len(transport.truncated_writes[0]), 6, label)
            self.assertEqual(len(client.partial_writes), 1, label)
            # The prefix really went on the wire, so the core-side receiver had
            # to drop exactly one truncated frame and resynchronize past it.
            # Every assertion below this one holds vacuously for a transport
            # that never put the prefix on the wire; this one does not.
            self.assertEqual(transport.core_bad_frames, 1, label)
            # Byte-identical retry: same sequence, same bytes, no renegotiation.
            self.assertEqual(
                client.partial_writes[0], transport.sent_frames[-1], label
            )
            # The truncated prefix never became a frame: the accepted frames,
            # the core→host stream and the applied patch match the clean run.
            self.assertEqual(
                transport.sent_frames, clean_transport.sent_frames, label
            )
            self.assertEqual(
                bytes(transport.delivered_response),
                bytes(clean_transport.delivered_response),
                label,
            )
            self.assertEqual(core.active_patch, clean_core.active_patch, label)
            self.assertIs(core.state, SessionState.READY, label)
            self.assertEqual(client.state, "ready", label)

    def test_partial_write_of_a_patch_command_never_applies_it(self):
        """A short write mid-transaction is raised, never silently resumed.

        PATCH_NAME is not idempotent, so the client refuses to guess: the
        truncated frame is reported to the host and the core's staged state is
        untouched, exactly as if the command had never been attempted.
        """
        core = build_mock_core()
        transport = UartBindingTransport(core)
        client = build_mock_client(core, transport)
        client.negotiate()
        names, _ = name_table_reference()
        tx = hashlib.sha256(b"partial-write").digest()[:16]
        client.open_patch(tx, b"partial-write-sound", 2)
        client.declare_name(names[0])
        accepted_frames = len(transport.sent_frames)

        transport.fail_next_write_after(4)
        with self.assertRaises(TransportWriteError) as caught:
            client.declare_name(names[1])
        self.assertEqual(caught.exception.written, 4)
        self.assertGreater(caught.exception.requested, 4)
        # Nothing was delivered: no new accepted frame, no new declaration.
        self.assertEqual(len(transport.sent_frames), accepted_frames)
        self.assertEqual(core._transaction["declared"], [names[0]])
        self.assertEqual(client.state, "patch_open")
        # The wire recovers: the host re-sends the whole command itself and
        # the core's receiver resynchronizes past the truncated prefix.
        client.declare_name(names[1])
        self.assertEqual(core._transaction["declared"], [names[0], names[1]])

    def test_binding_transport_enforces_transport_bound(self):
        # 160 keeps the real HELLO (119 bytes at the canonical identities) on
        # the wire while refusing the 243-byte frame below.
        oversize = encode_frame(
            KIND_COMMAND, CMD_PATCH_OPEN, 1, hashlib.sha256(b"big").digest() + b"\x00" * 200
        )
        self.assertGreater(len(oversize), 160)
        for transport in (
            UartBindingTransport(build_mock_core(), max_frame_bytes=160),
            SpiBindingTransport(build_mock_core(), max_frame_bytes=160),
            UsbBindingTransport(build_mock_core(), max_frame_bytes=160),
        ):
            with self.assertRaises(ValueError):
                transport.send(oversize)

        # The client pre-check refuses an oversized frame before any transport
        # (state must be ready so the refusal comes from the bound check, not
        # the session rule; RESET is the command tried).
        core = build_mock_core()
        transport = UsbBindingTransport(core, max_frame_bytes=160)
        client = build_mock_client(core, transport)
        client.negotiate()
        with self.assertRaises(ClientError):
            client.transact(CMD_RESET, b"\x00" * 240)


if __name__ == "__main__":
    unittest.main()