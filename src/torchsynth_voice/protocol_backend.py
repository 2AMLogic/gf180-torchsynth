"""Alternate explorer backend: protocol v2 client against the behavioral mock.

Routes the explorer render path through the host transport client
(:mod:`torchsynth_voice.protocol_client`) negotiating with the behavioral
hardware mock (:class:`torchsynth_voice.core_protocol_mock.MockCore`) per
issue #66: HELLO/READY negotiation, a full name-keyed patch-load transaction
over the artifact's physical parameter values, and audio sourced from fixed
golden vectors at the mock boundary.

This is the software lane. It adds no transport backend to the landed
explorer MVP CLI (spec/EXPLORER-MVP.md exclusions stand) and makes no
hardware, RTL, synthesis-fidelity or playback claim: the mock qualifies
protocol behavior only (spec/protocol/MOCK-HARNESS.md), and the audio
transfer/streaming command set remains unallocated pending issue #63.
"""

from __future__ import annotations

import hashlib
import struct
from fractions import Fraction
from pathlib import Path

from .core_protocol import (
    KNOWN_CAPABILITIES,
    PROTOCOL_VERSION,
    decode_audio_payload,
    encode_param_word,
    numeric_contract_version_bound,
)
from .core_protocol_mock import MockCore
from .explorer import FakePlayer, publish_fake_artifact
from .explorer_session import ExplorerSession
from .inventory import INVENTORY_PATH, load_json
from .protocol_client import HostClient, MockTransport, Transport

BACKEND_LABEL = "protocol-mock-v2-behavioral-synthetic-audio"
AUDIO_SOURCE_LABEL = "fixed-golden-vectors-behavioral-mock"

# Fixed golden audio vectors: exact rational host values within the C1 range
# [-4, +4), committed here so mock sourcing is pinned, not generated. This is
# a software data source; it is not RTL output and claims no fidelity.
GOLDEN_AUDIO_VECTOR = (
    Fraction(0),
    Fraction(1, 4),
    Fraction(-1, 4),
    Fraction(1),
    Fraction(-1),
    Fraction(5, 2),
    Fraction(-5, 2),
    Fraction(4382797, 1 << 21),
    Fraction(-4194304, 1 << 21),
    Fraction(1, 1 << 21),
    Fraction(-1, 1 << 21),
    Fraction(2),
)


def name_table_reference() -> tuple[list[str], bytes]:
    """The canonical name set and the SHA-256 of the inventory document."""
    inventory = load_json(INVENTORY_PATH)
    names = [row["name"] for row in inventory["parameters"]]
    return names, hashlib.sha256(INVENTORY_PATH.read_bytes()).digest()


def build_mock_core(*, audio_source=GOLDEN_AUDIO_VECTOR) -> MockCore:
    """A mock core configured for the canonical table and bound contract."""
    names, table_sha256 = name_table_reference()
    return MockCore(
        name_table=names,
        name_table_sha256=table_sha256,
        capabilities=KNOWN_CAPABILITIES,
        audio_source=audio_source,
    )


def build_mock_client(
    core: MockCore, transport: Transport, **kwargs
) -> HostClient:
    """A host client bound to the same contract identity as the mock."""
    _, table_sha256 = name_table_reference()
    defaults = dict(
        profile_id=b"torchsynth-1-voice-default",
        source_version=b"2b0964d4c6c3d472a2a0d54d91b408caaeffca6d",
        name_table_sha256=table_sha256,
        numeric_contract_version=numeric_contract_version_bound(),
        capabilities=KNOWN_CAPABILITIES,
        expected_profile=b"torchsynth-1-voice-default",
    )
    defaults.update(kwargs)
    return HostClient(transport, **defaults)


def _transaction_id(sound_index: int) -> bytes:
    digest = hashlib.sha256(struct.pack("<Q", sound_index)).digest()
    return digest[:16]


class ProtocolMockRenderer:
    """Renders by driving the protocol path; publishes the same fixture bytes.

    The published artifact is the deterministic synthetic fixture of the
    landed fake mode; the protocol round trip is an additional verification
    the backend performs, not a different artifact. Any protocol failure —
    negotiation refusal, hash mismatch, state violation — fails the render
    and never replaces a previous valid selection.
    """

    def __init__(self, store, *, core: MockCore, transport: Transport):
        self.store = store
        self.core = core
        self.transport = transport
        self.client: HostClient | None = None
        self.calls: list[int] = []

    def __call__(self, sound_index: int) -> dict[str, str]:
        from .artifacts import loads
        from .storage import METADATA

        self.calls.append(sound_index)
        stored = publish_fake_artifact(self.store, sound_index)
        inputs = loads((stored.path / METADATA).read_bytes())["inputs"]["value"]
        self._drive_protocol(sound_index, inputs, stored.artifact_id)
        return dict(stored.reference)

    def _drive_protocol(self, sound_index: int, inputs: dict, artifact_id: str):
        names, _ = name_table_reference()
        physical = inputs["parameters"]["physical_by_name"]
        values = {name: encode_param_word(physical[name]) for name in names}
        client = build_mock_client(self.core, self.transport)
        self.client = client
        client.negotiate()
        client.open_patch(
            _transaction_id(sound_index),
            artifact_id.encode("utf-8"),
            len(values),
        )
        for name in names:
            client.declare_name(name)
        for name, word in values.items():
            client.stage_value(name, word)
        client.commit_patch(values)
        client.reset()


def build_protocol_mock_session(store_root, *, seed: int | None = None):
    """Wire the protocol-mock backend; returns (session, probe).

    The probe carries the client, mock and transport doubles for test and
    diagnostic inspection, plus the contract identity the backend negotiated.
    """
    import random

    from .storage import ArtifactStore

    store = ArtifactStore(store_root)
    core = build_mock_core()
    transport = MockTransport(core)
    renderer = ProtocolMockRenderer(store, core=core, transport=transport)
    player = FakePlayer()
    rng = random.Random() if seed is None else random.Random(seed)
    session = ExplorerSession(store, renderer=renderer, player=player, rng=rng)
    probe = {
        "store": store,
        "backend": "protocol-mock",
        "renderer": renderer,
        "player": player,
        "mock": core,
        "transport": transport,
        "contract": backend_contract_identity(),
    }
    return session, probe


def backend_contract_identity() -> dict:
    """The contract identity this backend speaks (issue #66 display AC)."""
    _, table_sha256 = name_table_reference()
    return {
        "protocol_version": PROTOCOL_VERSION,
        "numeric_contract_version": numeric_contract_version_bound().hex(),
        "name_table_sha256": table_sha256.hex(),
        "kind": "behavioral-mock-software-only",
        "hardware_claim": "none",
    }


def backend_envelope(session: ExplorerSession, probe: dict) -> dict:
    """One honest envelope: backend label, contract identity, verified show()."""
    core: MockCore = probe["mock"]
    payload = core.audio_payload_bytes
    return {
        "backend": BACKEND_LABEL,
        "contract": probe["contract"],
        "session": session.show(),
        "audio": {
            "source": AUDIO_SOURCE_LABEL,
            "sample_count": len(core.audio_source_samples),
            "payload_sha256": hashlib.sha256(payload).hexdigest(),
            "decoded_sample_count": len(decode_audio_payload(payload)),
            "transport_command": "unallocated (issue #63); mock-boundary data path",
        },
    }
