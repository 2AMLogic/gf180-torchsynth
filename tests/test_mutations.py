"""Fault-injection seam contract tests; stdlib-only, Torch-free apparatus.

These tests exercise the #30 mutation framework end to end on the apparatus
harness with the landed downstream validators (scorecard, artifacts,
trace_capture, case_registry). Each injected fault must demonstrably trip its
named downstream refusal while the clean control demonstrably passes the same
check; sham and empty controls stay byte-identical to the ordinary run and
keep distinct provenance. Actual-Voice runtime fault injection is not
exercised here and is never substituted by these synthetic proofs.
"""

import copy
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import artifacts  # noqa: E402
from torchsynth_voice import case_registry  # noqa: E402
from torchsynth_voice import mutations  # noqa: E402
from torchsynth_voice import mutation_runtime  # noqa: E402
from torchsynth_voice import scorecard  # noqa: E402
from torchsynth_voice import trace_capture  # noqa: E402
from torchsynth_voice import trace_registry  # noqa: E402

DOCUMENT = trace_registry.load_registry()
CATALOG = mutations.load_seam_catalog(ROOT / mutations.SEAM_CATALOG_PATH)
HARNESS = mutation_runtime.MutationHarness(ROOT, DOCUMENT)
ABSENT_ROOT = Path("/nonexistent/gf180-mutation-gate")
SIBLING_CASE = "directed:mutation-harness-sibling"


def plan(*instances):
    return mutations.make_plan(HARNESS.binding(), list(instances), CATALOG)


def make_catalog_variant(**overrides):
    base = {
        "schema_version": 1,
        "seams": {
            name: dict(entry) for name, entry in CATALOG["seams"].items()
        },
    }
    base["seams"].update(overrides)
    return base


class SeamCatalogTests(unittest.TestCase):
    def test_catalog_is_machine_readable_with_downstream_bindings(self):
        self.assertGreaterEqual(len(CATALOG["seams"]), 5)
        for name, entry in CATALOG["seams"].items():
            with self.subTest(seam=name):
                self.assertIn(entry["phase"], ("producer", "persistence", "access", "capture"))
                self.assertTrue(entry["downstream"].strip())

    def test_derived_digest_seam_is_not_writable(self):
        self.assertFalse(CATALOG["seams"]["apparatus.evidence_digest"]["writable"])


