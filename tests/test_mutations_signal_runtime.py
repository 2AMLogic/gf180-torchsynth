"""Signal-family actual-Voice runtime adapter contract tests (#336).

Stdlib-only and Torch-free. These tests establish adapter *mechanics* only:
manifest/inventory binding, plan and schedule binding, the stdlib
realisations, the plan-time normalization refusal, paired exactness
detectors, the second-detector admissibility gate, the worker's fail-closed
schedule validation, the scoped session's event/cleanup/evidence behaviour
over a small fake tensor backend, and offline report validation on synthetic
records. None of them renders the pinned Voice, so none of them is
actual-Voice signal coverage: that execution (fresh processes, repeats,
admitted host, publication) is the campaign's obligation (#337) and is unrun
here.
"""

import array
import copy
import hashlib
import importlib.util
import json
import math
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "env/release-era"))
sys.path.insert(0, str(ROOT / "tests"))

from torchsynth_voice import mutations  # noqa: E402
from torchsynth_voice import mutations_signal  # noqa: E402
from torchsynth_voice import mutations_signal_runtime as sr  # noqa: E402
from torchsynth_voice import signal_voice_ops as ops  # noqa: E402
from torchsynth_voice.mutations import MutationError  # noqa: E402

import mutation_worker_signal as worker  # noqa: E402
from fake_torch import FakeModule, FakeTensor, FakeTorch  # noqa: E402

CATALOG = mutations.load_seam_catalog(ROOT / mutations.SEAM_CATALOG_PATH)
DOCUMENT = json.loads((ROOT / "spec/reference/trace-registry-v1.json").read_text())
INVENTORY = json.loads((ROOT / "spec/reference/parameter-inventory-v1.json").read_text())
PARAMETER_NAMES = [parameter["name"] for parameter in INVENTORY["parameters"]]
MANIFEST, MANIFEST_SHA256 = sr.load_manifest(ROOT)

_REGISTRY_SNAPSHOT = None


def setUpModule():
    # Scope the runtime operators to this module: the full suite shares one
    # process, and test_mutations_matrix pins the global registry count.
    global _REGISTRY_SNAPSHOT
    _REGISTRY_SNAPSHOT = dict(mutations.OPERATORS)
    mutations_signal.register_family()
    sr.register_runtime_operators()


def tearDownModule():
    mutations.OPERATORS.clear()
    mutations.OPERATORS.update(_REGISTRY_SNAPSHOT)


def sha(data):
    return hashlib.sha256(data).hexdigest()


_SCHEDULES = {}


def schedules_for(case_id, repeat=1):
    key = (case_id, repeat)
    if key not in _SCHEDULES:
        _SCHEDULES[key] = sr.process_schedules(
            MANIFEST, MANIFEST_SHA256, case_id, repeat, DOCUMENT, INVENTORY, CATALOG)
    return copy.deepcopy(_SCHEDULES[key])


def case(case_id):
    return sr.case_record(MANIFEST, case_id)


