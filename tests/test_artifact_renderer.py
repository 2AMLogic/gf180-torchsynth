"""Synthetic adapter evidence, deliberately independent of the numerical runtime."""

from __future__ import annotations

import copy
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.artifact_renderer import (  # noqa: E402
    DockerBackend,
    digest,
    fixture,
    qualification,
    render_artifact,
    request_template,
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


if __name__ == "__main__":
    unittest.main()