class PlanValidationTests(unittest.TestCase):
    def test_empty_plan_accepted_and_identity_deterministic(self):
        first = plan()
        second = plan()
        self.assertEqual(first, second)
        self.assertTrue(first["plan_id"].startswith("mp1-"))
        self.assertEqual(first["mutations"], [])

    def test_unknown_operator_refused(self):
        unknown = dict(mutation_runtime.instance("mi-x", "crash.producer", "apparatus.producer_call"))
        unknown["operator"] = "not.an.operator"
        with self.assertRaises(mutations.MutationError) as caught:
            plan(unknown)
        self.assertIn("unknown operator", str(caught.exception))

    def test_unknown_seam_refused_and_names_producer_handoff(self):
        with self.assertRaises(mutations.MutationError) as caught:
            plan(mutation_runtime.instance("mi-x", "crash.producer", "apparatus.not_a_seam"))
        message = str(caught.exception)
        self.assertIn("unknown seam", message)
        self.assertIn("producer handoff", message)

    def test_operator_seam_mismatch_refused(self):
        with self.assertRaises(mutations.MutationError) as caught:
            plan(mutation_runtime.instance("mi-x", "crash.producer", "apparatus.artifact_write"))
        self.assertIn("does not support seam", str(caught.exception))

    def test_mutation_at_non_writable_seam_refused(self):
        operator_id = "test.digestmutator"
        mutations.register_operator(
            operator_id,
            {
                "version": 1,
                "seam": "apparatus.evidence_digest",
                "sham": False,
                "magnitude": {"type": "null", "unit": "none"},
                "configuration": {},
                "composes_with": [],
                "summary": "test-only operator at a derived, non-writable seam",
            },
        )
        try:
            with self.assertRaises(mutations.MutationError) as caught:
                plan(mutation_runtime.instance("mi-x", operator_id, "apparatus.evidence_digest"))
            self.assertIn("not a writable injection seam", str(caught.exception))
        finally:
            mutations.OPERATORS.pop(operator_id, None)

    def test_boolean_magnitude_refused(self):
        with self.assertRaises(mutations.MutationError) as caught:
            plan(mutation_runtime.instance("mi-x", "truncate.payload", "apparatus.artifact_write", True))
        self.assertIn("never bool", str(caught.exception))

    def test_nonfinite_magnitude_refused(self):
        for value in (float("nan"), float("inf")):
            with self.subTest(value=value):
                with self.assertRaises(mutations.MutationError):
                    mutations.validate_magnitude(
                        {"type": "number", "unit": "ratio", "minimum": 0},
                        {"value": value, "unit": "ratio"},
                    )

    def test_missing_unit_refused(self):
        with self.assertRaises(mutations.MutationError) as caught:
            mutations.validate_magnitude(
                {"type": "integer", "unit": "byte", "minimum": 0}, {"value": 10}
            )
        self.assertIn("exactly value and unit", str(caught.exception))

    def test_wrong_unit_refused(self):
        bad = mutation_runtime.instance("mi-x", "truncate.payload", "apparatus.artifact_write", 10)
        bad["magnitude"] = {"value": 10, "unit": "second"}
        with self.assertRaises(mutations.MutationError) as caught:
            plan(bad)
        self.assertIn("magnitude unit must be byte", str(caught.exception))

    def test_out_of_domain_magnitude_refused(self):
        with self.assertRaises(mutations.MutationError):
            plan(mutation_runtime.instance("mi-x", "truncate.payload", "apparatus.artifact_write", -1))
        with self.assertRaises(mutations.MutationError):
            plan(mutation_runtime.instance("mi-x", "drop.receipt", "apparatus.receipt_append", 2))

    def test_duplicate_instance_id_refused(self):
        first = mutation_runtime.instance("mi-x", "truncate.payload", "apparatus.artifact_write", 10)
        second = mutation_runtime.instance("mi-x", "drop.receipt", "apparatus.receipt_append", 1)
        with self.assertRaises(mutations.MutationError) as caught:
            plan(first, second)
        self.assertIn("duplicate instance ID", str(caught.exception))

    def test_undeclared_combination_refused(self):
        first = mutation_runtime.instance("mi-a", "crash.producer", "apparatus.producer_call")
        second = mutation_runtime.instance("mi-b", "drop.receipt", "apparatus.receipt_append", 1)
        with self.assertRaises(mutations.MutationError) as caught:
            plan(first, second)
        self.assertIn("undeclared combination", str(caught.exception))

    def test_allowed_composition_accepted_with_declared_order(self):
        ordered = plan(
            mutation_runtime.instance("mi-a", "truncate.payload", "apparatus.artifact_write", 10),
            mutation_runtime.instance("mi-b", "drop.receipt", "apparatus.receipt_append", 1),
        )
        self.assertEqual(
            [item["operator"] for item in ordered["mutations"]],
            ["truncate.payload", "drop.receipt"],
        )

    def test_tampered_plan_refuses_identity(self):
        tampered = plan(mutation_runtime.instance("mi-x", "truncate.payload", "apparatus.artifact_write", 10))
        tampered["mutations"][0]["magnitude"]["value"] = 11
        with self.assertRaises(mutations.MutationError) as caught:
            mutations.validate_plan(tampered, CATALOG)
        self.assertIn("plan_id does not match", str(caught.exception))

    def test_magnitude_change_changes_plan_identity(self):
        small = plan(mutation_runtime.instance("mi-x", "truncate.payload", "apparatus.artifact_write", 10))
        large = plan(mutation_runtime.instance("mi-x", "truncate.payload", "apparatus.artifact_write", 20))
        self.assertNotEqual(small["plan_id"], large["plan_id"])

    def test_operator_version_mismatch_refused(self):
        bad = mutation_runtime.instance("mi-x", "truncate.payload", "apparatus.artifact_write", 10)
        bad["operator_version"] = 99
        with self.assertRaises(mutations.MutationError) as caught:
            plan(bad)
        self.assertIn("operator version mismatch", str(caught.exception))

    def test_unknown_plan_id_refused(self):
        forged = plan(mutation_runtime.instance("mi-x", "crash.producer", "apparatus.producer_call"))
        forged["plan_id"] = "mp1-" + "0" * 64
        with self.assertRaises(mutations.MutationError):
            mutations.validate_plan(forged, CATALOG)


