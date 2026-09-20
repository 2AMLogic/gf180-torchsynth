#!/usr/bin/env python3
"""Strata and measurement-coverage audit of the repeat-qualified corpus (#21).

Consumes the #19 first-run receipt and #20 repeat receipt through the landed
read-only validator (``audit_development_corpus.audit_receipt``), then
independently re-qualifies the pair per identity (byte-identical index
documents, byte-equal metadata and audio across both stores) before computing
any coverage. Coverage families and their literal thresholds are frozen in
``spec/reference/corpus-audit-rules-v1.json``; this tool never invents a
cutoff, never runs an estimator that no landed qualification covers, and never
derives a quality score. Per-case rows carry measured values or null with an
explicit status and reason; per-family status counts reconcile to the fixed
96-identity denominator; outliers are kept and linked, never filtered.

Refusals are structured: holdout identities are refused before any store
access, missing store directories are never created, and incomplete input
qualification retains diagnostics and refuses the qualified-corpus audit
instead of emitting partial coverage verdicts. Stdlib only; never renders,
writes into a store, or repairs.
"""

import argparse
import json
import math
import os
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from audit_development_corpus import DEVELOPMENT, audit_receipt  # noqa: E402
from torchsynth_voice.artifact_renderer import digest, json_bytes  # noqa: E402
from torchsynth_voice.artifacts import ValidationError, loads, verify_sha256  # noqa: E402
from torchsynth_voice.corpus import read_bytes as _store_read_bytes  # noqa: E402

SCHEMA = "torchsynth-development-corpus-coverage"
RULES_SCHEMA = "torchsynth-corpus-audit-rules"
AUDIO_SAMPLES = 176400
AUDIO_BYTES = AUDIO_SAMPLES * 4
SAMPLE_RATE_HZ = 44100.0
RULES_PATH = "spec/reference/corpus-audit-rules-v1.json"
INVENTORY_PATH = "spec/reference/parameter-inventory-v1.json"
DIRECTED_COVERAGE_PATH = "spec/reference/directed-coverage-v1.json"
FAMILIES = (
    "audio_facts",
    "normalization_seam",
    "parameter_strata",
    "pitch_stability",
    "noise_contribution_observed",
    "envelope_stages_observed",
    "frequency_ranges_observed",
)
GAP_LINKED_DIRECTED = {
    "pitch_stability": (
        "sources.vco_1:isolated",
        "sources.vco_2:isolated",
        "waveforms",
    ),
    "noise_contribution_observed": (
        "sources.noise:isolated",
        "routes mod_matrix.*->noise_amp:isolated",
        "special silence/near-silence",
    ),
    "envelope_stages_observed": (
        "envelopes adsr_*:* with planned traces adsr_1.output/adsr_2.output",
    ),
    "frequency_ranges_observed": (
        "waveforms",
        "sources.vco_1:isolated",
        "sources.vco_2:isolated",
    ),
}
GAP_RECOMMENDATION = {
    "pitch_stability": "Propose a versioned directed fixture that renders "
    "executed isolated-VCO cases (current entries are planned, audio_render "
    "not_run) plus a separately qualified mixed-output pitch-qualification "
    "fixture before any corpus pitch-stability claim.",
    "noise_contribution_observed": "Link planned #17 noise-isolated and "
    "noise_amp route fixtures and coordinate with the trace owner on captured "
    "post-VCA/weighted pre-normalization source signals; do not instrument "
    "the renderer here.",
    "envelope_stages_observed": "Link planned #17 ADSR cut/long/zero-stage "
    "fixtures with executed ADSR output traces; observed stage lengths stay "
    "unmeasured until such captures exist.",
    "frequency_ranges_observed": "Propose a versioned directed fixture with "
    "executed isolated-VCO renders before any corpus frequency-range claim; "
    "no qualified mixed-output spectral method exists today.",
}


def require(condition, message):
    if not condition:
        raise ValidationError("corpus-coverage audit: " + message)


def _read_store_file(root, relative, expected_sha256=None):
    data = _store_read_bytes(Path(root) / relative)
    if expected_sha256 is not None:
        verify_sha256(data, expected_sha256)
    return data


def _identity_values(peak_values):
    peak_abs = -1.0
    peak_index = 0
    for index, value in enumerate(peak_values):
        magnitude = abs(value)
        if magnitude > peak_abs:
            peak_abs = magnitude
            peak_index = index
    return peak_abs, peak_index


