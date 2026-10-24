"""Signal-family actual-Voice runtime adapter, host side (#336; Part of #300).

Stdlib-only. Binds the signal slice of the committed DR-0012 campaign
manifest (``spec/reference/runtime-family-campaign-v1.json`` revision 1,
frozen inventory digest ``b632d7ee...``) to the declared writable ``voice.*``
seams of ``spec/reference/mutation-seams-v1.json``:

* registers explicitly versioned runtime operators (``<fixture id>@voice-v1``)
  through the landed ``mutations.register_operator`` API; the #33 fixture
  operators of ``mutations_signal`` keep their definitions and no bridge
  operator is relabeled as a family operator;
* requires the adapter dispositions to equal the manifest's signal
  dispositions exactly (9 runtime cells, 2 second detectors, 1 composition,
  1 refusal, 7 deferred entries), so a manifest revision forces an explicit
  adapter update;
* builds ``mp1-`` plans bound to the manifest revision/digest and the resolved
  development case, and resolves them by *extending*
  ``mutation_runtime.resolve_voice_plan`` (registry association, slot bounds,
  graph causality) with the family fields and a campaign binding block for
  ``env/release-era/mutation_worker_signal.py``;
* executes the normalization-decision replacement as the expected plan-time
  refusal (``mutations.validate_plan``), never at a voice seam, and refuses
  the four deferred ``norm.*`` entries and the three revision-2 #297
  additions (DR-0015) explicitly;
* validates one worker process report offline: attempt inventory, plan and
  schedule re-derivation, event order/status, frame/identity preflight before
  any metric, paired exactness detectors against the same process's plain
  attempt, the admissibility-gated second detectors, controls, cleanup,
  passive-original versus consumed-replacement evidence and declared
  invariance; ``campaign_outcomes`` feeds the manifest's own accounting rule.

Scope limits, stated so they cannot be mistaken for results: no Voice is
rendered here; unit fixtures exercise adapter mechanics only and never
qualify actual-Voice coverage; reliability across fresh processes, host
admission and publication belong to the campaign (#337). Normalization
decision-site faults stay deferred (DR-0012 D3, AQ-1).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import mutation_runtime
from . import mutation_family_runtime as family
from . import mutations
from . import paired_metrics
from . import signal_voice_ops as ops
from .artifacts import canonical_bytes
from .mutations import MutationError

REQUEST_DOMAIN = b"torchsynth-signal-runtime-request-v1\n"
FAMILY = ops.FAMILY

# Exact adapter dispositions for the signal slice of manifest revision 2.
ADAPTER_DISPOSITIONS: Dict[str, str] = {
    "signal:gain.db": "runtime_cell",
    "signal:gain.polarity": "runtime_cell",
    "signal:gain.dc_offset": "runtime_cell",
    "signal:clip.round_step": "runtime_cell",
    "signal:clip.saturation_ceiling": "runtime_cell",
    "signal:osc.mode_substitute": "runtime_cell",
    "signal:osc.shape_scale": "runtime_cell",
    "signal:osc.tuning_shift": "runtime_cell",
    "signal:osc.phase_offset": "runtime_cell",
    "signal:osc.tuning_shift (property)": "second_detector",
    "signal:osc.phase_offset (property)": "second_detector",
    "signal:norm.always_on": "deferred_unmeasured",
    "signal:norm.off": "deferred_unmeasured",
    "signal:norm.wrong_peak": "deferred_unmeasured",
    "signal:norm.wrong_reciprocal": "deferred_unmeasured",
    "signal:normalization-decision-replacement": "refusal",
    "signal:composed-round-then-saturate": "composition",
    "signal:gain.db (minus 1 dB)": "deferred_unmeasured",
    "signal:clip.truncate_step": "deferred_unmeasured",
    "signal:clip.truncate_step vs clip.round_step (directed distinction)": "deferred_unmeasured",
}

# Deferred by manifest revision 2 (DR-0015): #297 publication faults with no
# runtime cell; they are not normalization-decision entries.
REVISION_2_DEFERRED = frozenset({
    "signal:gain.db (minus 1 dB)",
    "signal:clip.truncate_step",
    "signal:clip.truncate_step vs clip.round_step (directed distinction)",
})

# Explicit realisation of every executable entry: (runtime operator, target
# configuration). Each row is cross-checked against the manifest's runtime
# seam, target, configuration, operators and magnitudes.
REALISATIONS: Dict[str, List[Tuple[str, Dict[str, str]]]] = {
    "signal:gain.db": [(ops.GAIN_DB, {"trace": "vco_1.post_vca"})],
    "signal:gain.polarity": [(ops.POLARITY, {"trace": "vco_1.post_vca"})],
    "signal:gain.dc_offset": [(ops.DC_OFFSET, {"trace": "vco_1.post_vca"})],
    "signal:clip.round_step": [(ops.ROUND_STEP, {"trace": "vco_1.post_vca"})],
    "signal:clip.saturation_ceiling": [(ops.SATURATION, {"trace": "vco_2.post_vca"})],
    "signal:osc.mode_substitute": [(ops.MODE_SUBSTITUTE, {"trace": "vco_1.post_vca"})],
    "signal:osc.shape_scale": [(ops.SHAPE_SCALE, {"parameter": "vco_2.shape"})],
    "signal:osc.tuning_shift": [(ops.TUNING_SHIFT, {"parameter": "vco_1.tuning"})],
    "signal:osc.phase_offset": [(ops.PHASE_OFFSET, {"parameter": "vco_1.initial_phase"})],
    "signal:composed-round-then-saturate": [
        (ops.ROUND_STEP, {"trace": "vco_1.post_vca"}),
        (ops.SATURATION, {"trace": "vco_1.post_vca"}),
    ],
}

# The plan-time refusal: the fixture operator at the non-writable decision seam.
REFUSAL_ENTRY = "signal:normalization-decision-replacement"
REFUSAL_PHRASE = "producer handoff required"

CONTROL_ATTEMPTS = ("plain", "empty", "sham")
SHAM_TARGET = "vco_1.post_vca"

# Second-detector admissibility (det-signal-02/03). The periodic estimator's
# ``fundamental_in_band`` is an external provenance assertion; the pinned
# Voice exposes no producer assertion that vco_1's fundamental (keyboard
# midi_f0 + tuning + modulation) stays in band, so the adapter supplies none
# and the estimator's own gate decides. ``REFERENCE_HZ`` only labels the cents
# axis: the paired deviation subtracts the same reference on both attempts.
FUNDAMENTAL_IN_BAND_PROVENANCE = False
REFERENCE_HZ = 440.0
PERIODIC_TRACE = "vco_1.post_vca"
PERIODIC_PROVENANCE_TRACES = {
    "pitch": "control_upsample.vco_1_pitch",
    "amplitude": "control_upsample.vco_1_amp",
}
NUMPY_UNAVAILABLE = "numpy_unavailable_install_metrics_extra"

# Trace-level dataflow of the pinned Voice.output (torchsynth/synth.py at
# 2b0964d4): producer trace -> consumer traces. Used only to derive the
# declared blast radius; every other trace must stay invariant.
_ROUTES = [
    "mod_matrix.vco_1_pitch",
    "mod_matrix.vco_1_amp",
    "mod_matrix.vco_2_pitch",
    "mod_matrix.vco_2_amp",
    "mod_matrix.noise_amp",
]
DATAFLOW: Dict[str, List[str]] = {
    "keyboard.midi_f0": ["vco_1.raw", "vco_2.raw"],
    "keyboard.duration": [
        "lfo_1_rate_adsr.output",
        "lfo_2_rate_adsr.output",
        "lfo_1_amp_adsr.output",
        "lfo_2_amp_adsr.output",
        "adsr_1.output",
        "adsr_2.output",
    ],
    "lfo_1_rate_adsr.output": ["lfo_1.raw"],
    "lfo_2_rate_adsr.output": ["lfo_2.raw"],
    "lfo_1.raw": ["lfo_1.post_control_vca"],
    "lfo_1_amp_adsr.output": ["lfo_1.post_control_vca"],
    "lfo_2.raw": ["lfo_2.post_control_vca"],
    "lfo_2_amp_adsr.output": ["lfo_2.post_control_vca"],
    "adsr_1.output": _ROUTES,
    "adsr_2.output": _ROUTES,
    "lfo_1.post_control_vca": _ROUTES,
    "lfo_2.post_control_vca": _ROUTES,
    "mod_matrix.vco_1_pitch": ["control_upsample.vco_1_pitch"],
    "mod_matrix.vco_1_amp": ["control_upsample.vco_1_amp"],
    "mod_matrix.vco_2_pitch": ["control_upsample.vco_2_pitch"],
    "mod_matrix.vco_2_amp": ["control_upsample.vco_2_amp"],
    "mod_matrix.noise_amp": ["control_upsample.noise_amp"],
    "control_upsample.vco_1_pitch": ["vco_1.raw"],
    "vco_1.raw": ["vco_1.post_vca"],
    "control_upsample.vco_1_amp": ["vco_1.post_vca"],
    "control_upsample.vco_2_pitch": ["vco_2.raw"],
    "vco_2.raw": ["vco_2.post_vca"],
    "control_upsample.vco_2_amp": ["vco_2.post_vca"],
    "noise.raw": ["noise.post_vca"],
    "control_upsample.noise_amp": ["noise.post_vca"],
    "vco_1.post_vca": ["mixer.pre_normalization"],
    "vco_2.post_vca": ["mixer.pre_normalization"],
    "noise.post_vca": ["mixer.pre_normalization"],
    "mixer.pre_normalization": ["mixer.peak", "mixer.output"],
    "mixer.peak": ["mixer.gain"],
    "mixer.gain": ["mixer.output"],
    "mixer.output": [],
}
# Source parameter -> the first registry trace that consumes it.
PARAMETER_ROOTS = {
    "vco_1.tuning": "vco_1.raw",
    "vco_1.initial_phase": "vco_1.raw",
    "vco_2.shape": "vco_2.raw",
}


_require = family._require


sha256 = family.sha256


# --------------------------------------------------------------------------
# Manifest binding and inventory
# --------------------------------------------------------------------------


def load_manifest(root: Path) -> Tuple[Dict[str, Any], str]:
    """Load the committed manifest; fail closed on revision/digest drift."""
    return family.load_manifest(root, ops)


def manifest_binding(manifest: Dict[str, Any], manifest_sha256: str) -> Dict[str, Any]:
    return family.manifest_binding(manifest, manifest_sha256, ops)


def signal_entries(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [entry for entry in manifest["inventory"] if entry["family"] == FAMILY]


def entry_by_id(manifest: Dict[str, Any], entry_id: str) -> Dict[str, Any]:
    for entry in signal_entries(manifest):
        if entry["id"] == entry_id:
            return entry
    raise MutationError("not a signal entry of the bound manifest: " + str(entry_id))


def _runtime_magnitude(operator_id: str, magnitude: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    spec = ops.RUNTIME_OPERATORS[operator_id]["magnitude"]
    if spec["type"] == "null":
        _require(magnitude["value"] is None and magnitude["unit"] == "none",
                 "manifest magnitude must be null for " + operator_id)
        return None
    _require(magnitude["unit"] == spec["unit"], "manifest magnitude unit differs for " + operator_id)
    return {"value": magnitude["value"], "unit": magnitude["unit"]}


def _target_of(target: Dict[str, str]) -> str:
    return target["parameter"] if "parameter" in target else target["trace"]


def second_detectors_of(manifest: Dict[str, Any], entry_id: str) -> List[Dict[str, Any]]:
    return [
        entry for entry in signal_entries(manifest)
        if entry["disposition"] == "second_detector" and entry.get("shares_cell_with") == entry_id
    ]


def require_inventory_match(manifest: Dict[str, Any]) -> Dict[str, int]:
    """Adapter dispositions and realisations equal the manifest exactly."""
    entries = signal_entries(manifest)
    observed = {entry["id"]: entry["disposition"] for entry in entries}
    _require(len(observed) == len(entries), "duplicate signal entry ids in the manifest")
    _require(
        observed == ADAPTER_DISPOSITIONS,
        "signal adapter dispositions differ from the bound manifest; a manifest "
        "revision requires an explicit adapter update",
    )
    minimum = manifest["reliability"]["min_independent_executions_per_cell"]
    for entry in entries:
        entry_id = entry["id"]
        disposition = entry["disposition"]
        if disposition in ("runtime_cell", "composition"):
            realised = REALISATIONS[entry_id]
            _require(
                [ops.RUNTIME_OPERATORS[op]["realises"] for op, _ in realised] == entry["operators"],
                "realisation operators differ from the manifest: " + entry_id,
            )
            _require(
                [item["operator"] for item in entry["magnitude"]] == entry["operators"],
                "manifest magnitude order differs from operators: " + entry_id,
            )
            for (operator_id, _), magnitude in zip(realised, entry["magnitude"]):
                _runtime_magnitude(operator_id, magnitude)
            seams = {ops.RUNTIME_OPERATORS[op]["seam"] for op, _ in realised}
            _require(seams == {entry["runtime_seam"]}, "runtime seam differs: " + entry_id)
            targets = sorted({_target_of(target) for _, target in realised})
            _require(targets == [entry["runtime_target"]], "runtime target differs: " + entry_id)
            configuration = dict(entry["configuration"])
            _require(configuration.pop("slot", None) == "$case.batch_slot",
                     "configuration slot is not the case batch slot: " + entry_id)
            _require(configuration in [dict(target) for _, target in realised],
                     "configuration target differs: " + entry_id)
            _require(entry["expected_event"] == "applied", "expected event differs: " + entry_id)
            _require(entry["runtime_truth"]["rule"] == "plain_attempt_paired_reference",
                     "runtime truth rule differs: " + entry_id)
            _require(entry["repeats_required"] >= minimum, "repeats below the ratified minimum: " + entry_id)
            _require(entry["gated_detectors"] == [], "unexpected gated detector: " + entry_id)
            allowed = blast_radius(realised)
            for trace in entry["runtime_truth"]["compared_traces"]:
                _require(trace in allowed, "compared trace outside the declared blast radius: " + trace)
                _require(trace not in seam_traces(realised),
                         "compared trace is the replaced seam capture (never a detector operand): " + trace)
            _require(
                entry["counts_toward_runtime_total"] is (disposition == "runtime_cell"),
                "runtime-total accounting differs: " + entry_id,
            )
        elif disposition == "second_detector":
            shared = entry["shares_cell_with"]
            _require(ADAPTER_DISPOSITIONS.get(shared) == "runtime_cell",
                     "second detector does not share a runtime cell: " + entry_id)
            cell = entry_by_id(manifest, shared)
            _require(entry["operators"] == cell["operators"] and entry["magnitude"] == cell["magnitude"]
                     and entry["configuration"] == cell["configuration"],
                     "second detector differs from its shared cell: " + entry_id)
            _require(entry["development_cases"] == [] and entry["repeats_required"] == 0,
                     "a second detector has no attempt of its own: " + entry_id)
            admissibility = entry["runtime_truth"]["admissibility"]
            _require(admissibility["required"] is True
                     and admissibility["on_inadmissible"] == "refused_inadmissible_not_counted",
                     "second detector admissibility differs: " + entry_id)
            _require(entry["runtime_truth"]["compared_traces"] == [PERIODIC_TRACE],
                     "second detector trace differs: " + entry_id)
            _require(entry["counts_toward_runtime_total"] is False, "a second detector never counts: " + entry_id)
        elif disposition == "refusal":
            _require(entry_id == REFUSAL_ENTRY, "unexpected refusal entry: " + entry_id)
            _require(entry["runtime_seam"] == ops.NORMALIZATION_DECISION
                     and entry["operators"] == ["norm.always_on"],
                     "refusal seam/operator differs: " + entry_id)
            _require(entry["expected_event"] == "refused"
                     and REFUSAL_PHRASE in entry["expected_failure"].lower(),
                     "refusal expectation differs: " + entry_id)
            _require(entry["runtime_truth"]["rule"] == "plan_time_refusal", "refusal rule differs: " + entry_id)
            _require(entry["counts_toward_runtime_total"] is False, "a refusal never counts: " + entry_id)
        else:
            if entry_id in REVISION_2_DEFERRED:
                _require(entry["obligation_open"] is True and entry["development_cases"] == [],
                         "deferred entry must stay open with no cases: " + entry_id)
                continue
            _require(all(op.startswith("norm.") for op in entry["operators"]),
                     "deferred entry is not a normalization operator: " + entry_id)
            _require(entry["obligation_open"] is True and entry["development_cases"] == []
                     and entry["runtime_seam"] == ops.NORMALIZATION_DECISION,
                     "deferred entry must stay open with no cases: " + entry_id)
    counts: Dict[str, int] = {}
    for disposition in ADAPTER_DISPOSITIONS.values():
        counts[disposition] = counts.get(disposition, 0) + 1
    return counts


def case_record(manifest: Dict[str, Any], case_id: str) -> Dict[str, Any]:
    return family.case_record(manifest, case_id, ops)


def request_identity(manifest: Dict[str, Any], case: Dict[str, Any]) -> str:
    """Domain-separated identity of the resolved development request."""
    core = {
        "campaign": ops.MANIFEST_IDENTITY,
        "revision": ops.MANIFEST_REVISION,
        "frozen_inventory_sha256": ops.MANIFEST_FROZEN_SHA256,
        "family": FAMILY,
        "realisation": ops.REALISATION,
        "case": case,
        "profile": manifest["target"]["runtime_profile"],
    }
    return "srq1-" + sha256(REQUEST_DOMAIN + canonical_bytes(core))


# --------------------------------------------------------------------------
# Operator registration and plans
# --------------------------------------------------------------------------


def runtime_definitions() -> Dict[str, Dict[str, Any]]:
    definitions: Dict[str, Dict[str, Any]] = {}
    slot = {"type": "integer", "minimum": 0}
    for operator_id, spec in ops.RUNTIME_OPERATORS.items():
        if spec["seam"] == ops.PARAMETER_VALUE:
            configuration: Dict[str, Any] = {"parameter": list(spec["parameters"]), "slot": slot}
        else:
            configuration = {"trace": list(spec["traces"]), "slot": slot}
        composes = [right for left, right in ops.COMPOSITIONS if left == operator_id]
        composes += [left for left, right in ops.COMPOSITIONS if right == operator_id]
        definitions[operator_id] = {
            "version": 1,
            "seam": spec["seam"],
            "sham": operator_id == ops.SHAM,
            "magnitude": dict(spec["magnitude"]),
            "configuration": configuration,
            "composes_with": composes,
            "summary": (
                "signal-family voice-runtime sham: traverse the family dispatch and return the original slot"
                if operator_id == ops.SHAM
                else "signal-family voice-runtime realisation of "
                + spec["realises"]
                + " at "
                + spec["seam"]
                + " (signal_voice_ops, "
                + ops.REALISATION
                + "); bound to runtime-family-campaign-v1 revision 1"
            ),
        }
    return definitions


def register_runtime_operators() -> None:
    """Explicit trusted-code registration; identical re-registration is a no-op."""
    family.register_runtime_operators(runtime_definitions())


def _instance(instance_id: str, operator_id: str, magnitude: Optional[Dict[str, Any]],
              configuration: Dict[str, Any]) -> Dict[str, Any]:
    return family.instance(instance_id, operator_id, magnitude, configuration, ops)


def _source_binding(manifest: Dict[str, Any], case: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "case_id": case["id"],
        "partition": case["partition"],
        "fixture_identity": request_identity(manifest, case),
    }


def cell_plan(manifest: Dict[str, Any], entry_id: str, case_id: str,
              catalog: Dict[str, Any]) -> Dict[str, Any]:
    """The ``mp1-`` plan of one executable entry on one development case.

    Refusal, second-detector and deferred entries refuse explicitly here; they
    are never silently dropped and never planned at a voice seam.
    """
    require_inventory_match(manifest)
    entry = entry_by_id(manifest, entry_id)
    disposition = entry["disposition"]
    if disposition == "refusal":
        raise MutationError(
            "refused: " + entry_id + " names the non-writable voice.normalization_decision seam; "
            "it executes host-side as the expected plan-time refusal (producer handoff required)"
        )
    if disposition == "deferred_unmeasured":
        if entry_id in REVISION_2_DEFERRED:
            raise MutationError(
                "refused: " + entry_id + " is deferred_unmeasured (DR-0015); no runtime cell "
                "is preregistered for this #297 addition"
            )
        raise MutationError(
            "refused: " + entry_id + " is deferred_unmeasured (DR-0012 D3, AQ-1); "
            + ops.NORMALIZATION_REFUSAL
        )
    if disposition == "second_detector":
        raise MutationError(
            "refused: " + entry_id + " is a second detector of " + entry["shares_cell_with"]
            + "; it has no attempt of its own"
        )
    _require(case_id in entry["development_cases"], "case not declared for entry: " + str(case_id))
    case = case_record(manifest, case_id)
    register_runtime_operators()
    instances = []
    fault = entry_id.split(":", 1)[1]
    for index, ((operator_id, target), magnitude) in enumerate(
        zip(REALISATIONS[entry_id], entry["magnitude"])
    ):
        configuration = dict(target, slot=case["batch_slot"])
        instances.append(
            _instance("smr-" + fault + "-" + str(index), operator_id,
                      _runtime_magnitude(operator_id, magnitude), configuration)
        )
    return mutations.make_plan(_source_binding(manifest, case), instances, catalog)


def control_plan(manifest: Dict[str, Any], case_id: str, kind: str,
                 catalog: Dict[str, Any]) -> Dict[str, Any]:
    _require(kind in ("empty", "sham"), "unknown control plan: " + str(kind))
    case = case_record(manifest, case_id)
    register_runtime_operators()
    instances = []
    if kind == "sham":
        instances.append(_instance("smr-sham", ops.SHAM, None,
                                   {"trace": SHAM_TARGET, "slot": case["batch_slot"]}))
    return mutations.make_plan(_source_binding(manifest, case), instances, catalog)


def expected_attempts(manifest: Dict[str, Any], case_id: str) -> List[str]:
    """Per (case, repeat) process order: controls, entries in manifest order, clean rerun."""
    case_record(manifest, case_id)
    names = list(CONTROL_ATTEMPTS)
    for entry in signal_entries(manifest):
        if entry["disposition"] in ("runtime_cell", "composition") and case_id in entry["development_cases"]:
            names.append(entry["id"])
    names.append("clean_rerun")
    return names


# --------------------------------------------------------------------------
# Schedule resolution (extends mutation_runtime.resolve_voice_plan)
# --------------------------------------------------------------------------


def _registry_trace(document: Dict[str, Any], name: str) -> Dict[str, Any]:
    return family.registry_trace(document, name)


def _inventory_range(inventory: Dict[str, Any], name: str) -> Dict[str, Any]:
    return family.inventory_range(inventory, name)


def resolve_schedule(plan: Dict[str, Any], manifest: Dict[str, Any], manifest_sha256: str,
                     attempt: str, case_id: str, repeat: int, document: Dict[str, Any],
                     inventory: Dict[str, Any], catalog: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a plan against its entry/case binding and resolve the schedule.

    The landed bridge resolver (``mutation_runtime.resolve_voice_plan``) runs
    first and keeps its fail-closed checks (registry validity, writable
    single-output hook association, slot bounds, graph causality); this
    function then refuses any operator outside the signal allowlist and adds
    the family fields the signal worker dispatches on.
    """
    _require(type(repeat) is int and not isinstance(repeat, bool) and repeat >= 1,
             "repeat must be a positive integer")
    for instance in plan.get("mutations", []) if type(plan) is dict else []:
        reason = ops.refused_operator(instance.get("operator") if type(instance) is dict else None)
        _require(reason is None, "refused before rendering: " + str(reason))
    if attempt in ("empty", "sham"):
        expected = control_plan(manifest, case_id, attempt, catalog)
        entry = None
    else:
        expected = cell_plan(manifest, attempt, case_id, catalog)
        entry = entry_by_id(manifest, attempt)
    mutations.validate_plan(plan, catalog)
    _require(plan == expected,
             "plan does not match the manifest realisation for " + attempt + " on " + case_id)
    case = case_record(manifest, case_id)
    parameter_names = [parameter["name"] for parameter in inventory["parameters"]]
    base = mutation_runtime.resolve_voice_plan(plan, document, parameter_names, ops.BATCH_SIZE)
    instances = {instance["instance_id"]: instance for instance in plan["mutations"]}
    module_mutations: List[Dict[str, Any]] = []
    parameter_swaps: List[Dict[str, Any]] = []
    for kind, out in (("module_mutations", module_mutations), ("parameter_swaps", parameter_swaps)):
        for resolved in base[kind]:
            instance = instances[resolved["instance_id"]]
            operator_id = instance["operator"]
            spec = ops.RUNTIME_OPERATORS[operator_id]
            configuration = instance["configuration"]
            _require(configuration["slot"] == case["batch_slot"], "slot is not the case batch slot")
            extended = dict(
                resolved,
                operator=operator_id,
                applicator=operator_id,
                realises=spec["realises"],
                magnitude=instance["magnitude"],
                ratio=None,
            )
            if kind == "parameter_swaps":
                name = configuration["parameter"]
                _require(name in spec["parameters"], "parameter not declared for operator: " + name)
                extended["range"] = _inventory_range(inventory, name)
            else:
                trace = configuration["trace"]
                _require(trace in spec["traces"], "trace not declared for operator: " + trace)
                _require(_registry_trace(document, trace)["shape"] == [spec["frame"]],
                         "registry frame differs from the realisation frame: " + trace)
                _require(resolved["raise"] is False, "signal entries never raise")
                extended["frame"] = spec["frame"]
                if operator_id == ops.MODE_SUBSTITUTE:
                    for name in ops.MODE_SUBSTITUTE_INPUTS:
                        _require(trace in DATAFLOW[name], "mode substitute input is not a producer: " + name)
                    extended["inputs"] = list(ops.MODE_SUBSTITUTE_INPUTS)
            out.append(extended)
    return dict(
        base,
        family=ops.SCHEDULE_FAMILY,
        module_mutations=module_mutations,
        parameter_swaps=parameter_swaps,
        campaign=dict(
            manifest_binding(manifest, manifest_sha256),
            attempt=attempt,
            entry_id=None if entry is None else entry["id"],
            disposition="control" if entry is None else entry["disposition"],
            case=case,
            repeat=repeat,
        ),
    )


