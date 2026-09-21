"""Audio-sources fixed-format sweep receipt tests (issue #51).

Bounded, honest checks over the sweep tool and its receipt:

- quick-mode receipts are schema-valid, deterministic (byte-identical across
  runs), and carry the noise-identity and policy notes;
- the candidate fixed render path reproduces the landed primitives exactly
  (mixed-radical equivalence spot checks against ``ops``/``phase``/``lut``);
- the committed full receipt (when present) is internally consistent:
  every recommended candidate's stored rows recompute the advertised
  gates, retained raw traces hash-match, mutation probes fail their base
  verdict, and no normalization row precedes the mandatory gain/error rows.
"""

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import sweep_audio_sources as sw  # noqa: E402

FULL_RECEIPT = ROOT / "sim/candidates/audio-sources-sweep-v1.json"


class ToolContract(unittest.TestCase):
    def test_noise_is_never_swept(self):
        self.assertIn(
            "NEVER swept",
            "\n".join(sw.__doc__.splitlines()),
            "the tool docstring must declare the noise exactness rule",
        )

    def test_selected_point_uses_landed_primitives(self):
        table = sw._build_table(1024)
        # hash-linked table: payload round-trip refuses any content change
        payload = table.to_json()
        restored = sw.fp_lut.QuarterWaveTable.from_json(payload)
        self.assertEqual(restored.sha256(), table.sha256())
        with self.assertRaises(ValueError):
            bad = dict(payload)
            bad["entries"] = list(payload["entries"])
            bad["entries"][0] += 1
            sw.fp_lut.QuarterWaveTable.from_json(bad)

    def test_inline_linear_is_exactly_the_landed_linear(self):
        for entries in (1024, 4096):
            table = sw._build_table(entries)
            inline = sw._InlineLut(table, "linear")
            modulus = 1 << sw.LUT_PHASE_BITS
            for i in range(0, 8192, 13):
                phase = (i * 40503) % modulus
                self.assertEqual(
                    inline.evaluate(phase),
                    table.evaluate(phase),
                    "linear order must be the landed primitive arithmetic",
                )

    def test_quadratic_order_meets_m2_on_a_bounded_probe(self):
        table = sw._build_table(1024)
        inline = sw._InlineLut(table, "quadratic")
        linear = sw._InlineLut(table, "linear")
        worst_q, worst_l = 0.0, 0.0
        scale = table.spec.entry_format.scale
        modulus = 1 << sw.LUT_PHASE_BITS
        for i in range(0, 65536, 3):
            phase = (i * 2654435761) % modulus
            exact = __import__("math").cos(2.0 * __import__("math").pi * phase / modulus)
            err_q = abs(inline.evaluate(phase) / scale - exact)
            err_l = abs(linear.evaluate(phase) / scale - exact)
            worst_q = max(worst_q, err_q)
            worst_l = max(worst_l, err_l)
        self.assertLessEqual(worst_q, sw.M2_LIMIT)
        self.assertLess(worst_q, worst_l, "quadratic must beat linear at 1K")


class QuickReceipt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        out_a = Path(cls._tmp.name) / "a.json"
        out_b = Path(cls._tmp.name) / "b.json"
        sw.run(quick=True, out=out_a, write_traces=False, m2_points=4096, workers=1)
        sw.run(quick=True, out=out_b, write_traces=False, m2_points=4096, workers=1)
        cls.receipt_a = json.loads(out_a.read_bytes())
        cls.bytes_a = out_a.read_bytes()
        cls.bytes_b = out_b.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_quick_receipt_is_deterministic(self):
        self.assertEqual(self.bytes_a, self.bytes_b)

    def test_schema_and_status(self):
        self.assertEqual(self.receipt_a["schema"], sw.SCHEMA)
        self.assertEqual(self.receipt_a["status"], "candidate-pre-ratification")
        self.assertEqual(self.receipt_a["numeric_contract"], "unbound:#53")
        self.assertEqual(self.receipt_a["mode"], "quick")

    def test_policy_notes_declare_noise_and_ppa_rules(self):
        notes = " ".join(self.receipt_a["policy_notes"])
        self.assertIn("never swept", notes)
        self.assertIn("NOT PPA", notes)
        self.assertIn("pending #53 ratification", notes)

    def test_noise_identity_rows_present_and_exact(self):
        for case in self.receipt_a["cases"].values():
            fixture = case["fixture"]
            self.assertTrue(
                fixture["noise_bit_exact_identity_ok"],
                "canonical noise digest must reproduce (exact-by-identity)",
            )
            self.assertIn(
                "reference_phase_error_rad",
                fixture,
                "drift attribution must be recorded per fixture",
            )

    def test_every_config_row_carries_sites_and_proxies(self):
        for case in self.receipt_a["cases"].values():
            for entry in case["configs"].values():
                self.assertTrue(entry["proxies"]["lut_storage_bits"] > 0)
                self.assertIn("NOT a PPA result", entry["proxies"]["proxy_kind"])
                sites = {site["site"] for site in entry["sites"]}
                self.assertLessEqual(
                    {"S2", "S3", "S4", "S5", "LUT"}, sites,
                    "every rounding/saturation site must be enumerated",
                )

    def test_recommendations_are_candidates_only(self):
        for rec in self.receipt_a["recommendations"]:
            self.assertEqual(rec["status"], "CANDIDATE pending #53 ratification")


