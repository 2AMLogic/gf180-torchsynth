"""Timing-family actual-Voice runtime adapter, host side (#335; Part of #300).

Stdlib-only. Binds the timing slice of the committed DR-0012 campaign
manifest (``spec/reference/runtime-family-campaign-v1.json`` revision 1,
frozen inventory digest ``b632d7ee...``) to the declared ``voice.*`` seams of
``spec/reference/mutation-seams-v1.json``:

* registers explicitly versioned runtime operators (``<fixture id>@voice-v1``)
  through the landed ``mutations.register_operator`` API; the fixture
  operators of ``mutations_timing`` keep their ``apparatus.producer_call``
  definitions and bridge operators are never relabeled;
* requires the adapter dispositions to equal the manifest's timing
  dispositions exactly (9 runtime cells, 1 composition, 2 refusals,
  4 deferred entries), so a manifest revision forces an explicit adapter update;
* builds ``mp1-`` plans bound to the manifest revision/digest and the
  resolved development case, and resolves them into
  ``mutation-voice-schedule-v1`` schedules carrying a campaign binding block
  for ``env/release-era/mutation_worker_timing.py``;
* executes the two length-changing faults as host-side frame-preflight
  refusals on retained lane bytes (never at a voice seam);
* validates one worker process report offline: attempt inventory, plan and
  schedule re-derivation, event order/status, frame/identity preflight before
  any metric, time-locked (never aligned) paired exactness detectors against
  the same process's plain attempt, controls, cleanup, passive-original versus
  consumed-replacement evidence and declared invariance.

Scope limits, stated so they cannot be mistaken for results: no Voice is
rendered here; unit fixtures exercise adapter mechanics only and never
qualify actual-Voice coverage; reliability across fresh processes, host
admission and publication belong to the campaign (#337). The analytic
gated detectors (``det-timing-01/04/05``) run the estimators' own domain
refusals on the plain and the faulted attempt of the same process (DR-0012
D7): a refusal on either is ``refused_inadmissible_not_counted``, an admitted
result is ``detected`` or ``not_detected`` (the latter leaves the case open),
and a gated detector never counts as a kill by itself. A cell whose attempt
produced no faulted trace keeps ``not_evaluated``. ``modulation.lfo_depth_shift`` stays deferred (DR-0012 D8), as do the
revision-2 #297 additions (DR-0015), and the
literal ``align_corners`` operator stays a proposed addition (D4, AQ-3).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import envelope_estimators
from . import mutation_family_runtime as family
from . import mutations
from . import paired_metrics
from . import periodic_estimators
from . import timing_voice_ops as ops
from .artifacts import canonical_bytes
from .mutations import MutationError

REQUEST_DOMAIN = b"torchsynth-timing-runtime-request-v1\n"
FAMILY = ops.FAMILY

# Exact adapter dispositions for the timing slice of manifest revision 2.
ADAPTER_DISPOSITIONS: Dict[str, str] = {
    "timing:attack-breakpoint-shift-plus2": "runtime_cell",
    "timing:one-control-sample-delay": "runtime_cell",
    "timing:missing-sample": "refusal",
    "timing:duplicated-sample": "refusal",
    "timing:lfo-rate-shift-plus-half-hz": "runtime_cell",
    "timing:lfo-depth-shift-plus-0.1": "deferred_unmeasured",
    "timing:route-sign-flip": "runtime_cell",
    "timing:route-depth-shift-plus25": "runtime_cell",
    "timing:route-destination-swap": "runtime_cell",
    "timing:zero-order-hold-control": "runtime_cell",
    "timing:dropped-endpoint-coordinate": "runtime_cell",
    "timing:one-audio-sample-delay": "runtime_cell",
    "timing:composed-zoh-then-audio-delay": "composition",
    "timing:decay-breakpoint-shift-plus2": "deferred_unmeasured",
    "timing:release-breakpoint-shift-plus2": "deferred_unmeasured",
    "timing:sustain-amplitude-shift-plus0.1": "deferred_unmeasured",
}

# Explicit realisation of every executable entry: (runtime operator, target
# configuration). Nothing here is inferred from synthetic lane names; each
# row is cross-checked against the manifest's runtime seam/target/operators.
REALISATIONS: Dict[str, List[Tuple[str, Dict[str, str]]]] = {
    "timing:attack-breakpoint-shift-plus2": [
        (ops.BREAKPOINT, {"parameter": "adsr_1.attack"}),
    ],
    "timing:one-control-sample-delay": [
        (ops.DELAY_CONTROL, {"trace": "adsr_1.output"}),
    ],
    "timing:lfo-rate-shift-plus-half-hz": [
        (ops.LFO_RATE, {"parameter": "lfo_1.frequency"}),
    ],
    "timing:route-sign-flip": [
        (ops.ROUTE_SIGN, {"trace": "mod_matrix.vco_1_pitch"}),
    ],
    "timing:route-depth-shift-plus25": [
        (ops.ROUTE_DEPTH, {"trace": "mod_matrix.vco_1_amp"}),
    ],
    "timing:route-destination-swap": [
        (ops.ROUTE_SWAP, {"trace": "mod_matrix.vco_2_pitch", "partner": "mod_matrix.vco_2_amp"}),
    ],
    "timing:zero-order-hold-control": [
        (ops.ZOH, {"trace": "control_upsample.vco_1_amp", "control_trace": "mod_matrix.vco_1_amp"}),
    ],
    "timing:dropped-endpoint-coordinate": [
        (ops.OFF_ENDPOINT, {"trace": "control_upsample.vco_1_amp", "control_trace": "mod_matrix.vco_1_amp"}),
    ],
    "timing:one-audio-sample-delay": [
        (ops.DELAY_AUDIO, {"trace": "control_upsample.vco_1_amp"}),
    ],
    "timing:composed-zoh-then-audio-delay": [
        (ops.ZOH, {"trace": "control_upsample.vco_1_amp", "control_trace": "mod_matrix.vco_1_amp"}),
        (ops.DELAY_AUDIO, {"trace": "control_upsample.vco_1_amp"}),
    ],
}

# Host-side frame-preflight refusals (never applied at a voice seam).
REFUSALS: Dict[str, str] = {
    "timing:missing-sample": "timing.drop_sample",
    "timing:duplicated-sample": "timing.duplicate_sample",
}

CONTROL_ATTEMPTS = ("plain", "empty", "sham")
SHAM_TARGET = "adsr_1.output"

# Trace-level dataflow of the pinned Voice.output (torchsynth/synth.py at
# 2b0964d4, lines 475-510): producer trace -> consumer traces. Used only to
# derive the declared blast radius; every other trace must stay invariant.
_ROUTES = list(ops.ROUTE_TRACES)
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
PARAMETER_ROOTS = {"adsr_1.attack": "adsr_1.output", "lfo_1.frequency": "lfo_1.raw"}


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


def timing_entries(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [entry for entry in manifest["inventory"] if entry["family"] == FAMILY]


def entry_by_id(manifest: Dict[str, Any], entry_id: str) -> Dict[str, Any]:
    for entry in timing_entries(manifest):
        if entry["id"] == entry_id:
            return entry
    raise MutationError("not a timing entry of the bound manifest: " + str(entry_id))


def _runtime_magnitude(operator_id: str, magnitude: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    spec = ops.RUNTIME_OPERATORS[operator_id]["magnitude"]
    if spec["type"] == "null":
        _require(magnitude["value"] is None and magnitude["unit"] == "none",
                 "manifest magnitude must be null for " + operator_id)
        return None
    return {"value": magnitude["value"], "unit": magnitude["unit"]}


def require_inventory_match(manifest: Dict[str, Any]) -> Dict[str, int]:
    """Adapter dispositions and realisations equal the manifest exactly."""
    entries = timing_entries(manifest)
    observed = {entry["id"]: entry["disposition"] for entry in entries}
    _require(len(observed) == len(entries), "duplicate timing entry ids in the manifest")
    _require(
        observed == ADAPTER_DISPOSITIONS,
        "timing adapter dispositions differ from the bound manifest; a manifest "
        "revision requires an explicit adapter update",
    )
    for entry in entries:
        entry_id = entry["id"]
        if entry["disposition"] in ("runtime_cell", "composition"):
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
            seam = ops.RUNTIME_OPERATORS[realised[0][0]]["seam"]
            _require(entry["runtime_seam"] == seam, "runtime seam differs: " + entry_id)
            targets = []
            for _, target in realised:
                for key in ("parameter", "trace"):
                    if key in target and target[key] not in targets:
                        targets.append(target[key])
                if "partner" in target:
                    targets.append(target["partner"])
            _require(entry["runtime_target"] == "+".join(targets), "runtime target differs: " + entry_id)
            _require(entry["expected_event"] == "applied", "expected event differs: " + entry_id)
            _require(entry["runtime_truth"]["rule"] == "plain_attempt_paired_reference",
                     "runtime truth rule differs: " + entry_id)
            _require(entry["repeats_required"] >= manifest["reliability"]["min_independent_executions_per_cell"],
                     "repeats below the ratified minimum: " + entry_id)
            allowed = blast_radius(realised)
            for trace in entry["runtime_truth"]["compared_traces"]:
                _require(trace in allowed, "compared trace outside the declared blast radius: " + trace)
        elif entry["disposition"] == "refusal":
            _require(entry["operators"] == [REFUSALS[entry_id]], "refusal operator differs: " + entry_id)
            _require(entry["expected_event"] == "refused", "refusal event differs: " + entry_id)
            _require(entry["counts_toward_runtime_total"] is False, "a refusal never counts: " + entry_id)
        else:
            _require(entry["obligation_open"] is True and entry["development_cases"] == [],
                     "deferred entry must stay open with no cases: " + entry_id)
    counts = {}
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
        "case": case,
        "profile": manifest["target"]["runtime_profile"],
    }
    return "trq1-" + sha256(REQUEST_DOMAIN + canonical_bytes(core))


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
            if "control_traces" in spec:
                configuration["control_trace"] = list(spec["control_traces"])
            if operator_id == ops.ROUTE_SWAP:
                configuration["partner"] = list(ops.ROUTE_TRACES)
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
                "timing-family voice-runtime sham: traverse the family dispatch and return the original slot"
                if operator_id == ops.SHAM
                else "timing-family voice-runtime realisation of "
                + spec["realises"]
                + " at "
                + spec["seam"]
                + " (timing_voice_ops); bound to runtime-family-campaign-v1 revision 1"
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

    Refusal and deferred entries refuse explicitly here; they are never
    silently dropped and never planned at a voice seam.
    """
    require_inventory_match(manifest)
    entry = entry_by_id(manifest, entry_id)
    disposition = entry["disposition"]
    if disposition == "refusal":
        raise MutationError(
            "refused: " + entry_id + " is a length-changing fault that cannot be applied in place "
            "at voice.post_module; it executes host-side as a frame-preflight refusal"
        )
    if disposition == "deferred_unmeasured":
        raise MutationError(
            "refused: " + entry_id + " is deferred_unmeasured (DR-0012 D8); no faithful writable "
            "realisation is selected and its obligation stays open"
        )
    _require(case_id in entry["development_cases"], "case not declared for entry: " + case_id)
    case = case_record(manifest, case_id)
    register_runtime_operators()
    instances = []
    fault = entry_id.split(":", 1)[1]
    for index, ((operator_id, target), magnitude) in enumerate(
        zip(REALISATIONS[entry_id], entry["magnitude"])
    ):
        configuration = dict(target, slot=case["batch_slot"])
        instances.append(
            _instance("tmr-" + fault + "-" + str(index), operator_id,
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
        instances.append(_instance("tmr-sham", ops.SHAM, None,
                                   {"trace": SHAM_TARGET, "slot": case["batch_slot"]}))
    return mutations.make_plan(_source_binding(manifest, case), instances, catalog)


def expected_attempts(manifest: Dict[str, Any], case_id: str) -> List[str]:
    """Per (case, repeat) process order: controls, entries in manifest order, clean rerun."""
    names = list(CONTROL_ATTEMPTS)
    for entry in timing_entries(manifest):
        if entry["disposition"] in ("runtime_cell", "composition") and case_id in entry["development_cases"]:
            names.append(entry["id"])
    names.append("clean_rerun")
    return names


# --------------------------------------------------------------------------
# Schedule resolution
# --------------------------------------------------------------------------


def family_seam_map(document: Dict[str, Any]) -> Dict[str, Tuple[str, int, Optional[int]]]:
    """Trace -> (module, zero-based occurrence, output index) for forward-hook outputs.

    Unlike the bridge resolver, the declared mod_matrix multi-output elements
    are included: the ratified route cells name them as their
    ``voice.post_module`` targets (DR-0012 D4), and the replacement keeps the
    tuple element's tensor object so the #22 association stays intact.
    """
    seam_map: Dict[str, Tuple[str, int, Optional[int]]] = {}
    for trace in document["traces"]:
        producer = trace["producer"]
        if producer["mechanism"] != "forward-hook":
            continue
        if producer["output_index"] is not None and trace["name"] not in ops.ROUTE_TRACES:
            continue
        seam_map[trace["name"]] = (producer["module"], producer["occurrence"] - 1, producer["output_index"])
    return seam_map


def _registry_trace(document: Dict[str, Any], name: str) -> Dict[str, Any]:
    return family.registry_trace(document, name)


def _inventory_range(inventory: Dict[str, Any], name: str) -> Dict[str, Any]:
    return family.inventory_range(inventory, name)


def resolve_schedule(plan: Dict[str, Any], manifest: Dict[str, Any], manifest_sha256: str,
                     attempt: str, case_id: str, repeat: int, document: Dict[str, Any],
                     inventory: Dict[str, Any], catalog: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a plan against its entry/case binding and resolve the schedule."""
    _require(type(repeat) is int and repeat >= 1, "repeat must be a positive integer")
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
    seam_map = family_seam_map(document)
    module_mutations: List[Dict[str, Any]] = []
    parameter_swaps: List[Dict[str, Any]] = []
    declared_order: List[str] = []
    for position, instance in enumerate(plan["mutations"]):
        operator_id = instance["operator"]
        _require(operator_id in ops.RUNTIME_OPERATORS,
                 "operator outside the timing runtime allowlist: " + str(operator_id))
        spec = ops.RUNTIME_OPERATORS[operator_id]
        configuration = instance["configuration"]
        _require(configuration["slot"] == case["batch_slot"], "slot is not the case batch slot")
        declared_order.append(instance["instance_id"])
        base = {
            "instance_id": instance["instance_id"],
            "operator": operator_id,
            "applicator": operator_id,
            "realises": spec["realises"],
            "slot": configuration["slot"],
            "magnitude": instance["magnitude"],
            "sham": instance["sham"],
            "ratio": None,
            "declared_position": position,
        }
        if spec["seam"] == ops.PARAMETER_VALUE:
            name = configuration["parameter"]
            _require(name in spec["parameters"], "parameter not declared for operator: " + name)
            parameter_swaps.append(dict(base, parameter=name, range=_inventory_range(inventory, name)))
            continue
        trace = configuration["trace"]
        _require(trace in seam_map and trace in spec["traces"], "trace not declared for operator: " + trace)
        module, occurrence, output_index = seam_map[trace]
        _require(_registry_trace(document, trace)["shape"] == [spec["frame"]],
                 "registry frame differs from the realisation frame: " + trace)
        resolved = dict(base, trace=trace, module=module, occurrence=occurrence,
                        output_index=output_index, frame=spec["frame"], **{"raise": False})
        if "partner" in configuration:
            partner = configuration["partner"]
            _require(partner != trace and partner in seam_map, "invalid route partner: " + partner)
            partner_module, partner_occurrence, partner_index = seam_map[partner]
            _require((partner_module, partner_occurrence) == (module, occurrence),
                     "route partner is not the same module invocation")
            resolved["partner_trace"] = partner
            resolved["partner_output_index"] = partner_index
        if "control_trace" in configuration:
            control = configuration["control_trace"]
            _require(control.split(".", 1)[1] == trace.split(".", 1)[1]
                     and control in DATAFLOW and trace in DATAFLOW[control],
                     "control trace is not the producer of the upsampled lane")
            _require(_registry_trace(document, control)["shape"] == [ops.CONTROL_SAMPLES],
                     "control trace frame differs")
            resolved["control_trace"] = control
        module_mutations.append(resolved)
    _require(
        max([swap["declared_position"] for swap in parameter_swaps] + [-1])
        < min([item["declared_position"] for item in module_mutations] + [len(declared_order)]),
        "parameter mutations must be declared before module mutations (graph causality)",
    )
    return {
        "schema_version": 1,
        "kind": ops.SCHEDULE_KIND,
        "family": ops.SCHEDULE_FAMILY,
        "plan_id": plan["plan_id"],
        "source_binding": dict(plan["source_binding"]),
        "batch_size": ops.BATCH_SIZE,
        "module_mutations": module_mutations,
        "parameter_swaps": parameter_swaps,
        "declared_order": declared_order,
        "campaign": dict(
            manifest_binding(manifest, manifest_sha256),
            attempt=attempt,
            entry_id=None if entry is None else entry["id"],
            disposition="control" if entry is None else entry["disposition"],
            case=case,
            repeat=repeat,
        ),
    }


def process_schedules(manifest: Dict[str, Any], manifest_sha256: str, case_id: str, repeat: int,
                      document: Dict[str, Any], inventory: Dict[str, Any],
                      catalog: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Every schedule one fresh (case, repeat) worker process executes, in order."""
    schedules: Dict[str, Dict[str, Any]] = {}
    for attempt in expected_attempts(manifest, case_id):
        if attempt in ("plain", "clean_rerun"):
            continue
        plan = (control_plan(manifest, case_id, attempt, catalog) if attempt in ("empty", "sham")
                else cell_plan(manifest, attempt, case_id, catalog))
        schedules[attempt] = resolve_schedule(plan, manifest, manifest_sha256, attempt, case_id,
                                              repeat, document, inventory, catalog)
    return schedules


# --------------------------------------------------------------------------
# Refusals, blast radius and detectors
# --------------------------------------------------------------------------


def downstream(trace: str) -> List[str]:
    return family.downstream(trace, DATAFLOW)


def blast_radius(realised: List[Tuple[str, Dict[str, str]]]) -> List[str]:
    """Traces a realisation may change; the replaced seam itself keeps its original."""
    allowed: List[str] = []
    for _, target in realised:
        if "parameter" in target:
            root = PARAMETER_ROOTS[target["parameter"]]
            names = [root] + downstream(root)
        else:
            names = downstream(target["trace"])
            if "partner" in target:
                names += downstream(target["partner"])
        allowed.extend(name for name in names if name not in allowed)
    return sorted(allowed)


def seam_traces(realised: List[Tuple[str, Dict[str, str]]]) -> List[str]:
    names: List[str] = []
    for _, target in realised:
        for key in ("trace", "partner"):
            if key in target and target[key] not in names:
                names.append(target[key])
    return names


def trace_preflight(document: Dict[str, Any], name: str, data: bytes, inventory_digest: str) -> List[float]:
    """Frame and identity preflight; runs before any metric and refuses on mismatch."""
    return family.trace_preflight(document, name, data, inventory_digest, ops)


def paired_exactness(reference: List[float], candidate: List[float], rate_hz: float, trace: str) -> Dict[str, Any]:
    """Time-locked paired exactness row; index zero is the time origin, no alignment."""
    comparison = paired_metrics.compare_paired(
        reference, candidate, reference_rate_hz=rate_hz, candidate_rate_hz=rate_hz,
        unit="1", window_samples=max(1, len(reference)),
    )
    metrics = comparison["metrics"]
    exact = metrics["exact_equal"]["value"]
    max_abs = metrics["max_abs_error"]["value"]
    return {
        "trace": trace,
        "pair_status": comparison["pair_status"],
        "time_origin": comparison["settings"]["time_origin"],
        "framing_match": metrics["framing_match"]["value"],
        "exact_equal": exact,
        "max_abs_error": max_abs,
        "first_divergence_index": metrics["first_divergence_index"]["value"],
        "verdict": "PASS" if comparison["pair_status"] == "measured" and exact == 1 else "FAIL",
    }


def frame_refusal(manifest: Dict[str, Any], document: Dict[str, Any], entry_id: str, case_id: str,
                  plain_record: Dict[str, Any], read_bytes: Callable[[str], bytes]) -> Dict[str, Any]:
    """Host-side expected refusal of a length-changing fault on retained lane bytes."""
    entry = entry_by_id(manifest, entry_id)
    _require(entry["disposition"] == "refusal", "not a refusal entry: " + entry_id)
    _require(case_id in entry["development_cases"], "case not declared for entry: " + case_id)
    lane = entry["configuration"]["lane"]
    index = entry["configuration"]["index"]
    digest = _inventory_digests(plain_record)[lane]
    source = read_bytes(plain_record["retained_traces"][lane])
    samples = trace_preflight(document, lane, source, digest)
    _require(0 <= index < len(samples), "refusal index outside the declared frame")
    if REFUSALS[entry_id] == "timing.drop_sample":
        edited = samples[:index] + samples[index + 1:]
    else:
        edited = samples[: index + 1] + [samples[index]] + samples[index + 1:]
    replacement = ops.pack(edited)
    refusal = None
    try:
        trace_preflight(document, lane, replacement, sha256(replacement))
    except MutationError as error:
        refusal = str(error)
    _require(refusal is not None, "length-changing edit was not refused by the frame preflight")
    rate = _registry_trace(document, lane)["rate_hz"]
    landed = paired_metrics.compare_paired(samples, edited, reference_rate_hz=rate,
                                           candidate_rate_hz=rate, unit="1")["pair_status"]
    _require(landed == "frame_mismatch", "landed paired comparison did not refuse the frame")
    return {
        "entry_id": entry_id,
        "case": case_id,
        "operator": REFUSALS[entry_id],
        "status": "refused",
        "detail": {
            "lane": lane,
            "index": index,
            "declared_count": len(samples),
            "observed_count": len(edited),
            "source_sha256": digest,
            "replacement_sha256": sha256(replacement),
            "preflight_refusal": refusal,
            "landed_pair_status": landed,
        },
        "counts_toward_runtime_total": False,
    }


# --------------------------------------------------------------------------
# Admissibility-gated analytic detectors (DR-0012 D7)
# --------------------------------------------------------------------------

GATE_DETECTED = "detected"
GATE_NOT_DETECTED = "not_detected"
GATE_REFUSED = "refused_inadmissible_not_counted"
GATE_OUTCOMES = (GATE_DETECTED, GATE_NOT_DETECTED, GATE_REFUSED)
CONTROL_RATE_HZ = 441
ATTACK_LIMIT_CONTROL_SAMPLES = 0.02  # manifest bound of det-timing-01
LFO_RATE_LIMIT_HZ = 0.02  # manifest bound of det-timing-04
ROUTE_GAIN_LIMIT = 1e-8  # manifest bound of det-timing-05
ROUTE_SOURCE = "adsr_1.output"
LFO_TRACE = "lfo_1.raw"
LFO_UNIT = "linear-amplitude"
GATED_TRUTH_RULE = "plain_attempt_paired_reference"


def _gate_row(gated: Dict[str, Any], outcome: str, reason: str, **evidence: Any) -> Dict[str, Any]:
    return dict({"detector_id": gated["detector_id"], "outcome": outcome, "reason": reason}, **evidence)


def _gate_refusal(gated: Dict[str, Any], reason: str, **evidence: Any) -> Dict[str, Any]:
    return _gate_row(gated, GATE_REFUSED, reason, **evidence)


def _metric_values(results: List[Dict[str, Any]], names: List[str]) -> Tuple[Optional[List[List[float]]], str]:
    """Per-result metric values, or the first estimator refusal reason."""
    values: List[List[float]] = []
    for label, result in zip(("plain", "faulted"), results):
        row = []
        for name in names:
            metric = result["metrics"][name]
            if metric["value"] is None:
                return None, label + " attempt refused " + name + ": " + str(metric["reason"])
            row.append(metric["value"])
        values.append(row)
    return values, ""


def _gate_attack(gated: Dict[str, Any], plain: Callable[[str], List[float]],
                 faulted: Callable[[str], List[float]], context: Dict[str, Any]) -> Dict[str, Any]:
    alpha, note = context.get("alpha"), context.get("note_on_seconds")
    if alpha is None or note is None:
        return _gate_refusal(gated, "known alpha and note duration unavailable (gate context)")
    try:
        results = [envelope_estimators.estimate_envelope(
            read("adsr_1.output"), rate_hz=CONTROL_RATE_HZ, note_on_seconds=note, alpha=alpha)
            for read in (plain, faulted)]
    except ValueError as error:
        return _gate_refusal(gated, "estimator input refused: " + str(error))
    values, reason = _metric_values(results, ["attack_end"])
    if values is None:
        return _gate_refusal(gated, reason)
    reference, observed = values[0][0], values[1][0]
    deviation = abs(observed - reference)
    return _gate_row(
        gated, GATE_DETECTED if deviation > ATTACK_LIMIT_CONTROL_SAMPLES else GATE_NOT_DETECTED,
        "attack_end deviation against the plain-attempt estimate",
        estimator="envelope_estimators.estimate_envelope", unit="control_sample",
        plain_estimate=reference, faulted_estimate=observed, deviation=deviation,
        limit=ATTACK_LIMIT_CONTROL_SAMPLES)


def _gate_lfo_rate(gated: Dict[str, Any], plain: Callable[[str], List[float]],
                   faulted: Callable[[str], List[float]], context: Dict[str, Any]) -> Dict[str, Any]:
    if context.get("lfo_fundamental_in_band") is not True:
        return _gate_refusal(gated, "fundamental_in_band provenance unavailable (gate context)")
    estimates = []
    for label, read in (("plain", plain), ("faulted", faulted)):
        samples = read(LFO_TRACE)
        try:
            result = periodic_estimators.estimate_periodic(
                samples, sample_rate_hz=CONTROL_RATE_HZ, unit=LFO_UNIT, family="lfo",
                reference_hz=1.0,  # only scales the unused cents property
                preparation=periodic_estimators.identity_metadata(len(samples), CONTROL_RATE_HZ, LFO_UNIT),
                fundamental_in_band=True)
        except ValueError as error:
            return _gate_refusal(gated, label + " attempt input refused: " + str(error))
        frequency = result["estimates"]["frequency_hz"]
        if result["status"] != "valid" or frequency is None:
            return _gate_refusal(gated, label + " attempt refused: " + str(result["reason"]))
        estimates.append(frequency)
    deviation = abs(estimates[1] - estimates[0])
    return _gate_row(
        gated, GATE_DETECTED if deviation > LFO_RATE_LIMIT_HZ else GATE_NOT_DETECTED,
        "frequency_hz deviation against the plain-attempt estimate",
        estimator="periodic_estimators.estimate_periodic", unit="Hz",
        plain_estimate=estimates[0], faulted_estimate=estimates[1], deviation=deviation,
        limit=LFO_RATE_LIMIT_HZ)


def _gate_routes(gated: Dict[str, Any], plain: Callable[[str], List[float]],
                 faulted: Callable[[str], List[float]], context: Dict[str, Any]) -> Dict[str, Any]:
    source = plain(ROUTE_SOURCE)
    names = ["gain." + destination for destination in envelope_estimators.DESTINATIONS]
    try:
        results = [envelope_estimators.estimate_routes(
            source, {destination: read("mod_matrix." + destination)
                     for destination in envelope_estimators.DESTINATIONS}, rate_hz=CONTROL_RATE_HZ)
            for read in (plain, faulted)]
    except ValueError as error:
        return _gate_refusal(gated, "estimator input refused: " + str(error))
    values, reason = _metric_values(results, names)
    if values is None:
        return _gate_refusal(gated, reason)
    deviations = {name: abs(b - a) for name, a, b in zip(names, values[0], values[1])}
    return _gate_row(
        gated, GATE_DETECTED if max(deviations.values()) > ROUTE_GAIN_LIMIT else GATE_NOT_DETECTED,
        "route gain deviation against the plain-attempt estimates",
        estimator="envelope_estimators.estimate_routes", unit="1",
        plain_estimate=dict(zip(names, values[0])), faulted_estimate=dict(zip(names, values[1])),
        deviation=deviations, limit=ROUTE_GAIN_LIMIT)


GATED_DETECTORS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "det-timing-01": _gate_attack,
    "det-timing-04": _gate_lfo_rate,
    "det-timing-05": _gate_routes,
}


def evaluate_gated(entry: Dict[str, Any], plain: Callable[[str], List[float]],
                   faulted: Callable[[str], List[float]], context: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Gate outcome for every gated detector of a cell; structural drift fails closed."""
    rows = []
    for gated in entry["gated_detectors"]:
        detector = gated["detector_id"]
        _require(detector in GATED_DETECTORS, "no admissibility binding for gated detector: " + str(detector))
        _require(gated["truth_rule"] == GATED_TRUTH_RULE and gated["counts_as_kill"] is False
                 and gated["on_inadmissible"] == GATE_REFUSED,
                 "gated detector policy differs from the bound policy: " + detector)
        row = GATED_DETECTORS[detector](gated, plain, faulted, context)
        _require(row["detector_id"] == detector and row["outcome"] in GATE_OUTCOMES,
                 "gate outcome missing or malformed: " + detector)
        rows.append(row)
    return rows



# --------------------------------------------------------------------------
# Process report validation
# --------------------------------------------------------------------------

_IDENTITY_KEYS = family.IDENTITY_KEYS


def _inventory_digests(record: Dict[str, Any]) -> Dict[str, str]:
    return family.inventory_digests(record)


def _require_identical(plain: Dict[str, Any], record: Dict[str, Any], label: str) -> None:
    family.require_identical(plain, record, label, _IDENTITY_KEYS)


def _plan_for(manifest: Dict[str, Any], attempt: str, case_id: str, catalog: Dict[str, Any]) -> Dict[str, Any]:
    if attempt in ("empty", "sham"):
        return control_plan(manifest, case_id, attempt, catalog)
    return cell_plan(manifest, attempt, case_id, catalog)


def _check_module_event(event: Dict[str, Any], mutation: Dict[str, Any], record: Dict[str, Any],
                        plain: Dict[str, Any], consumed: Dict[Tuple[str, str], Dict[str, Any]],
                        read_bytes: Callable[[str], bytes], previous: Dict[str, str]) -> None:
    detail = event["detail"]
    digests = _inventory_digests(record)
    pairs = [(mutation["trace"], detail["original_slot_sha256"], detail["replacement_slot_sha256"])]
    if "partner_trace" in mutation:
        pairs.append((mutation["partner_trace"], detail["partner_original_slot_sha256"],
                      detail["partner_replacement_slot_sha256"]))
    for trace, original, replacement in pairs:
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
    if "control_trace" in mutation:
        _require(detail["control_input_slot_sha256"] == digests[mutation["control_trace"]],
                 "control input is not the registry producer's output (association broken)")


def _check_parameter_event(event: Dict[str, Any], swap: Dict[str, Any], record: Dict[str, Any]) -> None:
    detail = event["detail"]
    _require(detail["original_slot_sha256"] == record["parameters_selected_sha256"][swap["parameter"]],
             "parameter anchor did not observe the pinned post-randomization input")
    _require(detail["conversion"]["range"] == swap["range"],
             "live parameter range differs from the declared inventory range")
    _require(detail["replacement_slot_sha256"] != detail["original_slot_sha256"],
             "applied parameter event without a replacement")


def evaluate_process(manifest: Dict[str, Any], manifest_sha256: str, document: Dict[str, Any],
                     inventory: Dict[str, Any], catalog: Dict[str, Any], case_id: str, repeat: int,
                     schedules: Dict[str, Dict[str, Any]], records: Dict[str, Dict[str, Any]],
                     read_bytes: Callable[[str], bytes],
                     gate_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Offline validation of one (case, repeat) worker process report.

    ``gate_context`` carries the resolved case scalars the admissibility gates
    need and the report does not retain (``alpha``, ``note_on_seconds`` of
    ``adsr_1`` and ``lfo_fundamental_in_band`` provenance); a gate whose context
    is absent is ``refused_inadmissible_not_counted``, never guessed.

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

    plain_digests = _inventory_digests(plain)
    plain_samples: Dict[str, List[float]] = {}

    def samples_of(record: Dict[str, Any], trace: str) -> List[float]:
        _require(trace in record.get("retained_traces", {}), "retained trace missing: " + trace)
        return trace_preflight(document, trace, read_bytes(record["retained_traces"][trace]),
                               _inventory_digests(record)[trace])

    controls: Dict[str, Dict[str, str]] = {}
    cells: List[Dict[str, Any]] = []
    for entry in timing_entries(manifest):
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
            "gated_detectors": [
                {"detector_id": gated["detector_id"], "outcome": "not_evaluated",
                 "reason": "no faulted trace was produced; a missing gate outcome "
                           "leaves the case open (DR-0012 D7)"}
                for gated in entry["gated_detectors"]
            ],
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
        by_id = {item["instance_id"]: item for item in schedule["module_mutations"]}
        swaps = {item["instance_id"]: item for item in schedule["parameter_swaps"]}
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
        }
        if entry["detector_ids"] == ["det-timing-06"]:
            control_lane = entry["configuration"]["control_lane"]
            cell["invariance"]["source_control_lane_unchanged"] = digests[control_lane] == plain_digests[control_lane]
            cell["invariance"]["parameter_map_unchanged"] = (
                record["parameters_before_sha256"] == plain["parameters_before_sha256"])
        if "dropped-endpoint" in name:
            cell["invariance"]["original_endpoint_capture_unchanged"] = (
                digests["control_upsample.vco_1_amp"] == plain_digests["control_upsample.vco_1_amp"])
        for trace in entry["runtime_truth"]["compared_traces"]:
            if trace not in plain_samples:
                plain_samples[trace] = samples_of(plain, trace)
                rate = _registry_trace(document, trace)["rate_hz"]
                controls[trace] = {
                    control: paired_exactness(plain_samples[trace], samples_of(records[control], trace),
                                              rate, trace)["verdict"]
                    for control in ("empty", "sham", "clean_rerun")
                }
                _require(set(controls[trace].values()) == {"PASS"},
                         "control detector did not PASS on " + trace)
            rate = _registry_trace(document, trace)["rate_hz"]
            row = paired_exactness(plain_samples[trace], samples_of(record, trace), rate, trace)
            row["detector_ids"] = list(entry["detector_ids"])
            cell["mandatory_detectors"].append(row)
        detected = all(row["verdict"] == "FAIL" and row["framing_match"] == 1
                       and row["max_abs_error"] not in (None, 0) for row in cell["mandatory_detectors"])
        if entry["gated_detectors"]:
            replaced = {trace: item for (_, trace), item in consumed.items()}

            def faulted_trace(trace: str, record=record, replaced=replaced) -> List[float]:
                if trace in replaced:
                    data = read_bytes(replaced[trace]["file"])
                    return trace_preflight(document, trace, data, sha256(data))
                return samples_of(record, trace)

            cell["gated_detectors"] = evaluate_gated(
                entry, lambda trace: samples_of(plain, trace), faulted_trace, gate_context or {})
        if not all(cell["invariance"].values()):
            cell["outcome"] = "invariance_failed"
        elif not detected:
            cell["outcome"] = "open_not_detected"
        elif entry["disposition"] == "composition":
            cell["outcome"] = "composition_detected"
        elif any(gated["outcome"] == GATE_NOT_DETECTED for gated in cell["gated_detectors"]):
            cell["outcome"] = "open_gated_not_detected"
        else:
            cell["outcome"] = "detected"

    refusals = [
        frame_refusal(manifest, document, entry["id"], case_id, plain, read_bytes)
        for entry in timing_entries(manifest)
        if entry["disposition"] == "refusal" and case_id in entry["development_cases"]
    ]
    return {
        "schema": "torchsynth-timing-runtime-process-v1",
        "campaign": binding,
        "case": case_record(manifest, case_id),
        "repeat": repeat,
        "attempts": list(records),
        "controls": controls,
        "cells": cells,
        "refusals": refusals,
        "deferred_unmeasured": [entry["id"] for entry in timing_entries(manifest)
                                if entry["disposition"] == "deferred_unmeasured"],
        "scope": (
            "one fresh-process execution of the timing slice; reliability across repeats, host "
            "admission and publication are campaign obligations (#337); unit fixtures never "
            "qualify actual-Voice coverage"
        ),
    }
