"""Render trigger + host-fed noise stream: the DR-0010 pass/digest binding.

Verifies spec/protocol/RENDER-TRIGGER.md (issue #188) against the behavioral
mock core and the host client. Scope honesty: the core binds the
TRANSPORT-DECLARED noise-stream digest; it never hashes received bytes (open
question, issue #207). These are software-lane protocol tests only — no RTL,
synthesis, hardware, or sound-fidelity claim.
"""

from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_core_protocol_mock import HarnessTestCase  # noqa: E402

from torchsynth_voice.core_protocol import (  # noqa: E402
    CAP_NAME_KEYED_PATCH_LOAD,
    CAP_RENDER,
    CAP_RESET,
    CMD_NOISE_STREAM,
    CMD_PATCH_ABORT,
    CMD_PATCH_OPEN,
    CMD_RENDER_TRIGGER,
    CMD_RESET,
    ErrorCode,
    Frame,
    KIND_COMMAND,
    KNOWN_CAPABILITIES,
    PROTOCOL_VERSION,
    NOISE_STREAM_CLIP_BYTES,
    encode_frame,
    encode_noise_stream,
    encode_param_word,
    encode_render_trigger,
)
from torchsynth_voice.core_protocol_mock import MockCore, SessionState  # noqa: E402
from torchsynth_voice.protocol_client import (  # noqa: E402
    ClientStateError,
    HostClient,
    MockTransport,
    ProtocolError,
)

IDENTITY = b"identity-1"
OTHER_IDENTITY = b"identity-2"
CLIP = 64  # test-scale per-pass stream length (MockCore knob only)
NOISE = bytes((i * 37 + 11) & 0xFF for i in range(CLIP))
DIGEST = hashlib.sha256(NOISE).digest()
OTHER_DIGEST = hashlib.sha256(NOISE[::-1]).digest()
NAME = "vco_1.level"
VALUE = encode_param_word(0.5)


class RenderHarness(HarnessTestCase):
    """Drives the mock core frame by frame with a monotonic sequence."""

    def setUp(self):
        self.seq = 1

    def next_seq(self):
        self.seq += 1
        return self.seq

    def ready_core(self, *, capabilities=KNOWN_CAPABILITIES, commit=True, **kwargs):
        kwargs.setdefault("noise_stream_bytes", CLIP)
        core, _ = self.make_core(**kwargs)
        self.negotiate(core, sequence=1, capabilities=capabilities)
        if commit:
            self.assertEqual(
                self.open_patch(core, self.next_seq()),
                self.success(CMD_PATCH_OPEN, self.seq),
            )
            core.handle_frame(self.name_frame(self.next_seq(), NAME))
            core.handle_frame(self.value_frame(self.next_seq(), NAME, VALUE))
            core.handle_frame(self.commit_frame(self.next_seq(), {NAME: VALUE}))
            self.assertEqual(core.active_patch["sound_identity"], IDENTITY)
        return core

    def trigger(self, core, pass_index, identity=IDENTITY, digest=DIGEST):
        return core.handle_frame(
            encode_frame(
                KIND_COMMAND,
                CMD_RENDER_TRIGGER,
                self.next_seq(),
                encode_render_trigger(pass_index, identity, digest),
            )
        )

    def noise(self, core, pass_index, offset, data):
        return core.handle_frame(
            encode_frame(
                KIND_COMMAND,
                CMD_NOISE_STREAM,
                self.next_seq(),
                encode_noise_stream(pass_index, offset, data),
            )
        )

    def ok(self, response, command):
        self.assertEqual(response, self.success(command, self.seq))

    def feed_pass(self, core, pass_index, data=NOISE, step=16):
        for offset in range(0, len(data), step):
            self.ok(
                self.noise(core, pass_index, offset, data[offset : offset + step]),
                CMD_NOISE_STREAM,
            )

    def pass1_done(self, core):
        self.ok(self.trigger(core, 1), CMD_RENDER_TRIGGER)
        self.assertIs(core.state, SessionState.RENDERING)
        self.feed_pass(core, 1)
        self.assertTrue(core.render["pass1_complete"])


