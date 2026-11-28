"""Patch-control lane of the gf180 testbench harness (issue #69, split out
by #593).

``tb/run_tb.py`` re-exports selected names such as ``patch`` and
calls :func:`bind_core` right after defining the shared symbols this lane
needs (``CONSTANTS_PKG_SV``), so the lane reads them from the one live
``run_tb`` module (including when it runs as ``__main__``) instead of
copying them or importing a second copy.

The render-receiver half (issue #211) rides along: the ``normreplay`` lane's
``render_binding_frames`` / ``render_binding_integration`` resolve the
frame builders off ``run_tb``, which re-exports them from here.
"""

from __future__ import annotations

import hashlib
import random
import struct
import sys
from pathlib import Path

TB_ROOT = Path(__file__).resolve().parent
ROOT = TB_ROOT.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(TB_ROOT))

from tb_harness import (  # noqa: E402
    _compile_iverilog,
    _run,
    mutate_sv,
)
from torchsynth_voice import golden_vectors as gv  # noqa: E402
from torchsynth_voice.core_protocol import (  # noqa: E402
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
    CMD_RENDER_TRIGGER,
    CMD_RESET,
    KIND_COMMAND,
    ErrorCode,
    encode_frame,
    encode_hello,
    encode_noise_stream,
    encode_patch_commit,
    encode_patch_name,
    encode_patch_open,
    encode_patch_value,
    encode_render_trigger,
    numeric_contract_version_bound,
)
from torchsynth_voice.fixed_voice import (  # noqa: E402
    AcceptedFormats,
    entry_quantize,
)
from torchsynth_voice.fixedpoint import schedule as sched  # noqa: E402
from torchsynth_voice.fixedpoint.counters import StickyCounters  # noqa: E402
from torchsynth_voice.fixedpoint.schedule import ScheduleNotAccepted  # noqa: E402
from torchsynth_voice.patch_control_model import (  # noqa: E402
    decode_rsp_frames,
)

_core = None


def bind_core(core) -> None:
    """Bind the ``run_tb`` module that owns the shared core symbols."""

    global _core
    _core = core


# ======================================================================
# Patch-control flow (issue #69): the RTL patch loader/identity/reset/
# keyboard core against its cycle-exact Python mirror.
# ======================================================================

PATCH_DUT_SV = TB_ROOT / "sv/patch_control.sv"
PATCH_TB_SV = TB_ROOT / "sv/tb_patch_control.sv"
#: Must equal the DUT's TIMEOUT_CYCLES parameter default.
PATCH_TIMEOUT_CYCLES = 5000
#: Per-pass host-fed noise-stream length, the product profile's clip length
#: (RENDER-TRIGGER.md "Render lifecycle": SCHED_SAMPLES_PER_PASS x 4). Must
#: equal the DUT's NOISE_CLIP_BYTES parameter default and the mirror's.
NOISE_CLIP_BYTES = 705_600
#: The directed scale-down used for the render-lifecycle framing battery:
#: the same state machine over a clip short enough to feed many times in
#: one scenario. Chosen so both a multi-chunk feed and a ragged final chunk
#: occur (2048 = 1000 + 1000 + 48).
RENDER_SMALL_CLIP_BYTES = 2048
#: The largest NOISE_STREAM data region a 1024-byte max payload admits
#: (1 pass byte + 4 offset bytes + 2 length-prefix bytes of overhead).
NOISE_CHUNK_MAX = 1024 - 7

CORE_PROFILE_ID = b"torchsynth-1-voice-default"
CORE_IDENTITY = b"identity-golden-0001"
SOURCE_VERSION = b"torchsynth@2b0964d4c6c3d472a2a0d54d91b408caaeffca6d"


def patch_build_model(noise_clip_bytes: int = NOISE_CLIP_BYTES):
    """The mirror oracle bound to the pinned inventory + accepted register."""
    from torchsynth_voice.patch_control_model import PatchControlModel

    inventory = gv.load_inventory_document()
    names = sorted(entry["name"] for entry in inventory["parameters"])
    table_sha = hashlib.sha256(gv.INVENTORY_PATH.read_bytes()).digest()
    return PatchControlModel(
        names,
        table_sha256=table_sha,
        contract_version=numeric_contract_version_bound(),
        profile_id=CORE_PROFILE_ID,
        timeout_cycles=PATCH_TIMEOUT_CYCLES,
        noise_clip_bytes=noise_clip_bytes,
    )


def frame(command: int, seq: int, payload: bytes = b"") -> bytes:
    return encode_frame(KIND_COMMAND, command, seq, payload)


def frame_v1(command: int, seq: int) -> bytes:
    """A complete, CRC-valid protocol version 1 frame (stale peer)."""
    body = (
        struct.pack("<BBBH", 1, KIND_COMMAND, command, seq)
        + struct.pack("<H", 0)
    )
    return b"\x67\xf1" + body + struct.pack(
        "<H", crc16_ccitt_false(body)
    )


def crc16_ccitt_false(data: bytes) -> int:
    from torchsynth_voice.core_protocol import crc16_ccitt_false as crc

    return crc(data)


def paced(frames, gap: int = 300, after_commit: int = 4700) -> list:
    """Interleave a conforming host's pacing gap after every frame.

    The advertised rx_queue_depth is 1 and the core processes one frame at
    a time, so a deterministic golden path leaves each frame uncontended.
    A full 78-name commit's hash walk runs ~4.5k cycles, so the gap after
    a COMMIT frame is longer (still below the 5000-cycle patch timeout the
    mirror and DUT share). The backpressure scenario exercises the unpaced
    burst on purpose.
    """
    items: list = []
    for frame in frames:
        items.append(frame)
        if isinstance(frame, bytes):
            items.append(
                after_commit
                if (len(frame) > 4 and frame[4] == CMD_PATCH_COMMIT) else gap
            )
    return items