class ControlTests(unittest.TestCase):
    def test_ordinary_empty_and_sham_attempts_are_byte_identical(self):
        plain = HARNESS.plain_attempt()
        empty = HARNESS.attempt(plan())
        sham = HARNESS.attempt(
            plan(mutation_runtime.instance("mi-sham", "noop.sham", "apparatus.producer_call"))
        )
        for other in (empty, sham):
            self.assertEqual(plain.store, other.store)
            self.assertEqual(plain.receipts, other.receipts)
            self.assertEqual(plain.partition_claim, other.partition_claim)
        self.assertEqual(empty.errored, None)
        self.assertEqual(sham.errored, None)

    def test_empty_plan_records_no_events_and_stays_complete(self):
        empty = HARNESS.attempt(plan())
        self.assertEqual(empty.events, [])
        self.assertTrue(empty.events_summary["complete"])
        self.assertEqual(empty.events_summary["observed"], 0)

    def test_sham_is_marked_and_never_effective(self):
        sham = HARNESS.attempt(
            plan(mutation_runtime.instance("mi-sham", "noop.sham", "apparatus.producer_call"))
        )
        self.assertEqual(len(sham.events), 1)
        event = sham.events[0]
        self.assertTrue(event["sham"])
        self.assertEqual(event["status"], "ineffective")

    def test_sham_and_empty_plan_identities_stay_distinct(self):
        sham = plan(mutation_runtime.instance("mi-sham", "noop.sham", "apparatus.producer_call"))
        self.assertNotEqual(sham["plan_id"], plan()["plan_id"])

    def test_attempt_artifact_identity_never_collides_with_render_identity(self):
        payload = HARNESS.plain_attempt().payload
        mutation_identity = mutations.attempt_artifact_identity(payload)
        render_identity = artifacts.content_id({"payload": list(payload)})
        self.assertTrue(mutation_identity.startswith("mu1-"))
        self.assertTrue(render_identity.startswith("ra1-"))
        self.assertNotEqual(mutation_identity, render_identity)
        self.assertNotEqual(
            mutations.attempt_artifact_identity(payload + b"\x00"),
            mutation_identity,
        )

    def test_global_rng_state_is_untouched_by_attempts(self):
        before = random.getstate()
        HARNESS.attempt(plan())
        HARNESS.attempt(
            plan(mutation_runtime.instance("mi-sham", "noop.sham", "apparatus.producer_call"))
        )
        HARNESS.plain_attempt()
        self.assertEqual(random.getstate(), before)


