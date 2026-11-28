"""Helpers shared by the signal and timing runtime adapters (#405, #463, #541).

Stdlib-only. Each family adapter keeps module-level wrappers under its own
names; the bodies that were copied between ``mutations_signal_runtime`` and
``mutations_timing_runtime`` live here, parameterised only by the family's
voice-ops module, dataflow map, identity keys, entry selector or request
prefix.

#405 hoisted the bodies that were byte-identical. #463 hoisted the seven that
had drifted into *near*-duplicates (0.79-0.95 similar), which an exact-body
diff could not see. #541 hoisted the six verification-gate and plan-assembly
stages the two ``cell_plan``/``evaluate_process`` bodies still duplicated --
whole-function scoring had kept them out of #463's scope because each mixes
shared validation with family-specific orchestration -- and closed five
fail-closed checks that had drifted signal-only (sham consumed replacements,
duplicate and undeclared-instance consumed evidence, the module-event
operator/trace/slot binding, and the non-empty mandatory-detector guard).
Where two copies genuinely disagreed the difference is now an explicit
parameter with the reason recorded on the helper, so a future edit cannot
land on one family only and no divergence stays silent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import mutations
from . import paired_metrics
from .artifacts import canonical_bytes
from .mutation_runtime import _require, sha256
from .mutations import MutationError

IDENTITY_KEYS = (
    "batch_audio_sha256",
    "audio_slot_sha256",
    "slot_sha256",
    "noise_sha256",
    "rng_state",
    "labels_all",
    "invocation_counts",
    "capture_inventory",
    "parameters_before_sha256",
)

# Trace-level dataflow of the pinned Voice.output (torchsynth/synth.py at
# 2b0964d4, lines 475-510): producer trace -> consumer traces. This describes
# the pinned Voice, not either mutation family (#481): both family adapters
# alias ``VOICE_DATAFLOW`` instead of keeping their own copies, and the map is
# used only to derive the declared blast radius; every other trace must stay
# invariant.
VOICE_ROUTE_TRACES = (
    "mod_matrix.vco_1_pitch",
    "mod_matrix.vco_1_amp",
    "mod_matrix.vco_2_pitch",
    "mod_matrix.vco_2_amp",
    "mod_matrix.noise_amp",
)
_VOICE_ROUTES = list(VOICE_ROUTE_TRACES)
VOICE_DATAFLOW: Dict[str, List[str]] = {
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
    "adsr_1.output": _VOICE_ROUTES,
    "adsr_2.output": _VOICE_ROUTES,
    "lfo_1.post_control_vca": _VOICE_ROUTES,
    "lfo_2.post_control_vca": _VOICE_ROUTES,
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


def load_manifest(root: Path, ops: Any) -> Tuple[Dict[str, Any], str]:
    """Load the committed manifest; fail closed on revision/digest drift."""
    data = (Path(root) / ops.MANIFEST_PATH).read_bytes()
    manifest = json.loads(data)
    try:
        ops.require_manifest_binding(manifest)
    except ValueError as error:
        raise MutationError(str(error))
    return manifest, sha256(data)


def manifest_binding(manifest: Dict[str, Any], manifest_sha256: str, ops: Any) -> Dict[str, Any]:
    try:
        ops.require_manifest_binding(manifest)
    except ValueError as error:
        raise MutationError(str(error))
    return {
        "identity": ops.MANIFEST_IDENTITY,
        "revision": ops.MANIFEST_REVISION,
        "frozen_inventory_sha256": ops.MANIFEST_FROZEN_SHA256,
        "manifest_sha256": manifest_sha256,
    }


def case_record(manifest: Dict[str, Any], case_id: str, ops: Any) -> Dict[str, Any]:
    for case in manifest["cases"]:
        if case["id"] == case_id:
            holdout = manifest["holdout"]
            _require(case["partition"] == "development", "case outside the development partition")
            _require(
                not holdout["corpus_holdout_first_index"]
                <= case["global_sound_index"]
                <= holdout["corpus_holdout_last_index"],
                "case lies in the sealed holdout",
            )
            _require(case["batch_size"] == ops.BATCH_SIZE, "case batch outside the pinned batch")
            _require(
                divmod(case["global_sound_index"], case["batch_size"])
                == (case["batch_block"], case["batch_slot"]),
                "case coordinates are inconsistent",
            )
            return dict(case)
    raise MutationError("unknown campaign case: " + str(case_id))


def request_identity(manifest: Dict[str, Any], case: Dict[str, Any], ops: Any, *,
                     prefix: str, domain: bytes,
                     extra: Optional[Dict[str, Any]] = None) -> str:
    """Domain-separated identity of the resolved development request.

    ``prefix`` and ``domain`` are the family's own (``srq1-`` / ``trq1-`` and
    the matching request domain), so the two families' identities stay
    distinct. ``extra`` carries the keys only one family binds -- signal adds
    ``family`` and ``realisation``. ``canonical_bytes`` sorts object keys, so
    where a key is inserted does not affect the digest.
    """
    core = {
        "campaign": ops.MANIFEST_IDENTITY,
        "revision": ops.MANIFEST_REVISION,
        "frozen_inventory_sha256": ops.MANIFEST_FROZEN_SHA256,
        "case": case,
        "profile": manifest["target"]["runtime_profile"],
    }
    core.update(extra or {})
    return prefix + sha256(domain + canonical_bytes(core))


def runtime_definitions(ops: Any, *, family_label: str, provenance: str,
                        configure: Optional[Callable[[str, Dict[str, Any], Dict[str, Any]],
                                                     None]] = None,
                        ) -> Dict[str, Dict[str, Any]]:
    """Build the family's runtime operator definitions from ``ops.RUNTIME_OPERATORS``.

    ``configure`` is the family's hook for the trace-seam configuration rows
    only one family declares (timing's ``control_trace`` and route-swap
    ``partner``); it is called with the operator id, its spec and the
    configuration built so far. A parameter-value seam takes no hook, matching
    both original bodies.
    """
    definitions: Dict[str, Dict[str, Any]] = {}
    slot = {"type": "integer", "minimum": 0}
    for operator_id, spec in ops.RUNTIME_OPERATORS.items():
        if spec["seam"] == ops.PARAMETER_VALUE:
            configuration: Dict[str, Any] = {"parameter": list(spec["parameters"]), "slot": slot}
        else:
            configuration = {"trace": list(spec["traces"]), "slot": slot}
            if configure is not None:
                configure(operator_id, spec, configuration)
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
                family_label
                + "-family voice-runtime sham: traverse the family dispatch and return the original slot"
                if operator_id == ops.SHAM
                else family_label
                + "-family voice-runtime realisation of "
                + spec["realises"]
                + " at "
                + spec["seam"]
                + " ("
                + provenance
                + f"); bound to {ops.MANIFEST_IDENTITY} revision {ops.MANIFEST_REVISION}"
            ),
        }
    return definitions


def expected_attempts(manifest: Dict[str, Any], case_id: str, *,
                      entries: Callable[[Dict[str, Any]], List[Dict[str, Any]]],
                      control_attempts: Sequence[str],
                      precheck: Optional[Callable[[Dict[str, Any], str], Any]] = None,
                      ) -> List[str]:
    """Per (case, repeat) process order: controls, entries in manifest order, clean rerun.

    ``precheck`` is the family's ``case_record`` where that family resolves the
    case before listing attempts, which refuses an unknown or non-development
    case id. Signal passes it and timing does not, so on an unknown case id
    signal raises ``MutationError`` while timing returns the controls plus
    ``clean_rerun``. That asymmetry predates #463 and is preserved here rather
    than normalised silently: it is now one explicit argument instead of two
    copies that happen to differ by a line.
    """
    if precheck is not None:
        precheck(manifest, case_id)
    names = list(control_attempts)
    for entry in entries(manifest):
        if (entry["disposition"] in ("runtime_cell", "composition")
                and case_id in entry["development_cases"]):
            names.append(entry["id"])
    names.append("clean_rerun")
    return names


def process_schedules(manifest: Dict[str, Any], manifest_sha256: str, case_id: str, repeat: int,
                      document: Dict[str, Any], inventory: Dict[str, Any],
                      catalog: Dict[str, Any], *,
                      attempts: Callable[[Dict[str, Any], str], List[str]],
                      plan_for: Callable[..., Dict[str, Any]],
                      resolve: Callable[..., Dict[str, Any]],
                      ) -> Dict[str, Dict[str, Any]]:
    """Every schedule one fresh (case, repeat) worker process executes, in order.

    ``plan_for`` is the family's own control/cell plan router and ``resolve``
    its ``resolve_schedule``; ``plain`` and ``clean_rerun`` carry no schedule.
    """
    schedules: Dict[str, Dict[str, Any]] = {}
    for attempt in attempts(manifest, case_id):
        if attempt in ("plain", "clean_rerun"):
            continue
        plan = plan_for(manifest, attempt, case_id, catalog)
        schedules[attempt] = resolve(plan, manifest, manifest_sha256, attempt, case_id,
                                     repeat, document, inventory, catalog)
    return schedules


def register_runtime_operators(definitions: Dict[str, Any]) -> None:
    """Explicit trusted-code registration; identical re-registration is a no-op."""
    for operator_id, definition in definitions.items():
        registered = mutations.OPERATORS.get(operator_id)
        if registered is not None:
            _require(registered == definition,
                     "operator already registered with a different definition: " + operator_id)
            continue
        mutations.register_operator(operator_id, definition)


def instance(instance_id: str, operator_id: str, magnitude: Optional[Dict[str, Any]],
             configuration: Dict[str, Any], ops: Any) -> Dict[str, Any]:
    spec = ops.RUNTIME_OPERATORS[operator_id]
    return {
        "instance_id": instance_id,
        "operator": operator_id,
        "operator_version": 1,
        "seam": spec["seam"],
        "magnitude": magnitude,
        "configuration": dict(configuration),
        "sham": operator_id == ops.SHAM,
    }


def control_plan(manifest: Dict[str, Any], case_id: str, kind: str,
                 catalog: Dict[str, Any], *, ops: Any, instance_prefix: str,
                 sham_target: str, register: Callable[[], None],
                 source_binding: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
                 case_of: Callable[[Dict[str, Any], str], Dict[str, Any]],
                 ) -> Dict[str, Any]:
    """The ``mp1-`` plan of one control attempt (``empty`` or ``sham``).

    Identical in both adapters except for the instance prefix (``smr`` /
    ``tmr``), the sham target and the family's own binding and registration
    helpers (#541).
    """
    _require(kind in ("empty", "sham"), "unknown control plan: " + str(kind))
    case = case_of(manifest, case_id)
    register()
    instances = []
    if kind == "sham":
        instances.append(instance(instance_prefix + "-sham", ops.SHAM, None,
                                  {"trace": sham_target, "slot": case["batch_slot"]}, ops))
    return mutations.make_plan(source_binding(manifest, case), instances, catalog)


def build_cell_plan(manifest: Dict[str, Any], entry_id: str, entry: Dict[str, Any], case_id: str,
                    catalog: Dict[str, Any], *, ops: Any, instance_prefix: str,
                    realisations: Dict[str, List[Tuple[str, Dict[str, str]]]],
                    runtime_magnitude: Callable[[str, Dict[str, Any]], Optional[Dict[str, Any]]],
                    register: Callable[[], None],
                    source_binding: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
                    case_of: Callable[[Dict[str, Any], str], Dict[str, Any]],
                    ) -> Dict[str, Any]:
    """Post-refusal ``mp1-`` plan assembly of one executable entry (#541).

    Reached only after the family's own refusal, second-detector and deferred
    branches have fired, so what remains -- the case declaration check and the
    instance/magnitude assembly against the manifest order -- is shared. Each
    family keeps its ``_runtime_magnitude``: signal additionally refuses a
    manifest magnitude unit that differs from the operator spec, a check
    timing never had, so the difference stays an explicit argument.
    """
    _require(case_id in entry["development_cases"], "case not declared for entry: " + str(case_id))
    case = case_of(manifest, case_id)
    register()
    instances = []
    fault = entry_id.split(":", 1)[1]
    for index, ((operator_id, target), magnitude) in enumerate(
        zip(realisations[entry_id], entry["magnitude"])
    ):
        configuration = dict(target, slot=case["batch_slot"])
        instances.append(
            instance(instance_prefix + "-" + fault + "-" + str(index), operator_id,
                     runtime_magnitude(operator_id, magnitude), configuration, ops)
        )
    return mutations.make_plan(source_binding(manifest, case), instances, catalog)


def registry_trace(document: Dict[str, Any], name: str) -> Dict[str, Any]:
    for trace in document["traces"]:
        if trace["name"] == name:
            return trace
    raise MutationError("unknown registry trace: " + str(name))


def inventory_range(inventory: Dict[str, Any], name: str) -> Dict[str, Any]:
    for parameter in inventory["parameters"]:
        if parameter["name"] == name:
            return {
                "minimum": float(parameter["minimum"]),
                "maximum": float(parameter["maximum"]),
                "curve": float(parameter["curve"]),
                "symmetric": parameter["symmetric"],
            }
    raise MutationError("unknown inventory parameter: " + str(name))


def downstream(trace: str, dataflow: Dict[str, List[str]]) -> List[str]:
    seen: List[str] = []
    stack = list(dataflow[trace])
    while stack:
        name = stack.pop()
        if name not in seen:
            seen.append(name)
            stack.extend(dataflow[name])
    return sorted(seen)


def blast_radius(realised: List[Tuple[str, Dict[str, str]]], dataflow: Dict[str, List[str]],
                 parameter_roots: Dict[str, str]) -> List[str]:
    """Traces a realisation may change; a replaced seam itself keeps its original.

    A target carrying an optional ``partner`` contributes that trace's
    downstream too. Only timing declares one (the route-destination swap), so
    this is the timing body: on signal realisations, which never carry
    ``partner``, the extra branch cannot fire and the result is unchanged.
    """
    allowed: List[str] = []
    for _, target in realised:
        if "parameter" in target:
            root = parameter_roots[target["parameter"]]
            names = [root] + downstream(root, dataflow)
        else:
            names = downstream(target["trace"], dataflow)
            if "partner" in target:
                names += downstream(target["partner"], dataflow)
        allowed.extend(name for name in names if name not in allowed)
    return sorted(allowed)


def seam_traces(realised: List[Tuple[str, Dict[str, str]]]) -> List[str]:
    """The seam traces a realisation names, in realisation order, deduplicated.

    Collects ``partner`` as well for the same reason ``blast_radius`` does; a
    signal realisation has none, so its order and contents are unchanged.
    """
    names: List[str] = []
    for _, target in realised:
        for key in ("trace", "partner"):
            if key in target and target[key] not in names:
                names.append(target[key])
    return names


def paired_exactness(reference: List[float], candidate: List[float], rate_hz: float,
                     trace: str) -> Dict[str, Any]:
    """Paired exactness contract row: no preparation, no alignment, index zero is the origin.

    Alignment is never applied, because aligning would mask exactly the faults
    the time-locked detectors exist to catch (``det-signal-01/09``,
    ``det-timing``).

    ``mean_error`` is part of this row. Before #463 the signal copy reported it
    and the timing copy did not, from otherwise identical bodies. The omission
    was found to be unintentional, not a timing-family decision: no decision
    record, manifest field, publication schema or test asserted either shape;
    neither family's ``detected`` rule reads it (both use ``verdict``,
    ``framing_match`` and ``max_abs_error``); and ``compare_paired`` computes
    it identically for both, since both call it with the same arguments. So the
    shared row reports it for both families, and the timing report gains the
    key. On an unmeasured pair it is ``None``, as the signal row already was.
    """
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


def trace_preflight(document: Dict[str, Any], name: str, data: bytes, inventory_digest: str,
                    ops: Any) -> List[float]:
    """Frame and identity preflight; runs before any metric and refuses on mismatch."""
    trace = registry_trace(document, name)
    _require(trace["dtype"] == "float32", "trace dtype is not float32: " + name)
    frame = 1
    for size in trace["shape"]:
        frame *= size
    _require(type(data) is bytes and len(data) == 4 * frame,
             "frame preflight refusal: " + name + " declares " + str(frame) + " samples, observed "
             + str(len(data) // 4 if type(data) is bytes else None))
    _require(sha256(data) == inventory_digest,
             "identity preflight refusal: retained bytes do not match the capture inventory: " + name)
    return list(ops.floats(data, frame))


def inventory_digests(record: Dict[str, Any]) -> Dict[str, str]:
    return {item["name"]: item["sha256"] for item in record["capture_inventory"]}


def require_identical(plain: Dict[str, Any], record: Dict[str, Any], label: str,
                      identity_keys: Sequence[str]) -> None:
    for key in identity_keys:
        _require(record.get(key) == plain.get(key),
                 "control is not byte-identical to plain: " + label + " (" + key + ")")


def check_module_event(event: Dict[str, Any], mutation: Dict[str, Any], record: Dict[str, Any],
                       plain: Dict[str, Any], consumed: Dict[Tuple[str, str], Dict[str, Any]],
                       read_bytes: Callable[[str], bytes], previous: Dict[str, str]) -> None:
    """Verify one applied module event against its schedule row and evidence (#541).

    Strict superset of both pre-#541 bodies: the operator/trace/slot binding
    and the optional ``inputs`` association existed only in signal, the
    optional ``partner_trace`` pair loop and ``control_trace`` association
    only in timing. Neither family's schedules declare the other's optional
    keys, so each branch fires only for the family that declares it -- and
    the binding check now holds for both families' module events.
    """
    detail = event["detail"]
    digests = inventory_digests(record)
    trace = mutation["trace"]
    _require(detail.get("operator") == mutation["operator"] and detail.get("trace") == trace
             and detail.get("slot") == mutation["slot"],
             "event detail is not bound to its scheduled operator/trace/slot: " + mutation["instance_id"])
    pairs = [(trace, detail["original_slot_sha256"], detail["replacement_slot_sha256"])]
    if "partner_trace" in mutation:
        pairs.append((mutation["partner_trace"], detail["partner_original_slot_sha256"],
                      detail["partner_replacement_slot_sha256"]))
    for name, original, replacement in pairs:
        expected_original = previous.get(name, digests[name])
        _require(original == expected_original,
                 "event original digest is not the passive original/ordered input: " + name)
        _require(digests[name] == inventory_digests(plain)[name],
                 "seam capture did not retain the original bytes: " + name)
        _require(replacement != original, "applied event without a replacement: " + name)
        evidence = consumed.get((mutation["instance_id"], name))
        _require(evidence is not None, "consumed replacement evidence missing: " + name)
        data = read_bytes(evidence["file"])
        _require(sha256(data) == evidence["sha256"] == replacement,
                 "consumed replacement evidence does not match the event: " + name)
        previous[name] = replacement
    if "inputs" in mutation:
        _require(detail.get("input_slot_sha256") == [digests[name] for name in mutation["inputs"]],
                 "mode substitute inputs are not the VCA invocation's registry producers (association broken)")
    if "control_trace" in mutation:
        _require(detail["control_input_slot_sha256"] == digests[mutation["control_trace"]],
                 "control input is not the registry producer's output (association broken)")


def validate_process_controls(manifest: Dict[str, Any], manifest_sha256: str, case_id: str,
                              repeat: int, document: Dict[str, Any], inventory: Dict[str, Any],
                              catalog: Dict[str, Any], schedules: Dict[str, Dict[str, Any]],
                              records: Dict[str, Dict[str, Any]], *,
                              require_match: Callable[[Dict[str, Any]], Any],
                              binding_of: Callable[[Dict[str, Any], str], Dict[str, Any]],
                              attempts_of: Callable[[Dict[str, Any], str], List[str]],
                              schedules_of: Callable[..., Dict[str, Dict[str, Any]]],
                              plan_for: Callable[[Dict[str, Any], str, str, Dict[str, Any]],
                                                 Dict[str, Any]],
                              ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Manifest/schedule/plain/control preflight of one process report (#541).

    Timing's copy omitted the sham ``consumed_replacements == []`` guard
    signal always had, even though the shared worker records the field
    identically for both families; the shared body refuses it for both.
    Returns the campaign binding and the plain record the caller's report
    and cell validation read.
    """
    require_match(manifest)
    binding = binding_of(manifest, manifest_sha256)
    expected = attempts_of(manifest, case_id)
    _require(list(records) == expected,
             "attempt inventory differs from the expected order: " + ", ".join(records))
    _require(list(schedules) == [name for name in expected if name not in ("plain", "clean_rerun")],
             "schedule inventory differs from the expected attempts")
    fresh = schedules_of(manifest, manifest_sha256, case_id, repeat, document, inventory, catalog)
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
        require_identical(plain, record, name, IDENTITY_KEYS)
    _require(records["clean_rerun"]["events"] == [], "clean rerun recorded events")
    mutations.validate_events(plan_for(manifest, "empty", case_id, catalog), records["empty"]["events"])
    mutations.validate_events(plan_for(manifest, "sham", case_id, catalog), records["sham"]["events"])
    sham_events = records["sham"]["events"]
    _require(len(sham_events) == 1 and sham_events[0]["status"] == "ineffective"
             and sham_events[0]["sham"] is True, "sham event must be one sham ineffective event")
    _require(records["sham"].get("consumed_replacements", []) == [], "sham consumed a replacement")
    return binding, plain


def validate_applied_cell(cell: Dict[str, Any], name: str, record: Dict[str, Any],
                          schedule: Dict[str, Any], plan: Dict[str, Any], plain: Dict[str, Any],
                          plain_digests: Dict[str, str],
                          realised: List[Tuple[str, Dict[str, str]]],
                          read_bytes: Callable[[str], bytes], *,
                          dataflow: Dict[str, List[str]], parameter_roots: Dict[str, str],
                          check_parameter_event: Callable[[Dict[str, Any], Dict[str, Any],
                                                           Dict[str, Any]], None],
                          ) -> Optional[Tuple[Dict[Tuple[str, str], Dict[str, Any]], Dict[str, Any]]]:
    """Event, consumed-replacement and base-invariance validation of one cell (#541).

    Timing's copy had silently dropped the duplicate and undeclared-instance
    ``consumed_replacements`` guards; the shared body refuses both for both
    families. Returns ``None`` after recording an early-exit outcome
    (``errored``, ``refused``, ``open_ineffective``), or the consumed
    evidence map and the four-key base invariance for the caller to extend
    with its family-specific keys and detectors.
    """
    _require(record["state_restored"] is True, "attempt did not restore state: " + name)
    if record["errored"] is not None:
        declared = schedule["declared_order"]
        observed = [event["instance_id"] for event in record["events"]]
        _require(observed == declared[: len(observed)] and observed
                 and record["events"][-1]["status"] == "errored",
                 "errored attempt events are not an ordered prefix ending in errored: " + name)
        cell["outcome"] = "errored"
        return None
    mutations.validate_events(plan, record["events"])
    statuses = set(cell["events"])
    if "refused" in statuses or "errored" in statuses:
        cell["outcome"] = "refused" if "refused" in statuses else "errored"
        return None
    if statuses != {"applied"}:
        cell["outcome"] = "open_ineffective"
        return None
    consumed = {(item["instance_id"], item["trace"]): item
                for item in record.get("consumed_replacements", [])}
    _require(len(consumed) == len(record.get("consumed_replacements", [])),
             "duplicate consumed replacement evidence: " + name)
    by_id = {item["instance_id"]: item for item in schedule["module_mutations"]}
    swaps = {item["instance_id"]: item for item in schedule["parameter_swaps"]}
    _require(set(key[0] for key in consumed) <= set(by_id),
             "consumed replacement evidence for an undeclared instance: " + name)
    previous: Dict[str, str] = {}
    for event in record["events"]:
        if event["instance_id"] in swaps:
            check_parameter_event(event, swaps[event["instance_id"]], record)
        else:
            check_module_event(event, by_id[event["instance_id"]], record, plain, consumed,
                               read_bytes, previous)
    digests = inventory_digests(record)
    allowed = blast_radius(realised, dataflow, parameter_roots)
    seams = seam_traces(realised)
    siblings = sorted(trace for trace in plain_digests if trace not in allowed)
    base_invariance = {
        "original_retained_at_seam": all(digests[t] == plain_digests[t] for t in seams),
        "siblings_unchanged": [t for t in siblings if digests[t] != plain_digests[t]] == [],
        "undeclared_slots_unchanged": all(
            digest == plain["slot_sha256"][index]
            for index, digest in enumerate(record["slot_sha256"])
            if index != schedule["campaign"]["case"]["batch_slot"]
        ),
        "invocation_counts_unchanged": record["invocation_counts"] == plain["invocation_counts"],
    }
    return consumed, base_invariance


def evaluate_mandatory_detectors(cell: Dict[str, Any], entry: Dict[str, Any],
                                 record: Dict[str, Any], records: Dict[str, Dict[str, Any]],
                                 plain_samples: Dict[str, List[float]],
                                 controls: Dict[str, Dict[str, str]], document: Dict[str, Any],
                                 samples_of: Callable[[Dict[str, Any], str], List[float]],
                                 ) -> bool:
    """Mandatory paired-exactness detectors of one applied cell (#541).

    Every control attempt must PASS on each compared trace before the cell's
    own rows are read. ``detected`` requires a non-empty row set in which
    every row failed with a framing match and a non-zero error -- timing's
    copy dropped the non-empty guard, so an empty row set was vacuously
    detected. Rows carry the entry's ``detector_ids``.
    """
    for trace in entry["runtime_truth"]["compared_traces"]:
        if trace not in plain_samples:
            plain_samples[trace] = samples_of(records["plain"], trace)
        rate = registry_trace(document, trace)["rate_hz"]
        if trace not in controls:
            controls[trace] = {
                control: paired_exactness(plain_samples[trace], samples_of(records[control], trace),
                                          rate, trace)["verdict"]
                for control in ("empty", "sham", "clean_rerun")
            }
            _require(set(controls[trace].values()) == {"PASS"},
                     "control detector did not PASS on " + trace)
        row = paired_exactness(plain_samples[trace], samples_of(record, trace), rate, trace)
        row["detector_ids"] = list(entry["detector_ids"])
        cell["mandatory_detectors"].append(row)
    return bool(cell["mandatory_detectors"]) and all(
        row["verdict"] == "FAIL" and row["framing_match"] == 1
        and row["max_abs_error"] not in (None, 0) for row in cell["mandatory_detectors"])
