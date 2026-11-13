"""Timing-family actual-Voice runtime adapter contract tests (#335).

Stdlib-only and Torch-free. These tests establish adapter *mechanics* only:
manifest/inventory binding, plan and schedule binding, the stdlib
realisations, frame-preflight refusals, time-locked detectors, the worker's
fail-closed schedule validation, the scoped session's event/cleanup/evidence
behaviour over a small fake tensor backend, and offline report validation on
synthetic records. None of them renders the pinned Voice, so none of them is
actual-Voice timing coverage: that execution (fresh processes, repeats,
admitted host, publication) is the campaign's obligation (#337) and is
unrun here.
"""

import array
import copy
import hashlib
import json
import math
import struct
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "env/release-era"))
sys.path.insert(0, str(ROOT / "tests"))

from torchsynth_voice import mutations  # noqa: E402
from torchsynth_voice import mutations_timing  # noqa: E402
from torchsynth_voice import mutations_timing_runtime as tr  # noqa: E402
from torchsynth_voice import timing_voice_ops as ops  # noqa: E402
from torchsynth_voice.mutations import MutationError  # noqa: E402

import mutation_worker_timing as worker  # noqa: E402
from fake_torch import FakeModule, FakeTensor, FakeTorch  # noqa: E402

try:
    import numpy  # noqa: F401
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False

CATALOG = mutations.load_seam_catalog(ROOT / mutations.SEAM_CATALOG_PATH)
DOCUMENT = json.loads((ROOT / "spec/reference/trace-registry-v1.json").read_text())
INVENTORY = json.loads((ROOT / "spec/reference/parameter-inventory-v1.json").read_text())
PARAMETER_NAMES = [parameter["name"] for parameter in INVENTORY["parameters"]]
MANIFEST, MANIFEST_SHA256 = tr.load_manifest(ROOT)

_REGISTRY_SNAPSHOT = None


def setUpModule():
    # Scope the runtime operators to this module: the full suite shares one
    # process, and test_mutations_matrix pins the global registry count.
    global _REGISTRY_SNAPSHOT
    _REGISTRY_SNAPSHOT = dict(mutations.OPERATORS)
    mutations_timing.register_family()
    tr.register_runtime_operators()


def tearDownModule():
    mutations.OPERATORS.clear()
    mutations.OPERATORS.update(_REGISTRY_SNAPSHOT)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def schedules_for(case_id, repeat=1):
    return tr.process_schedules(MANIFEST, MANIFEST_SHA256, case_id, repeat, DOCUMENT, INVENTORY, CATALOG)


def case(case_id):
    return tr.case_record(MANIFEST, case_id)


def control_column(count=ops.CONTROL_SAMPLES, offset=0.5):
    return ops.pack([offset + 0.25 * math.sin(2 * math.pi * 3 * i / count) for i in range(count)])


