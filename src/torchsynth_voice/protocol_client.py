"""Host transport client for protocol version 2 (software lane).

Implements the host side of spec/protocol/FRAMING.md, SESSION.md and
PATCH-LOAD.md over any transport satisfying the spec/protocol/TRANSPORTS.md
interface contract (``send`` / ``recv`` / ``max_frame_bytes``). The client
never inspects transport framing beyond the protocol envelope: it resynchronizes
solely by scanning for ``sync`` and validating the frame CRC, per the UART
binding rule, so the same client runs unchanged on any conforming transport.

Scope honesty: this module and :class:`MockTransport` are software-lane
verification tooling against the behavioral mock. Physical UART/SPI/USB
bindings are issue #81's deliverable; nothing here is evidence of hardware,
RTL, synthesis fidelity, or sound playback. The audio transfer/streaming
command set stays unallocated (issue #63): the client refuses any command
outside the landed registry, and audio sourcing is a mock-boundary data path
per MOCK-HARNESS.md, not a protocol command.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from .core_protocol import (
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
    Frame,
    FrameError,
    KIND_COMMAND,
    KIND_ERROR,
    KIND_RESPONSE,
    MIN_FRAME_LEN,
    PROTOCOL_VERSION,
    SYNC,
    Ready,
    decode_frame,
    decode_ready,
    encode_frame,
    encode_hello,
    encode_patch_commit,
    encode_patch_name,
    encode_patch_open,
    encode_patch_value,
    numeric_contract_version_bound,
    patch_hash,
)

IDEMPOTENT_COMMANDS = frozenset({CMD_HELLO, CMD_RESET, CMD_PATCH_ABORT})


class TransportEmpty(Exception):
    """The transport has no buffered bytes to deliver right now."""


class Transport(Protocol):
    """The spec/protocol/TRANSPORTS.md interface contract, structural."""

    max_frame_bytes: int

    def send(self, data: bytes) -> None: ...

    def recv(self) -> bytes: ...


class ClientError(ValueError):
    """Base error for client-side protocol and policy refusals."""


class TransportWriteError(ClientError):
    """The link accepted only a prefix of a frame: a partial write.

    TRANSPORTS.md requires ``send`` to return only once every byte has been
    accepted by the link, or to raise. A transport that could place only part
    of a frame on the wire raises this instead of returning, so a truncated
    frame is never mistaken for a delivered command: the receiver drops the
    prefix whole on the ``sync``/CRC rule (FRAMING.md), therefore no core
    state can have moved and nothing was partially applied.
    """

    def __init__(self, written: int, requested: int):
        super().__init__(
            f"partial write: transport accepted {written} of {requested} frame bytes"
        )
        self.written = written
        self.requested = requested


class ProtocolError(ClientError):
    """The core answered an error response (SESSION.md error codes)."""

    def __init__(self, code: ErrorCode, command: int, sequence: int):
        super().__init__(f"{code.name} in response to command 0x{command:02X}")
        self.code = ErrorCode(code)
        self.command = command
        self.sequence = sequence


class NegotiationError(ProtocolError):
    """A fatal negotiation gate refusal; the session does not exist."""


class ClientStateError(ClientError):
    """A command was attempted in a session state that forbids it."""


class ProtocolTimeout(ClientError):
    """No matching response arrived within the per-command timeout."""


@dataclass(frozen=True)
class ContractIdentity:
    """The negotiated contract identity (protocol + numeric + name table)."""

    protocol_version: int
    numeric_contract_version: bytes
    name_table_sha256: bytes

    def to_dict(self) -> dict:
        return {
            "protocol_version": self.protocol_version,
            "numeric_contract_version": self.numeric_contract_version.hex(),
            "name_table_sha256": self.name_table_sha256.hex(),
        }


class FrameStream:
    """Reassembles protocol frames from arbitrary transport byte chunks.

    Resynchronization is solely by scanning for ``sync`` and validating the
    frame CRC (TRANSPORTS.md); a corrupted frame is dropped whole-point and
    counted, never partially applied (SESSION.md).
    """

    def __init__(self, *, max_frame_bytes: int | None = None):
        self._buffer = bytearray()
        self._max_frame_bytes = max_frame_bytes
        self.bad_frames = 0

    def feed(self, chunk: bytes) -> None:
        self._buffer += chunk

    def _extract(self) -> Frame | None:
        while True:
            start = self._buffer.find(SYNC)
            if start < 0:
                # Drop scanned bytes but keep a possible split sync prefix
                # (the first sync byte may be the last byte of this chunk).
                if self._buffer.endswith(SYNC[:1]):
                    del self._buffer[: len(self._buffer) - 1]
                else:
                    self._buffer.clear()
                return None
            if start:
                del self._buffer[:start]
            if len(self._buffer) < MIN_FRAME_LEN:
                return None
            length = int.from_bytes(self._buffer[7:9], "little")
            total = MIN_FRAME_LEN + length
            if self._max_frame_bytes is not None and total > self._max_frame_bytes:
                del self._buffer[: len(SYNC)]
                self.bad_frames += 1
                continue
            if len(self._buffer) < total:
                return None
            candidate = bytes(self._buffer[:total])
            try:
                frame = decode_frame(candidate)
            except FrameError:
                del self._buffer[: len(SYNC)]
                self.bad_frames += 1
                continue
            del self._buffer[:total]
            return frame

    def next_complete_frame(self) -> Frame | None:
        """Non-blocking complement to ``next_frame``: return the next complete
        frame already assembled in the buffer, or None without waiting for
        more bytes. Used by the TRANSPORTS.md binding models to drain the
        host→core stream on the core side (``transport_binding_models``).
        """
        return self._extract()

    def next_frame(self, recv, *, deadline: float, clock=time.monotonic) -> Frame:
        """Block on ``recv()`` until one valid frame is assembled."""
        while True:
            frame = self._extract()
            if frame is not None:
                return frame
            if clock() >= deadline:
                raise ProtocolTimeout("no complete frame before deadline")
            try:
                self.feed(recv())
            except TransportEmpty:
                if clock() >= deadline:
                    raise ProtocolTimeout("transport drained before deadline") from None


class HostClient:
    """Protocol v2 host: negotiation, patch transactions, retry/timeout policy.

    Sequence values are strictly increasing per session, wrapping modulo
    65536 (SESSION.md). Retries re-send the identical frame with the
    identical sequence: immediately after ``ERR_BUSY`` (the command was never
    enqueued), and after a timeout only for idempotent commands — a timed-out
    non-idempotent command raises instead of guessing, because a retry cannot
    reuse stale state (SESSION.md stale-state rule). A **partial write**
    (:class:`TransportWriteError`) follows the same shape: the truncated frame
    is dropped whole by the receiver, so the command provably never took
    effect, and the identical frame is re-sent for idempotent commands only.
    """

    def __init__(
        self,
        transport: Transport,
        *,
        profile_id: bytes,
        source_version: bytes,
        name_table_sha256: bytes,
        numeric_contract_version: bytes | None = None,
        capabilities: int = 0,
        expected_profile: bytes | None = None,
        sequence_start: int = 1,
        timeout_s: float = 5.0,
        busy_retries: int = 4,
        write_retries: int = 1,
        clock=time.monotonic,
    ):
        self._transport = transport
        self._profile_id = bytes(profile_id)
        self._expected_profile = (
            bytes(expected_profile) if expected_profile is not None else None
        )
        self._source_version = bytes(source_version)
        self._name_table_sha256 = bytes(name_table_sha256)
        self._numeric_contract_version = (
            numeric_contract_version_bound()
            if numeric_contract_version is None
            else bytes(numeric_contract_version)
        )
        self._capabilities = capabilities
        self._timeout_s = timeout_s
        self._busy_retries = busy_retries
        if write_retries < 0:
            raise ClientError("write_retries must be >= 0")
        self._write_retries = write_retries
        self._clock = clock
        self._next_sequence = sequence_start & 0xFFFF
        self._stream = FrameStream(max_frame_bytes=transport.max_frame_bytes)
        self.state = "closed"
        self.ready: Ready | None = None
        self.transaction_id: bytes | None = None
        #: Frames the transport accepted in full, in send order.
        self.frames_sent: list[bytes] = []
        #: Frames a partial write truncated on the wire, in attempt order.
        #: They are attempts, never deliveries: the receiver drops each one.
        self.partial_writes: list[bytes] = []

    @property
    def numeric_contract_version(self) -> bytes:
        return self._numeric_contract_version

    def contract_identity(self) -> ContractIdentity:
        return ContractIdentity(
            protocol_version=PROTOCOL_VERSION,
            numeric_contract_version=self._numeric_contract_version,
            name_table_sha256=self._name_table_sha256,
        )

    def _advance_sequence(self) -> int:
        sequence = self._next_sequence
        self._next_sequence = (sequence + 1) & 0xFFFF
        return sequence

    def _check_registry(self, command: int) -> None:
        if command not in COMMAND_NAMES or command in (CMD_READY,):
            raise ClientStateError(
                f"command 0x{command:02X} is outside the landed registry"
            )

    def transact(
        self,
        command: int,
        payload: bytes = b"",
        *,
        sequence: int | None = None,
        enforce_state: bool = True,
    ) -> Frame:
        """Send one command and return its echoed-sequence response frame.

        ``enforce_state=False`` exists for tests forcing session-state
        violations; production paths always enforce.
        """
        self._check_registry(command)
        if enforce_state:
            self._require_state_for(command)
        if sequence is None:
            sequence = self._advance_sequence()
        if not 0 <= sequence <= 0xFFFF:
            raise ClientError("sequence must fit u16")
        frame_bytes = encode_frame(KIND_COMMAND, command, sequence, payload)
        if len(frame_bytes) > self._transport.max_frame_bytes:
            raise ClientError("frame exceeds transport max_frame_bytes")
        return self._transact_bytes(command, frame_bytes)

    def _transact_bytes(self, command: int, frame_bytes: bytes) -> Frame:
        idempotent = command in IDEMPOTENT_COMMANDS
        busy_left = self._busy_retries
        timeout_left = 1 if idempotent else 0
        write_left = self._write_retries if idempotent else 0
        while True:
            try:
                self._transport.send(frame_bytes)
            except TransportWriteError:
                # The frame is truncated on the wire, so the receiver drops it
                # whole and the command never took effect. Re-send the
                # identical frame for idempotent commands only; a partially
                # written non-idempotent command is raised to the host, which
                # re-sends the whole transaction on a fresh transaction id
                # rather than resuming stale state (SESSION.md).
                self.partial_writes.append(frame_bytes)
                if write_left > 0:
                    write_left -= 1
                    continue
                if command == CMD_HELLO:
                    self.state = "closed"
                raise
            self.frames_sent.append(frame_bytes)
            deadline = self._clock() + self._timeout_s
            try:
                response = self._stream.next_frame(
                    self._transport.recv, deadline=deadline, clock=self._clock
                )
            except ProtocolTimeout:
                if timeout_left > 0:
                    timeout_left -= 1
                    continue
                self.state = "closed" if command == CMD_HELLO else self.state
                raise
            if response.version != PROTOCOL_VERSION:
                raise NegotiationError(
                    ErrorCode.PROTOCOL_VERSION,
                    response.command,
                    response.sequence,
                )
            if response.sequence != int.from_bytes(frame_bytes[5:7], "little"):
                raise ProtocolError(
                    ErrorCode.BAD_SEQUENCE, response.command, response.sequence
                )
            if response.command != command and not (
                command == CMD_HELLO and response.command == CMD_READY
            ):
                raise ProtocolError(
                    ErrorCode.UNSUPPORTED_COMMAND, response.command, response.sequence
                )
            if response.kind == KIND_ERROR:
                if not response.payload:
                    raise ProtocolError(
                        ErrorCode.BAD_FRAME, response.command, response.sequence
                    )
                code = ErrorCode(response.payload[0])
                if code is ErrorCode.BUSY:
                    if busy_left <= 0:
                        raise ProtocolError(code, command, response.sequence)
                    busy_left -= 1
                    continue
                if code is ErrorCode.PROTOCOL_VERSION:
                    self.state = "closed"
                    raise NegotiationError(code, command, response.sequence)
                if code in (ErrorCode.PATCH_HASH_MISMATCH, ErrorCode.TX_TIMEOUT):
                    # SESSION.md: these errors discarded the open transaction;
                    # the core is back in ready. Track it, never reuse stale state.
                    self.state = "ready"
                    self.transaction_id = None
                raise ProtocolError(code, command, response.sequence)
            if response.kind != KIND_RESPONSE:
                raise ProtocolError(
                    ErrorCode.BAD_FRAME, response.command, response.sequence
                )
            return response

    def _require_state_for(self, command: int) -> None:
        if command == CMD_HELLO:
            return
        if command == CMD_RESET:
            if self.state not in ("ready", "patch_open"):
                raise ClientStateError(f"RESET requires ready or patch_open, not {self.state}")
            return
        if command == CMD_PATCH_OPEN:
            if self.state != "ready":
                raise ClientStateError(f"PATCH_OPEN requires ready, not {self.state}")
            return
        if command in (
            CMD_PATCH_NAME,
            CMD_PATCH_VALUE,
            CMD_PATCH_COMMIT,
            CMD_PATCH_ABORT,
        ):
            if self.state != "patch_open":
                raise ClientStateError(
                    f"patch command 0x{command:02X} requires patch_open, not {self.state}"
                )
            return
        raise ClientStateError(f"command 0x{command:02X} has no session rule")

    def negotiate(self) -> Ready:
        """Send HELLO and validate the READY negotiation gates."""
        payload = encode_hello(
            self._profile_id,
            self._source_version,
            self._numeric_contract_version,
            self._capabilities,
        )
        try:
            response = self.transact(CMD_HELLO, payload)
        except ProtocolError as error:
            self.state = "closed"
            raise NegotiationError(error.code, error.command, error.sequence) from error
        if response.command != CMD_READY:
            raise NegotiationError(
                ErrorCode.UNSUPPORTED_COMMAND, response.command, response.sequence
            )
        ready = decode_ready(response.payload)
        if ready.numeric_contract_version != self._numeric_contract_version:
            self.state = "closed"
            raise NegotiationError(
                ErrorCode.PROTOCOL_VERSION, CMD_HELLO, response.sequence
            )
        if (
            self._expected_profile is not None
            and ready.profile_id != self._expected_profile
        ):
            self.state = "closed"
            raise NegotiationError(
                ErrorCode.BAD_STATE, CMD_HELLO, response.sequence
            )
        self.ready = ready
        self.state = "ready"
        return ready

    def reset(self) -> None:
        self._expect_success(self.transact(CMD_RESET))
        self.state = "ready"
        self.transaction_id = None

    def open_patch(
        self,
        transaction_id: bytes,
        sound_identity: bytes,
        declared_name_count: int,
    ) -> None:
        payload = encode_patch_open(
            transaction_id,
            sound_identity,
            self._name_table_sha256,
            declared_name_count,
        )
        self._expect_success(self.transact(CMD_PATCH_OPEN, payload))
        self.transaction_id = bytes(transaction_id)
        self.state = "patch_open"

    def declare_name(self, name: str) -> None:
        self._expect_success(self.transact(CMD_PATCH_NAME, encode_patch_name(name)))

    def stage_value(self, name: str, value: bytes) -> None:
        self._expect_success(
            self.transact(CMD_PATCH_VALUE, encode_patch_value(name, value))
        )

    def commit_patch(self, values: dict[str, bytes]) -> None:
        """Commit atomically under the bound, domain-separated patch hash."""
        digest = patch_hash(
            values, numeric_contract_version=self._numeric_contract_version
        )
        self._expect_success(
            self.transact(CMD_PATCH_COMMIT, encode_patch_commit(digest))
        )
        self.state = "ready"
        self.transaction_id = None

    def abort_patch(self) -> None:
        self._expect_success(self.transact(CMD_PATCH_ABORT))
        self.state = "ready"
        self.transaction_id = None

    def _expect_success(self, response: Frame) -> Frame:
        if response.kind != KIND_RESPONSE:
            raise ProtocolError(
                ErrorCode.BAD_FRAME, response.command, response.sequence
            )
        return response


class MockTransport:
    """In-memory loopback pairing one client with one MockCore.

    Software-only. ``recv`` returns small arbitrary chunks (byte boundaries
    need not match frame boundaries, per TRANSPORTS.md) so the client's
    reassembly path is exercised on every session.
    """

    def __init__(self, core, *, max_frame_bytes: int = 256, recv_chunk: int = 7):
        if recv_chunk < 1:
            raise ValueError("recv_chunk must be >= 1")
        self.core = core
        self.max_frame_bytes = max_frame_bytes
        self._recv_chunk = recv_chunk
        self._rx = bytearray()
        self.sent_frames: list[bytes] = []
        #: Truncated prefixes this transport put on the wire, in attempt order.
        self.truncated_writes: list[bytes] = []
        self._write_budget: int | None = None

    def fail_next_write_after(self, accepted_bytes: int) -> None:
        """Model a link that accepts only ``accepted_bytes`` of the next frame.

        The prefix reaches the wire and the write then fails
        (:class:`TransportWriteError`). The core never sees it: a receiver
        drops a truncated frame whole on the ``sync``/CRC rule, so this
        transport models the receiver's post-drop state directly rather than
        submitting a frame the core could not have decoded.
        """
        if accepted_bytes < 0:
            raise ValueError("accepted_bytes must be >= 0")
        self._write_budget = accepted_bytes

    def send(self, data: bytes) -> None:
        if len(data) > self.max_frame_bytes:
            raise ValueError("frame exceeds transport max_frame_bytes")
        if self._write_budget is not None and self._write_budget < len(data):
            accepted, self._write_budget = self._write_budget, None
            self.truncated_writes.append(bytes(data[:accepted]))
            raise TransportWriteError(accepted, len(data))
        self._write_budget = None
        self.sent_frames.append(bytes(data))
        immediate = self.core.submit(data)
        if immediate is not None:
            self._rx += immediate
            return
        response = self.core.poll()
        if response is not None:
            self._rx += response

    def recv(self) -> bytes:
        if not self._rx:
            raise TransportEmpty()
        chunk = bytes(self._rx[: self._recv_chunk])
        del self._rx[: self._recv_chunk]
        return chunk
