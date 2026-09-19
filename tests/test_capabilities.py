"""Synthetic compiler evidence is not a TorchSynth or hardware qualification."""

from __future__ import annotations

import copy
import hashlib
import json
import re
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
    coverage_hashes,
    evaluate,
    load_graph,
    node_digest,
    render_markdown,
    validate_graph,
    _structure,
    _validate,
)

LINE_TERMINATORS = ("\n", "\r", "\r\n", "\u2028", "\u2029")


def line_terminated_documents(graph, evidence):
    """Shared whole-document probes for native and independent schema checks."""
    for kind, original, paths in (
        (
            "graph",
            graph,
            (
                ("nodes", 0, "id"),
                ("nodes", 0, "claim"),
                ("nodes", 0, "exclusions", 0),
                ("nodes", 0, "coverage", "inputs", 0),
            ),
        ),
        (
            "evidence",
            evidence,
            (
                ("node_id",),
                ("node_sha256",),
                ("provenance", "project_commit"),
                ("provenance", "recorded_at"),
                ("provenance", "runtime_lock"),
                ("execution", "command", 0),
                ("execution", "log", "path"),
                ("result", "reason"),
            ),
        ),
    ):
        for path in paths:
            for ending in LINE_TERMINATORS:
                bad = copy.deepcopy(original)
                target = bad
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] += ending
                yield kind, path, ending, bad


def digest(data):
    return hashlib.sha256(data).hexdigest()


