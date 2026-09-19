"""Synthetic trace-bundle and companion controls; no TorchSynth or holdout data."""

from __future__ import annotations

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

from torchsynth_voice import trace_artifacts as ta  # noqa: E402
from torchsynth_voice import trace_registry  # noqa: E402
from torchsynth_voice.artifact_renderer import (  # noqa: E402
    fixture,
    render_artifact,
)
from torchsynth_voice.artifacts import ValidationError, loads  # noqa: E402
from torchsynth_voice.corpus import read_reference, run_corpus  # noqa: E402
from torchsynth_voice.storage import ArtifactStore  # noqa: E402

from test_artifact_renderer import FakeBackend, template as base_template  # noqa: E402

SUBSET = [
    "keyboard.midi_f0",
    "keyboard.duration",
    "adsr_1.output",
    "adsr_2.output",
    "mixer.output",
]


def document():
    return trace_registry.load_registry()


def synthetic_payload(name):
    trace = {t["name"]: t for t in document()["traces"]}[name]
    width = ta.element_count(trace["shape"]) * 4
    unit = hashlib.sha256(name.encode()).digest()
    return (unit * (width // len(unit) + 1))[:width]


def capture_inventory(names, payloads):
    inventory = []
    for name in names:
        trace = {t["name"]: t for t in document()["traces"]}[name]
        data = payloads[name]
        inventory.append(
            dict(
                name=name,
                shape=list(trace["shape"]),
                batch_shape=[
                    32 if n == "B" else n for n in trace["batch_shape"]
                ],
                dtype="float32",
                rate_hz=trace["rate_hz"],
                boundary=trace["boundary"],
                sha256=hashlib.sha256(data).hexdigest(),
            )
        )
    return inventory


class TracedBackend(FakeBackend):
    """FakeBackend whose trace payloads have the registry-declared widths."""

    def __call__(self, request, *, capture_provider=None):
        observation = super().__call__(request, capture_provider=capture_provider)
        observation["traces"] = {
            name: synthetic_payload(name)
            for name in request["requested_traces"]
        }
        return observation


def traced_template(names=None):
    value = base_template()
    value.update(ta.traced_request_fields(names=names))
    return value


def published_payloads(stored, record, names):
    order = trace_registry.requested_names(document(), names)
    payloads = {}
    for index, name in enumerate(order):
        reference = record["traces"]["value"][name]
        assert reference["ref"] == f"traces/trace-{index}.bin"
        payloads[name] = (stored.path / reference["ref"]).read_bytes()
    return payloads


class BindingTests(unittest.TestCase):
    def test_token_is_content_qualified_and_order_is_graph_order(self):
        raw = trace_registry.REGISTRY_PATH.read_bytes()
        expected = "tr1-" + hashlib.sha256(
            b"torchsynth-trace-registry-v1\n" + raw
        ).hexdigest()
        fields = ta.traced_request_fields(names=list(reversed(SUBSET)))
        self.assertEqual(fields["trace_registry_version"], expected)
        self.assertEqual(
            fields["requested_traces"],
            ["keyboard.midi_f0", "keyboard.duration", "adsr_1.output",
             "adsr_2.output", "mixer.output"],
        )

    def test_unknown_duplicate_and_empty_requests(self):
        with self.assertRaises(ValueError):
            ta.traced_request_fields(names=["not.a.trace"])
        with self.assertRaises(ValueError):
            ta.traced_request_fields(names=["adsr_1.output", "adsr_1.output"])
        empty = ta.traced_request_fields(names=[])
        self.assertEqual(empty["requested_traces"], [])
        full = ta.traced_request_fields()
        self.assertEqual(len(full["requested_traces"]), 32)
        self.assertIn("mixer.peak", full["requested_traces"])

    def test_production_selection_excludes_normalization_seams(self):
        selection = ta.production_selection()
        self.assertEqual(len(selection), 29)
        for seam in ta.NORMALIZATION_SEAMS:
            self.assertNotIn(seam, selection)
        fields = ta.traced_request_fields(names=selection)
        self.assertEqual(fields["requested_traces"], selection)

    def test_audio_only_and_traced_variants_distinct_ids_same_sound(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ArtifactStore(Path(directory).resolve() / "store")
            plain_request = dict(base_template(), fixture=fixture(0))
            traced_request = dict(plain_request, **ta.traced_request_fields(names=SUBSET))
            plain = render_artifact(plain_request, store, FakeBackend())
            traced = render_artifact(traced_request, store, TracedBackend())
            self.assertNotEqual(
                plain.reference["artifact_id"], traced.reference["artifact_id"]
            )
            plain_record = loads((plain.stored.path / "metadata.json").read_bytes())
            traced_record = loads((traced.stored.path / "metadata.json").read_bytes())
            plain_inputs = plain_record["inputs"]["value"]
            traced_inputs = traced_record["inputs"]["value"]
            differing = {
                key
                for key in plain_inputs
                if plain_inputs[key] != traced_inputs[key]
            }
            self.assertEqual(
                differing, {"trace_registry_version", "requested_traces"}
            )
            self.assertEqual(
                traced_record["audio"]["value"]["file"],
                plain_record["audio"]["value"]["file"],
            )
            self.assertEqual(
                traced_record["inputs"]["value"]["fixture"], plain_inputs["fixture"]
            )

    def test_changed_trace_request_creates_new_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ArtifactStore(Path(directory).resolve() / "store")
            first = render_artifact(
                dict(base_template(), fixture=fixture(0),
                     **ta.traced_request_fields(names=["adsr_1.output"])),
                store,
                TracedBackend(),
            )
            second = render_artifact(
                dict(base_template(), fixture=fixture(0),
                     **ta.traced_request_fields(names=["adsr_1.output", "adsr_2.output"])),
                store,
                TracedBackend(),
            )
            self.assertNotEqual(
                first.reference["artifact_id"], second.reference["artifact_id"]
            )


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.store = ArtifactStore(self.root / "store")
        self.backend = TracedBackend()
        self.provider = Mock()
        self.request = dict(
            base_template(), fixture=fixture(0),
            **ta.traced_request_fields(names=SUBSET),
        )
        self.product = render_artifact(
            self.request, self.store, self.backend, capture_provider=self.provider
        )
        self.record = loads(
            (self.product.stored.path / "metadata.json").read_bytes()
        )
        self.payloads = published_payloads(
            self.product.stored, self.record, SUBSET
        )
        self.inventory = capture_inventory(SUBSET, self.payloads)
        self.bundle = ta.build_bundle(
            self.product.stored, self.record, self.inventory
        )

    def validate(self, bundle=None):
        return ta.validate_bundle(self.bundle if bundle is None else bundle,
                                  store=self.store)

    def test_seam_provider_called_and_bundle_validates_with_accounting(self):
        self.provider.assert_called_once()
        stats = self.validate()
        self.assertEqual(stats["trace_payloads"], len(SUBSET))
        self.assertEqual(
            stats["trace_bytes"],
            sum(len(self.payloads[name]) for name in SUBSET),
        )
        self.assertGreater(stats["bundle_bytes"], 0)
        self.assertEqual(
            stats["artifact_bytes"],
            len((self.product.stored.path / "metadata.json").read_bytes()),
        )

    def test_closed_v1_references_preserved_exactly(self):
        for name in SUBSET:
            reference = self.bundle["traces"][name]["file"]
            self.assertEqual(set(reference), {"ref", "sha256", "size_bytes"})
            self.assertEqual(reference, self.record["traces"]["value"][name])

    def test_scalar_facts_carry_no_invented_rate_or_count(self):
        for name in ("keyboard.midi_f0", "keyboard.duration"):
            descriptor = self.bundle["traces"][name]
            self.assertIsNone(descriptor["rate_hz"])
            self.assertIsNone(descriptor["sample_count"])
            self.assertEqual(descriptor["classification"], "scalar")
            self.assertEqual(descriptor["file"]["size_bytes"], 4)
        sampled = self.bundle["traces"]["adsr_1.output"]
        self.assertEqual(sampled["rate_hz"], 441)
        self.assertEqual(sampled["sample_count"], 1764)
        self.assertEqual(sampled["classification"], "control")
        audio = self.bundle["traces"]["mixer.output"]
        self.assertEqual(audio["rate_hz"], 44100)
        self.assertEqual(audio["observation"], "observed")

    def test_stale_registry_digest_and_token_rejected(self):
        for field in ("sha256", "token"):
            bundle = copy.deepcopy(self.bundle)
            bundle["registry"][field] = "0" * 64
            with self.assertRaises(ValidationError):
                self.validate(bundle)

    def test_stale_artifact_metadata_reference_rejected(self):
        bundle = copy.deepcopy(self.bundle)
        bundle["artifact"] = dict(bundle["artifact"], sha256="a" * 64)
        with self.assertRaises(ValidationError):
            self.validate(bundle)

    def test_same_shaped_swapped_capture_records_rejected(self):
        inventory = copy.deepcopy(self.inventory)
        left = next(
            i for i, item in enumerate(inventory)
            if item["name"] == "adsr_1.output"
        )
        right = next(
            i for i, item in enumerate(inventory)
            if item["name"] == "adsr_2.output"
        )
        inventory[left]["sha256"], inventory[right]["sha256"] = (
            inventory[right]["sha256"],
            inventory[left]["sha256"],
        )
        with self.assertRaisesRegex(ValidationError, "published payload"):
            ta.build_bundle(self.product.stored, self.record, inventory)

    def test_scalar_rate_and_sampled_confusion_rejected(self):
        for name, changes in (
            ("keyboard.midi_f0", {"rate_hz": 44100}),
            ("keyboard.midi_f0", {"sample_count": 1}),
            ("adsr_1.output", {"rate_hz": None}),
            ("adsr_2.output", {"sample_count": None}),
            ("adsr_1.output", {"classification": "scalar"}),
        ):
            with self.subTest(changes=changes):
                bundle = copy.deepcopy(self.bundle)
                bundle["traces"][name].update(changes)
                with self.assertRaises(ValidationError):
                    self.validate(bundle)

    def test_duplicate_json_keys_rejected(self):
        raw = (
            b'{"schema": "torchsynth-trace-bundle",'
            b' "schema": "torchsynth-trace-bundle"}'
        )
        with self.assertRaises(ValidationError):
            loads(raw)

    def test_malformed_payload_path_rejected(self):
        for bad in ("../escape.bin", "traces//double.bin", "/absolute.bin"):
            with self.subTest(ref=bad):
                bundle = copy.deepcopy(self.bundle)
                bundle["traces"]["adsr_1.output"]["file"]["ref"] = bad
                with self.assertRaises(ValidationError):
                    self.validate(bundle)

    def test_descriptor_boundary_and_observation_mutations_rejected(self):
        for name, changes in (
            ("adsr_1.output", {"boundary": "post-midi-clamp"}),
            ("mixer.output", {"observation": "derived"}),
        ):
            with self.subTest(changes=changes):
                bundle = copy.deepcopy(self.bundle)
                bundle["traces"][name].update(changes)
                with self.assertRaises(ValidationError):
                    self.validate(bundle)

    def test_corrupt_truncated_extra_and_missing_payloads_refused(self):
        stored = self.product.stored.path
        target = stored / "traces/trace-2.bin"
        original = target.read_bytes()
        target.write_bytes(b"x" + original[1:])
        with self.assertRaises(ValidationError):
            self.validate()
        target.write_bytes(original[:-8])
        with self.assertRaises(ValidationError):
            self.validate()
        target.write_bytes(original)
        extra = stored / "traces/trace-99.bin"
        extra.write_bytes(b"undeclared")
        with self.assertRaises(ValidationError):
            self.validate()
        extra.unlink()
        missing = stored / "traces/trace-4.bin"
        kept = missing.read_bytes()
        missing.unlink()
        with self.assertRaises(ValidationError):
            self.validate()
        missing.write_bytes(kept)
        self.validate()

    def test_bundle_requires_requested_registry_subset(self):
        with self.assertRaises(ValueError):
            ta.validate_bundle(
                dict(self.bundle, requested_traces=["not.a.trace"]),
                store=self.store,
            )


class SurfaceMixin(unittest.TestCase):
    """Shared two-case traced-corpus scaffolding; defines no tests itself."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.store_root = self.root / "store"
        self.store = ArtifactStore(self.store_root)
        self.manifest = ROOT / "spec/reference/corpus-v0.json"
        self.template = traced_template(names=SUBSET)
        self.backend = TracedBackend()

    def renderer(self, request, store):
        return render_artifact(request, store, self.backend)

    def run_cases(self, **kwargs):
        return run_corpus(
            self.store_root,
            self.manifest,
            self.template,
            self.renderer,
            indices=kwargs.pop("indices", [0, 1]),
            **kwargs,
        )

    def build_surface(self, envelope):
        index_reference = envelope["index"]
        index = loads(read_reference(self.store_root, index_reference))
        bundles = []
        for case in index["cases"]:
            if case["status"] != "complete":
                continue
            stored = self.store.verify(
                case["artifact"]["artifact_id"],
                sha256=case["artifact"]["sha256"],
            )
            record = loads((stored.path / "metadata.json").read_bytes())
            names = record["inputs"]["value"]["requested_traces"]
            payloads = published_payloads(stored, record, names)
            bundles.append(
                ta.build_bundle(stored, record, capture_inventory(names, payloads))
            )
        companion = ta.build_companion(
            index_reference=index_reference, index=index, bundles=bundles
        )
        ta.publish_companion_surface(self.store_root, bundles, companion)
        return companion, bundles


class CompanionTests(SurfaceMixin):

    def test_two_case_companion_validates_and_is_immutable(self):
        envelope = self.run_cases()
        companion, bundles = self.build_surface(envelope)
        stats = ta.validate_companion(companion, root=self.store_root, store=self.store)
        self.assertEqual(stats["cases"], 2)
        self.assertEqual(stats["bundles"], 2)
        reference = ta.companion_reference(companion)
        published = self.store_root / reference["ref"]
        before = {
            p: (p.read_bytes(), p.stat().st_mtime_ns)
            for p in self.store_root.rglob("*") if p.is_file()
        }
        ta.publish_companion_surface(self.store_root, bundles, companion)
        after = {
            p: (p.read_bytes(), p.stat().st_mtime_ns)
            for p in self.store_root.rglob("*") if p.is_file()
        }
        self.assertEqual(before, after)
        costs = ta.storage_cost(self.store_root, companion)
        self.assertEqual(costs["aggregate"]["bundle_count"], 2)
        self.assertGreater(costs["aggregate"]["trace_payload_bytes"], 0)
        self.assertEqual(costs["aggregate"]["companion_bytes"], reference["size_bytes"])
        self.assertEqual(published.read_bytes()[:1], b"{")

    def test_resume_revalidates_without_renderer_calls(self):
        first = self.run_cases()
        companion, _ = self.build_surface(first)
        calls = len(self.backend.calls)
        resumed = self.run_cases(resume=first["run_id"])
        self.assertEqual(len(self.backend.calls), calls)
        self.assertEqual(resumed["counts"]["resume"], 2)
        self.assertEqual(resumed["index"], first["index"])
        stats = ta.validate_companion(companion, root=self.store_root, store=self.store)
        self.assertEqual(stats["trace_payloads"], 2 * len(SUBSET))

    def test_failed_case_stays_listed_without_bundle(self):
        def fail_second(request, store):
            if request["fixture"]["sound_index"] == 1:
                raise RuntimeError("synthetic injected failure")
            return self.renderer(request, store)

        envelope = run_corpus(
            self.store_root, self.manifest, self.template, fail_second,
            indices=[0, 1],
        )
        self.assertEqual(envelope["status"], "failed")
        companion, _ = self.build_surface(envelope)
        self.assertEqual(companion["status"], "failed")
        self.assertIsNone(companion["cases"][1]["bundle"])
        self.assertIsNotNone(companion["cases"][0]["bundle"])
        stats = ta.validate_companion(companion, root=self.store_root, store=self.store)
        self.assertEqual(stats["bundles"], 1)

    def test_coverage_omission_and_stale_references_rejected(self):
        envelope = self.run_cases()
        companion, _ = self.build_surface(envelope)
        dropped = copy.deepcopy(companion)
        dropped["cases"].pop()
        with self.assertRaisesRegex(ValidationError, "omits or adds"):
            ta.validate_companion(dropped, root=self.store_root, store=self.store)
        stale_index = copy.deepcopy(companion)
        stale_index["index"] = dict(stale_index["index"], sha256="b" * 64)
        with self.assertRaises(ValidationError):
            ta.validate_companion(stale_index, root=self.store_root, store=self.store)
        stale_bundle = copy.deepcopy(companion)
        stale_bundle["cases"][0]["bundle"] = dict(
            stale_bundle["cases"][0]["bundle"], sha256="c" * 64
        )
        with self.assertRaises(ValidationError):
            ta.validate_companion(stale_bundle, root=self.store_root, store=self.store)
        foreign = copy.deepcopy(companion)
        foreign["registry"] = dict(foreign["registry"], token="tr1-" + "d" * 64)
        with self.assertRaises(ValidationError):
            ta.validate_companion(foreign, root=self.store_root, store=self.store)

    def test_non_canonical_bundle_bytes_rejected(self):
        envelope = self.run_cases()
        companion, _ = self.build_surface(envelope)
        case = companion["cases"][0]
        bundle = loads(read_reference(self.store_root, case["bundle"]))
        loose = json.dumps(bundle).encode() + b"\n"
        loose_reference = dict(
            ref="trace-bundles/loose.json",
            sha256=hashlib.sha256(loose).hexdigest(),
            size_bytes=len(loose),
        )
        (self.store_root / "trace-bundles/loose.json").write_bytes(loose)
        patched = copy.deepcopy(companion)
        patched["cases"][0]["bundle"] = loose_reference
        with self.assertRaisesRegex(ValidationError, "canonical"):
            ta.validate_companion(patched, root=self.store_root, store=self.store)

    def test_verification_only_recheck_without_site_packages(self):
        envelope = self.run_cases()
        companion, _ = self.build_surface(envelope)
        before = {
            p: (p.read_bytes(), p.stat().st_mtime_ns)
            for p in self.store_root.rglob("*") if p.is_file()
        }
        code = (
            "import sys, json;"
            "sys.path.insert(0, 'src');"
            "from torchsynth_voice import trace_artifacts as ta;"
            "from torchsynth_voice.storage import ArtifactStore;"
            "companion = json.loads(sys.argv[2]);"
            "stats = ta.validate_companion(companion, root=sys.argv[1],"
            " store=ArtifactStore(sys.argv[1]));"
            "assert stats['bundles'] == 2, stats;"
            "assert not {'numpy', 'torch'} & set(sys.modules), sorted(sys.modules)"
        )
        result = subprocess.run(
            [
                sys.executable, "-S", "-c", code,
                str(self.store_root), json.dumps(companion),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        after = {
            p: (p.read_bytes(), p.stat().st_mtime_ns)
            for p in self.store_root.rglob("*") if p.is_file()
        }
        self.assertEqual(before, after)

    def test_interrupted_publication_preserves_prior_valid_surface(self):
        envelope = self.run_cases()
        companion, bundles = self.build_surface(envelope)
        reference = ta.companion_reference(companion)
        published = (self.store_root / reference["ref"]).read_bytes()
        original = ta.write_once

        def interrupt(path, data):
            if "trace-companions/" in str(path):
                raise KeyboardInterrupt("synthetic publication interruption")
            original(path, data)

        with patch.object(ta, "write_once", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                ta.publish_companion_surface(self.store_root, bundles, companion)
        self.assertEqual(
            (self.store_root / reference["ref"]).read_bytes(), published
        )
        ta.validate_companion(companion, root=self.store_root, store=self.store)
        completed = self.store.discover()
        self.assertEqual(len(completed), 2)


class VariantLinkageTests(SurfaceMixin):
    def run_plain(self):
        return run_corpus(
            self.store_root,
            self.manifest,
            base_template(),
            lambda request, store: render_artifact(
                request, store, FakeBackend()
            ),
            indices=[0, 1],
        )

    def test_variants_link_through_identity_with_identical_audio(self):
        traced = self.run_cases()
        plain = self.run_plain()
        companion, _ = self.build_surface(traced)
        linked = copy.deepcopy(companion)
        linked["variant_index"] = dict(plain["index"])
        stats = ta.validate_companion(linked, root=self.store_root, store=self.store)
        self.assertEqual(stats["audio_bytes_rehashed"], 2 * 705600)

    def test_variant_must_be_a_distinct_render(self):
        traced = self.run_cases()
        self.run_plain()
        companion, _ = self.build_surface(traced)
        linked = copy.deepcopy(companion)
        linked["variant_index"] = dict(companion["index"])
        with self.assertRaisesRegex(ValidationError, "distinct renders"):
            ta.validate_companion(linked, root=self.store_root, store=self.store)


class HoldoutGateTests(unittest.TestCase):
    def test_holdout_refused_before_store_or_renderer_access(self):
        render = Mock()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "store"
            with self.assertRaises(ValidationError):
                run_corpus(
                    root,
                    ROOT / "spec/reference/corpus-v0.json",
                    traced_template(names=SUBSET),
                    render,
                    indices=[96],
                )
            render.assert_not_called()
            self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()
