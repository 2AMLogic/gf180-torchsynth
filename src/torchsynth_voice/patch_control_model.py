"""Cycle-exact host mirror of the patch-control RTL core (issue #69).

`PatchControlModel` is the Python oracle for ``tb/sv/patch_control.sv``:
given the same cycle schedule the testbench drives into the RTL (one entry
per clock cycle: a byte value or ``None`` for an idle cycle), it produces

- the exact response byte stream the DUT must emit, in order, and
- the exact final observable state (session, render gate, active-patch
  words, keyboard control words, stored sound identity).

It implements the declared protocol semantics of ``spec/protocol/FRAMING.md``
(protocol version 2 envelope, numeric-contract gate), ``PATCH-LOAD.md``
(name-keyed staged patch transaction over the 78-name canonical table,
patch-hash-v2 domain-separated commit), ``SESSION.md`` (lifecycle,
ordering, backpressure, timeouts, idempotency, error taxonomy) and
``RENDER-TRIGGER.md`` (``CAP_RENDER``, ``RENDER_TRIGGER`` / ``NOISE_STREAM``,
the ``rendering`` state, the pass-1 declared-digest + sound-identity binding
compared at the pass-2 trigger, and the one-cycle ``bind_reject`` pulse that
discards the clip in the replay engine), plus the module header's declared
cycle contract, which the RTL replicates:

- one command byte accepted per cycle; the parser never stalls;
- t_hdr (7th header byte) decides admission: unsupported header version or
  unknown kind is answered there and the frame's remaining bytes are
  consumed and discarded; a frame arriving while the single frame slot is
  occupied is answered ``ERR_BUSY`` there and discarded; a declared length
  above ``max_payload`` is a bad frame whose bytes are consumed;
- t0 (high CRC byte) completes an admitted frame; the engine takes it at
  the first later cycle on which it is idle and no frame completes that
  cycle, then walks a command-specific deterministic schedule (one payload
  byte consumed per walk cycle, plus the name scan and the SHA-256
  compress/compare phases) and enqueues its response request on the walk's
  last cycle;
- the patch timer runs only while the session is ``patch_open``, reloads on
  every admitted transaction frame's t0 (and when a transaction opens), and
  expires silently after ``timeout_cycles`` consecutive non-reloaded
  cycles, discarding the staged transaction;
- response ordering equals request-enqueue order; response content is a
  pure function of the request (the READY profile snapshot is the core's
  bound constants).

Declared reconciliations (each realizes one under-specified protocol
sentence; both sides of the shared test suite hold to them):

1. The SESSION error table's per-error Recovery column governs: most errors
   leave core state unchanged (an open transaction stays open);
   ``ERR_PATCH_INCOMPLETE`` and ``ERR_PATCH_HASH_MISMATCH`` discard the
   transaction and return to ``ready``; only ``ERR_PROTOCOL_VERSION`` is
   fatal (core -> ``closed``).
2. A repeated ``PATCH_NAME`` for an already-declared name is the idempotent
   no-op rewrite of SESSION (its payload is the name itself, so a repeat is
   byte-identical by construction); only a value re-staged with different
   bytes is ``ERR_DUPLICATE_NAME``, leaving the staged slot unchanged.
3. The idempotent-class replay caches (``HELLO`` / ``RESET`` /
   ``PATCH_ABORT``) hold the most recent frame of each class keyed by
   sequence and exact frame bytes; an identical re-received frame replays
   the cached response and applies nothing. All protocol-legal frames of
   these classes fit the cache bound (bounded profile/source windows), so
   no idempotent frame is ever uncacheable.
4. A sub-patch (``declared_name_count`` < 78) that commits cleanly applies
   its words into the active bank but cannot assert the render gate:
   ``patch_active`` asserts only when a hash-matching commit applies a
   complete 78-name patch.
5. Transaction ids: re-opening a finished transaction's id answers
   ``ERR_BAD_SEQUENCE``; an expired one's id, or any transaction frame
   replaying a sequence of an expired transaction, answers
   ``ERR_TX_TIMEOUT``; replaying a sequence of a finished (non-expired)
   transaction answers ``ERR_BAD_SEQUENCE``.
6. ``RESET`` in ``closed`` is ``ERR_BAD_STATE`` (the lifecycle table allows
   only ``HELLO`` there).
7. The keyboard S1 outputs are formed at commit-apply:
   ``keyboard.midi_f0`` verbatim (the C4 Q10.21 wire word) and
   ``keyboard.duration`` as the exact widening of the uniform wire word
   into the model's Q16.30 length domain (``<<9``). The wire word is
   uniform Q10.21 for every parameter (FRAMING.md "Bound numeric
   encodings"), so the loader's duration output is the exact widening of
   that word, not the model's direct binary64 -> Q16.30 entry quantization
   (a wire-boundary artifact bounded by 2^-22; declared, not hidden).
8. Capabilities are negotiated, not asserted: the READY capability word is
   ``requested & granted_caps``, and a command whose capability bit is not
   in that word is ``ERR_UNSUPPORTED_COMMAND``, checked BEFORE any state or
   payload rule (RENDER-TRIGGER.md's capability paragraph is
   unconditional). The negotiated word survives ``RESET`` and is cleared
   only at reset and by the two fatal ``ERR_PROTOCOL_VERSION`` paths, so
   ``closed`` never holds a granted capability.
9. RENDER-TRIGGER.md's lifecycle table names ``ready`` and ``rendering``
   rows only. In ``closed`` the SESSION lifecycle admits ``HELLO`` alone, so
   both render codes are ``ERR_BAD_STATE`` there (unreachable in practice
   under refinement 8); in ``patch_open`` both are ``ERR_BAD_STATE`` (that
   document's own ordering rule). While ``rendering``: ``PATCH_OPEN`` and
   ``PATCH_ABORT`` are ``ERR_BAD_STATE`` (an abort must not silently end a
   render — ``RESET`` does that), and ``PATCH_NAME`` / ``PATCH_VALUE`` /
   ``PATCH_COMMIT`` answer the no-open-transaction outcome they already
   answer in ``ready``. A rejection that discards an OPEN render
   (``ERR_RENDER_BINDING`` or ``ERR_NOISE_STREAM``) pulses ``bind_reject``
   for one cycle on the decide cycle that answers it; a pass-1 identity
   mismatch does not (no clip existed), and neither do ``RESET`` / ``HELLO``,
   whose discard reaches the engine through its own ``rst`` / ``abort``.
"""

