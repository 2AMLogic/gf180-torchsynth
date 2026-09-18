from __future__ import annotations

import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.directed import (  # noqa: E402
    COVERAGE_PATH,
    MANIFEST_PATH,
    boundary_points,
    build_manifest,
    coverage_report,
    encode,
    inventory_rows,
    manifest_identity,
    physical_value,
    resolve_patch,
    seal,
    validate_manifest,
)
from torchsynth_voice.inventory import load_json  # noqa: E402


class DirectedTests(unittest.TestCase):
    def setUp(self):
        self.manifest = build_manifest()
        self.rows = inventory_rows()

    def case(self, case_id):
        return next(c for c in self.manifest["cases"] if c["id"] == case_id)

    def reject(self, document, message):
        seal(document)
        with self.assertRaisesRegex(ValueError, message):
            validate_manifest(document)

    def test_committed_generation_and_coverage(self):
        validate_manifest(self.manifest)
        self.assertEqual(encode(self.manifest), MANIFEST_PATH.read_bytes())
        self.assertEqual(encode(build_manifest()), MANIFEST_PATH.read_bytes())
        report = coverage_report(self.manifest)
        self.assertEqual(encode(report), COVERAGE_PATH.read_bytes())
        self.assertEqual(set(report["parameters"]), set(self.rows))
        for name, cases in report["parameters"].items():
            self.assertEqual(set(cases), set(boundary_points(self.rows[name])))
        for case in self.manifest["cases"]:
            self.assertEqual(set(resolve_patch(self.manifest, case)), set(self.rows))

    def test_conversion_known_answers_and_float32_pi(self):
        self.assertEqual(physical_value(self.rows["adsr_1.attack"], 0.5), 0.5)
        self.assertEqual(physical_value(self.rows["lfo_1.frequency"], 0.5), 1.25)
        self.assertEqual(physical_value(self.rows["vco_1.mod_depth"], 0.5), 0)
        self.assertEqual(physical_value(self.rows["lfo_1.mod_depth"], 0.5), 5)
        for endpoint, expected in ((0, -3.1415927410125732), (1, 3.1415927410125732)):
            self.assertEqual(
                physical_value(self.rows["vco_1.initial_phase"], endpoint), expected
            )
        self.assertAlmostEqual(physical_value(self.rows["mixer.noise"], 0.5), 2**-40)

    def test_positional_missing_unknown_and_duplicate_names(self):
        for change in ("array", "missing", "unknown"):
            with self.subTest(change=change):
                doc = copy.deepcopy(self.manifest)
                if change == "array":
                    doc["base"] = list(doc["base"].values())
                elif change == "missing":
                    del doc["base"]["keyboard.duration"]
                else:
                    doc["cases"][0]["overrides"]["unknown.name"] = {
                        "normalized": 0,
                        "physical": 0,
                    }
                self.reject(doc, "name-keyed|names")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text('{"base": {}, "base": {}}')
            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                load_json(path)

    def test_bad_ranges_conversions_and_non_finite_rejected(self):
        for field, value in (
            ("normalized", -0.1),
            ("normalized", 1.01),
            ("normalized", True),
            ("normalized", 0.1),
            ("physical", 999),
            ("physical", float("nan")),
            ("physical", float("inf")),
        ):
            with self.subTest(field=field, value=value):
                doc = copy.deepcopy(self.manifest)
                doc["base"]["keyboard.duration"][field] = value
                with self.assertRaises(ValueError):
                    seal(doc)
                    validate_manifest(doc)

    def test_duplicate_and_missing_cases_rejected(self):
        self.manifest["cases"].append(copy.deepcopy(self.manifest["cases"][0]))
        self.reject(self.manifest, "duplicate")
        self.manifest["cases"].pop()
        self.manifest["cases"].pop(0)
        self.reject(self.manifest, "coverage")

    def test_all_zero_lfo_weights_rejected_even_when_inaudible(self):
        for lfo in ("lfo_1", "lfo_2"):
            for weight in (0.0, 2**-149):
                doc = copy.deepcopy(self.manifest)
                for shape in ("sin", "tri", "saw", "rsaw", "sqr"):
                    doc["cases"][0]["overrides"][f"{lfo}.{shape}"] = {
                        "normalized": weight,
                        "physical": weight,
                    }
                self.reject(doc, "zero.*weights")

    def test_route_isolation_rejects_competing_input_and_silent_carrier(self):
        case = self.case("route:mod_matrix.lfo_1->vco_1_pitch")
        case["overrides"]["mod_matrix.lfo_2->vco_1_pitch"] = {
            "normalized": 1.0,
            "physical": 1.0,
        }
        self.reject(self.manifest, "isolation")
        del case["overrides"]["mod_matrix.lfo_2->vco_1_pitch"]
        case["overrides"]["mixer.vco_1"] = {"normalized": 0.0, "physical": 0.0}
        self.reject(self.manifest, "carrier")

    def test_waveform_regimes_are_continuous(self):
        report = coverage_report(self.manifest)
        self.assertEqual(len(report["waveforms"]), 15)
        self.assertEqual(
            report["discrete_modes"],
            {"count": 0, "reason": "All 78 inventory parameters are continuous."},
        )
        case = self.case("waveform:lfo_1:sin")
        case["overrides"]["lfo_1.tri"] = {"normalized": 0.5, "physical": 0.5}
        self.reject(self.manifest, "waveform")

    def test_adsr_silence_stress_and_trace_obligations(self):
        report = coverage_report(self.manifest)
        self.assertEqual(len(report["envelopes"]), 24)
        self.assertEqual(set(report["special"]), {"silence", "near-silence", "stress"})
        self.assertEqual(len(report["routes"]), 20)
        self.assertGreaterEqual(len(report["traces"]), 25)
        for trace in report["traces"].values():
            self.assertTrue(trace["cases"])
            self.assertEqual(trace["status"], "planned")
            self.assertTrue(trace["isolation"])
        self.assertEqual(report["audio_render"], "not_run")

    def test_isolated_source_and_supporting_envelope_cannot_be_silenced(self):
        for case_id, name in (
            ("source:noise", "mixer.noise"),
            ("waveform:lfo_1:sin", "adsr_1.sustain"),
            ("normalization:tie", "adsr_1.sustain"),
        ):
            doc = copy.deepcopy(self.manifest)
            case = next(c for c in doc["cases"] if c["id"] == case_id)
            case["overrides"][name] = {"normalized": 0.0, "physical": 0.0}
            self.reject(doc, "carrier|context|envelope")

    def test_normalization_targets_are_exact_neighbors_and_unmeasured(self):
        report = coverage_report(self.manifest)
        expected = {"below": 1 - 2**-24, "tie": 1.0, "above": 1 + 2**-23}
        for relation, peak in expected.items():
            target = report["normalization"][relation]
            self.assertEqual(target["target_peak"], peak)
            self.assertEqual(target["peak_status"], "unmeasured")
            self.assertTrue(target["render_obligation"])
        self.case("normalization:tie")["normalization_target"]["peak_status"] = (
            "measured"
        )
        self.reject(self.manifest, "normalization")

    def test_unsupported_claims_and_provenance_rejected(self):
        for field, value in (
            ("kind", "discrete"),
            ("target", "no.such.trace"),
            ("purpose", ""),
            ("measured_peak", 1.0),
        ):
            doc = copy.deepcopy(self.manifest)
            doc["cases"][0][field] = value
            self.reject(doc, "case|coverage|fields|purpose")
        self.manifest["inventory_sha256"] = "0" * 64
        self.reject(self.manifest, "inventory")

    def test_fixture_protocol_preserves_clip_and_separates_hardware_width(self):
        protocol = self.manifest["fixture_protocol"]
        self.assertEqual(protocol["qualification_batch_size"], 32)
        self.assertEqual(protocol["hardware_execution_width"], 1)
        self.assertEqual(protocol["output_samples"], 176400)
        self.assertEqual(protocol["noise_slot"], 0)
        self.assertEqual(protocol["scalar_batched_equivalence"], "unqualified")
        protocol["hardware_execution_width"] = 32
        self.reject(self.manifest, "fixture protocol")

    def test_any_patch_change_changes_hash_and_content_version(self):
        original = manifest_identity(self.manifest)
        self.case("waveform:lfo_1:blend")["overrides"]["lfo_1.sin"] = {
            "normalized": 0.75,
            "physical": 0.75,
        }
        changed = manifest_identity(self.manifest)
        self.assertNotEqual(original["sha256"], changed["sha256"])
        self.assertNotEqual(original["version"], changed["version"])
        with self.assertRaisesRegex(ValueError, "identity"):
            validate_manifest(self.manifest)
        seal(self.manifest)
        validate_manifest(self.manifest)

    def test_cli_check_is_read_only_and_detects_manifest_or_report_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "manifest.json"
            coverage = Path(directory) / "coverage.json"
            command = [
                sys.executable,
                str(ROOT / "tools/generate_directed_fixtures.py"),
                "--manifest",
                str(manifest),
                "--coverage",
                str(coverage),
            ]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run(
                command + ["--check"], capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            for path in (manifest, coverage):
                original = path.read_bytes()
                path.write_bytes(original + b" ")
                result = subprocess.run(
                    command + ["--check"], capture_output=True, text=True
                )
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertEqual(path.read_bytes(), original + b" ")
                path.write_bytes(original)

    def test_no_torch_import_in_fresh_interpreter(self):
        code = "import sys; from torchsynth_voice.directed import build_manifest; build_manifest(); assert 'torch' not in sys.modules"
        result = subprocess.run(
            [sys.executable, "-c", "import sys; sys.path.insert(0, 'src'); " + code],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
