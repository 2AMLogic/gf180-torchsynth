"""Source gates and A/B diagnostics; no Torch, Docker or network required."""

import copy
import difflib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

import compare_sources as compare


class SourceComparisonTests(unittest.TestCase):
    def test_release_patch_keeps_all_other_bytes_and_rejects_changed_nebula(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "original"
            (root / "torchsynth").mkdir(parents=True)
            patch = copy.deepcopy(compare.load_manifest()["patch"])
            source = root / patch["path"]
            source.write_text(patch["before"])
            nebula = root / "default.json"
            nebula.write_text("[]\n")
            original = compare.tree_identity(root)
            source.write_text(patch["after"])
            effective = compare.tree_identity(root)
            source.write_text(patch["before"])
            patch["unified_diff"] = "".join(
                difflib.unified_diff(
                    [patch["before"]],
                    [patch["after"]],
                    fromfile="a/" + patch["path"],
                    tofile="b/" + patch["path"],
                    n=0,
                )
            )
            patch["sha256"] = compare.sha256(patch["unified_diff"].encode())
            patch["patched_file_sha256"] = compare.sha256(patch["after"].encode())
            patch["patched_tree_sha256"] = effective["tree_sha256"]
            manifest = {"release": original, "patch": patch}
            destination = Path(directory) / "patched"
            self.assertEqual(
                compare.patched_release(root, destination, manifest), effective
            )
            self.assertEqual(
                (destination / "default.json").read_bytes(), nebula.read_bytes()
            )
            self.assertEqual(source.read_text(), patch["before"])
            nebula.write_text("[1]\n")
            with self.assertRaisesRegex(ValueError, "source tree mismatch"):
                compare.patched_release(root, Path(directory) / "refused", manifest)

    def test_source_change_and_extra_file_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "synth.py"
            source.write_text("raise RuntimeError('must not import')\n")
            expected = compare.tree_identity(root)
            compare.validate_tree(root, expected)
            source.write_text("# changed DSP\n")
            with self.assertRaisesRegex(ValueError, "source tree mismatch"):
                compare.validate_tree(root, expected)
            source.write_text("raise RuntimeError('must not import')\n")
            (root / "shadow.py").write_text("# unexpected import candidate\n")
            with self.assertRaisesRegex(ValueError, "source tree mismatch"):
                compare.validate_tree(root, expected)

    def test_source_refusal_happens_before_import_in_fresh_process(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [
                    sys.executable,
                    compare.__file__,
                    "render",
                    "--side",
                    "release",
                    "--source-root",
                    directory,
                    "--output",
                    directory,
                ],
                capture_output=True,
                text=True,
            )
            report = json.loads(result.stdout)
            self.assertEqual(result.returncode, 1)
            self.assertFalse(report["torchsynth_imported"])
            self.assertIn("source tree mismatch", report["error"])

    def test_patch_is_exact_and_hash_bound(self):
        manifest = compare.load_manifest()
        patch = manifest["patch"]
        self.assertEqual(
            compare.sha256(patch["unified_diff"].encode()), patch["sha256"]
        )
        self.assertEqual(
            patch["before"],
            "from pytorch_lightning.core.lightning import LightningModule\n",
        )
        self.assertEqual(patch["after"], "from lightning import LightningModule\n")
        changed = copy.deepcopy(manifest)
        changed["patch"]["after"] += "# extra edit\n"
        with self.assertRaisesRegex(ValueError, "unapproved patch"):
            compare.validate_patch(changed["patch"])

    def test_sample_change_reports_first_max_rms(self):
        left = struct.pack("<3f", 0, 1, -1)
        right = struct.pack("<3f", 0, 0.5, -1)
        result = compare.compare_samples(left, right)
        self.assertFalse(result["equal_bytes"])
        self.assertEqual(result["first_different_sample"], 1)
        self.assertEqual(result["max_abs_difference"], 0.5)
        self.assertAlmostEqual(result["rms_difference"], (0.25 / 3) ** 0.5)
        self.assertTrue(compare.compare_samples(left, left)["equal_bytes"])

    def test_signed_zero_is_byte_difference(self):
        result = compare.compare_samples(struct.pack("<f", 0), struct.pack("<f", -0.0))
        self.assertFalse(result["equal_bytes"])
        self.assertEqual(result["first_different_sample"], 0)
        self.assertEqual(result["max_abs_difference"], 0)

    def test_bad_sample_lengths_and_nonfinite_fail(self):
        for left, right in (
            (b"", b""),
            (b"x", b"x"),
            (b"1234", b"12345678"),
            (struct.pack("<f", float("nan")), struct.pack("<f", 0)),
        ):
            with self.subTest(left=left, right=right), self.assertRaises(ValueError):
                compare.compare_samples(left, right)

    def test_parameter_noise_and_configuration_change_fail(self):
        baseline = {
            "configuration": {"nebula": "default"},
            "normalized": {"mixer.noise": 0.25},
            "physical": {"mixer.noise": 0.001},
            "noise_sha256": "a" * 64,
        }
        self.assertEqual(compare.input_differences(baseline, baseline), [])
        for key in baseline:
            changed = copy.deepcopy(baseline)
            changed[key] = "changed"
            self.assertEqual(compare.input_differences(baseline, changed), [key])

    def test_normalization_cases_require_actual_threshold_crossing(self):
        compare.require_probe("normalization-off", 0.5, 0.1)
        compare.require_probe("normalization-on", 1.5, 0.1)
        compare.require_probe("noise-bearing", 0.5, 0.1)
        for name, peak, noise in (
            ("normalization-off", 1.1, 0.1),
            ("normalization-on", 1.0, 0.1),
            ("noise-bearing", 0.5, 0.0),
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                compare.require_probe(name, peak, noise)

    def test_comparison_cli_fails_for_changed_parameter_noise_or_sample(self):
        # Synthetic data tests the refusal/diagnostic pipeline, not source fidelity.
        # The separate Docker run supplies the real source-equivalence evidence.
        for mutation in ("parameter", "noise", "sample", "missing-probe"):
            with (
                self.subTest(mutation=mutation),
                tempfile.TemporaryDirectory() as directory,
            ):
                output = Path(directory)
                raw = struct.pack("<f", 0.25) * 176400
                artifact = {"file": "samples.f32le", "sha256": compare.sha256(raw)}
                sounds = [
                    {
                        "name": name,
                        "configuration": {},
                        "normalized": {"mixer.noise": 0.2},
                        "physical": {"mixer.noise": 0.2},
                        "noise_sha256": compare.sha256(raw),
                        "artifacts": {
                            kind: dict(artifact)
                            for kind in (
                                "audio",
                                "pre_normalization",
                                "noise",
                                "noise_contribution",
                            )
                        },
                    }
                    for name in compare.PROBES
                ]
                for side in ("selected", "release"):
                    (output / side).mkdir()
                    (output / side / "samples.f32le").write_bytes(raw)
                    run = {
                        "status": "passed",
                        "runtime": {},
                        "sounds": copy.deepcopy(sounds),
                    }
                    if side == "release":
                        if mutation == "parameter":
                            run["sounds"][0]["normalized"]["mixer.noise"] = 0.3
                        elif mutation == "noise":
                            run["sounds"][0]["noise_sha256"] = "0" * 64
                        elif mutation == "sample":
                            changed = struct.pack("<f", 0.5) + raw[4:]
                            (output / side / "changed.f32le").write_bytes(changed)
                            run["sounds"][0]["artifacts"]["audio"] = {
                                "file": "changed.f32le",
                                "sha256": compare.sha256(changed),
                            }
                        else:
                            run["sounds"].pop()
                    (output / (side + ".json")).write_text(json.dumps(run))
                    (output / (side + ".stderr")).write_text("")
                for filename in (
                    "image.id",
                    "image-platform.txt",
                    "docker-server.txt",
                    "build.log",
                ):
                    (output / filename).write_text("unit-test\n")
                (output / "negative.json").write_text('{"status": "passed"}')
                result = subprocess.run(
                    [
                        sys.executable,
                        compare.__file__,
                        "compare",
                        "--output",
                        str(output),
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 1)
                if mutation == "missing-probe":
                    self.assertIn(
                        "missing, duplicate or reordered probe", result.stderr
                    )
                    continue
                report = json.loads((output / "source-equivalence.json").read_text())
                self.assertEqual(report["status"], "failed")
                case = report["comparisons"][0]
                if mutation == "sample":
                    self.assertEqual(
                        case["comparisons"]["audio"]["first_different_sample"], 0
                    )
                    self.assertEqual(
                        case["comparisons"]["audio"]["max_abs_difference"], 0.25
                    )
                    self.assertGreater(
                        case["comparisons"]["audio"]["rms_difference"], 0
                    )
                else:
                    self.assertEqual(
                        case["input_differences"],
                        ["normalized" if mutation == "parameter" else "noise_sha256"],
                    )
                    self.assertEqual(case["comparisons"], {})


if __name__ == "__main__":
    unittest.main()