from __future__ import annotations

import struct
from typing import Dict, List, Optional, Sequence, Tuple

from .core_protocol import (
    CAP_NAME_KEYED_PATCH_LOAD,
    CAP_RENDER,
    CAP_RESET,
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
    KIND_COMMAND,
    KIND_ERROR,
    KIND_RESPONSE,
    NOISE_STREAM_CLIP_BYTES,
    NOISE_STREAM_DIGEST_BYTES,
    PARAM_VALUE_BYTES,
    PROTOCOL_VERSION,
    RENDER_PASS_1,
    RENDER_PASS_2,
    RENDER_PASSES,
    SYNC,
    crc16_ccitt_false,
    encode_ready,
    patch_hash,
)
from .core_protocol import Ready  # noqa: E402

CMD_ERROR = 0xFE

ERR_UNSUPPORTED_COMMAND = 0x01
ERR_PROTOCOL_VERSION = 0x02
ERR_BAD_FRAME = 0x03
ERR_BAD_SEQUENCE = 0x04
ERR_BUSY = 0x05
ERR_BAD_STATE = 0x06
ERR_PATCH_TX_ACTIVE = 0x07
ERR_PATCH_INCOMPLETE = 0x08
ERR_PATCH_HASH_MISMATCH = 0x09
ERR_UNKNOWN_NAME = 0x0A
ERR_DUPLICATE_NAME = 0x0B
ERR_TX_TIMEOUT = 0x0C
ERR_PAYLOAD_LENGTH = 0x0D
ERR_RENDER_BINDING = 0x0E
ERR_NOISE_STREAM = 0x0F

CLOSED, READY, PATCH, RENDER = 0, 1, 2, 3

TX_CMD_LO, TX_CMD_HI = CMD_PATCH_OPEN, CMD_PATCH_ABORT

REQ_QUEUE_DEPTH = 8

#: RENDER_TRIGGER's fixed payload cost: pass index (1) + identity length
#: prefix (2) + digest length prefix (2) + declared digest (32).
RENDER_TRIGGER_FIXED = 5 + NOISE_STREAM_DIGEST_BYTES
#: NOISE_STREAM's fixed payload cost: pass (1) + offset (4) + data length
#: prefix (2); at least one data byte must follow.
NOISE_STREAM_FIXED = 7
#: The identity window both PATCH_OPEN and RENDER_TRIGGER accept.
IDENTITY_WINDOW = 64


def crc16_step(crc: int, byte: int) -> int:
    crc ^= byte << 8
    for _ in range(8):
        crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