class CapabilityTests(RenderHarness):
    def test_render_commands_refused_when_not_granted(self):
        core = self.ready_core(capabilities=CAP_NAME_KEYED_PATCH_LOAD | CAP_RESET)
        self.expect_error(
            self.trigger(core, 1), ErrorCode.UNSUPPORTED_COMMAND, command=CMD_RENDER_TRIGGER
        )
        self.expect_error(
            self.noise(core, 1, 0, b"x"), ErrorCode.UNSUPPORTED_COMMAND, command=CMD_NOISE_STREAM
        )
        self.assertIs(core.state, SessionState.READY)
        self.assertIsNone(core.render)

    def test_core_not_supporting_render_never_grants_it(self):
        core, _ = self.make_core(capabilities=CAP_NAME_KEYED_PATCH_LOAD | CAP_RESET)
        ready = self.negotiate(core, capabilities=KNOWN_CAPABILITIES)
        self.assertFalse(ready.capabilities & CAP_RENDER)

    def test_profile_clip_length_is_the_default(self):
        core, _ = self.make_core()
        self.assertEqual(core._noise_stream_bytes, NOISE_STREAM_CLIP_BYTES)


class BindingTests(RenderHarness):
    def test_clean_two_pass_clip_completes_under_one_binding(self):
        core = self.ready_core()
        self.pass1_done(core)
        self.ok(self.trigger(core, 2), CMD_RENDER_TRIGGER)
        self.feed_pass(core, 2)
        self.assertIs(core.state, SessionState.READY)
        self.assertIsNone(core.render)
        self.assertEqual(
            core.completed_clips,
            [{"sound_identity": IDENTITY, "noise_stream_sha256": DIGEST}],
        )
        self.assertEqual(core.discarded_clips, [])

    def test_pass2_digest_mismatch_discards_clip_entire(self):
        # DR-0010: "a pass-2 digest mismatch discarding the clip entire".
        core = self.ready_core()
        self.pass1_done(core)
        self.expect_error(
            self.trigger(core, 2, digest=OTHER_DIGEST),
            ErrorCode.RENDER_BINDING,
            command=CMD_RENDER_TRIGGER,
        )
        self.assertIs(core.state, SessionState.READY)
        self.assertIsNone(core.render)
        self.assertEqual(core.completed_clips, [])
        self.assertEqual(
            core.discarded_clips,
            [
                (
                    {"sound_identity": IDENTITY, "noise_stream_sha256": DIGEST},
                    ErrorCode.RENDER_BINDING,
                )
            ],
        )
        # Detected before any pass-2 byte was accepted: the stream of the
        # discarded clip cannot continue, and no pass-2 chunk is taken.
        self.expect_error(
            self.noise(core, 2, 0, NOISE[:16]), ErrorCode.BAD_SEQUENCE, command=CMD_NOISE_STREAM
        )

    def test_pass2_identity_mismatch_discards_clip_entire(self):
        core = self.ready_core()
        self.pass1_done(core)
        self.expect_error(
            self.trigger(core, 2, identity=OTHER_IDENTITY),
            ErrorCode.RENDER_BINDING,
            command=CMD_RENDER_TRIGGER,
        )
        self.assertIs(core.state, SessionState.READY)
        self.assertEqual(core.completed_clips, [])
        self.assertEqual(core.discarded_clips[0][1], ErrorCode.RENDER_BINDING)

    def test_mismatch_negative_control_differs_only_in_the_digest(self):
        # Negative control: the identical sequence with the matching digest
        # completes, so the discard above is caused by the digest alone.
        for digest, completes in ((DIGEST, True), (OTHER_DIGEST, False)):
            with self.subTest(match=completes):
                self.setUp()
                core = self.ready_core()
                self.pass1_done(core)
                self.trigger(core, 2, digest=digest)
                if completes:
                    self.feed_pass(core, 2)
                self.assertEqual(len(core.completed_clips), 1 if completes else 0)
                self.assertEqual(len(core.discarded_clips), 0 if completes else 1)

    def test_recovery_after_discard_needs_a_fresh_pass1(self):
        core = self.ready_core()
        self.pass1_done(core)
        self.trigger(core, 2, digest=OTHER_DIGEST)
        # A bare pass-2 retry cannot resume the discarded clip.
        self.expect_error(self.trigger(core, 2), ErrorCode.BAD_SEQUENCE)
        # A fresh trigger renders cleanly from pass 1.
        self.pass1_done(core)
        self.ok(self.trigger(core, 2), CMD_RENDER_TRIGGER)
        self.feed_pass(core, 2)
        self.assertEqual(len(core.completed_clips), 1)
        self.assertEqual(len(core.discarded_clips), 1)

    def test_pass1_identity_must_match_active_patch(self):
        core = self.ready_core()
        self.expect_error(
            self.trigger(core, 1, identity=OTHER_IDENTITY), ErrorCode.RENDER_BINDING
        )
        self.assertIs(core.state, SessionState.READY)
        self.assertIsNone(core.render)
        self.assertEqual(core.discarded_clips, [])  # no clip existed

    def test_pass1_without_committed_patch_is_bad_state(self):
        core = self.ready_core(commit=False)
        self.expect_error(self.trigger(core, 1), ErrorCode.BAD_STATE)
        self.assertIsNone(core.render)

    def test_pass2_without_open_render_is_bad_sequence(self):
        core = self.ready_core()
        self.expect_error(self.trigger(core, 2), ErrorCode.BAD_SEQUENCE)

    def test_second_pass1_never_joins_or_replaces_open_clip(self):
        core = self.ready_core()
        self.ok(self.trigger(core, 1), CMD_RENDER_TRIGGER)
        self.expect_error(self.trigger(core, 1, digest=OTHER_DIGEST), ErrorCode.BAD_STATE)
        self.assertEqual(core.render["noise_stream_sha256"], DIGEST)
        self.assertIs(core.state, SessionState.RENDERING)

    def test_malformed_trigger_changes_nothing(self):
        core = self.ready_core()
        self.pass1_done(core)
        bad = encode_frame(
            KIND_COMMAND,
            CMD_RENDER_TRIGGER,
            self.next_seq(),
            b"\x02" + b"\x0a\x00" + IDENTITY + b"\x1f\x00" + DIGEST[:31],
        )
        self.expect_error(core.handle_frame(bad), ErrorCode.PAYLOAD_LENGTH)
        self.assertIs(core.state, SessionState.RENDERING)
        self.assertTrue(core.render["pass1_complete"])


