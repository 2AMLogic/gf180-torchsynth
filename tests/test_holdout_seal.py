"""Holdout seal gate controls (issue #55). Synthetic payloads only.

No holdout identity is rendered, no holdout artifact is read, and no real
ledger is written. Git-based checks run against throwaway repositories built
in a temporary directory.
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import check_holdout_seal  # noqa: E402
import render_corpus  # noqa: E402
from torchsynth_voice import holdout_seal as hs  # noqa: E402
from torchsynth_voice.artifacts import ValidationError  # noqa: E402
from torchsynth_voice.artifact_renderer import json_bytes, render_artifact  # noqa: E402
from torchsynth_voice.corpus import run_corpus, select_cases  # noqa: E402
from test_artifact_renderer import FakeBackend, template  # noqa: E402

CORPUS = ROOT / "spec/reference/corpus-v0.json"
# Any edit to the committed seal manifest changes this and fails review/CI.
# Regenerate with tools/check_holdout_seal.py --generate COMMIT only through a
# reviewed change that also explains why the freeze moved.
COMMITTED_SEAL_SHA256 = "d36b6b1a5363e4c627da5f8bb9ecc387423a4a48fa1244de18e0a58d08d4b49d"
INDICES = list(range(96, 128))


def git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
    )


class CommittedSealTests(unittest.TestCase):
    """Offline checks of the real committed seal (no git history required)."""

    def load(self):
        return hs._load_seal(ROOT, hs.SEAL_PATH)

    def test_seal_manifest_hash_is_pinned(self):
        _, sha = self.load()
        self.assertEqual(sha, COMMITTED_SEAL_SHA256)

    def test_pinned_files_and_model_match_current_bytes(self):
        seal, _ = self.load()
        self.assertEqual(hs.check_pinned_hashes(ROOT, seal), [])
        self.assertEqual(seal["holdout"]["holdout_identities_read"], 0)

    def test_pinned_files_tamper_is_detected(self):
        seal, _ = self.load()
        for mutate in (
            lambda s: s["files"].update({hs.RUBRIC_PATH: "0" * 64}),
            lambda s: s["model"]["files"].update({hs.MODEL_FILE: "0" * 64}),
            lambda s: s["model"].update(tree_sha256="0" * 64),
        ):
            copy = json.loads(json.dumps(seal))
            mutate(copy)
            self.assertTrue(hs.check_pinned_hashes(ROOT, copy))

    def test_seal_regenerates_from_pinned_commit_when_history_present(self):
        seal, _ = self.load()
        probe = subprocess.run(
            ["git", "cat-file", "-e", seal["pinned_commit"] + "^{commit}"],
            cwd=str(ROOT),
            capture_output=True,
        )
        if probe.returncode != 0:
            self.skipTest("pinned commit not present in this clone")
        self.assertEqual(hs.build_seal(ROOT, seal["pinned_commit"]), seal)


class GateFixture(unittest.TestCase):
    """A throwaway repository with synthetic pinned files and an origin."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name).resolve()
        self.repo = base / "repo"
        self.ledger = base / "ledger"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        files = {
            hs.RUBRIC_PATH: json.dumps({"synthetic": "rubric"}),
            hs.CORPUS_PATH: "corpus",
            hs.REGISTRY_PATH: "registry",
            hs.GOLDEN_PATH: "golden",
            hs.MODEL_FILE: "model = 1\n",
            hs.MODEL_PACKAGE + "/__init__.py": "",
            hs.MODEL_PACKAGE + "/ops.py": "ops = 1\n",
            hs.UPSTREAM_PATH: json.dumps({"target_commit": "a" * 40}),
        }
        for path, text in files.items():
            self.write(path, text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        self.seal = hs.build_seal(self.repo, "HEAD")
        self.write_seal(self.seal)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "seal")
        git(base, "clone", "-q", "--bare", str(self.repo), str(base / "origin.git"))
        git(self.repo, "remote", "add", "origin", str(base / "origin.git"))
        git(self.repo, "fetch", "-q", "origin")
        self.frozen = base / "frozen.json"
        self.write_frozen({"synthetic": "rubric"})

    def write(self, path, text):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def write_seal(self, seal):
        self.write(hs.SEAL_PATH, json.dumps(seal, indent=2))

    def write_frozen(self, rubric, **fields):
        document = dict(
            schema=hs.FROZEN_SCHEMA, schema_version=1, frozen=True, rubric=rubric
        )
        document.update(fields)
        self.frozen.write_text(json.dumps(document))

    def manifest_sha(self):
        return self.seal["files"][hs.CORPUS_PATH]

    def gate(self, **overrides):
        options = dict(
            indices=INDICES,
            corpus_manifest_sha256=self.manifest_sha(),
            frozen_rubric=self.frozen,
            audit_root=self.ledger,
            rubric_validator=lambda root: (True, ""),
        )
        options.update(overrides)
        return hs.verify_holdout_seal(self.repo, **options)

    def failed(self, **overrides):
        with self.assertRaises(hs.SealRefusal) as caught:
            self.gate(**overrides)
        return {c["check"] for c in caught.exception.checks if not c["ok"]}

    def commit_all(self, message="change"):
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", message)
        git(self.repo, "push", "-q", "origin", "HEAD:main")
        git(self.repo, "fetch", "-q", "origin")


