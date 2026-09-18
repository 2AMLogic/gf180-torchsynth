"""PDK-, Docker-, and Torch-free tests for the release-era preflight."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "release_probe", Path(__file__).with_name("probe.py")
)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class PreflightTests(unittest.TestCase):
    def test_changed_source_rejected_before_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "torchsynth"
            package.mkdir()
            (package / "config.py").write_text("raise RuntimeError('must not import')")
            manifest = {"files": {"torchsynth/config.py": "0" * 64}}
            with patch.dict(sys.modules, {"torch": None, "torchsynth": None}):
                with self.assertRaisesRegex(
                    ValueError, "source hash mismatch: torchsynth/config.py"
                ):
                    probe.run_probe(root, manifest, root)

    def test_missing_source_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                ValueError, "missing source: torchsynth/config.py"
            ):
                probe.validate_source(
                    Path(directory), {"files": {"torchsynth/config.py": "0" * 64}}
                )

    def test_manifest_includes_checkout_only_files(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                ValueError, "missing source: examples/examples.py"
            ):
                probe.validate_source(
                    Path(directory),
                    {
                        "files": {},
                        "source_checkout_only_files": {
                            "examples/examples.py": "0" * 64
                        },
                    },
                )

    def test_fixture_identity_matches_project(self):
        root = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(root / "src"))
        from torchsynth_voice.identity import SoundIdentity

        for index in (0, 31, 32, 39942):
            self.assertEqual(probe.identity(index), SoundIdentity(index).to_dict())

    def test_parameter_hash_is_named_and_order_independent(self):
        left = {"vco.pitch": 0.125, "mixer.level": 0.5}
        right = {"mixer.level": 0.5, "vco.pitch": 0.125}
        self.assertEqual(probe.json_bytes(left), probe.json_bytes(right))
        self.assertEqual(json.loads(probe.json_bytes(left)), left)
        with self.assertRaises(ValueError):
            probe.json_bytes({"vco.pitch": float("nan")})


if __name__ == "__main__":
    unittest.main()