def process_schedules(manifest: Dict[str, Any], manifest_sha256: str, case_id: str, repeat: int,
                      document: Dict[str, Any], inventory: Dict[str, Any],
                      catalog: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Every schedule one fresh (case, repeat) worker process executes, in order."""
    schedules: Dict[str, Dict[str, Any]] = {}
    for attempt in expected_attempts(manifest, case_id):
        if attempt in ("plain", "clean_rerun"):
            continue
        plan = _plan_for(manifest, attempt, case_id, catalog)
        schedules[attempt] = resolve_schedule(plan, manifest, manifest_sha256, attempt, case_id,
                                              repeat, document, inventory, catalog)
    return schedules


def _plan_for(manifest: Dict[str, Any], attempt: str, case_id: str, catalog: Dict[str, Any]) -> Dict[str, Any]:
    if attempt in ("empty", "sham"):
        return control_plan(manifest, case_id, attempt, catalog)
    return cell_plan(manifest, attempt, case_id, catalog)


# --------------------------------------------------------------------------
# Refusal, blast radius and detectors
# --------------------------------------------------------------------------


def normalization_refusal(manifest: Dict[str, Any], catalog: Dict[str, Any], case_id: str) -> Dict[str, Any]:
    """Execute the expected plan-time refusal at ``voice.normalization_decision``.

    The fixture operator ``norm.always_on`` (registered by ``mutations_signal``)
    is declared at the non-writable decision seam; ``mutations.validate_plan``
    must refuse naming the producer handoff. Nothing renders, nothing is
    wrapped, and the refusal never counts toward runtime kills.
    """
    from . import mutations_signal

    entry = entry_by_id(manifest, REFUSAL_ENTRY)
    _require(case_id in entry["development_cases"], "case not declared for the refusal: " + str(case_id))
    case = case_record(manifest, case_id)
    mutations_signal.register_family()
    instance = {
        "instance_id": "smr-normalization-decision-replacement-0",
        "operator": entry["operators"][0],
        "operator_version": mutations.OPERATORS[entry["operators"][0]]["version"],
        "seam": entry["runtime_seam"],
        "magnitude": None,
        "configuration": {"trace": entry["configuration"]["trace"], "slot": case["batch_slot"]},
        "sham": False,
    }
    refusal = None
    try:
        mutations.make_plan(_source_binding(manifest, case), [instance], catalog)
    except MutationError as error:
        refusal = str(error)
    _require(refusal is not None, "normalization decision replacement was not refused at plan time")
    _require(REFUSAL_PHRASE in refusal.lower(), "plan-time refusal does not name the producer handoff")
    return {
        "entry_id": REFUSAL_ENTRY,
        "case": case_id,
        "operator": entry["operators"][0],
        "seam": entry["runtime_seam"],
        "status": "refused",
        "detector_ids": list(entry["detector_ids"]),
        "detail": {"refusal": refusal, "rendered": False},
        "counts_toward_runtime_total": False,
        "establishes_decision_site_execution": False,
    }


def downstream(trace: str) -> List[str]:
    return family.downstream(trace, DATAFLOW)


def blast_radius(realised: List[Tuple[str, Dict[str, str]]]) -> List[str]:
    """Traces a realisation may change; a replaced seam itself keeps its original."""
    allowed: List[str] = []
    for _, target in realised:
        if "parameter" in target:
            root = PARAMETER_ROOTS[target["parameter"]]
            names = [root] + downstream(root)
        else:
            names = downstream(target["trace"])
        allowed.extend(name for name in names if name not in allowed)
    return sorted(allowed)


def seam_traces(realised: List[Tuple[str, Dict[str, str]]]) -> List[str]:
    names: List[str] = []
    for _, target in realised:
        if "trace" in target and target["trace"] not in names:
            names.append(target["trace"])
    return names


def trace_preflight(document: Dict[str, Any], name: str, data: bytes, inventory_digest: str) -> List[float]:
    """Frame and identity preflight; runs before any metric and refuses on mismatch."""
    return family.trace_preflight(document, name, data, inventory_digest, ops)


def paired_exactness(reference: List[float], candidate: List[float], rate_hz: float, trace: str) -> Dict[str, Any]:
    """Paired exactness contract row (det-signal-01/09); no preparation, no alignment."""
    comparison = paired_metrics.compare_paired(
        reference, candidate, reference_rate_hz=rate_hz, candidate_rate_hz=rate_hz,
        unit="1", window_samples=max(1, len(reference)),
    )
    metrics = comparison["metrics"]
    exact = metrics["exact_equal"]["value"]
    return {
        "trace": trace,
        "pair_status": comparison["pair_status"],
        "time_origin": comparison["settings"]["time_origin"],
        "framing_match": metrics["framing_match"]["value"],
        "exact_equal": exact,
        "max_abs_error": metrics["max_abs_error"]["value"],
        "mean_error": metrics["mean_error"]["value"],
        "first_divergence_index": metrics["first_divergence_index"]["value"],
        "verdict": "PASS" if comparison["pair_status"] == "measured" and exact == 1 else "FAIL",
    }


def _constant(samples: List[float]) -> bool:
    return all(value == samples[0] for value in samples)


def default_estimator(samples: List[float], *, envelope_shaped: bool) -> Dict[str, Any]:
    """The landed periodic estimator with the adapter's declared provenance."""
    from . import periodic_estimators as pe

    return pe.estimate_periodic(
        samples,
        sample_rate_hz=ops.AUDIO_RATE_HZ,
        unit="amplitude",
        family="oscillator",
        reference_hz=REFERENCE_HZ,
        preparation=pe.identity_metadata(len(samples), ops.AUDIO_RATE_HZ, "amplitude", time_origin=0.0),
        weights=(1, 0, 0, 0, 0),
        fundamental_in_band=FUNDAMENTAL_IN_BAND_PROVENANCE,
        envelope_shaped=envelope_shaped,
    )


def second_detector_gate(second: Dict[str, Any], attempts: Dict[str, Dict[str, List[float]]],
                         estimator: Callable[..., Dict[str, Any]]) -> Dict[str, Any]:
    """Admissibility gate of one second detector over the plain and faulted attempts.

    The estimator's own refusal on either attempt is recorded as
    ``refused_inadmissible_not_counted`` (never a kill; the shared cell's
    det-signal-01 verdict stands alone). An unavailable estimator backend is
    ``not_evaluated`` (the case stays open, DR-0012 D7). Scoring an admitted
    pair against the periodic analytic qualification limit is not implemented
    by this adapter, so an admitted pair is also ``not_evaluated``, never a
    detection and never a silent pass.
    """
    row: Dict[str, Any] = {
        "entry_id": second["id"],
        "detector_ids": list(second["detector_ids"]),
        "trace": PERIODIC_TRACE,
        "provenance": {"fundamental_in_band": FUNDAMENTAL_IN_BAND_PROVENANCE, "reference_hz": REFERENCE_HZ},
        "attempts": {},
        "counts_toward_runtime_total": False,
    }
    refusals = []
    for label in ("plain", "faulted"):
        lanes = attempts[label]
        envelope_shaped = not _constant(lanes[PERIODIC_PROVENANCE_TRACES["amplitude"]])
        varying_rate = not _constant(lanes[PERIODIC_PROVENANCE_TRACES["pitch"]])
        result = estimator(lanes[PERIODIC_TRACE], envelope_shaped=envelope_shaped or varying_rate)
        row["attempts"][label] = {
            "status": result.get("status"),
            "reason": result.get("reason"),
            "envelope_shaped": envelope_shaped,
            "varying_rate": varying_rate,
            "input_sha256": result.get("input_sha256"),
        }
        if result.get("status") == "refused" and result.get("reason") == NUMPY_UNAVAILABLE:
            row["outcome"] = "not_evaluated"
            row["reason"] = "periodic estimator backend unavailable (NumPy); the case stays open"
            return row
        if result.get("status") != "valid":
            refusals.append(label + ": " + str(result.get("reason")))
    if refusals:
        row["outcome"] = "refused_inadmissible_not_counted"
        row["reason"] = "; ".join(refusals)
    else:
        row["outcome"] = "not_evaluated"
        row["reason"] = ("admitted pair: scoring against the periodic analytic qualification limit is "
                         "not implemented by this adapter; the case stays open")
    return row


# --------------------------------------------------------------------------
# Process report validation
# --------------------------------------------------------------------------

_IDENTITY_KEYS = family.IDENTITY_KEYS


def _inventory_digests(record: Dict[str, Any]) -> Dict[str, str]:
    return family.inventory_digests(record)


def _require_identical(plain: Dict[str, Any], record: Dict[str, Any], label: str) -> None:
    family.require_identical(plain, record, label, _IDENTITY_KEYS)


def _check_module_event(event: Dict[str, Any], mutation: Dict[str, Any], record: Dict[str, Any],
                        plain: Dict[str, Any], consumed: Dict[Tuple[str, str], Dict[str, Any]],
                        read_bytes: Callable[[str], bytes], previous: Dict[str, str]) -> None:
    detail = event["detail"]
    digests = _inventory_digests(record)
    trace = mutation["trace"]
    _require(detail.get("operator") == mutation["operator"] and detail.get("trace") == trace
             and detail.get("slot") == mutation["slot"],
             "event detail is not bound to its scheduled operator/trace/slot: " + mutation["instance_id"])
    original = detail["original_slot_sha256"]
    replacement = detail["replacement_slot_sha256"]
    expected_original = previous.get(trace, digests[trace])
    _require(original == expected_original,
             "event original digest is not the passive original/ordered input: " + trace)
    _require(digests[trace] == _inventory_digests(plain)[trace],
             "seam capture did not retain the original bytes: " + trace)
    _require(replacement != original, "applied event without a replacement: " + trace)
    evidence = consumed.get((mutation["instance_id"], trace))
    _require(evidence is not None, "consumed replacement evidence missing: " + trace)
    data = read_bytes(evidence["file"])
    _require(sha256(data) == evidence["sha256"] == replacement,
             "consumed replacement evidence does not match the event: " + trace)
    previous[trace] = replacement
    if "inputs" in mutation:
        _require(detail.get("input_slot_sha256") == [digests[name] for name in mutation["inputs"]],
                 "mode substitute inputs are not the VCA invocation's registry producers (association broken)")


def _check_parameter_event(event: Dict[str, Any], swap: Dict[str, Any], record: Dict[str, Any]) -> None:
    detail = event["detail"]
    _require(detail.get("operator") == swap["operator"] and detail.get("parameter") == swap["parameter"]
             and detail.get("slot") == swap["slot"],
             "event detail is not bound to its scheduled operator/parameter/slot: " + swap["instance_id"])
    _require(detail["original_slot_sha256"] == record["parameters_selected_sha256"][swap["parameter"]],
             "parameter anchor did not observe the pinned post-randomization input")
    _require(detail.get("inventory_range") == swap["range"]
             and ops.same_binary32_range(detail["conversion"]["range"], swap["range"]),
             "live parameter range differs from the declared inventory range")
    conversion = detail["conversion"]
    _require(conversion.get("unit") == swap["magnitude"]["unit"]
             and conversion.get("magnitude") == swap["magnitude"]["value"]
             and type(conversion.get("conversion")) is str,
             "declared-magnitude unit conversion is not recorded in the event detail")
    _require(detail["replacement_slot_sha256"] != detail["original_slot_sha256"],
             "applied parameter event without a replacement")


def _operand_traces(manifest: Dict[str, Any], entry: Dict[str, Any]) -> List[str]:
    traces = list(entry["runtime_truth"]["compared_traces"])
    if second_detectors_of(manifest, entry["id"]):
        for trace in [PERIODIC_TRACE] + sorted(PERIODIC_PROVENANCE_TRACES.values()):
            if trace not in traces:
                traces.append(trace)
    return traces


def evaluate_process(manifest: Dict[str, Any], manifest_sha256: str, document: Dict[str, Any],
                     inventory: Dict[str, Any], catalog: Dict[str, Any], case_id: str, repeat: int,
                     schedules: Dict[str, Dict[str, Any]], records: Dict[str, Dict[str, Any]],
                     read_bytes: Callable[[str], bytes],
                     estimator: Callable[..., Dict[str, Any]] = default_estimator) -> Dict[str, Any]:
    """Offline validation of one (case, repeat) worker process report.

    Structural problems (binding, inventory, plan/schedule tamper, event
    completeness/order, controls, preflight, cleanup, evidence association)
    fail closed with ``MutationError``. Per-cell results are reported, never
    promoted: only ``detected`` is a candidate kill for the campaign, and only
    after the campaign's own repeat rule (#337).
    """
    require_inventory_match(manifest)
    binding = manifest_binding(manifest, manifest_sha256)
    expected = expected_attempts(manifest, case_id)
    _require(list(records) == expected,
             "attempt inventory differs from the expected order: " + ", ".join(records))
    _require(list(schedules) == [name for name in expected if name not in ("plain", "clean_rerun")],
             "schedule inventory differs from the expected attempts")
    fresh = process_schedules(manifest, manifest_sha256, case_id, repeat, document, inventory, catalog)
    for name, schedule in schedules.items():
        _require(schedule == fresh[name], "schedule differs from its manifest re-derivation: " + name)
    for name, record in records.items():
        _require(record.get("case") == case_id, "attempt bound to a foreign case: " + name)
        _require(record.get("attempt") == name, "attempt label mismatch: " + name)
        _require(record.get("plan_id") == (schedules[name]["plan_id"] if name in schedules else None),
                 "attempt not bound to its schedule plan: " + name)
    plain = records["plain"]
    _require(plain["errored"] is None and plain["shapes_ok"] is True and plain["state_restored"] is True,
             "plain attempt malformed")
    _require(plain["events"] == [], "plain attempt recorded events")
    for name in ("empty", "sham", "clean_rerun"):
        record = records[name]
        _require(record["errored"] is None and record["state_restored"] is True,
                 "control attempt errored or did not restore state: " + name)
        _require_identical(plain, record, name)
    _require(records["clean_rerun"]["events"] == [], "clean rerun recorded events")
    mutations.validate_events(_plan_for(manifest, "empty", case_id, catalog), records["empty"]["events"])
    mutations.validate_events(_plan_for(manifest, "sham", case_id, catalog), records["sham"]["events"])
    sham_events = records["sham"]["events"]
    _require(len(sham_events) == 1 and sham_events[0]["status"] == "ineffective"
             and sham_events[0]["sham"] is True, "sham event must be one sham ineffective event")
    _require(records["sham"].get("consumed_replacements", []) == [], "sham consumed a replacement")

    plain_digests = _inventory_digests(plain)
    plain_samples: Dict[str, List[float]] = {}

    def samples_of(record: Dict[str, Any], trace: str) -> List[float]:
        _require(trace in record.get("retained_traces", {}), "retained trace missing: " + trace)
        return trace_preflight(document, trace, read_bytes(record["retained_traces"][trace]),
                               _inventory_digests(record)[trace])

    def plain_of(trace: str) -> List[float]:
        if trace not in plain_samples:
            plain_samples[trace] = samples_of(plain, trace)
        return plain_samples[trace]

    controls: Dict[str, Dict[str, str]] = {}
    cells: List[Dict[str, Any]] = []
    measured: List[Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]] = []
    for entry in signal_entries(manifest):
        if entry["id"] not in records:
            continue
        name = entry["id"]
        record = records[name]
        schedule = schedules[name]
        realised = REALISATIONS[name]
        plan = _plan_for(manifest, name, case_id, catalog)
        cell: Dict[str, Any] = {
            "entry_id": name,
            "disposition": entry["disposition"],
            "runtime_evidence_class": entry["runtime_evidence_class"],
            "counts_toward_runtime_total": entry["counts_toward_runtime_total"],
            "events": [event["status"] for event in record["events"]],
            "mandatory_detectors": [],
            "second_detectors": [],
            "invariance": {},
        }
        cells.append(cell)
        _require(record["state_restored"] is True, "attempt did not restore state: " + name)
        if record["errored"] is not None:
            declared = schedule["declared_order"]
            observed = [event["instance_id"] for event in record["events"]]
            _require(observed == declared[: len(observed)] and observed
                     and record["events"][-1]["status"] == "errored",
                     "errored attempt events are not an ordered prefix ending in errored: " + name)
            cell["outcome"] = "errored"
            continue
        mutations.validate_events(plan, record["events"])
        statuses = set(cell["events"])
        if "refused" in statuses or "errored" in statuses:
            cell["outcome"] = "refused" if "refused" in statuses else "errored"
            continue
        if statuses != {"applied"}:
            cell["outcome"] = "open_ineffective"
            continue
        consumed = {(item["instance_id"], item["trace"]): item for item in record.get("consumed_replacements", [])}
        _require(len(consumed) == len(record.get("consumed_replacements", [])),
                 "duplicate consumed replacement evidence: " + name)
        by_id = {item["instance_id"]: item for item in schedule["module_mutations"]}
        swaps = {item["instance_id"]: item for item in schedule["parameter_swaps"]}
        _require(set(key[0] for key in consumed) <= set(by_id),
                 "consumed replacement evidence for an undeclared instance: " + name)
        previous: Dict[str, str] = {}
        for event in record["events"]:
            if event["instance_id"] in swaps:
                _check_parameter_event(event, swaps[event["instance_id"]], record)
            else:
                _check_module_event(event, by_id[event["instance_id"]], record, plain, consumed,
                                    read_bytes, previous)
        digests = _inventory_digests(record)
        allowed = blast_radius(realised)
        seams = seam_traces(realised)
        siblings = sorted(trace for trace in plain_digests if trace not in allowed)
        cell["invariance"] = {
            "original_retained_at_seam": all(digests[t] == plain_digests[t] for t in seams),
            "siblings_unchanged": [t for t in siblings if digests[t] != plain_digests[t]] == [],
            "undeclared_slots_unchanged": all(
                digest == plain["slot_sha256"][index]
                for index, digest in enumerate(record["slot_sha256"])
                if index != schedule["campaign"]["case"]["batch_slot"]
            ),
            "invocation_counts_unchanged": record["invocation_counts"] == plain["invocation_counts"],
            "parameters_before_unchanged": record["parameters_before_sha256"] == plain["parameters_before_sha256"],
        }
        measured.append((entry, record, schedule, cell))
        # Frame/identity preflight of every operand this cell's detectors read,
        # before any metric of any cell is computed.
        for trace in _operand_traces(manifest, entry):
            samples_of(record, trace)
            plain_of(trace)

    for trace in sorted({t for entry, _, _, _ in measured for t in entry["runtime_truth"]["compared_traces"]}):
        for control in ("empty", "sham", "clean_rerun"):
            samples_of(records[control], trace)

    # Metrics: paired exactness against the same process's plain attempt.
    for entry, record, schedule, cell in measured:
        name = entry["id"]
        for trace in entry["runtime_truth"]["compared_traces"]:
            rate = _registry_trace(document, trace)["rate_hz"]
            if trace not in controls:
                controls[trace] = {
                    control: paired_exactness(plain_of(trace), samples_of(records[control], trace),
                                              rate, trace)["verdict"]
                    for control in ("empty", "sham", "clean_rerun")
                }
                _require(set(controls[trace].values()) == {"PASS"},
                         "control detector did not PASS on " + trace)
            row = paired_exactness(plain_of(trace), samples_of(record, trace), rate, trace)
            row["detector_ids"] = list(entry["detector_ids"])
            cell["mandatory_detectors"].append(row)
        detected = bool(cell["mandatory_detectors"]) and all(
            row["verdict"] == "FAIL" and row["framing_match"] == 1
            and row["max_abs_error"] not in (None, 0) for row in cell["mandatory_detectors"])
        if entry["disposition"] == "composition":
            declared_events = [event["instance_id"] for event in record["events"]]
            detected = detected and declared_events == schedule["declared_order"]
        for second in second_detectors_of(manifest, name):
            lanes = {}
            for label, source in (("plain", plain), ("faulted", record)):
                lanes[label] = {
                    trace: (plain_of(trace) if source is plain else samples_of(source, trace))
                    for trace in [PERIODIC_TRACE] + sorted(PERIODIC_PROVENANCE_TRACES.values())
                }
            cell["second_detectors"].append(second_detector_gate(second, lanes, estimator))
        if not all(cell["invariance"].values()):
            cell["outcome"] = "invariance_failed"
        elif not detected:
            cell["outcome"] = "open_not_detected"
        elif entry["disposition"] == "composition":
            cell["outcome"] = "composition_detected"
        else:
            cell["outcome"] = "detected"

    refusals = [
        normalization_refusal(manifest, catalog, case_id)
        for entry in signal_entries(manifest)
        if entry["disposition"] == "refusal" and case_id in entry["development_cases"]
    ]
    return {
        "schema": "torchsynth-signal-runtime-process-v1",
        "campaign": binding,
        "realisation": ops.REALISATION,
        "case": case_record(manifest, case_id),
        "repeat": repeat,
        "attempts": list(records),
        "controls": controls,
        "cells": cells,
        "refusals": refusals,
        "deferred_unmeasured": [entry["id"] for entry in signal_entries(manifest)
                                if entry["disposition"] == "deferred_unmeasured"],
        "scope": (
            "one fresh-process execution of the signal slice; reliability across repeats, host "
            "admission and publication are campaign obligations (#337); unit fixtures never "
            "qualify actual-Voice coverage; normalization decision-site faults stay deferred (AQ-1)"
        ),
    }


