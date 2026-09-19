"""Round-trip tests for the mock core: session, idempotency, patch staging."""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.core_protocol import (  # noqa: E402
    CAP_NAME_KEYED_PATCH_LOAD,
    CAP_RESET,
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
    KIND_COMMAND,
    KIND_ERROR,
    KIND_RESPONSE,
    KNOWN_CAPABILITIES,
    NUMERIC_CONTRACT_UNBOUND,
    PROTOCOL_VERSION,
    crc16_ccitt_false,
    decode_frame,
    decode_ready,
    encode_frame,
    encode_hello,
    encode_patch_commit,
    encode_patch_name,
    encode_patch_open,
    encode_patch_value,
    patch_hash,
)

from torchsynth_voice.core_protocol_mock import MockCore, SessionState  # noqa: E402


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


INVENTORY_PATH = ROOT / "spec" / "reference" / "parameter-inventory-v1.json"


class HarnessTestCase(unittest.TestCase):
    def make_core(self, *, clock=None, name_table=None, **kwargs):
        clock = clock if clock is not None else FakeClock()
        if name_table is None:
            name_table = {"keyboard.midi_f0", "lfo_1.rate", "vco_1.level"}
        core = MockCore(
            name_table=name_table,
            name_table_sha256=b"\x11" * 32,
            clock=clock,
            **kwargs,
        )
        return core, clock

    def hello(self, sequence=1, capabilities=KNOWN_CAPABILITIES):
        return encode_frame(
            KIND_COMMAND,
            CMD_HELLO,
            sequence,
            encode_hello(b"profile-x", b"source-x", NUMERIC_CONTRACT_UNBOUND, capabilities),
        )

    def negotiate(self, core, sequence=1, capabilities=KNOWN_CAPABILITIES):
        response = decode_frame(core.handle_frame(self.hello(sequence, capabilities)))
        self.assertEqual(response.command, CMD_READY)
        self.assertEqual(response.kind, KIND_RESPONSE)
        return decode_ready(response.payload)

    def open_patch(self, core, sequence, transaction_id=b"tx-1", count=1, table_sha=b"\x11" * 32):
        return core.handle_frame(
            encode_frame(
                KIND_COMMAND,
                CMD_PATCH_OPEN,
                sequence,
                encode_patch_open(transaction_id, b"identity-1", table_sha, count),
            )
        )

    def name_frame(self, sequence, name):
        return encode_frame(KIND_COMMAND, CMD_PATCH_NAME, sequence, encode_patch_name(name))

    def value_frame(self, sequence, name, value):
        return encode_frame(KIND_COMMAND, CMD_PATCH_VALUE, sequence, encode_patch_value(name, value))

    def commit_frame(self, sequence, values):
        return encode_frame(
            KIND_COMMAND,
            CMD_PATCH_COMMIT,
            sequence,
            encode_patch_commit(patch_hash(values)),
        )

    def success(self, command, sequence):
        return encode_frame(KIND_RESPONSE, command, sequence)

    def expect_error(self, frame_bytes, code, *, command=None):
        response = decode_frame(frame_bytes)
        self.assertEqual(response.kind, KIND_ERROR)
        self.assertEqual(response.payload, bytes([int(code)]))
        if command is not None:
            self.assertEqual(response.command, command)
        return response