def schedule_of(items) -> list:
    """A per-cycle schedule from frame bytes and idle-cycle counts."""
    schedule: list = []
    for item in items:
        if isinstance(item, int):
            schedule.extend([None] * item)
        else:
            schedule.extend(item)
    return schedule


def write_schedule(path: Path, schedule: list) -> None:
    lines = ["B %d" % b if b is not None else "I" for b in schedule]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def patch_simulate(workdir: Path, simulator: str, stim: Path,
                   dut_sv: Path, clip_bytes: int = NOISE_CLIP_BYTES) -> tuple:
    """Compile + run the tb; return (response byte list, final observables).

    ``clip_bytes`` overrides the DUT's ``NOISE_CLIP_BYTES`` parameter (the
    per-pass host-fed noise-stream length, RENDER-TRIGGER.md). The product
    value is the default; a smaller one is a directed scale-down so the
    render lifecycle's framing cases cost a few thousand cycles instead of
    the ~1.4M a full 705,600-byte two-pass clip costs.
    """
    captured = workdir / "captured.txt"
    final = workdir / "final.txt"
    vvp = _compile_iverilog(
        workdir, simulator, "patch_control.vvp",
        [_core.CONSTANTS_PKG_SV, dut_sv, PATCH_TB_SV],
        flags=[
            "-I", str(TB_ROOT / "sv"),
            "-Ptb_patch_control.NOISE_CLIP_BYTES=%d" % clip_bytes,
        ],
    )
    _run(
        [
            "vvp", "-n", str(vvp),
            "+stim=%s" % stim,
            "+capture=%s" % captured,
            "+final=%s" % final,
        ],
        cwd=workdir,
    )
    rsp = [
        int(line)
        for line in captured.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    fields = final.read_text(encoding="utf-8").splitlines()
    head = [int(v) for v in fields[0].split()]
    obs = {
        "session": head[0],
        "patch_active": bool(head[1]),
        "kbd_midi_word": head[2],
        "kbd_duration_word": head[3],
        "identity_len": head[4],
        "bind_pulses": head[6],
        "bind_max_run": head[7],
        "identity_hex": fields[1].strip(),
        "bank": [int(v) for v in fields[2 : 2 + 78]],
    }
    return rsp, obs


def obs_from_mirror(obs: dict) -> dict:
    identity = obs["identity"]
    return {
        "session": obs["session"],
        "patch_active": obs["patch_active"],
        "kbd_midi_word": obs["kbd_midi_word"],
        "kbd_duration_word": obs["kbd_duration_word"],
        "identity_len": len(identity),
        "identity_hex": identity.hex(),
        "bind_pulses": obs["bind_pulses"],
        "bank": [obs["bank"][slot] for slot in range(78)],
    }


def _frame_summary(frame: tuple) -> str:
    kind, cmd, seq, payload = frame
    name = {2: "RSP", 3: "ERR"}.get(kind, "kind%d" % kind)
    detail = ""
    if kind == 3 and payload:
        try:
            detail = " " + ErrorCode(payload[0]).name
        except ValueError:
            detail = " code=0x%02x" % payload[0]
    return "%s(cmd=0x%02x seq=%d%s)" % (name, cmd, seq, detail)


def compare_run(label: str, schedule: list, workdir: Path, simulator: str,
                model, dut_sv: Path, extra_checks=None) -> bool:
    """Mirror vs RTL: exact response byte stream + exact final state."""
    expected_rsp, mirror_obs = model.run(schedule)
    if mirror_obs["session"] in (2, 3):
        raise SystemExit(
            "%s: scenario ends with an open transaction or an open render; "
            "end every scenario in ready" % label
        )
    workdir.mkdir(parents=True, exist_ok=True)
    stim = workdir / "stimulus.txt"
    write_schedule(stim, schedule)
    rsp, obs = patch_simulate(workdir, simulator, stim, dut_sv,
                              model.noise_clip_bytes)

    ok = True
    if rsp != list(expected_rsp):
        ok = False
        print("%s: RESPONSE STREAM MISMATCH" % label)
        expected_frames = decode_rsp_frames(bytes(expected_rsp))
        try:
            actual_frames = decode_rsp_frames(bytes(rsp))
        except AssertionError:
            actual_frames = []
        for index, (want, got) in enumerate(
            zip(expected_frames, actual_frames)
        ):
            if want != got:
                print(
                    "  first differing frame #%d: mirror %s vs rtl %s"
                    % (index, _frame_summary(want), _frame_summary(got))
                )
                break
        if len(expected_frames) != len(actual_frames):
            print(
                "  frame counts: mirror %d vs rtl %d"
                % (len(expected_frames), len(actual_frames))
            )
    want_obs = obs_from_mirror(mirror_obs)
    # the tb always dumps the full 64-byte identity window; only the
    # declared length-prefix carries meaning
    obs["identity_hex"] = obs["identity_hex"][: 2 * obs["identity_len"]]
    for key in ("session", "patch_active", "kbd_midi_word",
                "kbd_duration_word", "identity_len", "identity_hex",
                "bind_pulses"):
        if obs[key] != want_obs[key]:
            ok = False
            print(
                "%s: final state mismatch %s: mirror %r vs rtl %r"
                % (label, key, want_obs[key], obs[key])
            )
    # The engine's contract is a one-cycle pulse, not a level hold.
    if obs["bind_max_run"] > 1:
        ok = False
        print(
            "%s: bind_reject held high for %d consecutive cycles; the "
            "replay engine's contract is a one-cycle pulse"
            % (label, obs["bind_max_run"])
        )
    if obs["bank"] != want_obs["bank"]:
        ok = False
        first = next(
            slot
            for slot in range(78)
            if obs["bank"][slot] != want_obs["bank"][slot]
        )
        print(
            "%s: active bank mismatch at slot %d: mirror %d vs rtl %d"
            % (label, first, want_obs["bank"][first], obs["bank"][first])
        )
    if extra_checks is not None:
        ok = extra_checks(obs) and ok
    print("%s: %s" % (label, "OK" if ok else "FAIL"))
    return ok


def patch_anchor_words():
    """The 78 wire words from the anchor vector's own declared parameters."""
    vector = gv.load_vector(gv.SENTINEL_VECTOR_PATH)
    counters = StickyCounters()
    words = {}
    for name in sorted(vector["parameters"]):
        words[name] = entry_quantize(
            float(vector["parameters"][name]), formats_midi(), counters,
            "tb.patch.entry:" + name,
        )
    return words, vector


_formats_cache = None


def formats_midi():
    global _formats_cache
    if _formats_cache is None:
        _formats_cache = AcceptedFormats()
    return _formats_cache.midi


def word4(value: int) -> bytes:
    return (value & 0xFFFFFFFF).to_bytes(4, "little", signed=True)


def patch_hash_entries(model, entries: dict) -> bytes:
    from torchsynth_voice.core_protocol import patch_hash

    return patch_hash(entries, numeric_contract_version=model.contract_version)


def build_scenarios(model, words: dict, vector: dict) -> dict:
    """Every scenario's frame list + idle tail, keyed by scenario id."""
    from torchsynth_voice.core_protocol import patch_hash

    names = list(model.names)
    hello = frame(
        CMD_HELLO, 0,
        encode_hello(CORE_PROFILE_ID, SOURCE_VERSION,
                     model.contract_version, 3),
    )
    rng = random.Random(20260921)
    shuffled = list(names)
    rng.shuffle(shuffled)

    def full_tx(first_seq: int, tx_id: bytes, order: list):
        frames = [
            frame(CMD_PATCH_OPEN, first_seq,
                  encode_patch_open(tx_id, CORE_IDENTITY, model.table_sha256,
                                    len(names)))
        ]
        seq = first_seq + 1
        entries = {}
        for name in order:
            encoded = word4(words[name])
            entries[name] = encoded
            frames.append(frame(CMD_PATCH_NAME, seq,
                                encode_patch_name(name)))
            frames.append(frame(CMD_PATCH_VALUE, seq + 1,
                                encode_patch_value(name, encoded)))
            seq += 2
        digest = patch_hash_entries(model, entries)
        frames.append(frame(CMD_PATCH_COMMIT, seq,
                            encode_patch_commit(digest)))
        return frames, seq

    # ---- s1: full shuffled load, RESET, sub-patch, full re-load --------
    tx1, seq1 = full_tx(1, b"tx-full-0001", shuffled)
    sub_names = ["adsr_1.alpha", "keyboard.midi_f0"]
    sub_entries = {name: word4(words[name]) for name in sub_names}
    sub_digest = patch_hash_entries(model, sub_entries)
    base = seq1 + 1  # next free sequence after tx1's commit
    sub_frames = [
        frame(CMD_PATCH_OPEN, base + 1,
              encode_patch_open(b"tx-sub-0002", CORE_IDENTITY,
                                model.table_sha256, 2)),
        frame(CMD_PATCH_NAME, base + 2, encode_patch_name(sub_names[0])),
        frame(CMD_PATCH_VALUE, base + 3,
              encode_patch_value(sub_names[0], sub_entries[sub_names[0]])),
        frame(CMD_PATCH_NAME, base + 4, encode_patch_name(sub_names[1])),
        frame(CMD_PATCH_VALUE, base + 5,
              encode_patch_value(sub_names[1], sub_entries[sub_names[1]])),
        frame(CMD_PATCH_COMMIT, base + 6, encode_patch_commit(sub_digest)),
    ]
    tx3, _seq3 = full_tx(base + 7, b"tx-full-0003", list(names))
    s1_frames = (
        [hello] + tx1
        + [frame(CMD_RESET, base)]
        + sub_frames + tx3
    )

    # ---- s2: negative battery ------------------------------------------
    alpha_word = sub_entries["adsr_1.alpha"]
    attack_word = word4(words["adsr_1.attack"])
    pair_entries = {"adsr_1.alpha": alpha_word, "adsr_1.attack": attack_word}
    pair_digest = patch_hash_entries(model, pair_entries)
    alpha_only = {"adsr_1.alpha": alpha_word}
    alpha_digest = patch_hash_entries(model, alpha_only)
    neg = [hello]
    neg += [
        frame(CMD_PATCH_OPEN, 1, encode_patch_open(b"tx-neg-01", b"",
                                                   model.table_sha256, 2)),
        frame(CMD_PATCH_NAME, 2, encode_patch_name("adsr_1.alpha")),
        frame(CMD_PATCH_VALUE, 3, encode_patch_value("adsr_1.alpha",
                                                     alpha_word)),
        frame(CMD_PATCH_NAME, 4, encode_patch_name("adsr_1.attack")),
        frame(CMD_PATCH_VALUE, 5, encode_patch_value("adsr_1.attack",
                                                     attack_word)),
        frame(CMD_PATCH_COMMIT, 6, encode_patch_commit(bytes(32))),
    ]
    neg += [
        frame(CMD_PATCH_OPEN, 7, encode_patch_open(b"tx-neg-02", b"",
                                                   model.table_sha256, 2)),
        frame(CMD_PATCH_NAME, 8, encode_patch_name("adsr_1.alpha")),
        frame(CMD_PATCH_VALUE, 9, encode_patch_value("adsr_1.alpha",
                                                     alpha_word)),
        frame(CMD_PATCH_NAME, 10, encode_patch_name("adsr_1.attack")),
        frame(CMD_PATCH_VALUE, 11, encode_patch_value("adsr_1.attack",
                                                      attack_word)),
        frame(CMD_PATCH_COMMIT, 12, encode_patch_commit(pair_digest)),
    ]
    neg += [
        frame(CMD_PATCH_OPEN, 13, encode_patch_open(b"tx-neg-03", b"",
                                                    model.table_sha256, 3)),
        frame(CMD_PATCH_NAME, 14, encode_patch_name("adsr_1.alpha")),
        frame(CMD_PATCH_VALUE, 15, encode_patch_value("adsr_1.alpha",
                                                      alpha_word)),
        frame(CMD_PATCH_NAME, 16, encode_patch_name("adsr_1.attack")),
        frame(CMD_PATCH_VALUE, 17, encode_patch_value("adsr_1.attack",
                                                      attack_word)),
        frame(CMD_PATCH_NAME, 18, encode_patch_name("vco_1.tuning")),
        frame(CMD_PATCH_COMMIT, 19, encode_patch_commit(pair_digest)),
    ]
    neg += [
        frame(CMD_PATCH_OPEN, 20, encode_patch_open(b"tx-neg-04", b"",
                                                    model.table_sha256, 1)),
        frame(CMD_PATCH_VALUE, 21, encode_patch_value("keyboard.midi_f0",
                                                      alpha_word)),
        frame(CMD_PATCH_NAME, 22, encode_patch_name("adsr_1.alpha")),
        frame(CMD_PATCH_VALUE, 23, encode_patch_value("adsr_1.alpha",
                                                      b"\x01\x00\x00\x00")),
        frame(CMD_PATCH_VALUE, 24, encode_patch_value("adsr_1.alpha",
                                                      b"\x02\x00\x00\x00")),
        frame(CMD_PATCH_VALUE, 25, encode_patch_value("adsr_1.alpha",
                                                      b"\x01\x00\x00\x00")),
        frame(CMD_PATCH_COMMIT, 26, encode_patch_commit(
            patch_hash_entries(model, {"adsr_1.alpha": b"\x01\x00\x00\x00"}))),
    ]
    neg += [
        frame(CMD_PATCH_OPEN, 27, encode_patch_open(b"tx-neg-05", b"",
                                                    model.table_sha256, 1)),
        frame(CMD_PATCH_OPEN, 28, encode_patch_open(b"tx-neg-06", b"",
                                                    model.table_sha256, 1)),
        frame(CMD_PATCH_ABORT, 29),
        frame(CMD_PATCH_ABORT, 30),
        frame(CMD_PATCH_VALUE, 3, encode_patch_value("adsr_1.alpha",
                                                     alpha_word)),
        frame(CMD_PATCH_OPEN, 31, encode_patch_open(b"tx-neg-07", b"",
                                                    bytes(32), 1)),
        frame(0x2A, 32),
        frame(CMD_PATCH_NAME, 33, encode_patch_name("adsr_1.alpha")),
    ]
    neg += [
        frame(CMD_PATCH_OPEN, 34, encode_patch_open(b"tx-neg-08", b"",
                                                    model.table_sha256, 1)),
        frame(CMD_PATCH_NAME, 35, encode_patch_name("adsr_1.alpha")),
        frame(CMD_PATCH_VALUE, 36, encode_patch_value("adsr_1.alpha",
                                                      b"\x03\x00\x00\x00")),
        frame(CMD_PATCH_ABORT, 37),
        frame(CMD_PATCH_OPEN, 38, encode_patch_open(b"tx-neg-09", b"",
                                                    model.table_sha256, 1)),
        frame(CMD_PATCH_NAME, 39, encode_patch_name("adsr_1.alpha")),
        frame(CMD_PATCH_VALUE, 40, encode_patch_value("adsr_1.alpha",
                                                      alpha_word)),
        frame(CMD_PATCH_COMMIT, 41, encode_patch_commit(alpha_digest)),
    ]

    # ---- s3: fatal negotiation gate --------------------------------------
    bad_hello = frame(
        CMD_HELLO, 0,
        encode_hello(CORE_PROFILE_ID, SOURCE_VERSION, bytes(32), 3),
    )
    s3_frames = [
        bad_hello,
        frame(CMD_PATCH_OPEN, 1, encode_patch_open(b"tx-fatal", b"",
                                                   model.table_sha256, 1)),
        hello,
        frame_v1(CMD_RESET, 2),
    ]

    # ---- s4: backpressure ERR_BUSY + identical retry ----------------------
    one_tx = [
        frame(CMD_PATCH_OPEN, 1, encode_patch_open(b"tx-busy-01", b"",
                                                   model.table_sha256, 1)),
        frame(CMD_PATCH_NAME, 2, encode_patch_name("adsr_1.alpha")),
        frame(CMD_PATCH_VALUE, 3, encode_patch_value("adsr_1.alpha",
                                                     alpha_word)),
        frame(CMD_PATCH_COMMIT, 4, encode_patch_commit(alpha_digest)),
    ]
    busy_hello = frame(
        CMD_HELLO, 6,
        encode_hello(CORE_PROFILE_ID, SOURCE_VERSION,
                     model.contract_version, 3),
    )
    s4_frames = (
        paced([hello] + one_tx[:3])
        + [one_tx[3], frame(CMD_RESET, 5), busy_hello]
        + [350]
        + [busy_hello]
    )

    # ---- s5: patch timeout + continuation refusal + fresh tx --------------
    fresh_entries = {"keyboard.duration": word4(words["keyboard.duration"])}
    s5_frames = (
        [hello]
        + [frame(CMD_PATCH_OPEN, 1, encode_patch_open(b"tx-timeout-01", b"",
                                                      model.table_sha256, 1)),
           frame(CMD_PATCH_NAME, 2, encode_patch_name("adsr_1.alpha"))]
        + [PATCH_TIMEOUT_CYCLES + 50]
        + [frame(CMD_PATCH_NAME, 2, encode_patch_name("adsr_1.alpha"))]
        + [frame(CMD_PATCH_OPEN, 3, encode_patch_open(b"tx-timeout-02", b"",
                                                      model.table_sha256, 1)),
           frame(CMD_PATCH_NAME, 4,
                 encode_patch_name("keyboard.duration")),
           frame(CMD_PATCH_VALUE, 5,
                 encode_patch_value("keyboard.duration",
                                    fresh_entries["keyboard.duration"])),
           frame(CMD_PATCH_COMMIT, 6, encode_patch_commit(
               patch_hash_entries(model, fresh_entries)))]
    )

    # ---- s6: bad frames + stale protocol version --------------------------
    good_reset = frame(CMD_RESET, 1)
    corrupt = bytearray(good_reset)
    corrupt[-1] ^= 0xFF
    s6_frames = [
        hello, bytes(corrupt), good_reset, frame_v1(CMD_RESET, 2),
        frame(CMD_RESET, 3),
    ]

    scenarios = {
        "s1-full": (paced(s1_frames), [3000]),
        "s2-negative": (paced(neg), [3000]),
        "s3-fatal": (paced(s3_frames), [3000]),
        "s4-busy": (s4_frames, [3000]),
        "s5-timeout": (paced(s5_frames), [3000]),
        "s6-badframe": (paced(s6_frames), [3000]),
    }
    return {
        key: schedule_of(frames + tail)
        for key, (frames, tail) in scenarios.items()
    }


# ----------------------------------------------------------------------
# Render lane (issue #211): the RTL receiver for RENDER_TRIGGER (0x15) and
# NOISE_STREAM (0x16), the pass-1 declared-digest + sound-identity binding
# it compares at the pass-2 trigger, and the one-cycle bind_reject pulse it
# drives when a rejection discards an open clip
# (spec/protocol/RENDER-TRIGGER.md; DR-0010 "Clip lifecycle").
# ----------------------------------------------------------------------

#: The pinned declared noise-stream digests these scenarios bind. Content is
#: opaque to the receiver: it latches the pass-1 declaration and compares the
#: pass-2 one, and computes no digest of its own (declared-only: receiver-side
#: hashing was rejected by the 2026-09-26 DR-0010 amendment, issue #207).
RENDER_DIGEST_A = bytes(range(32))
RENDER_DIGEST_B = bytes(range(32, 64))
#: Sound identities that are NOT the committed patch's. The first is the
#: same LENGTH as CORE_IDENTITY so the binding's content compare is what
#: rejects it (a length-only check would let it through); the second differs
#: in length, so both halves of the compare are exercised.
RENDER_FOREIGN_IDENTITY = b"identity-golden-0002"
RENDER_SHORT_IDENTITY = b"identity-golden-00"

#: Capability words a host may request in HELLO.
CAPS_PATCH_AND_RESET = CAP_NAME_KEYED_PATCH_LOAD | CAP_RESET
CAPS_WITH_RENDER = CAPS_PATCH_AND_RESET | CAP_RENDER


class SeqCounter:
    """Monotonic protocol sequence allocator for a scenario."""

    def __init__(self, start: int = 1):
        self.value = start - 1

    def next(self) -> int:
        self.value += 1
        return self.value


def render_hello(model, caps: int, seq: int = 0) -> bytes:
    return frame(
        CMD_HELLO, seq,
        encode_hello(CORE_PROFILE_ID, SOURCE_VERSION,
                     model.contract_version, caps),
    )


def render_full_tx(model, words: dict, seqs: SeqCounter, tx_id: bytes,
                   identity: bytes = CORE_IDENTITY) -> list:
    """A complete 78-name transaction — the only kind that asserts the gate.

    `patch_active` (reconciliation 4) is the RTL's "a committed patch is
    active" predicate for a pass-1 trigger, and it asserts only for a
    hash-matching commit of the full canonical table.
    """
    names = list(model.names)
    frames = [
        frame(CMD_PATCH_OPEN, seqs.next(),
              encode_patch_open(tx_id, identity, model.table_sha256,
                                len(names)))
    ]
    entries = {}
    for name in names:
        encoded = word4(words[name])
        entries[name] = encoded
        frames.append(frame(CMD_PATCH_NAME, seqs.next(),
                            encode_patch_name(name)))
        frames.append(frame(CMD_PATCH_VALUE, seqs.next(),
                            encode_patch_value(name, encoded)))
    frames.append(frame(CMD_PATCH_COMMIT, seqs.next(),
                        encode_patch_commit(patch_hash_entries(model, entries))))
    return frames


def render_trigger_frame(seqs: SeqCounter, pass_index: int, identity: bytes,
                         digest: bytes) -> bytes:
    return frame(CMD_RENDER_TRIGGER, seqs.next(),
                 encode_render_trigger(pass_index, identity, digest))


def noise_frame(seqs: SeqCounter, pass_index: int, offset: int,
                length: int) -> bytes:
    """One NOISE_STREAM frame. The data bytes are opaque to the receiver."""
    return frame(CMD_NOISE_STREAM, seqs.next(),
                 encode_noise_stream(pass_index, offset, bytes(length)))


def noise_pass_frames(seqs: SeqCounter, pass_index: int, total: int,
                      chunk: int = NOISE_CHUNK_MAX, start: int = 0) -> list:
    """Contiguous NOISE_STREAM frames covering [start, total) of one pass."""
    frames = []
    offset = start
    while offset < total:
        length = min(chunk, total - offset)
        frames.append(noise_frame(seqs, pass_index, offset, length))
        offset += length
    return frames


def paced_render(frames, gap: int = 300, after_commit: int = 4700,
                 after_noise: int = 40) -> list:
    """Pacing for a render scenario.

    A NOISE_STREAM frame's walk is 8 cycles (the data region costs none), so
    a short gap keeps a 705,600-byte pass affordable while still leaving the
    single frame slot free before the next frame's t_hdr.
    """
    items: list = []
    for item in frames:
        items.append(item)
        if isinstance(item, bytes):
            command = item[4]
            if command == CMD_PATCH_COMMIT:
                items.append(after_commit)
            elif command == CMD_NOISE_STREAM:
                items.append(after_noise)
            else:
                items.append(gap)
    return items


def render_happy_path_frames(model, words: dict, clip_bytes: int) -> list:
    """One complete clip: bind at pass 1, re-feed under pass 2, complete."""
    seqs = SeqCounter()
    frames = [render_hello(model, CAPS_WITH_RENDER)]
    frames += render_full_tx(model, words, seqs, b"tx-render-0001")
    frames.append(render_trigger_frame(seqs, 1, CORE_IDENTITY,
                                       RENDER_DIGEST_A))
    frames += noise_pass_frames(seqs, 1, clip_bytes)
    frames.append(render_trigger_frame(seqs, 2, CORE_IDENTITY,
                                       RENDER_DIGEST_A))
    frames += noise_pass_frames(seqs, 2, clip_bytes)
    return frames


def render_negative_frames(model, words: dict, clip_bytes: int) -> list:
    """Every non-success row of RENDER-TRIGGER.md's lifecycle table."""
    seqs = SeqCounter()
    trig1 = lambda: render_trigger_frame(seqs, 1, CORE_IDENTITY,
                                         RENDER_DIGEST_A)  # noqa: E731
    frames = []

    # --- capability negotiation: a host that does not request CAP_RENDER
    #     gets ERR_UNSUPPORTED_COMMAND for both codes and nothing changes.
    frames.append(render_hello(model, CAPS_PATCH_AND_RESET))
    frames.append(trig1())
    frames.append(noise_frame(seqs, 1, 0, 8))

    # --- CAP_RENDER granted from here on.
    frames.append(render_hello(model, CAPS_WITH_RENDER, seq=1))

    # --- ready, no committed patch: pass 1 is ERR_BAD_STATE; pass 2 and
    #     NOISE_STREAM are ERR_BAD_SEQUENCE (no render is open).
    frames.append(trig1())
    frames.append(render_trigger_frame(seqs, 2, CORE_IDENTITY,
                                       RENDER_DIGEST_A))
    frames.append(noise_frame(seqs, 1, 0, 8))

    # --- payload shapes: a pass index that is neither 1 nor 2, a declared
    #     digest that is not 32 bytes, and a trailing byte.
    frames.append(frame(CMD_RENDER_TRIGGER, seqs.next(),
                        b"\x03" + struct.pack("<H", len(CORE_IDENTITY))
                        + CORE_IDENTITY + struct.pack("<H", 32)
                        + RENDER_DIGEST_A))
    frames.append(frame(CMD_RENDER_TRIGGER, seqs.next(),
                        b"\x01" + struct.pack("<H", len(CORE_IDENTITY))
                        + CORE_IDENTITY + struct.pack("<H", 31)
                        + RENDER_DIGEST_A[:31]))
    frames.append(frame(CMD_RENDER_TRIGGER, seqs.next(),
                        b"\x01" + struct.pack("<H", len(CORE_IDENTITY))
                        + CORE_IDENTITY + struct.pack("<H", 32)
                        + RENDER_DIGEST_A + b"\x00"))
    frames.append(frame(CMD_NOISE_STREAM, seqs.next(),
                        b"\x01" + struct.pack("<I", 0)
                        + struct.pack("<H", 4) + b"\x00\x00\x00"))
    # A declared length above the advertised max payload is a bad frame
    # whose bytes are consumed. This is the only committed frame whose
    # declared length needs BOTH length bytes, so it is also the regression
    # for the t_hdr stale-high-byte defect the render lane surfaced.
    frames.append(noise_frame(seqs, 1, 0, 2048 - 7))

    # --- a committed patch, then an identity that is not its own: the
    #     pass-1 ERR_RENDER_BINDING row, where no clip existed and so no
    #     bind_reject pulse is driven.
    frames += render_full_tx(model, words, seqs, b"tx-render-neg-01")
    frames.append(render_trigger_frame(seqs, 1, RENDER_FOREIGN_IDENTITY,
                                       RENDER_DIGEST_A))

    # --- a cached PATCH_ABORT, then a render: the replay must not resurrect
    #     the cached success while rendering (refinement 9).
    abort_seq = seqs.next()
    frames.append(frame(CMD_PATCH_OPEN, seqs.next(),
                        encode_patch_open(b"tx-render-neg-02", CORE_IDENTITY,
                                          model.table_sha256, 1)))
    frames.append(frame(CMD_PATCH_ABORT, abort_seq))
    frames.append(trig1())
    frames.append(frame(CMD_PATCH_ABORT, abort_seq))

    # --- rendering: ordering + second-trigger rows, all state-preserving.
    frames.append(frame(CMD_PATCH_OPEN, seqs.next(),
                        encode_patch_open(b"tx-render-neg-03", CORE_IDENTITY,
                                          model.table_sha256, 1)))
    frames.append(frame(CMD_PATCH_NAME, seqs.next(),
                        encode_patch_name("adsr_1.alpha")))
    frames.append(trig1())

    # --- NOISE_STREAM framing faults, each discarding the clip entire and
    #     pulsing bind_reject once: wrong pass, then non-contiguous offset.
    frames.append(noise_frame(seqs, 2, 0, 8))
    frames.append(trig1())
    frames.append(noise_frame(seqs, 1, 4, 8))

    # --- a pass-2 trigger before pass 1 reaches the clip length.
    frames.append(trig1())
    frames.append(noise_frame(seqs, 1, 0, 8))
    frames.append(render_trigger_frame(seqs, 2, CORE_IDENTITY,
                                       RENDER_DIGEST_A))

    # --- the pass-1/pass-2 boundary digest mismatch (zero samples exist).
    frames.append(trig1())
    frames += noise_pass_frames(seqs, 1, clip_bytes)
    frames.append(render_trigger_frame(seqs, 2, CORE_IDENTITY,
                                       RENDER_DIGEST_B))

    # --- the same boundary, identity mismatch instead: once with an
    #     identity of the same length (only the content compare can reject
    #     it) and once with a different length.
    frames.append(trig1())
    frames += noise_pass_frames(seqs, 1, clip_bytes)
    frames.append(render_trigger_frame(seqs, 2, RENDER_FOREIGN_IDENTITY,
                                       RENDER_DIGEST_A))
    frames.append(trig1())
    frames += noise_pass_frames(seqs, 1, clip_bytes)
    frames.append(render_trigger_frame(seqs, 2, RENDER_SHORT_IDENTITY,
                                       RENDER_DIGEST_A))

    # --- a pass-1 NOISE_STREAM after pass 1 is already complete.
    frames.append(trig1())
    frames += noise_pass_frames(seqs, 1, clip_bytes)
    frames.append(noise_frame(seqs, 1, clip_bytes, 8))

    # --- a second pass-2 trigger while pass 2 is already open.
    frames.append(trig1())
    frames += noise_pass_frames(seqs, 1, clip_bytes)
    frames.append(render_trigger_frame(seqs, 2, CORE_IDENTITY,
                                       RENDER_DIGEST_A))
    frames.append(render_trigger_frame(seqs, 2, CORE_IDENTITY,
                                       RENDER_DIGEST_A))

    # --- a framing fault mid pass 2: some samples were already released,
    #     the clip is still discarded entire (bytes past the clip length).
    frames.append(trig1())
    frames += noise_pass_frames(seqs, 1, clip_bytes)
    frames.append(render_trigger_frame(seqs, 2, CORE_IDENTITY,
                                       RENDER_DIGEST_A))
    frames += noise_pass_frames(seqs, 2, clip_bytes - 8)
    frames.append(noise_frame(seqs, 2, clip_bytes - 8, 16))

    # --- RESET while rendering: all render state discarded, no pulse (the
    #     engine's own rst completes that discard), and the applied patch
    #     goes with the session, so the next pass-1 trigger is BAD_STATE.
    frames.append(trig1())
    frames.append(noise_frame(seqs, 1, 0, 8))
    frames.append(frame(CMD_RESET, seqs.next()))
    frames.append(trig1())

    # --- HELLO while rendering renegotiates and discards the same way.
    frames += render_full_tx(model, words, seqs, b"tx-render-neg-04")
    frames.append(trig1())
    frames.append(noise_frame(seqs, 1, 0, 8))
    frames.append(render_hello(model, CAPS_WITH_RENDER, seq=2))
    frames.append(trig1())
    return frames


def patch(workdir: Path, simulator: str) -> int:
    """Issue #69 flow: loader/identity/reset/keyboard vs its mirror."""
    model = patch_build_model()
    words, vector = patch_anchor_words()
    scenarios = build_scenarios(model, words, vector)
    names = list(model.names)
    ok = True

    def check_s1(obs):
        good = True
        midi_trace = next(
            t["values"][0] for t in vector["traces"]
            if t["name"] == "keyboard.midi_f0"
        )
        if obs["kbd_midi_word"] != midi_trace:
            print(
                "  keyboard.midi_f0 word %d != anchor trace %d"
                % (obs["kbd_midi_word"], midi_trace)
            )
            good = False
        dur_wire = entry_quantize(
            float(vector["parameters"]["keyboard.duration"]),
            formats_midi(), StickyCounters(), "tb.patch.entry:duration",
        )
        expected_dur = (dur_wire << 9) & ((1 << 47) - 1)
        if obs["kbd_duration_word"] != expected_dur:
            print(
                "  keyboard.duration word %d != exact wire-word widening %d"
                % (obs["kbd_duration_word"], expected_dur)
            )
            good = False
        if not obs["patch_active"]:
            print("  full patch commit did not assert the render gate")
            good = False
        return good

    def check_s2(obs):
        good = True
        if obs["patch_active"]:
            print("  render gate asserted in a partial-only session")
            good = False
        alpha = int.from_bytes(word4(words["adsr_1.alpha"]), "little",
                               signed=True)
        if obs["bank"][names.index("adsr_1.alpha")] != alpha:
            print("  stale-state control: adsr_1.alpha bank word wrong")
            good = False
        if obs["bank"][names.index("keyboard.midi_f0")] != 0:
            print("  keyboard.midi_f0 leaked into the partial-only session")
            good = False
        return good

    run_specs = [
        ("full-78 shuffled load + reset + re-load", "s1-full", check_s1),
        ("negative battery (13 controls)", "s2-negative", check_s2),
        ("fatal negotiation gate", "s3-fatal", None),
        ("backpressure ERR_BUSY + identical retry", "s4-busy", None),
        ("patch timeout + continuation refusal", "s5-timeout", None),
        ("bad-frame recovery + stale protocol version", "s6-badframe", None),
    ]
    for label, key, checks in run_specs:
        ok = compare_run(label, scenarios[key], workdir / key, simulator,
                         model, PATCH_DUT_SV, checks) and ok

    # --- render lane (issue #211, spec/protocol/RENDER-TRIGGER.md) -------
    # The lifecycle battery runs against a scaled-down NOISE_CLIP_BYTES so
    # a clip can be opened and discarded a dozen times in one scenario; the
    # product profile's own 705,600-byte two-pass clip is then completed
    # once at the DEFAULT parameter, so the real length is simulated, not
    # merely asserted.
    small_model = patch_build_model(RENDER_SMALL_CLIP_BYTES)
    render_scenarios = {
        "s7-render": (
            small_model,
            schedule_of(paced_render(render_happy_path_frames(
                small_model, words, RENDER_SMALL_CLIP_BYTES)) + [3000]),
            0,
        ),
        "s8-render-negative": (
            small_model,
            schedule_of(paced_render(render_negative_frames(
                small_model, words, RENDER_SMALL_CLIP_BYTES)) + [3000]),
            9,
        ),
        "s9-render-full-clip": (
            model,
            schedule_of(paced_render(render_happy_path_frames(
                model, words, NOISE_CLIP_BYTES)) + [3000]),
            0,
        ),
    }
    render_specs = [
        ("render lifecycle: bind, re-feed, complete (clip %d B)"
         % RENDER_SMALL_CLIP_BYTES, "s7-render"),
        ("render lifecycle: every rejection row (clip %d B)"
         % RENDER_SMALL_CLIP_BYTES, "s8-render-negative"),
        ("render lifecycle: product-profile clip completion (%d B/pass)"
         % NOISE_CLIP_BYTES, "s9-render-full-clip"),
    ]
    for label, key in render_specs:
        run_model, schedule, want_pulses = render_scenarios[key]

        def check_pulses(obs, expected=want_pulses, name=label):
            if obs["bind_pulses"] != expected:
                print(
                    "  %s: %d bind_reject pulse(s), expected %d"
                    % (name, obs["bind_pulses"], expected)
                )
                return False
            print(
                "  %s: %d bind_reject pulse(s) reached the replay engine's "
                "discard input, each exactly one cycle wide"
                % (name, obs["bind_pulses"])
            )
            return True

        ok = compare_run(label, schedule, workdir / key, simulator,
                         run_model, PATCH_DUT_SV, check_pulses) and ok

    # The product clip length is a bound constant, not a magic number: it is
    # SCHED_SAMPLES_PER_PASS x 4 from the emitted accepted-schedule register.
    try:
        clip_bound = sched.constant(
            sched.require_accepted_schedule(), "samples_per_pass"
        ) * 4
    except ScheduleNotAccepted as error:
        print("PATCH REFUSED: DR-0010 schedule register refused: %s" % error)
        return 1
    if clip_bound != NOISE_CLIP_BYTES or model.noise_clip_bytes != NOISE_CLIP_BYTES:
        ok = False
        print(
            "  render clip length: accepted schedule says %d B/pass but the "
            "harness/mirror use %d/%d"
            % (clip_bound, NOISE_CLIP_BYTES, model.noise_clip_bytes)
        )
    else:
        print(
            "  render clip length: %d B/pass == SCHED_SAMPLES_PER_PASS x 4 "
            "from the accepted DR-0010 schedule register -> OK" % clip_bound
        )

    # Mutations: every planted fault MUST be detected (AC 6).
    mutations_ok = True
    mutation_specs = [
        (
            "wrong-entry-byte-order (parameter-order hash walk)",
            (
                "b = name_rom[c_slot] >> ((NAME_BYTES - 1 - c_entry_off) * 8);",
                "b = name_rom[c_slot] >> (c_entry_off * 8);",
            ),
            "s1-full",
        ),
        (
            "stale-contract-version gate weakened",
            (
                "else if (cap_c != NUMERIC_CONTRACT_VERSION) begin",
                "else if (cap_c == NUMERIC_CONTRACT_VERSION) begin",
            ),
            "s3-fatal",
        ),
        (
            "partial load committed (render gate on every commit)",
            (
                "if (tx_want == NUM_PARAMS)\n"
                "                    patch_active_r <= 1'b1;",
                "if (1'b1)\n"
                "                    patch_active_r <= 1'b1;",
            ),
            "s2-negative",
        ),
        (
            "hash domain separation broken",
            (
                "b = PATCH_DOMAIN_TAG[(DOMAIN_TAG_LEN*8-1) - c_fed*8 -: 8];",
                "b = 8'h00;",
            ),
            "s1-full",
        ),
        # --- render lane (issue #211) ---
        (
            "CAP_RENDER withheld (the capability the two codes are gated on)",
            (
                "        CAP_PATCH_LOAD | CAP_RESET | CAP_RENDER;",
                "        CAP_PATCH_LOAD | CAP_RESET;",
            ),
            "s7-render",
        ),
        (
            "pass-1 sound-identity binding dropped",
            (
                "                else if ((e_flen_b[7:0] != identity_len_r) ||\n"
                "                         (ident != identity_r))",
                "                else if (1'b0)",
            ),
            "s8-render-negative",
        ),
        (
            "pass-2 declared-digest compare dropped",
            ("(cap_c != rnd_digest)", "(1'b0)"),
            "s8-render-negative",
        ),
        (
            "pass-2 sound-identity compare dropped",
            ("(ident != rnd_identity) ||", "(1'b0) ||"),
            "s8-render-negative",
        ),
        (
            "noise-stream offset contiguity dropped",
            ("(noff != rnd_accepted) ||", "(1'b0) ||"),
            "s8-render-negative",
        ),
        (
            "bind_reject never pulsed (the discard never reaches the engine)",
            ("            bind_reject_r <= 1'b1;", "            bind_reject_r <= 1'b0;"),
            "s8-render-negative",
        ),
        (
            "clip-length completion transition dropped",
            (
                "if ((rnd_accepted + dlen) == CLIP_BYTES_W) begin",
                "if (1'b0) begin",
            ),
            "s7-render",
        ),
    ]
    mutation_oracles = {key: (model, scenarios[key]) for key in scenarios}
    mutation_oracles.update(
        {key: (run_model, schedule)
         for key, (run_model, schedule, _pulses) in render_scenarios.items()}
    )
    for index, (label, (anchor, replacement), scenario) in enumerate(
        mutation_specs
    ):
        mut_dir = workdir / ("mut-%02d-%s" % (index, scenario))
        mut_dir.mkdir(parents=True, exist_ok=True)
        mutated_sv = mut_dir / "patch_control_mut.sv"
        mutated_sv.write_text(
            mutate_sv(PATCH_DUT_SV.read_text(encoding="utf-8"),
                      anchor, replacement, label),
            encoding="utf-8",
        )
        mut_model, mut_schedule = mutation_oracles[scenario]
        detected = not compare_run(
            "mutation [%s] %s" % (scenario, label),
            mut_schedule, mut_dir, simulator, mut_model, mutated_sv,
        )
        print(
            "mutation %s: %s"
            % (label, "DETECTED (test fails the mutant)" if detected
               else "NOT DETECTED")
        )
        mutations_ok = mutations_ok and detected
    ok = ok and mutations_ok

    if not ok:
        print("PATCH RUN FAILED")
        return 1
    print(
        "PATCH RUN PASSED (mirror-vs-RTL byte/cycle-exact on %d scenarios "
        "including the render lifecycle and one product-profile "
        "705,600-B/pass clip completion + %d mutations detected + keyboard "
        "golden words)" % (len(run_specs) + len(render_specs),
                           len(mutation_specs))
    )
    return 0