class SealGateTests(GateFixture):
    def test_positive_control_verifies_without_writing(self):
        report = self.gate()
        self.assertEqual(report["status"], "verified")
        self.assertTrue(all(c["ok"] for c in report["checks"]))
        self.assertFalse(self.ledger.exists())

    def test_modified_rubric_byte_refused(self):
        self.write(hs.RUBRIC_PATH, json.dumps({"synthetic": "rubric "}))
        self.commit_all()
        self.assertIn("pinned-hashes", self.failed())

    def test_modified_model_hash_refused(self):
        self.write(hs.MODEL_PACKAGE + "/ops.py", "ops = 2\n")
        self.commit_all()
        self.assertIn("pinned-hashes", self.failed())

    def test_added_model_file_refused(self):
        self.write(hs.MODEL_PACKAGE + "/extra.py", "x = 1\n")
        self.commit_all()
        self.assertIn("pinned-hashes", self.failed())

    def test_pinned_commit_not_an_ancestor_refused(self):
        git(self.repo, "checkout", "-q", "-b", "side")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "side")
        side = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(self.repo), capture_output=True, text=True
        ).stdout.strip()
        git(self.repo, "checkout", "-q", "main")
        self.write_seal(dict(self.seal, pinned_commit=side))
        self.commit_all()
        failed = self.failed()
        self.assertIn("pinned-commit-ancestor-of-head", failed)
        self.assertIn("pinned-commit-ancestor-of-origin-main", failed)

    def test_pinned_commit_missing_refused(self):
        self.write_seal(dict(self.seal, pinned_commit="b" * 40))
        self.commit_all()
        self.assertIn("pinned-commit-exists", self.failed())

    def test_pinned_commit_not_on_origin_main_refused(self):
        git(self.repo, "checkout", "-q", "-b", "local")
        self.write(hs.MODEL_FILE, "model = 9\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "unpublished")
        local = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(self.repo), capture_output=True, text=True
        ).stdout.strip()
        seal = hs.build_seal(self.repo, local)
        self.write_seal(seal)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "seal")
        self.assertIn("pinned-commit-ancestor-of-origin-main", self.failed(
            corpus_manifest_sha256=seal["files"][hs.CORPUS_PATH]
        ))

    def test_dirty_pinned_file_refused(self):
        self.write(hs.REGISTRY_PATH, "registry dirty")
        failed = self.failed()
        self.assertIn("pinned-paths-clean", failed)
        self.assertIn("pinned-hashes", failed)

    def test_untracked_model_file_refused(self):
        self.write(hs.MODEL_PACKAGE + "/scratch.py", "x = 1\n")
        self.assertIn("pinned-paths-clean", self.failed())

    def test_seal_tamper_refused(self):
        tampered = json.loads(json.dumps(self.seal))
        tampered["files"][hs.GOLDEN_PATH] = "0" * 64
        self.write_seal(tampered)
        self.commit_all()
        failed = self.failed()
        self.assertIn("pinned-hashes", failed)
        self.assertIn("pinned-commit-content", failed)

    def test_malformed_seal_refused(self):
        self.write_seal(dict(self.seal, holdout={"first_index": 0}))
        self.assertEqual(self.failed(), {"seal-manifest"})

    def test_upstream_pin_mismatch_refused(self):
        self.write_seal(dict(self.seal, upstream_pin="c" * 40))
        self.commit_all()
        self.assertIn("upstream-pin", self.failed())

    def test_rubric_validator_failure_refused(self):
        self.assertIn(
            "rubric-validator",
            self.failed(rubric_validator=lambda root: (False, "boom")),
        )

    def test_frozen_rubric_content_mismatch_refused(self):
        self.write_frozen({"synthetic": "rubric", "tuned": 1})
        self.assertIn("frozen-rubric-equals-committed", self.failed())

    def test_frozen_rubric_unfrozen_or_extra_field_refused(self):
        self.write_frozen({"synthetic": "rubric"}, frozen=False)
        self.assertIn("frozen-rubric-equals-committed", self.failed())
        self.write_frozen({"synthetic": "rubric"}, note="x")
        self.assertIn("frozen-rubric-equals-committed", self.failed())

    def test_frozen_rubric_missing_refused(self):
        self.frozen.unlink()
        self.assertIn("frozen-rubric-equals-committed", self.failed())

    def test_wrong_corpus_manifest_refused(self):
        self.assertIn(
            "corpus-manifest-pinned", self.failed(corpus_manifest_sha256="0" * 64)
        )

    def test_preexisting_ledger_entry_refused(self):
        self.ledger.mkdir()
        (self.ledger / (self.manifest_sha() + ".json")).write_text("{}")
        self.assertIn("ledger-state", self.failed())
        # The only exception is explicit same-run resume.
        self.assertEqual(self.gate(resume=True)["status"], "verified")

    def test_resume_without_ledger_refused(self):
        self.assertIn("ledger-state", self.failed(resume=True))

    def test_ledger_inside_repository_refused(self):
        self.assertIn(
            "ledger-outside-repository", self.failed(audit_root=self.repo / "ledger")
        )

    def test_wrong_index_sets_refused(self):
        wrong = (
            INDICES[:-1],
            INDICES + [128],
            list(range(95, 127)),
            list(range(0, 96)),
            [96],
            None,
            INDICES[:-1] + [0],
        )
        for indices in wrong:
            with self.subTest(indices=indices):
                self.assertIn("index-set", self.failed(indices=indices))
        self.assertEqual(self.gate(indices=INDICES[::-1])["status"], "verified")

    def test_every_check_is_reported_even_after_first_failure(self):
        with self.assertRaises(hs.SealRefusal) as caught:
            self.gate(indices=[96], corpus_manifest_sha256="0" * 64)
        failed = {c["check"] for c in caught.exception.checks if not c["ok"]}
        self.assertEqual(failed, {"index-set", "corpus-manifest-pinned"})


