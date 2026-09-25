"""Protocol-mock explorer backend tests (issue 66).

Proves the alternate backend integration: the render path drives the protocol
v2 client against the behavioral mock, the envelope shows backend and contract
identity, repeat/save behavior is identical across the fake (Python) and
protocol-mock backends when identities match, and the **product model is
invariant to which transport binding carries the frames** — the same session
over loopback/UART/SPI/USB publishes the same artifact, the same bookmark
bytes and the same contract identity, at the library seam and through the
shipped CLI. Software-only: no hardware, RTL, fidelity or playback claim is
made or testable here, and no physical driver is exercised.
"""

from __future__ import annotations

import hashlib
import json
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
from torchsynth_voice.explorer import (  # noqa: E402
    FakePlayer,
    build_session,
)
from torchsynth_voice.explorer_session import ExplorerSession, SessionError  # noqa: E402
from torchsynth_voice.protocol_backend import (  # noqa: E402
    AUDIO_SOURCE_LABEL,
    BACKEND_LABEL,
    DEFAULT_TRANSPORT_BINDING,
    GOLDEN_AUDIO_VECTOR,
    TRANSPORT_BINDINGS,
    TRANSPORT_MAX_FRAME_BYTES,
    ProtocolMockRenderer,
    backend_contract_identity,
    backend_envelope,
    build_mock_client,
    build_mock_core,
    build_protocol_mock_session,
    build_transport,
    name_table_reference,
    transport_binding_identity,
)
from torchsynth_voice.protocol_client import (  # noqa: E402
    MockTransport,
    NegotiationError,
)
from torchsynth_voice.storage import ArtifactStore  # noqa: E402
from torchsynth_voice.transport_binding_models import (  # noqa: E402
    SpiBindingTransport,
    UartBindingTransport,
    UsbBindingTransport,
)

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

    def test_build_session_exposes_the_protocol_mock_backend(self):
        """The explorer's own wiring seam selects this backend by name."""
        store_root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(_rmtree, store_root)
        session, probe = build_session(store_root, backend="protocol-mock", seed=2)
        self.assertEqual(probe["backend"], "protocol-mock")
        self.assertEqual(probe["contract"], backend_contract_identity())
        self.assertIs(probe["session"], session)
        self.assertIsInstance(probe["renderer"], ProtocolMockRenderer)
        with self.assertRaises(SessionError):
            build_session(store_root, backend="protocol-mocked")


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