def audio_facts(data, rules):
    """Compute declared raw audio facts from complete original bytes."""
    property_rules = rules["families"]["audio_facts"]["properties"]
    require(
        len(data) == AUDIO_BYTES,
        "audio byte count %d != %d" % (len(data), AUDIO_BYTES),
    )
    values = struct.unpack("<%df" % AUDIO_SAMPLES, data)
    nonfinite = sum(1 for value in values if not math.isfinite(value))
    if nonfinite:
        raise ValidationError(
            "corpus-coverage audit: %d nonfinite samples" % nonfinite
        )
    rms = math.sqrt(math.fsum(value * value for value in values) / AUDIO_SAMPLES)
    dc_mean = math.fsum(values) / AUDIO_SAMPLES
    peak_abs, peak_index = _identity_values(values)
    peak_signed = values[peak_index]
    beyond = sum(1 for value in values if abs(value) > 1.0)
    at_full = sum(1 for value in values if abs(value) >= 1.0)
    signed_zero = sum(
        1 for value in values if value == 0.0 and math.copysign(1.0, value) < 0.0
    )
    exact_silence = all(value == 0.0 for value in values)
    near_rule = property_rules["near_silence"]
    near_silence = (
        rms < near_rule["rms_cutoff"] and peak_abs < near_rule["peak_cutoff"]
    )
    return dict(
        finite_sample_count=AUDIO_SAMPLES,
        exact_silence=exact_silence,
        signed_zero_sample_count=signed_zero,
        rms=rms,
        dc_mean=dc_mean,
        peak_abs=peak_abs,
        peak_signed=peak_signed,
        peak_index=peak_index,
        peak_time_seconds=peak_index / SAMPLE_RATE_HZ,
        beyond_full_scale_count=beyond,
        at_full_scale_count=at_full,
        near_silence=near_silence,
    )


def _cross_check_summaries(recorded, facts):
    """Refuse stored summary rows that disagree with recomputed facts."""
    require(
        recorded["rms"] == facts["rms"]
        and recorded["dc_mean"] == facts["dc_mean"]
        and recorded["peak_abs"] == facts["peak_abs"]
        and recorded["peak_index"] == facts["peak_index"]
        and recorded["clipped_sample_count"] == facts["beyond_full_scale_count"],
        "stored audio summary rows disagree with independently computed facts",
    )


def classify_normalization(observations, metadata_gain):
    """Classify the normalization seam from recorded producer observations."""
    if observations is None:
        return dict(
            pre_normalization_peak=None,
            normalization_gain_envelope=None,
            normalization_gain_metadata=metadata_gain,
            derived_pre_peak=None,
            classification="missing",
            status="missing",
            reason="run-envelope attempt receipt carries no observations block",
        )
    require(
        set(observations) == {
            "normalization_gain_derived",
            "pre_normalization_peak",
            "pre_normalization_sha256",
            "richer_module_facts",
        },
        "unexpected normalization observation fields",
    )
    pre_peak = observations["pre_normalization_peak"]
    envelope_gain = observations["normalization_gain_derived"]
    require(
        type(pre_peak) is float
        and type(envelope_gain) is float
        and math.isfinite(pre_peak)
        and math.isfinite(envelope_gain)
        and pre_peak >= 0.0,
        "nonfinite or negative recorded normalization seam value",
    )
    expected_gain = 1.0 / pre_peak if pre_peak > 1.0 else 1.0
    require(
        envelope_gain == expected_gain,
        "recorded normalization gain disagrees with the contract gain for "
        "pre-peak %r" % pre_peak,
    )
    require(
        metadata_gain == envelope_gain,
        "metadata normalization_gain differs from the envelope seam gain",
    )
    if pre_peak > 1.0:
        classification = "active"
        derived = 1.0 / envelope_gain
    else:
        classification = "not_applied"
        derived = None
    return dict(
        pre_normalization_peak=pre_peak,
        normalization_gain_envelope=envelope_gain,
        normalization_gain_metadata=metadata_gain,
        derived_pre_peak=derived,
        classification=classification,
        status="measured",
        reason=None,
    )