class InventoryBindingTests(unittest.TestCase):
    def test_adapter_dispositions_equal_the_manifest_exactly(self):
        counts = tr.require_inventory_match(MANIFEST)
        self.assertEqual(
            counts,
            {"runtime_cell": 9, "composition": 1, "refusal": 2, "deferred_unmeasured": 4},
        )
        manifest_counts = {}
        for entry in tr.timing_entries(MANIFEST):
            manifest_counts[entry["disposition"]] = manifest_counts.get(entry["disposition"], 0) + 1
        self.assertEqual(counts, manifest_counts)
        self.assertEqual(set(tr.ADAPTER_DISPOSITIONS), {e["id"] for e in tr.timing_entries(MANIFEST)})

    def test_manifest_drift_forces_an_explicit_adapter_update(self):
        drifted = copy.deepcopy(MANIFEST)
        for entry in drifted["inventory"]:
            if entry["id"] == "timing:lfo-depth-shift-plus-0.1":
                entry["disposition"] = "runtime_cell"
        with self.assertRaisesRegex(MutationError, "dispositions differ"):
            tr.require_inventory_match(drifted)
        with self.assertRaisesRegex(ValueError, "frozen inventory digest"):
            ops.require_manifest_binding(drifted)
        retargeted = copy.deepcopy(MANIFEST)
        for entry in retargeted["inventory"]:
            if entry["id"] == "timing:route-sign-flip":
                entry["runtime_target"] = "mod_matrix.vco_2_pitch"
        with self.assertRaisesRegex(MutationError, "runtime target differs"):
            tr.require_inventory_match(retargeted)

    def test_wrong_revision_or_digest_refuses(self):
        for key, value in (("revision", 3), ("identity", "other")):
            tampered = copy.deepcopy(MANIFEST)
            tampered[key] = value
            with self.assertRaises(ValueError):
                ops.require_manifest_binding(tampered)
        tampered = copy.deepcopy(MANIFEST)
        tampered["frozen_inventory_digest"]["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            ops.require_manifest_binding(tampered)

    def test_runtime_operators_are_versioned_family_ids_never_bridge(self):
        for operator_id, spec in ops.RUNTIME_OPERATORS.items():
            with self.subTest(operator=operator_id):
                self.assertTrue(operator_id.endswith("@voice-v1"))
                self.assertFalse(operator_id.startswith("bridge."))
                self.assertIn(spec["seam"], ("voice.post_module", "voice.parameter_value"))
                self.assertTrue(CATALOG["seams"][spec["seam"]]["writable"])
                self.assertEqual(mutations.OPERATORS[operator_id], tr.runtime_definitions()[operator_id])
                if spec["realises"] is not None:
                    fixture = mutations_timing.FAMILY_OPERATORS[spec["realises"]]
                    self.assertEqual(mutations.OPERATORS[spec["realises"]], fixture)
                    self.assertEqual(fixture["seam"], "apparatus.producer_call")
        realised = {spec["realises"] for spec in ops.RUNTIME_OPERATORS.values()}
        self.assertNotIn("modulation.lfo_depth_shift", realised)
        self.assertNotIn("timing.drop_sample", realised)
        self.assertNotIn("timing.duplicate_sample", realised)
        self.assertFalse(any("align_corners" in operator for operator in mutations.OPERATORS))

    def test_dataflow_covers_the_registry(self):
        names = {trace["name"] for trace in DOCUMENT["traces"]}
        self.assertEqual(set(tr.DATAFLOW), names)
        for consumers in tr.DATAFLOW.values():
            self.assertLessEqual(set(consumers), names)


class PlanBindingTests(unittest.TestCase):
    def test_refusal_and_deferred_entries_refuse_explicitly(self):
        for entry_id in ("timing:missing-sample", "timing:duplicated-sample"):
            with self.assertRaisesRegex(MutationError, "frame-preflight refusal"):
                tr.cell_plan(MANIFEST, entry_id, "global-0", CATALOG)
        with self.assertRaisesRegex(MutationError, "deferred_unmeasured"):
            tr.cell_plan(MANIFEST, "timing:lfo-depth-shift-plus-0.1", "global-0", CATALOG)
        with self.assertRaisesRegex(MutationError, "not a timing entry"):
            tr.cell_plan(MANIFEST, "timing:interp.align_corners_false", "global-0", CATALOG)

    def test_plan_binds_case_manifest_and_slot(self):
        plans = {
            case_id: tr.cell_plan(MANIFEST, "timing:route-sign-flip", case_id, CATALOG)
            for case_id in ("global-0", "global-31", "global-32")
        }
        self.assertEqual(len({plan["plan_id"] for plan in plans.values()}), 3)
        self.assertTrue(all(plan["plan_id"].startswith("mp1-") for plan in plans.values()))
        self.assertEqual(plans["global-31"]["mutations"][0]["configuration"]["slot"], 31)
        self.assertEqual(plans["global-32"]["mutations"][0]["configuration"]["slot"], 0)
        self.assertNotEqual(
            plans["global-0"]["source_binding"]["fixture_identity"],
            plans["global-32"]["source_binding"]["fixture_identity"],
        )
        with self.assertRaisesRegex(MutationError, "plan does not match"):
            tr.resolve_schedule(plans["global-0"], MANIFEST, MANIFEST_SHA256, "timing:route-sign-flip",
                                "global-32", 1, DOCUMENT, INVENTORY, CATALOG)

    def test_tampered_plans_refuse_before_resolution(self):
        plan = tr.cell_plan(MANIFEST, "timing:route-depth-shift-plus25", "global-0", CATALOG)

        def resolve(candidate, attempt="timing:route-depth-shift-plus25"):
            return tr.resolve_schedule(candidate, MANIFEST, MANIFEST_SHA256, attempt, "global-0", 1,
                                       DOCUMENT, INVENTORY, CATALOG)

        magnitude = copy.deepcopy(plan)
        magnitude["mutations"][0]["magnitude"]["value"] = 0.5
        magnitude["plan_id"] = mutations.plan_identity(magnitude)
        with self.assertRaisesRegex(MutationError, "plan does not match"):
            resolve(magnitude)
        bridge = copy.deepcopy(plan)
        bridge["mutations"][0]["operator"] = "bridge.scale_slot"
        bridge["plan_id"] = mutations.plan_identity(bridge)
        with self.assertRaises(MutationError):
            resolve(bridge)
        stale_id = copy.deepcopy(plan)
        stale_id["mutations"][0]["configuration"]["slot"] = 1
        with self.assertRaisesRegex(MutationError, "plan_id"):
            resolve(stale_id)
        with self.assertRaisesRegex(MutationError, "plan does not match"):
            resolve(plan, attempt="timing:route-sign-flip")

    def test_holdout_and_unknown_cases_refuse(self):
        with self.assertRaisesRegex(MutationError, "unknown campaign case"):
            tr.case_record(MANIFEST, "global-96")
        with self.assertRaisesRegex(MutationError, "case not declared"):
            tr.cell_plan(MANIFEST, "timing:route-sign-flip", "global-96", CATALOG)
        tampered = copy.deepcopy(MANIFEST)
        tampered["cases"][1]["global_sound_index"] = 100
        with self.assertRaises(MutationError):
            tr.case_record(tampered, "global-31")

    def test_schedule_inventory_and_order(self):
        schedules = schedules_for("global-0")
        self.assertEqual(list(schedules)[:2], ["empty", "sham"])
        self.assertEqual(
            tr.expected_attempts(MANIFEST, "global-0"),
            ["plain"] + list(schedules) + ["clean_rerun"],
        )
        self.assertEqual(len(schedules), 12)
        composed = schedules["timing:composed-zoh-then-audio-delay"]
        self.assertEqual([m["operator"] for m in composed["module_mutations"]], [ops.ZOH, ops.DELAY_AUDIO])
        self.assertEqual(composed["campaign"]["frozen_inventory_sha256"], ops.MANIFEST_FROZEN_SHA256)
        swap = schedules["timing:route-destination-swap"]["module_mutations"][0]
        self.assertEqual((swap["module"], swap["occurrence"], swap["output_index"]), ("mod_matrix", 0, 2))
        self.assertEqual(swap["partner_output_index"], 3)
        upsample = schedules["timing:zero-order-hold-control"]["module_mutations"][0]
        self.assertEqual((upsample["module"], upsample["occurrence"]), ("control_upsample", 1))
        attack = schedules["timing:attack-breakpoint-shift-plus2"]["parameter_swaps"][0]
        self.assertEqual(attack["range"], {"minimum": 0.0, "maximum": 2.0, "curve": 0.5, "symmetric": False})


class WorkerScheduleValidationTests(unittest.TestCase):
    def setUp(self):
        self.schedules = schedules_for("global-31")
        self.case = case("global-31")

    def validate(self, schedule, case_record=None, digest=None):
        worker.validate_timing_schedule(
            schedule, DOCUMENT, PARAMETER_NAMES, case_record or self.case, digest or MANIFEST_SHA256
        )

    def test_resolved_schedules_validate(self):
        for name, schedule in self.schedules.items():
            with self.subTest(attempt=name):
                self.validate(schedule)

    def test_fail_closed_rejections(self):
        base = self.schedules["timing:route-destination-swap"]
        delay = self.schedules["timing:one-audio-sample-delay"]

        def mutated(schedule, change):
            candidate = copy.deepcopy(schedule)
            change(candidate)
            return candidate

        cases = {
            "unknown operator": mutated(base, lambda s: s["module_mutations"][0].update(
                operator="modulation.route_swap@voice-v2", applicator="modulation.route_swap@voice-v2")),
            "bridge operator": mutated(base, lambda s: s["module_mutations"][0].update(
                operator="bridge.scale_slot", applicator="bridge.scale_slot")),
            "wrong manifest digest": mutated(base, lambda s: s["campaign"].update(
                frozen_inventory_sha256="0" * 64)),
            "invalid slot": mutated(base, lambda s: s["module_mutations"][0].update(slot=0)),
            "slot outside batch": mutated(base, lambda s: s["module_mutations"][0].update(slot=32)),
            "occurrence mismatch": mutated(delay, lambda s: s["module_mutations"][0].update(occurrence=0)),
            "output index mismatch": mutated(base, lambda s: s["module_mutations"][0].update(output_index=1)),
            "partner from another call": mutated(base, lambda s: s["module_mutations"][0].update(
                partner_trace="control_upsample.vco_2_amp")),
            "reordered": mutated(self.schedules["timing:composed-zoh-then-audio-delay"],
                                 lambda s: s["declared_order"].reverse()),
            "missing field": mutated(base, lambda s: s["campaign"].pop("repeat")),
            "extra field": mutated(base, lambda s: s["module_mutations"][0].update(note="x")),
            "frame mismatch": mutated(delay, lambda s: s["module_mutations"][0].update(frame=1764)),
            "bridge raise": mutated(delay, lambda s: s["module_mutations"][0].update(**{"raise": True})),
        }
        duplicate = copy.deepcopy(self.schedules["timing:composed-zoh-then-audio-delay"])
        duplicate["module_mutations"][1]["instance_id"] = duplicate["module_mutations"][0]["instance_id"]
        cases["duplicate instance"] = duplicate
        reversed_composition = copy.deepcopy(self.schedules["timing:composed-zoh-then-audio-delay"])
        first, second = reversed_composition["module_mutations"]
        first["declared_position"], second["declared_position"] = 1, 0
        reversed_composition["module_mutations"] = [second, first]
        reversed_composition["declared_order"].reverse()
        cases["undeclared composition order"] = reversed_composition
        for label, schedule in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(worker.TimingScheduleError):
                    self.validate(schedule)
        with self.assertRaisesRegex(worker.TimingScheduleError, "foreign case"):
            self.validate(base, case_record=case("global-0"))
        with self.assertRaisesRegex(worker.TimingScheduleError, "manifest revision"):
            self.validate(base, digest="f" * 64)


class RealisationTests(unittest.TestCase):
    def test_delay_preserves_the_frame_and_is_time_locked(self):
        data = control_column()
        delayed = ops.delay_one(data, ops.CONTROL_SAMPLES)
        self.assertEqual(len(delayed), len(data))
        self.assertEqual(delayed[:4], b"\x00\x00\x00\x00")
        self.assertEqual(delayed[4:], data[:-4])
        with self.assertRaises(ops.RealisationRefused):
            ops.delay_one(data[:-4], ops.CONTROL_SAMPLES)
        plain, faulted = list(ops.floats(data)), list(ops.floats(delayed))
        row = tr.paired_exactness(plain, faulted, 441, "adsr_1.output")
        self.assertEqual((row["verdict"], row["time_origin"], row["framing_match"]), ("FAIL", "index-zero", 1))
        self.assertGreater(row["max_abs_error"], 0)
        aligned = tr.paired_exactness(plain[:-1], faulted[1:], 441, "adsr_1.output")
        self.assertEqual(aligned["verdict"], "PASS")  # alignment would mask the fault; the detector never aligns

    def test_interpolation_realisations(self):
        control = control_column()
        landed = ops.linear_align_corners(control)
        held = ops.zero_order_hold(control)
        dropped = ops.off_endpoint(control)
        values = ops.floats(control)
        for output in (landed, held, dropped):
            self.assertEqual(len(output), 4 * ops.AUDIO_SAMPLES)
            self.assertEqual(output[:4], control[:4])
        self.assertEqual(held[-4:], control[-4:])
        self.assertEqual(landed[-4:], control[-4:])
        self.assertNotEqual(dropped[-4:], control[-4:])
        self.assertTrue(set(ops.floats(held)) <= set(values))
        self.assertNotEqual(held, landed)
        self.assertNotEqual(dropped, landed)
        # A literal align_corners=False toggle samples at (j+0.5)*N/M-0.5; the
        # dropped-endpoint realisation is a different coordinate and is never
        # presented as that toggle.
        n, m = ops.CONTROL_SAMPLES, ops.AUDIO_SAMPLES
        literal = []
        for j in range(0, m, 997):
            x = min(max((j + 0.5) * n / m - 0.5, 0.0), n - 1)
            low = min(int(x), n - 2)
            literal.append(ops.to_binary32(values[low] + (x - low) * (values[low + 1] - values[low])))
        sampled = [ops.floats(dropped)[j] for j in range(0, m, 997)]
        self.assertNotEqual(literal, sampled)

    def test_route_realisations(self):
        route = control_column()
        flipped = ops.sign_flip(route)
        self.assertEqual(list(ops.floats(flipped)), [-x for x in ops.floats(route)])
        zero = ops.pack([0.0] * ops.CONTROL_SAMPLES)
        self.assertEqual(ops.sign_flip(zero), zero)
        self.assertTrue(ops.numerically_equal(zero, ops.sign_flip(zero)))
        scaled = ops.scale(route, 1.25)
        self.assertEqual(list(ops.floats(scaled)), [ops.to_binary32(x * 1.25) for x in ops.floats(route)])

    def test_parameter_conversion_is_recorded_and_bounded(self):
        attack = {"minimum": 0.0, "maximum": 2.0, "curve": 0.5, "symmetric": False}
        normalized = ops.to_binary32(0.3)
        conversion = ops.shift_parameter(normalized, attack, 2.0, "control_sample")
        self.assertEqual(conversion["physical_shift"], 2.0 / 441)
        self.assertIn("441", conversion["conversion"])
        self.assertAlmostEqual(conversion["realized_physical"] - conversion["original_physical"], 2.0 / 441, places=6)
        self.assertNotEqual(conversion["replacement_normalized"], normalized)
        self.assertEqual(conversion["range"], attack)
        with self.assertRaisesRegex(ops.RealisationRefused, "outside the declared parameter range"):
            ops.shift_parameter(1.0, attack, 2.0, "control_sample")
        with self.assertRaisesRegex(ops.RealisationRefused, "symmetric"):
            ops.shift_parameter(normalized, dict(attack, symmetric=True), 2.0, "control_sample")
        lfo = {"minimum": 0.0, "maximum": 20.0, "curve": 0.25, "symmetric": False}
        rate = ops.shift_parameter(ops.to_binary32(0.5), lfo, 0.5, "hz")
        self.assertAlmostEqual(rate["realized_physical"] - rate["original_physical"], 0.5, places=4)


def _frame(trace):
    size = 1
    for dimension in trace["shape"]:
        size *= dimension
    return size


_PLAIN = {}
_ALTERED = {}


def plain_bytes(name):
    if name not in _PLAIN:
        trace = next(t for t in DOCUMENT["traces"] if t["name"] == name)
        frame = _frame(trace)
        salt = (sum(name.encode()) % 89) / 89.0
        _PLAIN[name] = ops.pack([0.25 + 0.5 * ((i * 37 + 11) % 101) / 101.0 + salt for i in range(frame)])
    return _PLAIN[name]


def altered_bytes(name):
    if name not in _ALTERED:
        data = plain_bytes(name)
        frame = len(data) // 4
        _ALTERED[name] = ops.delay_one(data, frame) if frame > 1 else ops.pack([ops.floats(data)[0] + 1.0])
    return _ALTERED[name]


class SyntheticProcess:
    """Worker-shaped records built from the resolved schedules (mechanics only)."""

    def __init__(self, case_id):
        self.case_id = case_id
        self.case = case(case_id)
        self.schedules = schedules_for(case_id)
        self.store = {}
        self.records = {}
        slot = self.case["batch_slot"]
        names = [trace["name"] for trace in DOCUMENT["traces"]]
        plain = {
            "attempt": "plain",
            "case": case_id,
            "plan_id": None,
            "errored": None,
            "shapes_ok": True,
            "state_restored": True,
            "events": [],
            "audio_slot_sha256": sha(b"audio"),
            "batch_audio_sha256": sha(b"batch"),
            "slot_sha256": [sha(bytes([index])) for index in range(32)],
            "noise_sha256": sha(b"noise"),
            "rng_state": sha(b"rng"),
            "labels_all": True,
            "invocation_counts": {"control_upsample": 5, "mod_matrix": 1},
            "parameters_before_sha256": {"adsr_1.attack": sha(b"p-batch")},
            "parameters_selected_sha256": {
                "adsr_1.attack": sha(struct.pack("<f", 0.25)),
                "lfo_1.frequency": sha(struct.pack("<f", 0.5)),
            },
            "capture_inventory": [{"name": n, "sha256": sha(plain_bytes(n))} for n in names],
            "retained_traces": {},
            "consumed_replacements": [],
        }
        for name in names:
            plain["retained_traces"][name] = self.put("plain", name, plain_bytes(name))
        self.records["plain"] = plain
        for name in ("empty", "sham"):
            record = copy.deepcopy(plain)
            record.update(attempt=name, plan_id=self.schedules[name]["plan_id"])
            self.records[name] = record
        sham = self.schedules["sham"]["module_mutations"][0]
        self.records["sham"]["events"] = [{
            "instance_id": sham["instance_id"], "seam": "voice.post_module", "status": "ineffective",
            "order": 0, "sham": True,
            "detail": {"original_slot_sha256": sha(plain_bytes(sham["trace"])),
                       "replacement_slot_sha256": sha(plain_bytes(sham["trace"]))},
        }]
        for name, schedule in self.schedules.items():
            if name in ("empty", "sham"):
                continue
            self.records[name] = self.fault_record(name, schedule, plain, slot)
        rerun = copy.deepcopy(plain)
        rerun["attempt"] = "clean_rerun"
        self.records["clean_rerun"] = rerun

    def put(self, attempt, name, data):
        key = attempt + "/" + name
        self.store[key] = data
        return key

    def fault_record(self, name, schedule, plain, slot):
        record = copy.deepcopy(plain)
        record.update(attempt=name, plan_id=schedule["plan_id"])
        realised = tr.REALISATIONS[name]
        seams = tr.seam_traces(realised)
        for trace in tr.blast_radius(realised):
            if trace in seams:
                continue
            record["retained_traces"][trace] = self.put(name, trace, altered_bytes(trace))
            for item in record["capture_inventory"]:
                if item["name"] == trace:
                    item["sha256"] = sha(altered_bytes(trace))
        record["slot_sha256"][slot] = sha(b"faulted-slot")
        events, consumed, previous = [], [], {}
        ordered = sorted(schedule["parameter_swaps"] + schedule["module_mutations"],
                         key=lambda item: item["declared_position"])
        for order, item in enumerate(ordered):
            if "parameter" in item:
                detail = {
                    "original_slot_sha256": plain["parameters_selected_sha256"][item["parameter"]],
                    "replacement_slot_sha256": sha(b"replacement-" + item["parameter"].encode()),
                    "conversion": {"range": dict(item["range"])},
                }
                seam = "voice.parameter_value"
            else:
                trace = item["trace"]
                replacement = ops.delay_one(plain_bytes(trace), item["frame"]) + item["instance_id"].encode()
                detail = {
                    "original_slot_sha256": previous.get(trace, sha(plain_bytes(trace))),
                    "replacement_slot_sha256": sha(replacement),
                }
                previous[trace] = sha(replacement)
                consumed.append({"instance_id": item["instance_id"], "trace": trace, "slot": slot,
                                 "sha256": sha(replacement),
                                 "file": self.put(name, "consumed-" + item["instance_id"], replacement)})
                if "partner_trace" in item:
                    partner = item["partner_trace"]
                    partner_replacement = plain_bytes(trace)
                    detail["partner_original_slot_sha256"] = sha(plain_bytes(partner))
                    detail["partner_replacement_slot_sha256"] = sha(partner_replacement)
                    consumed.append({"instance_id": item["instance_id"], "trace": partner, "slot": slot,
                                     "sha256": sha(partner_replacement),
                                     "file": self.put(name, "consumed-partner", partner_replacement)})
                if "control_trace" in item:
                    detail["control_input_slot_sha256"] = sha(plain_bytes(item["control_trace"]))
                seam = "voice.post_module"
            events.append({"instance_id": item["instance_id"], "seam": seam, "status": "applied",
                           "order": order, "sham": False, "detail": detail})
        record["events"] = events
        record["consumed_replacements"] = consumed
        return record

    def evaluate(self, schedules=None, records=None):
        return tr.evaluate_process(
            MANIFEST, MANIFEST_SHA256, DOCUMENT, INVENTORY, CATALOG, self.case_id, 1,
            self.schedules if schedules is None else schedules,
            self.records if records is None else records,
            self.store.__getitem__,
        )


class ReportValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.process = SyntheticProcess("global-31")
        cls.report = cls.process.evaluate()
        mixed = SyntheticProcess("global-0")
        records = mixed.records
        sign = records["timing:route-sign-flip"]
        sign["events"][0]["status"] = "ineffective"
        delay = records["timing:one-audio-sample-delay"]
        records["timing:one-audio-sample-delay"] = {
            "attempt": delay["attempt"], "case": delay["case"], "plan_id": delay["plan_id"],
            "errored": "RuntimeMutationCrash: injected", "state_restored": True,
            "events": [dict(delay["events"][0], status="errored",
                            detail={"error": "RuntimeMutationCrash", "message": "injected"})],
        }
        sibling = records["timing:zero-order-hold-control"]
        for item in sibling["capture_inventory"]:
            if item["name"] == "lfo_2.raw":
                item["sha256"] = sha(b"changed sibling")
        unchanged = records["timing:dropped-endpoint-coordinate"]
        unchanged["retained_traces"]["vco_1.post_vca"] = mixed.records["plain"]["retained_traces"]["vco_1.post_vca"]
        for item in unchanged["capture_inventory"]:
            if item["name"] == "vco_1.post_vca":
                item["sha256"] = sha(plain_bytes("vco_1.post_vca"))
        cls.mixed = mixed.evaluate()

    def outcomes(self, report):
        return {cell["entry_id"]: cell["outcome"] for cell in report["cells"]}

    def test_valid_process_reports_cells_without_promoting_them(self):
        outcomes = self.outcomes(self.report)
        self.assertEqual(outcomes["timing:one-control-sample-delay"], "detected")
        self.assertEqual(outcomes["timing:zero-order-hold-control"], "detected")
        self.assertEqual(outcomes["timing:dropped-endpoint-coordinate"], "detected")
        self.assertEqual(outcomes["timing:one-audio-sample-delay"], "detected")
        self.assertEqual(outcomes["timing:composed-zoh-then-audio-delay"], "composition_detected")
        for entry_id in ("timing:attack-breakpoint-shift-plus2", "timing:lfo-rate-shift-plus-half-hz",
                         "timing:route-sign-flip", "timing:route-depth-shift-plus25",
                         "timing:route-destination-swap"):
            # The synthetic traces are outside every analytic domain, so each gate
            # refuses; a refusal neither blocks nor creates a kill (D7).
            self.assertEqual(outcomes[entry_id], "detected")
        for cell in self.report["cells"]:
            for gated in cell["gated_detectors"]:
                self.assertEqual(gated["outcome"], tr.GATE_REFUSED, cell["entry_id"])
                self.assertNotEqual(gated["outcome"], "not_evaluated")
        self.assertEqual(len(outcomes), 10)
        self.assertEqual([r["status"] for r in self.report["refusals"]], ["refused", "refused"])
        self.assertEqual([r["detail"]["observed_count"] for r in self.report["refusals"]], [1763, 1765])
        self.assertEqual({r["detail"]["landed_pair_status"] for r in self.report["refusals"]}, {"frame_mismatch"})
        self.assertFalse(any(r["counts_toward_runtime_total"] for r in self.report["refusals"]))
        self.assertEqual(
            sorted(self.report["deferred_unmeasured"]),
            sorted(["timing:lfo-depth-shift-plus-0.1", "timing:decay-breakpoint-shift-plus2",
                    "timing:release-breakpoint-shift-plus2", "timing:sustain-amplitude-shift-plus0.1"]),
        )
        for trace, verdicts in self.report["controls"].items():
            self.assertEqual(set(verdicts.values()), {"PASS"}, trace)
        self.assertEqual(self.report["campaign"]["frozen_inventory_sha256"], ops.MANIFEST_FROZEN_SHA256)

    def test_mixed_outcomes_never_become_detection(self):
        outcomes = self.outcomes(self.mixed)
        self.assertEqual(outcomes["timing:route-sign-flip"], "open_ineffective")
        self.assertEqual(outcomes["timing:one-audio-sample-delay"], "errored")
        self.assertEqual(outcomes["timing:zero-order-hold-control"], "invariance_failed")
        self.assertEqual(outcomes["timing:dropped-endpoint-coordinate"], "open_not_detected")
        self.assertEqual(outcomes["timing:one-control-sample-delay"], "detected")

    def assertRefused(self, mutate, pattern):
        records = copy.deepcopy(self.process.records)
        schedules = copy.deepcopy(self.process.schedules)
        mutate(records, schedules)
        with self.assertRaisesRegex(MutationError, pattern):
            self.process.evaluate(schedules=schedules, records=records)

    def test_tamper_and_omission_fail_closed(self):
        cell = "timing:composed-zoh-then-audio-delay"
        self.assertRefused(lambda r, s: r[cell]["events"].pop(), "missing events")
        self.assertRefused(lambda r, s: r[cell]["events"].reverse(), "order")
        self.assertRefused(lambda r, s: r.pop("timing:route-sign-flip"), "attempt inventory")
        self.assertRefused(lambda r, s: r.update(extra=r["plain"]), "attempt inventory")
        self.assertRefused(lambda r, s: r[cell].update(plan_id="mp1-" + "0" * 64), "schedule plan")
        self.assertRefused(lambda r, s: r[cell].update(case="global-0"), "foreign case")
        self.assertRefused(lambda r, s: s[cell]["module_mutations"][0].update(operator=ops.OFF_ENDPOINT),
                           "re-derivation")
        self.assertRefused(lambda r, s: s[cell]["campaign"].update(manifest_sha256="0" * 64), "re-derivation")
        self.assertRefused(lambda r, s: r["sham"]["events"].clear(), "missing events|sham event")
        self.assertRefused(lambda r, s: r["sham"].update(audio_slot_sha256=sha(b"x")), "byte-identical")
        self.assertRefused(lambda r, s: r["clean_rerun"].update(state_restored=False), "restore state")

    def test_preflight_and_evidence_fail_closed(self):
        cell = "timing:one-control-sample-delay"
        compared = "mixer.pre_normalization"

        def frame(records, schedules):
            key = records[cell]["retained_traces"][compared]
            self.process.store[key + "#short"] = self.process.store[key][:-4]
            records[cell]["retained_traces"][compared] = key + "#short"

        self.assertRefused(frame, "frame preflight refusal")

        def identity(records, schedules):
            records[cell]["retained_traces"][compared] = records["plain"]["retained_traces"][compared]

        self.assertRefused(identity, "identity preflight refusal")
        route = "timing:route-sign-flip"

        def original(records, schedules):
            for item in records[route]["capture_inventory"]:
                if item["name"] == "mod_matrix.vco_1_pitch":
                    item["sha256"] = sha(b"replaced at the seam")

        self.assertRefused(original, "passive original")
        self.assertRefused(lambda r, s: r[route].update(consumed_replacements=[]), "consumed replacement")
        attack = "timing:attack-breakpoint-shift-plus2"
        self.assertRefused(
            lambda r, s: r[attack]["events"][0]["detail"].update(original_slot_sha256=sha(b"x")),
            "post-randomization")
        self.assertRefused(
            lambda r, s: r[attack]["events"][0]["detail"]["conversion"].update(range={}),
            "inventory range")

    def test_interpolation_control_input_association_is_checked(self):
        zoh = "timing:zero-order-hold-control"
        record = copy.deepcopy(self.process.records[zoh])
        mutation = self.process.schedules[zoh]["module_mutations"][0]
        consumed = {(i["instance_id"], i["trace"]): i for i in record["consumed_replacements"]}
        event = record["events"][0]
        tr._check_module_event(event, mutation, record, self.process.records["plain"], consumed,
                               self.process.store.__getitem__, {})
        event["detail"]["control_input_slot_sha256"] = sha(b"foreign control input")
        with self.assertRaisesRegex(MutationError, "association"):
            tr._check_module_event(event, mutation, record, self.process.records["plain"], consumed,
                                   self.process.store.__getitem__, {})

    def test_frame_preflight_precedes_metrics(self):
        lane = plain_bytes("adsr_1.output")
        for edited in (lane[:-4], lane + lane[-4:]):
            with self.assertRaisesRegex(MutationError, "frame preflight refusal"):
                tr.trace_preflight(DOCUMENT, "adsr_1.output", edited, sha(edited))


# ---------------------------------------------------------------------------
# Per-family fake voice over the shared fake tensor backend (fake_torch.py).
# ---------------------------------------------------------------------------


class GatedDetectorTests(unittest.TestCase):
    """Admissibility gates over analytic stand-ins (mechanics only, no Voice)."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "tools"))
        from qualify_envelope_estimators import constructed_envelope

        def envelope(attack):
            return constructed_envelope(attack=attack, decay=25.5, release=30.75, sustain=0.4,
                                        alpha=1, note=90.5, count=ops.CONTROL_SAMPLES)[0]

        cls.envelope = staticmethod(envelope)
        cls.context = {"alpha": 1, "note_on_seconds": 90.5 / 441, "lfo_fundamental_in_band": True}

    def gated(self, entry_id):
        entry = tr.entry_by_id(MANIFEST, entry_id)
        return entry, entry["gated_detectors"]

    def run_gate(self, entry_id, plain, faulted, context=None):
        entry, _ = self.gated(entry_id)
        return tr.evaluate_gated(entry, plain.__getitem__, faulted.__getitem__,
                                 self.context if context is None else context)

    @staticmethod
    def lfo(frequency, count=ops.CONTROL_SAMPLES):
        return [(1 - math.cos(2 * math.pi * frequency * i / 441)) / 2 for i in range(count)]

    def routes(self, gains, source):
        outputs = {"mod_matrix." + d: [g * x + 0.1 for x in source]
                   for d, g in zip(("vco_1_pitch", "vco_1_amp", "vco_2_pitch", "vco_2_amp", "noise_amp"), gains)}
        return dict(outputs, **{"adsr_1.output": source})

    def test_attack_gate_detects_an_admitted_shift_and_leaves_an_unmoved_case_open(self):
        entry = "timing:attack-breakpoint-shift-plus2"
        plain = {"adsr_1.output": self.envelope(20.25)}
        [row] = self.run_gate(entry, plain, {"adsr_1.output": self.envelope(22.25)})
        self.assertEqual(row["outcome"], tr.GATE_DETECTED)
        self.assertAlmostEqual(row["deviation"], 2.0, places=1)
        [row] = self.run_gate(entry, plain, plain)
        self.assertEqual(row["outcome"], tr.GATE_NOT_DETECTED)

    def test_attack_gate_refuses_inadmissible_inputs_on_either_attempt(self):
        entry = "timing:attack-breakpoint-shift-plus2"
        good = {"adsr_1.output": self.envelope(20.25)}
        degenerate = {"adsr_1.output": [0.0] * ops.CONTROL_SAMPLES}
        for plain, faulted in ((good, degenerate), (degenerate, good)):
            [row] = self.run_gate(entry, plain, faulted)
            self.assertEqual(row["outcome"], tr.GATE_REFUSED)
        [row] = self.run_gate(entry, good, good, context={})
        self.assertEqual(row["outcome"], tr.GATE_REFUSED)
        self.assertIn("alpha", row["reason"])
        [row] = self.run_gate(entry, good, good, context={"alpha": 99, "note_on_seconds": 0.2})
        self.assertEqual(row["outcome"], tr.GATE_REFUSED)

    def test_lfo_gate_refuses_without_numpy_or_provenance_context(self):
        entry = "timing:lfo-rate-shift-plus-half-hz"
        plain = {"lfo_1.raw": self.lfo(4.37)}
        faulted = {"lfo_1.raw": self.lfo(4.87)}
        with mock.patch.dict(sys.modules, {"numpy": None}):
            [row] = self.run_gate(entry, plain, faulted)
        self.assertEqual(row["outcome"], tr.GATE_REFUSED)
        self.assertIn("numpy_unavailable", row["reason"])
        [row] = self.run_gate(entry, plain, plain, context={})
        self.assertEqual(row["outcome"], tr.GATE_REFUSED)

    @unittest.skipUnless(HAVE_NUMPY, "NumPy unavailable; the mutations.yml timing-numerical job runs this with the metrics extra")
    def test_lfo_gate_detected_not_detected_and_refused(self):
        entry = "timing:lfo-rate-shift-plus-half-hz"
        plain = {"lfo_1.raw": self.lfo(4.37)}
        [row] = self.run_gate(entry, plain, {"lfo_1.raw": self.lfo(4.87)})
        self.assertEqual(row["outcome"], tr.GATE_DETECTED)
        self.assertAlmostEqual(row["deviation"], 0.5, places=2)
        [row] = self.run_gate(entry, plain, plain)
        self.assertEqual(row["outcome"], tr.GATE_NOT_DETECTED)
        [row] = self.run_gate(entry, plain, {"lfo_1.raw": [0.5] * ops.CONTROL_SAMPLES})
        self.assertEqual(row["outcome"], tr.GATE_REFUSED)

    def test_route_gate_detects_sign_flip_and_refuses_unexcited_or_nonlinear(self):
        entry = "timing:route-sign-flip"
        source = self.envelope(20.25)
        plain = self.routes([0.5, 0.25, -0.75, 1.0, 0.125], source)
        [row] = self.run_gate(entry, plain, self.routes([-0.5, 0.25, -0.75, 1.0, 0.125], source))
        self.assertEqual(row["outcome"], tr.GATE_DETECTED)
        [row] = self.run_gate(entry, plain, plain)
        self.assertEqual(row["outcome"], tr.GATE_NOT_DETECTED)
        flat = self.routes([0.5, 0.25, -0.75, 1.0, 0.125], [0.5] * ops.CONTROL_SAMPLES)
        [row] = self.run_gate(entry, flat, flat)
        self.assertEqual(row["outcome"], tr.GATE_REFUSED)
        nonlinear = dict(plain, **{"mod_matrix.vco_1_amp": [x * x for x in source]})
        [row] = self.run_gate(entry, plain, nonlinear)
        self.assertEqual(row["outcome"], tr.GATE_REFUSED)
        self.assertIn("faulted", row["reason"])

    def test_policy_or_binding_drift_fails_closed(self):
        entry = copy.deepcopy(tr.entry_by_id(MANIFEST, "timing:route-sign-flip"))
        entry["gated_detectors"][0]["detector_id"] = "det-timing-99"
        with self.assertRaisesRegex(MutationError, "no admissibility binding"):
            tr.evaluate_gated(entry, lambda t: [], lambda t: [], {})
        entry = copy.deepcopy(tr.entry_by_id(MANIFEST, "timing:route-sign-flip"))
        entry["gated_detectors"][0]["counts_as_kill"] = True
        with self.assertRaisesRegex(MutationError, "policy differs"):
            tr.evaluate_gated(entry, lambda t: [], lambda t: [], {})

    def test_missing_retained_trace_fails_closed(self):
        entry, _ = self.gated("timing:lfo-rate-shift-plus-half-hz")

        def missing(trace):
            raise MutationError("retained trace missing: " + trace)

        with self.assertRaisesRegex(MutationError, "retained trace missing"):
            tr.evaluate_gated(entry, missing, missing, self.context)


class FakeRange:
    minimum, maximum, curve, symmetric = 0.0, 2.0, 0.5, False


class FakeParameter:
    def __init__(self, data):
        self.data = data
        self.parameter_range = FakeRange()


BATCH, CONTROL, AUDIO = 2, ops.CONTROL_SAMPLES, ops.AUDIO_SAMPLES


class FakeVoice:
    """A two-slot miniature of the pinned dataflow: adsr -> mod_matrix -> upsample."""

    def __init__(self, fail_module=None):
        self.keyboard = FakeModule()
        self.adsr_1 = FakeModule(fail_module == "adsr_1")
        self.mod_matrix = FakeModule(fail_module == "mod_matrix")
        self.control_upsample = FakeModule(fail_module == "control_upsample")
        self.attack = FakeParameter(FakeTensor(array.array("f", [0.25, 0.75])))

    def get_parameters(self, include_frozen=True):
        return {("adsr_1", "attack"): self.attack}

    def render(self):
        self.keyboard((), (None, None))
        level = list(self.attack.data.values)
        adsr = FakeTensor(array.array("f", [level[b] * (0.5 + 0.5 * math.sin(i / 50.0))
                                            for b in range(BATCH) for i in range(CONTROL)]), width=CONTROL)
        self.adsr_1((), adsr)
        routes = tuple(
            FakeTensor(array.array("f", [(k + 1) * x for x in adsr.values]), width=CONTROL) for k in range(5)
        )
        self.mod_matrix((adsr,), routes)
        upsampled = []
        for route in routes:
            out = FakeTensor(array.array("f", bytes(4 * BATCH * AUDIO)), width=AUDIO)
            for b in range(BATCH):
                out[b].copy_(FakeTensor(array.array("f", ops.linear_align_corners(route[b].tobytes()))))
            upsampled.append(self.control_upsample((route,), out))
        return adsr, routes, upsampled


class FakeCapture:
    """Passive observer registered first; snapshots the slot before any mutation."""

    def __init__(self, voice, slot):
        self.voice, self.slot, self.snapshots, self.handles = voice, slot, {}, []
        self.entered = False

    def __enter__(self):
        self.entered = True
        for name in ("adsr_1", "mod_matrix", "control_upsample"):
            module = getattr(self.voice, name)
            self.handles.append(module.register_forward_hook(self._observe(name)))
        return self

    def _observe(self, name):
        def observe(module, inputs, result):
            outputs = result if isinstance(result, tuple) else (result,)
            for index, value in enumerate(outputs):
                self.snapshots.setdefault((name, index), []).append(value[self.slot].tobytes())
        return observe

    def __exit__(self, *args):
        for handle in self.handles:
            handle.remove()
        self.entered = False
        return False


def fake_schedule(name, slot):
    """A resolved schedule retargeted to the miniature batch (slot < 2)."""
    schedule = copy.deepcopy(schedules_for("global-0")[name])
    for item in schedule["module_mutations"] + schedule["parameter_swaps"]:
        item["slot"] = slot
    return schedule


class SessionMechanicsTests(unittest.TestCase):
    slot = 1

    def run_attempt(self, schedule, voice=None):
        voice = voice or FakeVoice()
        capture = FakeCapture(voice, self.slot)
        session = worker.TimingMutationSession(schedule, voice, capture, FakeTorch)
        session.attach()
        try:
            result = voice.render()
        finally:
            if session._attached:
                session.detach(*sys.exc_info())
        return voice, capture, session, result

    def plain(self):
        voice = FakeVoice()
        capture = FakeCapture(voice, self.slot)
        with capture:
            result = voice.render()
        return capture, result

    def test_route_depth_replaces_in_place_and_keeps_the_original_capture(self):
        plain_capture, plain_result = self.plain()
        voice, capture, session, result = self.run_attempt(fake_schedule("timing:route-depth-shift-plus25", self.slot))
        self.assertEqual([e["status"] for e in session.events], ["applied"])
        self.assertEqual(capture.snapshots[("mod_matrix", 1)], plain_capture.snapshots[("mod_matrix", 1)])
        self.assertNotEqual(capture.snapshots[("control_upsample", 0)][1],
                            plain_capture.snapshots[("control_upsample", 0)][1])
        self.assertEqual(capture.snapshots[("control_upsample", 0)][0],
                         plain_capture.snapshots[("control_upsample", 0)][0])
        (instance_id, trace, data), = session.consumed
        self.assertEqual(trace, "mod_matrix.vco_1_amp")
        self.assertEqual(sha(data), session.events[0]["detail"]["replacement_slot_sha256"])
        self.assertEqual(session.events[0]["detail"]["original_slot_sha256"],
                         sha(plain_capture.snapshots[("mod_matrix", 1)][0]))
        self.assertEqual(result[1][1][self.slot].tobytes(), data)
        self.assertEqual(result[1][1][0].tobytes(), plain_result[1][1][0].tobytes())
        self.assertEqual(voice.mod_matrix.hooks, [])
        self.assertFalse(capture.entered)

    def test_composition_events_are_ordered_and_chained(self):
        voice, capture, session, result = self.run_attempt(
            fake_schedule("timing:composed-zoh-then-audio-delay", self.slot))
        events = session.events
        self.assertEqual([e["status"] for e in events], ["applied", "applied"])
        self.assertEqual(events[1]["detail"]["original_slot_sha256"], events[0]["detail"]["replacement_slot_sha256"])
        self.assertEqual(events[0]["detail"]["control_input_slot_sha256"],
                         sha(capture.snapshots[("mod_matrix", 1)][0]))
        self.assertEqual(result[2][1][self.slot].tobytes()[:4], b"\x00\x00\x00\x00")

    def test_parameter_swap_restored_after_success(self):
        voice = FakeVoice()
        before = voice.attack.data.tobytes()
        _, _, session, _ = self.run_attempt(fake_schedule("timing:attack-breakpoint-shift-plus2", self.slot), voice)
        event = session.events[0]
        self.assertEqual(event["status"], "applied")
        self.assertIn("441", event["detail"]["conversion"]["conversion"])
        self.assertEqual(voice.attack.data.tobytes(), before)

    def test_applicator_error_records_errored_restores_and_reruns_clean(self):
        voice = FakeVoice()
        before = voice.attack.data.tobytes()
        plain_capture, _ = self.plain()
        schedule = fake_schedule("timing:route-sign-flip", self.slot)
        session = worker.TimingMutationSession(schedule, voice, FakeCapture(voice, self.slot), FakeTorch)

        def broken(row, data):
            row.copy_(FakeTensor(array.array("f", [0.0] * CONTROL)))
            raise RuntimeError("write failed midway")

        session._write_row = broken
        session.attach()
        with self.assertRaisesRegex(RuntimeError, "write failed midway"):
            try:
                voice.render()
            finally:
                session.detach(*sys.exc_info())
        self.assertEqual([e["status"] for e in session.events], ["errored"])
        self.assertEqual(session.events[0]["detail"]["error"], "RuntimeError")
        self.assertEqual((voice.mod_matrix.hooks, voice.keyboard.hooks), ([], []))
        self.assertEqual(voice.attack.data.tobytes(), before)
        rerun = FakeCapture(voice, self.slot)
        with rerun:
            voice.render()
        self.assertEqual(rerun.snapshots, plain_capture.snapshots)

    def test_partial_setup_failure_removes_installed_hooks(self):
        voice = FakeVoice(fail_module="control_upsample")
        capture = FakeCapture(voice, self.slot)
        schedule = fake_schedule("timing:attack-breakpoint-shift-plus2", self.slot)
        schedule["module_mutations"] = fake_schedule("timing:one-audio-sample-delay", self.slot)["module_mutations"]
        schedule["module_mutations"][0]["declared_position"] = 1
        schedule["declared_order"].append(schedule["module_mutations"][0]["instance_id"])
        session = worker.TimingMutationSession(schedule, voice, capture, FakeTorch)
        with self.assertRaisesRegex(RuntimeError, "partial setup failure"):
            session.attach()
        self.assertEqual((voice.keyboard.hooks, voice.adsr_1.hooks, voice.mod_matrix.hooks), ([], [], []))
        self.assertFalse(session._attached)
        self.assertFalse(capture.entered)

    def test_sham_and_refusal_write_nothing(self):
        plain_capture, _ = self.plain()
        _, capture, session, _ = self.run_attempt(fake_schedule("sham", self.slot))
        self.assertEqual([(e["status"], e["sham"]) for e in session.events], [("ineffective", True)])
        self.assertEqual(capture.snapshots, plain_capture.snapshots)
        self.assertEqual(session.consumed, [])
        voice = FakeVoice()
        voice.attack.data.values[self.slot] = 1.0
        schedule = fake_schedule("timing:attack-breakpoint-shift-plus2", self.slot)
        _, _, session, _ = self.run_attempt(schedule, voice)
        self.assertEqual(session.events[0]["status"], "refused")
        self.assertIn("outside the declared parameter range", session.events[0]["detail"]["reason"])

    def test_multiple_attempts_in_one_process_stay_isolated(self):
        plain_capture, _ = self.plain()
        voice = FakeVoice()
        for name in ("timing:route-destination-swap", "timing:one-control-sample-delay",
                     "timing:dropped-endpoint-coordinate"):
            _, _, session, _ = self.run_attempt(fake_schedule(name, self.slot), voice)
            self.assertEqual({e["status"] for e in session.events}, {"applied"}, name)
        rerun = FakeCapture(voice, self.slot)
        with rerun:
            voice.render()
        self.assertEqual(rerun.snapshots, plain_capture.snapshots)


if __name__ == "__main__":
    unittest.main()