class NoiseFramingTests(RenderHarness):
    def test_pass2_before_pass1_complete_discards(self):
        core = self.ready_core()
        self.ok(self.trigger(core, 1), CMD_RENDER_TRIGGER)
        self.ok(self.noise(core, 1, 0, NOISE[:16]), CMD_NOISE_STREAM)
        self.expect_error(self.trigger(core, 2), ErrorCode.NOISE_STREAM)
        self.assertIs(core.state, SessionState.READY)
        self.assertEqual(core.discarded_clips[0][1], ErrorCode.NOISE_STREAM)

    def test_non_contiguous_offset_discards(self):
        core = self.ready_core()
        self.ok(self.trigger(core, 1), CMD_RENDER_TRIGGER)
        self.ok(self.noise(core, 1, 0, NOISE[:16]), CMD_NOISE_STREAM)
        self.expect_error(self.noise(core, 1, 32, NOISE[32:48]), ErrorCode.NOISE_STREAM)
        self.assertIs(core.state, SessionState.READY)
        self.assertIsNone(core.render)

    def test_wrong_pass_chunk_discards(self):
        core = self.ready_core()
        self.pass1_done(core)
        self.ok(self.trigger(core, 2), CMD_RENDER_TRIGGER)
        self.expect_error(self.noise(core, 1, 0, NOISE[:16]), ErrorCode.NOISE_STREAM)
        self.assertEqual(core.completed_clips, [])

    def test_overrun_past_clip_length_discards(self):
        core = self.ready_core()
        self.ok(self.trigger(core, 1), CMD_RENDER_TRIGGER)
        self.expect_error(self.noise(core, 1, 0, NOISE + b"\x00"), ErrorCode.NOISE_STREAM)
        core2 = self.ready_core()
        self.pass1_done(core2)
        self.expect_error(self.noise(core2, 1, CLIP, b"\x00"), ErrorCode.NOISE_STREAM)

    def test_mid_pass2_fault_discards_clip_entire(self):
        core = self.ready_core()
        self.pass1_done(core)
        self.ok(self.trigger(core, 2), CMD_RENDER_TRIGGER)
        self.ok(self.noise(core, 2, 0, NOISE[:16]), CMD_NOISE_STREAM)
        self.expect_error(self.noise(core, 2, 0, NOISE[:16]), ErrorCode.NOISE_STREAM)
        self.assertEqual(core.completed_clips, [])
        self.assertEqual(core.discarded_clips[0][1], ErrorCode.NOISE_STREAM)

    def test_noise_without_render_is_bad_sequence(self):
        core = self.ready_core()
        self.expect_error(self.noise(core, 1, 0, b"x"), ErrorCode.BAD_SEQUENCE)


