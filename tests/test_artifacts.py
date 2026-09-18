from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.artifacts import (  # noqa: E402
    ValidationError,
    canonical_bytes,
    content_id,
    loads,
    validate_artifact,
    validate_corpus_index,
    verify_sha256,
)

FIXTURES = Path(__file__).parent / "fixtures/artifacts"


def artifact():
    return loads((FIXTURES / "complete.json").read_text(encoding="utf-8"))


def index(record=None):
    record = record or artifact()
    inputs = record["inputs"]["value"]
    return {
        "schema": "torchsynth-corpus-index",
        "schema_version": 1,
        "corpus_id": "synthetic-v1",
        "corpus_manifest_sha256": "b" * 64,
        "profile": inputs["profile"]["name"],
        "runtime_lock_sha256": inputs["runtime"]["lock_sha256"],
        "status": "complete",
        "expected_case_count": 1,
        "observed_case_count": 1,
        "cases": [
            {
                "case_id": "synthetic-39942",
                "split": "development",
                "fixture": copy.deepcopy(inputs["fixture"]),
                "status": "complete",
                "artifact": {
                    "artifact_id": record["artifact_id"],
                    "sha256": hashlib.sha256(
                        json.dumps(record, sort_keys=True, allow_nan=False).encode(
                            "utf-8"
                        )
                    ).hexdigest(),
                    "ref": "synthetic/metadata.json",
                },
                "warnings": [],
                "failures": [],
            }
        ],
        "warnings": [],
        "failures": [],
    }