def node(name="compiler", dependencies=()):
    return {
        "id": name,
        "claim": "Synthetic compiler behavior only",
        "claim_class": "compiler-behavior",
        "dependencies": list(dependencies),
        "engine": "stdlib",
        "layer": "verification",
        "scope": "synthetic-only",
        "coverage": {"inputs": ["fixture"], "references": []},
        "check": "capability-compiler-v1",
        "negative_controls": {"stale-input": "hash-mismatch"},
        "exclusions": ["No hardware, audio, runtime, or fidelity qualification"],
        "expensive": False,
        "cheaper_predecessor": None,
        "evidence": None,
    }


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for path in EVALUATOR_INPUTS + CHECKS["capability-compiler-v1"].inputs:
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / path, target)
        (self.root / "fixture").mkdir()
        (self.root / "fixture/source.py").write_text("synthetic source\n")
        (self.root / "fixture/spec.txt").write_text("synthetic spec\n")
        (self.root / "fixture/tool.py").write_text("synthetic tool\n")
        (self.root / "fixture/runtime.lock").write_text("stdlib synthetic runtime\n")
        self.graph = {"schema_version": 1, "nodes": [node()]}

    def write_json(self, path, value):
        data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return {"path": path, "sha256": digest(data)}

    def evidence(self, target=None):
        target = target or self.graph["nodes"][0]
        log = self.write_json(f"runs/{target['id']}.log", {"synthetic": True})
        record = {
            "schema_version": 1,
            "node_id": target["id"],
            "node_sha256": node_digest(target),
            "provenance": {
                "upstream_commit": "2b0964d4c6c3d472a2a0d54d91b408caaeffca6d",
                "project_commit": "b" * 40,
                "producer": "synthetic-unittest",
                "run_id": "synthetic-" + target["id"],
                "recorded_at": "2026-09-18T00:00:00Z",
                "runtime_lock": "fixture/runtime.lock",
            },
            "inputs": coverage_hashes(target, self.root),
            "dependencies": {
                dep: next(n for n in self.graph["nodes"] if n["id"] == dep)["evidence"][
                    "sha256"
                ]
                for dep in target["dependencies"]
            },
            "execution": {
                "check": target["check"],
                "command": list(CHECKS[target["check"]].command),
                "engine": target["engine"],
                "layer": target["layer"],
                "scope": target["scope"],
                "executed": True,
                "exit_code": 0,
                "log": log,
            },
            "result": {"verdict": "PASS", "reason": "synthetic check passed"},
            "controls": {
                name: {"fault": fault, "executed": True, "detected": True, "log": log}
                for name, fault in target["negative_controls"].items()
            },
            "references": {},
        }
        self.attach(record, target)
        return record

    def attach(self, record, target=None):
        target = target or self.graph["nodes"][0]
        target["evidence"] = self.write_json(f"runs/{target['id']}.json", record)

    def state(self, name="compiler"):
        return evaluate(self.graph, self.root)[name].state

    def test_clean_pass_requires_complete_current_evidence(self):
        self.evidence()
        self.assertEqual(self.state(), "PASS")

    def test_all_seven_states(self):
        self.assertEqual(self.state(), "READY")
        record = self.evidence()
        self.assertEqual(self.state(), "PASS")
        record["result"]["verdict"] = "FAIL"
        self.attach(record)
        self.assertEqual(self.state(), "FAIL")
        record["result"]["verdict"] = "NO VERDICT"
        self.attach(record)
        self.assertEqual(self.state(), "NO VERDICT")
        record["execution"].update(executed=False, exit_code=None)
        self.attach(record)
        self.assertEqual(self.state(), "NOT RUN")
        (self.root / "fixture/source.py").write_text("changed\n")
        self.assertEqual(self.state(), "STALE")
        self.graph["nodes"].append(node("child", ["compiler"]))
        self.assertEqual(self.state("child"), "BLOCKED")

    def test_invalid_graphs(self):
        mutations = []
        duplicate = copy.deepcopy(self.graph)
        duplicate["nodes"].append(node())
        mutations.append(duplicate)
        unknown = copy.deepcopy(self.graph)
        unknown["nodes"][0]["dependencies"] = ["unknown"]
        mutations.append(unknown)
        cyclic = copy.deepcopy(self.graph)
        cyclic["nodes"][0]["dependencies"] = ["child"]
        cyclic["nodes"].append(node("child", ["compiler"]))
        mutations.append(cyclic)
        for field in node():
            missing = copy.deepcopy(self.graph)
            del missing["nodes"][0][field]
            mutations.append(missing)
        for bad in mutations:
            with self.subTest(bad=bad), self.assertRaises(CapabilityError):
                validate_graph(bad)

    def test_missing_evidence_and_existing_arbitrary_file_do_not_pass(self):
        target = self.graph["nodes"][0]
        target["evidence"] = {"path": "missing.json", "sha256": "a" * 64}
        self.assertEqual(self.state(), "NOT RUN")
        target["evidence"] = self.write_json("arbitrary.json", {"status": "PASS"})
        self.assertEqual(self.state(), "NO VERDICT")

    def test_failed_or_missing_control_does_not_pass(self):
        for change in ("missing", "not-detected", "not-executed", "wrong-fault"):
            record = self.evidence()
            if change == "missing":
                record["controls"] = {}
            else:
                key, value = {
                    "not-detected": ("detected", False),
                    "not-executed": ("executed", False),
                    "wrong-fault": ("fault", "different"),
                }[change]
                record["controls"]["stale-input"][key] = value
            self.attach(record)
            with self.subTest(change=change):
                self.assertNotEqual(self.state(), "PASS")

    def test_covered_dirty_and_untracked_bytes_invalidate_only_affected_nodes(self):
        other = node("unrelated")
        other["coverage"]["inputs"] = ["fixture/runtime.lock"]
        self.graph["nodes"].append(other)
        self.evidence(other)
        for path in ("source.py", "spec.txt", "tool.py", "untracked.txt"):
            self.evidence()
            (self.root / "fixture" / path).write_text("changed " + path)
            with self.subTest(path=path):
                self.assertEqual(self.state(), "STALE")
                self.assertEqual(self.state("unrelated"), "PASS")

    def test_downstream_pass_never_greens_prerequisite(self):
        self.evidence()
        child = node("child", ["compiler"])
        self.graph["nodes"].append(child)
        self.evidence(child)
        self.assertEqual(self.state("child"), "PASS")
        (self.root / "runs/compiler.json").write_text("tampered")
        results = evaluate(self.graph, self.root)
        self.assertEqual(results["compiler"].state, "STALE")
        self.assertEqual(results["child"].state, "BLOCKED")
        self.assertEqual(results["child"].local_state, "PASS")

    def test_replaced_prerequisite_evidence_stales_downstream(self):
        record = self.evidence()
        child = node("child", ["compiler"])
        self.graph["nodes"].append(child)
        self.evidence(child)
        record["provenance"]["run_id"] = "replacement-run"
        self.attach(record)
        self.assertEqual(self.state(), "PASS")
        self.assertEqual(self.state("child"), "STALE")

    def test_declared_scope_change_stales_only_that_node(self):
        self.evidence()
        other = node("other")
        self.graph["nodes"].append(other)
        self.evidence(other)
        self.graph["nodes"][0]["exclusions"].append("Newly clarified limitation")
        self.assertEqual(self.state(), "STALE")
        self.assertEqual(self.state("other"), "PASS")

    def test_evaluator_and_schema_bytes_are_implicitly_covered(self):
        for path in EVALUATOR_INPUTS:
            self.evidence()
            with (self.root / path).open("ab") as handle:
                handle.write(b"\n")
            with self.subTest(path=path):
                self.assertEqual(self.state(), "STALE")

    def test_omitted_coverage_and_forged_digests_are_refused(self):
        record = self.evidence()
        del record["inputs"]["fixture"]
        self.attach(record)
        self.assertEqual(self.state(), "NO VERDICT")
        record = self.evidence()
        record["inputs"]["fixture"] = "0" * 64
        self.attach(record)
        self.assertEqual(self.state(), "STALE")
        self.graph["nodes"][0]["evidence"]["sha256"] = "0" * 64
        self.assertEqual(self.state(), "STALE")

    def test_missing_covered_file_is_stale(self):
        self.evidence()
        (self.root / EVALUATOR_INPUTS[0]).unlink()
        self.assertEqual(self.state(), "STALE")

    def test_unreadable_subdirectory_cannot_hide_new_covered_inputs(self):
        nested = self.root / "fixture/nested"
        nested.mkdir()
        self.evidence()
        (nested / "new-source.py").write_text("new covered source")
        nested.chmod(0)
        try:
            self.assertNotEqual(self.state(), "PASS")
        finally:
            nested.chmod(0o700)

    def test_execution_scope_provenance_and_controls_are_required(self):
        for field in ("check", "engine", "layer", "scope", "command"):
            record = self.evidence()
            record["execution"][field] = (
                ["malicious-command"] if field == "command" else "unknown"
            )
            self.attach(record)
            with self.subTest(field=field):
                self.assertEqual(self.state(), "NO VERDICT")
        for field in (
            "provenance",
            "execution",
            "controls",
            "inputs",
            "references",
            "dependencies",
        ):
            record = self.evidence()
            del record[field]
            self.attach(record)
            with self.subTest(missing=field):
                self.assertEqual(self.state(), "NO VERDICT")
        for field, value in (
            ("upstream_commit", "0" * 40),
            ("runtime_lock", "runs/compiler.log"),
            ("recorded_at", "2026-02-31T00:00:00Z"),
        ):
            record = self.evidence()
            record["provenance"][field] = value
            self.attach(record)
            with self.subTest(provenance=field):
                self.assertEqual(self.state(), "NO VERDICT")

    def test_nonzero_exit_fails_even_with_pass_string(self):
        record = self.evidence()
        record["execution"]["exit_code"] = 1
        self.attach(record)
        self.assertEqual(self.state(), "FAIL")

    def test_controls_have_their_own_exact_byte_log_references(self):
        record = self.evidence()
        record["controls"]["stale-input"]["log"] = self.write_json(
            "runs/control.log", {"detected": True}
        )
        self.attach(record)
        self.assertEqual(self.state(), "PASS")
        (self.root / "runs/control.log").write_text("changed")
        self.assertEqual(self.state(), "STALE")
        (self.root / "runs/control.log").unlink()
        self.assertEqual(self.state(), "NO VERDICT")

    def test_strict_read_rejects_nonfinite_duplicate_and_non_json_records(self):
        for bad in (
            b'{"value":NaN}',
            b'{"value":Infinity}',
            b'{"value":1e999}',
            b'{"status":"PASS","status":"PASS"}',
            b"PASS",
            b"\xff",
        ):
            (self.root / "record.json").write_bytes(bad)
            self.graph["nodes"][0]["evidence"] = {
                "path": "record.json",
                "sha256": digest(bad),
            }
            with self.subTest(bad=bad):
                self.assertEqual(self.state(), "NO VERDICT")

    def test_unknown_future_check_cannot_stamp_pass(self):
        target = self.graph["nodes"][0]
        record = self.evidence()
        target["check"] = "unknown-check"
        target["evidence"] = None
        self.assertEqual(self.state(), "NOT RUN")
        record["node_sha256"] = node_digest(target)
        record["execution"]["check"] = "unknown-check"
        self.attach(record)
        self.assertEqual(self.state(), "NO VERDICT")

    def test_known_check_cannot_be_reused_for_hardware_or_drop_controls(self):
        for field, value in (
            ("claim_class", "silicon-operation"),
            ("engine", "silicon"),
            ("scope", "hardware"),
            ("negative_controls", {"fake": "fault"}),
        ):
            bad = copy.deepcopy(self.graph)
            bad["nodes"][0][field] = value
            with self.subTest(field=field), self.assertRaises(CapabilityError):
                validate_graph(bad)

    def test_expensive_nodes_require_a_cheaper_predecessor(self):
        target = self.graph["nodes"][0]
        target["expensive"] = True
        for cheaper in (None, "missing", "compiler"):
            target["cheaper_predecessor"] = cheaper
            with self.subTest(cheaper=cheaper), self.assertRaises(CapabilityError):
                validate_graph(self.graph)
        target["dependencies"] = ["cheap"]
        target["cheaper_predecessor"] = "cheap"
        self.graph["nodes"].append(node("cheap"))
        validate_graph(self.graph)
        self.assertEqual(self.state(), "BLOCKED")

    def test_paths_reject_traversal_absolute_and_symlink_escape(self):
        for path in (
            "../outside",
            "/tmp/absolute",
            "a/../outside",
            "./relative",
            "a//b",
            "C:\\escape",
        ):
            bad = copy.deepcopy(self.graph)
            bad["nodes"][0]["coverage"]["inputs"] = [path]
            with self.subTest(path=path), self.assertRaises(CapabilityError):
                validate_graph(bad)
        self.evidence()
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / "record.json"
            external.write_text("{}")
            (self.root / "escape.json").symlink_to(external)
            self.graph["nodes"][0]["evidence"] = {
                "path": "escape.json",
                "sha256": digest(b"{}"),
            }
            self.assertEqual(self.state(), "NO VERDICT")

    def referenced_records(self):
        """Use real v1 validators, with synthetic metadata and temporary zero audio."""
        from torchsynth_voice.artifacts import content_id

        target = self.graph["nodes"][0]
        artifact = json.loads(
            (ROOT / "tests/fixtures/artifacts/complete.json").read_text()
        )
        artifact["inputs"]["value"]["runtime"]["lock_sha256"] = digest(
            (self.root / "fixture/runtime.lock").read_bytes()
        )
        artifact["artifact_id"] = content_id(artifact["inputs"]["value"])
        blob = artifact["audio"]["value"]["file"]
        audio = self.root / blob["ref"]
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes(b"\0" * blob["size_bytes"])
        metadata = self.write_json("metadata/render.json", artifact)
        row = {
            "schema_version": 1,
            "case": {"id": "synthetic", "partition": "development"},
            "trace": "synthetic-output",
            "property": "synthetic-error",
            "unit": "unitless",
            "estimator": {"name": "synthetic", "version": "1"},
            "rubric": {"id": "synthetic", "version": "1"},
            "expected": {"value": 0, "source": "synthetic"},
            "observed": 0,
            "tolerance": {"value": 0, "source": "synthetic"},
            "validity": {"status": "valid", "reason": "synthetic compiler fixture"},
            "coverage": "complete",
            "verdict": "PASS",
            "artifact": {
                "identity": artifact["artifact_id"],
                "sha256": metadata["sha256"],
            },
        }
        score = self.write_json("metadata/row.json", row)
        target["coverage"]["references"] = [
            {"id": "render", "kind": "render-artifact", "path": metadata["path"]},
            {"id": "row", "kind": "scorecard-row", "path": score["path"]},
        ]
        record = self.evidence()
        record["references"] = {"render": metadata["sha256"], "row": score["sha256"]}
        self.attach(record)
        return record, artifact, row

    def test_explicit_artifact_scorecard_adapter_with_valid_metadata(self):
        self.referenced_records()
        self.assertEqual(self.state(), "PASS")

    def test_reference_hashes_cover_exact_stored_metadata_bytes(self):
        record, artifact, row = self.referenced_records()
        self.assertEqual(self.state(), "PASS")
        path = self.root / "metadata/render.json"
        path.write_bytes(path.read_bytes() + b"\n")
        self.assertEqual(self.state(), "STALE")
        record["references"]["render"] = digest(path.read_bytes())
        self.attach(record)
        # The envelope digest was updated, but the scorecard's digest was not.
        self.assertEqual(self.state(), "NO VERDICT")
        row["artifact"]["sha256"] = record["references"]["render"]
        record["references"]["row"] = self.write_json("metadata/row.json", row)[
            "sha256"
        ]
        self.attach(record)
        self.assertEqual(self.state(), "PASS")

    def test_reference_failure_matrix(self):
        for mutation in (
            "missing",
            "identity",
            "row-nan",
            "audio-tamper",
            "audio-escape",
            "runtime",
            "no-verdict",
            "failed-row",
        ):
            record, artifact, row = self.referenced_records()
            if mutation == "missing":
                (self.root / "metadata/render.json").unlink()
            elif mutation == "identity":
                row["artifact"]["identity"] = "does-not-exist"
            elif mutation == "row-nan":
                row["observed"] = float("nan")
            elif mutation == "audio-tamper":
                (self.root / artifact["audio"]["value"]["file"]["ref"]).write_bytes(
                    b"corrupt"
                )
            elif mutation == "audio-escape":
                artifact["audio"]["value"]["file"]["ref"] = "../outside"
            elif mutation == "runtime":
                from torchsynth_voice.artifacts import content_id

                artifact["inputs"]["value"]["runtime"]["lock_sha256"] = "0" * 64
                artifact["artifact_id"] = content_id(artifact["inputs"]["value"])
                row["artifact"]["identity"] = artifact["artifact_id"]
            elif mutation == "no-verdict":
                row.update(observed=None, verdict="NO VERDICT")
                row["validity"]["status"] = "insufficient"
            else:
                row.update(observed=1, verdict="FAIL")
            if mutation in ("audio-escape", "runtime"):
                record["references"]["render"] = self.write_json(
                    "metadata/render.json", artifact
                )["sha256"]
                row["artifact"]["sha256"] = record["references"]["render"]
            record["references"]["row"] = self.write_json("metadata/row.json", row)[
                "sha256"
            ]
            self.attach(record)
            with self.subTest(mutation=mutation):
                self.assertNotEqual(self.state(), "PASS")

    def test_document_is_deterministic_and_independent_of_node_order(self):
        self.graph["nodes"].append(node("other"))
        original = render_markdown(self.graph, evaluate(self.graph, self.root))
        self.graph["nodes"].reverse()
        self.assertEqual(
            render_markdown(self.graph, evaluate(self.graph, self.root)), original
        )

    def test_cli_lightweight_and_strict_modes(self):
        self.evidence()
        self.write_json("spec/capabilities-v1.json", self.graph)
        command = [
            sys.executable,
            str(ROOT / "tools/compile_capabilities.py"),
            "--root",
            str(self.root),
        ]

        def run(*args):
            return subprocess.run(command + list(args), capture_output=True, text=True)

        self.assertEqual(run().returncode, 0)
        self.assertEqual(run("--check").returncode, 0)
        self.assertEqual(run("--strict").returncode, 0)
        (self.root / "fixture/source.py").write_text("stale")
        self.assertEqual(run("--check").returncode, 1)
        self.assertEqual(run().returncode, 0)
        self.assertEqual(run("--check").returncode, 0)
        strict = run("--strict")
        self.assertEqual(strict.returncode, 1)
        self.assertIn("STALE", strict.stderr)

    def test_schema_vocabulary_cannot_silently_outgrow_validator(self):
        supported = {
            "$schema",
            "title",
            "type",
            "properties",
            "required",
            "additionalProperties",
            "propertyNames",
            "const",
            "enum",
            "oneOf",
            "pattern",
            "items",
            "minItems",
            "uniqueItems",
            "minProperties",
        }

        def check(schema):
            self.assertLessEqual(schema.keys(), supported)
            for value in schema.get("properties", {}).values():
                check(value)
            for field in ("additionalProperties", "propertyNames", "items"):
                if isinstance(schema.get(field), dict):
                    check(schema[field])
            for child in schema.get("oneOf", []):
                check(child)

        for kind in ("graph", "evidence"):
            check(
                json.loads(
                    (
                        ROOT / f"spec/schemas/capability-{kind}-v1.schema.json"
                    ).read_text()
                )
            )

    def test_line_terminated_graph_and_evidence_lexemes_are_rejected(self):
        record = self.evidence()
        for kind, path, ending, bad in line_terminated_documents(self.graph, record):
            with self.subTest(kind=kind, path=path, ending=ending):
                with self.assertRaises(CapabilityError):
                    _validate(bad, kind)

    def test_every_schema_pattern_enforces_full_string_search_semantics(self):
        examples = (
            "identifier",
            "a" * 64,
            "b" * 40,
            "relative/path.json",
            "Meaningful text with spaces",
            "2026-09-18T00:00:00Z",
        )

        def check(schema):
            if isinstance(schema, dict):
                if "pattern" in schema:
                    pattern = schema["pattern"]
                    accepted = [x for x in examples if re.search(pattern, x)]
                    self.assertTrue(accepted, pattern)
                    for text in accepted:
                        _structure(text, schema)
                        for ending in LINE_TERMINATORS:
                            with self.subTest(pattern=pattern, ending=ending):
                                self.assertIsNone(re.search(pattern, text + ending))
                                with self.assertRaises(CapabilityError):
                                    _structure(text + ending, schema)
                for child in schema.values():
                    check(child)
            elif isinstance(schema, list):
                for child in schema:
                    check(child)

        for kind in ("graph", "evidence"):
            check(
                json.loads(
                    (
                        ROOT / f"spec/schemas/capability-{kind}-v1.schema.json"
                    ).read_text()
                )
            )