class SessionInteractionTests(RenderHarness):
    def test_reset_discards_open_render(self):
        core = self.ready_core()
        self.pass1_done(core)
        core.handle_frame(encode_frame(KIND_COMMAND, CMD_RESET, self.next_seq()))
        self.assertIs(core.state, SessionState.READY)
        self.assertIsNone(core.render)
        self.assertEqual(core.discarded_clips[0][1], "reset")
        self.expect_error(self.trigger(core, 2), ErrorCode.BAD_SEQUENCE)

    def test_hello_discards_open_render(self):
        core = self.ready_core()
        self.ok(self.trigger(core, 1), CMD_RENDER_TRIGGER)
        self.negotiate(core, sequence=self.next_seq())
        self.assertIsNone(core.render)
        self.assertEqual(core.discarded_clips[0][1], "hello")

    def test_patch_frames_do_not_interleave_with_render(self):
        core = self.ready_core()
        self.ok(self.trigger(core, 1), CMD_RENDER_TRIGGER)
        self.expect_error(
            self.open_patch(core, self.next_seq(), transaction_id=b"tx-2"),
            ErrorCode.BAD_STATE,
        )
        self.expect_error(
            core.handle_frame(encode_frame(KIND_COMMAND, CMD_PATCH_ABORT, self.next_seq())),
            ErrorCode.BAD_STATE,
        )
        self.assertIs(core.state, SessionState.RENDERING)
        # And render frames are refused inside a patch transaction.
        core2 = self.ready_core()
        self.open_patch(core2, self.next_seq(), transaction_id=b"tx-3")
        self.expect_error(self.trigger(core2, 1), ErrorCode.BAD_STATE)


class HostClientRenderTests(unittest.TestCase):
    def make(self, *, clip=CLIP, max_frame_bytes=256, max_payload=1024):
        core = MockCore(
            name_table={NAME},
            name_table_sha256=b"\x11" * 32,
            noise_stream_bytes=clip,
            max_payload=max_payload,
        )
        transport = MockTransport(core, max_frame_bytes=max_frame_bytes)
        client = HostClient(
            transport,
            profile_id=b"profile-x",
            source_version=b"source-x",
            name_table_sha256=b"\x11" * 32,
            capabilities=KNOWN_CAPABILITIES,
        )
        client.negotiate()
        client.open_patch(b"tx-1", IDENTITY, 1)
        client.declare_name(NAME)
        client.stage_value(NAME, VALUE)
        client.commit_patch({NAME: VALUE})
        return core, transport, client

    def test_render_clip_declares_digest_of_the_bytes_it_sends(self):
        core, transport, client = self.make()
        digest = client.render_clip(IDENTITY, NOISE)
        self.assertEqual(digest, DIGEST)
        self.assertEqual(client.state, "ready")
        self.assertEqual(
            core.completed_clips,
            [{"sound_identity": IDENTITY, "noise_stream_sha256": DIGEST}],
        )
        # Both passes carried the identical bytes, reassembled per pass.
        chunks = {1: bytearray(), 2: bytearray()}
        triggers = []
        from torchsynth_voice.core_protocol import (
            decode_frame,
            decode_noise_stream,
            decode_render_trigger,
        )

        for raw in transport.sent_frames:
            frame = decode_frame(raw)
            if frame.command == CMD_NOISE_STREAM:
                chunk = decode_noise_stream(frame.payload)
                chunks[chunk.pass_index] += chunk.data
            elif frame.command == CMD_RENDER_TRIGGER:
                triggers.append(decode_render_trigger(frame.payload))
        self.assertEqual(bytes(chunks[1]), NOISE)
        self.assertEqual(bytes(chunks[2]), NOISE)
        self.assertEqual([t.pass_index for t in triggers], [1, 2])
        self.assertEqual({t.noise_stream_sha256 for t in triggers}, {DIGEST})
        self.assertEqual({t.sound_identity for t in triggers}, {IDENTITY})

    def test_chunks_respect_transport_frame_limit(self):
        noise = bytes(range(256)) * 3  # 768 B: several chunks per pass
        core, transport, client = self.make(clip=len(noise), max_frame_bytes=128)
        client.render_clip(IDENTITY, noise)
        self.assertTrue(all(len(f) <= 128 for f in transport.sent_frames))
        noise_frames = [f for f in transport.sent_frames if f[4] == CMD_NOISE_STREAM]
        self.assertGreater(len(noise_frames), 2 * 6)
        self.assertEqual(len(core.completed_clips), 1)

    def test_pass2_digest_mismatch_raises_and_client_returns_to_ready(self):
        core, _, client = self.make()
        with self.assertRaises(ProtocolError) as caught:
            client.render_clip(IDENTITY, NOISE, pass2_noise_stream_sha256=OTHER_DIGEST)
        self.assertIs(caught.exception.code, ErrorCode.RENDER_BINDING)
        self.assertEqual(client.state, "ready")
        self.assertEqual(core.completed_clips, [])
        self.assertEqual(len(core.discarded_clips), 1)
        # The session survives: a clean clip renders next.
        client.render_clip(IDENTITY, NOISE)
        self.assertEqual(len(core.completed_clips), 1)

    def test_wrong_length_stream_is_discarded(self):
        core, _, client = self.make()
        with self.assertRaises(ProtocolError) as caught:
            client.render_clip(IDENTITY, NOISE[:-1])
        self.assertIs(caught.exception.code, ErrorCode.NOISE_STREAM)
        self.assertEqual(client.state, "ready")
        self.assertEqual(core.completed_clips, [])

    def test_render_refused_inside_patch_transaction(self):
        _, _, client = self.make()
        client.open_patch(b"tx-2", IDENTITY, 1)
        with self.assertRaises(ClientStateError):
            client.render_clip(IDENTITY, NOISE)


