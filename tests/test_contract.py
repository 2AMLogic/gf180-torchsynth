from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.contract import UpstreamContract, sha256_file  # noqa: E402


class ContractTests(unittest.TestCase):
    def test_manifest_has_full_pins(self) -> None:
        contract = UpstreamContract.load()
        self.assertEqual(len(contract.target_commit), 40)
        self.assertEqual(contract.data["profile"], "torchsynth-1-voice-default")
        self.assertEqual(contract.data["expected"]["latent_parameter_count"], 78)
        self.assertEqual(contract.data["expected"]["output_samples"], 176400)
        self.assertEqual(len(contract.files), 6)
        self.assertEqual(len(contract.source_checkout_only_files), 1)
        self.assertTrue(all(len(value) == 64 for value in contract.files.values()))

    def test_hash_verifier_reports_missing_and_changed_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "wanted.txt"
            target.write_text("correct\n", encoding="utf-8")
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "target_commit": "0" * 40,
                        "files": {
                            "wanted.txt": sha256_file(target),
                            "missing.txt": "0" * 64,
                        },
                    }
                ),
                encoding="utf-8",
            )
            contract = UpstreamContract.load(manifest)
            self.assertEqual(contract.verify_source_tree(root), ["missing missing.txt"])
            target.write_text("changed\n", encoding="utf-8")
            errors = contract.verify_source_tree(root)
            self.assertEqual(len(errors), 2)
            self.assertTrue(any(error.startswith("hash wanted.txt") for error in errors))


if __name__ == "__main__":
    unittest.main()