class ArtifactTests(unittest.TestCase):
    def test_complete_synthetic_artifact_and_index(self):
        record = artifact()
        validate_artifact(record)
        validate_corpus_index(index(record), artifacts={record["artifact_id"]: record})
        saved = loads((FIXTURES / "index.json").read_bytes())
        validate_corpus_index(saved, artifacts={record["artifact_id"]: record})
        verify_sha256(
            (FIXTURES / "complete.json").read_bytes(),
            saved["cases"][0]["artifact"]["sha256"],
        )
        verify_sha256(bytes(176400 * 4), record["audio"]["value"]["file"]["sha256"])

    def test_schema_uses_only_supported_keywords(self):
        supported = {
            "$schema",
            "$id",
            "$defs",
            "title",
            "description",
            "$ref",
            "type",
            "const",
            "enum",
            "oneOf",
            "required",
            "properties",
            "additionalProperties",
            "propertyNames",
            "items",
            "uniqueItems",
            "minItems",
            "minProperties",
            "minimum",
            "maximum",
            "exclusiveMinimum",
            "pattern",
        }

        def check(node):
            self.assertFalse(node.keys() - supported)
            for key in ("$defs", "properties"):
                for child in node.get(key, {}).values():
                    check(child)
            for key in ("propertyNames", "items", "additionalProperties"):
                if isinstance(node.get(key), dict):
                    check(node[key])
            for child in node.get("oneOf", []):
                check(child)

        for filename in (
            "render-artifact-v1.schema.json",
            "corpus-index-v1.schema.json",
        ):
            check(loads((ROOT / "spec/schemas" / filename).read_bytes()))

    def test_scalar_execution_retains_batched_fixture_identity(self):
        record = artifact()
        record["inputs"]["value"]["execution"] = {
            "mode": "resolved-scalar",
            "batch_size": 1,
            "reproducible": False,
        }
        record["artifact_id"] = content_id(record["inputs"]["value"])
        validate_artifact(record)
        self.assertNotEqual(record["artifact_id"], artifact()["artifact_id"])
        self.assertEqual(record["inputs"]["value"]["fixture"]["sound_index"], 39942)
        self.assertEqual(record["inputs"]["value"]["noise"]["slot"], 6)

    def test_failed_record_is_inspectable_but_never_complete(self):
        record = artifact()
        for state in ("unavailable", "invalid"):
            with self.subTest(state=state):
                failed = copy.deepcopy(record)
                failed.update(
                    status="failed", artifact_id=None, failures=["missing-provenance"]
                )
                failed["inputs"] = {"state": state, "reason": "runtime-lock-missing"}
                validate_artifact(failed, require_complete=False)
                with self.assertRaises(ValidationError):
                    validate_artifact(failed)
                failed["status"] = "complete"
                with self.assertRaises(ValidationError):
                    validate_artifact(failed, require_complete=False)

    def test_missing_provenance_never_defaults(self):
        for name in ("source", "runtime", "project_git", "renderer_version"):
            record = artifact()
            del record["inputs"]["value"][name]
            with self.subTest(name=name), self.assertRaises(ValidationError):
                validate_artifact(record)
        record = artifact()
        del record["inputs"]["value"]["runtime"]["lock_sha256"]
        with self.assertRaises(ValidationError):
            validate_artifact(record)
        record = artifact()
        record["inputs"]["value"]["runtime"]["versions"]["lightning"] = None
        with self.assertRaises(ValidationError):
            validate_artifact(record)

    def test_negative_fixture_mutations(self):
        for mutation in loads((FIXTURES / "rejections.json").read_text()):
            record = artifact()
            target = record
            for key in mutation["path"][:-1]:
                target = target[key]
            target[mutation["path"][-1]] = mutation["value"]
            # Refresh identity so semantic faults cannot hide behind a stale ID.
            if record["inputs"]["state"] == "available":
                record["artifact_id"] = content_id(record["inputs"]["value"])
            with (
                self.subTest(mutation=mutation["name"]),
                self.assertRaises(ValidationError),
            ):
                validate_artifact(record)

    def test_rejects_nonfinite_python_values_anywhere(self):
        for value in (float("nan"), float("inf"), -float("inf")):
            record = artifact()
            record["audio"]["value"]["rms"] = value
            with self.subTest(value=value), self.assertRaises(ValidationError):
                validate_artifact(record)
            with self.assertRaises(ValidationError):
                canonical_bytes(value)

    def test_strict_json_reader(self):
        for text in (
            '{"a":1,"a":2}',
            '{"a":{"x":1,"x":2}}',
            '{"x":NaN}',
            '{"x":Infinity}',
            '{"x":-Infinity}',
            '{"x":1e999}',
            '{"x":"\\ud800"}',
            '{"x":',
        ):
            with self.subTest(text=text), self.assertRaises(ValidationError):
                loads(text)
        self.assertEqual(loads('{"a":0.5}'), {"a": 0.5})

    def test_rejects_positional_missing_extra_and_mismatched_parameters(self):
        record = artifact()
        maps = record["inputs"]["value"]["parameters"]
        key = next(iter(maps["normalized_by_name"]))
        variants = []
        positional = copy.deepcopy(record)
        positional["inputs"]["value"]["parameters"]["normalized_by_name"] = [0.5] * 78
        variants.append(positional)
        for name in ("normalized_by_name", "physical_by_name"):
            missing = copy.deepcopy(record)
            del missing["inputs"]["value"]["parameters"][name][key]
            variants.append(missing)
            extra = copy.deepcopy(record)
            extra["inputs"]["value"]["parameters"][name]["extra.parameter"] = 0.5
            variants.append(extra)
        mismatched = copy.deepcopy(record)
        physical = mismatched["inputs"]["value"]["parameters"]["physical_by_name"]
        physical["renamed.parameter"] = physical.pop(key)
        variants.append(mismatched)
        for candidate in variants:
            candidate["artifact_id"] = content_id(candidate["inputs"]["value"])
            with (
                self.subTest(candidate=candidate["artifact_id"]),
                self.assertRaises(ValidationError),
            ):
                validate_artifact(candidate)

    def test_diagnostic_orders_are_permutations_only(self):
        record = artifact()
        names = list(record["inputs"]["value"]["parameters"]["normalized_by_name"])
        record["diagnostic_orders"] = {
            "randomization_order": names,
            "forward_order": list(reversed(names)),
        }
        validate_artifact(record)
        record["diagnostic_orders"]["forward_order"][0] = names[0]
        with self.assertRaises(ValidationError):
            validate_artifact(record)

    def test_requested_trace_coverage(self):
        record = artifact()
        record["inputs"]["value"]["requested_traces"] = ["mixer"]
        record["artifact_id"] = content_id(record["inputs"]["value"])
        with self.assertRaises(ValidationError):
            validate_artifact(record)
        record["traces"]["value"]["mixer"] = copy.deepcopy(
            record["audio"]["value"]["file"]
        )
        validate_artifact(record)

    def test_clean_and_dirty_git_digests(self):
        record = artifact()
        git = record["inputs"]["value"]["project_git"]
        git["diff_sha256"] = "c" * 64
        record["artifact_id"] = content_id(record["inputs"]["value"])
        with self.assertRaises(ValidationError):
            validate_artifact(record)
        git["dirty"] = True
        record["artifact_id"] = content_id(record["inputs"]["value"])
        validate_artifact(record)

    def test_train_test_boundaries(self):
        for sound_index, is_train in (
            (9215, True),
            (9216, False),
            (10239, False),
            (10240, True),
        ):
            record = artifact()
            inputs = record["inputs"]["value"]
            batch, slot = divmod(sound_index, 128)
            inputs["fixture"] = {
                "sound_index": sound_index,
                "upstream_batch_index": batch,
                "upstream_slot": slot,
                "upstream_name": f"synth1B1-{batch}-{slot}",
                "is_train": is_train,
            }
            inputs["noise"]["slot"] = sound_index % 32
            record["artifact_id"] = content_id(inputs)
            validate_artifact(record)

    def test_metadata_cannot_claim_success_with_failure_or_missing_audio(self):
        for change in (
            {"failures": ["render-failed"]},
            {"audio": {"state": "unavailable", "reason": "not-rendered"}},
            {"artifact_id": None},
            {"status": "failed"},
        ):
            record = artifact()
            record.update(change)
            with self.subTest(change=change), self.assertRaises(ValidationError):
                validate_artifact(record, require_complete=False)

    def test_boolean_is_not_an_integer_or_numeric_fact(self):
        paths = [
            ["schema_version"],
            ["inputs", "value", "fixture", "sound_index"],
            ["inputs", "value", "execution", "batch_size"],
            ["inputs", "value", "profile", "channels"],
            ["audio", "value", "rms"],
            ["audio", "value", "observed_sample_count"],
        ]
        for path in paths:
            record = artifact()
            target = record
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = True
            with self.subTest(path=path), self.assertRaises(ValidationError):
                validate_artifact(record)

    def test_no_heavy_imports_even_during_validation(self):
        code = (
            "import sys; from pathlib import Path; "
            f"sys.path.insert(0, {str(ROOT / 'src')!r}); "
            "from torchsynth_voice.artifacts import loads, validate_artifact; "
            f"validate_artifact(loads(Path({str(FIXTURES / 'complete.json')!r}).read_text())); "
            "assert not {'torch', 'torchsynth', 'numpy', 'lightning', 'jsonschema'} & sys.modules.keys()"
        )
        subprocess.run([sys.executable, "-S", "-c", code], check=True)