class FaultMatrixTests(unittest.TestCase):
    def test_partial_write_trips_artifact_digest_refusal(self):
        clean = HARNESS.plain_attempt()
        artifacts.verify_sha256(clean.payload, clean.receipts[0]["artifact"]["sha256"])
        faulted = HARNESS.attempt(
            plan(mutation_runtime.instance("mi-partial", "truncate.payload", "apparatus.artifact_write", 100))
        )
        self.assertEqual(len(faulted.payload), 100)
        self.assertEqual(faulted.events[0]["status"], "applied")
        with self.assertRaises(artifacts.ValidationError) as caught:
            artifacts.verify_sha256(faulted.payload, faulted.receipts[0]["artifact"]["sha256"])
        self.assertEqual(str(caught.exception), "file SHA-256 mismatch")

    def test_corrupt_byte_trips_artifact_digest_refusal(self):
        clean = HARNESS.plain_attempt()
        artifacts.verify_sha256(clean.payload, clean.receipts[0]["artifact"]["sha256"])
        faulted = HARNESS.attempt(
            plan(mutation_runtime.instance("mi-corrupt", "corrupt.byte", "apparatus.artifact_write", 7))
        )
        self.assertEqual(len(faulted.payload), len(clean.payload))
        self.assertNotEqual(faulted.payload, clean.payload)
        with self.assertRaises(artifacts.ValidationError) as caught:
            artifacts.verify_sha256(faulted.payload, faulted.receipts[0]["artifact"]["sha256"])
        self.assertEqual(str(caught.exception), "file SHA-256 mismatch")

    def test_corrupt_receipt_rebinds_digest_and_trips_refusal(self):
        faulted = HARNESS.attempt(
            plan(mutation_runtime.instance("mi-rebind", "corrupt.digest", "apparatus.receipt_append"))
        )
        receipt = faulted.receipts[0]
        scorecard.validate_row(receipt)
        self.assertNotEqual(receipt["artifact"]["sha256"], mutation_runtime.sha256(faulted.payload))
        with self.assertRaises(artifacts.ValidationError) as caught:
            artifacts.verify_sha256(faulted.payload, receipt["artifact"]["sha256"])
        self.assertEqual(str(caught.exception), "file SHA-256 mismatch")

    def test_dropped_receipt_shrinks_denominator_and_yields_no_verdict(self):
        clean = HARNESS.plain_attempt()
        mutations.require_complete_inventory(
            mutation_runtime.EXPECTED_INVENTORY, clean.inventory_observed
        )
        self.assertEqual(case_registry.row_outcome(clean.receipts), "PASS")
        report = scorecard.make_report(clean.receipts, partition="development", rubric=mutation_runtime.RUBRIC)
        self.assertEqual(report["summary"]["total_rows"], 1)
        faulted = HARNESS.attempt(
            plan(mutation_runtime.instance("mi-drop", "drop.receipt", "apparatus.receipt_append", 1))
        )
        self.assertEqual(faulted.receipts, [])
        with self.assertRaises(mutations.MutationError) as caught:
            mutations.require_complete_inventory(
                mutation_runtime.EXPECTED_INVENTORY, faulted.inventory_observed
            )
        self.assertIn("incomplete attempt inventory for receipts", str(caught.exception))
        empty_report = scorecard.make_report([], partition="development", rubric=mutation_runtime.RUBRIC)
        self.assertEqual(empty_report["summary"]["total_rows"], 0)
        self.assertEqual(case_registry.row_outcome([]), "NO VERDICT")

    def test_producer_crash_reported_in_band_and_aggregates_to_no_verdict(self):
        faulted = HARNESS.attempt(
            plan(mutation_runtime.instance("mi-crash", "crash.producer", "apparatus.producer_call"))
        )
        self.assertTrue(faulted.errored.startswith("ProducerCrash"))
        self.assertEqual(faulted.events[0]["status"], "errored")
        self.assertEqual(faulted.events[0]["detail"]["error"], "ProducerCrash")
        self.assertEqual(faulted.receipts, [])
        self.assertEqual(case_registry.row_outcome(faulted.receipts), "NO VERDICT")
        self.assertTrue(faulted.events_summary["complete"])

    def test_clean_rerun_after_crash_is_unchanged(self):
        pristine = HARNESS.plain_attempt()
        HARNESS.attempt(
            plan(mutation_runtime.instance("mi-crash", "crash.producer", "apparatus.producer_call"))
        )
        rerun = HARNESS.attempt(plan())
        self.assertEqual(rerun.store, pristine.store)
        self.assertEqual(rerun.receipts, pristine.receipts)

    def test_swapped_capture_trips_capture_inventory_refusal(self):
        clean = HARNESS.plain_attempt()
        trace_capture.validate_selected_capture(
            DOCUMENT, mutation_runtime.CAPTURE_SELECTION, clean.inventory, 32
        )
        faulted = HARNESS.attempt(
            plan(
                mutation_runtime.instance(
                    "mi-swap",
                    "swap.capture",
                    "capture.inventory",
                    configuration={"left": "adsr_1.output", "right": "mixer.peak"},
                )
            )
        )
        self.assertEqual(faulted.events[0]["status"], "applied")
        with self.assertRaises(ValueError) as caught:
            trace_capture.validate_selected_capture(
                DOCUMENT, mutation_runtime.CAPTURE_SELECTION, faulted.inventory, 32
            )
        self.assertIn("capture inventory mismatch", str(caught.exception))

    def test_missing_capture_entry_trips_refusal(self):
        inventory = mutation_runtime.capture_inventory(
            DOCUMENT, names=mutation_runtime.CAPTURE_SELECTION
        )
        trace_capture.validate_selected_capture(
            DOCUMENT, mutation_runtime.CAPTURE_SELECTION, inventory, 32
        )
        with self.assertRaises(ValueError) as caught:
            trace_capture.validate_selected_capture(
                DOCUMENT, mutation_runtime.CAPTURE_SELECTION, inventory[:-1], 32
            )
        self.assertIn("capture inventory mismatch", str(caught.exception))

    def test_wrong_rate_capture_trips_refusal(self):
        inventory = mutation_runtime.capture_inventory(
            DOCUMENT, names=mutation_runtime.CAPTURE_SELECTION
        )
        inventory[0]["rate_hz"] = 48000
        with self.assertRaises(ValueError) as caught:
            trace_capture.validate_selected_capture(
                DOCUMENT, mutation_runtime.CAPTURE_SELECTION, inventory, 32
            )
        self.assertIn("capture rate_hz mismatch: adsr_1.output", str(caught.exception))

    def test_holdout_gate_refuses_before_any_read(self):
        with self.assertRaises(case_registry.RegistryError) as caught:
            case_registry.evaluate({}, ABSENT_ROOT, partition="holdout")
        self.assertIn("holdout refused", str(caught.exception))
        try:
            case_registry.evaluate({}, ABSENT_ROOT, partition="development")
        except case_registry.RegistryError as error:
            self.assertNotIn("holdout refused", str(error))

    def test_covered_inputs_holdout_gate_refuses_before_any_read(self):
        holdout_case = {"id": "random-000096", "partition": "holdout"}
        with self.assertRaises(case_registry.RegistryError) as caught:
            case_registry.covered_inputs({}, holdout_case, ABSENT_ROOT)
        self.assertIn("holdout refused", str(caught.exception))
        try:
            case_registry.covered_inputs(
                {},
                holdout_case,
                ABSENT_ROOT,
                holdout_authorization="audit: gate-order proof for #30 qualification",
            )
        except case_registry.RegistryError as error:
            self.assertNotIn("holdout refused", str(error))

    def test_spoofed_partition_replaces_claim_at_the_seam(self):
        targets = {"board.partition_access": lambda: "holdout"}
        spoof = mutations.make_plan(
            HARNESS.binding(),
            [
                mutation_runtime.instance(
                    "mi-spoof", "spoof.partition", "board.partition_access", configuration={"claim": "development"}
                )
            ],
            CATALOG,
        )
        session = mutation_runtime.InjectionSession(spoof, targets)
        with session:
            claim = targets["board.partition_access"]()
        self.assertEqual(claim, "development")
        self.assertEqual(session.events[0]["status"], "applied")
        self.assertEqual(session.events[0]["detail"], {"claim": "development"})

    def test_forged_receipt_digest_refused_by_scorecard(self):
        receipt = copy.deepcopy(HARNESS.plain_attempt().receipts[0])
        receipt["artifact"]["sha256"] = "Z" * 64
        with self.assertRaises(scorecard.ScorecardError) as caught:
            scorecard.validate_row(receipt)
        self.assertIn("64 lowercase hexadecimal", str(caught.exception))

    def test_refusal_row_with_observed_value_refused(self):
        receipt = copy.deepcopy(HARNESS.plain_attempt().receipts[0])
        receipt.update(verdict="NO VERDICT", observed=None, coverage="none")
        receipt["validity"] = {"status": "insufficient", "reason": "refusal state fixture"}
        scorecard.validate_row(receipt)
        receipt["observed"] = 10
        with self.assertRaises(scorecard.ScorecardError) as caught:
            scorecard.validate_row(receipt)
        self.assertIn("refused or missing evidence requires observed=null", str(caught.exception))

    def test_missing_required_field_refused(self):
        receipt = copy.deepcopy(HARNESS.plain_attempt().receipts[0])
        del receipt["unit"]
        with self.assertRaises(scorecard.ScorecardError) as caught:
            scorecard.validate_row(receipt)
        self.assertIn("requires exactly these fields", str(caught.exception))

    def test_mixed_partition_report_refused(self):
        receipt = copy.deepcopy(HARNESS.plain_attempt().receipts[0])
        receipt["case"]["partition"] = "holdout"
        report = {
            "schema_version": 1,
            "partition": "development",
            "rubric": mutation_runtime.RUBRIC,
            "rows": [receipt],
            "summary": scorecard.summarize_rows([receipt]),
        }
        with self.assertRaises(scorecard.ScorecardError) as caught:
            scorecard.validate_report(report)
        self.assertIn("mix or relabel", str(caught.exception))

    def test_committed_fault_matrix_all_tripped_with_passing_controls(self):
        matrix = mutation_runtime.fault_matrix(DOCUMENT)
        self.assertGreaterEqual(len(matrix), 6)
        for entry in matrix:
            with self.subTest(fault=entry["fault"]):
                self.assertTrue(entry["control_accepted"], entry["fault"])
                self.assertTrue(entry["tripped"], entry["fault"])
                self.assertIn(entry["expected_refusal"], entry["observed_refusal"])