class NegotiationTests(HarnessTestCase):
    def test_hello_is_answered_with_ready(self):
        core, _ = self.make_core(profile_id=b"prof", locks=b"\x01", rx_queue_depth=3, patch_timeout_s=7.5)
        ready = self.negotiate(core)
        self.assertEqual(ready.profile_id, b"prof")
        self.assertEqual(ready.numeric_contract_version, NUMERIC_CONTRACT_UNBOUND)
        self.assertEqual(ready.locks, b"\x01")
        self.assertEqual(ready.capabilities, KNOWN_CAPABILITIES)
        self.assertEqual(ready.max_payload, 1024)
        self.assertEqual(ready.rx_queue_depth, 3)
        self.assertEqual(ready.patch_timeout_ms, 7500)
        self.assertIs(core.state, SessionState.READY)

    def test_granted_capabilities_are_the_intersection(self):
        core, _ = self.make_core(capabilities=CAP_RESET)
        ready = self.negotiate(core, capabilities=KNOWN_CAPABILITIES)
        self.assertEqual(ready.capabilities, CAP_RESET)

    def test_version_mismatch_is_fatal(self):
        core, _ = self.make_core()
        frame = bytearray(self.hello())
        frame[2] = PROTOCOL_VERSION + 1
        frame[-2:] = crc16_ccitt_false(frame[2:-2]).to_bytes(2, "little")
        response = decode_frame(core.handle_frame(bytes(frame)))
        self.assertEqual(response.kind, KIND_ERROR)
        self.assertEqual(response.payload, bytes([ErrorCode.PROTOCOL_VERSION]))
        self.assertIs(core.state, SessionState.CLOSED)

    def test_corrupted_version_frame_answers_bad_frame(self):
        core, _ = self.make_core()
        self.negotiate(core)
        frame = bytearray(self.hello(sequence=50))
        frame[2] = PROTOCOL_VERSION + 1
        response = decode_frame(core.handle_frame(bytes(frame)))
        self.assertEqual(response.payload, bytes([ErrorCode.BAD_FRAME]))
        self.assertIs(core.state, SessionState.READY)

    def test_core_recovers_by_new_hello_after_fatal(self):
        core, _ = self.make_core()
        frame = bytearray(self.hello())
        frame[2] = 0x2A
        core.handle_frame(bytes(frame))
        ready = self.negotiate(core, sequence=9)
        self.assertEqual(ready.capabilities, KNOWN_CAPABILITIES)
        self.assertIs(core.state, SessionState.READY)

    def test_command_in_closed_state_is_rejected(self):
        core, _ = self.make_core()
        reset = encode_frame(KIND_COMMAND, CMD_RESET, 1)
        self.expect_error(core.handle_frame(reset), ErrorCode.BAD_STATE, command=CMD_RESET)

    def test_undefined_command_is_unsupported(self):
        core, _ = self.make_core()
        self.negotiate(core)
        undefined = encode_frame(KIND_COMMAND, 0x30, 2)
        self.expect_error(
            core.handle_frame(undefined), ErrorCode.UNSUPPORTED_COMMAND, command=0x30
        )

    def test_oversized_payload_is_rejected(self):
        core, _ = self.make_core(max_payload=64)
        self.negotiate(core)
        oversized = encode_frame(KIND_COMMAND, CMD_RESET, 2, b"\x00" * 65)
        self.expect_error(
            core.handle_frame(oversized), ErrorCode.PAYLOAD_LENGTH, command=CMD_RESET
        )