def _validate_rules(rules):
    require(
        type(rules) is dict
        and rules.get("schema") == RULES_SCHEMA
        and rules.get("schema_version") == 1,
        "rules schema mismatch",
    )
    require(
        type(rules.get("identity")) is dict
        and type(rules["identity"].get("version")) is str,
        "rules identity missing",
    )
    families = rules.get("families")
    require(
        type(families) is dict and set(families) == set(FAMILIES),
        "rules families do not match the declared audit families",
    )
    for name in FAMILIES:
        family = families[name]
        require(
            type(family.get("method")) is dict
            and type(family["method"].get("name")) is str,
            "family %s lacks a declared method" % name,
        )
        require(
            type(family.get("applicability_rule")) is str,
            "family %s lacks an applicability rule" % name,
        )
        require(
            type(family.get("unavailable_reasons")) is dict,
            "family %s lacks unavailable-reason rules" % name,
        )
    audio = families["audio_facts"]["properties"]
    for field in ("rms_cutoff", "peak_cutoff", "rms_comparison", "peak_comparison"):
        require(
            field in audio.get("near_silence", {}),
            "near_silence rule lacks declared %s" % field,
        )
    require(
        audio["near_silence"]["rms_comparison"] == "<"
        and audio["near_silence"]["peak_comparison"] == "<",
        "near_silence comparisons must be the declared strict inequalities",
    )
    bins = families["parameter_strata"].get("bins", {})
    require(
        type(bins.get("edges")) is list
        and type(bins.get("labels")) is list
        and len(bins["edges"]) == len(bins["labels"]) + 1,
        "parameter bins lack declared edges/labels",
    )
    return rules


def _bin_label(value, edges, labels):
    require(
        type(value) is float and math.isfinite(value) and 0.0 <= value <= 1.0,
        "normalized value outside the declared [0.0, 1.0] bin axis",
    )
    for index in range(len(labels)):
        lower = edges[index]
        upper = edges[index + 1]
        final = index == len(labels) - 1
        if lower <= value < upper or (final and value == upper):
            return labels[index]
    raise ValidationError("corpus-coverage audit: value not binned")


def _load_inventory():
    inventory = loads((ROOT / INVENTORY_PATH).read_bytes())
    names = [parameter["name"] for parameter in inventory["parameters"]]
    require(
        len(names) == len(set(names)) == inventory["parameter_count"] == 78,
        "parameter inventory is not the canonical 78-name set",
    )
    return inventory, names


def _load_directed_coverage():
    path = ROOT / DIRECTED_COVERAGE_PATH
    if not path.is_file():
        return dict(path=DIRECTED_COVERAGE_PATH, sha256=None, present=False)
    data = path.read_bytes()
    document = loads(data)
    return dict(
        path=DIRECTED_COVERAGE_PATH,
        sha256=digest(data),
        present=True,
        audio_render=document.get("audio_render"),
        case_count=document.get("case_count"),
    )


def _case_metadata(root, case, label):
    ref = case["artifact"]
    locator = "artifacts/" + ref["artifact_id"] + "/metadata.json"
    require(
        ref["ref"] == locator,
        "%s artifact locator mismatch: %s" % (label, ref["ref"]),
    )
    metadata_bytes = _read_store_file(root, ref["ref"], ref["sha256"])
    record = loads(metadata_bytes)
    audio_ref = record["audio"]["value"]["file"]
    audio_bytes = _read_store_file(
        root,
        "artifacts/" + ref["artifact_id"] + "/" + audio_ref["ref"],
        audio_ref["sha256"],
    )
    require(
        len(audio_bytes) == audio_ref["size_bytes"],
        "%s audio reference byte count mismatch" % label,
    )
    return metadata_bytes, record, audio_bytes


def _envelope_attempts(root, receipt):
    data = _read_store_file(
        root, receipt["run"]["envelope_ref"], receipt["run"]["envelope_sha256"]
    )
    envelope = loads(data)
    attempts = {}
    for attempt in envelope["attempts"]:
        case_id = attempt["case_id"]
        require(
            case_id not in attempts,
            "duplicate envelope attempt for %s" % case_id,
        )
        require(
            attempt["status"] == "complete" and attempt["kind"] == "render",
            "incomplete envelope attempt for %s" % case_id,
        )
        attempts[case_id] = attempt
    return envelope, attempts


def _identity_side(receipt, root, expected):
    """Strict read-only qualification of one side; no directory is created."""
    root = Path(os.path.realpath(root))
    if not root.is_dir():
        raise ValidationError(
            "store root missing (refusing to create): %s" % root
        )
    observed = audit_receipt(receipt, store=root, expected=expected)
    index_bytes = _read_store_file(
        root, receipt["run"]["index_ref"], receipt["run"]["index_sha256"]
    )
    envelope, attempts = _envelope_attempts(root, receipt)
    require(
        set(attempts) == {"global-%d" % i for i in expected},
        "envelope attempts are not exactly the expected identity set",
    )
    for case in receipt["index"]["cases"]:
        attempt = attempts[case["case_id"]]
        require(
            attempt["artifact"]["artifact_id"]
            == case["artifact"]["artifact_id"],
            "envelope attempt artifact binding mismatch for %s"
            % case["case_id"],
        )
    return dict(
        root=root,
        observed_complete=observed,
        index_bytes=index_bytes,
        envelope=envelope,
        attempts=attempts,
    )


