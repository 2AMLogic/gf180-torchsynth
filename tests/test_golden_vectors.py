"""Golden-vector schema, loader, and first-mismatch reporter (issue #68)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import golden_vectors as gv  # noqa: E402

REGISTRY = gv.load_registry_document()
INVENTORY = gv.load_inventory_document()
PARAMETER_NAMES = {row["name"] for row in INVENTORY["parameters"]}


def make_vector(trace_values=8, parameter=None) -> dict:
    """A valid synthetic-bringup vector for the loader/reporter tests."""
    vector = {
        "schema": gv.VECTOR_SCHEMA,
        "schema_version": gv.SCHEMA_VERSION,
        "trace_registry_version": REGISTRY["semantic_version"],
        "parameter_inventory_commit": INVENTORY["source"]["commit"],
        "clip": {
            "profile": gv.SYNTHETIC_PROFILE,
            "sample_rate_hz": 44100,
            "sample_count": trace_values,
            "control_rate_hz": 441,
            "control_count": trace_values,
        },
        "parameters": {parameter or "adsr_1.alpha": 0.5},
        "traces": [
            {
                "name": "mixer.output",
                "kind": "audio",
                "encoding": "synthetic-int",
                "cycle_start": 0,
                "values": list(range(trace_values)),
            }
        ],
    }
    vector["content_hash"] = gv.compute_content_hash(vector)
    return vector


class TestCanonicalClipProfile(unittest.TestCase):
    """The canonical timing is grounded in the live registry, not invented."""

    def test_constants_match_registry_traces(self):
        by_name = {t["name"]: t for t in REGISTRY["traces"]}
        audio = by_name["mixer.output"]
        control = by_name["adsr_1.output"]
        self.assertEqual(gv.CANONICAL_SAMPLE_RATE_HZ, audio["rate_hz"])
        self.assertEqual(gv.CANONICAL_SAMPLE_COUNT, audio["sample_count"])
        self.assertEqual(gv.CANONICAL_CONTROL_RATE_HZ, control["rate_hz"])
        self.assertEqual(gv.CANONICAL_CONTROL_COUNT, control["sample_count"])
        self.assertEqual(gv.CANONICAL_SAMPLE_COUNT, 176400)
        self.assertEqual(gv.CANONICAL_SAMPLE_RATE_HZ, 44100)
        self.assertEqual(gv.CANONICAL_CONTROL_RATE_HZ, 441)
        self.assertEqual(gv.CANONICAL_CONTROL_COUNT, 1764)

    def test_canonical_profile_enforces_canonical_timing(self):
        vector = make_vector()
        vector["clip"] = {
            "profile": gv.CANONICAL_PROFILE,
            "sample_rate_hz": 44100,
            "sample_count": 8,
            "control_rate_hz": 441,
            "control_count": 8,
        }
        vector["content_hash"] = gv.compute_content_hash(vector)
        with self.assertRaises(gv.VectorError):
            gv.load_vector(vector, registry=REGISTRY, inventory=INVENTORY)

    def test_unknown_profile_rejected(self):
        vector = make_vector()
        vector["clip"]["profile"] = "mystery-profile"
        vector["content_hash"] = gv.compute_content_hash(vector)
        with self.assertRaises(gv.VectorError):
            gv.load_vector(vector, registry=REGISTRY, inventory=INVENTORY)


class TestLoader(unittest.TestCase):
    def test_valid_vector_round_trips_through_a_file(self):
        vector = make_vector()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "golden.json"
            path.write_text(json.dumps(vector), encoding="utf-8")
            loaded = gv.load_vector(path)
        self.assertEqual(loaded["traces"][0]["values"], list(range(8)))

    def test_unknown_trace_name_rejected(self):
        vector = make_vector()
        vector["traces"][0]["name"] = "not.a.registry.trace"
        vector["content_hash"] = gv.compute_content_hash(vector)
        with self.assertRaises(gv.VectorError):
            gv.load_vector(vector, registry=REGISTRY, inventory=INVENTORY)

    def test_unknown_parameter_name_rejected(self):
        vector = make_vector(parameter="not.an.inventory.parameter")
        with self.assertRaises(gv.VectorError):
            gv.load_vector(vector, registry=REGISTRY, inventory=INVENTORY)

    def test_all_78_inventory_names_accepted(self):
        self.assertEqual(len(PARAMETER_NAMES), 78)
        vector = make_vector()
        vector["parameters"] = {name: 0.25 for name in sorted(PARAMETER_NAMES)}
        vector["content_hash"] = gv.compute_content_hash(vector)
        gv.load_vector(vector, registry=REGISTRY, inventory=INVENTORY)

    def test_trace_kind_must_match_registry(self):
        vector = make_vector()
        vector["traces"][0]["kind"] = "control"
        vector["content_hash"] = gv.compute_content_hash(vector)
        with self.assertRaises(gv.VectorError):
            gv.load_vector(vector, registry=REGISTRY, inventory=INVENTORY)

    def test_scalar_trace_single_value(self):
        vector = make_vector()
        vector["traces"] = [
            {
                "name": "mixer.peak",
                "kind": "scalar",
                "encoding": "synthetic-int",
                "cycle_start": 0,
                "values": [3],
            }
        ]
        vector["content_hash"] = gv.compute_content_hash(vector)
        gv.load_vector(vector, registry=REGISTRY, inventory=INVENTORY)

        vector["traces"][0]["values"] = [3, 4]
        vector["content_hash"] = gv.compute_content_hash(vector)
        with self.assertRaises(gv.VectorError):
            gv.load_vector(vector, registry=REGISTRY, inventory=INVENTORY)

    def test_canonical_profile_requires_full_registry_length(self):
        vector = make_vector()
        vector["clip"] = {
            "profile": gv.CANONICAL_PROFILE,
            "sample_rate_hz": gv.CANONICAL_SAMPLE_RATE_HZ,
            "sample_count": gv.CANONICAL_SAMPLE_COUNT,
            "control_rate_hz": gv.CANONICAL_CONTROL_RATE_HZ,
            "control_count": gv.CANONICAL_CONTROL_COUNT,
        }
        vector["content_hash"] = gv.compute_content_hash(vector)
        with self.assertRaises(gv.VectorError) as caught:
            gv.load_vector(vector, registry=REGISTRY, inventory=INVENTORY)
        self.assertIn("176400", str(caught.exception))


class TestVersionChecks(unittest.TestCase):
    def test_registry_version_mismatch_rejected(self):
        vector = make_vector()
        vector["trace_registry_version"] = "voice-traces-v0"
        vector["content_hash"] = gv.compute_content_hash(vector)
        with self.assertRaises(gv.VectorError) as caught:
            gv.load_vector(vector, registry=REGISTRY, inventory=INVENTORY)
        self.assertIn("version mismatch", str(caught.exception))

    def test_inventory_commit_mismatch_rejected(self):
        vector = make_vector()
        vector["parameter_inventory_commit"] = "0" * 40
        vector["content_hash"] = gv.compute_content_hash(vector)
        with self.assertRaises(gv.VectorError):
            gv.load_vector(vector, registry=REGISTRY, inventory=INVENTORY)

    def test_unsupported_schema_rejected(self):
        vector = make_vector()
        vector["schema"] = "someone-else/golden-vector-v9"
        vector["content_hash"] = gv.compute_content_hash(vector)
        with self.assertRaises(gv.VectorError):
            gv.load_vector(vector, registry=REGISTRY, inventory=INVENTORY)


class TestContentHash(unittest.TestCase):
    def test_tampered_value_rejected(self):
        vector = make_vector()
        vector["traces"][0]["values"][2] = 999
        with self.assertRaises(gv.VectorError) as caught:
            gv.load_vector(vector, registry=REGISTRY, inventory=INVENTORY)
        self.assertIn("content_hash mismatch", str(caught.exception))

    def test_missing_hash_rejected(self):
        vector = make_vector()
        del vector["content_hash"]
        with self.assertRaises(gv.VectorError):
            gv.load_vector(vector, registry=REGISTRY, inventory=INVENTORY)

    def test_rehashed_tamper_is_consistently_loadable(self):
        vector = make_vector()
        vector["traces"][0]["values"][2] = 999
        vector["content_hash"] = gv.compute_content_hash(vector)
        gv.load_vector(vector, registry=REGISTRY, inventory=INVENTORY)


class TestFirstMismatchReporter(unittest.TestCase):
    def test_identical_streams_report_nothing(self):
        vector = make_vector(4)
        self.assertIsNone(
            gv.first_mismatch(vector, {"mixer.output": list(range(4))})
        )

    def test_first_mismatch_carries_all_five_columns(self):
        vector = make_vector(8)
        actual = list(range(8))
        actual[5] = 105
        mismatch = gv.first_mismatch(vector, {"mixer.output": actual})
        self.assertIsNotNone(mismatch)
        self.assertEqual(mismatch.cycle, 5)
        self.assertEqual(mismatch.sample, 5)
        self.assertEqual(mismatch.trace, "mixer.output")
        self.assertEqual(mismatch.expected, 5)
        self.assertEqual(mismatch.actual, 105)
        self.assertEqual(mismatch.row().split()[0], "cycle=5")
        for column in gv.MISMATCH_COLUMNS:
            self.assertIn(column, gv.format_mismatch(mismatch))

    def test_cycle_offset_comes_from_cycle_start(self):
        vector = make_vector(4)
        vector["traces"][0]["cycle_start"] = 1000
        vector["content_hash"] = gv.compute_content_hash(vector)
        actual = [0, 1, 3, 3]
        mismatch = gv.first_mismatch(vector, {"mixer.output": actual})
        self.assertEqual(mismatch.cycle, 1002)
        self.assertEqual(mismatch.sample, 2)

    def test_length_mismatch_reported_at_first_absent_index(self):
        vector = make_vector(4)
        mismatch = gv.first_mismatch(vector, {"mixer.output": [0, 1, 2]})
        self.assertEqual(mismatch.sample, 3)
        self.assertEqual(mismatch.expected, 3)
        self.assertIsNone(mismatch.actual)

    def test_missing_captured_trace_is_an_error_not_a_mismatch(self):
        vector = make_vector(4)
        with self.assertRaises(gv.VectorError):
            gv.first_mismatch(vector, {})

    def test_exact_comparison_is_not_tolerance_based(self):
        vector = make_vector(2)
        vector["traces"][0]["encoding"] = "f32le"
        vector["traces"][0]["values"] = [0.1, 0.2]
        actual = [0.1, 0.2 + 1e-12]
        mismatch = gv.first_mismatch(vector, {"mixer.output": actual})
        self.assertIsNotNone(mismatch)


if __name__ == "__main__":
    unittest.main()