class PatchControlModel:
    """Byte/cycle-exact functional mirror of tb/sv/patch_control.sv."""

    def __init__(
        self,
        names: Sequence[str],
        *,
        table_sha256: bytes,
        contract_version: bytes,
        profile_id: bytes,
        max_payload: int = 1024,
        timeout_cycles: int = 5000,
        advertised_max_payload: int = 1024,
        advertised_rx_queue_depth: int = 1,
        advertised_patch_timeout_ms: int = 2,
        kbd_midi_slot: int = 11,
        kbd_duration_slot: int = 10,
        noise_clip_bytes: int = NOISE_STREAM_CLIP_BYTES,
    ):
        if len(table_sha256) != 32 or len(contract_version) != 32:
            raise ValueError("table_sha256/contract_version must be 32 bytes")
        self.names = list(names)
        self.slot_of: Dict[str, int] = {name: i for i, name in enumerate(names)}
        self.num_params = len(names)
        self.kbd_midi_slot = kbd_midi_slot
        self.kbd_duration_slot = kbd_duration_slot
        self.table_sha256 = table_sha256
        self.contract_version = contract_version
        self.profile_id = profile_id
        self.max_payload = max_payload
        self.timeout_cycles = timeout_cycles
        self.adv_max_payload = advertised_max_payload
        self.adv_rx_depth = advertised_rx_queue_depth
        self.adv_timeout_ms = advertised_patch_timeout_ms
        # Per-pass host-fed noise-stream length. The product profile's value
        # is NOISE_STREAM_CLIP_BYTES (705,600 B = SCHED_SAMPLES_PER_PASS x 4);
        # a smaller value is a simulation scale-down only, matched by the
        # DUT's NOISE_CLIP_BYTES parameter (RENDER-TRIGGER.md).
        if noise_clip_bytes < 1:
            raise ValueError("noise_clip_bytes must be >= 1")
        self.noise_clip_bytes = noise_clip_bytes
        self.granted_caps = CAP_NAME_KEYED_PATCH_LOAD | CAP_RESET | CAP_RENDER
        self._session_caps = 0
        self._bind_pulses = 0
        self._reset_all()

    # ------------------------------------------------------------------
    # state
    # ------------------------------------------------------------------
    def _reset_all(self) -> None:
        self.session = CLOSED
        self.active_bank: List[int] = [0] * self.num_params
        self.staged_bank: List[int] = [0] * self.num_params
        self.patch_active = False
        self.kbd_midi_word = 0
        self.kbd_duration_word = 0
        self.identity = b""
        # The open render's binding + per-pass progress, or None. This is
        # the receiver's entire per-clip live state: the 32-byte declared
        # digest, the bound sound identity, the current pass, whether pass 1
        # reached the clip length, and this pass's accepted-byte count. No
        # clip buffer is ever held (DR-0010 Memory strategy) and no digest
        # is computed over received bytes (declared-digest binding only).
        self.render: Optional[dict] = None
        # transaction state
        self._tx_clear()
        # idempotent-class replay caches: seq -> (frame bytes, request)
        self._caches: Dict[int, Dict[str, object]] = {
            CMD_HELLO: {},
            CMD_RESET: {},
            CMD_PATCH_ABORT: {},
        }
        # done cache: the most recent finished transaction
        self._done_id = b""
        self._done_valid = False
        self._done_expired = False
        self._done_seq_first = 0
        self._done_seq_last = 0

    def _tx_clear(self) -> None:
        self._tx_open = False
        self._tx_id = b""
        self._tx_want = 0
        self._tx_have = 0
        self._declared = set()
        self._staged: Dict[int, int] = {}
        self._tx_identity = b""
        self._tx_seq_first = 0
        self._tx_seq_last = 0
        self._timer = self.timeout_cycles

    # ------------------------------------------------------------------
    # cycle-domain simulation
    # ------------------------------------------------------------------
    def run(self, schedule: Sequence[Optional[int]]) -> Tuple[bytes, dict]:
        """Run one reset-to-reset simulation over a per-cycle schedule.

        Returns ``(response_bytes, observables)``.
        """
        self._reset_all()
        # Session-scoped, not reset-scoped: the negotiated capability word
        # survives RESET (refinement 8). bind_reject pulses are counted for
        # the whole reset-to-reset run, like the testbench's own counter.
        self._session_caps = 0
        self._bind_pulses = 0
        self._parser = {
            "state": "hunt",
            "prev": 0,
            "cnt": 0,
            "version": 0,
            "kind": 0,
            "cmd": 0,
            "seq": 0,
            "length": 0,
            "pay": 0,
            "crc": 0xFFFF,
            "crc_lo": 0,
            "keep": False,
            "skip": 0,
            "bad_len": False,
            "payload": bytearray(),
        }
        self._frame_full = False
        self._frame = None
        self._engine_busy = 0  # cycles remaining (including enqueue cycle)
        self._req_queue: List[dict] = []
        self._rsp: List[int] = []
        self._builder_cycles = 0

        for byte in schedule:
            self._cycle(byte)

        return bytes(self._rsp), self.observables()

    def observables(self) -> dict:
        return {
            "session": self.session,
            "patch_active": self.patch_active,
            "bank": {slot: word for slot, word in enumerate(self.active_bank)},
            "kbd_midi_word": self.kbd_midi_word,
            "kbd_duration_word": self.kbd_duration_word,
            "identity": self.identity,
            "bind_pulses": self._bind_pulses,
        }

    # ------------------------------------------------------------------
    # one clock cycle
    # ------------------------------------------------------------------
    def _cycle(self, byte: Optional[int]) -> None:
        frame_completing = self._parser_step(byte)
        self._timer_step(frame_completing)
        self._engine_step(frame_completing)
        self._response_step()

    # ---- parser -------------------------------------------------------
    def _parser_step(self, byte: Optional[int]) -> bool:
        p = self._parser
        if byte is None:
            return False
        state = p["state"]
        if state == "hunt":
            if p["prev"] == SYNC[0] and byte == SYNC[1]:
                p["state"] = "header"
                p["cnt"] = 0
                # CRC-16/CCITT-FALSE over everything after sync only
                # (FRAMING.md envelope: bytes 2 .. 9+length)
                p["crc"] = 0xFFFF
                p["bad_len"] = False
                p["keep"] = False
            p["prev"] = byte
            return False
        if state == "skip":
            p["skip"] -= 1
            if p["skip"] == 0:
                if p["bad_len"]:
                    self._enqueue_error(p["cmd"], p["seq"], ERR_BAD_FRAME)
                    p["bad_len"] = False
                p["state"] = "hunt"
            return False

        if state in ("header", "payload"):
            p["crc"] = crc16_step(p["crc"], byte)
        if state == "header":
            cnt = p["cnt"]
            if cnt == 0:
                p["version"] = byte
            elif cnt == 1:
                p["kind"] = byte
            elif cnt == 2:
                p["cmd"] = byte
            elif cnt == 3:
                p["seq"] = byte
            elif cnt == 4:
                p["seq"] |= byte << 8
            elif cnt == 5:
                p["length"] = byte
            else:
                p["length"] |= byte << 8
                # t_hdr: admission decision
                if p["version"] != PROTOCOL_VERSION:
                    self._enqueue_error(p["cmd"], p["seq"], ERR_PROTOCOL_VERSION)
                    self.session = CLOSED
                    self._tx_clear()
                    self._reset_all()
                    self.session = CLOSED
                    self._session_caps = 0  # fatal: renegotiate (refinement 8)
                    p["state"] = "skip"
                    p["skip"] = p["length"] + 2
                    return False
                if p["kind"] != KIND_COMMAND:
                    self._enqueue_error(p["cmd"], p["seq"], ERR_BAD_FRAME)
                    p["state"] = "skip"
                    p["skip"] = p["length"] + 2
                    return False
                if p["length"] > self.max_payload:
                    p["bad_len"] = True
                    p["state"] = "skip"
                    p["skip"] = p["length"] + 2
                    return False
                if self._frame_full:
                    self._enqueue_error(p["cmd"], p["seq"], ERR_BUSY)
                    p["state"] = "skip"
                    p["skip"] = p["length"] + 2
                    return False
                p["keep"] = True
                p["payload"] = bytearray()
                p["pay"] = 0
                p["state"] = "payload" if p["length"] else "crc_lo"
            p["cnt"] += 1
            return False
        if state == "payload":
            if p["keep"]:
                p["payload"].append(byte)
            p["pay"] += 1
            if p["pay"] == p["length"]:
                p["state"] = "crc_lo"
            return False
        if state == "crc_lo":
            p["crc_lo"] = byte
            p["state"] = "crc_hi"
            return False
        # crc_hi: t0
        if p["keep"]:
            received = p["crc_lo"] | (byte << 8)
            if received == p["crc"]:
                self._frame_full = True
                self._frame = (p["cmd"], p["seq"], bytes(p["payload"]))
                if (
                    self.session == PATCH
                    and TX_CMD_LO <= p["cmd"] <= TX_CMD_HI
                ):
                    self._timer = self.timeout_cycles
                p["state"] = "hunt"
                p["prev"] = byte
                return True  # frame completing this cycle
            self._enqueue_error(p["cmd"], p["seq"], ERR_BAD_FRAME)
        p["state"] = "hunt"
        p["prev"] = byte
        return False

    # ---- patch timer ---------------------------------------------------
    def _timer_step(self, frame_completing: bool) -> None:
        if self.session != PATCH:
            return
        if frame_completing:
            return  # reload already applied at the t0 event
        self._timer -= 1
        if self._timer == 0:
            # silent expiry: discard staged state, record the transaction
            self._done_id = self._tx_id
            self._done_seq_first = self._tx_seq_first
            self._done_seq_last = self._tx_seq_last
            self._done_valid = True
            self._done_expired = True
            self._tx_clear()
            self.session = READY

    # ---- engine ---------------------------------------------------------
    def _engine_step(self, frame_completing: bool) -> None:
        if self._engine_busy:
            self._engine_busy -= 1
            if self._engine_busy == 0:
                self._engine_decide()
            return
        if (
            self._frame_full
            and not frame_completing
            and len(self._req_queue) < REQ_QUEUE_DEPTH
        ):
            cmd, seq, payload = self._frame
            self._frame_full = False
            self._frame = None
            self._engine_start(cmd, seq, payload)

    # engine walk lengths (must match the RTL walk schedule exactly)
    @staticmethod
    def _walk_hello() -> int:
        # prof len, src len, contract prefix, contract, caps, decide
        return 2 + 2 + 2 + 32 + 2 + 1

    @staticmethod
    def _walk_open(id_len: int, ident_len: int) -> int:
        # id len, id, identity len, identity, table prefix, table, count
        return 2 + id_len + 2 + ident_len + 2 + 32 + 2 + 1

    @staticmethod
    def _walk_name(name_len: int) -> int:
        return 2 + name_len + 78 + 1

    @staticmethod
    def _walk_value(name_len: int, value_len: int) -> int:
        return 2 + name_len + 2 + value_len + 78 + 1

    @staticmethod
    def _commit_pad(stream_len: int) -> Tuple[int, int, int]:
        """(feed_bytes, k_pad, n_blocks) for a staged-stream length."""
        k_pad = (56 - ((stream_len + 1) % 64)) % 64
        total = stream_len + 1 + k_pad + 8
        blocks = total // 64
        return total, k_pad, blocks

    @staticmethod
    def _walk_render_trigger(ident_len: int) -> int:
        # pass index, identity len, identity (0 costs no cycle), digest
        # prefix, declared digest, decide
        return 1 + 2 + ident_len + 2 + NOISE_STREAM_DIGEST_BYTES + 1

    @staticmethod
    def _walk_noise_stream() -> int:
        # pass index, offset, data length prefix, decide. The data region
        # costs no walk cycle: the bytes are consumed, never stored.
        return NOISE_STREAM_FIXED + 1

    def _walk_commit(self, staged: Dict[int, int]) -> int:
        stream_len = 63 + sum(
            len(self.names[slot].encode("utf-8")) + 7 for slot in sorted(staged)
        )
        feed, _k_pad, blocks = self._commit_pad(stream_len)
        return feed + blocks * 64 + 32 + 1

    def _engine_start(self, cmd: int, seq: int, payload: bytes) -> None:
        self._cur = (cmd, seq, payload)
        # The RTL clears e_err when it takes a frame (E_IDLE), so no
        # take-time verdict ever leaks from the previous command.
        self._pending_unsupported = False
        self._pending_render_bad_state = False
        if cmd == CMD_RESET or cmd == CMD_PATCH_ABORT:
            self._engine_busy = 1
            return
        if cmd not in (
            CMD_HELLO,
            CMD_PATCH_OPEN,
            CMD_PATCH_NAME,
            CMD_PATCH_VALUE,
            CMD_PATCH_COMMIT,
            CMD_RENDER_TRIGGER,
            CMD_NOISE_STREAM,
        ):
            # unknown command: immediate unsupported error
            self._engine_busy = 1
            self._pending_unsupported = True
            return
        self._pending_unsupported = False
        self._pending_render_bad_state = False
        if cmd in (CMD_RENDER_TRIGGER, CMD_NOISE_STREAM):
            # refinement 8: the capability gate is outermost and walk-free
            if not self._session_caps & CAP_RENDER:
                self._engine_busy = 1
                self._pending_unsupported = True
                return
            if self.session in (CLOSED, PATCH):
                # refinement 9, decided at take so the walk's field scratch
                # is never read stale at decide
                self._engine_busy = 1
                self._pending_render_bad_state = True
                return
            if cmd == CMD_RENDER_TRIGGER:
                fields = self._parse_render_trigger(payload)
                self._engine_busy = (
                    1 if fields is None
                    else self._walk_render_trigger(len(fields[1]))
                )
            else:
                chunk = self._parse_noise_stream(payload)
                self._engine_busy = (
                    1 if chunk is None else self._walk_noise_stream()
                )
            return
        if cmd == CMD_HELLO:
            self._engine_busy = self._walk_hello()
        elif cmd == CMD_PATCH_OPEN:
            if self.session != READY:
                self._engine_busy = 1
                return
            lens = self._read_opaque_lens(payload)
            if lens is None:
                self._engine_busy = 1
                return
            id_len, ident_len, tail = lens
            self._engine_busy = self._walk_open(id_len, ident_len)
        elif cmd == CMD_PATCH_NAME:
            if self.session != PATCH:
                self._engine_busy = 1
                return
            nl = self._name_len(payload)
            self._engine_busy = self._walk_name(nl)
        elif cmd == CMD_PATCH_VALUE:
            if self.session != PATCH:
                self._engine_busy = 1
                return
            nl, vl = self._value_lens(payload)
            self._engine_busy = self._walk_value(nl, vl)
        else:  # PATCH_COMMIT
            if self.session != PATCH:
                self._engine_busy = 1
                return
            if len(payload) != 34:
                self._engine_busy = 1
                return
            if self._tx_have != self._tx_want or not self._declared:
                # incomplete: decided before the hash run
                self._engine_busy = 1
                return
            self._engine_busy = self._walk_commit(self._staged)

    @staticmethod
    def _read_opaque_lens(payload: bytes) -> Optional[Tuple[int, int, int]]:
        if len(payload) < 4:
            return None
        id_len = payload[0] | (payload[1] << 8)
        if 4 + id_len + 2 > len(payload):
            return None
        off = 2 + id_len
        ident_len = payload[off] | (payload[off + 1] << 8)
        tail = 4 + id_len + ident_len
        return id_len, ident_len, tail

    @staticmethod
    def _name_len(payload: bytes) -> int:
        if len(payload) < 2:
            return 0
        return payload[0] | (payload[1] << 8)

    def _value_lens(self, payload: bytes) -> Tuple[int, int]:
        nl = self._name_len(payload)
        off = 2 + nl
        if off + 2 > len(payload):
            return nl, 0
        vl = payload[off] | (payload[off + 1] << 8)
        return nl, vl

    # ---- decide (applies effects, enqueues the response request) --------
    def _engine_decide(self) -> None:
        cmd, seq, payload = self._cur

        if getattr(self, "_pending_unsupported", False):
            self._enqueue_error(cmd, seq, ERR_UNSUPPORTED_COMMAND)
            return

        # idempotent-class replay check (before any effect). PATCH_ABORT is
        # excluded while rendering: refinement 9 answers ERR_BAD_STATE there
        # before the replay cache is consulted, exactly as the RTL's decide
        # checks the session state ahead of its cache hit.
        if cmd in (CMD_HELLO, CMD_RESET, CMD_PATCH_ABORT) and not (
            cmd == CMD_PATCH_ABORT and self.session == RENDER
        ):
            cached = self._caches[cmd].get(seq)
            if cached is not None and cached["frame"] == self._frame_bytes(cmd, seq, payload):
                self._enqueue(cached["request"])
                return

        if cmd == CMD_RESET:
            if self.session == CLOSED:
                self._enqueue_error(cmd, seq, ERR_BAD_STATE)
                return
            self._reset_all()
            self.session = READY
            self._cache_idempotent(cmd, seq, payload, self._empty_rsp(cmd, seq))
            self._enqueue(self._empty_rsp(cmd, seq))
            return

        if cmd == CMD_PATCH_ABORT:
            if self.session == CLOSED:
                self._enqueue_error(cmd, seq, ERR_BAD_STATE)
                return
            if self.session == RENDER:
                # refinement 9: an abort must not silently end a render
                self._enqueue_error(cmd, seq, ERR_BAD_STATE)
                return
            if self.session != PATCH:
                # ready or rendering: no transaction is open (refinement 9)
                self._tx_seq_span_record(seq)
                self._enqueue_error(cmd, seq, self._expired_or_bad_sequence())
                return
            self._finish_transaction(seq, expired=False)
            self.session = READY
            self._cache_idempotent(cmd, seq, payload, self._empty_rsp(cmd, seq))
            self._enqueue(self._empty_rsp(cmd, seq))
            return

        if cmd == CMD_HELLO:
            fields = self._parse_hello(payload)
            if fields is None:
                self._enqueue_error(cmd, seq, ERR_PAYLOAD_LENGTH)
                return
            profile, source, contract, caps = fields
            if contract != self.contract_version:
                self._reset_all()
                self.session = CLOSED
                self._session_caps = 0  # fatal: renegotiate (refinement 8)
                self._enqueue_error(cmd, seq, ERR_PROTOCOL_VERSION)
                return
            self._reset_all()
            self.session = READY
            self._session_caps = caps & self.granted_caps  # negotiated
            request = self._ready_rsp(cmd, seq, caps)
            self._cache_idempotent(cmd, seq, payload, request)
            self._enqueue(request)
            return

        if cmd == CMD_PATCH_OPEN:
            if self.session == PATCH:
                self._enqueue_error(cmd, seq, ERR_PATCH_TX_ACTIVE)
                return
            if self.session == CLOSED:
                self._enqueue_error(cmd, seq, ERR_BAD_STATE)
                return
            if self.session == RENDER:
                # refinement 9 / RENDER-TRIGGER.md ordering rule: render and
                # patch-transaction frames never interleave.
                self._enqueue_error(cmd, seq, ERR_BAD_STATE)
                return
            fields = self._parse_open(payload)
            if fields is None:
                self._enqueue_error(cmd, seq, ERR_PAYLOAD_LENGTH)
                return
            tx_id, identity, table_ref, want = fields
            if table_ref != self.table_sha256:
                self._enqueue_error(cmd, seq, ERR_UNKNOWN_NAME)
                return
            if want < 1 or want > self.num_params:
                self._enqueue_error(cmd, seq, ERR_PAYLOAD_LENGTH)
                return
            if self._done_valid and tx_id == self._done_id:
                code = ERR_TX_TIMEOUT if self._done_expired else ERR_BAD_SEQUENCE
                self._enqueue_error(cmd, seq, code)
                return
            self._tx_open = True
            self._tx_id = tx_id
            self._tx_want = want
            self._tx_have = 0
            self._declared = set()
            self._staged = {}
            self._tx_identity = identity
            self._tx_seq_first = seq
            self._tx_seq_last = seq
            self._timer = self.timeout_cycles
            self.session = PATCH
            self._enqueue(self._empty_rsp(cmd, seq))
            return

        if cmd == CMD_PATCH_NAME:
            if self.session == CLOSED:
                self._enqueue_error(cmd, seq, ERR_BAD_STATE)
                return
            if self.session != PATCH:
                # ready or rendering: no transaction is open (refinement 9)
                self._tx_seq_span_record(seq)
                self._enqueue_error(cmd, seq, self._expired_or_bad_sequence())
                return
            shape_ok, name = self._parse_name(payload)
            self._tx_seq_span_record(seq)
            if not shape_ok:
                self._enqueue_error(cmd, seq, ERR_PAYLOAD_LENGTH)
                return
            if name is None or name not in self.slot_of:
                self._enqueue_error(cmd, seq, ERR_UNKNOWN_NAME)
                return
            slot = self.slot_of[name]
            if slot in self._declared:
                # idempotent no-op re-declaration (refinement 2)
                self._enqueue(self._empty_rsp(cmd, seq))
                return
            self._declared.add(slot)
            self._tx_have += 1
            self._enqueue(self._empty_rsp(cmd, seq))
            return

        if cmd == CMD_PATCH_VALUE:
            if self.session == CLOSED:
                self._enqueue_error(cmd, seq, ERR_BAD_STATE)
                return
            if self.session != PATCH:
                # ready or rendering: no transaction is open (refinement 9)
                self._tx_seq_span_record(seq)
                self._enqueue_error(cmd, seq, self._expired_or_bad_sequence())
                return
            shape_ok, name, word_bytes = self._parse_value(payload)
            self._tx_seq_span_record(seq)
            if not shape_ok:
                self._enqueue_error(cmd, seq, ERR_PAYLOAD_LENGTH)
                return
            if name is None:
                self._enqueue_error(cmd, seq, ERR_UNKNOWN_NAME)
                return
            if name not in self.slot_of:
                self._enqueue_error(cmd, seq, ERR_UNKNOWN_NAME)
                return
            slot = self.slot_of[name]
            if slot not in self._declared:
                self._enqueue_error(cmd, seq, ERR_UNKNOWN_NAME)
                return
            word = int.from_bytes(word_bytes, "little", signed=True)
            if slot in self._staged and self._staged[slot] != word:
                self._enqueue_error(cmd, seq, ERR_DUPLICATE_NAME)
                return
            self._staged[slot] = word
            self._enqueue(self._empty_rsp(cmd, seq))
            return

        if cmd == CMD_RENDER_TRIGGER:
            self._decide_render_trigger(cmd, seq, payload)
            return

        if cmd == CMD_NOISE_STREAM:
            self._decide_noise_stream(cmd, seq, payload)
            return

        # PATCH_COMMIT
        if self.session == CLOSED:
            self._enqueue_error(cmd, seq, ERR_BAD_STATE)
            return
        if self.session != PATCH:
            # ready or rendering: no transaction is open (refinement 9)
            self._tx_seq_span_record(seq)
            self._enqueue_error(cmd, seq, self._expired_or_bad_sequence())
            return
        if len(payload) != 34 or payload[0] | (payload[1] << 8) != 32:
            self._enqueue_error(cmd, seq, ERR_PAYLOAD_LENGTH)
            return
        payload = payload[2:]
        self._tx_seq_span_record(seq)
        if self._tx_have != self._tx_want or not self._declared:
            self._discard_transaction(seq)
            self.session = READY
            self._enqueue_error(cmd, seq, ERR_PATCH_INCOMPLETE)
            return
        entries = {
            self.names[slot]: self._staged[slot].to_bytes(
                PARAM_VALUE_BYTES, "little", signed=True
            )
            for slot in self._staged
        }
        digest = patch_hash(entries, numeric_contract_version=self.contract_version)
        if digest != payload:
            self._discard_transaction(seq)
            self.session = READY
            self._enqueue_error(cmd, seq, ERR_PATCH_HASH_MISMATCH)
            return
        # apply atomically
        for slot, word in self._staged.items():
            self.active_bank[slot] = word
        if self._tx_want == self.num_params:
            self.patch_active = True
        if self.kbd_midi_slot in self._staged:
            self.kbd_midi_word = self._staged[self.kbd_midi_slot] & 0xFFFFFFFF
        if self.kbd_duration_slot in self._staged:
            self.kbd_duration_word = (
                self._staged[self.kbd_duration_slot] << 9
            ) & ((1 << 47) - 1)
        self.identity = self._tx_identity
        self._finish_transaction(seq, expired=False)
        self.session = READY
        self._enqueue(self._empty_rsp(cmd, seq))
        return

    # ---- render lane (RENDER-TRIGGER.md) ---------------------------------
    def _discard_render(self) -> None:
        """Drop the render binding whole (DR-0010: no resumable render)."""
        self.render = None

    def _reject_render(self, cmd: int, seq: int, code: int) -> None:
        """A rejection for an OPEN render: discard entire, pulse, answer.

        This is the single site that drives the replay engine's
        ``bind_reject`` (RENDER-TRIGGER.md "Where the binding reaches the
        RTL"): one cycle, on the decide cycle that answers ``code``.
        """
        self._discard_render()
        self.session = READY
        self._bind_pulses += 1
        self._enqueue_error(cmd, seq, code)

    def _decide_render_trigger(self, cmd: int, seq: int, payload: bytes) -> None:
        if self._pending_render_bad_state:
            self._enqueue_error(cmd, seq, ERR_BAD_STATE)
            return
        fields = self._parse_render_trigger(payload)
        if fields is None:
            self._enqueue_error(cmd, seq, ERR_PAYLOAD_LENGTH)
            return
        pass_index, identity, digest_prefix, digest = fields
        if pass_index not in RENDER_PASSES or digest_prefix != NOISE_STREAM_DIGEST_BYTES:
            self._enqueue_error(cmd, seq, ERR_PAYLOAD_LENGTH)
            return

        if pass_index == RENDER_PASS_1:
            if self.session == RENDER:
                # a second trigger never joins or replaces a clip
                self._enqueue_error(cmd, seq, ERR_BAD_STATE)
                return
            if not self.patch_active:
                self._enqueue_error(cmd, seq, ERR_BAD_STATE)
                return
            if identity != self.identity:
                # no clip existed, so nothing is discarded and no pulse
                self._enqueue_error(cmd, seq, ERR_RENDER_BINDING)
                return
            self.render = {
                "sound_identity": identity,
                "noise_stream_sha256": digest,
                "pass_index": RENDER_PASS_1,
                "pass1_complete": False,
                "accepted": 0,
            }
            self.session = RENDER
            self._enqueue(self._empty_rsp(cmd, seq))
            return

        # pass 2
        if self.session != RENDER:
            self._enqueue_error(cmd, seq, ERR_BAD_SEQUENCE)
            return
        render = self.render
        if render["pass_index"] != RENDER_PASS_1 or not render["pass1_complete"]:
            # pass 1 short of the clip length, or pass 2 already open
            self._reject_render(cmd, seq, ERR_NOISE_STREAM)
            return
        if (
            identity != render["sound_identity"]
            or digest != render["noise_stream_sha256"]
        ):
            # the DR-0010 binding check, before any pass-2 noise byte exists
            self._reject_render(cmd, seq, ERR_RENDER_BINDING)
            return
        render["pass_index"] = RENDER_PASS_2
        render["accepted"] = 0
        self._enqueue(self._empty_rsp(cmd, seq))

    def _decide_noise_stream(self, cmd: int, seq: int, payload: bytes) -> None:
        if self._pending_render_bad_state:
            self._enqueue_error(cmd, seq, ERR_BAD_STATE)
            return
        chunk = self._parse_noise_stream(payload)
        if chunk is None:
            self._enqueue_error(cmd, seq, ERR_PAYLOAD_LENGTH)
            return
        pass_index, offset, data_len = chunk
        if pass_index not in RENDER_PASSES:
            self._enqueue_error(cmd, seq, ERR_PAYLOAD_LENGTH)
            return
        if self.session != RENDER:
            self._enqueue_error(cmd, seq, ERR_BAD_SEQUENCE)
            return
        render = self.render
        if (
            pass_index != render["pass_index"]
            or offset != render["accepted"]
            or (render["pass_index"] == RENDER_PASS_1 and render["pass1_complete"])
            or render["accepted"] + data_len > self.noise_clip_bytes
        ):
            self._reject_render(cmd, seq, ERR_NOISE_STREAM)
            return
        # consumed as they arrive; only the accepted count is kept
        render["accepted"] += data_len
        if render["accepted"] == self.noise_clip_bytes:
            if render["pass_index"] == RENDER_PASS_1:
                render["pass1_complete"] = True
            else:
                # the clip is complete: nothing is retained
                self._discard_render()
                self.session = READY
        self._enqueue(self._empty_rsp(cmd, seq))

    @staticmethod
    def _parse_render_trigger(payload: bytes):
        """(pass_index, identity, digest_prefix, digest) or None.

        ``None`` means the payload is not the shape the walk could traverse
        (too short, an identity beyond the 64-byte window, or trailing
        bytes) — an ERR_PAYLOAD_LENGTH answered walk-free.
        """
        if len(payload) < RENDER_TRIGGER_FIXED:
            return None
        ident_len = payload[1] | (payload[2] << 8)
        if ident_len > IDENTITY_WINDOW:
            return None
        if len(payload) != RENDER_TRIGGER_FIXED + ident_len:
            return None
        identity = payload[3 : 3 + ident_len]
        digest_prefix = payload[3 + ident_len] | (payload[4 + ident_len] << 8)
        return payload[0], identity, digest_prefix, payload[5 + ident_len :]

    @staticmethod
    def _parse_noise_stream(payload: bytes):
        """(pass_index, offset, data_len) or None for a walk-free shape fault."""
        if len(payload) < NOISE_STREAM_FIXED + 1:
            return None
        data_len = payload[5] | (payload[6] << 8)
        if len(payload) != NOISE_STREAM_FIXED + data_len:
            return None
        return payload[0], int.from_bytes(payload[1:5], "little"), data_len

    # ---- shared helpers --------------------------------------------------
    @staticmethod
    def _frame_bytes(cmd: int, seq: int, payload: bytes) -> bytes:
        body = (
            struct.pack("<BBBH", PROTOCOL_VERSION, KIND_COMMAND, cmd, seq)
            + struct.pack("<H", len(payload))
            + payload
        )
        crc = crc16_ccitt_false(body)
        return SYNC + body + struct.pack("<H", crc)

    def _empty_rsp(self, cmd: int, seq: int) -> dict:
        return {"kind": KIND_RESPONSE, "cmd": cmd, "seq": seq, "payload": b""}

    def _ready_rsp(self, cmd: int, seq: int, caps: int) -> dict:
        payload = encode_ready(
            Ready(
                profile_id=self.profile_id,
                numeric_contract_version=self.contract_version,
                locks=b"",
                capabilities=caps & self.granted_caps,
                max_payload=self.adv_max_payload,
                rx_queue_depth=self.adv_rx_depth,
                patch_timeout_ms=self.adv_timeout_ms,
            )
        )
        return {"kind": KIND_RESPONSE, "cmd": cmd, "seq": seq, "payload": payload}

    def _enqueue_error(self, cmd: int, seq: int, code: int) -> None:
        self._enqueue(
            {
                "kind": KIND_ERROR,
                "cmd": cmd,
                "seq": seq,
                "payload": bytes([code]),
            }
        )

    def _enqueue(self, request: dict) -> None:
        if len(self._req_queue) >= REQ_QUEUE_DEPTH:
            raise AssertionError(
                "request queue overflow (%d): the RTL wraps at %d; this is a "
                "stimulus/mirror divergence" % (len(self._req_queue), REQ_QUEUE_DEPTH)
            )
        self._req_queue.append(request)

    def _cache_idempotent(self, cmd: int, seq: int, payload: bytes, request: dict) -> None:
        self._caches[cmd][seq] = {
            "frame": self._frame_bytes(cmd, seq, payload),
            "request": request,
        }

    def _tx_seq_span_record(self, seq: int) -> None:
        if self._tx_open:
            self._tx_seq_last = seq

    def _expired_or_bad_sequence(self) -> int:
        if (
            self._done_valid
            and self._done_expired
            and self._done_seq_first <= self._cur[1] <= self._done_seq_last
        ):
            return ERR_TX_TIMEOUT
        return ERR_BAD_SEQUENCE

    def _discard_transaction(self, seq: int) -> None:
        self._finish_transaction(seq, expired=False)

    def _finish_transaction(self, seq: int, *, expired: bool) -> None:
        self._done_id = self._tx_id
        self._done_seq_first = self._tx_seq_first
        self._done_seq_last = self._tx_seq_last
        self._done_valid = True
        self._done_expired = expired
        self._tx_clear()

    # ---- payload parsing (shape checks at decide) -------------------------
    @staticmethod
    def _parse_hello(payload: bytes):
        off = 0
        try:
            plen = payload[off] | (payload[off + 1] << 8)
            off += 2
            if plen > 64 or off + plen > len(payload):
                return None
            profile = payload[off : off + plen]
            off += plen
            slen = payload[off] | (payload[off + 1] << 8)
            off += 2
            if slen > 64 or off + slen + 2 + 32 + 2 != len(payload):
                return None
            source = payload[off : off + slen]
            off += slen
            # core_protocol carries the contract version opaque-packed
            # (2-byte length prefix + 32 bytes), like every HELLO region
            if payload[off] | (payload[off + 1] << 8) != 32:
                return None
            off += 2
            contract = payload[off : off + 32]
            off += 32
            caps = payload[off] | (payload[off + 1] << 8)
            return profile, source, contract, caps
        except IndexError:
            return None

    @staticmethod
    def _parse_open(payload: bytes):
        try:
            lens = PatchControlModel._read_opaque_lens(payload)
            if lens is None:
                return None
            id_len, ident_len, _tail = lens
            off = 2
            tx_id = payload[off : off + id_len]
            if not 1 <= len(tx_id) <= 16:
                return None
            off += id_len
            identity = payload[off + 2 : off + 2 + ident_len]
            if len(identity) > 64:
                return None
            off += 2 + ident_len
            if payload[off] | (payload[off + 1] << 8) != 32:
                return None
            off += 2
            table_ref = payload[off : off + 32]
            if len(table_ref) != 32:
                return None
            off += 32
            want = payload[off] | (payload[off + 1] << 8)
            off += 2
            if off != len(payload):
                return None
            return tx_id, identity, table_ref, want
        except IndexError:
            return None

    @staticmethod
    def _parse_name(payload: bytes):
        """(shape_ok, name_or_None): shape violations and decode failures
        are distinct outcomes (refinement 6: unresolvable vs bad shape)."""
        if len(payload) < 2:
            return False, None
        nl = payload[0] | (payload[1] << 8)
        if nl == 0 or 2 + nl != len(payload):
            return False, None
        try:
            return True, payload[2 : 2 + nl].decode("utf-8")
        except UnicodeDecodeError:
            return True, None

    @staticmethod
    def _parse_value(payload: bytes):
        """(shape_ok, name_or_None, word_bytes_or_None)."""
        if len(payload) < 4:
            return False, None, None
        nl = payload[0] | (payload[1] << 8)
        off = 2 + nl
        if off + 2 > len(payload):
            return False, None, None
        vl = payload[off] | (payload[off + 1] << 8)
        off += 2
        if vl != PARAM_VALUE_BYTES or off + vl != len(payload):
            return False, None, None
        try:
            name = payload[2 : 2 + nl].decode("utf-8")
        except UnicodeDecodeError:
            return True, None, payload[off : off + vl]
        return True, name, payload[off : off + vl]

    # ---- response pipeline (order + content only) --------------------------
    def _response_step(self) -> None:
        if not self._req_queue:
            return
        request = self._req_queue.pop(0)
        body = (
            struct.pack(
                "<BBBH",
                PROTOCOL_VERSION,
                request["kind"],
                request["cmd"],
                request["seq"],
            )
            + struct.pack("<H", len(request["payload"]))
            + request["payload"]
        )
        crc = crc16_ccitt_false(body)
        self._rsp.extend(SYNC + body + struct.pack("<H", crc))


def decode_rsp_frames(stream: bytes) -> List[Tuple[int, int, int, bytes]]:
    """Split a response byte stream into (kind, cmd, seq, payload) tuples."""
    frames = []
    i = 0
    while i < len(stream):
        if stream[i : i + 2] != SYNC:
            raise AssertionError("response stream lost sync at %d" % i)
        version, kind, cmd, seq = struct.unpack("<BBBH", stream[i + 2 : i + 7])
        length = struct.unpack("<H", stream[i + 7 : i + 9])[0]
        assert version == PROTOCOL_VERSION
        payload = stream[i + 9 : i + 9 + length]
        crc = struct.unpack("<H", stream[i + 9 + length : i + 11 + length])[0]
        assert crc == crc16_ccitt_false(stream[i + 2 : i + 9 + length])
        frames.append((kind, cmd, seq, payload))
        i += 11 + length
    return frames


def expected_bank_words(parameter_words: Dict[str, int], names: Sequence[str]) -> List[int]:
    """Active-bank word vector for a fully applied named patch."""
    slot_of = {name: i for i, name in enumerate(names)}
    return [parameter_words.get(name, 0) for name in names]
