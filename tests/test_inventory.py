from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.contract import sha256_file  # noqa: E402
from torchsynth_voice.inventory import (  # noqa: E402
    ANNOTATIONS_PATH,
    INVENTORY_PATH,
    apply_nebula,
    encode_inventory,
    generate_inventory,
    load_json,
    validate_annotations,
    validate_inventory,
)


class InventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inventory = load_json(INVENTORY_PATH)
        self.annotations = load_json(ANNOTATIONS_PATH)

    def test_committed_schema_and_annotations(self) -> None:
        validate_inventory(self.inventory)
        names = {row["name"] for row in self.inventory["parameters"]}
        validate_annotations(self.annotations, names)
        self.assertEqual(len(names), 78)
        self.assertEqual(self.inventory["nebula_entry_count"], 156)
        self.assertEqual(
            self.inventory["annotations_sha256"], sha256_file(ANNOTATIONS_PATH)
        )
        for row in self.inventory["parameters"]:
            self.assertEqual(
                row["annotation"], self.annotations["parameters"][row["name"]]
            )

    def test_orders_are_independent_permutations(self) -> None:
        rows = self.inventory["parameters"]
        forward = sorted(rows, key=lambda row: row["forward_position"])
        randomized = sorted(rows, key=lambda row: row["randomization_position"])
        self.assertEqual(forward[0]["name"], "keyboard.midi_f0")
        self.assertEqual(forward[1]["name"], "keyboard.duration")
        self.assertEqual(randomized[0]["name"], "adsr_1.alpha")
        self.assertEqual(randomized, sorted(rows, key=lambda row: row["torch_name"]))
        for field in ("forward_position", "randomization_position"):
            self.assertEqual(sorted(row[field] for row in rows), list(range(78)))

    def test_selectors_are_explicitly_continuous(self) -> None:
        for name, annotation in self.annotations["parameters"].items():
            self.assertEqual(annotation["interpretation"], "continuous", name)
        for lfo in ("lfo_1", "lfo_2"):
            for shape in ("sin", "tri", "saw", "rsaw", "sqr"):
                annotation = self.annotations["parameters"][f"{lfo}.{shape}"]
                self.assertEqual(annotation["selector"], "continuous_weight")
                self.assertIn("zero", annotation["notes"])
        self.assertEqual(
            self.annotations["parameters"]["vco_2.shape"]["selector"],
            "continuous_morph",
        )

    def test_duplicate_missing_extra_inventory_names_rejected(self) -> None:
        for mutation in ("duplicate", "missing", "extra"):
            with self.subTest(mutation=mutation):
                document = copy.deepcopy(self.inventory)
                rows = document["parameters"]
                if mutation == "duplicate":
                    rows[-1] = rows[0]
                elif mutation == "missing":
                    rows.pop()
                else:
                    rows.append(copy.deepcopy(rows[0]))
                with self.assertRaises(ValueError):
                    validate_inventory(document)

    def test_invalid_numeric_fields_and_orders_rejected(self) -> None:
        for field, value in (
            ("minimum", float("nan")),
            ("maximum", float("inf")),
            ("minimum", True),
            ("maximum", "2"),
            ("curve", 0),
            ("symmetric", 1),
            ("forward_position", 0.5),
            ("randomization_position", -1),
            ("minimum", 1000),
        ):
            with self.subTest(field=field, value=value):
                document = copy.deepcopy(self.inventory)
                document["parameters"][0][field] = value
                with self.assertRaises(ValueError):
                    validate_inventory(document)
        self.inventory["parameters"][1]["forward_position"] = 0
        with self.assertRaises(ValueError):
            validate_inventory(self.inventory)

    def test_annotation_schema_and_exact_coverage(self) -> None:
        names = set(self.annotations["parameters"])
        for mutation in ("missing", "extra", "unit", "meaning", "selector", "unknown"):
            with self.subTest(mutation=mutation):
                document = copy.deepcopy(self.annotations)
                row = document["parameters"]["adsr_1.alpha"]
                if mutation == "missing":
                    del document["parameters"]["adsr_1.alpha"]
                elif mutation == "extra":
                    document["parameters"]["unknown.parameter"] = row.copy()
                elif mutation == "unit":
                    row["unit"] = None
                elif mutation == "unknown":
                    row["unreviewed"] = True
                else:
                    del row[mutation]
                with self.assertRaises(ValueError):
                    validate_annotations(document, names)

    def test_nebula_duplicate_missing_extra_and_invalid_values_rejected(self) -> None:
        facts = [{"name": "keyboard.duration", "curve": 1, "symmetric": False}]
        entries = [
            {"name": ["keyboard", "duration", "curve"], "value": 0.5},
            {"name": ["keyboard", "duration", "symmetric"], "value": False},
        ]
        apply_nebula(facts, entries)
        self.assertEqual(facts[0]["curve"], 0.5)
        invalid = [entries[:1], entries + entries[:1]]
        for key, value in (
            ("missing", 1),
            ("curve", True),
            ("curve", float("nan")),
            ("curve", -1),
            ("symmetric", 0),
            ("minimum", 0),
        ):
            changed = copy.deepcopy(entries)
            changed[0] = {"name": ["keyboard", "duration", key], "value": value}
            invalid.append(changed)
        changed = copy.deepcopy(entries)
        changed[0]["name"][0] = "unknown"
        invalid.append(changed)
        for entries in invalid:
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                apply_nebula(copy.deepcopy(facts), entries)

    def test_duplicate_json_keys_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text('{"parameters": {}, "parameters": {}}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_json(path)


@unittest.skipUnless(
    os.environ.get("TORCHSYNTH_ROOT"), "set TORCHSYNTH_ROOT for pinned-source tests"
)
class UpstreamInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = Path(os.environ["TORCHSYNTH_ROOT"])

    def test_regeneration_is_byte_exact_twice(self) -> None:
        first = encode_inventory(generate_inventory(self.source))
        second = encode_inventory(generate_inventory(self.source))
        self.assertEqual(first, second)
        self.assertEqual(first, INVENTORY_PATH.read_bytes())

    def test_effective_nebula_overrides(self) -> None:
        rows = {
            row["name"]: row for row in generate_inventory(self.source)["parameters"]
        }
        self.assertEqual(rows["lfo_1.mod_depth"]["minimum"], -10.0)
        self.assertEqual(rows["lfo_1.mod_depth"]["maximum"], 20.0)
        entries = load_json(self.source / "torchsynth/nebulae/voice/default.json")
        self.assertEqual(len(entries), 156)
        self.assertEqual(len({tuple(entry["name"]) for entry in entries}), 156)
        for entry in entries:
            module, parameter, hyperparameter = entry["name"]
            self.assertEqual(
                rows[f"{module}.{parameter}"][hyperparameter], entry["value"]
            )

    def test_phase_ranges_use_upstream_float32_pi(self) -> None:
        document = generate_inventory(self.source)
        phases = [
            row for row in document["parameters"] if row["parameter"] == "initial_phase"
        ]
        self.assertEqual(len(phases), 4)
        for row in phases:
            self.assertEqual(row["minimum"], -3.1415927410125732)
            self.assertEqual(row["maximum"], 3.1415927410125732)

    def test_changed_source_range_and_nebula_hashes_fail_before_extraction(
        self,
    ) -> None:
        for relative, old, new in (
            ("torchsynth/module.py", "0.0, 2.0", "0.0, 3.0"),
            ("torchsynth/nebulae/voice/default.json", "0.5", "0.6"),
            ("torchsynth/synth.py", '"keyboard"', '"renamed_keyboard"'),
        ):
            with (
                self.subTest(relative=relative),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                shutil.copytree(self.source / "torchsynth", root / "torchsynth")
                path = root / relative
                original = path.read_text(encoding="utf-8")
                changed = original.replace(old, new, 1)
                self.assertNotEqual(original, changed)
                path.write_text(changed, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "hash " + relative):
                    generate_inventory(root)

    def test_cli_check_rejects_changed_range_and_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            path.write_bytes(INVENTORY_PATH.read_bytes())
            command = [
                sys.executable,
                str(ROOT / "tools/generate_parameter_inventory.py"),
                "--torchsynth-root",
                str(self.source),
                "--output",
                str(path),
                "--check",
            ]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            document = json.loads(path.read_text(encoding="utf-8"))
            document["parameters"][0]["maximum"] += 1
            changed = encode_inventory(document)
            path.write_bytes(changed)
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("differs", result.stderr)
            self.assertEqual(path.read_bytes(), changed)


if __name__ == "__main__":
    unittest.main()
