"""Host mirror of the #75 noise-stream convert lane (host-fed exact stream).

The accepted noise policy is DR-0003 / DR-0008 C8 (both Accepted,
2026-09-21): the canonical noise stream is host- or testbench-fed
**bit-exactly**; the canonical slot is ``sound_index % 32`` with seed 13;
there is **no error metric** for noise — exactness. An on-chip generator
reproducing the CPU ``torch.rand`` bitstream would be a new noise policy
requiring its own decision record (DR-0008 Section 7 / Section 13), so the
#75 RTL (``tb/sv/noise_stream_dut.sv``) contains no generator: it converts
the fed binary32 bytes to Q2.21 exactly as the frozen fixed model's noise
lane does (``src/torchsynth_voice/fixed_voice.py``, site ``noise.source_q``):
``half_even(x * 2^21)`` with saturation to the C1 word, via exponent shift
+ half-even round — no multiplier (DR-0010 #75 owner row).

This module is the integer mirror the tb harness and tests compare the RTL
against, plus the host-feed resolve path (the canonical bytes from
:mod:`torchsynth_voice.float_sources`) and the golden-receipt trace-digest
binding. It makes no synthesis, layout, signoff, hardware-playback, or
sound-fidelity claim.
"""

from __future__ import annotations

import hashlib
import json
import struct
from typing import List, Tuple

from .float_sources import AUDIO_SAMPLES, NOISE_SEED, NOISE_STREAMS, NoiseSource

#: C1 audio word (DR-0008 Section 2): 24-bit Q2.21, range [-4, +4).
C1_WIDTH = 24
C1_FRAC_BITS = 21
C1_MIN = -(1 << (C1_WIDTH - 1))
C1_MAX = (1 << (C1_WIDTH - 1)) - 1

#: Declared clip byte length: one binary32 sample per audio sample (C8/C9
#: re-feed obligation, 2 x 705,600 B per clip, DR-0003 acceptance item 3).
EXPECTED_NOISE_BYTES = AUDIO_SAMPLES * 4

#: Error codes exported by the RTL DUT's sticky error register.
ERROR_NONE = 0
ERROR_SLOT_IDENTITY = 1
ERROR_BYTE_OVERRUN = 2
ERROR_STREAM_TRUNCATED = 3

_PACK_F32 = struct.Struct("<f")
_PACK_U32 = struct.Struct("<I")


def canonical_slot(sound_index: int) -> int:
    """The C8 slot rule: ``sound_index % 32``."""
    if type(sound_index) is not int or sound_index < 0:
        raise ValueError("sound_index must be a non-negative int")
    return sound_index % NOISE_STREAMS


def resolve_canonical_bytes(sound_index: int, seed: int = NOISE_SEED) -> bytes:
    """The host-feed path: the resolved bytes for one global sound index."""

    if seed != NOISE_SEED:
        raise ValueError("the canonical noise seed is 13")
    return NoiseSource.resolve(sound_index)


def noise_bytes_sha256(noise_bytes: bytes) -> str:
    return hashlib.sha256(noise_bytes).hexdigest()


def check_fed_bytes(noise_bytes: bytes, sound_index: int, declared_slot: int) -> None:
    """The pre-render framing/identity validation the host owes (C8).

    Length must be exactly one clip of binary32 samples, the declared slot
    must equal ``sound_index % 32``, and the stream must carry the canonical
    seed's identity (its bytes resolve from seed 13).
    """

    if not isinstance(noise_bytes, (bytes, bytearray)):
        raise ValueError("fed noise must be bytes")
    if len(noise_bytes) != EXPECTED_NOISE_BYTES:
        raise ValueError(
            "fed noise must contain exactly %d binary32 bytes; got %d"
            % (EXPECTED_NOISE_BYTES, len(noise_bytes))
        )
    if declared_slot != canonical_slot(sound_index):
        raise ValueError(
            "slot identity violation: declared slot %d != sound_index %d %% 32"
            % (declared_slot, sound_index)
        )