class LocalizationTests(unittest.TestCase):
    def test_faults_localize_across_three_boundaries(self):
        boundaries = [
            ("apparatus.producer_call", "mi-crash", "crash.producer", None, None),
            ("apparatus.artifact_write", "mi-partial", "truncate.payload", 100, None),
            ("apparatus.receipt_append", "mi-drop", "drop.receipt", 1, None),
            ("capture.inventory", "mi-swap", "swap.capture", None, {"left": "adsr_1.output", "right": "mixer.peak"}),
        ]
        clean = HARNESS.plain_attempt()
        for seam, instance_id, operator, magnitude, configuration in boundaries:
            with self.subTest(seam=seam):
                faulted = HARNESS.attempt(
                    plan(mutation_runtime.instance(instance_id, operator, seam, magnitude, configuration))
                )
                self.assertEqual([event["seam"] for event in faulted.events], [seam])
                self.assertEqual(len(faulted.events), 1)

    def test_fault_blast_radius_stays_inside_declared_seam_data(self):
        clean = HARNESS.plain_attempt()
        declared_sha256 = clean.receipts[0]["artifact"]["sha256"]
        corrupted_store = HARNESS.attempt(
            plan(mutation_runtime.instance("mi-corrupt", "corrupt.byte", "apparatus.artifact_write", 7))
        )
        self.assertNotEqual(corrupted_store.payload, clean.payload)
        self.assertEqual(corrupted_store.receipts[0]["artifact"]["sha256"], declared_sha256)
        rebound_receipt = HARNESS.attempt(
            plan(mutation_runtime.instance("mi-rebind", "corrupt.digest", "apparatus.receipt_append"))
        )
        self.assertEqual(rebound_receipt.payload, clean.payload)
        self.assertNotEqual(rebound_receipt.receipts[0]["artifact"]["sha256"], declared_sha256)

    def test_sibling_case_bytes_are_independent_of_injected_faults(self):
        class SiblingHarness(mutation_runtime.MutationHarness):
            CASE_ID = SIBLING_CASE

        sibling = SiblingHarness(ROOT, DOCUMENT)
        sibling_plain = sibling.plain_attempt()
        self.assertNotEqual(sibling_plain.payload, HARNESS.plain_attempt().payload)
        HARNESS.attempt(
            plan(mutation_runtime.instance("mi-corrupt", "corrupt.byte", "apparatus.artifact_write", 3))
        )
        self.assertEqual(sibling.plain_attempt().store, sibling_plain.store)

    def test_events_carry_machine_readable_seam_and_detail(self):
        faulted = HARNESS.attempt(
            plan(mutation_runtime.instance("mi-partial", "truncate.payload", "apparatus.artifact_write", 100))
        )
        event = faulted.events[0]
        self.assertIn(event["seam"], CATALOG["seams"])
        self.assertEqual(event["detail"]["kept_bytes"], 100)


