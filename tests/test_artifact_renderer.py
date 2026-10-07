"""Synthetic adapter evidence, deliberately independent of the numerical runtime."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import artifact_renderer  # noqa: E402
from torchsynth_voice.artifact_renderer import (  # noqa: E402
    PORTABLE_COMMAND_REPRESENTATION,
    DockerBackend,
    ReceiptPortabilityError,
    digest,
    failure_receipt,
    fixture,
    host_path_findings,
    json_bytes,
    qualification,
    render_artifact,
    request_template,
    require_portable_receipt,
    validate_failure_receipt,
)
from torchsynth_voice.artifacts import ValidationError, canonical_bytes, loads  # noqa: E402
from torchsynth_voice.storage import ArtifactStore, CollisionError  # noqa: E402


def template():
    value = request_template()
    value["project_git"] = dict(
        commit="b" * 40,
        dirty=False,
        diff_sha256=hashlib.sha256(b"").hexdigest(),
        untracked_sha256=digest(canonical_bytes({})),
    )
    return value


class FakeBackend:
    def __init__(self):
        self.calls = []
        self.mutate = None

    def __call__(self, request, *, capture_provider=None):
        self.calls.append(copy.deepcopy(request))
        names = [
            p["name"]
            for p in loads(
                (ROOT / "spec/reference/parameter-inventory-v1.json").read_bytes()
            )["parameters"]
        ]
        zeros = bytes(176400 * 4)
        observation = dict(
            audio=zeros,
            noise=zeros,
            pre_normalization=zeros,
            noise_slot=request["fixture"]["sound_index"] % 32,
            noise_sha256=digest(zeros),
            parameters=dict(
                normalized_by_name=dict.fromkeys(names, 0.5),
                physical_by_name={
                    k: request["locks_physical"].get(k, 0.5) for k in names
                },
                locks_physical=request["locks_physical"],
            ),
            traces={name: b"synthetic-capture" for name in request["requested_traces"]},
            receipt={
                "synthetic": True,
                "warning_categories": ["synthetic-fixture-not-rendered"],
            },
        )
        if capture_provider:
            capture_provider(request)
        if self.mutate:
            self.mutate(observation)
        return observation


class RendererTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = ArtifactStore(self.root / "store")
        self.request = dict(template(), fixture=fixture(0))
        self.backend = FakeBackend()

    def render(self):
        return render_artifact(self.request, self.store, self.backend)

    def test_actual_named_input_and_explicit_empty_traces(self):
        product = self.render()
        record = loads((product.stored.path / "metadata.json").read_bytes())
        self.assertEqual(record["traces"]["value"], {})
        self.assertEqual(
            record["inputs"]["value"]["noise"]["sha256"], digest(bytes(705600))
        )
        self.assertEqual(product.receipt["observations"]["pre_normalization_peak"], 0)
        self.assertEqual(
            set(p.name for p in product.stored.path.iterdir()),
            {"audio.f32le", "metadata.json"},
        )

    def test_request_mismatch_before_backend_access(self):
        for path, value in (
            (["source", "commit"], "f" * 40),
            (["runtime", "versions", "torch"], "2.0"),
            (["profile", "duration_seconds"], 1),
            (["execution", "batch_size"], 1),
            (["execution", "batch_size"], 128),
            (["locks_physical"], {"position.0": 0.5}),
            (["requested_traces"], ["a", "a"]),
        ):
            with self.subTest(path=path):
                request = copy.deepcopy(self.request)
                target = request
                for name in path[:-1]:
                    target = target[name]
                target[path[-1]] = value
                backend = Mock()
                with self.assertRaises(ValidationError):
                    render_artifact(request, self.store, backend)
                backend.assert_not_called()

    def test_invalid_observations_never_publish(self):
        def wrong_names(observation):
            for key in ("normalized_by_name", "physical_by_name"):
                values = observation["parameters"][key]
                values["position.0"] = values.pop(next(iter(values)))

        mutations = [
            wrong_names,
            lambda o: o["parameters"]["normalized_by_name"].pop(
                next(iter(o["parameters"]["normalized_by_name"]))
            ),
            lambda o: o.update(noise_slot=1),
            lambda o: o.update(noise_sha256="0" * 64),
            lambda o: o.pop("noise_sha256"),
            lambda o: o.update(audio=b"short"),
            lambda o: o.update(traces={"unknown": b"x"}),
            lambda o: o.update(pre_normalization=b"short"),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.backend.mutate = mutation
                with self.assertRaises(ValidationError):
                    self.render()
                self.assertEqual(self.store.discover(), ())

    def test_same_render_capture_bound_before_identity(self):
        audio_only = self.render()
        self.request.update(
            trace_registry_version="synthetic-registry-v1",
            requested_traces=["synthetic.trace"],
        )
        provider = Mock()
        traced = render_artifact(
            self.request, self.store, self.backend, capture_provider=provider
        )
        provider.assert_called_once()
        self.assertNotEqual(
            audio_only.reference["artifact_id"], traced.reference["artifact_id"]
        )
        self.assertEqual(
            (traced.stored.path / "traces/trace-0.bin").read_bytes(),
            b"synthetic-capture",
        )

    def test_same_id_different_bytes_collision_preserves_original(self):
        first = self.render()
        original = (first.stored.path / "metadata.json").read_bytes()
        self.backend.mutate = lambda o: o.update(audio=b"\0\0\x80\x3f" * 176400)
        with self.assertRaises(CollisionError):
            self.render()
        self.assertEqual((first.stored.path / "metadata.json").read_bytes(), original)

    def test_gain_derived_from_observed_pre_normalization(self):
        self.backend.mutate = lambda o: o.update(
            audio=b"\0\0\x80\x3f" * 176400, pre_normalization=b"\0\0\0\x40" * 176400
        )
        product = self.render()
        record = loads((product.stored.path / "metadata.json").read_bytes())
        self.assertEqual(record["audio"]["value"]["normalization_gain"], 0.5)
        self.assertEqual(product.receipt["observations"]["pre_normalization_peak"], 2)

    def test_runtime_publication_binding_and_no_scalar_promotion(self):
        publication, runtime = qualification()
        self.assertEqual(
            runtime["math_environment"],
            {"ATEN_CPU_CAPABILITY": None, "MKL_CBWR": "COMPATIBLE"},
        )
        self.assertEqual(len(publication["cells"]), 128)
        self.request["execution"] = dict(
            mode="resolved-scalar", batch_size=1, reproducible=False
        )
        with self.assertRaisesRegex(ValidationError, "execution"):
            DockerBackend()(self.request)

    def test_worker_source_gate_precedes_numerical_import(self):
        # A malformed source is refused in a fresh subprocess even without Torch installed.
        import subprocess

        code = "import sys; from pathlib import Path; sys.path.insert(0, 'env/release-era'); import render_artifact as w; w.render_selected({}, Path('missing-source'))"
        result = subprocess.run(
            [sys.executable, "-S", "-c", code], cwd=ROOT, capture_output=True, text=True
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source", result.stderr)
        self.assertNotIn("No module named 'torch'", result.stderr)


# --- Portable attempt receipts (issue #289) ---------------------------------

HOST_PATH_INJECTIONS = {
    "posix-standalone": {"note": "/Users/operator/store"},
    "posix-embedded-mount": {
        "command": ["--mount", "type=bind,src=/home/op/checkout,dst=/repo,readonly"],
        "command_representation": PORTABLE_COMMAND_REPRESENTATION,
    },
    "posix-nested": {"deep": {"list": [1, {"x": "cwd=/srv/build"}]}},
    "posix-key": {"files": {"/private/tmp/tmpabc": "x"}},
    "posix-escape-from-container-root": {
        "command": ["/repo/../Users/op"],
        "command_representation": PORTABLE_COMMAND_REPRESENTATION,
    },
    "container-root-outside-its-field": {"note": "/repo/env"},
    "windows-drive-backslash": {"note": "C:\\Users\\op\\store"},
    "windows-drive-slash": {"note": "src=D:/work/store"},
    "unc": {"note": "\\\\fileserver\\share\\store"},
    "unc-extended": {"note": "\\\\?\\C:\\store"},
    "windows-rooted": {"nested": ["\\Users\\op"]},
    "home-relative": {"note": "~/corpus-store"},
    "url-like": {"note": "file:///Users/op/store"},
}


class PortableReceiptPolicyTests(unittest.TestCase):
    """Lexical, host-OS independent policy; no Docker or numerical rendering."""

    def test_every_host_path_form_rejected(self):
        for name, receipt in HOST_PATH_INJECTIONS.items():
            with self.subTest(name=name):
                self.assertTrue(host_path_findings(receipt))
                with self.assertRaises(ReceiptPortabilityError):
                    require_portable_receipt(receipt)

    def test_diagnostics_never_echo_the_offending_text(self):
        with self.assertRaises(ReceiptPortabilityError) as caught:
            require_portable_receipt({"/Users/op/secret": {"y": "/Users/op/secret"}})
        self.assertNotIn("/Users", str(caught.exception))
        self.assertEqual(host_path_findings(str(caught.exception)), [])

    def test_declared_container_paths_and_portable_values_accepted(self):
        receipt = dict(
            command=[
                "docker",
                "--platform",
                "linux/amd64",
                "--mount",
                "type=bind,src=<project-root>,dst=/repo,readonly",
                "--mount",
                "type=bind,src=<worker-output>,dst=/output",
                "sha256:" + "e" * 64,
                "/repo/env/release-era/render_artifact.py",
                "/output/request.json",
                "/opt/torchsynth",
            ],
            command_representation=PORTABLE_COMMAND_REPRESENTATION,
            host=dict(docker_server="linux/arm64 29.7.2"),
            runtime=dict(
                torch_build="CXX_COMPILER=/opt/rh/devtoolset-9/root/usr/bin/c++, X=1"
            ),
            source_sha256={"torchsynth/config.py": "a" * 64},
        )
        self.assertEqual(host_path_findings(receipt), [])
        require_portable_receipt(receipt)
        require_portable_receipt({})

    def test_command_requires_marker_and_placeholder_mount_sources(self):
        good = ["--mount", "type=bind,src=<project-root>,dst=/repo"]
        with self.assertRaisesRegex(ReceiptPortabilityError, "representation"):
            require_portable_receipt({"command": good})
        for command in (
            ["--mount", "type=bind,src=relative/checkout,dst=/repo"],
            ["--mount", "type=bind,source=checkout,dst=/repo"],
            ["-v", "checkout:/repo"],
            ["--volume=checkout:/repo"],
            ["-vcheckout:/repo"],
            "docker run",
            ["docker", 1],
        ):
            with self.subTest(command=command):
                with self.assertRaises(ReceiptPortabilityError):
                    require_portable_receipt(
                        dict(
                            command=command,
                            command_representation=PORTABLE_COMMAND_REPRESENTATION,
                        )
                    )
        require_portable_receipt(
            dict(
                command=["-v", "<worker-output>:/output", *good],
                command_representation=PORTABLE_COMMAND_REPRESENTATION,
            )
        )

    def test_failure_diagnostic_withholds_path_bearing_messages(self):
        for message in (
            "No such file: '/Users/op/store/x'",
            "worker failed: Traceback at C:\\build\\x.py",
            "unc \\\\server\\share",
        ):
            with self.subTest(message=message):
                receipt = failure_receipt(OSError(message))
                validate_failure_receipt(receipt)
                self.assertIsNone(receipt["message"])
                self.assertEqual(receipt["message_status"], "withheld-host-path")
                self.assertEqual(
                    receipt["message_sha256"], hashlib.sha256(message.encode()).hexdigest()
                )
                self.assertEqual(host_path_findings(receipt), [])
        receipt = failure_receipt(ValidationError("host outside DR-0006 measured scope"))
        self.assertEqual(
            receipt,
            dict(
                error_type="ValidationError",
                message="host outside DR-0006 measured scope",
                message_sha256=digest(b"host outside DR-0006 measured scope"),
                message_status="portable",
            ),
        )
        for bad in (
            dict(receipt, message="tampered"),
            dict(receipt, message=None),
            dict(receipt, message_status="withheld-host-path"),
            dict(receipt, extra=True),
            {"error": "/Users/op"},
        ):
            with self.assertRaises(ValidationError):
                validate_failure_receipt(bad)

    def test_retained_historical_receipts_classified_not_portable_read_only(self):
        path = ROOT / "sim/reference/corpus-smoke.json"
        original = path.read_bytes()
        record = loads(original)
        renders = [
            a["receipt"]
            for run in ("render_run", "resumed_run")
            for a in record[run]["attempts"]
            if a["kind"] == "render"
        ]
        self.assertTrue(renders)
        for receipt in renders:
            with self.assertRaises(ReceiptPortabilityError):
                require_portable_receipt(receipt)
            # Only the two host mount sources are findings; container paths
            # and the qualified runtime's toolchain path are declared.
            self.assertEqual(
                {kind for _, kind in host_path_findings(receipt)}, {"posix-absolute"}
            )
            self.assertEqual(len(host_path_findings(receipt)), 2)
        self.assertEqual(path.read_bytes(), original)

    def test_render_artifact_refuses_path_bearing_receipt_before_publication(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = ArtifactStore(Path(temp.name).resolve() / "store")
        for name, injected in HOST_PATH_INJECTIONS.items():
            with self.subTest(name=name):
                backend = FakeBackend()
                backend.mutate = lambda o, injected=injected: o["receipt"].update(
                    copy.deepcopy(injected)
                )
                with self.assertRaises(ReceiptPortabilityError):
                    render_artifact(dict(template(), fixture=fixture(0)), store, backend)
                self.assertEqual(store.discover(), ())


# The committed qualification is read, never mocked: request validation and
# the backend's runtime check both bind to it. Only host facts are faked.
_PUBLICATION, QUALIFIED_RUNTIME = qualification()
FAKE_IMAGE = _PUBLICATION["provenance"]["image"]["Id"]
FAKE_SERVER = _PUBLICATION["provenance"]["docker_server"]
FAKE_WORKER = b"# synthetic worker stand-in; never executed\n"
FAKE_WORKER_SHA256 = hashlib.sha256(FAKE_WORKER).hexdigest()


def fake_project_root(base):
    """A producer checkout whose path contains spaces; only the worker file."""
    root = base / "producer checkout with spaces"
    (root / "env/release-era").mkdir(parents=True)
    (root / "env/release-era/render_artifact.py").write_bytes(FAKE_WORKER)
    return root


def _fake_check_output(args, text=False, **_):
    if args[0] == "sysctl":
        out = "Apple M5\n"
    elif args[:2] == ["docker", "version"]:
        out = FAKE_SERVER + "\n"
    elif args[:3] == ["docker", "image", "inspect"]:
        out = json.dumps([dict(Id=FAKE_IMAGE, Architecture="amd64", Os="linux")])
    else:  # pragma: no cover - any other process access is a test failure
        raise AssertionError(f"unexpected process: {args}")
    return out if text else out.encode()


def mount_source(command, target):
    """Find a bind source by its container target, never by argv position."""
    suffix = ",dst=" + target
    matches = [
        a
        for a in command
        if a.startswith("type=bind,src=") and (a.endswith(suffix) or suffix + "," in a)
    ]
    assert len(matches) == 1, matches
    return matches[0][len("type=bind,src=") :].split(suffix, 1)[0]


@contextlib.contextmanager
def mocked_docker_launch(module, project_git, observation_factory):
    """Mock DR-0006 host facts and the worker process; returns executed argvs.

    The fake worker reads the request and writes the files the real one would
    in the actual ``/output`` bind source found in the executed argv.
    """
    executed = []

    def run(command, **_):
        executed.append(list(command))
        output = Path(mount_source(command, "/output"))
        request = loads((output / "request.json").read_bytes())
        observation = observation_factory(request)
        for name in ("audio", "noise", "pre_normalization"):
            (output / (name + ".f32le")).write_bytes(observation.pop(name))
        traces = observation.pop("traces")
        if traces:
            (output / "traces").mkdir()
            descriptors = []
            for index, (name, data) in enumerate(traces.items()):
                ref = f"traces/trace-{index}.bin"
                (output / ref).write_bytes(data)
                descriptors.append(
                    dict(name=name, file_ref=ref, sha256=hashlib.sha256(data).hexdigest())
                )
            (output / "trace-descriptors.json").write_bytes(json_bytes(descriptors))
        else:
            observation["traces"] = {}
        observation["receipt"] = dict(
            request_sha256=digest(json_bytes(request)),
            worker_sha256=FAKE_WORKER_SHA256,
            runtime=copy.deepcopy(QUALIFIED_RUNTIME),
            warning_categories=[],
        )
        (output / "observation.json").write_bytes(json_bytes(observation))
        return subprocess.CompletedProcess(command, 0, b"worker stdout", b"")

    with contextlib.ExitStack() as stack:
        stack.enter_context(
            patch.object(
                module, "project_identity", Mock(return_value=project_git)
            )
        )
        stack.enter_context(patch("platform.system", return_value="Darwin"))
        stack.enter_context(patch("platform.mac_ver", return_value=("26.5.1", "", "")))
        stack.enter_context(patch("platform.machine", return_value="arm64"))
        stack.enter_context(patch("platform.platform", return_value="macOS-26.5.1"))
        stack.enter_context(patch("subprocess.check_output", _fake_check_output))
        stack.enter_context(patch("subprocess.run", side_effect=run))
        yield executed


def assert_portable_launch(test, executed, receipt, project_root):
    """Real argv has real sources; the receipt is the same argv with placeholders."""
    argv = executed[-1]
    real_project = mount_source(argv, "/repo")
    real_output = mount_source(argv, "/output")
    test.assertEqual(real_project, str(project_root))
    test.assertTrue(Path(real_output).is_absolute())
    test.assertNotIn("<", real_project + real_output)
    test.assertEqual(receipt["command_representation"], PORTABLE_COMMAND_REPRESENTATION)
    test.assertEqual(
        receipt["command"],
        [
            a.replace("src=" + real_project + ",", "src=<project-root>,").replace(
                "src=" + real_output + ",", "src=<worker-output>,"
            )
            for a in argv
        ],
    )
    test.assertNotEqual(receipt["command"], argv)
    published = json.dumps(receipt)
    test.assertNotIn(real_project, published)
    test.assertNotIn(real_output, published)
    test.assertEqual(host_path_findings(receipt), [])
    require_portable_receipt(receipt)
    test.assertEqual(receipt["image"], FAKE_IMAGE)
    test.assertIn(FAKE_IMAGE, receipt["command"])
    test.assertEqual(receipt["stdout_sha256"], digest(b"worker stdout"))
    test.assertEqual(receipt["stderr_sha256"], digest(b""))
    test.assertEqual(receipt["worker_sha256"], FAKE_WORKER_SHA256)
    test.assertEqual(receipt["runtime"], QUALIFIED_RUNTIME)
    test.assertEqual(receipt["exit_code"], 0)


class DockerBackendReceiptTests(unittest.TestCase):
    """Mocked launch: real mount sources reach argv, placeholders reach receipts."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name).resolve()
        self.project_root = fake_project_root(base)
        self.store = ArtifactStore(base / "store")
        self.request = dict(template(), fixture=fixture(0))

    def test_audio_only_backend_publishes_placeholder_command(self):
        backend = DockerBackend(project_root=self.project_root)
        with mocked_docker_launch(
            artifact_renderer, self.request["project_git"], FakeBackend()
        ) as executed:
            product = render_artifact(self.request, self.store, backend)
        self.assertEqual(len(executed), 1)
        assert_portable_launch(self, executed, product.receipt, self.project_root)
        self.assertEqual(
            mount_source(product.receipt["command"], "/repo"), "<project-root>"
        )
        self.assertIn(
            "type=bind,src=<project-root>,dst=/repo,readonly", product.receipt["command"]
        )
        self.assertIn("/repo/env/release-era/render_artifact.py", product.receipt["command"])
        self.assertEqual(len(self.store.discover()), 1)

    def test_worker_failure_diagnostic_is_withheld_when_path_bearing(self):
        backend = DockerBackend(project_root=self.project_root)
        stderr = ("Traceback: " + str(self.project_root) + "/x.py").encode()
        with mocked_docker_launch(
            artifact_renderer, self.request["project_git"], FakeBackend()
        ):
            with patch(
                "subprocess.run",
                return_value=subprocess.CompletedProcess([], 1, b"", stderr),
            ):
                with self.assertRaises(ValidationError) as caught:
                    backend(self.request)
        receipt = failure_receipt(caught.exception)
        self.assertEqual(receipt["message_status"], "withheld-host-path")
        self.assertNotIn(str(self.project_root), json.dumps(receipt))


if __name__ == "__main__":
    unittest.main()
