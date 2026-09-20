#!/usr/bin/env python3
"""Composed whole-voice float conformance for issue #43.

Stdlib-only. Renders the composed independent float model
(``torchsynth_voice.float_voice``) end to end from resolved requests and
publishes the four-class conformance record (structural identity, validity,
coverage, numeric errors) against the pinned Voice evidence committed in
this repository. The per-module matches are settled landed evidence
(#129/#130/#131) and are composed here, never re-proved: the record binds
their committed records by digest and adds only the cross-module seams.

Cases and declared comparisons:

- five pinned source fixtures (``tests/fixtures/float-sources/``): the
  composed model is rendered from each case's committed observed maps and
  noise identity, then compared at the two pinned seams with committed raw
  buffers — ``control_upsample.vco_{1,2}_pitch`` under the landed
  control-path rubric and ``vco_{1,2}.raw`` under the landed per-case
  float-sources limits; ``noise.raw`` is exact-bytes;
- three pinned directed normalization cases (``normalization:above``,
  ``below``, ``tie``): composed from the committed base map plus overrides;
  the keyboard scalars, ``noise.raw``, ``mixer.peak`` and ``mixer.gain``
  digests and the branch/byte relations must equal the release-era capture
  bindings in ``sim/reference/trace-capture.json`` exactly; the mix and
  control buffers are declared-metrics rows whose paired comparison needs
  the operator's raw store, so those rows are recorded store-gated, never
  passed here;
- two development receipts (``global-0``, ``global-6``): the corpus
  resolver owns their physical maps and the raw buffers live in the
  operator's store; they are retained as receipt links only;
- three preregistered composed wiring mutations (swapped VCO pitch
  routing, LFO/ADSR control-rate confusion, normalization before the mix),
  each of which must fail its named rows on its gate case and localize to
  its boundary; a mutation that does not fail is a defect of the control.

With ``--check`` the committed record is validated statically: input
digests, fixture digests, digest bindings, mutation outcomes and class
separation — no renders. The default mode re-renders every decided row and
rewrites the record with ``--record``. Store-gated rows are never issued a
verdict here: a row that has not run is never reported as a pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import float_interfaces as fi  # noqa: E402
from torchsynth_voice import float_voice as fv  # noqa: E402
from torchsynth_voice import trace_registry as tr  # noqa: E402
from torchsynth_voice.float_sources import NoiseSource, f32le_values  # noqa: E402
from torchsynth_voice.paired_metrics import (  # noqa: E402
    Limit,
    Rubric,
    compare_paired,
)

RECORD_SCHEMA = "torchsynth-float-voice-conformance"
RECORD_VERSION = "float-voice-v1"

TRACE_CAPTURE = ROOT / "sim/reference/trace-capture.json"
FLOAT_SOURCES_RECORD = ROOT / "sim/reference/float-sources-v1.json"
CONTROL_PATH_RECORD = ROOT / "sim/reference/control-path-float-v1.json"
RUBRIC = ROOT / "spec/reference/control-path-rubric-v1.json"
DIRECTED = ROOT / "spec/reference/directed-voice-v1.json"
FIXTURE_ROOT = ROOT / "tests/fixtures/float-sources"
NOISE_STREAMS = FIXTURE_ROOT / "noise-streams.json"

FIXTURE_CASES = (
    "boundary:vco_1.initial_phase:upper",
    "boundary:vco_1.mod_depth:upper",
    "boundary:vco_1.tuning:upper",
    "boundary:vco_2.mod_depth:upper",
    "waveform:vco_2:saw",
)
NORMALIZATION_CASES = ("normalization:above", "normalization:below", "normalization:tie")
DEVELOPMENT_RECEIPTS = ("global-0", "global-6")

FIXTURE_REF_TRACES = (
    "control_upsample.vco_1_pitch",
    "control_upsample.vco_2_pitch",
    "vco_1.raw",
    "vco_2.raw",
)

CLASSES = ("structural-identity", "validity", "coverage", "numeric-error")


def sha256_of(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest_of_values(values):
    return hashlib.sha256(fv.f32le_bytes(values)).hexdigest()


def load_json(path):
    return json.loads(path.read_bytes())


def load_rubric_limits():
    """Landed control-path rubric limits for the composed upsample rows."""

    document = load_json(RUBRIC)
    limits = {}
    for name in ("control_upsample.vco_1_pitch", "control_upsample.vco_2_pitch"):
        item = document["traces"][name]
        limits[name] = Limit(
            item["limits"]["max_abs_error"]["expected"],
            item["limits"]["max_abs_error"]["tolerance"],
            item["limits"]["max_abs_error"]["unit"],
            "reuse of landed " + str(item["rubric_id"]) + " on the composed path",
        )
    return limits


def load_source_limits():
    """Landed float-sources per-case limits for the composed VCO rows."""

    document = load_json(FLOAT_SOURCES_RECORD)
    return {
        case_id: Limit(
            0,
            limit,
            "amplitude",
            "reuse of landed float-sources-v1 per-case limit",
        )
        for case_id, limit in document["limits"].items()
    }


def build_request(normalized, physical, sound_index, noise_slot, noise_sha):
    samples = NoiseSource.resolve(sound_index)
    return fi.ResolvedRequest(
        sound_index,
        normalized,
        {
            "seed": 13,
            "slot": noise_slot,
            "sample_count": 176400,
            "sha256": noise_sha if noise_sha is not None else hashlib.sha256(samples).hexdigest(),
            "samples": samples,
        },
        physical=physical,
        execution_status="canonical-batched",
    )


def fixture_request(case_id):
    directory = FIXTURE_ROOT / case_id
    params = load_json(directory / "params.json")
    request = build_request(
        params["normalized_by_name"],
        params["physical_by_name"],
        params["sound_index"],
        params["noise_slot"],
        params["noise_sha256"],
    )
    references = {}
    for name in FIXTURE_REF_TRACES:
        data = (directory / (name + ".f32le")).read_bytes()
        references[name] = list(f32le_values(data))
    return params, request, references


def directed_request(case_id, directed, noise_streams):
    case = [item for item in directed["cases"] if item["id"] == case_id][0]
    normalized = {name: entry["normalized"] for name, entry in directed["base"].items()}
    physical = {name: entry["physical"] for name, entry in directed["base"].items()}
    for name, override in case["overrides"].items():
        normalized[name] = override["normalized"]
        physical[name] = override["physical"]
    slot = str(0)
    request = build_request(normalized, physical, 0, 0, noise_streams["slots"][slot]["sha256"])
    return case, request


def row(cls, checkpoint, module, kind, verdict, **detail):
    item = {"class": cls, "checkpoint": checkpoint, "module": module, "kind": kind, "verdict": verdict}
    item.update(detail)
    return item


def registry_attributes():
    return {entry["name"]: entry for entry in tr.load_registry()["traces"]}


def structural_row(checkpoint, values, attributes):
    entry = attributes[checkpoint]
    expected_count = entry["sample_count"]
    if expected_count is None:
        expected_count = 1
    problems = []
    if len(values) != expected_count:
        problems.append("length %d != %d" % (len(values), expected_count))
    declared_shape = entry["shape"]
    if declared_shape == [] and len(values) != 1:
        problems.append("scalar shape holds %d values" % len(values))
    if declared_shape == [1] and len(values) != 1:
        problems.append("whole-clip shape holds %d values" % len(values))
    round_trip = fv.f32le_values(fv.f32le_bytes(values))
    if list(round_trip) != list(values):
        problems.append("value is not exactly binary32")
    verdict = "PASS" if not problems else "FAIL"
    return row(
        "structural-identity",
        checkpoint,
        checkpoint.split(".")[0],
        "registry-attributes",
        verdict,
        declared_shape=declared_shape,
        declared_rate_hz=entry["rate_hz"],
        declared_dtype=entry["dtype"],
        declared_encoding=entry["encoding"],
        detail=None if not problems else "; ".join(problems),
    )


def coverage_row(checkpoint, composed_order):
    present = checkpoint in composed_order
    return row(
        "coverage",
        checkpoint,
        checkpoint.split(".")[0],
        "checkpoint-present",
        "PASS" if present else "FAIL",
        detail=None if present else "missing from the composed render",
    )


def paired_numeric_row(case_id, name, reference, candidate, limit):
    measurement = compare_paired(
        reference,
        candidate,
        reference_rate_hz=44100,
        candidate_rate_hz=44100,
        unit="amplitude",
        window_samples=len(reference),
    )
    error = measurement["metrics"]["max_abs_error"]["value"]
    verdict = "PASS" if error <= limit.tolerance else "FAIL"
    return row(
        "numeric-error",
        name,
        name.split(".")[0],
        "paired-declared-metrics",
        verdict,
        case=case_id,
        max_abs_error=error,
        limit=limit.tolerance,
        limit_source=limit.source,
        detail=None if verdict == "PASS" else "max_abs_error %.6g exceeds %.6g" % (error, limit.tolerance),
    )


def exact_numeric_row(case_id, name, candidate_values, expected_digest, label):
    observed = digest_of_values(candidate_values)
    verdict = "PASS" if observed == expected_digest else "FAIL"
    return row(
        "numeric-error",
        name,
        name.split(".")[0],
        "exact-digest",
        verdict,
        case=case_id,
        binding=label,
        observed=observed,
        expected=expected_digest,
        detail=None if verdict == "PASS" else "digest mismatch against " + label,
    )


def store_gated_row(case_id, name, file_name, digest):
    return row(
        "numeric-error",
        name,
        name.split(".")[0],
        "store-gated-paired",
        "STORE-GATED",
        case=case_id,
        raw_file=file_name,
        raw_sha256=digest,
        detail=(
            "declared-metrics paired comparison requires the raw captured"
            " buffer; retained as a receipt link, never decided here"
        ),
    )


def case_validity_rows(case_id, request, rendered, diagnostics, captured_branch=None):
    rows = []
    noise_bytes = request.noise["samples"]
    same_noise = fv.f32le_bytes(rendered["noise.raw"]) == noise_bytes
    rows.append(row(
        "validity", "noise.raw", "noise", "exact-bytes-identity",
        "PASS" if same_noise else "FAIL",
        case=case_id,
        detail=None if same_noise else "composed noise.raw is not the resolved input bytes",
    ))
    gain_expected = [fv.fm.derived_gain(rendered["mixer.peak"][0])]
    gain_ok = rendered["mixer.gain"] == gain_expected
    rows.append(row(
        "validity", "mixer.gain", "mixer", "derived-gain-rule",
        "PASS" if gain_ok else "FAIL",
        case=case_id,
        detail=None if gain_ok else "mixer.gain is not the derived reciprocal diagnostic",
    ))
    branch = diagnostics["normalized_branch"]
    relation_ok = (rendered["mixer.output"] == rendered["mixer.pre_normalization"]) == (not branch)
    rows.append(row(
        "validity", "mixer.output", "mixer", "branch-byte-relation",
        "PASS" if relation_ok else "FAIL",
        case=case_id,
        normalized_branch=branch,
        detail=None if relation_ok else "bypass/division byte relation contradicts the branch decision",
    ))
    if captured_branch is not None:
        branch_ok = branch == captured_branch["peak_gt_one"]
        rows.append(row(
            "validity", "mixer.peak", "mixer", "captured-branch-agreement",
            "PASS" if branch_ok else "FAIL",
            case=case_id,
            composed_branch=branch,
            captured_branch=captured_branch["peak_gt_one"],
            detail=None if branch_ok else "composed branch disagrees with the captured branch",
        ))
        captured_relation_bypass = (
            captured_branch["pre_normalization_sha256"]
            == captured_branch["output_sha256"]
        )
        relation_agrees = (not branch) == captured_relation_bypass
        rows.append(row(
            "validity", "mixer.output", "mixer", "captured-byte-relation-agreement",
            "PASS" if relation_agrees else "FAIL",
            case=case_id,
            composed_relation="bypass-preserves" if not branch else "division-applied",
            captured_relation=(
                "bypass-preserves" if captured_relation_bypass else "division-applied"
            ),
            detail=None if relation_agrees else "composed byte relation disagrees with the captured digests",
        ))
    return rows


def endpoint_rows(composed):
    rows = []
    for route in tr.ROUTES:
        control = composed["mod_matrix." + route]
        upsampled = composed["control_upsample." + route]
        ok = upsampled[0] == control[0] and upsampled[-1] == control[-1]
        rows.append(row(
            "validity",
            "control_upsample." + route,
            "control_upsample",
            "endpoint-exact",
            "PASS" if ok else "FAIL",
            detail=None if ok else "upsampled endpoints are not exact copies of the control column",
        ))
    return rows


def evaluate_case(case_id, request, rendered, diagnostics, elapsed):
    attributes = registry_attributes()
    order = fv.voice_checkpoints()
    rows = []
    for name in order:
        rows.append(coverage_row(name, list(rendered)))
    for name in order:
        rows.append(structural_row(name, rendered[name], attributes))
    rows.extend(case_validity_rows(case_id, request, rendered, diagnostics))
    rows.extend(endpoint_rows(rendered))
    return {
        "case_id": case_id,
        "render_seconds": elapsed,
        "rows": rows,
    }


def fixture_case(case_id, rubric_limits, source_limits):
    params, request, references = fixture_request(case_id)
    started = time.time()
    rendered, diagnostics = fv.FloatVoiceModel(request).render()
    elapsed = time.time() - started
    case = evaluate_case(case_id, request, rendered, diagnostics, elapsed)
    case["case_class"] = "pinned-fixture"
    case["request_provenance"] = {
        "params_json_sha256": sha256_of(FIXTURE_ROOT / case_id / "params.json"),
        "sound_index": params["sound_index"],
        "noise_slot": params["noise_slot"],
        "noise_sha256": params["noise_sha256"],
        "physical_class": "measured-observation (committed observed maps)",
    }
    case["raw_artifact_links"] = {
        name: {
            "path": "tests/fixtures/float-sources/%s/%s.f32le" % (case_id, name),
            "sha256": sha256_of(FIXTURE_ROOT / case_id / (name + ".f32le")),
            "size_bytes": (FIXTURE_ROOT / case_id / (name + ".f32le")).stat().st_size,
        }
        for name in FIXTURE_REF_TRACES
    }
    for name in FIXTURE_REF_TRACES:
        if name.startswith("control_upsample."):
            limit = rubric_limits[name]
        else:
            limit = source_limits[case_id]
        case["rows"].append(
            paired_numeric_row(case_id, name, references[name], rendered[name], limit)
        )
    noise_streams = load_json(NOISE_STREAMS)
    slot = str(params["noise_slot"] % 32)
    case["rows"].append(
        exact_numeric_row(
            case_id,
            "noise.raw",
            rendered["noise.raw"],
            noise_streams["slots"][slot]["sha256"],
            "noise-streams slot %s" % slot,
        )
    )
    return case


def normalization_case(case_id, directed, noise_streams, capture):
    case_record, request = directed_request(case_id, directed, noise_streams)
    capture_case = [item for item in capture["cases"] if item["case"]["id"] == case_id][0]
    inventory = {entry["name"]: entry for entry in capture_case["full_inventory"]}
    branch = capture_case["normalization_branch"]
    started = time.time()
    rendered, diagnostics = fv.FloatVoiceModel(request).render()
    elapsed = time.time() - started
    case = evaluate_case(case_id, request, rendered, diagnostics, elapsed)
    case["case_class"] = "pinned-directed"
    case["request_provenance"] = {
        "directed_id": case_id,
        "sound_index": 0,
        "noise_slot": 0,
        "physical_class": "measured-observation (committed base map + directed overrides)",
        "normalization_target_peak": case_record["normalization_target"]["target_peak"],
    }
    case["rows"].extend(case_validity_rows(case_id, request, rendered, diagnostics, branch))
    case["rows"].append(
        exact_numeric_row(
            case_id, "keyboard.midi_f0", rendered["keyboard.midi_f0"],
            inventory["keyboard.midi_f0"]["sha256"], "trace-capture inventory",
        )
    )
    case["rows"].append(
        exact_numeric_row(
            case_id, "keyboard.duration", rendered["keyboard.duration"],
            inventory["keyboard.duration"]["sha256"], "trace-capture inventory",
        )
    )
    case["rows"].append(
        exact_numeric_row(
            case_id, "noise.raw", rendered["noise.raw"],
            inventory["noise.raw"]["sha256"], "trace-capture inventory",
        )
    )
    case["rows"].append(
        exact_numeric_row(
            case_id, "mixer.peak", rendered["mixer.peak"],
            branch["peak_sha256"], "captured normalization branch",
        )
    )
    case["rows"].append(
        exact_numeric_row(
            case_id, "mixer.gain", rendered["mixer.gain"],
            inventory["mixer.gain"]["sha256"], "trace-capture inventory",
        )
    )
    target = case_record["normalization_target"]["target_peak"]
    peak = rendered["mixer.peak"][0]
    target_row = row(
        "numeric-error",
        "mixer.peak",
        "mixer",
        "directed-target-exact",
        "PASS" if peak == target else "FAIL",
        case=case_id,
        composed_peak=peak,
        directed_target=target,
        detail=None if peak == target else "composed peak is not the exact directed binary32 target",
    )
    case["rows"].append(target_row)
    case["raw_artifact_links"] = {
        entry["name"]: {"raw_file": entry["file"], "sha256": entry["sha256"]}
        for entry in capture_case["full_inventory"]
    }
    case["store_receipt"] = {
        "capture_record": "sim/reference/trace-capture.json",
        "capture_record_sha256": sha256_of(TRACE_CAPTURE),
        "runtime_profile": capture.get("runtime_profile", "release-mkl-compatible-v1"),
        "note": (
            "raw f32le buffers for the 32 whole-voice traces remain in the"
            " operator store; paired declared-metrics rows for the mix and"
            " control buffers are store-gated receipts, not decided here"
        ),
    }
    case["store_gated_rows"] = [
        store_gated_row(case_id, entry["name"], entry["file"], entry["sha256"])
        for entry in capture_case["full_inventory"]
        if entry["name"]
        not in ("noise.raw", "keyboard.midi_f0", "keyboard.duration", "mixer.peak", "mixer.gain")
    ]
    return case


def receipt_cases(capture):
    cases = []
    for case_id in DEVELOPMENT_RECEIPTS:
        capture_case = [item for item in capture["cases"] if item["case"]["id"] == case_id][0]
        cases.append({
            "case_id": case_id,
            "case_class": "development-receipt",
            "rows": [],
            "store_receipt": {
                "capture_record": "sim/reference/trace-capture.json",
                "capture_record_sha256": sha256_of(TRACE_CAPTURE),
                "whole_voice_inventory_count": len(capture_case["full_inventory"]),
                "noise_sha256": capture_case["modes"]["full"]["noise_sha256"],
                "audio_sha256": capture_case["modes"]["full"]["audio_sha256"],
                "note": (
                    "development corpus index; the corpus resolver owns the"
                    " physical map and the raw buffers remain in the operator"
                    " store; the landed control-path record (#130) matched the"
                    " 96 development cases under its committed rubric"
                ),
            },
        })
    return cases


def divergence_counter(baseline, mutated):
    diverged = []
    for name, values in baseline.items():
        if mutated.get(name) != values:
            diverged.append(name)
    return diverged


def mutation_gate(mutation, gate_case_id, baseline, mutated_render, mutated_diagnostics):
    first = fv.diverged_boundary(baseline, mutated_render)
    localized = first is not None
    intact = all(
        mutated_render[name] == baseline[name]
        for name in fv.MUTATION_INTACT_TRACES[mutation]
        if name in baseline
    )
    named = {
        name: (mutated_render.get(name) != baseline.get(name))
        for name in fv.MUTATION_FAILED_TRACES[mutation]
    }
    failed_named = any(named.values())
    return {
        "mutation": mutation,
        "gate_case": gate_case_id,
        "must_fail": (
            "composed wiring fault must fail its named composed rows and"
            " localize to " + fv.MUTATION_BOUNDARIES[mutation]
        ),
        "failed": bool(failed_named and localized),
        "named_trace_divergence": named,
        "intact_rows_preserved": intact,
        "first_diverged_checkpoint": None if first is None else first[0],
        "first_diverged_module": None if first is None else first[1],
        "localized_boundary": fv.MUTATION_BOUNDARIES[mutation] if localized else None,
    }


def render_invariants():
    """Bounded reset/replay invariants of the composed model.

    Determinism: a repeated render is byte-identical. No residue: after an
    intervening different render, the first request re-renders
    byte-identically. Input immutability: the resolved request's noise
    bytes and normalized map are unchanged after every render.
    """

    first_params, first_request, _ = fixture_request("boundary:vco_1.tuning:upper")
    other_params, other_request, _ = fixture_request("waveform:vco_2:saw")
    normalized_digest = hashlib.sha256(
        json.dumps(sorted(first_request.normalized.items()), separators=(",", ":")).encode()
    ).hexdigest()
    noise_bytes = first_request.noise["samples"]
    once, _ = fv.FloatVoiceModel(first_request).render()
    twice, _ = fv.FloatVoiceModel(first_request).render()
    _, _ = fv.FloatVoiceModel(other_request).render()
    restored, _ = fv.FloatVoiceModel(first_request).render()
    unchanged = (
        first_request.noise["samples"] == noise_bytes
        and hashlib.sha256(
            json.dumps(sorted(first_request.normalized.items()), separators=(",", ":")).encode()
        ).hexdigest()
        == normalized_digest
    )
    return {
        "repeated_render_byte_identical": once == twice,
        "changed_request_then_restoration_byte_identical": once == restored,
        "request_unchanged_after_renders": unchanged,
        "note": (
            "state ownership per FLOAT-INTERFACES: the composed model holds"
            " no cross-render state; these bounded checks are executed, not"
            " assumed"
        ),
    }


def run_mutations(directed, noise_streams):
    results = []
    pitch_gate = "boundary:vco_1.mod_depth:upper"
    params, request, _ = fixture_request(pitch_gate)
    baseline, _ = fv.FloatVoiceModel(request).render()
    mutated, _ = fv.FloatVoiceModel(request, {fv.MUTATION_SWAPPED_VCO_PITCH}).render()
    results.append(
        mutation_gate(
            fv.MUTATION_SWAPPED_VCO_PITCH, pitch_gate, baseline, mutated, None
        )
    )
    mutated, _ = fv.FloatVoiceModel(request, {fv.MUTATION_LFO_ADSR_SWAP}).render()
    results.append(
        mutation_gate(
            fv.MUTATION_LFO_ADSR_SWAP, pitch_gate, baseline, mutated, None
        )
    )
    mix_gate = "normalization:above"
    _, mix_request = directed_request(mix_gate, directed, noise_streams)
    mix_baseline, _ = fv.FloatVoiceModel(mix_request).render()
    mix_mutated, _ = fv.FloatVoiceModel(
        mix_request, {fv.MUTATION_NORMALIZE_BEFORE_MIX}
    ).render()
    results.append(
        mutation_gate(
            fv.MUTATION_NORMALIZE_BEFORE_MIX, mix_gate, mix_baseline, mix_mutated, None
        )
    )
    effectiveness = {}
    for case_id in ("normalization:below", "normalization:tie"):
        _, case_request = directed_request(case_id, directed, noise_streams)
        case_baseline, _ = fv.FloatVoiceModel(case_request).render()
        case_mutated, _ = fv.FloatVoiceModel(
            case_request, {fv.MUTATION_NORMALIZE_BEFORE_MIX}
        ).render()
        effectiveness[case_id] = {
            "diverged": divergence_counter(case_baseline, case_mutated) or None,
            "note": (
                "expected ineffective at or below the branch: no division is"
                " applied either way, so byte identity is preserved"
                if not divergence_counter(case_baseline, case_mutated)
                else None
            ),
        }
    results.append({
        "mutation": fv.MUTATION_NORMALIZE_BEFORE_MIX + " branch-effectiveness",
        "by_case": effectiveness,
    })
    return results


def build_record():
    capture = load_json(TRACE_CAPTURE)
    directed = load_json(DIRECTED)
    noise_streams = load_json(NOISE_STREAMS)
    rubric_limits = load_rubric_limits()
    source_limits = load_source_limits()
    cases = []
    for case_id in FIXTURE_CASES:
        cases.append(fixture_case(case_id, rubric_limits, source_limits))
    for case_id in NORMALIZATION_CASES:
        cases.append(normalization_case(case_id, directed, noise_streams, capture))
    cases.extend(receipt_cases(capture))
    record = {
        "schema": RECORD_SCHEMA,
        "semantic_version": RECORD_VERSION,
        "schema_version": 1,
        "numeric_contract": "unbound:#53",
        "issue": 43,
        "source_commit": fi.SOURCE_COMMIT,
        "composed_policy": {
            "version": fv.CALCULATION_POLICY.version,
            "calculation_dtype": fv.CALCULATION_POLICY.calculation_dtype,
            "operation_policy": fv.CALCULATION_POLICY.operation_policy,
        },
        "composed_model": "src/torchsynth_voice/float_voice.py",
        "composes_landed_records": {
            "sources": {
                "path": "sim/reference/float-sources-v1.json",
                "sha256": sha256_of(FLOAT_SOURCES_RECORD),
            },
            "control_path": {
                "path": "sim/reference/control-path-float-v1.json",
                "sha256": sha256_of(CONTROL_PATH_RECORD),
                "capture_manifest_sha256": load_json(CONTROL_PATH_RECORD)[
                    "capture_manifest_sha256"
                ],
            },
            "whole_voice_capture": {
                "path": "sim/reference/trace-capture.json",
                "sha256": sha256_of(TRACE_CAPTURE),
            },
            "control_path_rubric": {
                "path": "spec/reference/control-path-rubric-v1.json",
                "sha256": sha256_of(RUBRIC),
            },
            "directed_fixtures": {
                "path": "spec/reference/directed-voice-v1.json",
                "sha256": sha256_of(DIRECTED),
            },
            "trace_registry": {
                "path": "spec/reference/trace-registry-v1.json",
                "registry_token": tr.registry_token(),
            },
            "parameter_inventory": {
                "path": "spec/reference/parameter-inventory-v1.json",
                "sha256": fi.inventory_sha256(),
            },
            "checkpoint_map": {
                "path": "spec/reference/float-checkpoints-v1.json",
                "registry_token": fi.load_checkpoints()["registry_token"],
            },
        },
        "checkpoint_order": fv.voice_checkpoints(),
        "render_invariants": render_invariants(),
        "class_separation": {
            "structural-identity": "registry shapes, rates, dtypes and binary32 encodability",
            "validity": (
                "exact-byte input identity, request immutability, derived-gain"
                " rule, branch byte relations, endpoint contract, mutation"
                " intact rows"
            ),
            "coverage": "every declared checkpoint present in evaluation order",
            "numeric-error": (
                "paired declared-metrics and exact-digest comparisons; each row"
                " carries its own limit and provenance; store-gated rows are"
                " retained receipts and are never decided here"
            ),
        },
        "cases": cases,
        "mutations": run_mutations(directed, noise_streams),
    }
    return record


def summarize(record):
    summary = {}
    for cls in CLASSES:
        decided = 0
        passed = 0
        failed = 0
        gated = 0
        for case in record["cases"]:
            for item in case.get("rows", []):
                if item.get("class") != cls:
                    continue
                verdict = item["verdict"]
                if verdict == "STORE-GATED":
                    gated += 1
                elif verdict == "PASS":
                    decided += 1
                    passed += 1
                else:
                    decided += 1
                    failed += 1
            for item in case.get("store_gated_rows", []):
                if item.get("class") == cls:
                    gated += 1
        summary[cls] = {
            "decided": decided,
            "pass": passed,
            "fail": failed,
            "store_gated": gated,
        }
    return summary


def static_check(path):
    record = load_json(path)
    problems = []
    if record.get("schema") != RECORD_SCHEMA:
        problems.append("unknown schema: %s" % record.get("schema"))
    if record.get("semantic_version") != RECORD_VERSION:
        problems.append("unknown semantic version")
    if record.get("numeric_contract") != "unbound:#53":
        problems.append("numeric contract must stay unbound")
    composed = record.get("composes_landed_records", {})
    bindings = {
        "sources": FLOAT_SOURCES_RECORD,
        "control_path": CONTROL_PATH_RECORD,
        "whole_voice_capture": TRACE_CAPTURE,
        "control_path_rubric": RUBRIC,
        "directed_fixtures": DIRECTED,
    }
    for key, path_object in bindings.items():
        entry = composed.get(key, {})
        expected = entry.get("sha256")
        observed = sha256_of(path_object)
        if expected != observed:
            problems.append("binding %s: record %s vs tree %s" % (key, expected, observed))
    if composed.get("trace_registry", {}).get("registry_token") != tr.registry_token():
        problems.append("registry token mismatch")
    if composed.get("checkpoint_map", {}).get("registry_token") != fi.load_checkpoints()["registry_token"]:
        problems.append("checkpoint map token mismatch")
    if record.get("checkpoint_order") != fv.voice_checkpoints():
        problems.append("checkpoint order mismatch")
    mutation_results = [
        item for item in record.get("mutations", []) if "failed" in item
    ]
    for item in mutation_results:
        if not item["failed"]:
            problems.append("mutation did not fail: " + item["mutation"])
        if not item.get("intact_rows_preserved"):
            problems.append("mutation disturbed its intact rows: " + item["mutation"])
        if not item.get("localized_boundary"):
            problems.append("mutation did not localize: " + item["mutation"])
    if len(mutation_results) != 3:
        problems.append("expected three gated mutations")
    for cls in CLASSES:
        if cls not in record.get("class_separation", {}):
            problems.append("missing class separation entry: " + cls)
    invariants = record.get("render_invariants", {})
    for key in (
        "repeated_render_byte_identical",
        "changed_request_then_restoration_byte_identical",
        "request_unchanged_after_renders",
    ):
        if not invariants.get(key):
            problems.append("render invariant failed: " + key)
    for case in record.get("cases", []):
        for item in case.get("rows", []):
            if item.get("class") not in CLASSES:
                problems.append("row outside the declared classes: %s %s" % (case.get("case_id"), item.get("checkpoint")))
            if item.get("verdict") not in ("PASS", "FAIL", "STORE-GATED"):
                problems.append("row without a decided or gated verdict")
        for item in case.get("store_gated_rows", []):
            if item.get("verdict") != "STORE-GATED":
                problems.append("store-gated row is not marked STORE-GATED")
    if problems:
        for problem in problems:
            print("CHECK FAIL:", problem)
        return 1
    summary = summarize(record)
    print("static record check OK")
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", type=Path, help="statically validate the committed record; no renders")
    parser.add_argument("--record", type=Path, help="write the regenerated record here")
    args = parser.parse_args(argv)
    if args.check is not None:
        return static_check(args.check)
    started = time.time()
    record = build_record()
    summary = summarize(record)
    print(json.dumps(summary, indent=1, sort_keys=True))
    for item in record["mutations"]:
        if "failed" in item:
            print(
                "mutation %-24s failed=%s intact=%s boundary=%s"
                % (
                    item["mutation"],
                    item["failed"],
                    item["intact_rows_preserved"],
                    item["localized_boundary"],
                )
            )
    failures = [
        (case["case_id"], item)
        for case in record["cases"]
        for item in case.get("rows", [])
        if item["verdict"] == "FAIL"
    ]
    for case_id, item in failures:
        print(
            "FAIL %s %s [%s/%s]: %s"
            % (
                case_id,
                item["checkpoint"],
                item["class"],
                item["kind"],
                item.get("detail"),
            )
        )
    if args.record is not None:
        args.record.parent.mkdir(parents=True, exist_ok=True)
        args.record.write_text(
            json.dumps(record, indent=1, sort_keys=True, allow_nan=False) + "\n"
        )
        print("record written:", args.record)
    print("elapsed %.1fs" % (time.time() - started))
    failed_mutations = [
        item for item in record["mutations"] if "failed" in item and not item["failed"]
    ]
    if failures or failed_mutations:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