def campaign_outcomes(report: Dict[str, Any]) -> Dict[Tuple[str, str, int], str]:
    """Per-execution statuses in the shape of ``runtime_kill_totals`` (manifest accounting).

    Only runtime cells whose mandatory detectors detected the fault report
    ``detected``; second detectors report ``refused_inadmissible`` (the
    accounting rule's spelling of ``refused_inadmissible_not_counted``) or stay
    absent so the case remains open. Compositions, refusals and deferred
    entries are never keyed here: they never count toward runtime kills.
    """
    _require(report.get("schema") == "torchsynth-signal-runtime-process-v1", "not a signal process report")
    _require(report["campaign"].get("frozen_inventory_sha256") == ops.MANIFEST_FROZEN_SHA256,
             "report bound to another campaign revision")
    case_id = report["case"]["id"]
    repeat = report["repeat"]
    outcomes: Dict[Tuple[str, str, int], str] = {}
    for cell in report["cells"]:
        if cell["disposition"] != "runtime_cell":
            continue
        outcomes[(cell["entry_id"], case_id, repeat)] = (
            "detected" if cell["outcome"] == "detected" else cell["outcome"]
        )
        for second in cell["second_detectors"]:
            if second["outcome"] == "refused_inadmissible_not_counted":
                outcomes[(second["entry_id"], case_id, repeat)] = "refused_inadmissible"
            elif second["outcome"] == "detected":
                outcomes[(second["entry_id"], case_id, repeat)] = "detected"
    return outcomes
