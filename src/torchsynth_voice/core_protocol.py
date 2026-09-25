"""Core/host protocol framing: envelope, numeric binding, negotiation structures.

Implements spec/protocol/FRAMING.md (protocol version 2). The regions that
protocol version 1 carried as opaque placeholders are bound here to the
accepted DR-0008 choice register (Accepted by reviewed merge, 2026-09-21;
``spec/reference/fixedpoint-choices-v1.json``): the numeric-contract version,
the patch hash, the uniform parameter value word, and the audio sample word.
The register is consumed only through the refusal gate in
:mod:`torchsynth_voice.fixedpoint.choices`; a non-accepted register is never
bound.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from enum import IntEnum
from fractions import Fraction
from typing import Mapping

from .fixedpoint.choices import CHOICES_PATH, load_choices, require_accepted
from .fixedpoint.rounding import div_round

PROTOCOL_VERSION = 2

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
# Render trigger + host-fed noise stream (spec/protocol/RENDER-TRIGGER.md,
# issue #188): the DR-0010 pass/digest binding on the transport lane.
CMD_RENDER_TRIGGER = 0x15
CMD_NOISE_STREAM = 0x16
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
    CMD_RENDER_TRIGGER: "RENDER_TRIGGER",
    CMD_NOISE_STREAM: "NOISE_STREAM",
    CMD_RESET: "RESET",
    CMD_ERROR: "ERROR",
}

CAP_NAME_KEYED_PATCH_LOAD = 1 << 0
CAP_RESET = 1 << 1
CAP_RENDER = 1 << 2
KNOWN_CAPABILITIES = CAP_NAME_KEYED_PATCH_LOAD | CAP_RESET | CAP_RENDER

# Render pass indices carried by RENDER_TRIGGER / NOISE_STREAM.
RENDER_PASS_1 = 1
RENDER_PASS_2 = 2
RENDER_PASSES = (RENDER_PASS_1, RENDER_PASS_2)

# Declared noise-stream digest: SHA-256 (32 bytes) over the clip's complete
# host-fed byte stream — the digest noise_stream_golden.noise_bytes_sha256
# produces and check_fed_bytes verifies. The core binds the DECLARED digest;
# it does not hash the bytes it receives (open question, issue #207).
NOISE_STREAM_DIGEST_BYTES = 32

# Per-pass noise-stream length of the product profile: one binary32 sample
# per audio sample, SCHED_SAMPLES_PER_PASS x 4 = 176,400 x 4 (DR-0003
# acceptance item 3, DR-0010 schedule). Tests pin it to both sources.
NOISE_STREAM_CLIP_BYTES = 705_600

# Pre-binding protocol version 1 advertised this reserved ASCII marker in the
# numeric_contract_version field. It is carried only so stale peers can be
# recognized and refused; a v2 core never accepts it.
NUMERIC_CONTRACT_STALE = b"unbound:#53"

# Audio sample word: DR-0008 C1 (accepted) — 24-bit two's-complement Q2.21,
# range [-4, +4), carried little-endian in 3 bytes.
AUDIO_SAMPLE_BYTES = 3
AUDIO_INT_BITS = 2
AUDIO_FRAC_BITS = 21
_AUDIO_WORD_MIN = -(1 << (8 * AUDIO_SAMPLE_BYTES - 1))
_AUDIO_WORD_MAX = (1 << (8 * AUDIO_SAMPLE_BYTES - 1)) - 1

# Uniform parameter value word: DR-0008 C4 (accepted) — 32-bit
# two's-complement Q10.21, carried little-endian in 4 bytes. This is the
# host-entry word for every parameter value on the wire; per-parameter
# physical interpretation stays owned by the model lanes, never by the core.
PARAM_VALUE_BYTES = 4
PARAM_INT_BITS = 10
PARAM_FRAC_BITS = 21
_PARAM_WORD_MIN = -(1 << (8 * PARAM_VALUE_BYTES - 1))
_PARAM_WORD_MAX = (1 << (8 * PARAM_VALUE_BYTES - 1)) - 1

# Patch hash: SHA-256 (32 bytes), domain-separated with the numeric contract
# version so a digest is valid only under the negotiated numeric contract.
PATCH_HASH_BYTES = 32
PATCH_HASH_DOMAIN = b"gf180-torchsynth/patch-hash-v2\x00"


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
    RENDER_BINDING = 0x0E
    NOISE_STREAM = 0x0F


@dataclass(frozen=True)
class PlaceholderWidth:
    name: str
    encoded_as: str
    deferred_to: str


@dataclass(frozen=True)
class BoundField:
    name: str
    encoding: str
    authority: str


# Regions still carried as opaque byte strings. Their deferring decisions did
# not gate on #53 and have not landed; binding them is out of scope.
PLACEHOLDER_WIDTHS = (
    PlaceholderWidth("sound_identity", "length-prefixed opaque bytes", "#12/#88"),
    PlaceholderWidth("profile_id", "length-prefixed opaque bytes", "profile naming"),
    PlaceholderWidth("locks", "length-prefixed opaque bytes", "not defined by this subset"),
)

# Regions bound by this protocol version to accepted DR-0008 register values.
BOUND_FIELDS = (
    BoundField(
        "numeric_contract_version",
        f"{PATCH_HASH_BYTES}-byte SHA-256 of {CHOICES_PATH.name}",
        "DR-0008 Sections 12/13 (Accepted 2026-09-21)",
    ),
    BoundField(
        "patch_value",
        f"{PARAM_VALUE_BYTES}-byte two's-complement Q10.21 little-endian word",
        "DR-0008 C4, C6, C7 (accepted 2026-09-21)",
    ),
    BoundField(
        "patch_hash",
        f"{PATCH_HASH_BYTES}-byte SHA-256, domain-separated with the numeric contract version",
        "DR-0008 Sections 12/13 (Accepted 2026-09-21)",
    ),
    BoundField(
        "audio_sample",
        f"{AUDIO_SAMPLE_BYTES}-byte two's-complement Q2.21 little-endian word",
        "DR-0008 C1, C6, C7 (accepted 2026-09-21)",
    ),
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

    def take_u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def take_opaque(self) -> bytes:
        return self.take(self.take_u16())

    def require_end(self) -> None:
        if self._offset != len(self._data):
            raise PayloadError("payload_trailing_bytes")


def _round_half_even(value: Fraction) -> int:
    return div_round(value.numerator, value.denominator)


def numeric_contract_version_bound() -> bytes:
    """Return the bound numeric-contract version for protocol version 2.

    The value is the SHA-256 of the machine-readable DR-0008 choice register.
    The register is read through the structural loader and every choice this
    binding depends on is consumed through the acceptance gate, so a register
    that is not Accepted is refused instead of bound.
    """
    payload = load_choices()
    for choice_id in ("C1", "C4", "C6", "C7"):
        require_accepted(choice_id, payload)
    return hashlib.sha256(CHOICES_PATH.read_bytes()).digest()


def encode_audio_word(value) -> int:
    """Host real value to the C1 Q2.21 integer word (half-even, saturating)."""
    scaled = Fraction(value) * (1 << AUDIO_FRAC_BITS)
    word = _round_half_even(scaled)
    if word > _AUDIO_WORD_MAX:
        return _AUDIO_WORD_MAX
    if word < _AUDIO_WORD_MIN:
        return _AUDIO_WORD_MIN
    return word


def decode_audio_word(data: bytes) -> Fraction:
    """C1 wire bytes to the exact rational sample value."""
    if len(data) != AUDIO_SAMPLE_BYTES:
        raise PayloadError("audio_sample_width")
    word = int.from_bytes(data, "little", signed=True)
    return Fraction(word, 1 << AUDIO_FRAC_BITS)


def encode_audio_sample(value) -> bytes:
    """C1 audio sample: 3-byte little-endian Q2.21 (half-even, saturating)."""
    return encode_audio_word(value).to_bytes(AUDIO_SAMPLE_BYTES, "little", signed=True)


def decode_audio_sample(data: bytes) -> Fraction:
    return decode_audio_word(data)


def encode_audio_payload(samples) -> bytes:
    """Pack samples contiguously, little-endian, no padding, sample[0] first."""
    return b"".join(encode_audio_sample(sample) for sample in samples)


def decode_audio_payload(data: bytes) -> list:
    if len(data) % AUDIO_SAMPLE_BYTES:
        raise PayloadError("audio_payload_length")
    return [
        decode_audio_word(data[i : i + AUDIO_SAMPLE_BYTES])
        for i in range(0, len(data), AUDIO_SAMPLE_BYTES)
    ]


def encode_param_word(value) -> bytes:
    """Host real value to the C4 Q10.21 wire word (half-even, saturating)."""
    scaled = Fraction(value) * (1 << PARAM_FRAC_BITS)
    word = _round_half_even(scaled)
    if word > _PARAM_WORD_MAX:
        word = _PARAM_WORD_MAX
    elif word < _PARAM_WORD_MIN:
        word = _PARAM_WORD_MIN
    return word.to_bytes(PARAM_VALUE_BYTES, "little", signed=True)


def decode_param_word(data: bytes) -> Fraction:
    """C4 wire bytes to the exact rational parameter value."""
    if len(data) != PARAM_VALUE_BYTES:
        raise PayloadError("param_value_width")
    word = int.from_bytes(data, "little", signed=True)
    return Fraction(word, 1 << PARAM_FRAC_BITS)


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
    if len(value) != PARAM_VALUE_BYTES:
        raise PayloadError("param_value_width")
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


def encode_patch_commit(patch_hash_digest: bytes) -> bytes:
    if len(patch_hash_digest) != PATCH_HASH_BYTES:
        raise PayloadError("patch_hash_width")
    return pack_opaque(patch_hash_digest)


def decode_patch_commit(payload: bytes) -> bytes:
    reader = PayloadReader(payload)
    digest = reader.take_opaque()
    reader.require_end()
    if len(digest) != PATCH_HASH_BYTES:
        raise PayloadError("patch_hash_width")
    return digest


@dataclass(frozen=True)
class RenderTrigger:
    pass_index: int
    sound_identity: bytes
    noise_stream_sha256: bytes


def encode_render_trigger(
    pass_index: int, sound_identity: bytes, noise_stream_sha256: bytes
) -> bytes:
    """RENDER_TRIGGER (0x15) payload (spec/protocol/RENDER-TRIGGER.md)."""
    if pass_index not in RENDER_PASSES:
        raise ValueError("pass_index must be 1 or 2")
    if len(noise_stream_sha256) != NOISE_STREAM_DIGEST_BYTES:
        raise ValueError("noise_stream_sha256 must be 32 bytes")
    return (
        struct.pack("<B", pass_index)
        + pack_opaque(sound_identity)
        + pack_opaque(noise_stream_sha256)
    )


def decode_render_trigger(payload: bytes) -> RenderTrigger:
    reader = PayloadReader(payload)
    trigger = RenderTrigger(
        reader.take_u8(), reader.take_opaque(), reader.take_opaque()
    )
    reader.require_end()
    if trigger.pass_index not in RENDER_PASSES:
        raise PayloadError("render_pass_index")
    if len(trigger.noise_stream_sha256) != NOISE_STREAM_DIGEST_BYTES:
        raise PayloadError("noise_stream_digest_width")
    return trigger


@dataclass(frozen=True)
class NoiseChunk:
    pass_index: int
    offset: int
    data: bytes


def encode_noise_stream(pass_index: int, offset: int, data: bytes) -> bytes:
    """NOISE_STREAM (0x16) payload (spec/protocol/RENDER-TRIGGER.md)."""
    if pass_index not in RENDER_PASSES:
        raise ValueError("pass_index must be 1 or 2")
    if not 0 <= offset <= 0xFFFFFFFF:
        raise ValueError("offset must fit u32")
    if not data:
        raise ValueError("a noise chunk carries at least one byte")
    return struct.pack("<BI", pass_index, offset) + pack_opaque(data)


def decode_noise_stream(payload: bytes) -> NoiseChunk:
    reader = PayloadReader(payload)
    chunk = NoiseChunk(reader.take_u8(), reader.take_u32(), reader.take_opaque())
    reader.require_end()
    if chunk.pass_index not in RENDER_PASSES:
        raise PayloadError("render_pass_index")
    if not chunk.data:
        raise PayloadError("noise_chunk_empty")
    return chunk


def patch_hash(entries: Mapping[str, bytes], *, numeric_contract_version: bytes) -> bytes:
    """Bound digest over staged entries under the negotiated numeric contract.

    Domain-separated SHA-256: the ASCII domain tag, then the 32-byte
    numeric-contract version, then the staged concatenation in sorted
    canonical-name order (UTF-8 name, one 0x00 separator byte, value bytes,
    u16 little-endian value length).
    """
    if len(numeric_contract_version) != PATCH_HASH_BYTES:
        raise ValueError("numeric_contract_version must be 32 bytes")
    for value in entries.values():
        if len(value) != PARAM_VALUE_BYTES:
            raise ValueError("staged values must be bound parameter words")
    accumulator = bytearray(PATCH_HASH_DOMAIN + numeric_contract_version)
    for name in sorted(entries):
        encoded = name.encode("utf-8")
        value = entries[name]
        accumulator += encoded + b"\x00" + value + struct.pack("<H", len(value))
    return hashlib.sha256(bytes(accumulator)).digest()