class PatchLoadTests(HarnessTestCase):
    def test_full_happy_path_round_trip(self):
        core, _ = self.make_core()
        self.negotiate(core)
        self.assertEqual(self.open_patch(core, 2, b"tx-1", count=2), self.success(CMD_PATCH_OPEN, 2))
        self.assertEqual(core.handle_frame(self.name_frame(3, "keyboard.midi_f0")), self.success(CMD_PATCH_NAME, 3))
        self.assertEqual(
            core.handle_frame(self.value_frame(4, "keyboard.midi_f0", b"\xfe\x00\x7f")),
            self.success(CMD_PATCH_VALUE, 4),
        )
        self.assertEqual(core.handle_frame(self.name_frame(5, "vco_1.level")), self.success(CMD_PATCH_NAME, 5))
        self.assertEqual(
            core.handle_frame(self.value_frame(6, "vco_1.level", b"\x01")),
            self.success(CMD_PATCH_VALUE, 6),
        )
        values = {"keyboard.midi_f0": b"\xfe\x00\x7f", "vco_1.level": b"\x01"}
        self.assertEqual(
            core.handle_frame(self.commit_frame(7, values)),
            self.success(CMD_PATCH_COMMIT, 7),
        )
        self.assertEqual(core.active_patch["values"], values)
        self.assertEqual(core.active_patch["sound_identity"], b"identity-1")
        self.assertIs(core.state, SessionState.READY)

    def test_opaque_values_are_preserved_verbatim(self):
        core, _ = self.make_core()
        self.negotiate(core)
        self.open_patch(core, 2)
        raw = bytes(range(256))
        core.handle_frame(self.name_frame(3, "lfo_1.rate"))
        core.handle_frame(self.value_frame(4, "lfo_1.rate", raw))
        commit = core.handle_frame(self.commit_frame(5, {"lfo_1.rate": raw}))
        self.assertEqual(commit, self.success(CMD_PATCH_COMMIT, 5))
        self.assertEqual(core.active_patch["values"]["lfo_1.rate"], raw)

    def test_hash_mismatch_rejects_and_discards(self):
        core, _ = self.make_core()
        self.negotiate(core)
        self.open_patch(core, 2)
        core.handle_frame(self.name_frame(3, "lfo_1.rate"))
        core.handle_frame(self.value_frame(4, "lfo_1.rate", b"\x01"))
        wrong_hash = encode_frame(
            KIND_COMMAND, CMD_PATCH_COMMIT, 5, encode_patch_commit(b"\x99" * 32)
        )
        self.expect_error(
            core.handle_frame(wrong_hash),
            ErrorCode.PATCH_HASH_MISMATCH,
            command=CMD_PATCH_COMMIT,
        )
        self.assertIs(core.state, SessionState.READY)
        self.assertIsNone(core.active_patch)

    def test_incomplete_commit_rejected(self):
        core, _ = self.make_core()
        self.negotiate(core)
        self.open_patch(core, 2, count=2)
        core.handle_frame(self.name_frame(3, "lfo_1.rate"))
        core.handle_frame(self.value_frame(4, "lfo_1.rate", b"\x01"))
        self.expect_error(
            core.handle_frame(self.commit_frame(5, {"lfo_1.rate": b"\x01"})),
            ErrorCode.PATCH_INCOMPLETE,
        )

    def test_unknown_name_and_unknown_table(self):
        core, _ = self.make_core()
        self.negotiate(core)
        self.expect_error(
            self.open_patch(core, 2, table_sha=b"\x22" * 32),
            ErrorCode.UNKNOWN_NAME,
            command=CMD_PATCH_OPEN,
        )
        self.assertEqual(self.open_patch(core, 3), self.success(CMD_PATCH_OPEN, 3))
        self.expect_error(
            core.handle_frame(self.name_frame(4, "not.a_param")),
            ErrorCode.UNKNOWN_NAME,
        )
        self.expect_error(
            core.handle_frame(self.value_frame(5, "not.a_param", b"\x01")),
            ErrorCode.UNKNOWN_NAME,
        )

    def test_duplicate_name_and_conflicting_value(self):
        core, _ = self.make_core()
        self.negotiate(core)
        self.open_patch(core, 2, count=2)
        core.handle_frame(self.name_frame(3, "lfo_1.rate"))
        self.expect_error(
            core.handle_frame(self.name_frame(4, "lfo_1.rate")),
            ErrorCode.DUPLICATE_NAME,
        )
        core.handle_frame(self.value_frame(5, "lfo_1.rate", b"\x01"))
        self.assertEqual(
            core.handle_frame(self.value_frame(6, "lfo_1.rate", b"\x01")),
            self.success(CMD_PATCH_VALUE, 6),
        )
        self.expect_error(
            core.handle_frame(self.value_frame(7, "lfo_1.rate", b"\x02")),
            ErrorCode.DUPLICATE_NAME,
        )

    def test_second_open_while_transaction_active(self):
        core, _ = self.make_core()
        self.negotiate(core)
        self.open_patch(core, 2)
        self.expect_error(
            self.open_patch(core, 3, transaction_id=b"tx-2"),
            ErrorCode.PATCH_TX_ACTIVE,
        )

    def test_commit_is_atomic_against_mixing(self):
        core, _ = self.make_core()
        self.negotiate(core)
        self.open_patch(core, 2)
        core.handle_frame(self.name_frame(3, "lfo_1.rate"))
        core.handle_frame(self.value_frame(4, "lfo_1.rate", b"\x01"))
        first_commit = self.commit_frame(5, {"lfo_1.rate": b"\x01"})
        self.assertEqual(core.handle_frame(first_commit), self.success(CMD_PATCH_COMMIT, 5))
        applied = dict(core.active_patch["values"])

        self.open_patch(core, 6, transaction_id=b"tx-2")
        core.handle_frame(self.name_frame(7, "lfo_1.rate"))
        core.handle_frame(self.value_frame(8, "lfo_1.rate", b"\x02"))
        self.expect_error(
            core.handle_frame(first_commit), ErrorCode.BAD_SEQUENCE, command=CMD_PATCH_COMMIT
        )
        self.assertEqual(core.active_patch["values"], applied)
        self.assertIs(core.state, SessionState.PATCH_OPEN)

    def test_abort_discards_staged_state_and_replays(self):
        core, _ = self.make_core()
        self.negotiate(core)
        self.open_patch(core, 2)
        core.handle_frame(self.name_frame(3, "lfo_1.rate"))
        abort = encode_frame(KIND_COMMAND, CMD_PATCH_ABORT, 4)
        self.assertEqual(core.handle_frame(abort), self.success(CMD_PATCH_ABORT, 4))
        self.assertIs(core.state, SessionState.READY)
        self.assertEqual(
            self.open_patch(core, 5, transaction_id=b"tx-2"),
            self.success(CMD_PATCH_OPEN, 5),
        )
        core.handle_frame(self.name_frame(6, "lfo_1.rate"))
        self.assertIsNone(core.active_patch)