class CapabilityRepositoryTests(unittest.TestCase):
    """Repository agreement is not part of a synthetic compiler attestation."""

    def test_committed_view_and_real_claims_remain_unrun(self):
        graph = load_graph(ROOT / "spec/capabilities-v1.json")
        results = evaluate(graph, ROOT)
        self.assertFalse(any(result.state == "PASS" for result in results.values()))
        self.assertTrue(all(result.healthy for result in results.values()))
        self.assertEqual(
            (ROOT / "docs/CAPABILITIES.md").read_text(), render_markdown(graph, results)
        )
        classes = {item["claim_class"] for item in graph["nodes"]}
        self.assertTrue(
            {
                "implementation-identity",
                "auditory-transparency",
                "distribution-preservation",
                "fpga-operation",
                "gf180-synthesis",
                "gf180-routing",
                "silicon-operation",
            }
            <= classes
        )

    def test_import_and_real_graph_check_need_only_stdlib(self):
        command = (
            "import sys; from pathlib import Path; "
            f"sys.path.insert(0, {str(ROOT / 'src')!r}); "
            "from torchsynth_voice.capabilities import load_graph,evaluate; "
            f"evaluate(load_graph(Path({str(ROOT / 'spec/capabilities-v1.json')!r})),Path({str(ROOT)!r})); "
            "assert not {'torch','torchsynth','numpy','lightning','jsonschema'} & sys.modules.keys()"
        )
        subprocess.run([sys.executable, "-S", "-c", command], check=True)


