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


class TestAcceptedContractBinding(unittest.TestCase):
    """The accepted-contract hash link (issue #68 AC-1/AC-7).

    The real anchor vector must bind the live accepted artifacts: the
    emitted constants package, the choice register, and the accepted
    DR-0008 record (via the frozen receipt bindings). Any interface
    change must fail this check instead of silently recompiling.
    """

    @classmethod
    def setUpClass(cls):
        cls.anchor = gv.load_vector(gv.SENTINEL_VECTOR_PATH)

    def rehash(self, vector):
        vector["content_hash"] = gv.compute_content_hash(vector)
        return vector

    def test_live_anchor_binds_the_live_contract(self):
        verified = gv.verify_accepted_contract(self.anchor)
        self.assertEqual(
            verified["constants_package_sha256"],
            gv.sha256_file(gv.CONSTANTS_PACKAGE_PATH),
        )
        self.assertEqual(
            verified["choices_register_sha256"],
            gv.sha256_file(gv.CHOICES_REGISTER_PATH),
        )
        self.assertEqual(
            verified["dr_0008_record_sha256"],
            gv.sha256_file(gv.DR_0008_RECORD_PATH),
        )

    def test_record_digest_binds_through_the_receipt(self):
        """The anchor provenance carries no record digest; the receipt does."""
        self.assertNotIn("dr_0008_record_sha256", self.anchor["provenance"])
        verified = gv.verify_accepted_contract(self.anchor)
        self.assertEqual(verified["dr_0008_record_source"], "receipt bindings")

    def test_tampered_provenance_digest_refuses(self):
        vector = dict(self.anchor)
        vector["provenance"] = dict(
            self.anchor["provenance"],
            dr_0008_constants_package_sha256="0" * 64,
        )
        with self.assertRaises(gv.VectorError) as caught:
            gv.verify_accepted_contract(self.rehash(vector))
        self.assertIn("regenerate", str(caught.exception))

    def test_modified_live_package_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            stale = Path(tmp) / "stale_pkg.sv"
            stale.write_bytes(
                Path(gv.CONSTANTS_PACKAGE_PATH).read_bytes() + b"\n// edited\n"
            )
            with self.assertRaises(gv.VectorError) as caught:
                gv.verify_accepted_contract(self.anchor, constants_path=stale)
            self.assertIn("contract refusal", str(caught.exception))

    def test_modified_live_register_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            stale = Path(tmp) / "stale_register.json"
            stale.write_bytes(
                Path(gv.CHOICES_REGISTER_PATH).read_bytes()
                .replace(b'"Accepted"', b'"Accepted "', 1)
            )
            with self.assertRaises(gv.VectorError):
                gv.verify_accepted_contract(self.anchor, register_path=stale)

    def test_modified_live_record_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            stale = Path(tmp) / "stale_record.md"
            stale.write_bytes(
                Path(gv.DR_0008_RECORD_PATH).read_bytes() + b"stale\n"
            )
            with self.assertRaises(gv.VectorError):
                gv.verify_accepted_contract(self.anchor, record_path=stale)

    def test_non_accepted_dr_status_refuses(self):
        vector = dict(self.anchor)
        vector["provenance"] = dict(
            self.anchor["provenance"], dr_0008_status="Proposed"
        )
        with self.assertRaises(gv.VectorError) as caught:
            gv.verify_accepted_contract(self.rehash(vector))
        self.assertIn("not Accepted", str(caught.exception))

    def test_missing_provenance_refuses(self):
        with self.assertRaises(gv.VectorError):
            gv.verify_accepted_contract({"schema": gv.VECTOR_SCHEMA})

    def test_unreadable_receipt_refuses_record_binding(self):
        vector = dict(self.anchor)
        vector["provenance"] = {
            k: v
            for k, v in self.anchor["provenance"].items()
            if k != "dr_0008_record_sha256"
        }
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "no-receipt.json"
            with self.assertRaises(gv.VectorError) as caught:
                gv.verify_accepted_contract(
                    self.rehash(vector), receipt_path=missing
                )
            self.assertIn("dr_0008_record_sha256", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