class SessionSemanticsTests(HarnessTestCase):
    def test_reset_discards_only_in_flight_state(self):
        core, _ = self.make_core()
        self.negotiate(core)
        self.open_patch(core, 2)
        core.handle_frame(self.name_frame(3, "lfo_1.rate"))
        core.handle_frame(self.value_frame(4, "lfo_1.rate", b"\x01"))
        self.assertEqual(
            core.handle_frame(self.commit_frame(5, {"lfo_1.rate": b"\x01"})),
            self.success(CMD_PATCH_COMMIT, 5),
        )
        self.open_patch(core, 6, transaction_id=b"tx-2")
        core.handle_frame(self.name_frame(7, "lfo_1.rate"))
        self.assertEqual(
            core.handle_frame(encode_frame(KIND_COMMAND, CMD_RESET, 8)),
            self.success(CMD_RESET, 8),
        )
        self.assertIs(core.state, SessionState.READY)
        self.assertEqual(core.active_patch["values"]["lfo_1.rate"], b"\x01")
        self.expect_error(
            core.handle_frame(self.name_frame(9, "lfo_1.rate")),
            ErrorCode.BAD_SEQUENCE,
        )

    def test_reset_without_capability_is_unsupported(self):
        core, _ = self.make_core(capabilities=CAP_RESET)
        self.negotiate(core)
        self.assertEqual(
            core.handle_frame(encode_frame(KIND_COMMAND, CMD_RESET, 2)),
            self.success(CMD_RESET, 2),
        )
        self.expect_error(
            self.open_patch(core, 3),
            ErrorCode.UNSUPPORTED_COMMAND,
            command=CMD_PATCH_OPEN,
        )
        self.expect_error(
            core.handle_frame(self.name_frame(4, "lfo_1.rate")),
            ErrorCode.UNSUPPORTED_COMMAND,
        )

    def test_idempotent_retry_replays_identical_response(self):
        core, _ = self.make_core()
        self.negotiate(core)
        reset = encode_frame(KIND_COMMAND, CMD_RESET, 2)
        first = core.handle_frame(reset)
        second = core.handle_frame(reset)
        self.assertEqual(first, second)
        self.assertIs(core.state, SessionState.READY)

    def test_transaction_timeout_discards_and_requires_fresh_open(self):
        core, clock = self.make_core(patch_timeout_s=1.0)
        self.negotiate(core)
        self.open_patch(core, 2, transaction_id=b"tx-1")
        core.handle_frame(self.name_frame(3, "lfo_1.rate"))
        clock.now += 2.0
        self.expect_error(
            core.handle_frame(self.value_frame(4, "lfo_1.rate", b"\x01")),
            ErrorCode.TX_TIMEOUT,
        )
        self.assertIs(core.state, SessionState.READY)
        self.expect_error(
            self.open_patch(core, 5, transaction_id=b"tx-1"),
            ErrorCode.BAD_SEQUENCE,
            command=CMD_PATCH_OPEN,
        )
        self.assertEqual(
            self.open_patch(core, 6, transaction_id=b"tx-2"),
            self.success(CMD_PATCH_OPEN, 6),
        )
        self.assertIs(core.state, SessionState.PATCH_OPEN)

    def test_backpressure_answers_busy_and_retry_succeeds(self):
        core, _ = self.make_core(rx_queue_depth=1)
        self.negotiate(core)
        first = encode_frame(KIND_COMMAND, CMD_RESET, 2)
        second = encode_frame(KIND_COMMAND, CMD_RESET, 3)
        self.assertIsNone(core.submit(first))
        busy = decode_frame(core.submit(second))
        self.assertEqual(busy.kind, KIND_ERROR)
        self.assertEqual(busy.command, CMD_RESET)
        self.assertEqual(busy.sequence, 3)
        self.assertEqual(busy.payload, bytes([ErrorCode.BUSY]))
        self.assertEqual(core.poll(), self.success(CMD_RESET, 2))
        self.assertEqual(core.handle_frame(second), self.success(CMD_RESET, 3))

    def test_garbage_and_corrupted_frames_answer_bad_frame(self):
        core, _ = self.make_core()
        self.negotiate(core)
        self.expect_error(core.handle_frame(b"\x00\x01\x02"), ErrorCode.BAD_FRAME)
        corrupted = bytearray(self.hello(sequence=99))
        corrupted[4] ^= 0xFF
        response = decode_frame(core.handle_frame(bytes(corrupted)))
        self.assertEqual(response.payload, bytes([ErrorCode.BAD_FRAME]))
        self.assertIs(core.state, SessionState.READY)