def narrow_f32_bits(bits: int) -> int:
    """One binary32 word -> the C1 Q2.21 word (bit-exact convert mirror).

    Bit-level twin of the RTL path: decode sign/exponent/mantissa, then
    ``half_even(m * 2^E)`` by integer shift with ties-to-even, then C7
    saturation. No float participates, matching the DUT (and the DR-0010
    #75 owner row: exponent shift + half-even round, no multiplier).

    Non-finite patterns (exponent 255) are unreachable from the canonical
    stream (``uniform_(-1, 1)``); the mirror refuses them, mirroring the
    fixed model's own ``round()`` overflow, while the DUT's declared policy
    for that unobservable branch is saturation.
    """

    if type(bits) is not int or not 0 <= bits <= 0xFFFFFFFF:
        raise ValueError("bits must be one u32 word")
    sign = (bits >> 31) & 1
    exponent = (bits >> 23) & 0xFF
    fraction = bits & 0x7FFFFF
    if exponent == 255:
        raise ValueError("non-finite binary32 is unreachable from the canonical stream")
    if exponent == 0:
        # Subnormal: value = fraction * 2^-149; scaled: fraction * 2^-128.
        magnitude = fraction
        exp2 = -128
    else:
        # Normal: value = (0x800000 | fraction) * 2^(e - 150); scaled by 2^21.
        magnitude = fraction | 0x800000
        exp2 = exponent - 129
    if exp2 >= 0:
        value = magnitude << exp2
    else:
        shift = -exp2
        value = magnitude >> shift
        remainder = magnitude & ((1 << shift) - 1)
        half = 1 << (shift - 1)
        if remainder > half or (remainder == half and (value & 1)):
            value += 1
    if sign:
        value = -value
    if value > C1_MAX:
        return C1_MAX
    if value < C1_MIN:
        return C1_MIN
    return value


def truncate_f32_bits(bits: int) -> int:
    """The LSB-truncation mutant of :func:`narrow_f32_bits` (negative control).

    Drops the fractional remainder instead of half-even rounding. A mutation
    set that does not fail the golden comparisons is a metric defect, so the
    tests require this to diverge on real canonical stream bits.
    """

    if type(bits) is not int or not 0 <= bits <= 0xFFFFFFFF:
        raise ValueError("bits must be one u32 word")
    sign = (bits >> 31) & 1
    exponent = (bits >> 23) & 0xFF
    fraction = bits & 0x7FFFFF
    if exponent == 255:
        raise ValueError("non-finite binary32 is unreachable from the canonical stream")
    if exponent == 0:
        magnitude = fraction
        exp2 = -128
    else:
        magnitude = fraction | 0x800000
        exp2 = exponent - 129
    if exp2 >= 0:
        value = magnitude << exp2
    else:
        value = magnitude >> (-exp2)
    if sign:
        value = -value
    if value > C1_MAX:
        return C1_MAX
    if value < C1_MIN:
        return C1_MIN
    return value


def mirror_stream(noise_bytes: bytes) -> List[int]:
    """The full fed byte stream -> the ``noise.raw`` Q2.21 word stream.

    Validates length and framing exactly as the host owes before a render,
    then converts every little-endian binary32 sample.
    """

    if not isinstance(noise_bytes, (bytes, bytearray)):
        raise ValueError("fed noise must be bytes")
    if len(noise_bytes) != EXPECTED_NOISE_BYTES:
        raise ValueError(
            "fed noise must contain exactly %d binary32 bytes; got %d"
            % (EXPECTED_NOISE_BYTES, len(noise_bytes))
        )
    out: List[int] = []
    for offset in range(0, EXPECTED_NOISE_BYTES, 4):
        bits = int.from_bytes(noise_bytes[offset:offset + 4], "little")
        out.append(narrow_f32_bits(bits))
    return out


def truncate_stream(noise_bytes: bytes) -> List[int]:
    """The truncation mutant over a full fed stream (mutation probe)."""

    if not isinstance(noise_bytes, (bytes, bytearray)):
        raise ValueError("fed noise must be bytes")
    if len(noise_bytes) != EXPECTED_NOISE_BYTES:
        raise ValueError(
            "fed noise must contain exactly %d binary32 bytes; got %d"
            % (EXPECTED_NOISE_BYTES, len(noise_bytes))
        )
    out: List[int] = []
    for offset in range(0, EXPECTED_NOISE_BYTES, 4):
        bits = int.from_bytes(noise_bytes[offset:offset + 4], "little")
        out.append(truncate_f32_bits(bits))
    return out


def f32_bits_and_value(noise_bytes: bytes, sample_index: int) -> Tuple[int, float]:
    """Debug view: the raw u32 bits and the binary32 value of one sample."""

    offset = sample_index * 4
    chunk = noise_bytes[offset:offset + 4]
    if len(chunk) != 4:
        raise ValueError("sample index outside the fed stream")
    bits = int.from_bytes(chunk, "little")
    return bits, _PACK_F32.unpack(chunk)[0]


def trace_digest(words: List[int]) -> str:
    """The golden receipt's per-trace digest binding.

    Must stay byte-identical to ``sha256_json`` in
    ``tools/generate_fixed_voice_golden.py`` (``json.dumps`` with sorted
    keys, compact separators, ASCII); the tests bind this against every
    committed case's ``traces["noise.raw"]`` digest.
    """

    blob = json.dumps(words, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
