"""Synthetic corpus controls; no TorchSynth or holdout data is loaded."""

from __future__ import annotations

import sys
import tempfile
import unittest
import copy
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.artifacts import ValidationError, loads  # noqa: E402
from torchsynth_voice.artifact_renderer import json_bytes, render_artifact  # noqa: E402
from torchsynth_voice.corpus import (  # noqa: E402
    run_corpus,
    select_cases,
    verify_run,
    validate_run,
    read_reference,
)
from test_artifact_renderer import FakeBackend, template  # noqa: E402


class AdmissionTests(unittest.TestCase):
    def test_default_has_only_development(self):
        cases = select_cases(ROOT / "spec/reference/corpus-v0.json")
        self.assertEqual([c["sound_index"] for c in cases], list(range(96)))

    def test_mixed_holdout_refused(self):
        with self.assertRaisesRegex(ValidationError, "holdout"):
            select_cases(ROOT / "spec/reference/corpus-v0.json", indices=[95, 96])

    def test_missing_verification_root_remains_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "absent"
            with self.assertRaises((ValidationError, OSError)):
                verify_run(root, "a" * 32)
            self.assertFalse(root.exists())

    def test_manifest_duplicate_missing_extra_and_counts(self):
        data = loads((ROOT / "spec/reference/corpus-v0.json").read_bytes())
        for mutate in (
            lambda x: x["cases"].append(x["cases"][0]),
            lambda x: x["cases"][0].update(stop_exclusive=95),
            lambda x: x["cases"][1].update(stop_exclusive=129),
            lambda x: x["rules"].update(case_count=127),
        ):
            modified = copy.deepcopy(data)
            mutate(modified)
            with self.assertRaises(ValidationError):
                select_cases(modified)
        for indices in ([0, 0], [128], [-1], [True], []):
            with self.assertRaises(ValidationError):
                select_cases(data, indices=indices)


class CorpusTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()
        self.root = self.directory / "store"
        self.manifest = ROOT / "spec/reference/corpus-v0.json"
        self.template = template()
        self.backend = FakeBackend()

    def renderer(self, request, store):
        return render_artifact(request, store, self.backend)

    def run_cases(self, **kwargs):
        return run_corpus(
            self.root,
            self.manifest,
            self.template,
            self.renderer,
            indices=kwargs.pop("indices", [0, 1]),
            **kwargs,
        )

    def documents(self, envelope):
        return (
            loads(read_reference(self.root, envelope["plan"])),
            loads(read_reference(self.root, envelope["index"])),
        )

    def test_two_case_resume_zero_recomputation_and_stable_index(self):
        first = self.run_cases()
        self.assertEqual(
            first["counts"],
            dict(
                expected=2,
                observed=2,
                success=2,
                failure=0,
                attempt=2,
                retry=0,
                resume=0,
            ),
        )
        stats = {
            p: (p.read_bytes(), p.stat().st_mtime_ns)
            for p in (self.root / "artifacts").rglob("*")
            if p.is_file()
        }
        again = self.run_cases(resume=first["run_id"])
        self.assertEqual(len(self.backend.calls), 2)
        self.assertEqual(again["index"], first["index"])
        self.assertEqual(again["counts"]["resume"], 2)
        self.assertEqual(verify_run(self.root, first["run_id"]), again)
        self.assertEqual(
            stats, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in stats}
        )
        bad = copy.deepcopy(again)
        bad["attempts"][-1]["artifact"] = dict(
            bad["attempts"][-1]["artifact"], sha256="a" * 64
        )
        plan, index = self.documents(again)
        index["cases"][-1]["artifact"]["sha256"] = "a" * 64
        with self.assertRaisesRegex(ValidationError, "exact artifact reference"):
            validate_run(bad, plan, index)

    def test_failed_case_denominator_and_retry_history(self):
        def fail_second(request, store):
            if request["fixture"]["sound_index"] == 1:
                raise RuntimeError("synthetic injected failure")
            return self.renderer(request, store)

        first = run_corpus(
            self.root, self.manifest, self.template, fail_second, indices=[0, 1]
        )
        plan, index = self.documents(first)
        self.assertEqual(
            first["counts"],
            dict(
                expected=2,
                observed=1,
                success=1,
                failure=1,
                attempt=2,
                retry=0,
                resume=0,
            ),
        )
        self.assertIsNone(index["cases"][1]["artifact"])
        self.assertEqual(index["cases"][1]["failures"], ["render-RuntimeError"])
        self.assertEqual(verify_run(self.root, first["run_id"]), first)
        second = self.run_cases(resume=first["run_id"])
        self.assertEqual(
            second["counts"],
            dict(
                expected=2,
                observed=2,
                success=2,
                failure=0,
                attempt=3,
                retry=1,
                resume=1,
            ),
        )
        self.assertEqual(second["attempts"][1]["failure"], "render-RuntimeError")

    def test_stale_resume_refused_before_renderer(self):
        first = self.run_cases()
        self.template["project_git"]["commit"] = "c" * 40
        with self.assertRaisesRegex(ValidationError, "stale"):
            self.run_cases(resume=first["run_id"])
        self.assertEqual(len(self.backend.calls), 2)

    def test_corrupt_extra_missing_metadata_and_payload_refused(self):
        first = self.run_cases()
        _, index = self.documents(first)
        directory = self.root / Path(index["cases"][0]["artifact"]["ref"]).parent
        audio = directory / "audio.f32le"
        original = audio.read_bytes()
        audio.write_bytes(b"x" + original[1:])
        for operation in (
            lambda: verify_run(self.root, first["run_id"]),
            lambda: self.run_cases(resume=first["run_id"]),
        ):
            with self.assertRaises(ValidationError):
                operation()
        audio.write_bytes(original)
        extra = directory / "audition.wav"
        extra.write_bytes(b"undeclared")
        with self.assertRaises(ValidationError):
            verify_run(self.root, first["run_id"])
        extra.unlink()
        audio.unlink()
        with self.assertRaises(ValidationError):
            verify_run(self.root, first["run_id"])
        self.assertEqual(len(self.backend.calls), 2)

    def test_all_index_and_run_inconsistencies_rejected(self):
        envelope = self.run_cases()
        plan, index = self.documents(envelope)
        for key in envelope["counts"]:
            bad = copy.deepcopy(envelope)
            bad["counts"][key] += 1
            with self.assertRaises(ValidationError):
                validate_run(bad, plan, index, root=self.root)
        for mutate in (
            lambda x: x["cases"].pop(),
            lambda x: x["cases"].append(x["cases"][0]),
            lambda x: x["cases"][1].update(fixture=x["cases"][0]["fixture"]),
            lambda x: x.update(expected_case_count=3),
            lambda x: x.update(observed_case_count=1),
            lambda x: x["cases"][0].update(case_id="global-128"),
            lambda x: x["cases"][0]["artifact"].update(sha256="a" * 64),
        ):
            bad = copy.deepcopy(index)
            mutate(bad)
            with self.assertRaises(ValidationError):
                validate_run(envelope, plan, bad, root=self.root)

    def test_interrupted_index_publication_resumes_without_rendering(self):
        from torchsynth_voice import corpus

        original = corpus.write_once

        def interrupt(path, data):
            if Path(path).parent.name == "indexes":
                raise KeyboardInterrupt("synthetic publication interruption")
            original(path, data)

        with patch.object(corpus, "write_once", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_cases()
        run_id = next((self.root / "runs").iterdir()).name
        resumed = self.run_cases(resume=run_id)
        self.assertEqual(resumed["counts"]["resume"], 2)
        self.assertEqual(len(self.backend.calls), 2)

    def test_interrupted_attempt_retained_on_resume(self):
        def interrupt(request, store):
            raise KeyboardInterrupt("synthetic worker interruption")

        with self.assertRaises(KeyboardInterrupt):
            run_corpus(
                self.root, self.manifest, self.template, interrupt, indices=[0, 1]
            )
        run_id = next((self.root / "runs").iterdir()).name
        resumed = self.run_cases(resume=run_id)
        self.assertEqual(resumed["attempts"][0]["failure"], "interrupted-attempt")
        self.assertEqual(resumed["counts"]["retry"], 1)
        self.assertIsNone(resumed["elapsed_seconds"])

    def assert_interrupted_reuse_recovers(self, **options):
        from torchsynth_voice import corpus

        first = self.run_cases(**options)
        original = corpus.write_once
        before = {
            p: (p.read_bytes(), p.stat().st_mtime_ns)
            for p in self.root.rglob("*")
            if p.is_file()
        }

        def interrupt(path, data):
            if Path(path).name == "000002-finish.json":
                raise KeyboardInterrupt("synthetic reuse interruption")
            original(path, data)

        with patch.object(corpus, "write_once", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_cases(resume=first["run_id"], **options)
        resumed = self.run_cases(resume=first["run_id"], **options)
        self.assertEqual(len(self.backend.calls), 2)
        self.assertEqual(resumed["index"], first["index"])
        self.assertEqual(
            resumed["counts"],
            dict(
                expected=2,
                observed=2,
                success=2,
                failure=0,
                attempt=2,
                retry=0,
                resume=2,
            ),
        )
        interrupted = resumed["attempts"][2]
        self.assertEqual(interrupted["kind"], "resume")
        self.assertEqual(interrupted["failure"], "interrupted-attempt")
        self.assertIsNone(interrupted["artifact"])
        self.assertIsNone(interrupted["elapsed_seconds"])
        self.assertIsNone(resumed["elapsed_seconds"])
        self.assertEqual(
            resumed["attempts"][3]["artifact"], first["attempts"][0]["artifact"]
        )
        plan, index = self.documents(resumed)
        for changes in (
            {"failure": "render-ValidationError"},
            {"elapsed_seconds": 0},
            {"receipt": {"unexpected": True}},
        ):
            bad = copy.deepcopy(resumed)
            bad["attempts"][2].update(changes)
            with self.assertRaisesRegex(ValidationError, "invalid interrupted resume"):
                validate_run(bad, plan, index)
        bad = copy.deepcopy(resumed)
        bad["attempts"].pop(0)
        for sequence, event in enumerate(bad["attempts"]):
            event["sequence"] = sequence
        with self.assertRaisesRegex(ValidationError, "resume without a completed"):
            validate_run(bad, plan, index)
        self.assertEqual(
            verify_run(
                self.root,
                first["run_id"],
                allow_holdout=options.get("mode") == "holdout",
            ),
            resumed,
        )
        self.assertEqual(
            before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before}
        )
        return first

    def test_interrupted_reuse_retains_completed_artifact_without_rendering(self):
        self.assert_interrupted_reuse_recovers()

    def test_interrupted_holdout_reuse_retains_one_shot_admission(self):
        freeze = self.directory / "frozen.json"
        freeze.write_bytes(
            json_bytes(
                dict(
                    schema="torchsynth-frozen-rubric",
                    schema_version=1,
                    frozen=True,
                    rubric={"synthetic-threshold": 0},
                )
            )
        )
        options = dict(
            mode="holdout",
            frozen_rubric=freeze,
            audit_root=self.directory / "audit",
            indices=[96, 97],
        )
        first = self.assert_interrupted_reuse_recovers(**options)
        admission = read_reference(self.root, first["admission"])
        plan, _ = self.documents(first)
        ledger = options["audit_root"] / (plan["manifest"]["sha256"] + ".json")
        self.assertEqual(ledger.read_bytes(), admission)
        with self.assertRaisesRegex(ValidationError, "one-shot"):
            self.run_cases(**options)
        self.assertEqual(len(self.backend.calls), 2)

    def test_verification_without_site_packages_is_read_only(self):
        first = self.run_cases()
        before = {
            p: (p.read_bytes(), p.stat().st_mtime_ns)
            for p in self.root.rglob("*")
            if p.is_file()
        }
        code = "import sys;sys.path.insert(0,'src');from torchsynth_voice.corpus import verify_run;verify_run(sys.argv[1],sys.argv[2]);assert not {'numpy','torch','torchsynth'} & set(sys.modules)"
        result = subprocess.run(
            [sys.executable, "-S", "-c", code, str(self.root), first["run_id"]],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before}
        )

    def test_holdout_admission_one_shot_and_same_run_resume_synthetic_only(self):
        freeze = self.directory / "frozen.json"
        freeze.write_bytes(
            json_bytes(
                dict(
                    schema="torchsynth-frozen-rubric",
                    schema_version=1,
                    frozen=True,
                    rubric={"synthetic-threshold": 0},
                )
            )
        )
        audit = self.directory / "audit"
        for options in (
            {},
            {"mode": "holdout"},
            {"mode": "holdout", "frozen_rubric": freeze},
        ):
            with self.assertRaises(ValidationError):
                self.run_cases(indices=[96], **options)
        self.assertFalse(self.root.exists())
        self.assertEqual(self.backend.calls, [])
        options = dict(
            mode="holdout", frozen_rubric=freeze, audit_root=audit, indices=[96]
        )
        first = self.run_cases(**options)
        admission = read_reference(self.root, first["admission"])
        with self.assertRaisesRegex(ValidationError, "one-shot"):
            self.run_cases(**options)
        resumed = self.run_cases(resume=first["run_id"], **options)
        self.assertEqual(resumed["counts"]["resume"], 1)
        self.assertEqual(len(self.backend.calls), 1)
        self.assertEqual(read_reference(self.root, first["admission"]), admission)
        with self.assertRaisesRegex(ValidationError, "holdout"):
            verify_run(self.root, first["run_id"])
        self.assertEqual(
            verify_run(self.root, first["run_id"], allow_holdout=True), resumed
        )

    def test_mixed_range_refused_before_any_renderer_or_store_access(self):
        render = Mock()
        with self.assertRaisesRegex(ValidationError, "holdout"):
            run_corpus(self.root, self.manifest, self.template, render, indices=[0, 96])
        render.assert_not_called()
        self.assertFalse(self.root.exists())


if __name__ == "__main__":
    unittest.main()
