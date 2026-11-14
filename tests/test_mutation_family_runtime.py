"""Shared signal/timing runtime adapter helpers (#405, #463). Stdlib-only.

These tests establish that the two family adapters agree on the helpers they
share, and that the places where they deliberately differ are the only places
they differ. They render, measure and qualify nothing: a passing run says the
adapters' shared mechanics are consistent, never that any fault was detected
in the actual Voice.

The cross-family row-shape test is the standing regression guard for #463.
``paired_exactness`` had drifted -- signal reported ``mean_error`` and timing
did not, from otherwise identical bodies -- and nothing failed. Both families
now route through one body, and a future one-sided edit fails here.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import mutation_family_runtime as family  # noqa: E402
from torchsynth_voice import mutations_signal_runtime as sr  # noqa: E402
from torchsynth_voice import mutations_timing_runtime as tr  # noqa: E402
from torchsynth_voice.mutations import MutationError  # noqa: E402

REFERENCE = [0.0, 0.25, -0.5, 1.0, 0.125, -0.125, 0.0, 0.75]

# The shared paired-exactness row. Named explicitly so that adding or dropping
# a key is a deliberate edit to this list, not a silent drift in one family.
ROW_KEYS = (
    "trace",
    "pair_status",
    "time_origin",
    "framing_match",
    "exact_equal",
    "max_abs_error",
    "mean_error",
    "first_divergence_index",
    "verdict",
)


class PairedExactnessRowTests(unittest.TestCase):
    """One row shape for both families, including ``mean_error`` (#463)."""

    def test_both_families_report_the_same_row_keys(self):
        for candidate in (list(REFERENCE),                      # exactly equal
                          [0.0] + REFERENCE[:-1],               # one-sample delay
                          [x + 0.5 for x in REFERENCE],         # dc offset
                          REFERENCE[:-1]):                      # frame mismatch
            signal_row = sr.paired_exactness(REFERENCE, candidate, 441.0, "probe.trace")
            timing_row = tr.paired_exactness(REFERENCE, candidate, 441.0, "probe.trace")
            self.assertEqual(tuple(signal_row), ROW_KEYS)
            self.assertEqual(tuple(timing_row), ROW_KEYS)
            self.assertEqual(signal_row, timing_row)

    def test_mean_error_is_reported_by_both_families(self):
        # Before #463 the timing row omitted this key entirely.
        offset = [x + 0.5 for x in REFERENCE]
        for module in (sr, tr):
            row = module.paired_exactness(REFERENCE, offset, 441.0, "probe.trace")
            self.assertIn("mean_error", row)
            self.assertEqual(row["mean_error"], 0.5)
            self.assertEqual(row["verdict"], "FAIL")

    def test_mean_error_is_none_on_an_unmeasured_pair(self):
        for module in (sr, tr):
            row = module.paired_exactness(REFERENCE, REFERENCE[:-1], 441.0, "probe.trace")
            self.assertEqual(row["pair_status"], "frame_mismatch")
            self.assertIsNone(row["mean_error"])

    def test_the_row_never_aligns(self):
        # Aligning would mask exactly the faults the time-locked detectors
        # exist to catch, so a delayed candidate must still FAIL.
        delayed = [0.0] + REFERENCE[:-1]
        for module in (sr, tr):
            row = module.paired_exactness(REFERENCE, delayed, 441.0, "probe.trace")
            self.assertEqual((row["verdict"], row["time_origin"], row["framing_match"]),
                             ("FAIL", "index-zero", 1))
            self.assertGreater(row["max_abs_error"], 0)


class SeamTraceTests(unittest.TestCase):
    """``seam_traces`` / ``blast_radius`` carry the timing-only ``partner``."""

    def test_partner_is_collected_after_its_trace(self):
        realised = [("op", {"trace": "a", "partner": "b"})]
        self.assertEqual(family.seam_traces(realised), ["a", "b"])

    def test_targets_without_a_partner_are_unaffected(self):
        realised = [("op", {"trace": "a"}), ("op", {"trace": "a"}), ("op", {"parameter": "p"})]
        self.assertEqual(family.seam_traces(realised), ["a"])

    def test_blast_radius_walks_the_partner_branch(self):
        dataflow = {"a": ["a_out"], "b": ["b_out"], "a_out": [], "b_out": []}
        without = family.blast_radius([("op", {"trace": "a"})], dataflow, {})
        with_partner = family.blast_radius(
            [("op", {"trace": "a", "partner": "b"})], dataflow, {})
        self.assertEqual(without, ["a_out"])
        self.assertEqual(with_partner, ["a_out", "b_out"])

    def test_blast_radius_resolves_a_parameter_through_its_root(self):
        dataflow = {"r": ["r_out"], "r_out": []}
        self.assertEqual(
            family.blast_radius([("op", {"parameter": "p"})], dataflow, {"p": "r"}),
            ["r", "r_out"])

    def test_no_signal_realisation_declares_a_partner(self):
        # The timing bodies are supersets only because this holds.
        for realised in sr.REALISATIONS.values():
            for _, target in realised:
                self.assertNotIn("partner", target)


class RequestIdentityTests(unittest.TestCase):
    """Each family keeps its own prefix, domain and bound keys."""

    def test_prefixes_stay_distinct(self):
        manifest, _ = sr.load_manifest(ROOT)
        case = sr.case_record(manifest, "global-0")
        self.assertTrue(sr.request_identity(manifest, case).startswith("srq1-"))
        timing_manifest, _ = tr.load_manifest(ROOT)
        timing_case = tr.case_record(timing_manifest, "global-0")
        self.assertTrue(tr.request_identity(timing_manifest, timing_case).startswith("trq1-"))

    def test_the_two_families_bind_different_identities(self):
        manifest, _ = sr.load_manifest(ROOT)
        case = sr.case_record(manifest, "global-0")
        self.assertNotEqual(sr.request_identity(manifest, case)[5:],
                            tr.request_identity(manifest, case)[5:])

    def test_extra_keys_change_the_digest(self):
        manifest, _ = tr.load_manifest(ROOT)
        case = tr.case_record(manifest, "global-0")
        bare = family.request_identity(manifest, case, __import__(
            "torchsynth_voice.timing_voice_ops", fromlist=["x"]),
            prefix="x-", domain=b"d\n")
        extended = family.request_identity(manifest, case, __import__(
            "torchsynth_voice.timing_voice_ops", fromlist=["x"]),
            prefix="x-", domain=b"d\n", extra={"family": "timing"})
        self.assertNotEqual(bare, extended)


class ExpectedAttemptsTests(unittest.TestCase):
    """The optional ``case_record`` precheck is the families' one asymmetry."""

    def test_both_families_order_controls_then_entries_then_clean_rerun(self):
        for module in (sr, tr):
            manifest, _ = module.load_manifest(ROOT)
            names = module.expected_attempts(manifest, "global-0")
            self.assertEqual(names[:3], list(module.CONTROL_ATTEMPTS))
            self.assertEqual(names[-1], "clean_rerun")
            self.assertEqual(len(names), len(set(names)))

    def test_signal_refuses_an_unknown_case_and_timing_does_not(self):
        # Pre-existing, now an explicit argument rather than two near-copies
        # that happen to differ by one line (#463).
        signal_manifest, _ = sr.load_manifest(ROOT)
        with self.assertRaises(MutationError):
            sr.expected_attempts(signal_manifest, "no-such-case")
        timing_manifest, _ = tr.load_manifest(ROOT)
        self.assertEqual(tr.expected_attempts(timing_manifest, "no-such-case"),
                         list(tr.CONTROL_ATTEMPTS) + ["clean_rerun"])


class RuntimeDefinitionTests(unittest.TestCase):
    """The ``configure`` hook is the only trace-seam difference."""

    def test_summaries_name_their_own_family_and_provenance(self):
        for module, label, provenance in ((sr, "signal", "signal_voice_ops"),
                                          (tr, "timing", "timing_voice_ops")):
            definitions = module.runtime_definitions()
            self.assertTrue(definitions)
            for definition in definitions.values():
                self.assertTrue(definition["summary"].startswith(label + "-family voice-runtime"))
            # The sham summary names no realisation, so it carries no
            # provenance or campaign binding tail -- as in both originals.
            (sham,) = [d for d in definitions.values() if d["sham"]]
            self.assertEqual(
                sham["summary"],
                label + "-family voice-runtime sham: traverse the family dispatch"
                " and return the original slot")
            non_sham = [d for d in definitions.values() if not d["sham"]]
            self.assertTrue(non_sham)
            for definition in non_sham:
                self.assertIn(provenance, definition["summary"])
                self.assertTrue(definition["summary"].endswith(
                    "); bound to runtime-family-campaign-v1 revision 1"))

    def test_only_timing_declares_control_trace_and_partner(self):
        signal_configurations = [d["configuration"] for d in sr.runtime_definitions().values()]
        for configuration in signal_configurations:
            self.assertNotIn("control_trace", configuration)
            self.assertNotIn("partner", configuration)
        timing_configurations = [d["configuration"] for d in tr.runtime_definitions().values()]
        self.assertTrue(any("control_trace" in c for c in timing_configurations))
        self.assertTrue(any("partner" in c for c in timing_configurations))

    def test_every_definition_carries_the_shared_shape(self):
        for module in (sr, tr):
            for definition in module.runtime_definitions().values():
                self.assertEqual(tuple(definition),
                                 ("version", "seam", "sham", "magnitude", "configuration",
                                  "composes_with", "summary"))
                self.assertEqual(definition["version"], 1)
                self.assertEqual(definition["configuration"]["slot"],
                                 {"type": "integer", "minimum": 0})


if __name__ == "__main__":
    unittest.main()