class GateSkippedTests(unittest.TestCase):
    """The unseal path cannot bypass the gate."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.directory = Path(tmp.name).resolve()
        self.store = self.directory / "store"
        self.ledger = self.directory / "ledger"
        self.backend = FakeBackend()
        self.freeze = self.directory / "frozen.json"
        self.freeze.write_bytes(
            json_bytes(
                dict(
                    schema=hs.FROZEN_SCHEMA,
                    schema_version=1,
                    frozen=True,
                    rubric={"synthetic-threshold": 0},
                )
            )
        )

    def run_holdout(self, **extra):
        return run_corpus(
            self.store,
            CORPUS,
            template(),
            lambda request, store: render_artifact(request, store, self.backend),
            indices=[96],
            mode="holdout",
            frozen_rubric=self.freeze,
            audit_root=self.ledger,
            **extra,
        )

    def assert_nothing_touched(self):
        self.assertEqual(self.backend.calls, [])
        self.assertFalse(self.store.exists())
        self.assertFalse(self.ledger.exists())

    def test_run_corpus_holdout_without_gate_refused(self):
        with self.assertRaisesRegex(ValidationError, "seal verification gate"):
            self.run_holdout()
        with self.assertRaisesRegex(ValidationError, "seal verification gate"):
            self.run_holdout(seal_gate=None)
        self.assert_nothing_touched()

    def test_refusing_gate_blocks_admission_store_and_renderer(self):
        def refuse(context):
            raise hs.SealRefusal([dict(check="synthetic", ok=False, detail="")])

        with self.assertRaises(hs.SealRefusal):
            self.run_holdout(seal_gate=refuse)
        self.assert_nothing_touched()

    def test_gate_receives_verified_context(self):
        seen = []
        result = self.run_holdout(seal_gate=seen.append)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["indices"], [96])
        self.assertFalse(seen[0]["resume"])
        self.assertEqual(len(seen[0]["manifest_sha256"]), 64)

    def test_development_run_rejects_a_gate_and_holdout_options(self):
        with self.assertRaises(ValidationError):
            run_corpus(
                self.store,
                CORPUS,
                template(),
                lambda request, store: None,
                indices=[0],
                seal_gate=lambda context: None,
            )
        self.assert_nothing_touched()

    def run_cli(self, *argv):
        out = io.StringIO()
        with patch.object(
            render_corpus, "DockerBackend", side_effect=AssertionError("renderer built")
        ), contextlib.redirect_stdout(out):
            code = render_corpus.main(["--store", str(self.store), *argv])
        return code, json.loads(out.getvalue())

    def test_cli_unseal_refused_before_renderer_construction(self):
        code, report = self.run_cli(
            "--holdout-once",
            "--indices",
            "96",
            "--frozen-rubric",
            str(self.freeze),
            "--holdout-audit-root",
            str(self.ledger),
        )
        self.assertEqual(code, 2)
        self.assertEqual(report["status"], "refused")
        failed = {c["check"] for c in report["checks"] if not c["ok"]}
        self.assertIn("index-set", failed)
        self.assertIn("frozen-rubric-equals-committed", failed)
        self.assert_nothing_touched()

    def test_standalone_tool_exit_codes(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = check_holdout_seal.main(
                [
                    "--indices",
                    "96",
                    "--frozen-rubric",
                    str(self.freeze),
                    "--holdout-audit-root",
                    str(self.ledger),
                ]
            )
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out.getvalue())["status"], "refused")
        self.assertFalse(self.ledger.exists())


class DevelopmentPathGuardTests(unittest.TestCase):
    """Fast-suite guard: development tooling cannot reach holdout (#55)."""

    # Only these may mention holdout ledgers, freeze records or holdout modes.
    HOLDOUT_AWARE = {
        "src/torchsynth_voice/corpus.py",
        "src/torchsynth_voice/holdout_seal.py",
        "src/torchsynth_voice/case_registry.py",
        "tools/render_corpus.py",
        "tools/check_holdout_seal.py",
        "tools/render_scorecard.py",
    }
    MARKERS = re.compile(
        r"holdout_audit_root|holdout-audit-root|frozen_rubric|frozen-rubric|"
        r"mode=\"holdout\"|--holdout-once|allow_holdout|allow-holdout|seal_gate"
    )
    DEVELOPMENT_TOOLS = (
        "tools/audit_corpus_coverage.py",
        "tools/audit_development_corpus.py",
        "tools/compare_development_corpus.py",
        "tools/measure_development_trace_cost.py",
        "tools/validate_rubric.py",
    )

    def sources(self):
        for pattern in ("tools/*.py", "src/torchsynth_voice/**/*.py"):
            for path in sorted(ROOT.glob(pattern)):
                yield path.relative_to(ROOT).as_posix(), path.read_text()

    def test_only_allowlisted_modules_reference_holdout_controls(self):
        offenders = [
            name
            for name, text in self.sources()
            if name not in self.HOLDOUT_AWARE
            and name != "tests"
            and self.MARKERS.search(text)
        ]
        self.assertEqual(offenders, [])

    def test_development_tools_never_range_over_holdout_indices(self):
        names = list(self.DEVELOPMENT_TOOLS) + sorted(
            p.relative_to(ROOT).as_posix() for p in ROOT.glob("tools/generate_*.py")
        )
        for name in names:
            tree = ast.parse((ROOT / name).read_text())
            for node in ast.walk(tree):
                if not (
                    isinstance(node, ast.Call)
                    and getattr(node.func, "id", None) == "range"
                    and all(
                        isinstance(a, ast.Constant) and type(a.value) is int
                        for a in node.args
                    )
                ):
                    continue
                values = [a.value for a in node.args]
                low, high = (0, values[0]) if len(values) == 1 else values[:2]
                self.assertFalse(
                    low < 128 and high > 96,
                    "%s:%d range(%s) reaches holdout indices 96-127"
                    % (name, node.lineno, values),
                )

    def test_development_tools_do_not_name_holdout_store_or_ledger_paths(self):
        for name in self.DEVELOPMENT_TOOLS + tuple(
            p.relative_to(ROOT).as_posix() for p in ROOT.glob("tools/generate_*.py")
        ):
            text = (ROOT / name).read_text()
            self.assertIsNone(
                re.search(r"holdout[-_](store|ledger|audit)", text), name
            )

    def test_scorecard_default_partition_is_development(self):
        text = (ROOT / "tools/render_scorecard.py").read_text()
        self.assertIn('choices=("development", "holdout"), default="development"', text)
        with self.assertRaises(SystemExit):
            import render_scorecard

            with contextlib.redirect_stderr(io.StringIO()):
                render_scorecard.main(["--partition", "holdout", "--check"])

    def test_development_selection_refuses_every_holdout_index(self):
        for index in INDICES:
            with self.assertRaises(ValidationError):
                select_cases(CORPUS, indices=[index])
            with self.assertRaises(ValidationError):
                select_cases(CORPUS, indices=[0, index])
        self.assertEqual(
            [c["sound_index"] for c in select_cases(CORPUS)], list(range(96))
        )

    def test_development_run_with_holdout_index_touches_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "store"
            backend = FakeBackend()
            with self.assertRaises(ValidationError):
                run_corpus(
                    store,
                    CORPUS,
                    template(),
                    lambda request, s: render_artifact(request, s, backend),
                    indices=[96],
                )
            self.assertFalse(store.exists())
            self.assertEqual(backend.calls, [])

    def test_coverage_audit_refuses_holdout_before_reading_data(self):
        from audit_corpus_coverage import audit_corpus_coverage

        with self.assertRaisesRegex(ValidationError, "holdout"):
            audit_corpus_coverage(
                "/nonexistent/first",
                "/nonexistent/second",
                first_store="/nonexistent/a",
                second_store="/nonexistent/b",
                rules={},
                expected=[95, 96],
            )


if __name__ == "__main__":
    unittest.main()
