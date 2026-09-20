"""Tests for the issue 44 listening scaffolding (config, generator, analyzer).

Stdlib-only; no Torch, no real corpus store. The generator is exercised
against a synthetic read-only corpus store fixture built per the landed
store layout (receipt -> index -> artifacts/<id>/{metadata.json,audio.f32le}).

These tests verify the INERT scaffolding mechanics only: fail-closed gates,
determinism, custody, and analysis behavior on synthetic responses. No
listening has occurred; no perceptual claim is made or tested.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import listening_protocol_common as common  # noqa: E402
from listening_protocol_common import (  # noqa: E402
    ProtocolConfigError,
    clopper_pearson,
    config_identity,
    spearman,
    stimulus_code,
)

SCHEMA_PATH = ROOT / "spec" / "reference" / "listening-protocol-config-v1.schema.json"
EXAMPLE_CONFIG_PATH = (
    ROOT / "spec" / "reference" / "listening-protocol-config-example-v1.json"
)
MATRIX_PATH = ROOT / "sim" / "reference" / "mutation-matrix-v1.json"
GENERATOR = ROOT / "tools" / "generate_listening_stimuli.py"
ANALYZER = ROOT / "tools" / "analyze_listening_responses.py"


def load_example_config():
    matrix = json.loads(MATRIX_PATH.read_bytes().decode("utf-8"))
    return common.load_config(EXAMPLE_CONFIG_PATH, matrix=matrix)


def ratified_config(tmp: Path, **overrides) -> Path:
    """A ratified variant of the example config with the ethics box filled."""
    cfg = load_example_config()
    cfg["ratified"] = True
    cfg["ratification"] = {
        "status": "adopted",
        "decided_by": "operator",
        "decided_utc": "2026-09-20T00:00:00Z",
        "protocol_version": "listening-protocol-v1",
        "notes": "test ratification",
    }
    for field in common.ETHICS_FIELDS:
        cfg["ethics"][field] = "operator decision recorded (%s)" % field
    cfg.update(overrides)
    path = tmp / "ratified-config.json"
    path.write_bytes(common.canonical_bytes(cfg))
    return path


def synthetic_store(root: Path, dev_cases=6, with_holdout=False, samples=256):
    """Build a minimal read-only corpus store per the landed layout."""
    artifacts = root / "artifacts"
    indexes = root / "indexes"
    artifacts.mkdir(parents=True)
    indexes.mkdir(parents=True)
    cases = []
    total = dev_cases + (1 if with_holdout else 0)
    for i in range(total):
        case_id = "global-%d" % i
        # Deterministic finite audio (real corpus audio is finite per the
        # coverage receipt; the generator refuses non-finite samples).
        values = []
        for j in range(samples):
            phase = (j % 32) / 32.0
            values.append(0.5 * math.sin(2.0 * math.pi * phase) + 0.01 * (i + 1))
        audio = struct.pack("<%df" % len(values), *values)
        artifact_id = (
            "ra1-" + hashlib.sha256(("artifact:" + case_id).encode()).hexdigest()
        )
        rel_dir = artifacts / artifact_id
        rel_dir.mkdir()
        (rel_dir / "audio.f32le").write_bytes(audio)
        metadata = {
            "case_id": case_id,
            "audio": {
                "value": {
                    "file": {
                        "ref": "audio.f32le",
                        "sha256": hashlib.sha256(audio).hexdigest(),
                        "size_bytes": len(audio),
                    }
                }
            },
        }
        metadata_bytes = json.dumps(metadata, sort_keys=True).encode()
        (rel_dir / "metadata.json").write_bytes(metadata_bytes)
        cases.append(
            {
                "case_id": case_id,
                "split": "holdout"
                if (with_holdout and i == total - 1)
                else "development",
                "status": "complete",
                "artifact": {
                    "artifact_id": artifact_id,
                    "ref": "artifacts/%s/metadata.json" % artifact_id,
                    "sha256": hashlib.sha256(metadata_bytes).hexdigest(),
                },
            }
        )
    index = {
        "corpus_id": "synthetic-test-corpus",
        "expected_case_count": total,
        "observed_case_count": total,
        "status": "PASS",
        "cases": cases,
    }
    index_bytes = json.dumps(index, sort_keys=True).encode()
    (indexes / "index.json").write_bytes(index_bytes)
    receipt = {
        "schema": "torchsynth-development-corpus-evidence",
        "schema_version": 1,
        "status": "PASS",
        "source_commit": json.loads(
            (ROOT / "spec" / "reference" / "upstream.json").read_bytes().decode()
        )["target_commit"],
        "run": {
            "index_ref": "indexes/index.json",
            "index_sha256": hashlib.sha256(index_bytes).hexdigest(),
        },
        "index": index,
    }
    receipt_path = root / "receipt.json"
    receipt_path.write_bytes(json.dumps(receipt, sort_keys=True).encode())
    coverage_path = root / "coverage.json"
    coverage_path.write_bytes(
        json.dumps(synthetic_coverage(dev_cases), sort_keys=True).encode()
    )
    return receipt_path, coverage_path


def synthetic_coverage(dev_cases):
    return {
        "schema": "torchsynth-development-corpus-coverage",
        "schema_version": 1,
        "verdict": "QUALIFIED",
        "families": {
            "audio_facts": {
                "rows": [
                    {
                        "case_id": "global-%d" % i,
                        "exact_silence": False,
                        "near_silence": i
                        == 0,  # one near-silent case exercises exclusions
                    }
                    for i in range(dev_cases)
                ]
            },
            "normalization_seam": {
                "rows": [
                    {
                        "case_id": "global-%d" % i,
                        "classification": "active" if i % 2 else "not_applied",
                    }
                    for i in range(dev_cases)
                ]
            },
            "pitch_stability": {"attempted_estimators": 0},
        },
    }


def run_python(script: Path, *args: str):
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        timeout=300,
    )


class ConfigValidationTests(unittest.TestCase):
    def test_example_config_is_valid_against_schema_and_validator(self):
        schema = json.loads(SCHEMA_PATH.read_bytes().decode("utf-8"))
        self.assertEqual(schema["$id"], "torchsynth-listening-protocol-config-v1")
        self.assertEqual(schema["properties"]["ratified"]["type"], "boolean")
        cfg = load_example_config()  # raises ProtocolConfigError on refusal
        self.assertFalse(cfg["ratified"])
        self.assertEqual(cfg["selection"]["pool"], "development-corpus")

    def test_example_config_keys_match_schema_required_sets(self):
        schema = json.loads(SCHEMA_PATH.read_bytes().decode("utf-8"))
        example = json.loads(EXAMPLE_CONFIG_PATH.read_bytes().decode("utf-8"))
        self.assertEqual(set(example), set(schema["required"]))
        for section in (
            "method",
            "selection",
            "blinding",
            "level_conditions",
            "session",
            "analysis",
            "ethics",
        ):
            required = set(schema["properties"][section].get("required", []))
            if required:
                self.assertTrue(required <= set(example[section]), section)

    def test_unratified_flag_is_required_boolean(self):
        matrix = json.loads(MATRIX_PATH.read_bytes().decode("utf-8"))
        cfg = load_example_config()
        del cfg["ratified"]
        with self.assertRaises(ProtocolConfigError):
            common.validate_config(cfg, matrix=matrix)

    def test_ladder_point_outside_declared_matrix_range_refuses(self):
        matrix = json.loads(MATRIX_PATH.read_bytes().decode("utf-8"))
        cfg = load_example_config()
        cfg["ladders"]["gain.db"]["points"] = [-0.05, -0.2, -0.5, -1.0, -61.0]
        with self.assertRaises(ProtocolConfigError) as caught:
            common.validate_config(cfg, matrix=matrix)
        self.assertIn("outside the declared matrix range", str(caught.exception))

    def test_out_of_scope_operator_refuses(self):
        matrix = json.loads(MATRIX_PATH.read_bytes().decode("utf-8"))
        cfg = load_example_config()
        cfg["binary_trials"]["param.positional_shuffle"] = {"render": "host-run-gated"}
        with self.assertRaises(ProtocolConfigError) as caught:
            common.validate_config(cfg, matrix=matrix)
        self.assertIn("provenance refusal", str(caught.exception))
        cfg = load_example_config()
        cfg["ladders"]["corrupt.byte"] = {
            "render": "host-run-gated",
            "unit": "byte_offset",
            "points": [1, 2, 3, 4, 5],
        }
        with self.assertRaises(ProtocolConfigError) as caught:
            common.validate_config(cfg, matrix=matrix)
        self.assertIn("framework family", str(caught.exception))

    def test_unit_disagreement_with_matrix_refuses(self):
        matrix = json.loads(MATRIX_PATH.read_bytes().decode("utf-8"))
        cfg = load_example_config()
        cfg["ladders"]["gain.db"]["unit"] = "amplitude"
        with self.assertRaises(ProtocolConfigError) as caught:
            common.validate_config(cfg, matrix=matrix)
        self.assertIn("disagrees with the declared matrix unit", str(caught.exception))

    def test_aggregate_score_must_be_false(self):
        matrix = json.loads(MATRIX_PATH.read_bytes().decode("utf-8"))
        cfg = load_example_config()
        cfg["analysis"]["aggregate_score"] = True
        with self.assertRaises(ProtocolConfigError) as caught:
            common.validate_config(cfg, matrix=matrix)
        self.assertIn("aggregate_score", str(caught.exception))

    def test_holdout_pool_refuses(self):
        matrix = json.loads(MATRIX_PATH.read_bytes().decode("utf-8"))
        cfg = load_example_config()
        cfg["selection"]["pool"] = "holdout-corpus"
        with self.assertRaises(ProtocolConfigError) as caught:
            common.validate_config(cfg, matrix=matrix)
        self.assertIn("holdout", str(caught.exception))

    def test_unknown_exclusion_selector_refuses(self):
        matrix = json.loads(MATRIX_PATH.read_bytes().decode("utf-8"))
        cfg = load_example_config()
        cfg["selection"]["exclusions"][0]["selector"] = "sounds_boring"
        with self.assertRaises(ProtocolConfigError):
            common.validate_config(cfg, matrix=matrix)

    def test_unsupported_corpus_audio_render_target_refuses_at_generation(self):
        matrix = json.loads(MATRIX_PATH.read_bytes().decode("utf-8"))
        cfg = load_example_config()
        cfg["ladders"]["noise.seed_shift"]["render"] = "corpus-audio"
        supported = common.corpus_audio_applications()
        self.assertNotIn("noise.seed_shift", supported)
        # validator accepts structure; the generator refuses (covered in GeneratorTests)
        common.validate_config(cfg, matrix=matrix)


class GeneratorTests(unittest.TestCase):
    def _run_generator(
        self, tmp: Path, config_path: Path, store_dir: Path, out_name: str, extra=()
    ):
        out = tmp / out_name
        result = run_python(
            GENERATOR,
            "--config",
            str(config_path),
            "--receipt",
            str(store_dir / "receipt.json"),
            "--coverage",
            str(store_dir / "coverage.json"),
            "--store",
            str(store_dir),
            "--out",
            str(out),
            *extra,
        )
        return result, out

    def test_unratified_config_refuses_without_explicit_override(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            store_dir = tmp / "store"
            synthetic_store(store_dir)
            small = tmp / "small-config.json"
            cfg = load_example_config()
            cfg["selection"]["cases_per_operator"] = 1
            small.write_bytes(common.canonical_bytes(cfg))
            result, out = self._run_generator(tmp, small, store_dir, "out.json")
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("not ratified", result.stderr)
            self.assertFalse(out.exists())

    def test_determinism_same_config_and_seed_same_receipts(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            store_dir = tmp / "store"
            synthetic_store(store_dir)
            small = tmp / "small-config.json"
            cfg = load_example_config()
            cfg["selection"]["cases_per_operator"] = 2
            small.write_bytes(common.canonical_bytes(cfg))
            result_a, out_a = self._run_generator(
                tmp, small, store_dir, "a.json", extra=("--allow-unratified",)
            )
            result_b, out_b = self._run_generator(
                tmp, small, store_dir, "b.json", extra=("--allow-unratified",)
            )
            self.assertEqual(result_a.returncode, 0, result_a.stderr)
            self.assertEqual(result_b.returncode, 0, result_b.stderr)
            self.assertEqual(out_a.read_bytes(), out_b.read_bytes())
            manifest = json.loads(out_a.read_bytes().decode("utf-8"))
            self.assertEqual(manifest["status"], "UNRATIFIED-EXAMPLE-NOT-EVIDENCE")
            self.assertEqual(
                manifest["evidence_status"], "not_protocol_evidence_unratified_config"
            )
            # corpus-audio operators from the example config: 4 ladder ops x 5 steps x 2 cases + 1 binary x 2
            self.assertEqual(len(manifest["stimuli"]), 4 * 5 * 2 + 2)
            # gated: 9 ladder operators + 13 binary operators from the example config
            self.assertEqual(len(manifest["skipped"]), 22)
            for row in manifest["stimuli"]:
                self.assertIsNotNone(row["paired_metrics"])
                self.assertIn(row["binding"]["mp1_plan_identity"], (None,))
                self.assertIsNotNone(row["binding"]["mp1_plan_refusal"])
                self.assertTrue(row["ls1_stimulus_identity"].startswith("ls1i-"))

    def test_different_seed_changes_selection_and_receipts(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            store_dir = tmp / "store"
            synthetic_store(store_dir, dev_cases=8)
            receipt = json.loads(
                (store_dir / "receipt.json").read_bytes().decode("utf-8")
            )
            coverage = json.loads(
                (store_dir / "coverage.json").read_bytes().decode("utf-8")
            )
            cases = common.development_cases(receipt)
            facts = common.coverage_facts(coverage)
            cfg = load_example_config()
            cfg["selection"]["cases_per_operator"] = 2
            declared = common.declared_operators(cfg)
            # Pick two seeds whose draws provably differ (test stays deterministic).
            seeds = None
            for seed_a in range(1, 60):
                for seed_b in range(seed_a + 1, 61):
                    cfg["selection"]["seed"] = seed_a
                    selection_a = common.select_cases(cfg, cases, facts, declared)
                    cfg["selection"]["seed"] = seed_b
                    selection_b = common.select_cases(cfg, cases, facts, declared)
                    if any(
                        selection_a[op]["cases"] != selection_b[op]["cases"]
                        for op in selection_a
                    ):
                        seeds = (seed_a, seed_b)
                        break
                if seeds:
                    break
            self.assertIsNotNone(seeds)
            # sanity: same config + same seed is stable
            cfg["selection"]["seed"] = seeds[0]
            first = common.select_cases(cfg, cases, facts, declared)
            second = common.select_cases(cfg, cases, facts, declared)
            self.assertEqual(first, second)

    def test_holdout_case_in_scope_refuses(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            store_dir = tmp / "store"
            synthetic_store(store_dir, dev_cases=6, with_holdout=True)
            small = tmp / "small-config.json"
            cfg = load_example_config()
            cfg["selection"]["cases_per_operator"] = 1
            small.write_bytes(common.canonical_bytes(cfg))
            result, out = self._run_generator(
                tmp, small, store_dir, "out.json", extra=("--allow-unratified",)
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("holdout refusal", result.stderr)
            self.assertFalse(out.exists())

    def test_receipts_only_custody_refuses_audio_inside_repository(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            store_dir = tmp / "store"
            synthetic_store(store_dir)
            small = tmp / "small-config.json"
            cfg = load_example_config()
            cfg["selection"]["cases_per_operator"] = 1
            small.write_bytes(common.canonical_bytes(cfg))
            result, out = self._run_generator(
                tmp,
                small,
                store_dir,
                "out.json",
                extra=("--allow-unratified", "--audio-dir", str(ROOT)),
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("receipts-only", result.stderr)
            self.assertFalse(out.exists())

    def test_no_audio_written_by_default(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            store_dir = tmp / "store"
            synthetic_store(store_dir)
            small = tmp / "small-config.json"
            cfg = load_example_config()
            cfg["selection"]["cases_per_operator"] = 1
            small.write_bytes(common.canonical_bytes(cfg))
            result, out = self._run_generator(
                tmp, small, store_dir, "out.json", extra=("--allow-unratified",)
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads(out.read_bytes().decode("utf-8"))
            self.assertFalse(manifest["custody"]["audio_written"])
            f32_files = [p for p in tmp.rglob("*.f32")]
            self.assertEqual(f32_files, [])

    def test_blinded_index_carries_no_condition_fields(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            store_dir = tmp / "store"
            synthetic_store(store_dir)
            small = tmp / "small-config.json"
            cfg = load_example_config()
            cfg["selection"]["cases_per_operator"] = 1
            small.write_bytes(common.canonical_bytes(cfg))
            result, out = self._run_generator(
                tmp, small, store_dir, "out.json", extra=("--allow-unratified",)
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads(out.read_bytes().decode("utf-8"))
            for entry in manifest["blinded_index"]:
                self.assertEqual(set(entry), {"stimulus_code", "audio_sha256"})
            codes = {entry["stimulus_code"] for entry in manifest["blinded_index"]}
            for operator in ("gain", "clip", "polarity"):
                for code in codes:
                    self.assertNotIn(operator, code)

    def test_generator_refuses_unsupported_corpus_audio_render(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            store_dir = tmp / "store"
            synthetic_store(store_dir)
            small = tmp / "small-config.json"
            cfg = load_example_config()
            cfg["ladders"]["noise.seed_shift"]["render"] = "corpus-audio"
            cfg["selection"]["cases_per_operator"] = 1
            small.write_bytes(common.canonical_bytes(cfg))
            result, out = self._run_generator(
                tmp, small, store_dir, "out.json", extra=("--allow-unratified",)
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("no sample-domain application", result.stderr)

    def test_generator_refuses_store_drift(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            store_dir = tmp / "store"
            synthetic_store(store_dir)
            # corrupt every audio file after the receipt/index were written
            for artifact_dir in (store_dir / "artifacts").iterdir():
                audio = artifact_dir / "audio.f32le"
                audio.write_bytes(audio.read_bytes()[:-4])
            small = tmp / "small-config.json"
            cfg = load_example_config()
            cfg["selection"]["cases_per_operator"] = 1
            small.write_bytes(common.canonical_bytes(cfg))
            result, out = self._run_generator(
                tmp, small, store_dir, "out.json", extra=("--allow-unratified",)
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("sha256 mismatch", result.stderr)


class AnalyzerTests(unittest.TestCase):
    def _synthetic_responses(self, cfg: dict, listeners=6, flip_l1_l5=False):
        """Synthetic post-unblind response set for the 4 corpus-audio ladders.

        Detection counts per step are deterministic (not random): L1..L5 get
        3,4,5,6,6 of ``listeners`` detections (6,4,5,6,3 when L1/L5 are
        flipped), so monotonicity verdicts are stable and the flip is exact.
        """
        cfg_id = config_identity(cfg)
        condition_map = []
        operators = sorted(
            op
            for op, spec in cfg["ladders"].items()
            if spec["render"] == "corpus-audio"
        )
        cases = ["global-2", "global-3", "global-4", "global-5"]
        for op in operators:
            for case_id in cases:
                condition_map.append(
                    {
                        "stimulus_code": stimulus_code(cfg_id, case_id, "", None),
                        "case_id": case_id,
                        "operator": "",
                        "ladder_step": None,
                    }
                )
        for op in operators:
            for case_id in cases:
                for step in range(1, 6):
                    condition_map.append(
                        {
                            "stimulus_code": stimulus_code(cfg_id, case_id, op, step),
                            "case_id": case_id,
                            "operator": op,
                            "ladder_step": step,
                        }
                    )
        detect_counts = {1: 3, 2: 4, 3: 5, 4: 6, 5: 6}
        if flip_l1_l5:
            detect_counts[1], detect_counts[5] = detect_counts[5], detect_counts[1]
        abx = []
        for listener_index in range(listeners):
            trials = []
            for entry in condition_map:
                if entry["operator"]:
                    detected = listener_index < detect_counts[entry["ladder_step"]]
                    trials.append(
                        {
                            "stimulus_code": entry["stimulus_code"],
                            "detected": detected,
                            "identity_same_patch": entry["ladder_step"] <= 3,
                        }
                    )
            abx.append(
                {
                    "listener_code": "L%d" % listener_index,
                    "session_anchor_passed": True,
                    "abx_trials": trials,
                    "catch_trials": [
                        {
                            "stimulus_code": condition_map[0]["stimulus_code"],
                            "false_positive": False,
                        }
                        for _ in range(10)
                    ],
                    "mushra_blocks": [],
                }
            )
        return {
            "schema": "torchsynth-listening-responses",
            "schema_version": 1,
            "protocol_config_sha256": cfg_id,
            "unblinded": True,
            "condition_map": condition_map,
            "listeners": abx,
        }

    def test_fail_closed_on_unratified_config(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            cfg = load_example_config()
            config_path = tmp / "unratified.json"
            config_path.write_bytes(common.canonical_bytes(cfg))
            responses_path = tmp / "responses.json"
            responses_path.write_bytes(
                common.canonical_bytes(self._synthetic_responses(cfg))
            )
            result = run_python(
                ANALYZER,
                "--config",
                str(config_path),
                "--responses",
                str(responses_path),
            )
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("not ratified", result.stderr)

    def test_fail_closed_on_incomplete_ethics_box(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            cfg_path = ratified_config(tmp)
            cfg = json.loads(cfg_path.read_bytes().decode("utf-8"))
            cfg["ethics"]["consent_text"] = None
            cfg_path.write_bytes(common.canonical_bytes(cfg))
            responses_path = tmp / "responses.json"
            responses_path.write_bytes(
                common.canonical_bytes(self._synthetic_responses(cfg))
            )
            result = run_python(
                ANALYZER, "--config", str(cfg_path), "--responses", str(responses_path)
            )
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("ethics", result.stderr)

    def test_fail_closed_on_config_identity_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            cfg_path = ratified_config(tmp)
            cfg = json.loads(cfg_path.read_bytes().decode("utf-8"))
            responses = self._synthetic_responses(cfg)
            responses["protocol_config_sha256"] = "0" * 64
            responses_path = tmp / "responses.json"
            responses_path.write_bytes(common.canonical_bytes(responses))
            result = run_python(
                ANALYZER, "--config", str(cfg_path), "--responses", str(responses_path)
            )
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("binds config identity", result.stderr)

    def test_fail_closed_on_condition_map_drift(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            cfg_path = ratified_config(tmp)
            cfg = json.loads(cfg_path.read_bytes().decode("utf-8"))
            responses = self._synthetic_responses(cfg)
            responses["condition_map"][0]["stimulus_code"] = "ls1-forged"
            responses_path = tmp / "responses.json"
            responses_path.write_bytes(common.canonical_bytes(responses))
            result = run_python(
                ANALYZER, "--config", str(cfg_path), "--responses", str(responses_path)
            )
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("drift", result.stderr)

    def test_monotone_ladder_analyzes_rows_only(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            cfg_path = ratified_config(tmp)
            cfg = json.loads(cfg_path.read_bytes().decode("utf-8"))
            responses_path = tmp / "responses.json"
            responses_path.write_bytes(
                common.canonical_bytes(self._synthetic_responses(cfg))
            )
            out = tmp / "analysis.json"
            result = run_python(
                ANALYZER,
                "--config",
                str(cfg_path),
                "--responses",
                str(responses_path),
                "--out",
                str(out),
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            analysis = json.loads(out.read_bytes().decode("utf-8"))
            self.assertEqual(analysis["score_policy"], "rows_only_no_aggregate")
            self.assertFalse(
                [key for key in analysis if "score" in key and key != "score_policy"]
            )
            self.assertTrue(analysis["detection_rows"])
            ladder_rows = [
                row
                for row in analysis["ladder_rows"]
                if row["operator"] == "clip.round_step"
            ]
            self.assertTrue(ladder_rows)
            for row in ladder_rows:
                self.assertTrue(row["monotone"], row)
                self.assertGreater(row["spearman_rho"], 0.9)
            group = [
                row
                for row in analysis["monotonicity_rows"]
                if row.get("scope") == "all_cases"
                and row["operator"] == "clip.round_step"
            ]
            self.assertEqual(group[0]["verdict"], "valid_anchor")
            # identity intact at L1-L3, detection rises: no red flags expected
            self.assertEqual(analysis["red_flag_rows"], [])

    def test_flipped_ladder_step_flips_the_analysis(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            cfg_path = ratified_config(tmp)
            cfg = json.loads(cfg_path.read_bytes().decode("utf-8"))
            clean_path = tmp / "clean-responses.json"
            flipped_path = tmp / "flipped-responses.json"
            clean_path.write_bytes(
                common.canonical_bytes(self._synthetic_responses(cfg, flip_l1_l5=False))
            )
            flipped_path.write_bytes(
                common.canonical_bytes(self._synthetic_responses(cfg, flip_l1_l5=True))
            )
            out_clean, out_flipped = tmp / "clean.json", tmp / "flipped.json"
            result_clean = run_python(
                ANALYZER,
                "--config",
                str(cfg_path),
                "--responses",
                str(clean_path),
                "--out",
                str(out_clean),
            )
            result_flipped = run_python(
                ANALYZER,
                "--config",
                str(cfg_path),
                "--responses",
                str(flipped_path),
                "--out",
                str(out_flipped),
            )
            self.assertEqual(result_clean.returncode, 0, result_clean.stderr)
            self.assertEqual(result_flipped.returncode, 0, result_flipped.stderr)
            clean = json.loads(out_clean.read_bytes().decode("utf-8"))
            flipped = json.loads(out_flipped.read_bytes().decode("utf-8"))

            def group_row(analysis, operator):
                return next(
                    row
                    for row in analysis["monotonicity_rows"]
                    if row.get("scope") == "all_cases" and row["operator"] == operator
                )

            for operator in ("clip.round_step", "gain.dc_offset"):
                clean_row, flipped_row = (
                    group_row(clean, operator),
                    group_row(flipped, operator),
                )
                self.assertEqual(clean_row["verdict"], "valid_anchor")
                self.assertEqual(flipped_row["verdict"], "invalid_anchor")
                self.assertGreater(clean_row["spearman_rho"], 0.9)
                self.assertLess(flipped_row["spearman_rho"], 0.5)
            violation_rows = [
                row
                for row in flipped["monotonicity_rows"]
                if row.get("verdict") == "invalid_anchor" and "case_id" in row
            ]
            self.assertTrue(violation_rows)

    def test_identity_red_flag_reported_not_merged(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            cfg_path = ratified_config(tmp)
            cfg = json.loads(cfg_path.read_bytes().decode("utf-8"))
            responses = self._synthetic_responses(cfg, listeners=6)
            # identity broken even at sub-audible L1
            for listener in responses["listeners"]:
                for trial in listener["abx_trials"]:
                    trial["identity_same_patch"] = False
            responses_path = tmp / "responses.json"
            responses_path.write_bytes(common.canonical_bytes(responses))
            out = tmp / "analysis.json"
            result = run_python(
                ANALYZER,
                "--config",
                str(cfg_path),
                "--responses",
                str(responses_path),
                "--out",
                str(out),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            analysis = json.loads(out.read_bytes().decode("utf-8"))
            self.assertTrue(analysis["red_flag_rows"])
            reasons = {row["reason"] for row in analysis["red_flag_rows"]}
            self.assertIn("identity_failure_at_subaudible_magnitude", reasons)
            # rows stay independent: detection rows never absorb identity into a score
            for row in analysis["detection_rows"]:
                self.assertIn("identity_same_patch_proportion", row)
                self.assertNotIn("score", row)

    def test_invalid_listener_flagged(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            cfg_path = ratified_config(tmp)
            cfg = json.loads(cfg_path.read_bytes().decode("utf-8"))
            responses = self._synthetic_responses(cfg, listeners=2)
            responses["listeners"][1]["catch_trials"] = [
                {
                    "stimulus_code": responses["condition_map"][0]["stimulus_code"],
                    "false_positive": True,
                }
                for _ in range(10)
            ]
            responses["listeners"][1]["session_anchor_passed"] = False
            responses_path = tmp / "responses.json"
            responses_path.write_bytes(common.canonical_bytes(responses))
            out = tmp / "analysis.json"
            result = run_python(
                ANALYZER,
                "--config",
                str(cfg_path),
                "--responses",
                str(responses_path),
                "--out",
                str(out),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            analysis = json.loads(out.read_bytes().decode("utf-8"))
            flagged = [
                row for row in analysis["listener_validity_rows"] if row["invalidated"]
            ]
            self.assertEqual([row["listener_code"] for row in flagged], ["L1"])


class StatisticsTests(unittest.TestCase):
    def test_clopper_pearson_bounds_and_chance(self):
        low, high = clopper_pearson(5, 10, 0.05)
        self.assertLess(low, 0.5)
        self.assertGreater(high, 0.5)
        low, high = clopper_pearson(0, 10, 0.05)
        self.assertEqual(low, 0.0)
        self.assertLess(high, 0.4)
        low, high = clopper_pearson(10, 10, 0.05)
        self.assertGreater(low, 0.6)
        self.assertEqual(high, 1.0)
        self.assertGreater(
            clopper_pearson(8, 10, 0.05)[0], clopper_pearson(5, 10, 0.05)[0]
        )

    def test_spearman_matches_expected_orderings(self):
        self.assertAlmostEqual(spearman([1, 2, 3, 4, 5], [10, 20, 30, 40, 50]), 1.0)
        self.assertAlmostEqual(spearman([1, 2, 3, 4, 5], [50, 40, 30, 20, 10]), -1.0)
        self.assertAlmostEqual(spearman([1, 2, 3], [2, 1, 3]), 0.5)
        self.assertIsNone(spearman([1, 1, 1], [1, 2, 3]))
        self.assertIsNone(spearman([1], [1]))

    def test_stimulus_codes_are_deterministic_and_opaque(self):
        a = stimulus_code("cfg", "global-0", "gain.db", 3)
        b = stimulus_code("cfg", "global-0", "gain.db", 3)
        self.assertEqual(a, b)
        self.assertNotEqual(a, stimulus_code("cfg", "global-0", "gain.db", 4))
        self.assertNotEqual(a, stimulus_code("cfg2", "global-0", "gain.db", 3))
        self.assertTrue(a.startswith("ls1-"))
        self.assertNotIn("gain", a)


class CorpusAudioApplicationTests(unittest.TestCase):
    def test_landed_applications_compose_over_synthetic_samples(self):
        applications = common.corpus_audio_applications()
        self.assertEqual(
            set(applications),
            {
                "gain.db",
                "gain.dc_offset",
                "clip.round_step",
                "clip.saturation_ceiling",
                "gain.polarity",
            },
        )
        samples = [0.5, -0.25, 0.125, 0.0]
        degraded, detail = applications["gain.db"](samples, -6.0)
        self.assertAlmostEqual(degraded[0], 0.5 * (10 ** (-6.0 / 20.0)), places=6)
        self.assertEqual(detail["db"], -6.0)
        flipped, _ = applications["gain.polarity"](samples, None)
        self.assertEqual(flipped, [-0.5, 0.25, -0.125, 0.0])
        quantized, _ = applications["clip.round_step"]([0.3, -0.3, 0.125, 0.0], 0.5)
        self.assertEqual(quantized, [0.5, -0.5, 0.0, 0.0])
        saturated, _ = applications["clip.saturation_ceiling"](samples, 0.2)
        self.assertEqual(saturated, [0.2, -0.2, 0.125, 0.0])
        offset, _ = applications["gain.dc_offset"](samples, 0.1)
        self.assertAlmostEqual(offset[3], 0.1, places=6)

    def test_mp1_binding_attempt_records_the_landed_refusal(self):
        binding = common.mp1_plan_binding_attempt("global-0", "gain.db", -0.5)
        self.assertIsNone(binding["mp1_plan_identity"])
        self.assertIn("MutationError", binding["mp1_plan_refusal"])

    def test_f32_roundtrip_is_stable(self):
        samples = [0.0, 0.5, -0.25, 1.0, -1.0]
        self.assertEqual(common.f32le_decode(common.f32le_encode(samples)), samples)


if __name__ == "__main__":
    unittest.main()
