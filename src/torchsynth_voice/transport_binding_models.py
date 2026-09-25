"""In-process conformance models of the TRANSPORTS.md bindings (UART/SPI/USB).

Each class models only the byte- and frame-boundary behavior that
spec/protocol/TRANSPORTS.md assigns to one binding, at the level the software
mock harness qualifies (the real link stand-in at byte-frame level):

- :class:`UartBindingTransport` — continuous full-duplex byte stream. No
  message boundaries exist below the frame in either direction; core responses
  are delivered in arbitrary ``chunk_bytes`` polls that need not match frame
  boundaries. ``interleave_rx_noise`` injects wire garbage before pending
  response bytes: the only error detection is the frame CRC, and the client
  resynchronizes solely by scanning for ``sync`` (TRANSPORTS.md UART binding).
- :class:`SpiBindingTransport` — half-duplex. ``send`` accumulates write-phase
  bytes only; the core responds during the read phase (the first ``recv``
  after a send drives the core side). Delivery windows are CS periods of
  ``cs_period_bytes``; a CS period SHOULD carry whole or multiple frames but
  is not required to, so periods never must align with frame boundaries.
- :class:`UsbBindingTransport` — one bulk OUT and one bulk IN endpoint.
  Delivery on the host→core direction consumes whatever complete frames the
  OUT bytes hold; every ``recv`` returns one full bulk IN packet of
  ``packet_bytes`` that may hold whole frames, several frames, or a partial
  frame. USB packet boundary is not framing.

Every binding additionally models the **partial write** TRANSPORTS.md forbids
a conforming ``send`` from reporting as success
(:meth:`_BindingTransport.fail_next_write_after`): the accepted prefix goes on
the wire and the write raises, so the receiver must drop the truncated frame
whole and resynchronize while the session state stays exactly where it was.

Scope honesty: these are software-lane conformance doubles, not physical
drivers (physical UART/SPI/USB bindings are issue #81's deliverable) and they
make no hardware, RTL, synthesis-fidelity, or playback claim. They exist to
prove the product model — client, protocol, contract identity, core behavior —
is invariant to which binding carries the bytes: "which carrier is chosen is a
configuration decision, not a protocol change" (TRANSPORTS.md). Tests:
``tests/test_transport_bindings.py``.
"""

from __future__ import annotations

from .core_protocol import encode_frame
from .protocol_client import FrameStream, TransportEmpty, TransportWriteError

__all__ = [
    "SpiBindingTransport",
    "UartBindingTransport",
    "UsbBindingTransport",
]


class _BindingTransport:
    """Shared client↔core pipe; subclasses shape delivery per one binding."""

    def __init__(self, core, *, max_frame_bytes: int = 512):
        if max_frame_bytes < 1:
            raise ValueError("max_frame_bytes must be >= 1")
        self.core = core
        self.max_frame_bytes = max_frame_bytes
        #: Client-side command frame bytes, in send order (parity asserts).
        self.sent_frames: list[bytes] = []
        #: Bytes the core produced for the host, in response order. Wire noise
        #: never enters here; this is the clean core→host stream.
        self._delivered_response = bytearray()
        #: Byte count of each non-empty ``recv`` (boundary asserts).
        self.recv_chunk_sizes: list[int] = []
        #: Truncated prefixes a partial write put on the wire, in order.
        self.truncated_writes: list[bytes] = []
        self._sink = FrameStream(max_frame_bytes=max_frame_bytes)
        self._tx = bytearray()  # pending host→core write-phase bytes
        self._rx = bytearray()  # pending core→host bytes
        self._write_budget: int | None = None

    @property
    def delivered_response(self) -> bytes:
        return bytes(self._delivered_response)

    def fail_next_write_after(self, accepted_bytes: int) -> None:
        """Model a link that accepts only ``accepted_bytes`` of the next frame.

        Every binding can short-write: a UART stops mid-byte-stream, a CS
        period ends early, a bulk OUT transfer stalls. The accepted prefix
        really goes on the wire — the core-side receiver must drop it whole by
        the ``sync``/CRC rule and resynchronize — and ``send`` then raises
        :class:`~torchsynth_voice.protocol_client.TransportWriteError` rather
        than returning as though the frame had been delivered.
        """
        if accepted_bytes < 0:
            raise ValueError("accepted_bytes must be >= 0")
        self._write_budget = accepted_bytes

    def send(self, data: bytes) -> None:
        if len(data) > self.max_frame_bytes:
            raise ValueError("frame exceeds transport max_frame_bytes")
        if self._write_budget is not None and self._write_budget < len(data):
            accepted, self._write_budget = self._write_budget, None
            prefix = bytes(data[:accepted])
            self.truncated_writes.append(prefix)
            self._tx += prefix
            raise TransportWriteError(accepted, len(data))
        self._write_budget = None
        self.sent_frames.append(bytes(data))
        self._tx += data

    def recv(self) -> bytes:
        raise NotImplementedError

    # -- core side ---------------------------------------------------------

    def _feed_core_tx(self) -> None:
        """Deliver the host→core bytes to the core; process each complete frame.

        The core-side receiver scans for ``sync`` and validates the frame CRC
        exactly like the client (FrameStream); a core that has retired to
        ready still refuses bad frames the same way, never partially applying
        them.
        """
        self._sink.feed(bytes(self._tx))
        self._tx.clear()
        while True:
            frame = self._sink.next_complete_frame()
            if frame is None:
                return
            frame_bytes = encode_frame(
                frame.kind, frame.command, frame.sequence, frame.payload
            )
            immediate = self.core.submit(frame_bytes)
            if immediate is not None:
                self._rx += immediate
                self._delivered_response += immediate
                continue
            response = self.core.poll()
            if response is not None:
                self._rx += response
                self._delivered_response += response

    def _deliver(self, limit: int) -> bytes:
        chunk = bytes(self._rx[:limit])
        del self._rx[:limit]
        if chunk:
            self.recv_chunk_sizes.append(len(chunk))
        return chunk

    def _recv_or_empty(self, limit: int) -> bytes:
        chunk = self._deliver(limit)
        if not chunk:
            raise TransportEmpty()
        return chunk