def _receipt_identity(receipt, receipt_sha256, label):
    run = receipt["run"]
    return dict(
        label=label,
        receipt_sha256=receipt_sha256,
        run_id=run["run_id"],
        index_sha256=run["index_sha256"],
        envelope_sha256=run["envelope_sha256"],
        producer_commit=receipt["producer_git"]["commit"],
        source_commit=receipt["source_commit"],
        manifest_sha256=receipt["manifest_sha256"],
        runtime_profile=receipt.get("runtime_profile"),
    )


def _status_row(case_id, value, status, reason=None, **extra):
    row = dict(case_id=case_id, value=value, status=status, reason=reason)
    row.update(extra)
    return row


def _reconcile(rows, expected_count, family):
    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    require(
        len(rows) == expected_count,
        "family %s has %d rows, denominator is %d"
        % (family, len(rows), expected_count),
    )
    require(
        sum(counts.values()) == expected_count,
        "family %s statuses do not reconcile to the denominator" % family,
    )
    return dict(counts=counts, total=len(rows))


def audit_corpus_coverage(
    first_receipt,
    second_receipt,
    *,
    first_store,
    second_store,
    rules,
    expected=None,
    rules_sha256=None,
):
    """Full read-only coverage audit; returns the deterministic report."""
    expected = list(DEVELOPMENT if expected is None else expected)
    if expected != sorted(expected) or len(set(expected)) != len(expected):
        raise ValidationError("expected identity set must be sorted and unique")
    if any(not 0 <= i < 96 for i in expected):
        raise ValidationError(
            "holdout identities 96-127 require explicit admission; "
            "the coverage audit is development-only and no holdout payload "
            "is read"
        )
    rules = _validate_rules(rules)
    rules_bytes = json_bytes(rules)
    rules_binding = (
        digest(rules_bytes) if rules_sha256 is None else rules_sha256
    )
    if type(first_receipt) is not dict:
        first_receipt = loads(Path(first_receipt).read_bytes())
    if type(second_receipt) is not dict:
        second_receipt = loads(Path(second_receipt).read_bytes())

    qualification = dict(
        rules=dict(
            path=RULES_PATH,
            sha256=rules_binding,
            version=rules["identity"]["version"],
        ),
        receipts=dict(
            first=_receipt_identity(first_receipt, None, "first"),
            second=_receipt_identity(second_receipt, None, "repeat"),
        ),
        checks={},
    )
    sides = {}
    for label, receipt, store, key in (
        ("first_receipt_audit", first_receipt, first_store, "first"),
        ("repeat_receipt_audit", second_receipt, second_store, "second"),
    ):
        try:
            side = _identity_side(receipt, store, expected)
        except ValidationError as error:
            return dict(
                schema=SCHEMA,
                schema_version=1,
                expected_identity_count=len(expected),
                qualification=qualification,
                verdict="REFUSED",
                refusal=dict(check=label, reason=str(error)),
                limits=[
                    "Input qualification is incomplete; diagnostics are "
                    "retained and no coverage verdicts are emitted.",
                ],
            )
        sides[key] = side
        qualification["checks"][label] = dict(
            verdict="PASS",
            observed_complete=side["observed_complete"],
            store_present=True,
        )

    first, second = sides["first"], sides["second"]
    qualification["receipts"]["first"] = _receipt_identity(
        first_receipt,
        digest(json_bytes(first_receipt)),
        "first",
    )
    qualification["receipts"]["second"] = _receipt_identity(
        second_receipt,
        digest(json_bytes(second_receipt)),
        "repeat",
    )
    qualification["repeat_linkage"] = dict(
        first_run_id=first_receipt["run"]["run_id"],
        repeat_run_id=second_receipt["run"]["run_id"],
        repeat_of=second_receipt.get("repeat_of"),
        comparison_verdict_recorded=(
            second_receipt.get("comparison", {}).get("verdict")
        ),
        comparison_report_sha256_recorded=(
            second_receipt.get("comparison", {}).get("report_sha256")
        ),
    )
    producer_fields = ("producer_commit", "source_commit", "manifest_sha256")
    for field in producer_fields:
        require(
            qualification["receipts"]["first"][field]
            == qualification["receipts"]["second"][field],
            "receipts disagree on %s" % field,
        )
    index_equal = first["index_bytes"] == second["index_bytes"]
    qualification["checks"]["index_bytes_equal_across_stores"] = dict(
        verdict="PASS" if index_equal else "FAIL", index_sha256=first_receipt[
            "run"
        ]["index_sha256"]
    )
    if not index_equal:
        raise ValidationError(
            "corpus index documents differ across stores; the repeat "
            "qualification fails and no coverage verdict is emitted"
        )

    inventory, names = _load_inventory()
    inventory_by_name = {
        parameter["name"]: parameter for parameter in inventory["parameters"]
    }
    edges = rules["families"]["parameter_strata"]["bins"]["edges"]
    labels = rules["families"]["parameter_strata"]["bins"]["labels"]

    audio_rows = []
    normalization_rows = []
    parameter_rows = []
    pitch_rows = []
    noise_rows = []
    envelope_rows = []
    frequency_rows = []
    parameter_counts = {
        name: {label: 0 for label in labels} for name in names
    }
    parameter_extremes = {
        name: dict(normalized_min=None, normalized_max=None,
                   physical_min=None, physical_max=None)
        for name in names
    }
    regime_counts = {
        "lfo_1_dominant_weight": {},
        "lfo_2_dominant_weight": {},
        "vco_2_shape_bin": {label: 0 for label in labels},
    }
    repeat_metadata_equal = 0
    repeat_audio_equal = 0
    repeat_divergent = []

    for case in first_receipt["index"]["cases"]:
        case_id = case["case_id"]
        if case["status"] != "complete":
            for rows, family in (
                (audio_rows, "audio_facts"),
                (normalization_rows, "normalization_seam"),
                (parameter_rows, "parameter_strata"),
                (pitch_rows, "pitch_stability"),
                (noise_rows, "noise_contribution_observed"),
                (envelope_rows, "envelope_stages_observed"),
                (frequency_rows, "frequency_ranges_observed"),
            ):
                rows.append(
                    _status_row(
                        case_id,
                        None,
                        "missing",
                        "index case status is %s" % case["status"],
                    )
                )
            continue
        first_metadata, record, first_audio = _case_metadata(
            first["root"], case, "first"
        )
        second_case = None
        for candidate in second_receipt["index"]["cases"]:
            if candidate["case_id"] == case_id:
                second_case = candidate
                break
        require(
            second_case is not None,
            "repeat side lacks identity %s" % case_id,
        )
        second_metadata, second_record, second_audio = _case_metadata(
            second["root"], second_case, "second"
        )
        metadata_equal = first_metadata == second_metadata
        audio_equal = first_audio == second_audio
        if metadata_equal:
            repeat_metadata_equal += 1
        if audio_equal:
            repeat_audio_equal += 1
        if not (metadata_equal and audio_equal):
            repeat_divergent.append(
                dict(
                    case_id=case_id,
                    metadata_equal=metadata_equal,
                    audio_equal=audio_equal,
                )
            )
        inputs = record["inputs"]["value"]
        audio_value = record["audio"]["value"]
        try:
            facts = audio_facts(first_audio, rules)
            recorded = dict(
                rms=audio_value["rms"],
                dc_mean=audio_value["dc_mean"],
                peak_abs=audio_value["peak_abs"],
                peak_index=audio_value["peak_index"],
                clipped_sample_count=audio_value["clipped_sample_count"],
                normalization_gain=audio_value["normalization_gain"],
            )
            _cross_check_summaries(recorded, facts)
            facts.update(
                case_id=case_id,
                producer_recorded=recorded,
                status="measured",
                reason=None,
            )
            audio_rows.append(facts)
        except ValidationError as error:
            audio_rows.append(
                _status_row(case_id, None, "error", str(error))
            )
        attempt = first["attempts"][case_id]
        observations = attempt["receipt"].get("observations")
        try:
            normalization_rows.append(
                dict(
                    classify_normalization(
                        observations, audio_value["normalization_gain"]
                    ),
                    case_id=case_id,
                )
            )
        except ValidationError as error:
            normalization_rows.append(
                _status_row(case_id, None, "error", str(error))
            )
        capture = inputs.get("requested_traces")
        require(
            type(capture) is list,
            "case %s lacks a declared requested-trace inventory" % case_id,
        )
        pitch_rows.append(
            _status_row(
                case_id,
                None,
                "unqualified",
                rules["families"]["pitch_stability"]["unavailable_reasons"][
                    "unqualified"
                ],
                exact_silence=(
                    audio_rows[-1]["exact_silence"]
                    if audio_rows[-1]["status"] == "measured"
                    else None
                ),
                requested_traces=sorted(capture),
            )
        )
        noise_rows.append(
            _status_row(
                case_id,
                None,
                "missing",
                rules["families"]["noise_contribution_observed"][
                    "unavailable_reasons"
                ]["missing"],
                intended_control_mixer_noise_normalized=(
                    inputs["parameters"]["normalized_by_name"]["mixer.noise"]
                ),
                intended_control_mixer_noise_physical=(
                    inputs["parameters"]["physical_by_name"]["mixer.noise"]
                ),
            )
        )
        envelope_physical = inputs["parameters"]["physical_by_name"]
        envelope_rows.append(
            _status_row(
                case_id,
                None,
                "missing",
                rules["families"]["envelope_stages_observed"][
                    "unavailable_reasons"
                ]["missing"],
                requested_settings=dict(
                    attack_seconds=envelope_physical["adsr_1.attack"],
                    decay_seconds=envelope_physical["adsr_1.decay"],
                    release_seconds=envelope_physical["adsr_1.release"],
                    note_duration_seconds=envelope_physical[
                        "keyboard.duration"
                    ],
                ),
            )
        )
        frequency_rows.append(
            _status_row(
                case_id,
                None,
                "unqualified",
                rules["families"]["frequency_ranges_observed"][
                    "unavailable_reasons"
                ]["unqualified"],
            )
        )
        normalized_map = inputs["parameters"]["normalized_by_name"]
        physical_map = inputs["parameters"]["physical_by_name"]
        if (
            set(normalized_map) != set(names)
            or set(physical_map) != set(names)
        ):
            parameter_rows.append(
                _status_row(
                    case_id,
                    None,
                    "error",
                    "metadata parameter maps are not exactly the canonical "
                    "78-name set",
                )
            )
            continue
        nonfinite = [
            name
            for name, value in list(normalized_map.items())
            + list(physical_map.items())
            if not (type(value) is float and math.isfinite(value))
        ]
        if nonfinite:
            parameter_rows.append(
                _status_row(
                    case_id,
                    None,
                    "error",
                    "nonfinite parameter values: %s" % ",".join(sorted(nonfinite)),
                )
            )
            continue
        for name in names:
            normalized = normalized_map[name]
            physical = physical_map[name]
            parameter_counts[name][_bin_label(normalized, edges, labels)] += 1
            extremes = parameter_extremes[name]
            extremes["normalized_min"] = (
                normalized
                if extremes["normalized_min"] is None
                else min(extremes["normalized_min"], normalized)
            )
            extremes["normalized_max"] = (
                normalized
                if extremes["normalized_max"] is None
                else max(extremes["normalized_max"], normalized)
            )
            extremes["physical_min"] = (
                physical
                if extremes["physical_min"] is None
                else min(extremes["physical_min"], physical)
            )
            extremes["physical_max"] = (
                physical
                if extremes["physical_max"] is None
                else max(extremes["physical_max"], physical)
            )
        for lfo, key in (
            ("lfo_1_dominant_weight", "lfo_1"),
            ("lfo_2_dominant_weight", "lfo_2"),
        ):
            weights = {
                weight: physical_map["%s.%s" % (key, weight)]
                for weight in ("sin", "saw", "rsaw", "sqr", "tri")
            }
            dominant = max(sorted(weights), key=lambda w: weights[w])
            counts_map = regime_counts[lfo]
            counts_map[dominant] = counts_map.get(dominant, 0) + 1
        shape = physical_map["vco_2.shape"]
        regime_counts["vco_2_shape_bin"][
            _bin_label(shape, edges, labels)
        ] += 1
        parameter_rows.append(
            _status_row(
                case_id,
                dict(
                    artifact_sha256=case["artifact"]["sha256"],
                    audio_sha256=record["audio"]["value"]["file"]["sha256"],
                ),
                "measured",
                reason=None,
            )
        )

    require(
        not repeat_divergent,
        "per-identity repeat qualification failed for %d case(s): %s"
        % (
            len(repeat_divergent),
            ",".join(row["case_id"] for row in repeat_divergent),
        ),
    )
    qualification["checks"]["per_case_byte_repeat"] = dict(
        verdict="PASS",
        metadata_bytes_equal=repeat_metadata_equal,
        audio_bytes_equal=repeat_audio_equal,
        divergent=[],
    )

    count = len(expected)
    per_parameter = {}
    for name in names:
        parameter = inventory_by_name[name]
        per_parameter[name] = dict(
            module=parameter["module"],
            unit=parameter["annotation"]["unit"],
            physical_range=dict(
                minimum=parameter["minimum"], maximum=parameter["maximum"]
            ),
            bin_counts=parameter_counts[name],
            observed=parameter_extremes[name],
        )
    families = dict(
        audio_facts=dict(
            method=rules["families"]["audio_facts"]["method"],
            rows=audio_rows,
            reconciliation=_reconcile(audio_rows, count, "audio_facts"),
            attempted_estimators=0,
        ),
        normalization_seam=dict(
            method=rules["families"]["normalization_seam"]["method"],
            rows=normalization_rows,
            reconciliation=_reconcile(
                normalization_rows, count, "normalization_seam"
            ),
            attempted_estimators=0,
        ),
        parameter_strata=dict(
            method=rules["families"]["parameter_strata"]["method"],
            rows=parameter_rows,
            reconciliation=_reconcile(parameter_rows, count, "parameter_strata"),
            attempted_estimators=0,
            bins=dict(edges=edges, labels=labels),
            per_parameter=per_parameter,
        ),
        continuous_regimes=dict(
            note="intended parameter controls, never measured output "
            "contributions or invented selector enums",
            dominant_weight_counts=dict(
                lfo_1=regime_counts["lfo_1_dominant_weight"],
                lfo_2=regime_counts["lfo_2_dominant_weight"],
            ),
            vco_2_shape_bins=regime_counts["vco_2_shape_bin"],
        ),
        pitch_stability=dict(
            method=rules["families"]["pitch_stability"]["method"],
            rows=pitch_rows,
            reconciliation=_reconcile(pitch_rows, count, "pitch_stability"),
            attempted_estimators=rules["families"]["pitch_stability"][
                "attempted_estimators"
            ],
        ),
        noise_contribution_observed=dict(
            method=rules["families"]["noise_contribution_observed"]["method"],
            rows=noise_rows,
            reconciliation=_reconcile(
                noise_rows, count, "noise_contribution_observed"
            ),
            attempted_estimators=rules["families"][
                "noise_contribution_observed"
            ]["attempted_estimators"],
        ),
        envelope_stages_observed=dict(
            method=rules["families"]["envelope_stages_observed"]["method"],
            rows=envelope_rows,
            reconciliation=_reconcile(
                envelope_rows, count, "envelope_stages_observed"
            ),
            attempted_estimators=rules["families"][
                "envelope_stages_observed"
            ]["attempted_estimators"],
        ),
        frequency_ranges_observed=dict(
            method=rules["families"]["frequency_ranges_observed"]["method"],
            rows=frequency_rows,
            reconciliation=_reconcile(
                frequency_rows, count, "frequency_ranges_observed"
            ),
            attempted_estimators=rules["families"][
                "frequency_ranges_observed"
            ]["attempted_estimators"],
        ),
    )

    directed = _load_directed_coverage()
    gaps = []
    gap_specs = (
        (
            "pitch_stability",
            "no landed qualified pitch/periodic method covers mixed Voice "
            "audio; corpus captured audio-only",
            rules["families"]["pitch_stability"]["attempted_estimators"],
        ),
        (
            "noise_contribution_observed",
            "no captured post-VCA/weighted pre-normalization source signals; "
            "corpus captured audio-only",
            rules["families"]["noise_contribution_observed"][
                "attempted_estimators"
            ],
        ),
        (
            "envelope_stages_observed",
            "no captured envelope stage evidence; corpus captured audio-only",
            rules["families"]["envelope_stages_observed"][
                "attempted_estimators"
            ],
        ),
        (
            "frequency_ranges_observed",
            "no landed qualified spectral method covers mixed Voice audio",
            rules["families"]["frequency_ranges_observed"][
                "attempted_estimators"
            ],
        ),
    )
    for family, gap, attempted in gap_specs:
        unresolved = [
            row["case_id"]
            for row in families[family]["rows"]
            if row["status"] != "measured"
        ]
        gaps.append(
            dict(
                family=family,
                gap=gap,
                affected_case_count=len(unresolved),
                affected_cases=unresolved,
                attempted_estimators=attempted,
                linked_directed=sorted(GAP_LINKED_DIRECTED[family]),
                linked_directed_status=(
                    "planned intentions (audio_render: %s, %s prepared "
                    "cases); not executed evidence"
                    % (directed.get("audio_render"), directed.get("case_count"))
                    if directed.get("present")
                    else "directed-coverage manifest absent"
                ),
                recommendation=GAP_RECOMMENDATION[family],
            )
        )

    measured_audio = sum(
        1 for row in audio_rows if row["status"] == "measured"
    )
    outliers = [
        dict(
            case_id=row["case_id"],
            exact_silence=row["exact_silence"],
            near_silence=row["near_silence"],
            beyond_full_scale_count=row["beyond_full_scale_count"],
            peak_abs=row["peak_abs"],
        )
        for row in audio_rows
        if row["status"] == "measured"
        and (
            row["exact_silence"]
            or row["near_silence"]
            or row["beyond_full_scale_count"] > 0
        )
    ]
    error_rows = {
        family: [
            row["case_id"]
            for row in families[family]["rows"]
            if row["status"] == "error"
        ]
        for family in (
            "audio_facts",
            "normalization_seam",
            "parameter_strata",
            "pitch_stability",
            "noise_contribution_observed",
            "envelope_stages_observed",
            "frequency_ranges_observed",
        )
    }
    unmeasured = {
        family: [
            row["case_id"]
            for row in families[family]["rows"]
            if row["status"] != "measured"
        ]
        for family in error_rows
    }
    verdict = "QUALIFIED"
    if any(error_rows.values()):
        verdict = "INCOMPLETE"
    report = dict(
        schema=SCHEMA,
        schema_version=1,
        verdict=verdict,
        expected_identity_count=count,
        qualification=qualification,
        denominator=dict(
            expected=count,
            observed_rows=count,
            partition="development",
            holdout_identities_read=0,
        ),
        families=families,
        error_rows=error_rows,
        unmeasured_cases={
            family: dict(count=len(cases), case_ids=cases)
            for family, cases in unmeasured.items()
            if cases
        },
        outliers=dict(
            note="outliers remain in the corpus and are linked here, never "
            "filtered",
            rows=outliers,
        ),
        coverage_gaps=gaps,
        directed_coverage=directed,
        limits=[
            "Coverage and estimator applicability are separate from error: "
            "no distribution, average, or aggregate score is computed or "
            "called correctness, and this audit is not a fidelity, quality, "
            "release, or hardware claim.",
            "Coverage counts do not estimate error; absent independent truth "
            "means no error verdict, not zero error.",
            "The corpus was rendered audio-only: requested traces are empty "
            "for every case and richer module facts are unavailable "
            "(audio-only-adapter); absent seams are NOT MEASURED, never zero.",
            "Linked directed fixtures are planned intentions, not executed "
            "measurements; follow-up filings route through the orchestrator.",
        ],
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--first-receipt",
        type=Path,
        default=ROOT / "sim/reference/development-corpus-first.json",
    )
    parser.add_argument(
        "--second-receipt",
        type=Path,
        default=ROOT / "sim/reference/development-corpus-repeat.json",
    )
    parser.add_argument("--first-store", type=Path)
    parser.add_argument("--second-store", type=Path)
    parser.add_argument(
        "--rules",
        type=Path,
        default=ROOT / RULES_PATH,
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="write the deterministic coverage report here (outside stores)",
    )
    parser.add_argument(
        "--check",
        type=Path,
        help="recompute from read-only inputs and require byte-identical "
        "report at this path",
    )
    args = parser.parse_args()
    require(
        args.first_store is not None and args.second_store is not None,
        "both raw stores are required; receipt-only input qualification "
        "cannot emit coverage verdicts",
    )
    if args.report is not None:
        for label, root in (
            ("first", args.first_store),
            ("second", args.second_store),
        ):
            resolved = Path(os.path.realpath(root))
            target = Path(os.path.realpath(args.report))
            require(
                target != resolved and resolved not in target.parents,
                "report output %s must stay outside the %s store"
                % (target, label),
            )
    rules = loads(args.rules.read_bytes())
    report = audit_corpus_coverage(
        args.first_receipt,
        args.second_receipt,
        first_store=args.first_store,
        second_store=args.second_store,
        rules=rules,
    )
    data = json_bytes(report)
    if args.check is not None:
        committed = args.check.read_bytes()
        identical = committed == data
        print(
            json.dumps(
                dict(
                    check=args.check.name,
                    recomputed_sha256=digest(data),
                    committed_sha256=digest(committed),
                    verdict="IDENTICAL" if identical else "DIVERGED",
                ),
                indent=2,
            )
        )
        return 0 if identical else 1
    if args.report is not None:
        args.report.write_bytes(data)
    summary = dict(
        verdict=report["verdict"],
        expected_identity_count=report["expected_identity_count"],
        family_status_counts={
            name: family["reconciliation"]["counts"]
            for name, family in report["families"].items()
            if name != "continuous_regimes"
        },
        outliers=len(report["outliers"]["rows"]),
        coverage_gaps=len(report["coverage_gaps"]),
        report_sha256=digest(data),
    )
    print(json.dumps(summary, indent=2))
    return 0 if report["verdict"] == "QUALIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
