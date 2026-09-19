"""Synthetic registry controls. These are not Voice or hardware measurements."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import case_registry as cr
from torchsynth_voice.paired_metrics import compare_paired


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(cr.encode(value))


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "input.txt").write_text("synthetic input v1\n")
        source = {
            "schema_version": 1,
            "cases": [
                {"id": name, "partition": "development"}
                for name in ("good", "bad", "missing", "unrun", "stale")
            ]
            + [{"id": "sealed", "partition": "holdout"}],
        }
        write_json(self.root / "fixtures.json", source)
        self.registry = {
            "schema_version": 1,
            "id": "synthetic-controls-v1",
            "evidence_root": "evidence/scorecard-v1",
            "sources": [
                {
                    "family": "synthetic",
                    "manifest": {
                        "path": "fixtures.json",
                        "sha256": cr.digest(cr.encode(source)),
                        "identity": "synthetic-controls-v1",
                    },
                }
            ],
            "rubric": {"id": "synthetic-exactness-only", "version": "1"},
            "inputs": {
                name: {"identity": name + "-v1", "files": ["input.txt"]}
                for name in (
                    "reference",
                    "implementation",
                    "preparation",
                    "estimator",
                    "rubric",
                )
            },
            "engine": "synthetic",
            "configuration": {"spectral": False},
            "required_rows": [
                {
                    "trace": "synthetic-output",
                    "property": "exact_equal",
                    "unit": "1",
                    "estimator": {
                        "name": "time-locked-paired",
                        "version": "synthetic-control-v1",
                    },
                    "expected": {"value": 1, "source": "synthetic exactness only"},
                    "tolerance": {"value": 0, "source": "synthetic exactness only"},
                }
            ],
        }

    def cases(self):
        return cr.expand_registry(self.registry, self.root)

    def record(self, name="good", candidate=None, *, missing_input=False):
        case = next(case for case in self.cases() if case["id"] == name)
        authorization = (
            "synthetic access test only" if case["partition"] == "holdout" else None
        )
        fingerprint, covered = cr.covered_inputs(
            self.registry, case, self.root, holdout_authorization=authorization
        )
        comparison = compare_paired(
            [0.0, 1.0, 0.0],
            None
            if missing_input
            else candidate
            if candidate is not None
            else [0.0, 1.0, 0.0],
            reference_rate_hz=44100,
            candidate_rate_hz=44100,
            unit="amplitude",
            spectral=False,
        )
        metrics = []
        for key in self.registry["required_rows"]:
            metric = comparison["metrics"][key["property"]]
            metrics.append(
                {
                    **{k: key[k] for k in ("trace", "property", "unit", "estimator")},
                    **{k: metric[k] for k in ("value", "status", "reason", "coverage")},
                }
            )
        measurement = {
            "schema_version": 1,
            "schema": "case-measurement",
            "case": {"id": name, "partition": case["partition"]},
            "fingerprint": fingerprint,
            "captures": [],
            "measurements": metrics,
        }
        directory = cr.case_directory(self.registry, case, self.root)
        write_json(directory / "measurement.json", measurement)
        reference = {
            "kind": "measurement",
            "identity": "cm1-" + cr.digest(cr.encode(measurement)),
            "sha256": cr.digest(cr.encode(measurement)),
            "ref": "measurement.json",
        }
        rows = cr.measurement_rows(measurement, self.registry, reference)
        result = {
            "schema_version": 1,
            "case": case,
            "rubric": self.registry["rubric"],
            "reference": self.registry["inputs"]["reference"]["identity"],
            "implementation": {
                "engine": self.registry["engine"],
                "identity": self.registry["inputs"]["implementation"]["identity"],
            },
            "configuration": self.registry["configuration"],
            "configuration_sha256": cr.digest(
                cr.canonical_bytes(self.registry["configuration"])
            ),
            "command": ["python3", "tests/test_case_registry.py", "--controls"],
            "access": {
                "partition": case["partition"],
                "holdout_authorization": authorization,
            },
            "fingerprint": fingerprint,
            "covered_inputs": covered,
            "artifacts": [reference],
            "rows": rows,
            "outcome": cr.row_outcome(rows),
        }
        write_json(directory / "result.json", result)
        return directory, result

    def outcome(self, name):
        return next(
            case
            for case in cr.evaluate(self.registry, self.root)["cases"]
            if case["id"] == name
        )

    def test_actual_good_and_bad_measurements(self):
        self.record()
        self.record("bad", [0.0, 0.0, 1.0])
        self.assertEqual(self.outcome("good")["outcome"], "PASS")
        self.assertEqual(self.outcome("bad")["outcome"], "FAIL")
        self.assertEqual(self.outcome("unrun")["outcome"], "NOT RUN")

    def test_missing_corrupt_evidence_and_omitted_rows_refuse(self):
        directory, result = self.record("missing")
        (directory / "measurement.json").unlink()
        self.assertEqual(self.outcome("missing")["outcome"], "NO VERDICT")
        directory, result = self.record("missing")
        (directory / "measurement.json").write_text("corrupt")
        self.assertEqual(self.outcome("missing")["outcome"], "NO VERDICT")
        directory, result = self.record("missing")
        result["rows"] = []
        write_json(directory / "result.json", result)
        case = self.outcome("missing")
        self.assertEqual(case["outcome"], "NO VERDICT")
        self.assertEqual(case["expected_rows"], 1)
        self.assertEqual(case["missing_rows"], 1)

    def test_changed_covered_input_is_stale(self):
        self.record("stale")
        (self.root / "input.txt").write_text("changed source\n")
        self.assertEqual(self.outcome("stale")["outcome"], "STALE")

    def test_default_holdout_is_not_inspected(self):
        self.record("sealed")
        original = Path.stat

        def guarded(path, *args, **kwargs):
            self.assertNotIn("holdout", path.parts, "even stat of holdout is forbidden")
            return original(path, *args, **kwargs)

        with patch.object(Path, "stat", guarded):
            board = cr.evaluate(self.registry, self.root)
        case = next(c for c in board["cases"] if c["id"] == "sealed")
        self.assertIsNone(case["outcome"])
        self.assertEqual(case["access"], "sealed")
        self.assertEqual(board["partitions"]["holdout"]["sealed_cases"], 1)

    def test_explicit_holdout_requires_audit_before_any_reads(self):
        with patch.object(cr, "_read", side_effect=AssertionError("unexpected read")):
            with self.assertRaisesRegex(cr.RegistryError, "holdout refused"):
                cr.evaluate(self.registry, self.root, partition="holdout")
            case = {"partition": "holdout"}
            with self.assertRaisesRegex(cr.RegistryError, "holdout refused"):
                cr.covered_inputs(self.registry, case, self.root)
        self.record("sealed")
        board = cr.evaluate(
            self.registry,
            self.root,
            partition="holdout",
            holdout_authorization="synthetic-only audit",
        )
        self.assertEqual(
            board["audit"]["holdout_authorization"], "synthetic-only audit"
        )
        self.assertEqual(board["partitions"]["holdout"]["outcomes"]["PASS"], 1)
        self.assertEqual(board["partitions"]["development"]["inspected_cases"], 0)
        self.assertIn("EXPLICIT HOLDOUT ACCESS", cr.render_markdown(board))

    def test_missing_attempt_directory_versus_missing_result(self):
        self.assertEqual(self.outcome("good")["outcome"], "NOT RUN")
        case = next(c for c in self.cases() if c["id"] == "good")
        cr.case_directory(self.registry, case, self.root).mkdir(parents=True)
        self.assertEqual(self.outcome("good")["outcome"], "NO VERDICT")
        directory = cr.case_directory(self.registry, case, self.root)
        original = Path.stat

        def unreadable(path, *args, **kwargs):
            if path == directory:
                raise PermissionError("synthetic unreadable attempt")
            return original(path, *args, **kwargs)

        with patch.object(Path, "stat", unreadable):
            self.assertEqual(self.outcome("good")["outcome"], "NO VERDICT")

    def test_missing_input_retains_missing_evidence_substate(self):
        self.record("missing", missing_input=True)
        result = self.outcome("missing")
        self.assertEqual(result["outcome"], "NO VERDICT")
        self.assertEqual(result["row_counts"]["MISSING EVIDENCE"], 1)
        self.assertIsNone(result["rows"][0]["observed"])
        self.assertFalse(result["measured"])

    def test_duplicate_extra_conflicting_and_wrong_rows_never_overwrite(self):
        mutations = [
            lambda r: r["rows"].append(copy.deepcopy(r["rows"][0])),
            lambda r: r["rows"].append(
                {**r["rows"][0], "observed": 0, "verdict": "FAIL"}
            ),
            lambda r: r["rows"].append({**r["rows"][0], "property": "extra"}),
            lambda r: r["rows"][0].update(unit="Hz"),
            lambda r: r["rows"][0]["rubric"].update(version="wrong"),
            lambda r: r["rows"][0]["estimator"].update(version="wrong"),
            lambda r: r["rows"][0]["case"].update(partition="holdout"),
            lambda r: r["rows"][0]["case"].update(id="another-case"),
            lambda r: r["case"].update(partition="holdout"),
            lambda r: r.update(outcome="FAIL"),
            lambda r: r.update(configuration_sha256="a" * 64),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                directory, result = self.record()
                # Detach aliases shared by the producer's construction helper.
                result = copy.deepcopy(json.loads(json.dumps(result)))
                mutate(result)
                write_json(directory / "result.json", result)
                board = cr.evaluate(self.registry, self.root)
                row = next(c for c in board["cases"] if c["id"] == "good")
                self.assertEqual(row["outcome"], "NO VERDICT")
                self.assertFalse(row["coverage_complete"])
                self.assertEqual(
                    board["partitions"]["development"]["inspected_expected_rows"], 5
                )

    def test_invalid_numbers_and_corrupt_result_are_not_observations(self):
        for value in (float("nan"), float("inf"), True, "0", None):
            directory, result = self.record()
            result["rows"][0]["observed"] = value
            (directory / "result.json").write_text(json.dumps(result))
            self.assertEqual(self.outcome("good")["outcome"], "NO VERDICT")
        for data in (b"{", b'{"schema_version":1,"schema_version":1}', b"null", b"[]"):
            (directory / "result.json").write_bytes(data)
            self.assertEqual(self.outcome("good")["outcome"], "NO VERDICT")

    def test_forged_pass_disagrees_with_bad_measurement(self):
        directory, result = self.record("bad", [0.0, 0.0, 1.0])
        result["rows"][0].update(verdict="PASS", observed=1)
        result["outcome"] = "PASS"
        write_json(directory / "result.json", result)
        self.assertEqual(self.outcome("bad")["outcome"], "NO VERDICT")

    def test_all_covered_policy_groups_and_missing_provenance(self):
        original = copy.deepcopy(self.registry)
        for group in self.registry["inputs"]:
            self.registry = copy.deepcopy(original)
            self.record()
            self.registry["inputs"][group]["identity"] += "-new"
            self.assertEqual(self.outcome("good")["outcome"], "STALE")
        self.registry = copy.deepcopy(original)
        self.record()
        self.registry["configuration"]["spectral"] = True
        self.assertEqual(self.outcome("good")["outcome"], "STALE")
        self.registry = copy.deepcopy(original)
        self.record()
        self.registry["required_rows"][0]["estimator"]["version"] = "new"
        self.assertEqual(self.outcome("good")["outcome"], "STALE")
        self.registry = copy.deepcopy(original)
        self.record()
        (self.root / "input.txt").unlink()
        self.assertEqual(self.outcome("good")["outcome"], "NO VERDICT")

    def test_changed_fixture_is_stale_and_bad_manifest_refuses_generation(self):
        self.record()
        source = self.registry["sources"][0]["manifest"]
        path = self.root / source["path"]
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(cr.RegistryError, "manifest hash mismatch"):
            cr.evaluate(self.registry, self.root)
        source["sha256"] = cr.digest(path.read_bytes())
        self.assertEqual(self.outcome("good")["outcome"], "STALE")

    def test_duplicate_case_and_required_key_are_invalid_registry(self):
        self.registry["required_rows"].append(
            copy.deepcopy(self.registry["required_rows"][0])
        )
        with self.assertRaisesRegex(cr.RegistryError, "duplicate required row"):
            self.cases()
        self.registry["required_rows"].pop()
        self.registry["sources"].append(copy.deepcopy(self.registry["sources"][0]))
        with self.assertRaisesRegex(cr.RegistryError, "duplicate case ID"):
            self.cases()

    def test_paths_cannot_escape_or_read_another_partition(self):
        for relative in (
            "../holdout/secret",
            "/absolute",
            "C:\\secret",
            "a//b",
            "a/./b",
            "a/../b",
            "good\n",
        ):
            directory, result = self.record()
            result["artifacts"][0]["ref"] = relative
            write_json(directory / "result.json", result)
            self.assertEqual(self.outcome("good")["outcome"], "NO VERDICT")
        directory, _ = self.record()
        (directory / "measurement.json").unlink()
        (directory / "measurement.json").symlink_to(self.root / "input.txt")
        self.assertEqual(self.outcome("good")["outcome"], "NO VERDICT")
        for relative in (
            "evidence/scorecard-v1/holdout/a",
            "docs/scorecard.json",
            "x/holdout/y",
        ):
            self.registry["inputs"]["reference"]["files"] = [relative]
            with self.assertRaisesRegex(cr.RegistryError, "provenance cannot read"):
                cr.evaluate(self.registry, self.root)

    def test_mixed_units_and_states_preserve_raw_counts(self):
        for name, unit in (
            ("framing_match", "1"),
            ("error_rms", "amplitude"),
            ("snr_db", "dB"),
        ):
            row = copy.deepcopy(self.registry["required_rows"][0])
            row.update(property=name, unit=unit)
            if name != "framing_match":
                row["expected"]["value"] = None
                row["tolerance"]["value"] = None
            self.registry["required_rows"].append(row)
        self.record("bad", [0.0, 0.0, 1.0])
        case = self.outcome("bad")
        self.assertEqual(case["outcome"], "NO VERDICT")
        self.assertEqual(
            case["row_counts"],
            {"PASS": 1, "FAIL": 1, "NO VERDICT": 2, "MISSING EVIDENCE": 0},
        )
        self.assertEqual([r["observed"] for r in case["rows"]], [0, 1, None, None])
        self.assertEqual(case["expected_rows"], 4)

    def test_measurement_record_shape_and_hash_validation(self):
        for mutation in ("case", "fingerprint", "duplicate", "invalid", "empty"):
            directory, result = self.record()
            record = json.loads((directory / "measurement.json").read_bytes())
            if mutation == "case":
                record["case"]["partition"] = "holdout"
            elif mutation == "fingerprint":
                record["fingerprint"] = "a" * 64
            elif mutation == "duplicate":
                record["measurements"] *= 2
            elif mutation == "invalid":
                record["measurements"][0]["status"] = "invalid"
            else:
                record["measurements"] = []
            data = cr.encode(record)
            (directory / "measurement.json").write_bytes(data)
            result["artifacts"][0].update(
                sha256=cr.digest(data), identity="cm1-" + cr.digest(data)
            )
            result["rows"][0]["artifact"].update(
                sha256=cr.digest(data), identity="cm1-" + cr.digest(data)
            )
            write_json(directory / "result.json", result)
            self.assertEqual(self.outcome("good")["outcome"], "NO VERDICT")

    def test_capture_validation_is_read_only(self):
        payload = struct.pack("<3f", 0.0, 1.0, 0.0)
        (self.root / "samples.f32").write_bytes(payload)
        capture = {
            "role": "reference",
            "trace": "synthetic-output",
            "ref": "samples.f32",
            "sha256": cr.digest(payload),
            "size_bytes": len(payload),
            "encoding": "f32le",
            "sample_count": 3,
            "sample_rate_hz": 44100,
        }
        record = {
            "captures": [capture],
            "measurements": [{"status": "valid", "trace": "synthetic-output"}],
        }
        with self.assertRaisesRegex(cr.RegistryError, "both captures"):
            cr._captures(record, self.root, False)
        record["captures"].append({**capture, "role": "candidate"})
        cr._captures(record, self.root, False)
        (self.root / "samples.f32").write_bytes(
            struct.pack("<3f", 0.0, float("nan"), 0.0)
        )
        for ref in record["captures"]:
            ref["sha256"] = cr.digest((self.root / "samples.f32").read_bytes())
        with self.assertRaisesRegex(cr.RegistryError, "nonfinite capture"):
            cr._captures(record, self.root, False)

    def test_real_artifact_validator_and_exact_metadata_hash_bridge(self):
        from test_storage import fixture

        directory = self.root / "render"
        record = fixture(directory)
        data = (directory / "metadata.json").read_bytes()
        reference = {
            "kind": "render",
            "identity": record["artifact_id"],
            "sha256": cr.digest(data),
            "ref": "metadata.json",
        }
        before = sorted(str(p.relative_to(directory)) for p in directory.rglob("*"))
        self.assertEqual(
            cr.render_reference(directory, reference),
            {
                "identity": record["artifact_id"],
                "sha256": hashlib.sha256(data).hexdigest(),
            },
        )
        self.assertEqual(
            before, sorted(str(p.relative_to(directory)) for p in directory.rglob("*"))
        )
        (directory / "metadata.json").write_bytes(data + b"\n")
        with self.assertRaisesRegex(cr.RegistryError, "metadata hash mismatch"):
            cr.render_reference(directory, reference)
        (directory / "metadata.json").write_bytes(data)
        (directory / "audio.f32le").write_bytes(b"bad")
        with self.assertRaisesRegex(cr.RegistryError, "payload size/hash mismatch"):
            cr.render_reference(directory, reference)

    def populate_states(self):
        self.record("good")
        self.record("bad", [0.0, 0.0, 1.0])
        directory, _ = self.record("missing")
        (directory / "measurement.json").unlink()
        directory, result = self.record("stale")
        result["fingerprint"] = "a" * 64
        write_json(directory / "result.json", result)

    def test_all_board_outcomes_determinism_and_cli_check_no_write(self):
        self.populate_states()
        board = cr.evaluate(self.registry, self.root)
        self.assertEqual(
            board["partitions"]["development"]["outcomes"],
            dict.fromkeys(cr.OUTCOMES, 1),
        )
        self.assertEqual(board["partitions"]["development"]["actual_measured_cases"], 0)
        self.assertEqual(
            board["partitions"]["development"]["synthetic_measured_cases"], 2
        )
        self.assertEqual(
            cr.encode(board), cr.encode(cr.evaluate(self.registry, self.root))
        )
        self.assertEqual(
            cr.render_markdown(board),
            cr.render_markdown(cr.evaluate(self.registry, self.root)),
        )
        write_json(self.root / "registry.json", self.registry)
        command = [
            sys.executable,
            "-S",
            str(ROOT / "tools/render_scorecard.py"),
            "--root",
            str(self.root),
            "--registry",
            "registry.json",
        ]
        for _ in range(2):
            self.assertEqual(
                subprocess.run(command, capture_output=True, check=False).returncode, 0
            )
            self.assertEqual(
                subprocess.run(
                    [*command, "--check"], capture_output=True, check=False
                ).returncode,
                0,
            )
        view = self.root / "docs/SCORECARD.md"
        view.write_text("outdated")
        snapshot = {
            str(p): (p.stat().st_mtime_ns, p.read_bytes())
            for p in (self.root / "docs").iterdir()
        }
        self.assertEqual(
            subprocess.run(
                [*command, "--check"], capture_output=True, check=False
            ).returncode,
            1,
        )
        self.assertEqual(
            snapshot,
            {
                str(p): (p.stat().st_mtime_ns, p.read_bytes())
                for p in (self.root / "docs").iterdir()
            },
        )
        self.assertEqual(
            subprocess.run(
                [*command, "--output", "evidence/scorecard-v1"],
                capture_output=True,
                check=False,
            ).returncode,
            1,
        )
        refused = subprocess.run(
            [*command, "--partition", "holdout"], capture_output=True, check=False
        )
        self.assertEqual(refused.returncode, 2)
        self.assertIn(b"holdout access refused", refused.stderr)
        explicit = subprocess.run(
            [
                *command,
                "--partition",
                "holdout",
                "--allow-holdout",
                "synthetic CLI test only",
            ],
            capture_output=True,
            check=False,
        )
        self.assertEqual(explicit.returncode, 0, explicit.stderr)
        self.assertIn(b"EXPLICIT HOLDOUT ACCESS", explicit.stderr)
        self.assertTrue((self.root / "docs/scorecard-holdout.json").is_file())
        self.assertEqual(view.read_text(), "outdated")


class ProjectRegistryTests(unittest.TestCase):
    def test_checked_in_board_and_all_manifest_cases(self):
        registry = cr.loads(
            (ROOT / "spec/reference/case-registry-v1.json").read_bytes()
        )
        cases = cr.expand_registry(registry, ROOT)
        self.assertEqual(len(cases), 520)
        directed = cr.loads(
            (ROOT / "spec/reference/directed-voice-v1.json").read_bytes()
        )
        self.assertEqual(
            {c["id"] for c in cases if c["family"] == "directed"},
            {c["id"] for c in directed["cases"]},
        )
        self.assertEqual(len({c["fixture_identity"] for c in cases}), 520)
        command = [
            sys.executable,
            "-S",
            str(ROOT / "tools/render_scorecard.py"),
            "--check",
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_schema_patterns_have_strict_search_parity(self):
        for key, value in (
            ("id", "one"),
            ("path", "some/path.json"),
            ("hash", "a" * 64),
        ):
            pattern = cr._schema()["$defs"][key]["pattern"]
            self.assertIsNotNone(re.search(pattern, value))
            for ending in ("\n", "\r", "\r\n", "\u2028", "\u2029"):
                self.assertIsNone(re.search(pattern, value + ending))

    def test_schema_vocabulary_is_implemented(self):
        supported = {
            "$schema",
            "$id",
            "$defs",
            "title",
            "$ref",
            "type",
            "enum",
            "const",
            "pattern",
            "minimum",
            "maximum",
            "exclusiveMinimum",
            "properties",
            "additionalProperties",
            "required",
            "minProperties",
            "propertyNames",
            "items",
            "uniqueItems",
            "minItems",
        }

        def walk(schema):
            self.assertLessEqual(set(schema), supported)
            for key in ("$defs", "properties"):
                for child in schema.get(key, {}).values():
                    walk(child)
            for key in ("additionalProperties", "propertyNames", "items"):
                if isinstance(schema.get(key), dict):
                    walk(schema[key])

        walk(cr._schema())


def schema_parity():
    """Opt-in independent validator; ordinary discovery stays stdlib-only."""
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    schema = cr._schema()
    Draft202012Validator.check_schema(schema)
    row_schema = cr.loads(
        (ROOT / "spec/schemas/scorecard-row-v1.schema.json").read_bytes()
    )
    resources = Registry().with_resources(
        [
            (schema["$id"], Resource.from_contents(schema)),
            (row_schema["$id"], Resource.from_contents(row_schema)),
        ]
    )
    fixture = RegistryTests("test_actual_good_and_bad_measurements")
    fixture.setUp()
    count = 0
    try:
        directory, result = fixture.record()
        documents = {
            "registry": fixture.registry,
            "result": result,
            "measurement": cr.loads((directory / "measurement.json").read_bytes()),
        }
        for kind, original in documents.items():
            reference = schema["$id"] + (
                "" if kind == "registry" else "#/$defs/" + kind
            )
            validator = Draft202012Validator({"$ref": reference}, registry=resources)
            variants = [original]

            def mutations(value, path=(), original=original, variants=variants):
                if isinstance(value, dict):
                    for key, child in value.items():
                        bad = copy.deepcopy(original)
                        target = bad
                        for part in path:
                            target = target[part]
                        del target[key]
                        variants.append(bad)
                        mutations(child, (*path, key))
                elif isinstance(value, list):
                    for index, child in enumerate(value):
                        mutations(child, (*path, index))
                else:
                    for replacement in (None, True, 0, -1, [], {}, " "):
                        bad = copy.deepcopy(original)
                        target = bad
                        for part in path[:-1]:
                            target = target[part]
                        target[path[-1]] = replacement
                        variants.append(bad)

            mutations(original)
            for variant in variants:
                try:
                    cr.validate_document(variant, kind)
                    native = True
                except ValueError:
                    native = False
                independent = validator.is_valid(variant)
                if native != independent:
                    raise AssertionError(f"schema/API mismatch: {kind}: {variant}")
                count += 1
    finally:
        fixture.doCleanups()
    print(f"Draft 2020-12 schema/API parity: {count} documents; passed.")


def controls():
    """Run real synthetic measurements with a write fence around project evidence."""
    protected = [ROOT / "evidence", ROOT / "sim/reference"]

    def guard(event, args):
        paths = []
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            if args[2] & (
                os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
            ):
                paths = [args[0]]
        elif event in ("os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.utime"):
            paths = [args[0]]
        elif event in ("os.rename", "os.link", "os.symlink"):
            paths = args[:2]
        for value in paths:
            path = Path(os.fsdecode(value)).resolve()
            if any(path == base or path.is_relative_to(base) for base in protected):
                raise PermissionError("controls cannot write production evidence")

    # This hook remains installed until this dedicated control process exits.
    sys.addaudithook(guard)
    for base in protected:
        try:
            with (base / "forbidden-control-write").open("wb"):
                raise AssertionError("write fence failed")
        except PermissionError as error:
            if str(error) != "controls cannot write production evidence":
                raise

    def snapshot():
        result = {}
        for base in protected:
            result[str(base)] = "present" if base.exists() else "absent"
            if not base.exists():
                continue
            for directory, dirs, files in os.walk(base):
                # Never inspect sealed contents, even to hash them for a control.
                dirs[:] = [name for name in dirs if name != "holdout"]
                result[directory] = sorted(dirs + files)
                for name in files:
                    path = Path(directory) / name
                    result[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result

    before = snapshot()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(RegistryTests)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if snapshot() != before:
        raise AssertionError("production evidence changed during controls")
    if not result.wasSuccessful():
        print("Controls failed; no successful control result is asserted.")
        return 1
    print(
        "Controls: good=PASS; delayed candidate=FAIL; missing/corrupt evidence=NO VERDICT;"
    )
    print("omitted rows=NO VERDICT; changed inputs=STALE; no attempt=NOT RUN.")
    print(
        "Production evidence write fence active; byte/directory snapshot unchanged; holdout unopened."
    )
    return 0


if __name__ == "__main__":
    if "--controls" in sys.argv:
        raise SystemExit(controls())
    if "--schemas" in sys.argv:
        schema_parity()
    else:
        unittest.main()
