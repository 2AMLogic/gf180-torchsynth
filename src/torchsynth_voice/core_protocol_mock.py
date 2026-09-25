"""Mock core for the core/host protocol: session, patch staging, backpressure.

Implements spec/protocol/SESSION.md and spec/protocol/PATCH-LOAD.md as a
byte-level round-trip model. It verifies protocol behavior only; it is not
evidence of synthesis fidelity or RTL equivalence. The regions protocol
version 1 carried opaquely are bound to the accepted DR-0008 register; the
still-opaque fields (sound_identity, profile_id, locks) remain verbatim.
"""

from __future__ import annotations

import time
from collections import deque
from enum import Enum

from fractions import Fraction

from .core_protocol import (
    CAP_NAME_KEYED_PATCH_LOAD,
    CAP_RENDER,
    CAP_RESET,
    CMD_ERROR,
    CMD_HELLO,
    CMD_NOISE_STREAM,
    CMD_PATCH_ABORT,
    CMD_PATCH_COMMIT,
    CMD_PATCH_NAME,
    CMD_PATCH_OPEN,
    CMD_PATCH_VALUE,
    CMD_READY,
    CMD_RENDER_TRIGGER,
    CMD_RESET,
    COMMAND_NAMES,
    NOISE_STREAM_CLIP_BYTES,
    PARAM_VALUE_BYTES,
    RENDER_PASS_1,
    RENDER_PASS_2,
    ErrorCode,
    FrameError,
    KIND_COMMAND,
    KIND_ERROR,
    KIND_RESPONSE,
    KNOWN_CAPABILITIES,
    PROTOCOL_VERSION,
    Ready,
    decode_frame,
    decode_hello,
    decode_noise_stream,
    decode_patch_commit,
    decode_patch_name,
    decode_patch_open,
    decode_patch_value,
    decode_render_trigger,
    encode_audio_payload,
    encode_frame,
    encode_ready,
    numeric_contract_version_bound,
    patch_hash,
)

IDEMPOTENT_COMMANDS = frozenset({CMD_HELLO, CMD_RESET, CMD_PATCH_ABORT})
PATCH_COMMANDS = frozenset(
    {
        CMD_PATCH_OPEN,
        CMD_PATCH_NAME,
        CMD_PATCH_VALUE,
        CMD_PATCH_COMMIT,
        CMD_PATCH_ABORT,
    }
)


RENDER_COMMANDS = frozenset({CMD_RENDER_TRIGGER, CMD_NOISE_STREAM})


class SessionState(Enum):
    CLOSED = "closed"
    READY = "ready"
    PATCH_OPEN = "patch_open"
    RENDERING = "rendering"


