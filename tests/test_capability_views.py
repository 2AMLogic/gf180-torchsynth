"""Generated progress is evidence state, never issue completion or a quality score."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.capabilities import (  # noqa: E402
    CHECKS,
    EVALUATOR_INPUTS,
    CapabilityError,
    Result,
    evaluate,
    load_graph,
)
from torchsynth_voice.capability_views import (  # noqa: E402
    BEGIN,
    END,
    STATES,
    render_json,
    render_readme,
    replace_readme,
)


class CapabilityViewTests(unittest.TestCase):
    def setUp(self):
        self.graph = load_graph(ROOT / "spec/capabilities-v1.json")
        self.results = evaluate(self.graph, ROOT)

    def test_all_states_counts_details_and_exclusions_are_preserved(self):
        # Renderer-only inputs; not evidence, never written to project records.
        for item, state in zip(self.graph["nodes"], STATES):
            self.results[item["id"]] = Result(state, "NO VERDICT", ("reason",), False)
        report = json.loads(render_json(self.graph, self.results))
        self.assertEqual(set(report["counts"]), set(STATES))
        self.assertEqual(sum(report["counts"].values()), len(self.graph["nodes"]))
        self.assertFalse(report["evidence_healthy"])
        indexed = {node["id"]: node for node in report["nodes"]}
        for declaration in self.graph["nodes"]:
            result = self.results[declaration["id"]]
            rendered = indexed[declaration["id"]]
            self.assertEqual(rendered["state"], result.state)
            self.assertEqual(rendered["local_state"], result.local_state)
            self.assertEqual(rendered["reasons"], list(result.reasons))
            self.assertEqual(
                rendered["dependencies"], sorted(declaration["dependencies"])
            )
            for field in ("claim_class", "exclusions", "evidence"):
                self.assertEqual(rendered[field], declaration[field])
        summary = render_readme(self.graph, self.results)
        for state in STATES:
            self.assertIn(state, summary)
        self.assertIn("not a completion percentage", summary)
        self.assertNotIn("100%", summary)

    def test_views_are_independent_of_input_order_and_have_no_time_or_head(self):
        other = copy.deepcopy(self.graph)
        other["nodes"].reverse()
        for node in other["nodes"]:
            node["dependencies"].reverse()
        for renderer in (render_json, render_readme):
            self.assertEqual(
                renderer(self.graph, self.results), renderer(other, self.results)
            )
        report = json.loads(render_json(self.graph, self.results))
        self.assertNotIn("generated_at", report)
        self.assertNotIn("head_sha", report)

    def test_marker_replacement_preserves_every_byte_outside_block(self):
        prefix = "# Café\r\nManual status explanation.\r\n".encode()
        suffix = b"\r\nHandwritten footer\r\n"
        original = prefix + BEGIN.encode() + b"\r\nstale\r\n" + END.encode() + suffix
        block = render_readme(self.graph, self.results)
        result = replace_readme(original, block)
        self.assertTrue(result.startswith(prefix + BEGIN.encode()))
        self.assertTrue(result.endswith(END.encode() + suffix))
        self.assertEqual(replace_readme(result, block), result)

    def test_missing_duplicate_reversed_or_inline_markers_fail_closed(self):
        for bad in (
            "plain text",
            BEGIN,
            END,
            END + "\n" + BEGIN,
            BEGIN + "\n" + BEGIN + "\n" + END,
            BEGIN + "\n" + END + "\n" + END,
            "prefix " + BEGIN + "\n" + END,
            BEGIN + "\n" + END + " suffix",
        ):
            with self.subTest(bad=bad), self.assertRaises(CapabilityError):
                replace_readme(bad.encode(), "generated\n")

    def test_project_claims_are_not_inferred_without_attached_evidence(self):
        graph = copy.deepcopy(self.graph)
        for node in graph["nodes"]:
            node["evidence"] = None
        results = evaluate(graph, ROOT)
        report = json.loads(render_json(graph, results))
        self.assertEqual(report["counts"]["PASS"], 0)
        self.assertTrue(report["evidence_healthy"])
        self.assertIn("READY is unrun", render_readme(graph, results))

    def test_committed_views_match_the_same_resolver(self):
        self.assertEqual(
            (ROOT / "docs/capabilities.json").read_text(),
            render_json(self.graph, self.results),
        )
        readme = (ROOT / "README.md").read_bytes()
        self.assertEqual(
            readme, replace_readme(readme, render_readme(self.graph, self.results))
        )


class CapabilityViewCliTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "spec").mkdir()
        graph = load_graph(ROOT / "spec/capabilities-v1.json")
        for node in graph["nodes"]:
            node["evidence"] = None
        (self.root / "spec/capabilities-v1.json").write_text(json.dumps(graph))
        (self.root / "README.md").write_text(BEGIN + "\nold\n" + END + "\n")
        self.command = [
            sys.executable,
            "-S",
            str(ROOT / "tools/compile_capabilities.py"),
            "--root",
            str(self.root),
        ]
        self.paths = ("docs/CAPABILITIES.md", "docs/capabilities.json", "README.md")

    def run_compiler(self, *args):
        return subprocess.run(
            self.command + list(args), capture_output=True, text=True, timeout=30
        )

    def test_check_detects_each_view_without_writes_and_generation_is_idempotent(self):
        self.assertEqual(self.run_compiler().returncode, 0)
        before = {path: (self.root / path).stat().st_mtime_ns for path in self.paths}
        self.assertEqual(self.run_compiler().returncode, 0)
        self.assertEqual(
            before, {path: (self.root / path).stat().st_mtime_ns for path in self.paths}
        )
        self.assertEqual(self.run_compiler("--check").returncode, 0)
        self.assertEqual(self.run_compiler("--strict").returncode, 0)
        for path in self.paths:
            target = self.root / path
            original = target.read_bytes()
            target.write_bytes(original.replace(b"BLOCKED", b"PASS", 1))
            changed = target.read_bytes()
            result = self.run_compiler("--check")
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn(path, result.stderr)
            self.assertEqual(target.read_bytes(), changed)
            target.write_bytes(original)

    def test_unhealthy_evidence_can_be_published_but_strict_still_refuses(self):
        path = self.root / "spec/capabilities-v1.json"
        graph = json.loads(path.read_text())
        graph["nodes"][0]["evidence"] = {"path": "missing.json", "sha256": "0" * 64}
        path.write_text(json.dumps(graph))
        self.assertEqual(self.run_compiler().returncode, 0)
        self.assertEqual(self.run_compiler("--check").returncode, 0)
        report = json.loads((self.root / "docs/capabilities.json").read_text())
        self.assertFalse(report["evidence_healthy"])
        self.assertEqual(report["counts"]["PASS"], 0)
        result = self.run_compiler("--strict")
        self.assertEqual(result.returncode, 1)
        self.assertIn("NOT RUN", result.stderr)

    def test_bad_readme_is_refused_before_any_output_write(self):
        (self.root / "README.md").write_bytes(b"no markers\n")
        result = self.run_compiler()
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.root / "docs").exists())
        self.assertEqual((self.root / "README.md").read_bytes(), b"no markers\n")

    def test_repository_agreement_tests_allow_correctly_displayed_unhealthy_state(self):
        # Exercise the actual tests used before automatic publication, rather
        # than only asserting the compiler accepts an honest red view.
        for relative in (
            EVALUATOR_INPUTS
            + CHECKS["capability-compiler-v1"].inputs
            + ("tests/test_capability_views.py",)
        ):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        graph_path = self.root / "spec/capabilities-v1.json"
        graph = json.loads(graph_path.read_text())
        graph["nodes"][0]["evidence"] = {"path": "missing.json", "sha256": "0" * 64}
        graph_path.write_text(json.dumps(graph))
        self.assertEqual(self.run_compiler().returncode, 0)
        result = subprocess.run(
            [
                sys.executable,
                "-S",
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-p",
                "test_capabilit*.py",
                "-k",
                "committed_view",
                "-v",
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Ran 2 tests", result.stderr)
        self.assertEqual(self.run_compiler("--strict").returncode, 1)

    def test_output_collision_and_escape_are_refused(self):
        for args in (
            ("--output", "README.md"),
            ("--json-output", "spec/capabilities-v1.json"),
            ("--output", "../outside.md"),
        ):
            with self.subTest(args=args):
                self.assertEqual(self.run_compiler(*args).returncode, 1)
        self.assertFalse((self.root / "docs").exists())


if __name__ == "__main__":
    unittest.main()