class UartBindingTransport(_BindingTransport):
    """UART binding model: continuous byte stream, no sub-frame boundaries."""

    def __init__(self, core, *, chunk_bytes: int = 5, max_frame_bytes: int = 512):
        super().__init__(core, max_frame_bytes=max_frame_bytes)
        if chunk_bytes < 1:
            raise ValueError("chunk_bytes must be >= 1")
        self._chunk_bytes = chunk_bytes

    def send(self, data: bytes) -> None:
        # Whatever the link accepted is already on the stream, partial write
        # or not; the core-side receiver resynchronizes on sync/CRC.
        try:
            super().send(data)
        finally:
            self._feed_core_tx()

    def recv(self) -> bytes:
        return self._recv_or_empty(self._chunk_bytes)

    def interleave_rx_noise(self, noise: bytes) -> None:
        """Wire garbage placed on the stream before any pending response bytes.

        Models the UART corruption the binding rule addresses: the client
        must resynchronize by scanning for ``sync``; nothing here inspects
        frame content.
        """
        self._rx = bytearray(noise) + self._rx


class SpiBindingTransport(_BindingTransport):
    """SPI binding model: half-duplex, CS-period delivery windows."""

    def __init__(self, core, *, cs_period_bytes: int = 7, max_frame_bytes: int = 512):
        super().__init__(core, max_frame_bytes=max_frame_bytes)
        if cs_period_bytes < 1:
            raise ValueError("cs_period_bytes must be >= 1")
        self._cs_period_bytes = cs_period_bytes
        self._in_read_phase = False

    def send(self, data: bytes) -> None:
        # A new CS write period: the core sees nothing until the read phase.
        self._in_read_phase = False
        super().send(data)

    def recv(self) -> bytes:
        if not self._in_read_phase:
            self._in_read_phase = True
            self._feed_core_tx()
        chunk = self._deliver(self._cs_period_bytes)
        if not chunk:
            self._in_read_phase = False
            raise TransportEmpty()
        return chunk


class UsbBindingTransport(_BindingTransport):
    """USB binding model: bulk OUT/IN endpoints with fixed packet quantization."""

    def __init__(self, core, *, packet_bytes: int = 31, max_frame_bytes: int = 512):
        super().__init__(core, max_frame_bytes=max_frame_bytes)
        if packet_bytes < 1:
            raise ValueError("packet_bytes must be >= 1")
        self._packet_bytes = packet_bytes

    def send(self, data: bytes) -> None:
        # A stalled bulk OUT transfer still delivered the bytes it moved.
        try:
            super().send(data)
        finally:
            self._feed_core_tx()

    def recv(self) -> bytes:
        # One full bulk IN packet per poll (short only at drain tail).
        return self._recv_or_empty(self._packet_bytes)