class MockCore:
    def __init__(
        self,
        *,
        name_table,
        name_table_sha256: bytes,
        capabilities: int = KNOWN_CAPABILITIES,
        profile_id: bytes = b"torchsynth-1-voice-default",
        numeric_contract_version: bytes | None = None,
        locks: bytes = b"",
        max_payload: int = 1024,
        rx_queue_depth: int = 2,
        patch_timeout_s: float = 5.0,
        clock=time.monotonic,
        audio_source=None,
        noise_stream_bytes: int = NOISE_STREAM_CLIP_BYTES,
    ):
        self._name_table = frozenset(name_table)
        self._name_table_sha256 = bytes(name_table_sha256)
        self._supported_capabilities = capabilities & KNOWN_CAPABILITIES
        self._profile_id = bytes(profile_id)
        if numeric_contract_version is None:
            numeric_contract_version = numeric_contract_version_bound()
        self._numeric_contract_version = bytes(numeric_contract_version)
        self._locks = bytes(locks)
        self._max_payload = max_payload
        self._rx_queue_depth = rx_queue_depth
        self._patch_timeout_s = patch_timeout_s
        self._clock = clock
        # Per-pass noise-stream length. The product profile value is
        # NOISE_STREAM_CLIP_BYTES (705,600 B); a smaller value is a
        # test-scale knob of this behavioral mock only, never a protocol
        # parameter (spec/protocol/RENDER-TRIGGER.md).
        if noise_stream_bytes < 1:
            raise ValueError("noise_stream_bytes must be >= 1")
        self._noise_stream_bytes = noise_stream_bytes

        #: The open render (RENDER-TRIGGER.md): binding + per-pass progress.
        self.render = None
        #: Bindings of clips whose pass 2 completed, in completion order.
        self.completed_clips = []
        #: (binding, ErrorCode) of clips discarded entire, in discard order.
        self.discarded_clips = []

        self.state = SessionState.CLOSED
        self.granted_capabilities = 0
        self.active_patch = None
        self._audio_source = None
        if audio_source is not None:
            self.set_audio_source(audio_source)
        self._queue = deque()
        self._replay_cache = {}
        self._last_sequence = None
        self._transaction = None
        self._finished_transaction_id = None
        self._expired_transaction_id = None

    @property
    def pending(self) -> int:
        return len(self._queue)

    def set_audio_source(self, samples) -> None:
        """Source audio from fixed golden vectors (MOCK-HARNESS.md).

        The samples are exact host-side reals; they are stored verbatim and
        packed on demand through the bound C1 codec. This is a software data
        source at the mock boundary, not RTL behavior and not a transfer
        command: the audio transfer/streaming command set stays unallocated
        pending issue #63.
        """
        validated = []
        for sample in samples:
            try:
                value = Fraction(sample)
            except (TypeError, ValueError, OverflowError) as error:
                raise ValueError(
                    "audio source samples must be finite reals"
                ) from error
            validated.append(value)
        self._audio_source = tuple(validated)

    @property
    def audio_payload_bytes(self) -> bytes:
        """The sourced audio packed per the bound 3-byte Q2.21 sample codec."""
        if self._audio_source is None:
            raise ValueError("no audio source; supply fixed golden vectors first")
        return encode_audio_payload(self._audio_source)

    @property
    def audio_source_samples(self) -> tuple:
        """The stored golden samples as exact rationals (readback)."""
        if self._audio_source is None:
            raise ValueError("no audio source; supply fixed golden vectors first")
        return self._audio_source

    def submit(self, frame_bytes: bytes):
        try:
            frame = decode_frame(frame_bytes)
        except FrameError:
            return self._error_frame(CMD_ERROR, 0, ErrorCode.BAD_FRAME)
        if len(self._queue) >= self._rx_queue_depth:
            return self._error_frame(frame.command, frame.sequence, ErrorCode.BUSY)
        self._queue.append(frame_bytes)
        return None

    def poll(self):
        self._expire()
        if not self._queue:
            return None
        return self._process(self._queue.popleft())

    def handle_frame(self, frame_bytes: bytes) -> bytes:
        immediate = self.submit(frame_bytes)
        if immediate is not None:
            return immediate
        response = self.poll()
        if response is None:
            return self._error_frame(CMD_ERROR, 0, ErrorCode.BAD_FRAME)
        return response

    def _expire(self):
        if self._transaction is None:
            return
        if self._clock() - self._transaction["last_activity"] >= self._patch_timeout_s:
            self._retire_transaction()
            self._expired_transaction_id = self._finished_transaction_id
            self.state = SessionState.READY

    def _retire_transaction(self):
        if self._transaction is not None:
            self._finished_transaction_id = self._transaction["transaction_id"]
        self._transaction = None

    def _error_frame(self, command: int, sequence: int, code: ErrorCode) -> bytes:
        return encode_frame(KIND_ERROR, command, sequence, bytes([code]))

    def _success_frame(self, command: int, sequence: int) -> bytes:
        return encode_frame(KIND_RESPONSE, command, sequence)

    def _respond(self, command: int, sequence: int, error: ErrorCode | None = None) -> bytes:
        if error is not None:
            return self._error_frame(command, sequence, error)
        return self._success_frame(command, sequence)

    def _process(self, frame_bytes: bytes) -> bytes:
        try:
            frame = decode_frame(frame_bytes)
        except FrameError:
            return self._error_frame(CMD_ERROR, 0, ErrorCode.BAD_FRAME)

        if frame.version != PROTOCOL_VERSION:
            self._fatal()
            return self._error_frame(frame.command, frame.sequence, ErrorCode.PROTOCOL_VERSION)

        if frame.kind != KIND_COMMAND:
            return self._respond(frame.command, frame.sequence, ErrorCode.BAD_FRAME)

        if len(frame.payload) > self._max_payload:
            return self._respond(frame.command, frame.sequence, ErrorCode.PAYLOAD_LENGTH)

        command = frame.command
        if command not in COMMAND_NAMES or command in (CMD_READY, CMD_ERROR):
            return self._respond(command, frame.sequence, ErrorCode.UNSUPPORTED_COMMAND)

        key = (command, frame.sequence)
        if key in self._replay_cache:
            cached_request, cached_response = self._replay_cache[key]
            if cached_request == frame_bytes:
                return cached_response
            return self._respond(command, frame.sequence, ErrorCode.BAD_SEQUENCE)

        if self.state is SessionState.CLOSED and command != CMD_HELLO:
            return self._respond(command, frame.sequence, ErrorCode.BAD_STATE)

        if (
            self._last_sequence is not None
            and command not in IDEMPOTENT_COMMANDS
            and frame.sequence <= self._last_sequence
        ):
            return self._respond(command, frame.sequence, ErrorCode.BAD_SEQUENCE)

        handler = {
            CMD_HELLO: self._handle_hello,
            CMD_RESET: self._handle_reset,
            CMD_PATCH_OPEN: self._handle_patch_open,
            CMD_PATCH_NAME: self._handle_patch_name,
            CMD_PATCH_VALUE: self._handle_patch_value,
            CMD_PATCH_COMMIT: self._handle_patch_commit,
            CMD_PATCH_ABORT: self._handle_patch_abort,
            CMD_RENDER_TRIGGER: self._handle_render_trigger,
            CMD_NOISE_STREAM: self._handle_noise_stream,
        }[command]
        response = handler(frame)

        if command in IDEMPOTENT_COMMANDS:
            self._replay_cache[key] = (frame_bytes, response)
        if command != CMD_HELLO and (
            self._last_sequence is None or frame.sequence > self._last_sequence
        ):
            self._last_sequence = frame.sequence
        return response

    def _fatal(self):
        self.state = SessionState.CLOSED
        self._queue.clear()
        self._replay_cache.clear()
        self._last_sequence = None
        self._transaction = None
        self._finished_transaction_id = None
        self._expired_transaction_id = None
        self.granted_capabilities = 0
        self._discard_render("fatal")

    def _discard_render(self, reason) -> None:
        """Drop the open render whole (DR-0010: no resumable render).

        ``reason`` is the ErrorCode that discarded the clip, or a string for
        a session-level discard (``"reset"``, ``"hello"``, ``"fatal"``). A
        discarded clip has no valid output; only a completed pass 2 makes a
        clip valid (spec/protocol/RENDER-TRIGGER.md).
        """
        if self.render is not None:
            self.discarded_clips.append((self._render_binding(), reason))
        self.render = None

    def _render_binding(self) -> dict:
        return {
            "sound_identity": self.render["sound_identity"],
            "noise_stream_sha256": self.render["noise_stream_sha256"],
        }

    def _handle_hello(self, frame):
        try:
            hello = decode_hello(frame.payload)
        except FrameError:
            return self._respond(frame.command, frame.sequence, ErrorCode.PAYLOAD_LENGTH)

        if hello.numeric_contract_version != self._numeric_contract_version:
            self._fatal()
            return self._error_frame(
                frame.command, frame.sequence, ErrorCode.PROTOCOL_VERSION
            )

        self._retire_transaction()
        self._discard_render("hello")
        self._replay_cache.clear()
        self._last_sequence = frame.sequence
        self._finished_transaction_id = None
        self._expired_transaction_id = None
        self.state = SessionState.READY
        self.granted_capabilities = hello.capabilities & self._supported_capabilities
        ready = Ready(
            profile_id=self._profile_id,
            numeric_contract_version=self._numeric_contract_version,
            locks=self._locks,
            capabilities=self.granted_capabilities,
            max_payload=self._max_payload,
            rx_queue_depth=self._rx_queue_depth,
            patch_timeout_ms=int(self._patch_timeout_s * 1000),
        )
        return encode_frame(KIND_RESPONSE, CMD_READY, frame.sequence, encode_ready(ready))

    def _handle_reset(self, frame):
        if not self.granted_capabilities & CAP_RESET:
            return self._respond(frame.command, frame.sequence, ErrorCode.UNSUPPORTED_COMMAND)
        self._retire_transaction()
        self._discard_render("reset")
        self.state = SessionState.READY
        return self._success_frame(frame.command, frame.sequence)

    def _handle_patch_open(self, frame):
        if not self.granted_capabilities & CAP_NAME_KEYED_PATCH_LOAD:
            return self._respond(frame.command, frame.sequence, ErrorCode.UNSUPPORTED_COMMAND)
        if self.state is SessionState.PATCH_OPEN:
            return self._respond(frame.command, frame.sequence, ErrorCode.PATCH_TX_ACTIVE)
        if self.state is SessionState.RENDERING:
            # Render and patch frames never interleave (RENDER-TRIGGER.md).
            return self._respond(frame.command, frame.sequence, ErrorCode.BAD_STATE)
        try:
            opened = decode_patch_open(frame.payload)
        except FrameError:
            return self._respond(frame.command, frame.sequence, ErrorCode.PAYLOAD_LENGTH)
        if opened.name_table_sha256 != self._name_table_sha256:
            return self._respond(frame.command, frame.sequence, ErrorCode.UNKNOWN_NAME)
        if self._finished_transaction_id == opened.transaction_id:
            return self._respond(frame.command, frame.sequence, ErrorCode.BAD_SEQUENCE)
        self._expired_transaction_id = None
        self._transaction = {
            "transaction_id": opened.transaction_id,
            "sound_identity": opened.sound_identity,
            "declared_count": opened.declared_name_count,
            "declared": [],
            "staged": {},
            "last_activity": self._clock(),
        }
        self.state = SessionState.PATCH_OPEN
        return self._success_frame(frame.command, frame.sequence)

    def _require_open_transaction(self, frame):
        if self._transaction is None:
            if self._expired_transaction_id is not None:
                return self._respond(frame.command, frame.sequence, ErrorCode.TX_TIMEOUT)
            return self._respond(frame.command, frame.sequence, ErrorCode.BAD_SEQUENCE)
        return None

    def _touch(self):
        self._transaction["last_activity"] = self._clock()

    def _handle_patch_name(self, frame):
        if not self.granted_capabilities & CAP_NAME_KEYED_PATCH_LOAD:
            return self._respond(frame.command, frame.sequence, ErrorCode.UNSUPPORTED_COMMAND)
        no_transaction = self._require_open_transaction(frame)
        if no_transaction is not None:
            return no_transaction
        try:
            name = decode_patch_name(frame.payload)
        except FrameError:
            return self._respond(frame.command, frame.sequence, ErrorCode.PAYLOAD_LENGTH)
        transaction = self._transaction
        if len(transaction["declared"]) >= transaction["declared_count"]:
            return self._respond(frame.command, frame.sequence, ErrorCode.PATCH_INCOMPLETE)
        if name not in self._name_table:
            return self._respond(frame.command, frame.sequence, ErrorCode.UNKNOWN_NAME)
        if name in transaction["declared"]:
            return self._respond(frame.command, frame.sequence, ErrorCode.DUPLICATE_NAME)
        transaction["declared"].append(name)
        self._touch()
        return self._success_frame(frame.command, frame.sequence)

    def _handle_patch_value(self, frame):
        if not self.granted_capabilities & CAP_NAME_KEYED_PATCH_LOAD:
            return self._respond(frame.command, frame.sequence, ErrorCode.UNSUPPORTED_COMMAND)
        no_transaction = self._require_open_transaction(frame)
        if no_transaction is not None:
            return no_transaction
        try:
            name, value = decode_patch_value(frame.payload)
        except FrameError:
            return self._respond(frame.command, frame.sequence, ErrorCode.PAYLOAD_LENGTH)
        if len(value) != PARAM_VALUE_BYTES:
            return self._respond(frame.command, frame.sequence, ErrorCode.PAYLOAD_LENGTH)
        transaction = self._transaction
        if name not in transaction["declared"]:
            return self._respond(frame.command, frame.sequence, ErrorCode.UNKNOWN_NAME)
        if name in transaction["staged"]:
            if transaction["staged"][name] == value:
                self._touch()
                return self._success_frame(frame.command, frame.sequence)
            return self._respond(frame.command, frame.sequence, ErrorCode.DUPLICATE_NAME)
        transaction["staged"][name] = value
        self._touch()
        return self._success_frame(frame.command, frame.sequence)

    def _handle_patch_commit(self, frame):
        if not self.granted_capabilities & CAP_NAME_KEYED_PATCH_LOAD:
            return self._respond(frame.command, frame.sequence, ErrorCode.UNSUPPORTED_COMMAND)
        no_transaction = self._require_open_transaction(frame)
        if no_transaction is not None:
            return no_transaction
        try:
            declared_hash = decode_patch_commit(frame.payload)
        except FrameError:
            return self._respond(frame.command, frame.sequence, ErrorCode.PAYLOAD_LENGTH)
        transaction = self._transaction
        missing = set(transaction["declared"]) - set(transaction["staged"])
        if missing or len(transaction["declared"]) != transaction["declared_count"]:
            return self._respond(frame.command, frame.sequence, ErrorCode.PATCH_INCOMPLETE)
        computed = patch_hash(
            transaction["staged"],
            numeric_contract_version=self._numeric_contract_version,
        )
        if computed != declared_hash:
            self._retire_transaction()
            self.state = SessionState.READY
            return self._respond(frame.command, frame.sequence, ErrorCode.PATCH_HASH_MISMATCH)
        self.active_patch = {
            "transaction_id": transaction["transaction_id"],
            "sound_identity": transaction["sound_identity"],
            "values": dict(transaction["staged"]),
        }
        self._retire_transaction()
        self.state = SessionState.READY
        return self._success_frame(frame.command, frame.sequence)

    def _handle_patch_abort(self, frame):
        if not self.granted_capabilities & CAP_NAME_KEYED_PATCH_LOAD:
            return self._respond(frame.command, frame.sequence, ErrorCode.UNSUPPORTED_COMMAND)
        if self.state is SessionState.RENDERING:
            # A patch abort must not silently end a render; RESET does that.
            return self._respond(frame.command, frame.sequence, ErrorCode.BAD_STATE)
        self._retire_transaction()
        self.state = SessionState.READY
        return self._success_frame(frame.command, frame.sequence)

    # --- render trigger + host-fed noise stream (RENDER-TRIGGER.md) --------

    def _discard_with(self, frame, code: ErrorCode) -> bytes:
        """Discard the open clip entire, return to ready, answer ``code``."""
        self._discard_render(code)
        self.state = SessionState.READY
        return self._respond(frame.command, frame.sequence, code)

    def _handle_render_trigger(self, frame):
        if not self.granted_capabilities & CAP_RENDER:
            return self._respond(frame.command, frame.sequence, ErrorCode.UNSUPPORTED_COMMAND)
        if self.state is SessionState.PATCH_OPEN:
            return self._respond(frame.command, frame.sequence, ErrorCode.BAD_STATE)
        try:
            trigger = decode_render_trigger(frame.payload)
        except FrameError:
            return self._respond(frame.command, frame.sequence, ErrorCode.PAYLOAD_LENGTH)

        if trigger.pass_index == RENDER_PASS_1:
            if self.state is SessionState.RENDERING:
                # A second trigger never joins or replaces an open clip.
                return self._respond(frame.command, frame.sequence, ErrorCode.BAD_STATE)
            if self.active_patch is None:
                return self._respond(frame.command, frame.sequence, ErrorCode.BAD_STATE)
            if trigger.sound_identity != self.active_patch["sound_identity"]:
                # No clip existed yet, so nothing is discarded.
                return self._respond(
                    frame.command, frame.sequence, ErrorCode.RENDER_BINDING
                )
            self.render = {
                "sound_identity": trigger.sound_identity,
                "noise_stream_sha256": trigger.noise_stream_sha256,
                "pass_index": RENDER_PASS_1,
                "accepted": 0,
                "pass1_complete": False,
            }
            self.state = SessionState.RENDERING
            return self._success_frame(frame.command, frame.sequence)

        # Pass 2.
        if self.state is not SessionState.RENDERING:
            return self._respond(frame.command, frame.sequence, ErrorCode.BAD_SEQUENCE)
        render = self.render
        if render["pass_index"] != RENDER_PASS_1 or not render["pass1_complete"]:
            return self._discard_with(frame, ErrorCode.NOISE_STREAM)
        # The DR-0010 binding: one sound identity + one (declared) noise
        # stream digest across both passes, checked before any pass-2 byte.
        if (
            trigger.sound_identity != render["sound_identity"]
            or trigger.noise_stream_sha256 != render["noise_stream_sha256"]
        ):
            return self._discard_with(frame, ErrorCode.RENDER_BINDING)
        render["pass_index"] = RENDER_PASS_2
        render["accepted"] = 0
        return self._success_frame(frame.command, frame.sequence)

    def _handle_noise_stream(self, frame):
        if not self.granted_capabilities & CAP_RENDER:
            return self._respond(frame.command, frame.sequence, ErrorCode.UNSUPPORTED_COMMAND)
        if self.state is SessionState.PATCH_OPEN:
            return self._respond(frame.command, frame.sequence, ErrorCode.BAD_STATE)
        try:
            chunk = decode_noise_stream(frame.payload)
        except FrameError:
            return self._respond(frame.command, frame.sequence, ErrorCode.PAYLOAD_LENGTH)
        if self.state is not SessionState.RENDERING:
            return self._respond(frame.command, frame.sequence, ErrorCode.BAD_SEQUENCE)
        render = self.render
        if (
            chunk.pass_index != render["pass_index"]
            or chunk.offset != render["accepted"]
            or (render["pass_index"] == RENDER_PASS_1 and render["pass1_complete"])
            or render["accepted"] + len(chunk.data) > self._noise_stream_bytes
        ):
            return self._discard_with(frame, ErrorCode.NOISE_STREAM)
        # Consumed as it arrives; no clip buffer is retained (DR-0010).
        render["accepted"] += len(chunk.data)
        if render["accepted"] == self._noise_stream_bytes:
            if render["pass_index"] == RENDER_PASS_1:
                render["pass1_complete"] = True
            else:
                self.completed_clips.append(self._render_binding())
                self.render = None
                self.state = SessionState.READY
        return self._success_frame(frame.command, frame.sequence)