class SessionLifecycleTests(unittest.TestCase):
    def crash_session(self, targets):
        crash = plan(mutation_runtime.instance("mi-crash", "crash.producer", "apparatus.producer_call"))
        return mutation_runtime.InjectionSession(crash, targets)

    def test_missing_target_refuses_before_wrapping(self):
        targets = {"apparatus.producer_call": HARNESS._produce}
        swap_plan = plan(
            mutation_runtime.instance(
                "mi-swap", "swap.capture", "capture.inventory", configuration={"left": "a", "right": "b"}
            )
        )
        session = mutation_runtime.InjectionSession(swap_plan, targets)
        with self.assertRaises(mutations.MutationError) as caught:
            session.__enter__()
        self.assertIn("declared seam has no target", str(caught.exception))
        self.assertEqual(targets["apparatus.producer_call"], HARNESS._produce)

    def test_targets_restored_after_crash(self):
        targets = {"apparatus.producer_call": HARNESS._produce}
        session = self.crash_session(targets)
        with self.assertRaises(mutation_runtime.ProducerCrash):
            with session:
                targets["apparatus.producer_call"]()
        self.assertEqual(targets["apparatus.producer_call"], HARNESS._produce)

    def test_original_value_runs_before_replacement(self):
        seen = []

        def producer():
            seen.append("original")
            return b"payload"

        plan_sham = plan(mutation_runtime.instance("mi-sham", "noop.sham", "apparatus.producer_call"))
        session = mutation_runtime.InjectionSession(plan_sham, {"apparatus.producer_call": producer})
        with session:
            self.assertEqual(session.targets["apparatus.producer_call"](), b"payload")
        self.assertEqual(seen, ["original"])

    def test_duplicate_dispatch_of_one_instance_refused(self):
        targets = {"apparatus.producer_call": HARNESS._produce}
        session = mutation_runtime.InjectionSession(
            plan(mutation_runtime.instance("mi-x", "noop.sham", "apparatus.producer_call")), targets
        )
        with session:
            session.targets["apparatus.producer_call"]()
            with self.assertRaises(mutations.MutationError) as caught:
                session.targets["apparatus.producer_call"]()
        self.assertIn("duplicate event", str(caught.exception))

    def test_impossible_cross_seam_order_refused_at_completion(self):
        impossible = mutations.make_plan(
            HARNESS.binding(),
            [
                mutation_runtime.instance("mi-b", "drop.receipt", "apparatus.receipt_append", 1),
                mutation_runtime.instance("mi-a", "truncate.payload", "apparatus.artifact_write", 100),
            ],
            CATALOG,
        )
        with self.assertRaises(mutations.MutationError) as caught:
            HARNESS.attempt(impossible)
        self.assertIn("actual event order does not match the declared plan order", str(caught.exception))

    def test_event_log_completeness_fails_closed(self):
        multi = plan(
            mutation_runtime.instance("mi-a", "truncate.payload", "apparatus.artifact_write", 100),
            mutation_runtime.instance("mi-b", "drop.receipt", "apparatus.receipt_append", 1),
        )
        events = [
            {
                "instance_id": "mi-a",
                "seam": "apparatus.artifact_write",
                "status": "applied",
                "order": 0,
                "sham": False,
                "detail": {"kept_bytes": 100},
            },
            {
                "instance_id": "mi-b",
                "seam": "apparatus.receipt_append",
                "status": "applied",
                "order": 1,
                "sham": False,
                "detail": {"dropped": 1},
            },
        ]
        self.assertTrue(mutations.validate_events(multi, events)["complete"])
        with self.assertRaises(mutations.MutationError) as missing:
            mutations.validate_events(multi, events[:-1])
        self.assertIn("missing events", str(missing.exception))
        reordered = [events[1], events[0]]
        with self.assertRaises(mutations.MutationError) as swapped:
            mutations.validate_events(multi, reordered)
        self.assertIn("event order", str(swapped.exception))
        extra = events + [dict(events[0], instance_id="mi-unknown")]
        with self.assertRaises(mutations.MutationError) as extra_caught:
            mutations.validate_events(multi, extra)
        self.assertIn("extra event", str(extra_caught.exception))
        with self.assertRaises(mutations.MutationError) as duplicated:
            mutations.validate_events(multi, events + [dict(events[0])])
        self.assertIn("duplicate event", str(duplicated.exception))