class ProfileLengthTests(RenderHarness):
    """The mock's render lifecycle at the product-profile stream length.

    Triggers travel as full frames. The 705,600 B of noise per pass are
    delivered to the mock's NOISE_STREAM handler as decoded frames: pushing
    2 x 705,600 B through the bit-serial pure-Python CRC-16 of every frame
    costs minutes per run and exercises nothing the framed, test-scale
    tests above do not already exercise (CRC/framing is covered by
    test_core_protocol). Everything past frame decode — payload decode,
    pass/offset/length checks, completion — is the real handler.
    """

    CHUNK = 65_000

    def deliver(self, core, pass_index, offset, data):
        frame = Frame(
            PROTOCOL_VERSION,
            KIND_COMMAND,
            CMD_NOISE_STREAM,
            self.next_seq(),
            encode_noise_stream(pass_index, offset, data),
        )
        return core._handle_noise_stream(frame)

    def feed_profile_pass(self, core, pass_index, stream):
        for offset in range(0, len(stream), self.CHUNK):
            self.ok(
                self.deliver(core, pass_index, offset, stream[offset : offset + self.CHUNK]),
                CMD_NOISE_STREAM,
            )

    def test_profile_length_clip_completes_only_at_705600_bytes(self):
        stream = hashlib.sha256(b"seed").digest() * (NOISE_STREAM_CLIP_BYTES // 32)
        self.assertEqual(len(stream), NOISE_STREAM_CLIP_BYTES)
        digest = hashlib.sha256(stream).digest()
        core = self.ready_core(noise_stream_bytes=NOISE_STREAM_CLIP_BYTES)
        self.ok(self.trigger(core, 1, digest=digest), CMD_RENDER_TRIGGER)
        # One byte short of the profile length: pass 1 is not complete, so
        # a pass-2 trigger here would discard; check the state instead.
        self.feed_profile_pass(core, 1, stream[:-1])
        self.assertFalse(core.render["pass1_complete"])
        self.ok(self.deliver(core, 1, len(stream) - 1, stream[-1:]), CMD_NOISE_STREAM)
        self.assertTrue(core.render["pass1_complete"])
        self.ok(self.trigger(core, 2, digest=digest), CMD_RENDER_TRIGGER)
        self.feed_profile_pass(core, 2, stream)
        self.assertEqual(
            core.completed_clips,
            [{"sound_identity": IDENTITY, "noise_stream_sha256": digest}],
        )
        self.assertIs(core.state, SessionState.READY)

    def test_profile_length_overrun_discards(self):
        stream = bytes(NOISE_STREAM_CLIP_BYTES + 1)
        core = self.ready_core(noise_stream_bytes=NOISE_STREAM_CLIP_BYTES)
        self.ok(self.trigger(core, 1), CMD_RENDER_TRIGGER)
        self.feed_profile_pass(core, 1, stream[:NOISE_STREAM_CLIP_BYTES])
        self.expect_error(
            self.deliver(core, 1, NOISE_STREAM_CLIP_BYTES, b"\x00"),
            ErrorCode.NOISE_STREAM,
            command=CMD_NOISE_STREAM,
        )
        self.assertEqual(core.completed_clips, [])


if __name__ == "__main__":
    unittest.main()