class InventoryNameTableTests(HarnessTestCase):
    def test_inventory_names_load_and_round_trip(self):
        inventory = json.loads(INVENTORY_PATH.read_text())
        names = [entry["name"] for entry in inventory["parameters"]]
        self.assertEqual(len(names), inventory["parameter_count"])
        table_sha = hashlib.sha256(INVENTORY_PATH.read_bytes()).digest()
        core = MockCore(name_table=names, name_table_sha256=table_sha, clock=FakeClock())
        self.negotiate(core)
        target = "keyboard.midi_f0"
        self.assertEqual(
            core.handle_frame(
                encode_frame(
                    KIND_COMMAND,
                    CMD_PATCH_OPEN,
                    2,
                    encode_patch_open(b"tx-inv", b"identity", table_sha, 1),
                )
            ),
            self.success(CMD_PATCH_OPEN, 2),
        )
        core.handle_frame(self.name_frame(3, target))
        core.handle_frame(self.value_frame(4, target, b"\x00\x01"))
        self.assertEqual(
            core.handle_frame(self.commit_frame(5, {target: b"\x00\x01"})),
            self.success(CMD_PATCH_COMMIT, 5),
        )
        self.assertEqual(core.active_patch["values"], {target: b"\x00\x01"})

    def test_registry_has_no_note_or_streaming_commands(self):
        for name in COMMAND_NAMES.values():
            self.assertNotIn("NOTE", name)
            self.assertNotIn("STREAM", name)
            self.assertNotIn("AUDIO", name)


if __name__ == "__main__":
    unittest.main()
