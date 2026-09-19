"""Core/host protocol framing: envelope, opaque encoding, negotiation structures.

Implements spec/protocol/FRAMING.md. Everything numeric-gated is an opaque
byte string or a named placeholder width; final binding waits on #53.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Mapping

PROTOCOL_VERSION = 1

SYNC = b"\x67\xf1"
HEADER_LEN = 9
CRC_LEN = 2
MIN_FRAME_LEN = HEADER_LEN + CRC_LEN
MAX_PAYLOAD = 0xFFFF

KIND_COMMAND = 0x01
KIND_RESPONSE = 0x02
KIND_ERROR = 0x03

CMD_HELLO = 0x01
CMD_READY = 0x02
CMD_PATCH_OPEN = 0x10
CMD_PATCH_NAME = 0x11
CMD_PATCH_VALUE = 0x12
CMD_PATCH_COMMIT = 0x13
CMD_PATCH_ABORT = 0x14
CMD_RESET = 0x20
CMD_ERROR = 0xFE

COMMAND_NAMES = {
    CMD_HELLO: "HELLO",
    CMD_READY: "READY",
    CMD_PATCH_OPEN: "PATCH_OPEN",
    CMD_PATCH_NAME: "PATCH_NAME",
    CMD_PATCH_VALUE: "PATCH_VALUE",
    CMD_PATCH_COMMIT: "PATCH_COMMIT",
    CMD_PATCH_ABORT: "PATCH_ABORT",
    CMD_RESET: "RESET",
    CMD_ERROR: "ERROR",
}

CAP_NAME_KEYED_PATCH_LOAD = 1 << 0
CAP_RESET = 1 << 1
KNOWN_CAPABILITIES = CAP_NAME_KEYED_PATCH_LOAD | CAP_RESET

NUMERIC_CONTRACT_UNBOUND = b"unbound:#53"


class ErrorCode(IntEnum):
    UNSUPPORTED_COMMAND = 0x01
    PROTOCOL_VERSION = 0x02
    BAD_FRAME = 0x03
    BAD_SEQUENCE = 0x04
    BUSY = 0x05
    BAD_STATE = 0x06
    PATCH_TX_ACTIVE = 0x07
    PATCH_INCOMPLETE = 0x08
    PATCH_HASH_MISMATCH = 0x09
    UNKNOWN_NAME = 0x0A
    DUPLICATE_NAME = 0x0B
    TX_TIMEOUT = 0x0C
    PAYLOAD_LENGTH = 0x0D


@dataclass(frozen=True)
class PlaceholderWidth:
    name: str
    encoded_as: str
    deferred_to: str


PLACEHOLDER_WIDTHS = (
    PlaceholderWidth("patch_value", "length-prefixed opaque bytes", "#53"),
    PlaceholderWidth("numeric_contract_version", "length-prefixed opaque bytes", "#53"),
    PlaceholderWidth("patch_hash", "length-prefixed opaque bytes", "#53"),
    PlaceholderWidth("sound_identity", "length-prefixed opaque bytes", "#12/#88"),
    PlaceholderWidth("profile_id", "length-prefixed opaque bytes", "profile naming"),
    PlaceholderWidth("locks", "length-prefixed opaque bytes", "not defined by this subset"),
)


class FrameError(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class PayloadError(FrameError):
    pass


def crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
    return crc & 0xFFFF


@dataclass(frozen=True)
class Frame:
    version: int
    kind: int
    command: int
    sequence: int
    payload: bytes


def encode_frame(kind: int, command: int, sequence: int, payload: bytes = b"") -> bytes:
    if not 0 <= kind <= 0xFF or not 0 <= command <= 0xFF:
        raise ValueError("kind and command must be bytes")
    if not 0 <= sequence <= 0xFFFF:
        raise ValueError("sequence must fit u16")
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload exceeds u16 length")
    body = struct.pack(
        "<BBBH", PROTOCOL_VERSION, kind, command, sequence
    ) + struct.pack("<H", len(payload)) + payload
    return SYNC + body + struct.pack("<H", crc16_ccitt_false(body))


def decode_frame(data: bytes) -> Frame:
    if len(data) < MIN_FRAME_LEN:
        raise FrameError("short_frame")
    if data[:2] != SYNC:
        raise FrameError("bad_sync")
    length = struct.unpack("<H", data[7:9])[0]
    end = HEADER_LEN + length
    if len(data) != end + CRC_LEN:
        raise FrameError("length_mismatch")
    (wire_crc,) = struct.unpack("<H", data[end:])
    if crc16_ccitt_false(data[2:end]) != wire_crc:
        raise FrameError("bad_crc")
    version, kind, command, sequence = struct.unpack("<BBBH", data[2:7])
    return Frame(version, kind, command, sequence, data[HEADER_LEN:end])


def pack_opaque(value: bytes) -> bytes:
    if len(value) > MAX_PAYLOAD:
        raise ValueError("opaque region exceeds u16 length")
    return struct.pack("<H", len(value)) + value


class PayloadReader:
    def __init__(self, payload: bytes):
        self._data = payload
        self._offset = 0

    def take(self, count: int) -> bytes:
        if self._offset + count > len(self._data):
            raise PayloadError("payload_truncated")
        chunk = self._data[self._offset : self._offset + count]
        self._offset += count
        return chunk

    def take_u8(self) -> int:
        return self.take(1)[0]

    def take_u16(self) -> int:
        return struct.unpack("<H", self.take(2))[0]

    def take_opaque(self) -> bytes:
        return self.take(self.take_u16())

    def require_end(self) -> None:
        if self._offset != len(self._data):
            raise PayloadError("payload_trailing_bytes")


@dataclass(frozen=True)
class Hello:
    profile_id: bytes
    source_version: bytes
    numeric_contract_version: bytes
    capabilities: int


def encode_hello(
    profile_id: bytes,
    source_version: bytes,
    numeric_contract_version: bytes,
    capabilities: int,
) -> bytes:
    return (
        pack_opaque(profile_id)
        + pack_opaque(source_version)
        + pack_opaque(numeric_contract_version)
        + struct.pack("<H", capabilities)
    )


def decode_hello(payload: bytes) -> Hello:
    reader = PayloadReader(payload)
    hello = Hello(
        reader.take_opaque(),
        reader.take_opaque(),
        reader.take_opaque(),
        reader.take_u16(),
    )
    reader.require_end()
    return hello


@dataclass(frozen=True)
class Ready:
    profile_id: bytes
    numeric_contract_version: bytes
    locks: bytes
    capabilities: int
    max_payload: int
    rx_queue_depth: int
    patch_timeout_ms: int


def encode_ready(ready: Ready) -> bytes:
    return (
        pack_opaque(ready.profile_id)
        + pack_opaque(ready.numeric_contract_version)
        + pack_opaque(ready.locks)
        + struct.pack(
            "<HHBH",
            ready.capabilities,
            ready.max_payload,
            ready.rx_queue_depth,
            ready.patch_timeout_ms,
        )
    )


def decode_ready(payload: bytes) -> Ready:
    reader = PayloadReader(payload)
    ready = Ready(
        reader.take_opaque(),
        reader.take_opaque(),
        reader.take_opaque(),
        reader.take_u16(),
        reader.take_u16(),
        reader.take_u8(),
        reader.take_u16(),
    )
    reader.require_end()
    return ready


@dataclass(frozen=True)
class PatchOpen:
    transaction_id: bytes
    sound_identity: bytes
    name_table_sha256: bytes
    declared_name_count: int


def encode_patch_open(
    transaction_id: bytes,
    sound_identity: bytes,
    name_table_sha256: bytes,
    declared_name_count: int,
) -> bytes:
    if not 1 <= len(transaction_id) <= 16:
        raise ValueError("transaction_id must be 1..16 bytes")
    return (
        pack_opaque(transaction_id)
        + pack_opaque(sound_identity)
        + pack_opaque(name_table_sha256)
        + struct.pack("<H", declared_name_count)
    )


def decode_patch_open(payload: bytes) -> PatchOpen:
    reader = PayloadReader(payload)
    opened = PatchOpen(
        reader.take_opaque(),
        reader.take_opaque(),
        reader.take_opaque(),
        reader.take_u16(),
    )
    reader.require_end()
    if not 1 <= len(opened.transaction_id) <= 16:
        raise PayloadError("transaction_id_length")
    return opened


def encode_patch_name(name: str) -> bytes:
    return pack_opaque(name.encode("utf-8"))


def decode_patch_name(payload: bytes) -> str:
    reader = PayloadReader(payload)
    name = reader.take_opaque()
    reader.require_end()
    try:
        return name.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PayloadError("name_not_utf8") from exc


def encode_patch_value(name: str, value: bytes) -> bytes:
    return pack_opaque(name.encode("utf-8")) + pack_opaque(value)


def decode_patch_value(payload: bytes) -> tuple[str, bytes]:
    reader = PayloadReader(payload)
    name = reader.take_opaque()
    value = reader.take_opaque()
    reader.require_end()
    try:
        return name.decode("utf-8"), value
    except UnicodeDecodeError as exc:
        raise PayloadError("name_not_utf8") from exc


def encode_patch_commit(patch_hash: bytes) -> bytes:
    return pack_opaque(patch_hash)


def decode_patch_commit(payload: bytes) -> bytes:
    reader = PayloadReader(payload)
    digest = reader.take_opaque()
    reader.require_end()
    return digest


def patch_hash(entries: Mapping[str, bytes]) -> bytes:
    """Stand-in digest over staged entries; final binding waits on #53.

    Concatenation in sorted canonical-name order: UTF-8 name, one 0x00
    separator byte, value bytes, then the u16 little-endian value length.
    """
    accumulator = bytearray()
    for name in sorted(entries):
        encoded = name.encode("utf-8")
        value = entries[name]
        accumulator += encoded + b"\x00" + value + struct.pack("<H", len(value))
    return hashlib.sha256(bytes(accumulator)).digest()