class CapabilityRegistrationTests(unittest.TestCase):
    def test_registered_checks_run_with_only_their_covered_inputs(self):
        for check in CHECKS.values():
            with (
                self.subTest(command=check.command),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                for relative in EVALUATOR_INPUTS + check.inputs:
                    target = root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(ROOT / relative, target)
                completed = subprocess.run(
                    check.command, cwd=root, capture_output=True, text=True, timeout=30
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_check_specific_fixture_invalidation_and_repository_view_independence(self):
        case = CapabilityTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        # Complete a disposable repository even before the coverage fix, so the
        # regression tests stale evidence rather than just a missing test input.
        for relative in (
            "tests/fixtures/artifacts/complete.json",
            "spec/capabilities-v1.json",
            "docs/CAPABILITIES.md",
        ) + CHECKS["contract-tests-v1"].inputs:
            target = case.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        unrelated = node("contract")
        unrelated["check"] = "contract-tests-v1"
        check = CHECKS[unrelated["check"]]
        for field in ("claim_class", "engine", "layer", "scope"):
            unrelated[field] = getattr(check, field)
        unrelated["negative_controls"] = dict(check.controls)
        case.graph["nodes"].append(unrelated)
        case.evidence(unrelated)
        record = case.evidence()
        fixture = case.root / "tests/fixtures/artifacts/complete.json"
        original = fixture.read_bytes()
        fixture.write_text("{}\n")
        self.assertEqual(case.state(), "STALE")
        self.assertEqual(case.state("contract"), "PASS")
        failed = subprocess.run(
            CHECKS["capability-compiler-v1"].command,
            cwd=case.root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertNotEqual(failed.returncode, 0)
        fixture.write_bytes(original)
        self.assertEqual(case.state(), "PASS")
        # Canonical graph/view checking is a separate normal-suite responsibility;
        # these changing outputs must not enter a compiler evidence hash cycle.
        for relative in ("spec/capabilities-v1.json", "docs/CAPABILITIES.md"):
            self.assertNotIn(relative, record["inputs"])
            (case.root / relative).write_text("deliberately invalid repository view\n")
        completed = subprocess.run(
            CHECKS["capability-compiler-v1"].command,
            cwd=case.root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(case.state(), "PASS")
        self.assertEqual(case.state("contract"), "PASS")


if __name__ == "__main__":
    unittest.main()