class FullReceipt(unittest.TestCase):
    """Checks over the committed full receipt (skipped when absent)."""

    @classmethod
    def setUpClass(cls):
        cls.present = FULL_RECEIPT.exists()
        if cls.present:
            cls.receipt = json.loads(FULL_RECEIPT.read_bytes())

    def test_full_receipt_exists_and_is_full(self):
        if not self.present:
            self.skipTest("full receipt not committed")
        self.assertEqual(self.receipt["mode"], "full")
        self.assertEqual(self.receipt["sample_count"], 176400)

    def test_all_directed_fixtures_ran(self):
        if not self.present:
            self.skipTest("full receipt not committed")
        record = json.loads(
            (ROOT / "sim/reference/float-sources-v1.json").read_bytes()
        )
        for case_id in record["fixtures"]:
            self.assertIn(case_id, self.receipt["cases"], f"{case_id} missing")
        stress = [
            label
            for label in self.receipt["cases"]
            if label.startswith("normalization-stress")
        ]
        self.assertEqual(len(stress), 1, "normalization-stress case must run")

    def test_normalization_never_precedes_mandatory_rows(self):
        if not self.present:
            self.skipTest("full receipt not committed")
        for case in self.receipt["cases"].values():
            for entry in case["configs"].values():
                rows = entry["rows"]
                self.assertFalse(
                    rows["mixer.pre_normalization"].get("post_normalization", False),
                    "mandatory mix rows must be pre-normalization",
                )
                self.assertTrue(
                    rows["mixer.output"]["post_normalization"],
                    "normalization rows are separate and marked",
                )
                self.assertTrue(
                    rows["normalization"]["reported_after_mandatory_rows"]
                )

    def test_mutation_probes_detected(self):
        if not self.present:
            self.skipTest("full receipt not committed")
        self.assertTrue(self.receipt["mutations"], "mutation sensitivity required")
        for mutation in self.receipt["mutations"]:
            self.assertTrue(
                mutation["detected"],
                f"mutation {mutation['mutation']!r} must clear the "
                "sensitivity bar over the base render",
            )
            self.assertGreaterEqual(
                mutation["sensitivity_ratio"], mutation["sensitivity_bar"]
            )
            self.assertGreater(
                mutation["max_abs_error"], mutation["base_max_abs_error"]
            )

    def test_retained_raw_traces_hash_match(self):
        if not self.present:
            self.skipTest("full receipt not committed")
        retained = self.receipt.get("retained_raw_traces")
        self.assertIsNotNone(retained, "Pareto candidates must retain raw traces")
        self.assertTrue(retained["files"])
        for entry in retained["files"]:
            path = ROOT / entry["file"]
            self.assertTrue(path.exists(), entry["file"])
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(), entry["sha256"]
            )

    def test_selected_point_gates_recompute(self):
        if not self.present:
            self.skipTest("full receipt not committed")
        selected = next(
            rec
            for rec in self.receipt["recommendations"]
            if rec["config_id"] == "Q2.21+u32+4K-linear"
        )
        stress = next(
            label
            for label in self.receipt["cases"]
            if label.startswith("normalization-stress")
        )
        counters = self.receipt["cases"][stress]["configs"][
            "Q2.21+u32+4K-linear"
        ]["rows"]["saturation_counters"]
        self.assertEqual(
            counters["total_saturation"] == 0,
            selected["gates"]["normalization_anchor_saturation_zero"],
        )
        m2_rows = self.receipt["intrinsic"]["m2_lut_vs_cos"]
        m2_pass = {
            (row["n_entries"], row["order"])
            for row in m2_rows
            if row["verdict"] == "PASS"
        }
        self.assertEqual(
            (4096, "linear") in m2_pass,
            selected["gates"]["m2_pass"],
        )
        # the selected 4K-linear geometry must meet M2 with the analytic
        # margin DR-0008 Section 5 records
        row = next(
            row
            for row in m2_rows
            if row["n_entries"] == 4096 and row["order"] == "linear"
        )
        self.assertLessEqual(row["max_abs_error_vs_cos"], sw.M2_LIMIT)
        self.assertLessEqual(row["max_abs_error_vs_cos"], 2.6e-7 + 1e-8)

    def test_recommendations_carry_candidate_status(self):
        if not self.present:
            self.skipTest("full receipt not committed")
        self.assertTrue(self.receipt["recommendations"])
        for rec in self.receipt["recommendations"]:
            self.assertEqual(rec["status"], "CANDIDATE pending #53 ratification")

    def test_sixteen_bit_words_expose_anchor_clipping(self):
        if not self.present:
            self.skipTest("full receipt not committed")
        stress = next(
            label
            for label in self.receipt["cases"]
            if label.startswith("normalization-stress")
        )
        clipped = [
            conf
            for conf, entry in self.receipt["cases"][stress]["configs"].items()
            if entry["rows"]["saturation_counters"]["total_saturation"] > 0
        ]
        self.assertTrue(
            clipped,
            "at least one swept word must saturate at the anchor (headroom evidence)",
        )


if __name__ == "__main__":
    unittest.main()