class IdentityEncodingTests(unittest.TestCase):
    def test_canonical_encoding_vector(self):
        self.assertEqual(
            canonical_bytes({"z": 0.5, "a": [True, None, "é"]}),
            b'["object",[["a",["array",[["bool",true],["null"],["string","\\u00e9"]]]],'
            b'["z",["number","1","2"]]]]',
        )
        self.assertEqual(canonical_bytes(1), canonical_bytes(1.0))
        self.assertEqual(canonical_bytes(-0.0), canonical_bytes(0))
        self.assertNotEqual(canonical_bytes(True), canonical_bytes(1))

    def test_key_order_is_irrelevant(self):
        inputs = artifact()["inputs"]["value"]
        reversed_inputs = copy.deepcopy(dict(reversed(list(inputs.items()))))
        self.assertEqual(content_id(inputs), content_id(reversed_inputs))
        parameters = reversed_inputs["parameters"]
        parameters["physical_by_name"] = dict(
            reversed(list(parameters["physical_by_name"].items()))
        )
        self.assertEqual(content_id(inputs), content_id(reversed_inputs))

    def test_every_input_leaf_affects_identity(self):
        inputs = artifact()["inputs"]["value"]
        expected = content_id(inputs)

        def leaves(value, path=()):
            if isinstance(value, dict):
                if not value:
                    yield path, value
                for key, child in value.items():
                    yield from leaves(child, path + (key,))
            elif isinstance(value, list):
                # Array ordering is normative; empty arrays still participate.
                yield path, value
            else:
                yield path, value

        for path, value in leaves(inputs):
            candidate = copy.deepcopy(inputs)
            target = candidate
            for key in path[:-1]:
                target = target[key]
            if isinstance(value, bool):
                changed = not value
            elif isinstance(value, (int, float)):
                changed = value + 1
            elif isinstance(value, list):
                changed = value + ["changed"]
            elif isinstance(value, dict):
                changed = {"synthetic.p00": 0.25}
            else:
                changed = value + "-changed"
            target[path[-1]] = changed
            with self.subTest(path=path):
                self.assertNotEqual(expected, content_id(candidate))

    def test_output_hash_is_integrity_not_content_identity(self):
        record = artifact()
        original = record["artifact_id"]
        original_bytes = canonical_bytes(record)
        record["audio"]["value"]["file"]["sha256"] = "d" * 64
        self.assertEqual(original, content_id(record["inputs"]["value"]))
        validate_artifact(record)
        with self.assertRaises(ValidationError):
            verify_sha256(
                canonical_bytes(record), hashlib.sha256(original_bytes).hexdigest()
            )
        verify_sha256(b"audio", hashlib.sha256(b"audio").hexdigest())
        with self.assertRaises(ValidationError):
            verify_sha256(b"corrupt", hashlib.sha256(b"audio").hexdigest())

    def test_stale_content_id_is_rejected(self):
        record = artifact()
        record["inputs"]["value"]["runtime"]["lock_sha256"] = "e" * 64
        with self.assertRaises(ValidationError):
            validate_artifact(record)