class EnvelopeTests(unittest.TestCase):
    def envelope(self, result, fault_matrix=None, plan=None):
        artifacts_evidence = [
            {"path": path, "sha256": mutation_runtime.sha256(data), "size_bytes": len(data)}
            for path, data in sorted(result.store.items())
        ]
        return mutations.make_envelope(
            plan=plan or self.plan,
            events_summary=result.events_summary,
            artifacts=artifacts_evidence,
            controls={"controls_fixture": True},
            fault_matrix=fault_matrix or [],
            runtime_scope="stdlib-apparatus-only",
            not_run=["actual Voice runtime fault injection"],
        )

    def setUp(self):
        self.plan = HARNESS.sham_plan()
        self.result = HARNESS.attempt(self.plan)

    def test_envelope_roundtrip_and_identity_verification(self):
        envelope = self.envelope(self.result)
        self.assertTrue(envelope["envelope_id"].startswith("mu1-"))
        self.assertEqual(mutations.validate_envelope(envelope), envelope["envelope_id"])

    def test_tampered_envelope_refuses_identity(self):
        envelope = self.envelope(self.result)
        envelope["controls"]["controls_fixture"] = False
        with self.assertRaises(mutations.MutationError) as caught:
            mutations.validate_envelope(envelope)
        self.assertIn("envelope identity mismatch", str(caught.exception))

    def test_envelope_identity_binds_events_and_artifacts(self):
        first = self.envelope(self.result)
        changed_events = copy.deepcopy(self.result)
        changed_events.events_summary = dict(changed_events.events_summary, applied=99)
        second = self.envelope(changed_events)
        self.assertNotEqual(first["envelope_id"], second["envelope_id"])

    def test_envelope_binds_plan_and_stays_distinct_across_plans(self):
        empty_envelope = self.envelope(HARNESS.attempt(HARNESS.empty_plan()), plan=HARNESS.empty_plan())
        sham_envelope = self.envelope(self.result)
        self.assertNotEqual(empty_envelope["plan_id"], sham_envelope["plan_id"])
        self.assertNotEqual(empty_envelope["envelope_id"], sham_envelope["envelope_id"])

    def test_fault_matrix_entries_are_envelope_bound(self):
        matrix = mutation_runtime.fault_matrix(DOCUMENT)
        envelope = self.envelope(self.result, fault_matrix=matrix)
        self.assertTrue(all(entry["tripped"] for entry in envelope["fault_matrix"]))


if __name__ == "__main__":
    unittest.main()
