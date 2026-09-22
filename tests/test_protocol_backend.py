"""Protocol-mock explorer backend tests (issue 66).

Proves the alternate backend integration: the render path drives the protocol
v2 client against the behavioral mock, the envelope shows backend and contract
identity, and repeat/save behavior is identical across the fake (Python) and
protocol-mock backends when identities match. Software-only: no hardware, RTL,
fidelity or playback claim is made or testable here.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.core_protocol import (  # noqa: E402
    decode_audio_payload,
    encode_param_word,
    numeric_contract_version_bound,
)
from torchsynth_voice.core_protocol_mock import MockCore  # noqa: E402
from torchsynth_voice.explorer import FakePlayer, build_session  # noqa: E402
from torchsynth_voice.explorer_session import ExplorerSession, SessionError  # noqa: E402
from torchsynth_voice.protocol_backend import (  # noqa: E402
    AUDIO_SOURCE_LABEL,
    BACKEND_LABEL,
    GOLDEN_AUDIO_VECTOR,
    ProtocolMockRenderer,
    backend_contract_identity,
    backend_envelope,
    build_mock_client,
    build_protocol_mock_session,
    name_table_reference,
)
from torchsynth_voice.protocol_client import (  # noqa: E402
    MockTransport,
    NegotiationError,
)
from torchsynth_voice.storage import ArtifactStore  # noqa: E402

BOUND = numeric_contract_version_bound()


class BackendWiringTests(unittest.TestCase):
    def test_name_table_reference_is_the_canonical_inventory(self):
        names, table_sha256 = name_table_reference()
        self.assertEqual(len(names), 78)
        self.assertEqual(len(set(names)), 78)
        self.assertEqual(len(table_sha256), 32)

    def test_mock_and_client_share_the_contract_identity(self):
        store_root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(_rmtree, store_root)
        _, probe = build_protocol_mock_session(store_root)
        identity = backend_contract_identity()
        self.assertEqual(identity["protocol_version"], 2)
        self.assertEqual(identity["numeric_contract_version"], BOUND.hex())
        self.assertEqual(identity["kind"], "behavioral-mock-software-only")
        self.assertEqual(identity["hardware_claim"], "none")
        self.assertEqual(probe["contract"], identity)


class RenderPathProtocolTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(_rmtree, self.root)
        self.session, self.probe = build_protocol_mock_session(self.root / "store")

    def test_render_drives_full_protocol_transaction(self):
        index = self.session.allowed_indices[0]
        selection = self.session.request(index)
        mock = self.probe["mock"]
        client = self.probe["renderer"].client
        self.assertIsNotNone(client)
        self.assertEqual(client.state, "ready")
        self.assertIsNotNone(mock.active_patch)
        self.assertEqual(mock.active_patch["transaction_id"][0:16], mock.active_patch["transaction_id"])
        inputs = selection.inputs
        expected = {
            name: encode_param_word(value)
            for name, value in inputs["parameters"]["physical_by_name"].items()
        }
        self.assertEqual(mock.active_patch["values"], expected)
        self.assertEqual(mock.active_patch["sound_identity"], selection.stored.artifact_id.encode())
        self.assertEqual(len(mock.active_patch["values"]), 78)
        # The negotiation used the bound contract and full known capabilities.
        self.assertEqual(client.numeric_contract_version, BOUND)
        self.assertEqual(self.probe["renderer"].calls, [index])

    def test_envelope_shows_backend_and_contract_identity(self):
        index = self.session.allowed_indices[0]
        self.session.request(index)
        envelope = backend_envelope(self.session, self.probe)
        self.assertEqual(envelope["backend"], BACKEND_LABEL)
        self.assertEqual(envelope["contract"], backend_contract_identity())
        mock = self.probe["mock"]
        payload = mock.audio_payload_bytes
        self.assertEqual(
            envelope["audio"]["payload_sha256"], hashlib.sha256(payload).hexdigest()
        )
        self.assertEqual(envelope["audio"]["sample_count"], len(GOLDEN_AUDIO_VECTOR))
        self.assertEqual(
            envelope["audio"]["decoded_sample_count"],
            len(decode_audio_payload(payload)),
        )
        self.assertEqual(envelope["audio"]["source"], AUDIO_SOURCE_LABEL)
        shown = envelope["session"]
        self.assertEqual(shown["sound_index"], index)
        self.assertEqual(shown["runtime_qualification"], "not-established")

    def test_mock_audio_source_is_the_fixed_golden_vector(self):
        mock = self.probe["mock"]
        self.assertEqual(mock.audio_source_samples, tuple(GOLDEN_AUDIO_VECTOR))
        self.assertEqual(len(mock.audio_payload_bytes), 3 * len(GOLDEN_AUDIO_VECTOR))

    def test_contract_mismatch_fails_the_render_and_keeps_selection(self):
        store = ArtifactStore(self.root / "store-mismatch")
        names, table_sha256 = name_table_reference()
        core = MockCore(
            name_table=names,
            name_table_sha256=table_sha256,
            numeric_contract_version=b"\x00" * 32,
        )
        renderer = ProtocolMockRenderer(store, core=core, transport=MockTransport(core))
        session = ExplorerSession(store, renderer=renderer, player=FakePlayer())
        index = session.allowed_indices[0]
        with self.assertRaises(SessionError):
            session.request(index)
        self.assertIsNone(session.selection)
        self.assertIsNotNone(renderer.client)
        self.assertEqual(renderer.client.state, "closed")
        with self.assertRaises(NegotiationError):
            build_mock_client(
                core,
                MockTransport(core),
                name_table_sha256=table_sha256,
            ).negotiate()


class BackendParityTests(unittest.TestCase):
    def test_repeat_and_save_identical_across_fake_and_protocol_backends(self):
        root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(_rmtree, root)
        fake_session, _ = build_session(root / "store-fake", backend="fake")
        index = fake_session.allowed_indices[0]
        fake_selection = fake_session.request(index)
        fake_session.save(root / "bookmark-fake.json")

        proto_session, proto_probe = build_protocol_mock_session(root / "store-proto")
        proto_selection = proto_session.request(index)
        proto_session.save(root / "bookmark-proto.json")

        self.assertEqual(
            fake_selection.stored.reference, proto_selection.stored.reference
        )
        self.assertEqual(
            (root / "bookmark-fake.json").read_bytes(),
            (root / "bookmark-proto.json").read_bytes(),
        )
        self.assertEqual(
            fake_session.repeat().stored.reference,
            proto_session.repeat().stored.reference,
        )
        self.assertEqual(
            fake_session.show()["reference"], proto_session.show()["reference"]
        )
        del proto_probe


def _rmtree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