class CorpusTests(unittest.TestCase):
    def test_duplicate_case_or_sound_or_artifact_is_rejected(self):
        for field in ("case_id", "sound_index", "artifact_id"):
            value = index()
            other = copy.deepcopy(value["cases"][0])
            if field != "case_id":
                other["case_id"] = "second"
            if field != "sound_index":
                other["fixture"].update(
                    sound_index=39943, upstream_slot=7, upstream_name="synth1B1-312-7"
                )
            if field != "artifact_id":
                other["artifact"]["artifact_id"] = "different-artifact"
            value["cases"].append(other)
            value["expected_case_count"] = value["observed_case_count"] = 2
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ValidationError, field),
            ):
                validate_corpus_index(value)

    def test_count_mismatches_and_bools(self):
        for field in ("expected_case_count", "observed_case_count"):
            for count in (0, 2, True):
                value = index()
                value[field] = count
                with (
                    self.subTest(field=field, count=count),
                    self.assertRaises(ValidationError),
                ):
                    validate_corpus_index(value)

    def test_failed_case_has_no_success_reference(self):
        value = index()
        case = value["cases"][0]
        value.update(status="failed", observed_case_count=0, failures=["incomplete"])
        case.update(status="failed", artifact=None, failures=["render-failed"])
        validate_corpus_index(value, require_complete=False)
        with self.assertRaises(ValidationError):
            validate_corpus_index(value)
        case["artifact"] = index()["cases"][0]["artifact"]
        with self.assertRaises(ValidationError):
            validate_corpus_index(value, require_complete=False)

    def test_cross_artifact_checks(self):
        record = artifact()
        for field in ("runtime_lock_sha256", "fixture"):
            value = index(record)
            if field == "fixture":
                value["cases"][0]["fixture"].update(
                    sound_index=39943, upstream_slot=7, upstream_name="synth1B1-312-7"
                )
            else:
                value[field] = "d" * 64
            with self.subTest(field=field), self.assertRaises(ValidationError):
                validate_corpus_index(value, artifacts={record["artifact_id"]: record})
        with self.assertRaises(ValidationError):
            validate_corpus_index(index(record), artifacts={})


if __name__ == "__main__":
    unittest.main()
