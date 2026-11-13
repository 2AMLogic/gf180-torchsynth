"""Identity-family runtime adapter contract tests (#334).

Stdlib-only, Torch-free. These tests drive the family-owned runtime adapter
(``torchsynth_voice.mutations_identity_runtime`` and
``env/release-era/mutation_worker_identity.py``) with a deterministic
SYNTHETIC producer standing in for the pinned Voice. They establish adapter
mechanics only: schedule/plan binding, applicator events, passive-original
retention, cleanup, control equality, invariance checks, fail-closed report
validation and the campaign accounting hook. They never render the actual
Voice and never establish actual-Voice runtime coverage; that execution
belongs to the campaign child (#337) and is unrun here.
"""

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import directed  # noqa: E402
from torchsynth_voice import mutation_runtime  # noqa: E402
from torchsynth_voice import mutations  # noqa: E402
from torchsynth_voice import mutations_identity as mi  # noqa: E402
from torchsynth_voice import mutations_identity_runtime as rt  # noqa: E402
from torchsynth_voice.identity import SoundIdentity  # noqa: E402

mi.register_family()
WORKER = rt.load_worker(ROOT)
MANIFEST = rt.load_campaign(ROOT)
CATALOG = mutations.load_seam_catalog(ROOT / mutations.SEAM_CATALOG_PATH)
ROWS = WORKER.inventory_rows(ROOT)
IDENTITY_CELLS = [e["id"] for e in rt.identity_entries(MANIFEST)]


def _sha(data):
    return rt.sha256(data)


class SyntheticProducer:
    """Deterministic stand-in for one fresh Voice; adapter mechanics only."""

    kind = "synthetic-adapter-fixture"

    def __init__(self, request, fail_produce=False, leak_state=False,
                 render_salt=b""):
        self.request = request
        self.inventory_rows = ROWS
        self.fail_produce = fail_produce
        self.leak_state = leak_state
        self.render_salt = render_salt
        self._state = b"pristine"

    def state_token(self):
        return _sha(self._state)

    def render(self):
        block = self.request["batch_block"]
        audio = [
            (_sha(b"synthetic-audio:%d:%d" % (block, slot) + self.render_salt)
             .encode() * 2)
            for slot in range(WORKER.BATCH_SIZE)
        ]
        return {
            "audio_slot": audio[self.request["batch_slot"]],
            "batch_audio_sha256": _sha(b"".join(audio)),
            "slot_sha256": [_sha(chunk) for chunk in audio],
            "capture_inventory": [
                {"name": "noise.post_vca", "sha256": _sha(b"trace:%d" % block)}
            ],
            "invocation_counts": {"noise": 1},
            "rng_state": "synthetic-rng",
            "shapes_ok": True,
            "labels_all": True,
        }

    def produce(self):
        if self.fail_produce:
            raise RuntimeError("synthetic producer failure")
        if self.leak_state:
            self._state = b"leaked"
        index = self.request["global_sound_index"]
        normalized = {
            name: ((position * 7919 + index) % 4096 + 1) / 8192
            for position, name in enumerate(ROWS)
        }
        noise = rt.truth_noise(WORKER.NOISE_SEED, index % WORKER.NOISE_STREAMS)
        return WORKER.make_payload(self.request, normalized, ROWS, noise)

    def noise_stream(self, seed, slot):
        return rt.truth_noise(seed, slot)


def run(schedule, output, factory=SyntheticProducer):
    records = WORKER.run_schedule(schedule, factory, output)
    report = WORKER.build_report(schedule, records, {"kind": SyntheticProducer.kind})
    return report


class RunFixture:
    """One synthetic run of a case, kept for tamper tests."""

    def __init__(self, case_id, repeat=1):
        self.schedule = rt.resolve_identity_schedule(case_id, repeat, ROOT, MANIFEST)
        self.directory = Path(tempfile.mkdtemp(prefix="rti-"))
        self.report = run(self.schedule, self.directory)
        self.evaluation = rt.evaluate_report(self.report, self.schedule, self.directory, ROOT)

    def copy_output(self):
        target = Path(tempfile.mkdtemp(prefix="rti-copy-"))
        shutil.copytree(self.directory, target, dirs_exist_ok=True)
        return target

    def cleanup(self):
        shutil.rmtree(self.directory, ignore_errors=True)