def load_campaign_validator():
    spec = importlib.util.spec_from_file_location(
        "validate_runtime_family_campaign_for_signal_tests",
        ROOT / "tools/validate_runtime_family_campaign.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InventoryBindingTests(unittest.TestCase):
    def test_adapter_dispositions_equal_the_manifest_exactly(self):
        counts = sr.require_inventory_match(MANIFEST)
        self.assertEqual(counts, {"runtime_cell": 9, "second_detector": 2, "composition": 1,
                                  "refusal": 1, "deferred_unmeasured": 4})
        from_manifest = {e["id"]: e["disposition"] for e in MANIFEST["inventory"] if e["family"] == "signal"}
        self.assertEqual(from_manifest, sr.ADAPTER_DISPOSITIONS)

    def test_manifest_drift_forces_an_explicit_adapter_update(self):
        drifted = copy.deepcopy(MANIFEST)
        for entry in drifted["inventory"]:
            if entry["id"] == "signal:norm.off":
                entry["disposition"] = "runtime_cell"
        with self.assertRaisesRegex(MutationError, "dispositions differ"):
            sr.require_inventory_match(drifted)
        retargeted = copy.deepcopy(MANIFEST)
        for entry in retargeted["inventory"]:
            if entry["id"] == "signal:gain.db":
                entry["runtime_target"] = "vco_2.post_vca"
        with self.assertRaisesRegex(MutationError, "runtime target differs"):
            sr.require_inventory_match(retargeted)
        resized = copy.deepcopy(MANIFEST)
        for entry in resized["inventory"]:
            if entry["id"] == "signal:osc.tuning_shift":
                entry["magnitude"][0]["unit"] = "cent"
        with self.assertRaisesRegex(MutationError, "magnitude unit differs"):
            sr.require_inventory_match(resized)

    def test_wrong_revision_or_digest_refuses(self):
        for mutate in (
            lambda m: m.update(revision=2),
            lambda m: m["frozen_inventory_digest"].update(sha256="0" * 64),
            lambda m: m["inventory"].pop(),
        ):
            manifest = copy.deepcopy(MANIFEST)
            mutate(manifest)
            with self.assertRaisesRegex(MutationError, "frozen inventory digest"):
                sr.manifest_binding(manifest, MANIFEST_SHA256)
            with self.assertRaises(ValueError):
                ops.require_manifest_binding(manifest)

    def test_runtime_operators_are_versioned_family_ids_never_bridge(self):
        definitions = sr.runtime_definitions()
        self.assertEqual(set(definitions), set(ops.RUNTIME_OPERATORS))
        for operator_id, definition in definitions.items():
            self.assertTrue(operator_id.endswith("@voice-v1"), operator_id)
            self.assertFalse(operator_id.startswith(("bridge.", "norm.")), operator_id)
            realises = ops.RUNTIME_OPERATORS[operator_id]["realises"]
            if realises is not None:
                fixture = mutations_signal.OPERATOR_DEFINITIONS[realises]
                self.assertEqual(definition["seam"], fixture["seam"], operator_id)
                self.assertEqual(definition["magnitude"], fixture["magnitude"], operator_id)
        self.assertEqual(definitions[ops.ROUND_STEP]["composes_with"], [ops.SATURATION])
        self.assertEqual(definitions[ops.GAIN_DB]["composes_with"], [])
        for operator_id in ("norm.always_on", "norm.wrong_peak", "bridge.scale_slot", "gain.db", "x"):
            self.assertIsNotNone(ops.refused_operator(operator_id))
        self.assertIn("AQ-1", ops.refused_operator("norm.off"))
        self.assertIsNone(ops.refused_operator(ops.GAIN_DB))

    def test_dataflow_covers_the_registry(self):
        self.assertEqual(set(sr.DATAFLOW), {t["name"] for t in DOCUMENT["traces"]})
        self.assertEqual(sr.blast_radius([(ops.GAIN_DB, {"trace": "vco_1.post_vca"})]),
                         ["mixer.gain", "mixer.output", "mixer.peak", "mixer.pre_normalization"])
        self.assertIn("vco_2.raw", sr.blast_radius([(ops.SHAPE_SCALE, {"parameter": "vco_2.shape"})]))
        self.assertNotIn("vco_1.raw", sr.blast_radius([(ops.SHAPE_SCALE, {"parameter": "vco_2.shape"})]))


class PlanBindingTests(unittest.TestCase):
    def test_refusal_second_detector_and_deferred_entries_refuse_explicitly(self):
        with self.assertRaisesRegex(MutationError, "plan-time refusal"):
            sr.cell_plan(MANIFEST, "signal:normalization-decision-replacement", "global-0", CATALOG)
        for entry_id in ("signal:norm.always_on", "signal:norm.off", "signal:norm.wrong_peak",
                         "signal:norm.wrong_reciprocal"):
            with self.assertRaisesRegex(MutationError, "deferred_unmeasured.*AQ-1"):
                sr.cell_plan(MANIFEST, entry_id, "global-0", CATALOG)
        with self.assertRaisesRegex(MutationError, "second detector"):
            sr.cell_plan(MANIFEST, "signal:osc.tuning_shift (property)", "global-0", CATALOG)

    def test_normalization_decision_replacement_is_a_plan_time_refusal(self):
        refusal = sr.normalization_refusal(MANIFEST, CATALOG, "global-0")
        self.assertEqual(refusal["status"], "refused")
        self.assertIn("producer handoff required", refusal["detail"]["refusal"])
        self.assertFalse(refusal["counts_toward_runtime_total"])
        self.assertFalse(refusal["establishes_decision_site_execution"])
        self.assertFalse(refusal["detail"]["rendered"])
        with self.assertRaisesRegex(MutationError, "not declared for the refusal"):
            sr.normalization_refusal(MANIFEST, CATALOG, "global-31")

    def test_plan_binds_case_manifest_and_slot(self):
        plan = sr.cell_plan(MANIFEST, "signal:osc.tuning_shift", "global-31", CATALOG)
        self.assertTrue(plan["plan_id"].startswith("mp1-"))
        self.assertEqual(plan["source_binding"]["case_id"], "global-31")
        self.assertEqual(plan["source_binding"]["fixture_identity"],
                         sr.request_identity(MANIFEST, case("global-31")))
        (instance,) = plan["mutations"]
        self.assertEqual(instance["operator"], ops.TUNING_SHIFT)
        self.assertEqual(instance["seam"], "voice.parameter_value")
        self.assertEqual(instance["configuration"], {"parameter": "vco_1.tuning", "slot": 31})
        self.assertEqual(instance["magnitude"], {"value": 1.0, "unit": "semitone"})
        other = sr.cell_plan(MANIFEST, "signal:osc.tuning_shift", "global-32", CATALOG)
        self.assertNotEqual(plan["plan_id"], other["plan_id"])
        composed = sr.cell_plan(MANIFEST, "signal:composed-round-then-saturate", "global-0", CATALOG)
        self.assertEqual([m["operator"] for m in composed["mutations"]], [ops.ROUND_STEP, ops.SATURATION])

    def test_tampered_plans_refuse_before_resolution(self):
        name = "signal:gain.db"
        plan = sr.cell_plan(MANIFEST, name, "global-0", CATALOG)

        def resolve(candidate, attempt=name, case_id="global-0"):
            return sr.resolve_schedule(candidate, MANIFEST, MANIFEST_SHA256, attempt, case_id, 1,
                                       DOCUMENT, INVENTORY, CATALOG)

        resolve(plan)
        tampered = copy.deepcopy(plan)
        tampered["mutations"][0]["magnitude"]["value"] = 2.0
        with self.assertRaisesRegex(MutationError, "plan_id"):
            resolve(tampered)
        tampered["plan_id"] = mutations.plan_identity(tampered)
        with self.assertRaisesRegex(MutationError, "does not match the manifest realisation"):
            resolve(tampered)
        for operator_id in ("norm.always_on", "bridge.scale_slot", "gain.db"):
            tampered = copy.deepcopy(plan)
            tampered["mutations"][0]["operator"] = operator_id
            with self.assertRaisesRegex(MutationError, "refused before rendering"):
                resolve(tampered)
        with self.assertRaisesRegex(MutationError, "does not match the manifest realisation"):
            resolve(plan, case_id="global-31")
        with self.assertRaisesRegex(MutationError, "does not match"):
            resolve(plan, attempt="signal:gain.polarity")
        with self.assertRaisesRegex(MutationError, "repeat"):
            sr.resolve_schedule(plan, MANIFEST, MANIFEST_SHA256, name, "global-0", 0,
                                DOCUMENT, INVENTORY, CATALOG)

    def test_holdout_and_unknown_cases_refuse(self):
        manifest = copy.deepcopy(MANIFEST)
        manifest["cases"].append({"batch_block": 3, "batch_size": 32, "batch_slot": 0,
                                  "global_sound_index": 96, "id": "global-96", "partition": "development"})
        with self.assertRaisesRegex(MutationError, "holdout"):
            sr.case_record(manifest, "global-96")
        with self.assertRaisesRegex(MutationError, "unknown campaign case"):
            sr.case_record(MANIFEST, "global-7")
        with self.assertRaisesRegex(MutationError, "case not declared"):
            sr.cell_plan(MANIFEST, "signal:gain.db", "global-7", CATALOG)

    def test_schedule_inventory_extends_the_bridge_resolver(self):
        expected = ["plain", "empty", "sham", "signal:gain.db", "signal:gain.polarity",
                    "signal:gain.dc_offset", "signal:clip.round_step", "signal:clip.saturation_ceiling",
                    "signal:osc.mode_substitute", "signal:osc.shape_scale", "signal:osc.tuning_shift",
                    "signal:osc.phase_offset", "signal:composed-round-then-saturate", "clean_rerun"]
        for case_id in ("global-0", "global-31", "global-32"):
            self.assertEqual(sr.expected_attempts(MANIFEST, case_id), expected)
        schedules = schedules_for("global-31")
        self.assertEqual(list(schedules), expected[1:-1])
        for name, schedule in schedules.items():
            self.assertEqual(schedule["campaign"]["frozen_inventory_sha256"], ops.MANIFEST_FROZEN_SHA256)
            self.assertEqual(schedule["campaign"]["case"], case("global-31"))
            self.assertEqual(schedule["kind"], "mutation-voice-schedule-v1")
            for item in schedule["module_mutations"] + schedule["parameter_swaps"]:
                self.assertEqual(item["slot"], 31)
                self.assertIsNone(item["ratio"])
        saturate = schedules["signal:clip.saturation_ceiling"]["module_mutations"][0]
        self.assertEqual((saturate["module"], saturate["occurrence"], saturate["trace"]), ("vca", 1, "vco_2.post_vca"))
        gain = schedules["signal:gain.db"]["module_mutations"][0]
        self.assertEqual((gain["module"], gain["occurrence"]), ("vca", 0))
        swap = schedules["signal:osc.phase_offset"]["parameter_swaps"][0]
        self.assertEqual(swap["range"]["maximum"], 3.1415927410125732)


class WorkerScheduleValidationTests(unittest.TestCase):
    def setUp(self):
        self.schedules = schedules_for("global-0")
        self.case = case("global-0")

    def validate(self, schedule, case_record=None, digest=None):
        worker.validate_signal_schedule(schedule, DOCUMENT, PARAMETER_NAMES,
                                        case_record or self.case, digest or MANIFEST_SHA256)

    def test_resolved_schedules_validate(self):
        for name, schedule in self.schedules.items():
            self.validate(schedule)

    def test_fail_closed_rejections(self):
        gain = "signal:gain.db"
        tuning = "signal:osc.tuning_shift"
        composed = "signal:composed-round-then-saturate"

        def rejected(name, mutate, pattern, **kwargs):
            schedule = copy.deepcopy(self.schedules[name])
            mutate(schedule)
            with self.assertRaisesRegex(ValueError, pattern):
                self.validate(schedule, **kwargs)

        def operator(op):
            return lambda s: s["module_mutations"][0].update(operator=op, applicator=op)

        rejected(gain, operator("norm.always_on"), "AQ-1")
        rejected(gain, operator("bridge.scale_slot"), "bridge")
        rejected(gain, operator("gain.db@voice-v2"), "allowlist")
        rejected(gain, lambda s: s["module_mutations"][0].update(applicator=ops.POLARITY), "applicator")
        rejected(gain, lambda s: None, "manifest revision", digest="0" * 64)
        rejected(gain, lambda s: s["campaign"].update(frozen_inventory_sha256="0" * 64), "manifest revision")
        rejected(gain, lambda s: None, "foreign case", case_record=case("global-31"))
        rejected(gain, lambda s: s["module_mutations"][0].update(slot=32), "slot outside pinned batch")
        rejected(gain, lambda s: s["module_mutations"][0].update(slot=1), "case batch slot")
        rejected(gain, lambda s: s["module_mutations"][0].update(occurrence=1), "registry association")
        rejected(gain, lambda s: s["module_mutations"][0].update(trace="mixer.pre_normalization"),
                 "module-output trace")
        rejected(gain, lambda s: s["module_mutations"][0].update(trace="mixer.output"), "registry association")
        rejected(gain, lambda s: s["module_mutations"][0].update(trace="vco_2.post_vca", occurrence=1),
                 "not declared for operator")
        rejected(gain, lambda s: s["module_mutations"][0].update(ratio=1.0), "bridge ratio")
        rejected(gain, lambda s: s["module_mutations"][0]["magnitude"].update(value=61), "magnitude")
        rejected(gain, lambda s: s["module_mutations"][0]["magnitude"].update(unit="ratio"), "magnitude")
        rejected(gain, lambda s: s["module_mutations"][0].update(frame=1764), "frame")
        rejected(gain, lambda s: s["module_mutations"][0].update(sham=True), "sham flag")
        rejected(gain, lambda s: s["module_mutations"][0].update(extra=1), "entry fields")
        rejected(gain, lambda s: s.update(family="timing-runtime-v1"), "unsupported signal schedule")
        rejected(tuning, lambda s: s["parameter_swaps"][0].update(parameter="vco_2.tuning"), "not declared")
        rejected(tuning, lambda s: s["parameter_swaps"][0].update(parameter="vco_9.tuning"), "unknown inventory")
        rejected(tuning, lambda s: s["campaign"].update(repeat=0), "repeat")
        rejected(composed, lambda s: s["declared_order"].reverse(), "declared order")
        rejected(composed, lambda s: s["module_mutations"][1].update(
            instance_id=s["module_mutations"][0]["instance_id"]), "duplicate")

        def undeclared(schedule):
            second = schedule["module_mutations"][1]
            second.update(operator=ops.GAIN_DB, applicator=ops.GAIN_DB, realises="gain.db",
                          magnitude={"value": 1.0, "unit": "dB"})

        rejected(composed, undeclared, "undeclared runtime composition")

        def swapped(schedule):
            first, second = schedule["module_mutations"]
            first["declared_position"], second["declared_position"] = 1, 0
            schedule["declared_order"].reverse()

        rejected(composed, swapped, "undeclared runtime composition")
        rejected("signal:osc.mode_substitute", lambda s: s["module_mutations"][0].pop("inputs"), "inputs")


class RealisationTests(unittest.TestCase):
    lane = ops.pack([0.9 * math.sin(2 * math.pi * 3 * i / 64) for i in range(64)])

    def test_output_realisations_match_the_fixture_arithmetic(self):
        values = list(ops.floats(self.lane))
        expected, _ = mutations_signal.apply_gain_db(values, 1.0)
        replacement, detail = ops.gain_db(self.lane, 1.0, 64)
        self.assertEqual(list(ops.floats(replacement)), expected)
        self.assertEqual(detail["gain_linear"], ops.to_binary32(10 ** (1 / 20)))
        expected, _ = mutations_signal.apply_polarity(values)
        self.assertEqual(list(ops.floats(ops.polarity(self.lane, 64)[0])), expected)
        expected, _ = mutations_signal.apply_dc_offset(values, 0.05)
        self.assertEqual(list(ops.floats(ops.dc_offset(self.lane, 0.05, 64)[0])), expected)
        expected, _ = mutations_signal.apply_round_step(values, 0.0078125)
        self.assertEqual(list(ops.floats(ops.round_step(self.lane, 0.0078125, 64)[0])), expected)
        expected, _ = mutations_signal.apply_saturation(values, 0.75)
        self.assertEqual(list(ops.floats(ops.saturation(self.lane, 0.75, 64)[0])), expected)
        with self.assertRaises(ops.RealisationRefused):
            ops.gain_db(self.lane, 1.0)  # frame preflight: 64 != 176400
        with self.assertRaises(ops.RealisationRefused):
            ops.round_step(self.lane, 0.0, 64)

    def test_mode_substitute_uses_the_same_vca_inputs(self):
        raw = ops.pack([math.cos(2 * math.pi * 3 * i / 64) for i in range(64)])
        amp = ops.pack([0.5 + 0.001 * i for i in range(64)])
        replacement, detail = ops.mode_substitute(raw, amp, 64)
        for r, a, out in zip(ops.floats(raw), ops.floats(amp), ops.floats(replacement)):
            self.assertEqual(out, a if r >= 0 else -a)
        self.assertEqual((detail["mode_from"], detail["mode_to"]), ("sine", "square"))
        with self.assertRaisesRegex(ops.RealisationRefused, "VCA inputs"):
            ops.apply_output(ops.MODE_SUBSTITUTE, raw, None, 64, None)
        with self.assertRaisesRegex(ops.RealisationRefused, "AQ-1"):
            ops.apply_output("norm.always_on", raw, None, 64)

    def test_parameter_conversions_are_recorded_and_bounded(self):
        tuning = sr._inventory_range(INVENTORY, "vco_1.tuning")
        phase = sr._inventory_range(INVENTORY, "vco_1.initial_phase")
        shape = sr._inventory_range(INVENTORY, "vco_2.shape")
        record = ops.convert_parameter(ops.TUNING_SHIFT, 0.5, tuning, {"value": 1.0, "unit": "semitone"})
        self.assertAlmostEqual(record["realized_physical"], 1.0, places=5)
        self.assertIn("semitones", record["conversion"])
        with self.assertRaisesRegex(ops.RealisationRefused, "outside the declared parameter range"):
            ops.convert_parameter(ops.TUNING_SHIFT, 0.99, tuning, {"value": 1.0, "unit": "semitone"})
        record = ops.convert_parameter(ops.PHASE_OFFSET, 0.95, phase, {"value": math.pi / 4, "unit": "radian"})
        self.assertTrue(record["wrapped"])
        self.assertAlmostEqual(math.cos(record["realized_physical"]),
                               math.cos(record["original_physical"] + math.pi / 4), places=5)
        record = ops.convert_parameter(ops.SHAPE_SCALE, 0.25, shape, {"value": 2.0, "unit": "ratio"})
        self.assertEqual((record["replacement_normalized"], record["clamped"]), (0.5, False))
        record = ops.convert_parameter(ops.SHAPE_SCALE, 0.75, shape, {"value": 2.0, "unit": "ratio"})
        self.assertEqual((record["replacement_normalized"], record["clamped"]), (1.0, True))
        with self.assertRaisesRegex(ops.RealisationRefused, "unit"):
            ops.convert_parameter(ops.TUNING_SHIFT, 0.5, tuning, {"value": 100.0, "unit": "cent"})
        with self.assertRaisesRegex(ops.RealisationRefused, "symmetric"):
            ops.convert_parameter(ops.TUNING_SHIFT, 0.5, dict(tuning, symmetric=True),
                                  {"value": 1.0, "unit": "semitone"})
        with self.assertRaisesRegex(ops.RealisationRefused, "AQ-1"):
            ops.convert_parameter("norm.off", 0.5, tuning, None)
        live = dict(phase, minimum=-math.pi, maximum=math.pi)
        self.assertTrue(ops.same_binary32_range(live, phase))
        self.assertFalse(ops.same_binary32_range(dict(live, maximum=3.0), phase))


# ---------------------------------------------------------------------------
# Synthetic worker-shaped process records (mechanics only; nothing rendered)
# ---------------------------------------------------------------------------


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
        values = list(ops.floats(plain_bytes(name)))
        values[0] += 1.0
        _ALTERED[name] = ops.pack(values)
    return _ALTERED[name]


class SyntheticProcess:
    """Worker-shaped records built from the resolved schedules (mechanics only)."""

    def __init__(self, case_id, repeat=1):
        self.case_id = case_id
        self.repeat = repeat
        self.case = case(case_id)
        self.schedules = schedules_for(case_id, repeat)
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
            "invocation_counts": {"vca": 3, "control_upsample": 5},
            "parameters_before_sha256": {"vco_1.tuning": sha(b"p-batch")},
            "parameters_selected_sha256": {
                "vco_1.tuning": sha(struct.pack("<f", 0.5)),
                "vco_1.initial_phase": sha(struct.pack("<f", 0.5)),
                "vco_2.shape": sha(struct.pack("<f", 0.25)),
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
        realised = sr.REALISATIONS[name]
        seams = sr.seam_traces(realised)
        for trace in sr.blast_radius(realised):
            if trace in seams:
                continue
            record["retained_traces"][trace] = self.put(name, trace, altered_bytes(trace))
            for item in record["capture_inventory"]:
                if item["name"] == trace:
                    item["sha256"] = sha(altered_bytes(trace))
        record["slot_sha256"][slot] = sha(b"faulted-slot")
        digests = {item["name"]: item["sha256"] for item in record["capture_inventory"]}
        events, consumed, previous = [], [], {}
        ordered = sorted(schedule["parameter_swaps"] + schedule["module_mutations"],
                         key=lambda item: item["declared_position"])
        for order, item in enumerate(ordered):
            if "parameter" in item:
                detail = {
                    "operator": item["operator"],
                    "parameter": item["parameter"],
                    "slot": slot,
                    "original_slot_sha256": plain["parameters_selected_sha256"][item["parameter"]],
                    "replacement_slot_sha256": sha(b"replacement-" + item["parameter"].encode()),
                    "inventory_range": dict(item["range"]),
                    "conversion": {"range": dict(item["range"]), "unit": item["magnitude"]["unit"],
                                   "magnitude": item["magnitude"]["value"], "conversion": "declared"},
                }
                seam = "voice.parameter_value"
            else:
                trace = item["trace"]
                replacement = altered_bytes(trace) + item["instance_id"].encode()
                detail = {
                    "operator": item["operator"],
                    "trace": trace,
                    "slot": slot,
                    "original_slot_sha256": previous.get(trace, sha(plain_bytes(trace))),
                    "replacement_slot_sha256": sha(replacement),
                }
                if "inputs" in item:
                    detail["input_slot_sha256"] = [digests[n] for n in item["inputs"]]
                previous[trace] = sha(replacement)
                consumed.append({"instance_id": item["instance_id"], "trace": trace, "slot": slot,
                                 "sha256": sha(replacement),
                                 "file": self.put(name, "consumed-" + item["instance_id"], replacement)})
                seam = "voice.post_module"
            events.append({"instance_id": item["instance_id"], "seam": seam, "status": "applied",
                           "order": order, "sham": False, "detail": detail})
        record["events"] = events
        record["consumed_replacements"] = consumed
        return record

    def evaluate(self, schedules=None, records=None, estimator=None):
        return sr.evaluate_process(
            MANIFEST, MANIFEST_SHA256, DOCUMENT, INVENTORY, CATALOG, self.case_id, self.repeat,
            self.schedules if schedules is None else schedules,
            self.records if records is None else records,
            self.store.__getitem__,
            estimator=estimator or refusing_estimator,
        )


def refusing_estimator(samples, *, envelope_shaped):
    return {"status": "refused", "reason": "fundamental_in_band_provenance_required_aliases_unidentifiable",
            "input_sha256": None}


def numpy_missing_estimator(samples, *, envelope_shaped):
    return {"status": "refused", "reason": sr.NUMPY_UNAVAILABLE, "input_sha256": None}


def admitting_estimator(samples, *, envelope_shaped):
    return {"status": "valid", "reason": "analytic_stationary_model", "input_sha256": None}


class ReportValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.process = SyntheticProcess("global-0")
        cls.report = cls.process.evaluate()
        mixed = SyntheticProcess("global-31")
        records = mixed.records
        records["signal:gain.polarity"]["events"][0]["status"] = "ineffective"
        dc = records["signal:gain.dc_offset"]
        records["signal:gain.dc_offset"] = {
            "attempt": dc["attempt"], "case": dc["case"], "plan_id": dc["plan_id"],
            "errored": "RuntimeError: write failed", "state_restored": True,
            "events": [dict(dc["events"][0], status="errored",
                            detail={"error": "RuntimeError", "message": "write failed"})],
        }
        for item in records["signal:clip.round_step"]["capture_inventory"]:
            if item["name"] == "vco_2.raw":
                item["sha256"] = sha(b"changed sibling")
        unchanged = records["signal:osc.shape_scale"]
        unchanged["retained_traces"]["vco_2.post_vca"] = records["plain"]["retained_traces"]["vco_2.post_vca"]
        for item in unchanged["capture_inventory"]:
            if item["name"] == "vco_2.post_vca":
                item["sha256"] = sha(plain_bytes("vco_2.post_vca"))
        records["signal:osc.tuning_shift"]["events"][0]["status"] = "refused"
        cls.mixed = mixed.evaluate(estimator=numpy_missing_estimator)

    def outcomes(self, report):
        return {cell["entry_id"]: cell["outcome"] for cell in report["cells"]}

    def test_valid_process_reports_cells_without_promoting_them(self):
        outcomes = self.outcomes(self.report)
        for entry_id, disposition in sr.ADAPTER_DISPOSITIONS.items():
            if disposition == "runtime_cell":
                self.assertEqual(outcomes[entry_id], "detected", entry_id)
        self.assertEqual(outcomes["signal:composed-round-then-saturate"], "composition_detected")
        self.assertEqual(len(outcomes), 10)
        for cell in self.report["cells"]:
            for row in cell["mandatory_detectors"]:
                self.assertEqual((row["verdict"], row["framing_match"], row["exact_equal"]), ("FAIL", 1, 0))
            self.assertTrue(all(cell["invariance"].values()), cell["entry_id"])
        seconds = {s["entry_id"]: s["outcome"] for c in self.report["cells"] for s in c["second_detectors"]}
        self.assertEqual(seconds, {"signal:osc.tuning_shift (property)": "refused_inadmissible_not_counted",
                                   "signal:osc.phase_offset (property)": "refused_inadmissible_not_counted"})
        (refusal,) = self.report["refusals"]
        self.assertEqual((refusal["entry_id"], refusal["status"]),
                         ("signal:normalization-decision-replacement", "refused"))
        self.assertEqual(self.report["deferred_unmeasured"],
                         ["signal:norm.always_on", "signal:norm.off", "signal:norm.wrong_peak",
                          "signal:norm.wrong_reciprocal"])
        for trace, verdicts in self.report["controls"].items():
            self.assertEqual(set(verdicts.values()), {"PASS"}, trace)
        self.assertEqual(set(self.report["controls"]), {"mixer.pre_normalization", "vco_1.post_vca", "vco_2.post_vca"})
        self.assertEqual(self.report["campaign"]["frozen_inventory_sha256"], ops.MANIFEST_FROZEN_SHA256)
        self.assertIn("never qualify actual-Voice coverage", self.report["scope"])

    def test_mixed_outcomes_never_become_detection(self):
        outcomes = self.outcomes(self.mixed)
        self.assertEqual(outcomes["signal:gain.polarity"], "open_ineffective")
        self.assertEqual(outcomes["signal:gain.dc_offset"], "errored")
        self.assertEqual(outcomes["signal:clip.round_step"], "invariance_failed")
        self.assertEqual(outcomes["signal:osc.shape_scale"], "open_not_detected")
        self.assertEqual(outcomes["signal:osc.tuning_shift"], "refused")
        self.assertEqual(outcomes["signal:gain.db"], "detected")
        self.assertEqual(self.mixed["refusals"], [])  # the refusal entry declares global-0 only
        seconds = {s["entry_id"]: s["outcome"] for c in self.mixed["cells"] for s in c["second_detectors"]}
        self.assertEqual(seconds, {"signal:osc.phase_offset (property)": "not_evaluated"})

    def test_second_detector_gate_never_promotes_an_admitted_pair(self):
        lanes = {label: {sr.PERIODIC_TRACE: [0.0] * 16,
                         "control_upsample.vco_1_pitch": [0.0] * 16,
                         "control_upsample.vco_1_amp": [0.5] * 15 + [0.25]} for label in ("plain", "faulted")}
        second = sr.entry_by_id(MANIFEST, "signal:osc.tuning_shift (property)")
        row = sr.second_detector_gate(second, lanes, admitting_estimator)
        self.assertEqual(row["outcome"], "not_evaluated")
        self.assertTrue(row["attempts"]["plain"]["envelope_shaped"])
        row = sr.second_detector_gate(second, lanes, refusing_estimator)
        self.assertEqual(row["outcome"], "refused_inadmissible_not_counted")
        row = sr.second_detector_gate(second, lanes, sr.default_estimator)
        self.assertIn(row["outcome"], ("refused_inadmissible_not_counted", "not_evaluated"))
        self.assertNotEqual(row["outcome"], "detected")

    def test_campaign_accounting_integration(self):
        validator = load_campaign_validator()
        outcomes = {}
        for case_id in ("global-0", "global-31", "global-32"):
            for repeat in (1, 2):
                report = copy.deepcopy(self.report)
                report["case"] = case(case_id)
                report["repeat"] = repeat
                outcomes.update(sr.campaign_outcomes(report))
        signal_only = copy.deepcopy(MANIFEST)
        signal_only["inventory"] = sr.signal_entries(MANIFEST)
        totals = validator.runtime_kill_totals(signal_only, outcomes)
        self.assertEqual(totals["kills"], 27)
        self.assertEqual(totals["open"], 0)
        self.assertNotIn(("signal:composed-round-then-saturate", "global-0", 1), outcomes)
        mixed = sr.campaign_outcomes(self.mixed)
        self.assertNotIn(("signal:osc.phase_offset (property)", "global-31", 1), mixed)
        one_repeat = {k: v for k, v in outcomes.items() if k[2] == 1}
        self.assertEqual(validator.runtime_kill_totals(signal_only, one_repeat)["kills"], 0)
        with self.assertRaisesRegex(MutationError, "another campaign revision"):
            sr.campaign_outcomes(dict(self.report, campaign=dict(self.report["campaign"],
                                                                 frozen_inventory_sha256="0" * 64)))

    def assertRefused(self, mutate, pattern):
        records = copy.deepcopy(self.process.records)
        schedules = copy.deepcopy(self.process.schedules)
        mutate(records, schedules)
        with self.assertRaisesRegex(MutationError, pattern):
            self.process.evaluate(schedules=schedules, records=records)

    def test_tamper_and_omission_fail_closed(self):
        cell = "signal:composed-round-then-saturate"
        self.assertRefused(lambda r, s: r[cell]["events"].pop(), "missing events")
        self.assertRefused(lambda r, s: r[cell]["events"].reverse(), "order")
        self.assertRefused(lambda r, s: r[cell]["events"].append(dict(r[cell]["events"][0], order=2)),
                           "duplicate event")
        self.assertRefused(lambda r, s: r.pop("signal:gain.polarity"), "attempt inventory")
        self.assertRefused(lambda r, s: r.update(extra=r["plain"]), "attempt inventory")
        self.assertRefused(lambda r, s: r[cell].update(plan_id="mp1-" + "0" * 64), "schedule plan")
        self.assertRefused(lambda r, s: r[cell].update(case="global-31"), "foreign case")
        self.assertRefused(lambda r, s: s[cell]["module_mutations"][0].update(operator=ops.SATURATION),
                           "re-derivation")
        self.assertRefused(lambda r, s: s[cell]["campaign"].update(manifest_sha256="0" * 64), "re-derivation")
        self.assertRefused(lambda r, s: s["signal:gain.db"]["source_binding"].update(case_id="global-31"),
                           "re-derivation")
        self.assertRefused(lambda r, s: r["sham"]["events"].clear(), "missing events|sham event")
        self.assertRefused(lambda r, s: r["sham"].update(audio_slot_sha256=sha(b"x")), "byte-identical")
        self.assertRefused(lambda r, s: r["empty"].update(slot_sha256=list(reversed(r["plain"]["slot_sha256"]))),
                           "byte-identical")
        self.assertRefused(lambda r, s: r["clean_rerun"].update(state_restored=False), "restore state")
        self.assertRefused(lambda r, s: r["signal:gain.db"].update(state_restored=False), "restore state")
        self.assertRefused(lambda r, s: r["signal:gain.db"]["events"][0]["detail"].update(operator=ops.POLARITY),
                           "not bound to its scheduled")

    def test_preflight_and_evidence_fail_closed(self):
        cell = "signal:gain.db"
        compared = "mixer.pre_normalization"

        def frame(records, schedules):
            key = records[cell]["retained_traces"][compared]
            self.process.store[key + "#short"] = self.process.store[key][:-4]
            records[cell]["retained_traces"][compared] = key + "#short"

        self.assertRefused(frame, "frame preflight refusal")

        def identity(records, schedules):
            records[cell]["retained_traces"][compared] = records["plain"]["retained_traces"][compared]

        self.assertRefused(identity, "identity preflight refusal")

        def original(records, schedules):
            for item in records[cell]["capture_inventory"]:
                if item["name"] == "vco_1.post_vca":
                    item["sha256"] = sha(b"replaced at the seam")

        self.assertRefused(original, "passive original")
        self.assertRefused(lambda r, s: r[cell].update(consumed_replacements=[]), "consumed replacement")

        def foreign_consumed(records, schedules):
            records[cell]["consumed_replacements"][0]["file"] = records["plain"]["retained_traces"][compared]

        self.assertRefused(foreign_consumed, "consumed replacement evidence does not match")
        tuning = "signal:osc.tuning_shift"
        self.assertRefused(
            lambda r, s: r[tuning]["events"][0]["detail"].update(original_slot_sha256=sha(b"x")),
            "post-randomization")
        self.assertRefused(
            lambda r, s: r[tuning]["events"][0]["detail"]["conversion"].update(range={}),
            "inventory range")
        self.assertRefused(
            lambda r, s: r[tuning]["events"][0]["detail"]["conversion"].pop("conversion"),
            "unit conversion")
        mode = "signal:osc.mode_substitute"
        self.assertRefused(
            lambda r, s: r[mode]["events"][0]["detail"].update(input_slot_sha256=[sha(b"a"), sha(b"b")]),
            "association")


# ---------------------------------------------------------------------------
# Per-family fake voice over the shared fake tensor backend (fake_torch.py).
# ---------------------------------------------------------------------------


class FakeRange:
    def __init__(self, parameter_range):
        self.minimum = parameter_range["minimum"]
        self.maximum = parameter_range["maximum"]
        self.curve = parameter_range["curve"]
        self.symmetric = parameter_range["symmetric"]


class FakeParameter:
    def __init__(self, name, values):
        self.data = FakeTensor(array.array("f", values))
        self.parameter_range = FakeRange(sr._inventory_range(INVENTORY, name))


BATCH, AUDIO = 2, ops.AUDIO_SAMPLES


class FakeVoice:
    """A two-slot miniature of the pinned audio path: vco -> vca (x3) -> mixer."""

    def __init__(self, fail_module=None):
        self.keyboard = FakeModule()
        self.vca = FakeModule(fail_module == "vca")
        self.parameters = {
            ("vco_1", "tuning"): FakeParameter("vco_1.tuning", [0.5, 0.5]),
            ("vco_1", "initial_phase"): FakeParameter("vco_1.initial_phase", [0.5, 0.95]),
            ("vco_2", "shape"): FakeParameter("vco_2.shape", [0.25, 0.25]),
        }

    def get_parameters(self, include_frozen=True):
        return dict(self.parameters)

    def _physical(self, module, name, slot):
        parameter = self.parameters[(module, name)]
        normalized = parameter.data.values[slot]
        r = parameter.parameter_range
        return r.minimum + (r.maximum - r.minimum) * normalized

    def _lane(self, sample):
        return FakeTensor(array.array("f", [sample(b, i) for b in range(BATCH) for i in range(AUDIO)]),
                          width=AUDIO)

    def render(self):
        self.keyboard((), (None, None))
        tuning = [self._physical("vco_1", "tuning", b) for b in range(BATCH)]
        phase = [self._physical("vco_1", "initial_phase", b) for b in range(BATCH)]
        shape = [self._physical("vco_2", "shape", b) for b in range(BATCH)]
        step = 2 * math.pi * 110.0 / ops.AUDIO_RATE_HZ
        raw_1 = self._lane(lambda b, i: math.cos(step * 2 ** (tuning[b] / 12) * i + phase[b]))
        amp = self._lane(lambda b, i: 0.5 + 0.5 * ((i % 441) / 441.0))
        post_1 = self._lane(lambda b, i: raw_1.values[b * AUDIO + i] * amp.values[b * AUDIO + i])
        self.vca((raw_1, amp), post_1)
        raw_2 = self._lane(lambda b, i: (1 - shape[b]) * (1.0 if (i // 100) % 2 else -1.0)
                           + shape[b] * ((i % 200) / 100.0 - 1.0))
        post_2 = self._lane(lambda b, i: raw_2.values[b * AUDIO + i] * amp.values[b * AUDIO + i])
        self.vca((raw_2, amp), post_2)
        post_3 = self._lane(lambda b, i: 0.01 * ((i * 7919) % 13 - 6))
        self.vca((post_3, amp), post_3)
        mixed = array.array("f", (a + b + c for a, b, c in zip(post_1.values, post_2.values, post_3.values)))
        return (raw_1, post_1, post_2, FakeTensor(mixed, width=AUDIO))


class FakeCapture:
    """Passive observer registered first; snapshots the slot before any mutation."""

    def __init__(self, voice, slot):
        self.voice, self.slot, self.snapshots, self.handles = voice, slot, {}, []
        self.entered = False

    def __enter__(self):
        self.entered = True
        self.handles.append(self.voice.vca.register_forward_hook(self._observe("vca")))
        return self

    def _observe(self, name):
        def observe(module, inputs, result):
            self.snapshots.setdefault(name, []).append(result[self.slot].tobytes())
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

    @classmethod
    def setUpClass(cls):
        voice = FakeVoice()
        cls.plain_capture = FakeCapture(voice, cls.slot)
        with cls.plain_capture:
            cls.plain_result = voice.render()

    def run_attempt(self, schedule, voice=None):
        voice = voice or FakeVoice()
        capture = FakeCapture(voice, self.slot)
        session = worker.SignalMutationSession(schedule, voice, capture, FakeTorch)
        session.attach()
        try:
            result = voice.render()
        finally:
            if session._attached:
                session.detach(*sys.exc_info())
        return voice, capture, session, result

    def test_gain_replaces_in_place_and_keeps_the_original_capture(self):
        voice, capture, session, result = self.run_attempt(fake_schedule("signal:gain.db", self.slot))
        (event,) = session.events
        self.assertEqual(event["status"], "applied")
        self.assertEqual(capture.snapshots["vca"], self.plain_capture.snapshots["vca"])
        (instance_id, trace, data), = session.consumed
        self.assertEqual(trace, "vco_1.post_vca")
        self.assertEqual(sha(data), event["detail"]["replacement_slot_sha256"])
        self.assertEqual(event["detail"]["original_slot_sha256"], sha(self.plain_capture.snapshots["vca"][0]))
        self.assertEqual(event["detail"]["realisation"]["db"], 1.0)
        self.assertGreater(event["detail"]["affected_samples"], 0)
        self.assertEqual(result[1][self.slot].tobytes(), data)
        self.assertNotEqual(result[3][self.slot].tobytes(), self.plain_result[3][self.slot].tobytes())
        self.assertEqual(result[1][0].tobytes(), self.plain_result[1][0].tobytes())
        self.assertEqual(result[3][0].tobytes(), self.plain_result[3][0].tobytes())
        self.assertEqual((voice.vca.hooks, voice.keyboard.hooks), ([], []))
        self.assertFalse(capture.entered)

    def test_composition_events_are_ordered_and_chained(self):
        _, _, session, result = self.run_attempt(fake_schedule("signal:composed-round-then-saturate", self.slot))
        events = session.events
        self.assertEqual([e["status"] for e in events], ["applied", "applied"])
        self.assertEqual(events[1]["detail"]["original_slot_sha256"], events[0]["detail"]["replacement_slot_sha256"])
        values = ops.floats(result[1][self.slot].tobytes())
        self.assertLessEqual(max(abs(v) for v in values), 0.75)
        self.assertEqual([c[1] for c in session.consumed], ["vco_1.post_vca", "vco_1.post_vca"])

    def test_saturation_targets_the_declared_vco_2_occurrence(self):
        _, capture, session, result = self.run_attempt(fake_schedule("signal:clip.saturation_ceiling", self.slot))
        self.assertEqual(session.events[0]["detail"]["occurrence"], 1)
        self.assertEqual(result[1][self.slot].tobytes(), self.plain_result[1][self.slot].tobytes())
        self.assertNotEqual(result[2][self.slot].tobytes(), self.plain_result[2][self.slot].tobytes())
        self.assertEqual(capture.snapshots["vca"], self.plain_capture.snapshots["vca"])

    def test_mode_substitute_binds_the_vca_invocation_inputs(self):
        _, _, session, result = self.run_attempt(fake_schedule("signal:osc.mode_substitute", self.slot))
        (event,) = session.events
        self.assertEqual(event["status"], "applied")
        raw = self.plain_result[0][self.slot].tobytes()
        self.assertEqual(event["detail"]["input_slot_sha256"][0], sha(raw))
        self.assertEqual(event["detail"]["inputs"], ["vco_1.raw", "control_upsample.vco_1_amp"])
        for r, out in zip(ops.floats(raw), ops.floats(result[1][self.slot].tobytes())):
            self.assertEqual(out > 0, r >= 0)

    def test_parameter_swaps_record_conversion_and_restore(self):
        voice = FakeVoice()
        before = {key: p.data.tobytes() for key, p in voice.parameters.items()}
        _, capture, session, result = self.run_attempt(fake_schedule("signal:osc.tuning_shift", self.slot), voice)
        (event,) = session.events
        self.assertEqual(event["status"], "applied")
        self.assertIn("semitones", event["detail"]["conversion"]["conversion"])
        self.assertEqual(event["detail"]["inventory_range"], sr._inventory_range(INVENTORY, "vco_1.tuning"))
        self.assertNotEqual(capture.snapshots["vca"][0], self.plain_capture.snapshots["vca"][0])
        self.assertEqual(capture.snapshots["vca"][1], self.plain_capture.snapshots["vca"][1])
        self.assertEqual({key: p.data.tobytes() for key, p in voice.parameters.items()}, before)
        _, _, session, _ = self.run_attempt(fake_schedule("signal:osc.phase_offset", self.slot), voice)
        self.assertTrue(session.events[0]["detail"]["conversion"]["wrapped"])
        _, _, session, _ = self.run_attempt(fake_schedule("signal:osc.shape_scale", self.slot), voice)
        self.assertEqual(session.events[0]["detail"]["conversion"]["replacement_normalized"], 0.5)
        self.assertEqual({key: p.data.tobytes() for key, p in voice.parameters.items()}, before)

    def test_applicator_error_records_errored_restores_and_reruns_clean(self):
        voice = FakeVoice()
        before = {key: p.data.tobytes() for key, p in voice.parameters.items()}
        schedule = fake_schedule("signal:gain.polarity", self.slot)
        session = worker.SignalMutationSession(schedule, voice, FakeCapture(voice, self.slot), FakeTorch)

        def broken(row, data):
            row.copy_(FakeTensor(array.array("f", [0.0] * AUDIO)))
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
        self.assertEqual((voice.vca.hooks, voice.keyboard.hooks), ([], []))
        self.assertEqual({key: p.data.tobytes() for key, p in voice.parameters.items()}, before)
        rerun = FakeCapture(voice, self.slot)
        with rerun:
            voice.render()
        self.assertEqual(rerun.snapshots, self.plain_capture.snapshots)

    def test_parameter_error_after_swap_restores_state(self):
        voice = FakeVoice()
        before = {key: p.data.tobytes() for key, p in voice.parameters.items()}
        schedule = fake_schedule("signal:osc.tuning_shift", self.slot)
        schedule["module_mutations"] = fake_schedule("signal:gain.db", self.slot)["module_mutations"]
        schedule["module_mutations"][0]["declared_position"] = 1
        session = worker.SignalMutationSession(schedule, voice, FakeCapture(voice, self.slot), FakeTorch)
        session._write_row = lambda row, data: (_ for _ in ()).throw(RuntimeError("late failure"))
        session.attach()
        with self.assertRaisesRegex(RuntimeError, "late failure"):
            try:
                voice.render()
            finally:
                session.detach(*sys.exc_info())
        self.assertEqual([e["status"] for e in session.events], ["applied", "errored"])
        self.assertEqual({key: p.data.tobytes() for key, p in voice.parameters.items()}, before)

    def test_partial_setup_failure_removes_installed_hooks(self):
        voice = FakeVoice(fail_module="vca")
        capture = FakeCapture(voice, self.slot)
        schedule = fake_schedule("signal:osc.tuning_shift", self.slot)
        schedule["module_mutations"] = fake_schedule("signal:gain.db", self.slot)["module_mutations"]
        schedule["module_mutations"][0]["declared_position"] = 1
        session = worker.SignalMutationSession(schedule, voice, capture, FakeTorch)
        with self.assertRaisesRegex(RuntimeError, "partial setup failure"):
            session.attach()
        self.assertEqual((voice.keyboard.hooks, voice.vca.hooks), ([], []))
        self.assertFalse(session._attached)
        self.assertFalse(capture.entered)

    def test_sham_and_refusals_write_nothing(self):
        _, capture, session, _ = self.run_attempt(fake_schedule("sham", self.slot))
        self.assertEqual([(e["status"], e["sham"]) for e in session.events], [("ineffective", True)])
        self.assertEqual(capture.snapshots, self.plain_capture.snapshots)
        self.assertEqual(session.consumed, [])
        voice = FakeVoice()
        voice.parameters[("vco_1", "tuning")].data.values[self.slot] = 1.0
        _, _, session, _ = self.run_attempt(fake_schedule("signal:osc.tuning_shift", self.slot), voice)
        self.assertEqual(session.events[0]["status"], "refused")
        self.assertIn("outside the declared parameter range", session.events[0]["detail"]["reason"])
        self.assertEqual(voice.parameters[("vco_1", "tuning")].data.values[self.slot], 1.0)
        schedule = fake_schedule("signal:gain.db", self.slot)
        schedule["module_mutations"][0]["applicator"] = "norm.always_on"
        _, capture, session, result = self.run_attempt(schedule)
        self.assertEqual(session.events[0]["status"], "refused")
        self.assertIn("AQ-1", session.events[0]["detail"]["reason"])
        self.assertEqual(session.consumed, [])
        self.assertEqual(result[1][self.slot].tobytes(), self.plain_result[1][self.slot].tobytes())

    def test_unsupported_schedules_refuse_before_any_render(self):
        class Harness:
            case = case("global-0")

        schedules = schedules_for("global-0")
        schedules["signal:gain.db"]["module_mutations"][0].update(
            operator="norm.always_on", applicator="norm.always_on")
        with self.assertRaisesRegex(ValueError, "refused before rendering"):
            worker.run_case_process(Harness(), schedules, None, DOCUMENT, PARAMETER_NAMES, MANIFEST_SHA256)
        reordered = schedules_for("global-0")
        reordered = dict(reversed(list(reordered.items())))
        with self.assertRaisesRegex(ValueError, "controls must precede"):
            worker.run_case_process(Harness(), reordered, None, DOCUMENT, PARAMETER_NAMES, MANIFEST_SHA256)


if __name__ == "__main__":
    unittest.main()