class TransportSubstitutabilityTests(unittest.TestCase):
    """AC 5 at the product model: swapping the binding changes nothing visible.

    `tests/test_transport_bindings.py` proves the client/core stack is
    invariant to the binding. This class proves the claim the acceptance
    criterion actually makes — that the *product model* (renderer, published
    artifact, bookmark, session payload, contract identity) is invariant too,
    so choosing a carrier is a configuration act and not a protocol change
    (spec/protocol/TRANSPORTS.md). The bindings are software-lane conformance
    doubles; no physical link is opened and no hardware claim is made.
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(_rmtree, self.root)

    def test_every_known_binding_is_modeled_and_bounded_identically(self):
        self.assertEqual(
            sorted(TRANSPORT_BINDINGS), ["loopback", "spi", "uart", "usb"]
        )
        self.assertEqual(DEFAULT_TRANSPORT_BINDING, "loopback")
        expected = {
            "loopback": MockTransport,
            "uart": UartBindingTransport,
            "spi": SpiBindingTransport,
            "usb": UsbBindingTransport,
        }
        for name, kind in expected.items():
            with self.subTest(binding=name):
                link = build_transport(build_mock_core(), name)
                self.assertIsInstance(link, kind)
                # One frame bound for every carrier: a behavioral difference
                # can only come from delivery shape, never from the envelope.
                self.assertEqual(link.max_frame_bytes, TRANSPORT_MAX_FRAME_BYTES)

    def test_unknown_binding_is_refused_by_name(self):
        for call in (
            lambda: build_transport(build_mock_core(), "rs232"),
            lambda: transport_binding_identity("rs232"),
            lambda: build_protocol_mock_session(self.root / "s", transport="rs232"),
        ):
            with self.assertRaises(ValueError) as caught:
                call()
            message = str(caught.exception)
            self.assertIn("rs232", message)
            # Refused by listing what is modeled, never silently defaulted.
            for known in TRANSPORT_BINDINGS:
                self.assertIn(known, message)

    def test_product_model_identical_across_every_binding(self):
        results = {}
        for binding in sorted(TRANSPORT_BINDINGS):
            session, probe = build_protocol_mock_session(
                self.root / f"store-{binding}", seed=5, transport=binding
            )
            index = session.allowed_indices[0]
            selection = session.request(index)
            bookmark = self.root / f"bookmark-{binding}.json"
            session.save(bookmark)
            repeated = session.repeat()
            client = probe["renderer"].client
            self.assertEqual(probe["transport_binding"], binding)
            self.assertEqual(client.state, "ready")
            results[binding] = {
                "reference": selection.stored.reference,
                "shown": session.show(),
                "bookmark": bookmark.read_bytes(),
                "repeat": repeated.stored.reference,
                "contract": probe["contract"],
                # The exact command frames the client put on the wire.
                "frames": list(client.frames_sent),
                "patch": dict(probe["mock"].active_patch["values"]),
                "calls": list(probe["renderer"].calls),
            }

        baseline = results[DEFAULT_TRANSPORT_BINDING]
        self.assertEqual(len(baseline["patch"]), 78)
        self.assertEqual(baseline["calls"], [0])
        for binding, observed in results.items():
            with self.subTest(binding=binding):
                self.assertEqual(observed, baseline)

    def test_envelope_names_the_binding_without_borrowing_its_identity(self):
        contract = backend_contract_identity()
        for binding in sorted(TRANSPORT_BINDINGS):
            with self.subTest(binding=binding):
                session, probe = build_protocol_mock_session(
                    self.root / f"env-{binding}", seed=5, transport=binding
                )
                session.request(session.allowed_indices[0])
                envelope = backend_envelope(session, probe)
                self.assertEqual(envelope["transport"]["binding"], binding)
                self.assertEqual(
                    envelope["transport"]["kind"], "software-lane-binding-model"
                )
                self.assertEqual(envelope["transport"]["hardware_claim"], "none")
                self.assertEqual(
                    envelope["transport"]["physical_link"], "none (issue #81)"
                )
                # The contract identity is the same for every carrier: it is
                # negotiated by the protocol, not conferred by the transport.
                self.assertEqual(envelope["contract"], contract)
                self.assertEqual(envelope["backend"], BACKEND_LABEL)

    def test_explorer_seam_selects_and_refuses_bindings(self):
        session, probe = build_session(
            self.root / "seam", backend="protocol-mock", seed=5, transport="usb"
        )
        self.assertEqual(probe["transport_binding"], "usb")
        self.assertIsInstance(probe["transport"], UsbBindingTransport)
        self.assertIs(probe["session"], session)
        with self.assertRaises(SessionError):
            build_session(
                self.root / "seam-bad", backend="protocol-mock", transport="rs232"
            )
        # A backend with no wire protocol has no carrier to name; it refuses
        # the argument instead of accepting one it would silently ignore.
        for backend in ("fake", "none"):
            with self.subTest(backend=backend):
                with self.assertRaises(SessionError):
                    build_session(
                        self.root / f"seam-{backend}",
                        backend=backend,
                        transport="uart",
                    )


class ExplorerBackendDisplayTests(unittest.TestCase):
    """The explorer CLI itself shows backend and contract identity (AC 4).

    `tools/explore.py --backend protocol-mock` is the user-visible surface:
    the envelope names the backend and the contract the client negotiated, and
    a backend that speaks no wire protocol reports a null contract rather than
    borrowing this one. Software-only: nothing here observes hardware.
    """

    def setUp(self):
        self.base = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(_rmtree, self.base)

    def cli(self, *args):
        return _explore_cli(self, *args)

    def test_cli_envelope_names_backend_and_contract_identity(self):
        envelope = self.cli(
            "--store",
            str(self.base / "store"),
            "--backend",
            "protocol-mock",
            "--seed",
            "5",
            "random",
        )
        self.assertEqual(envelope["backend"], BACKEND_LABEL)
        self.assertEqual(envelope["contract"], backend_contract_identity())
        # The identity is honest about what it is and is not.
        self.assertEqual(envelope["contract"]["protocol_version"], 2)
        self.assertEqual(envelope["contract"]["kind"], "behavioral-mock-software-only")
        self.assertEqual(envelope["contract"]["hardware_claim"], "none")
        self.assertEqual(
            envelope["runtime_admission"]["status"], "outside-admitted-profile"
        )

    def test_cli_backend_without_a_wire_protocol_reports_no_contract(self):
        envelope = self.cli(
            "--store",
            str(self.base / "store-fake"),
            "--backend",
            "fake",
            "--seed",
            "5",
            "random",
        )
        self.assertEqual(envelope["backend"], "fake-synthetic-fixtures-no-audio")
        self.assertIsNone(envelope["contract"])
        # Each backend reports the renderer that actually ran, by name.
        self.assertIn("fake_renderer_calls_this_process", envelope)
        self.assertNotIn("protocol_mock_renderer_calls_this_process", envelope)

    def test_cli_repeat_and_save_identical_across_python_and_mock_backends(self):
        """AC 6 through the shipped CLI, not only the library seam."""
        results = {}
        calls_key = {
            "fake": "fake_renderer_calls_this_process",
            "protocol-mock": "protocol_mock_renderer_calls_this_process",
        }
        for backend in ("fake", "protocol-mock"):
            store = self.base / f"store-{backend}"
            bookmark = self.base / f"bookmark-{backend}.json"
            first = self.cli(
                "--store", str(store), "--backend", backend, "--seed", "5", "random"
            )
            reference = first["session"]["reference"]
            saved = self.cli(
                "--store",
                str(store),
                "--backend",
                backend,
                "--selected",
                str(first["session"]["sound_index"]),
                reference["artifact_id"],
                reference["sha256"],
                "save",
                str(bookmark),
            )
            repeated = self.cli(
                "--store",
                str(store),
                "--backend",
                backend,
                "--bookmark",
                str(bookmark),
                "repeat",
            )
            # Save and repeat re-verify a pinned artifact; neither renders.
            self.assertEqual(first[calls_key[backend]], 1, backend)
            self.assertEqual(saved[calls_key[backend]], 0, backend)
            self.assertEqual(repeated[calls_key[backend]], 0, backend)
            results[backend] = (
                first["session"],
                bookmark.read_bytes(),
                repeated["session"],
            )

        python_shown, python_bookmark, python_repeat = results["fake"]
        mock_shown, mock_bookmark, mock_repeat = results["protocol-mock"]
        self.assertEqual(python_shown, mock_shown)
        self.assertEqual(python_bookmark, mock_bookmark)
        self.assertEqual(python_repeat, mock_repeat)


class ExplorerTransportCliTests(unittest.TestCase):
    """AC 5 on the shipped CLI: `--transport` moves bytes, nothing else.

    The user-visible surface must make the substitutability claim checkable
    without reading the library: the same seed over every binding prints the
    same session payload and writes byte-identical bookmarks, the envelope
    names the carrier that ran, and a backend that speaks no wire protocol
    refuses the flag rather than ignoring it.
    """

    def setUp(self):
        self.base = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(_rmtree, self.base)

    def test_cli_product_model_is_invariant_across_bindings(self):
        results = {}
        for binding in sorted(TRANSPORT_BINDINGS):
            store = self.base / f"store-{binding}"
            bookmark = self.base / f"bookmark-{binding}.json"
            first = _explore_cli(
                self,
                "--store", str(store),
                "--backend", "protocol-mock",
                "--transport", binding,
                "--seed", "5",
                "random",
            )
            reference = first["session"]["reference"]
            saved = _explore_cli(
                self,
                "--store", str(store),
                "--backend", "protocol-mock",
                "--transport", binding,
                "--selected",
                str(first["session"]["sound_index"]),
                reference["artifact_id"],
                reference["sha256"],
                "save", str(bookmark),
            )
            self.assertEqual(first["transport"]["binding"], binding)
            self.assertEqual(first["transport"]["hardware_claim"], "none")
            self.assertEqual(first["backend"], BACKEND_LABEL)
            self.assertEqual(first["contract"], backend_contract_identity())
            self.assertEqual(first["protocol_mock_renderer_calls_this_process"], 1)
            self.assertEqual(saved["protocol_mock_renderer_calls_this_process"], 0)
            results[binding] = (first["session"], bookmark.read_bytes())

        baseline = results[DEFAULT_TRANSPORT_BINDING]
        for binding, observed in results.items():
            with self.subTest(binding=binding):
                self.assertEqual(observed, baseline)

        # …and still identical to the Python backend, which has no carrier at
        # all: the transport choice does not move the product model either way.
        python = _explore_cli(
            self,
            "--store", str(self.base / "store-fake"),
            "--backend", "fake",
            "--seed", "5",
            "random",
        )
        self.assertIsNone(python["transport"])
        self.assertEqual(python["session"], baseline[0])

    def test_cli_refuses_a_transport_for_a_backend_without_a_protocol(self):
        completed = _explore_cli_raw(
            "--store", str(self.base / "store-refused"),
            "--backend", "fake",
            "--transport", "uart",
            "--seed", "5",
            "random",
        )
        self.assertEqual(completed.returncode, 2, completed.stdout)
        self.assertIn("speaks no wire protocol", completed.stderr)

    def test_cli_rejects_an_unmodeled_binding_at_argument_parse(self):
        completed = _explore_cli_raw(
            "--store", str(self.base / "store-rs232"),
            "--backend", "protocol-mock",
            "--transport", "rs232",
            "random",
        )
        self.assertEqual(completed.returncode, 2, completed.stdout)
        self.assertIn("rs232", completed.stderr)


def _explore_cli_raw(*args):
    import subprocess

    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "explore.py"), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )


def _explore_cli(test, *args):
    completed = _explore_cli_raw(*args)
    test.assertEqual(completed.returncode, 0, completed.stderr)
    return json.loads(completed.stdout)


def _rmtree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