class ProducerRealizationTests(unittest.TestCase):
    def test_producer_conversion_matches_the_pinned_conversion(self):
        for row in directed.inventory_rows().values():
            for normalized in (0.0, 0.25, 0.5, 0.7509765625, 1.0):
                self.assertEqual(WORKER.physical_value(row, normalized),
                                 directed.physical_value(row, normalized), row["name"])

    def test_producer_identity_projection_matches_sound_identity(self):
        for index in (0, 31, 32, 95, 4160, 9216):
            for batch in (32, 128):
                self.assertEqual(WORKER.identity_projection(index, batch),
                                 SoundIdentity(index).to_dict(batch))

    def test_inventory_order_is_canonical(self):
        self.assertEqual(list(ROWS), mi.canonical_names())


class ScheduleTests(unittest.TestCase):
    def test_every_identity_cell_is_scheduled_in_manifest_order(self):
        self.assertEqual(len(IDENTITY_CELLS), 7)
        for case in MANIFEST["cases"]:
            schedule = rt.resolve_identity_schedule(case["id"], 1, ROOT, MANIFEST)
            names = rt.expected_attempt_inventory(schedule)
            self.assertEqual(names[:3], ["plain", "empty", "sham"])
            self.assertEqual(names[-1], "clean_rerun")
            self.assertEqual([a["cell_id"] for a in schedule["attempts"][3:-1]],
                             IDENTITY_CELLS)
            self.assertEqual(schedule["refused_cells"], [])
            self.assertEqual(schedule["evidence_class"], "boundary_runtime")
            WORKER.validate_schedule(schedule)

    def test_plans_bind_case_inventory_registry_slot_and_campaign(self):
        schedule = rt.resolve_identity_schedule("global-31", 2, ROOT, MANIFEST)
        bindings = schedule["bindings"]
        self.assertEqual(bindings["batch_slot"], 31)
        self.assertEqual(bindings["noise_seed"], 13)
        self.assertEqual(bindings["clean_control"], "plain")
        self.assertEqual(bindings["campaign"]["revision"], MANIFEST["revision"])
        self.assertEqual(bindings["campaign"]["frozen_inventory_digest"],
                         MANIFEST["frozen_inventory_digest"]["sha256"])
        self.assertEqual(bindings["parameter_inventory_sha256"],
                         _sha((ROOT / rt.INVENTORY_PATH).read_bytes()))
        self.assertEqual(bindings["trace_registry_sha256"],
                         _sha((ROOT / rt.REGISTRY_PATH).read_bytes()))
        self.assertEqual(schedule["request"], {
            "case_id": "global-31", "global_sound_index": 31, "batch_size": 32,
            "batch_block": 0, "batch_slot": 31, "partition": "development"})
        for attempt in schedule["attempts"]:
            if attempt["plan"] is None:
                continue
            self.assertTrue(attempt["plan_id"].startswith("mp1-"))
            mutations.validate_plan(attempt["plan"], CATALOG)
            self.assertIn("global-31:r2", attempt["plan"]["source_binding"]["case_id"])
            for instance in attempt["plan"]["mutations"]:
                self.assertEqual(instance["seam"], "apparatus.producer_call")

    def test_plan_identity_tracks_case_and_repeat(self):
        first = rt.resolve_identity_schedule("global-0", 1, ROOT, MANIFEST)
        other_repeat = rt.resolve_identity_schedule("global-0", 2, ROOT, MANIFEST)
        other_case = rt.resolve_identity_schedule("global-32", 1, ROOT, MANIFEST)
        ids = lambda s: [a["plan_id"] for a in s["attempts"] if a["plan_id"]]  # noqa: E731
        self.assertTrue(set(ids(first)).isdisjoint(ids(other_repeat)))
        self.assertTrue(set(ids(first)).isdisjoint(ids(other_case)))

    def test_manifest_configuration_and_magnitudes_reach_the_plans(self):
        schedule = rt.resolve_identity_schedule("global-0", 1, ROOT, MANIFEST)
        by_cell = {a["cell_id"]: a for a in schedule["attempts"] if a["cell_id"]}
        shuffle = by_cell["identity:positional-parameter-shuffle"]["plan"]["mutations"][0]
        self.assertEqual(shuffle["configuration"],
                         {"first": "adsr_1.alpha", "second": "adsr_1.attack"})
        batch = by_cell["identity:identity-batch-shift-128"]["plan"]["mutations"][0]
        self.assertEqual(batch["magnitude"], {"value": 128, "unit": "batch_size"})
        composed = by_cell["identity:composed-wrong-slot-then-wrong-seed"]
        self.assertEqual([i["operator"] for i in composed["plan"]["mutations"]],
                         ["noise.slot_shift", "noise.seed_shift"])
        self.assertEqual(composed["kind"], "composition")

    def test_unsupported_cells_refuse_and_stay_unmeasured(self):
        entry = copy.deepcopy(next(e for e in rt.identity_entries(MANIFEST)
                                   if e["id"] == "identity:wrong-noise-slot"))
        variants = {
            "voice.normalization_decision": "producer handoff",
            "voice.post_module": "named producer handoff",
            "apparatus.not_a_seam": "producer handoff",
        }
        for seam, needle in variants.items():
            changed = dict(entry, runtime_seam=seam)
            self.assertIn(needle, rt.cell_refusal(changed, CATALOG, WORKER))
        self.assertIn("evidence class", rt.cell_refusal(
            dict(entry, runtime_evidence_class="graph_runtime"), CATALOG, WORKER))
        self.assertIn("applicator", rt.cell_refusal(
            dict(entry, operators=["bridge.zero_slot"],
                 magnitude=[{"operator": "bridge.zero_slot", "unit": "slot",
                             "value": 5}]), CATALOG, WORKER))
        self.assertIn("unit", rt.cell_refusal(
            dict(entry, magnitude=[{"operator": "noise.slot_shift", "unit": "Hz",
                                    "value": 5}]), CATALOG, WORKER))
        self.assertIsNone(rt.cell_refusal(entry, CATALOG, WORKER))

        def refuse_slot(entry, catalog, worker):
            if entry["id"] == "identity:wrong-noise-slot":
                return "refused for the test"
            return None

        with mock.patch.object(rt, "cell_refusal", refuse_slot):
            schedule = rt.resolve_identity_schedule("global-0", 1, ROOT, MANIFEST)
        self.assertEqual(schedule["refused_cells"], [{
            "cell_id": "identity:wrong-noise-slot", "reason": "refused for the test",
            "status": "refused_unmeasured"}])
        self.assertNotIn("identity:wrong-noise-slot",
                         [a["cell_id"] for a in schedule["attempts"]])

    def test_holdout_and_tampered_manifests_refuse(self):
        manifest = copy.deepcopy(MANIFEST)
        manifest["cases"][0] = dict(manifest["cases"][0], global_sound_index=100,
                                    batch_block=3, batch_slot=4)
        with self.assertRaisesRegex(mutations.MutationError, "holdout"):
            rt.resolve_request(manifest, "global-0")
        with self.assertRaisesRegex(mutations.MutationError, "unknown campaign case"):
            rt.resolve_request(MANIFEST, "global-96")
        tampered = copy.deepcopy(MANIFEST)
        tampered["frozen_inventory_digest"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(mutations.MutationError, "manifest refused"):
            rt.load_campaign(ROOT, tampered)

    def test_worker_schedule_gate_refuses_holdout_and_order(self):
        schedule = rt.resolve_identity_schedule("global-0", 1, ROOT, MANIFEST)
        holdout = copy.deepcopy(schedule)
        holdout["request"].update(global_sound_index=100, batch_block=3, batch_slot=4)
        with self.assertRaisesRegex(ValueError, "holdout"):
            WORKER.validate_schedule(holdout)
        reordered = copy.deepcopy(schedule)
        reordered["attempts"][1], reordered["attempts"][3] = (
            reordered["attempts"][3], reordered["attempts"][1])
        with self.assertRaisesRegex(ValueError, "attempt order"):
            WORKER.validate_schedule(reordered)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.schedule = rt.resolve_identity_schedule("global-0", 1, ROOT, MANIFEST)
        self.producer = SyntheticProducer(self.schedule["request"])

    def _plan(self, instances):
        return mutations.make_plan(
            rt.source_binding(self.schedule["request"], 1, "session-test",
                              self.schedule["bindings"]),
            instances, CATALOG)

    def test_crash_records_errored_event_and_restores_the_seam(self):
        produce = self.producer.produce
        targets = {WORKER.SEAM: produce}
        plan = self._plan([mutation_runtime.instance(
            "rti-crash", "crash.producer", "apparatus.producer_call")])
        session = WORKER.IdentityInjectionSession(plan, targets, self.producer)
        with self.assertRaises(WORKER.ProducerCrash):
            with session:
                targets[WORKER.SEAM]()
        self.assertIs(targets[WORKER.SEAM], produce)
        self.assertEqual([e["status"] for e in session.events], ["errored"])
        self.assertEqual(session.events[0]["detail"]["error"], "ProducerCrash")
        self.assertIsNotNone(session.passive_original)
        mutations.validate_events(plan, session.events)

    def test_unsupported_operator_refuses_before_the_producer_runs(self):
        calls = []

        def produce():
            calls.append(1)
            return self.producer.produce()

        targets = {WORKER.SEAM: produce}
        plan = {"plan_id": "mp1-test", "mutations": [
            {"instance_id": "a", "operator": "noise.slot_shift",
             "seam": "apparatus.producer_call", "sham": False,
             "magnitude": {"value": 5, "unit": "slot"}, "configuration": {}},
            {"instance_id": "b", "operator": "timing.unknown",
             "seam": "apparatus.producer_call", "sham": False,
             "magnitude": None, "configuration": {}},
        ]}
        session = WORKER.IdentityInjectionSession(plan, targets, self.producer)
        with self.assertRaises(WORKER.RuntimeRefusal):
            with session:
                targets[WORKER.SEAM]()
        self.assertEqual(calls, [])
        self.assertIs(targets[WORKER.SEAM], produce)
        self.assertEqual([e["status"] for e in session.events], ["refused", "refused"])
        self.assertIn("no identity runtime applicator",
                      session.events[1]["detail"]["reason"])

    def test_ineffective_shuffle_of_identical_selection(self):
        plan = self._plan([mutation_runtime.instance(
            "rti-same", "param.positional_shuffle", "apparatus.producer_call",
            configuration={"first": "adsr_1.alpha", "second": "adsr_1.alpha"})])
        targets = {WORKER.SEAM: self.producer.produce}
        session = WORKER.IdentityInjectionSession(plan, targets, self.producer)
        with session:
            presented = targets[WORKER.SEAM]()
        self.assertEqual(session.events[0]["status"], "ineffective")
        self.assertEqual(presented.digest(), session.passive_original.digest())


class SyntheticRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = RunFixture("global-0")

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def test_clean_controls_and_every_cell_detected(self):
        evaluation = self.fixture.evaluation
        self.assertEqual(evaluation["errors"], [])
        self.assertEqual(evaluation["status"], "PASS")
        self.assertTrue(all(evaluation["controls"].values()), evaluation["controls"])
        self.assertEqual(sorted(evaluation["cells"]), sorted(IDENTITY_CELLS))
        for cell_id, cell in evaluation["cells"].items():
            self.assertEqual(cell["status"], "detected", (cell_id, cell))
            self.assertTrue(all(cell["invariance"].values()), cell_id)

    def test_synthetic_evidence_is_labelled_mechanics_only(self):
        evaluation = self.fixture.evaluation
        self.assertTrue(evaluation["adapter_mechanics_only"])
        self.assertFalse(evaluation["counts_toward_graph_runtime_kills"])
        self.assertEqual(evaluation["evidence_class"], "boundary_runtime")
        with self.assertRaisesRegex(mutations.MutationError, "synthetic"):
            rt.campaign_outcomes(MANIFEST, [evaluation])

    def test_identity_and_conversion_refuse_before_noise_rows(self):
        cells = self.fixture.evaluation["cells"]
        for cell_id in ("identity:identity-batch-shift-128",
                        "identity:train-test-designation-flip",
                        "identity:positional-parameter-shuffle",
                        "identity:wrong-normalized-physical-conversion"):
            self.assertFalse(cells[cell_id]["perceptual_rows_reached"], cell_id)
            self.assertNotIn("noise.identity",
                             [row["property"] for row in cells[cell_id]["rows"]])
        self.assertEqual(
            cells["identity:positional-parameter-shuffle"]["first_wrong_canonical_parameter"],
            "adsr_1.alpha")
        self.assertEqual(
            cells["identity:identity-batch-shift-128"]["first_wrong_identity_field"],
            "render_batch_size")

    def test_noise_identity_fails_while_statistics_stay_plausible(self):
        cells = self.fixture.evaluation["cells"]
        for cell_id in ("identity:wrong-noise-slot", "identity:wrong-noise-seed",
                        "identity:composed-wrong-slot-then-wrong-seed"):
            cell = cells[cell_id]
            self.assertTrue(cell["perceptual_rows_reached"])
            self.assertTrue(cell["statistics_plausible"])
            verdicts = {row["property"]: row["verdict"] for row in cell["rows"]}
            self.assertEqual(verdicts["noise.identity"], "FAIL")
            self.assertTrue(all(verdicts["noise.ac.%d" % lag] == "PASS"
                                for lag in mi.NOISE_LAGS))

    def test_events_ordered_with_payload_digest_chain(self):
        records = {r["attempt"]: r for r in self.fixture.report["attempts"]}
        composed = records["cell-composed-wrong-slot-then-wrong-seed"]
        self.assertEqual([e["instance_id"] for e in composed["events"]],
                         ["rti-composed-wrong-slot-then-wrong-seed-0",
                          "rti-composed-wrong-slot-then-wrong-seed-1"])
        self.assertEqual([e["status"] for e in composed["events"]], ["applied", "applied"])
        first, second = composed["events"]
        self.assertEqual(first["detail"]["source_payload_sha256"],
                         composed["original_payload"]["sha256"])
        self.assertEqual(second["detail"]["source_payload_sha256"],
                         first["detail"]["replacement_payload_sha256"])
        self.assertEqual(second["detail"]["replacement_payload_sha256"],
                         composed["presented_payload"]["sha256"])
        self.assertEqual(records["empty"]["events"], [])
        self.assertEqual([e["status"] for e in records["sham"]["events"]], ["ineffective"])
        self.assertEqual(records["plain"]["events"], [])

    def test_passive_original_and_consumed_replacement_retained_separately(self):
        records = {r["attempt"]: r for r in self.fixture.report["attempts"]}
        plain = records["plain"]
        slot = records["cell-wrong-noise-slot"]
        self.assertEqual(slot["original_payload"]["sha256"], plain["original_payload"]["sha256"])
        self.assertEqual(slot["original_payload"]["noise"]["sha256"],
                         plain["original_payload"]["noise"]["sha256"])
        self.assertNotEqual(slot["presented_payload"]["noise"]["sha256"],
                            slot["original_payload"]["noise"]["sha256"])
        self.assertNotEqual(slot["original_payload"]["noise"]["file"],
                            slot["presented_payload"]["noise"]["file"])
        presented = json.loads((self.fixture.directory / slot["presented_payload"]["document"][
            "file"]).read_bytes())
        self.assertEqual(presented["noise"]["slot"], 5)
        self.assertEqual(slot["events"][0]["detail"]["wrong_slot"], 5)

    def test_every_attempt_restores_state_and_seam(self):
        for record in self.fixture.report["attempts"]:
            self.assertIsNone(record["errored"], record["attempt"])
            self.assertTrue(record["state_restored"], record["attempt"])
            self.assertTrue(record["seam_restored"], record["attempt"])


class SecondBlockRunTests(unittest.TestCase):
    """global-32 (batch block 1, slot 0): truth comes from the case record."""

    def test_block_one_case(self):
        fixture = RunFixture("global-32")
        try:
            evaluation = fixture.evaluation
            self.assertEqual(evaluation["status"], "PASS", evaluation["errors"])
            cells = evaluation["cells"]
            self.assertTrue(all(c["status"] == "detected" for c in cells.values()))
            self.assertEqual(
                cells["identity:identity-batch-shift-128"]["first_wrong_identity_field"],
                "render_batch_index")
            plain = fixture.report["attempts"][0]
            document = json.loads((fixture.directory / plain["original_payload"][
                "document"]["file"]).read_bytes())
            self.assertEqual(document["identity"], SoundIdentity(32).to_dict(32))
            self.assertEqual(document["noise"]["slot"], 0)
        finally:
            fixture.cleanup()


class TamperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = RunFixture("global-0")

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def evaluate(self, report=None, schedule=None, directory=None):
        return rt.evaluate_report(
            report if report is not None else self.fixture.report,
            schedule if schedule is not None else self.fixture.schedule,
            directory if directory is not None else self.fixture.directory, ROOT)

    def assertRefused(self, evaluation, needle):
        self.assertEqual(evaluation["status"], "FAIL")
        self.assertTrue(any(needle in error for error in evaluation["errors"]),
                        evaluation["errors"])

    def record(self, report, name):
        return next(r for r in report["attempts"] if r["attempt"] == name)

    def test_plan_magnitude_tamper(self):
        schedule = copy.deepcopy(self.fixture.schedule)
        attempt = next(a for a in schedule["attempts"]
                       if a["cell_id"] == "identity:wrong-noise-slot")
        attempt["plan"]["mutations"][0]["magnitude"]["value"] = 6
        self.assertRefused(self.evaluate(schedule=schedule), "schedule refused")

    def test_operator_tamper(self):
        schedule = copy.deepcopy(self.fixture.schedule)
        attempt = next(a for a in schedule["attempts"]
                       if a["cell_id"] == "identity:wrong-noise-seed")
        attempt["plan"]["mutations"][0]["operator"] = "noise.slot_shift"
        self.assertRefused(self.evaluate(schedule=schedule), "schedule refused")

    def test_report_not_bound_to_schedule(self):
        report = copy.deepcopy(self.fixture.report)
        report["schedule_sha256"] = "0" * 64
        self.assertRefused(self.evaluate(report), "not bound to this schedule")

    def test_case_tamper(self):
        report = copy.deepcopy(self.fixture.report)
        report["request"]["case_id"] = "global-31"
        self.assertRefused(self.evaluate(report), "request does not match")

    def test_plan_id_tamper_on_record(self):
        report = copy.deepcopy(self.fixture.report)
        self.record(report, "cell-wrong-noise-slot")["plan_id"] = "mp1-" + "0" * 64
        self.assertRefused(self.evaluate(report), "plan_id does not match")

    def test_omitted_event(self):
        report = copy.deepcopy(self.fixture.report)
        self.record(report, "cell-composed-wrong-slot-then-wrong-seed")["events"].pop()
        self.assertRefused(self.evaluate(report), "event log refused")

    def test_reordered_events(self):
        report = copy.deepcopy(self.fixture.report)
        events = self.record(report, "cell-composed-wrong-slot-then-wrong-seed")["events"]
        events.reverse()
        self.assertRefused(self.evaluate(report), "event log refused")

    def test_event_digest_tamper(self):
        report = copy.deepcopy(self.fixture.report)
        event = self.record(report, "cell-wrong-noise-seed")["events"][0]
        event["detail"]["source_payload_sha256"] = "0" * 64
        self.assertRefused(self.evaluate(report), "payload chain")

    def test_missing_and_extra_attempts(self):
        report = copy.deepcopy(self.fixture.report)
        report["attempts"] = [r for r in report["attempts"]
                              if r["attempt"] != "cell-wrong-noise-seed"]
        self.assertRefused(self.evaluate(report), "attempt inventory")
        report = copy.deepcopy(self.fixture.report)
        report["attempts"].append(copy.deepcopy(report["attempts"][-1]))
        self.assertRefused(self.evaluate(report), "attempt inventory")

    def test_retained_bytes_tamper(self):
        directory = self.fixture.copy_output()
        try:
            record = self.record(self.fixture.report, "cell-wrong-noise-slot")
            path = directory / record["presented_payload"]["noise"]["file"]
            data = bytearray(path.read_bytes())
            data[0] ^= 1
            path.write_bytes(bytes(data))
            self.assertRefused(self.evaluate(directory=directory), "re-hash")
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_presented_replaced_by_original_is_refused(self):
        report = copy.deepcopy(self.fixture.report)
        record = self.record(report, "cell-wrong-noise-slot")
        record["presented_payload"] = copy.deepcopy(record["original_payload"])
        self.assertRefused(self.evaluate(report), "last replacement")

    def test_unrestored_state_is_refused(self):
        report = copy.deepcopy(self.fixture.report)
        self.record(report, "clean_rerun")["state_restored"] = False
        self.assertRefused(self.evaluate(report), "state not restored")


def rehash_plans(schedule):
    """Rebuild every plan (and its plan_id) so the schedule is self-consistent.

    A tamper applied this way cannot be caught by stale-plan_id checks alone;
    only re-deriving the schedule from the ratified manifest refuses it.
    """
    for attempt in schedule["attempts"]:
        if attempt["plan"] is None:
            continue
        plan = mutations.make_plan(
            rt.source_binding(schedule["request"], schedule["repeat"], attempt["attempt"],
                              schedule["bindings"]),
            attempt["plan"]["mutations"], CATALOG)
        attempt["plan"] = plan
        attempt["plan_id"] = plan["plan_id"]
        attempt["declared_order"] = [i["instance_id"] for i in plan["mutations"]]
    return schedule


class ManifestBindingTamperTests(unittest.TestCase):
    """Self-consistent schedules that differ from the manifest fail closed."""

    NEEDLE = "not the schedule the ratified manifest resolves"

    def run_tampered(self, tamper):
        schedule = copy.deepcopy(rt.resolve_identity_schedule("global-0", 1, ROOT, MANIFEST))
        tamper(schedule)
        rehash_plans(schedule)
        # The tampered schedule passes the worker's own gate and every plan
        # re-validates, so the tamper is not a stale-plan_id artefact.
        WORKER.validate_schedule(schedule)
        for attempt in schedule["attempts"]:
            if attempt["plan"] is not None:
                mutations.validate_plan(attempt["plan"], CATALOG)
        directory = Path(tempfile.mkdtemp(prefix="rti-bind-"))
        try:
            report = run(schedule, directory)
            evaluation = rt.evaluate_report(report, schedule, directory, ROOT)
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        self.assertEqual(evaluation["status"], "FAIL")
        self.assertEqual(len(evaluation["errors"]), 1, evaluation["errors"])
        self.assertIn(self.NEEDLE, evaluation["errors"][0])
        self.assertEqual(evaluation["cells"], {})
        return schedule

    def test_parameter_inventory_digest_tamper(self):
        def tamper(schedule):
            schedule["bindings"]["parameter_inventory_sha256"] = "0" * 64
        schedule = self.run_tampered(tamper)
        self.assertEqual(schedule["attempts"][3]["plan"]["source_binding"]["fixture_identity"],
                         rt.source_binding(schedule["request"], 1,
                                           schedule["attempts"][3]["attempt"],
                                           schedule["bindings"])["fixture_identity"])

    def test_rehashed_magnitude_tamper(self):
        def tamper(schedule):
            attempt = next(a for a in schedule["attempts"]
                           if a["cell_id"] == "identity:wrong-noise-slot")
            attempt["plan"]["mutations"][0]["magnitude"]["value"] = 6
        schedule = self.run_tampered(tamper)
        attempt = next(a for a in schedule["attempts"]
                       if a["cell_id"] == "identity:wrong-noise-slot")
        self.assertTrue(attempt["plan_id"].startswith("mp1-"))

    def test_dropped_attempt_tamper(self):
        def tamper(schedule):
            schedule["attempts"] = [a for a in schedule["attempts"]
                                    if a["cell_id"] != "identity:wrong-noise-seed"]
        schedule = self.run_tampered(tamper)
        self.assertNotIn("identity:wrong-noise-seed",
                         [a["cell_id"] for a in schedule["attempts"]])

    def test_untampered_schedule_still_passes(self):
        fixture = RunFixture("global-0")
        try:
            self.assertEqual(fixture.evaluation["status"], "PASS",
                             fixture.evaluation["errors"])
        finally:
            fixture.cleanup()


class ErrorPathTests(unittest.TestCase):
    def setUp(self):
        self.schedule = rt.resolve_identity_schedule("global-0", 1, ROOT, MANIFEST)
        self.directory = Path(tempfile.mkdtemp(prefix="rti-err-"))

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_producer_error_on_a_fault_restores_and_never_passes(self):
        def factory(request, calls=[0]):
            calls[0] += 1
            # The 4th attempt (first fault) fails inside the producer call.
            return SyntheticProducer(request, fail_produce=calls[0] == 4)

        report = run(self.schedule, self.directory, factory)
        failed = report["attempts"][3]
        self.assertIn("synthetic producer failure", failed["errored"])
        self.assertTrue(failed["seam_restored"])
        self.assertTrue(failed["state_restored"])
        clean = report["attempts"][-1]
        self.assertIsNone(clean["errored"])
        evaluation = rt.evaluate_report(report, self.schedule, self.directory, ROOT)
        self.assertEqual(evaluation["status"], "FAIL")
        self.assertEqual(evaluation["cells"], {})

    def test_partial_setup_failure_is_recorded_not_passed(self):
        def factory(request, calls=[0]):
            calls[0] += 1
            if calls[0] == 2:
                raise RuntimeError("voice construction failed")
            return SyntheticProducer(request)

        report = run(self.schedule, self.directory, factory)
        empty = report["attempts"][1]
        self.assertIn("voice construction failed", empty["errored"])
        self.assertFalse(empty["state_restored"])
        evaluation = rt.evaluate_report(report, self.schedule, self.directory, ROOT)
        self.assertEqual(evaluation["status"], "FAIL")

    def test_state_leak_is_refused(self):
        def factory(request, calls=[0]):
            calls[0] += 1
            return SyntheticProducer(request, leak_state=calls[0] == 5)

        report = run(self.schedule, self.directory, factory)
        self.assertFalse(report["attempts"][4]["state_restored"])
        evaluation = rt.evaluate_report(report, self.schedule, self.directory, ROOT)
        self.assertEqual(evaluation["status"], "FAIL")

    def test_render_drift_breaks_invariance(self):
        def factory(request, calls=[0]):
            calls[0] += 1
            return SyntheticProducer(request,
                                     render_salt=b"drift" if calls[0] == 6 else b"")

        report = run(self.schedule, self.directory, factory)
        evaluation = rt.evaluate_report(report, self.schedule, self.directory, ROOT)
        self.assertEqual(evaluation["status"], "FAIL")
        self.assertTrue(any("invariance failed" in e for e in evaluation["errors"]),
                        evaluation["errors"])


class CampaignAccountingTests(unittest.TestCase):
    """Accounting mechanics only; the relabelled producer is a test device."""

    @classmethod
    def setUpClass(cls):
        cls.runs = [RunFixture("global-0", 1), RunFixture("global-0", 2)]

    @classmethod
    def tearDownClass(cls):
        for item in cls.runs:
            item.cleanup()

    def relabelled(self):
        evaluations = []
        for item in self.runs:
            evaluation = copy.deepcopy(item.evaluation)
            evaluation["producer_kind"] = WORKER.ACTUAL_VOICE_PRODUCER
            evaluation["adapter_mechanics_only"] = False
            evaluations.append(evaluation)
        return evaluations

    def test_two_repeats_feed_the_manifest_accounting_rule(self):
        outcomes = rt.campaign_outcomes(MANIFEST, self.relabelled())
        self.assertEqual(outcomes[("identity:wrong-noise-slot", "global-0", 2)], "detected")
        totals = rt.identity_totals(MANIFEST, outcomes, ROOT)
        self.assertFalse(totals["counts_toward_graph_runtime_kills"])
        # Six runtime cells x one detected case; the other two cases stay open.
        self.assertEqual(totals["boundary_runtime"]["kills"], 6)
        self.assertEqual(totals["boundary_runtime"]["open"], 12)
        self.assertEqual(totals["boundary_runtime"]["excluded"], 3)
        self.assertEqual(
            totals["compositions_reported_separately"][
                "identity:composed-wrong-slot-then-wrong-seed@global-0"], "detected")

    def test_single_repeat_leaves_cells_open(self):
        outcomes = rt.campaign_outcomes(MANIFEST, self.relabelled()[:1])
        totals = rt.identity_totals(MANIFEST, outcomes, ROOT)
        self.assertEqual(totals["boundary_runtime"]["kills"], 0)

    def test_repeat_byte_difference_and_failed_execution_downgrade(self):
        evaluations = self.relabelled()
        evaluations[1]["cells"]["identity:wrong-noise-slot"][
            "presented_payload_sha256"] = "0" * 64
        outcomes = rt.campaign_outcomes(MANIFEST, evaluations)
        self.assertEqual(outcomes[("identity:wrong-noise-slot", "global-0", 1)],
                         "repeat_bytes_differ")
        evaluations = self.relabelled()
        evaluations[0]["status"] = "FAIL"
        outcomes = rt.campaign_outcomes(MANIFEST, evaluations)
        self.assertEqual(rt.identity_totals(MANIFEST, outcomes, ROOT)[
            "boundary_runtime"]["kills"], 0)

    def test_other_campaign_revision_is_refused(self):
        evaluations = self.relabelled()
        evaluations[0]["campaign"] = dict(evaluations[0]["campaign"], revision=3)
        with self.assertRaisesRegex(mutations.MutationError, "different campaign"):
            rt.campaign_outcomes(MANIFEST, evaluations)


if __name__ == "__main__":
    unittest.main()
