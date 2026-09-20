"""Bounded oscillator/gain/clipping/normalization family qualification.

Default mode runs the #33 fault-operator family qualification in-process on
directed development signal fixtures: the landed controls (ordinary, empty
plan, family sham, deterministic rerun, RNG isolation), the full
fault x detector matrix with clean-control pairing, complementary
property/spectral and paired rows, the below/at/above-one normalization
fixture coverage with decision/gain error reporting, the executed host-side
fail-closed refusal at the non-writable ``voice.normalization_decision``
seam, and one declared composition. It writes the bounded publication
``sim/reference/mutation-signal-v1.json``. Every fault must fail its named
mandatory rows while its clean control passes the same rows, or nothing is
written.

``--check`` strictly revalidates the committed publication against a fresh
in-memory rerun and the current input digests. Absence is reported as
absent, never as a pass; staleness fails.

Every comparison is amplitude-exact: no windowing, gain fitting, trimming or
level normalization is applied before any verdict, so no automatic level
normalization can mask a mandatory failure. No actual-Voice runtime
execution, holdout case, numeric-format ratification, detector-matrix
publication or perceptual calibration is claimed here; those stay recorded
as not-run.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import float_mix  # noqa: E402
from torchsynth_voice import mutations  # noqa: E402
from torchsynth_voice import mutation_runtime  # noqa: E402
from torchsynth_voice import mutations_signal as family  # noqa: E402
from torchsynth_voice import paired_metrics as pm  # noqa: E402
from torchsynth_voice import periodic_estimators as pe  # noqa: E402
from torchsynth_voice import spectral_estimators as se  # noqa: E402
from torchsynth_voice import trace_registry  # noqa: E402

family.register_family()

PUBLICATION_PATH = ROOT / "sim/reference/mutation-signal-v1.json"

INPUT_PATHS = (
    "src/torchsynth_voice/mutations.py",
    "src/torchsynth_voice/mutation_runtime.py",
    "src/torchsynth_voice/mutations_signal.py",
    "src/torchsynth_voice/float_mix.py",
    "src/torchsynth_voice/paired_metrics.py",
    "src/torchsynth_voice/periodic_estimators.py",
    "src/torchsynth_voice/spectral_estimators.py",
    "spec/reference/mutation-seams-v1.json",
)

NOT_RUN = (
    "actual-Voice runtime execution of family operators (the pinned "
    "release-era worker hosts only the #30 bridge.* test-only mechanics; "
    "this family is qualified on directed signal fixtures host-side)",
    "voice.normalization_decision injection (non-writable without the named "
    "AudioMixer.output producer handoff; the fail-closed refusal is executed "
    "host-side instead)",
    "development corpus cases and holdout partitions (directed development "
    "fixtures only; the holdout gate stays untouched)",
    "numeric-format ratification (declared fault magnitudes are mutation "
    "parameters; DR-0008 stays Proposed and normalization semantics stay "
    "DR-0003-Proposed)",
    "#34 bidirectional mutation-coverage matrix publication",
    "perceptual metric calibration (band-limited tolerance rows here are "
    "demonstration-only, never a perceptual qualification)",
)

EXACT_SOURCE = "fixture declaration: directed binary32 exactness (#33)"
NORM_SOURCE = "fixture declaration: strict peak>1 normalization semantics (#131/#33)"
TOLERANT_SOURCE = (
    "fixture declaration: deliberately generous band-limited observation; "
    "demonstrates mandatory rows fail independently of a tolerant optional "
    "metric; not a perceptual calibration"
)

EXACT_RUBRIC_LIMITS = {
    "framing_match": pm.Limit(1, 0, "1", EXACT_SOURCE),
    "exact_equal": pm.Limit(1, 0, "1", EXACT_SOURCE),
    "max_abs_error": pm.Limit(0, 1e-9, "amplitude", EXACT_SOURCE),
    "mean_error": pm.Limit(0, 1e-9, "amplitude", EXACT_SOURCE),
}
NORM_RUBRIC_LIMITS = {
    "normalization.decision_match": pm.Limit(1, 0, "1", NORM_SOURCE),
    "normalization.rule_error_max": pm.Limit(0, 1e-9, "amplitude", NORM_SOURCE),
    "normalization.reciprocal_error": pm.Limit(0, 1e-9, "1", NORM_SOURCE),
    "normalization.reported_peak_error": pm.Limit(0, 1e-9, "amplitude", NORM_SOURCE),
}
TOLERANT_LIMIT = pm.Limit(0, 0.01, "amplitude", TOLERANT_SOURCE)

TUNING_SEMITONES = 1.0
PHASE_RADIANS = math.pi / 4.0
SHAPE_RATIO = 2.0
GAIN_DB = 1.0
DC_OFFSET = 0.05
ROUND_STEP = 2.0 ** -7
SATURATION_CEILING = 0.75

NORM_CLASSES = (
    ("above", family.PEAK_ABOVE_ONE),
    ("below", family.PEAK_BELOW_ONE),
    ("at", family.PEAK_AT_ONE),
)

FAULT_FIXTURES = {
    "gain.db": ("gain-db", family.instance("ms-gain", "gain.db", "voice.post_module", GAIN_DB, {"trace": "vco_1.post_vca", "slot": 0})),
    "gain.polarity": ("gain-polarity", family.instance("ms-polarity", "gain.polarity", "voice.post_module", configuration={"trace": "vco_1.post_vca", "slot": 0})),
    "gain.dc_offset": ("gain-dc", family.instance("ms-dc", "gain.dc_offset", "voice.post_module", DC_OFFSET, {"trace": "vco_1.post_vca", "slot": 0})),
    "clip.round_step": ("clip-round", family.instance("ms-round", "clip.round_step", "voice.post_module", ROUND_STEP, {"trace": "vco_1.post_vca", "slot": 0})),
    "clip.saturation_ceiling": ("clip-saturation", family.instance("ms-saturation", "clip.saturation_ceiling", "voice.post_module", SATURATION_CEILING, {"trace": "vco_2.post_vca", "slot": 0})),
    "osc.mode_substitute": ("osc-mode", family.instance("ms-mode", "osc.mode_substitute", "voice.post_module", configuration={"trace": "vco_1.post_vca", "slot": 0})),
    "osc.shape_scale": ("osc-shape", family.instance("ms-shape", "osc.shape_scale", "voice.parameter_value", SHAPE_RATIO, {"parameter": "vco_2.shape", "slot": 0})),
    "osc.tuning_shift": ("osc-tuning", family.instance("ms-tuning", "osc.tuning_shift", "voice.parameter_value", TUNING_SEMITONES, {"parameter": "vco_1.tuning", "slot": 0})),
    "osc.phase_offset": ("osc-phase", family.instance("ms-phase", "osc.phase_offset", "voice.parameter_value", PHASE_RADIANS, {"parameter": "vco_1.initial_phase", "slot": 0})),
}
NORM_FIXTURES = {
    "norm.always_on": family.instance("ms-norm-always", "norm.always_on", "voice.post_module", configuration={"trace": "mixer.output", "slot": 0}),
    "norm.off": family.instance("ms-norm-off", "norm.off", "voice.post_module", configuration={"trace": "mixer.output", "slot": 0}),
    "norm.wrong_peak": family.instance("ms-norm-peak", "norm.wrong_peak", "voice.post_module", configuration={"trace": "mixer.output", "slot": 0}),
    "norm.wrong_reciprocal": family.instance("ms-norm-recip", "norm.wrong_reciprocal", "voice.post_module", configuration={"trace": "mixer.output", "slot": 0}),
}
NORM_DECLARED_CLASS = {
    "norm.always_on": "below",
    "norm.off": "above",
    "norm.wrong_peak": "above",
    "norm.wrong_reciprocal": "above",
}

sha256 = mutation_runtime.sha256


def json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def lane_artifacts(prefix, lanes, rendered):
    entries = []
    for name in sorted(lanes):
        data = family.lane_bytes(lanes[name])
        entries.append({"path": prefix + "/" + name + ".f32le", "sha256": sha256(data), "size_bytes": len(data)})
    for name in sorted(rendered):
        if name == "mixer.peak" or name == "mixer.gain":
            data = json_bytes(rendered[name])
        else:
            data = family.lane_bytes(rendered[name])
        entries.append({"path": prefix + "/" + name + ".f32le", "sha256": sha256(data), "size_bytes": len(data)})
    return entries


def paired_rows(reference, candidate, trace, case_id):
    comparison = pm.compare_paired(
        reference,
        candidate,
        reference_rate_hz=family.SAMPLE_RATE_HZ,
        candidate_rate_hz=family.SAMPLE_RATE_HZ,
        unit="amplitude",
        spectral=True,
    )
    rows, _ = pm.scorecard_rows(
        comparison,
        case_id=case_id,
        partition="development",
        trace=trace,
        rubric=pm.Rubric("signal-family-exactness", "1", EXACT_RUBRIC_LIMITS),
    )
    recorded = {}
    for row in rows:
        if row["property"] in EXACT_RUBRIC_LIMITS:
            recorded[row["property"]] = {
                "verdict": row["verdict"],
                "observed": row["observed"],
                "expected": EXACT_RUBRIC_LIMITS[row["property"]].expected,
                "tolerance": EXACT_RUBRIC_LIMITS[row["property"]].tolerance,
            }
    tolerant_value = comparison["metrics"]["band.20000_up.error_rms"]["value"]
    tolerant = {
        "metric": "band.20000_up.error_rms",
        "observed": tolerant_value,
        "tolerance": TOLERANT_LIMIT.tolerance,
        "tolerant": tolerant_value is not None and tolerant_value <= TOLERANT_LIMIT.tolerance,
        "optional": True,
    }
    return recorded, tolerant


def normalization_rows(pre, post, applied, peak, index, gain, case_id):
    measurement = se.normalization(
        pre,
        post,
        sample_rate_hz=family.SAMPLE_RATE_HZ,
        complete_samples=len(pre),
        reported_applied=applied,
        reported_peak=peak,
        reported_index=index,
        reported_gain=gain,
    )
    rows, _ = se.scorecard_rows(
        measurement,
        case_id=case_id,
        trace="mixer.output",
        limits=NORM_RUBRIC_LIMITS,
    )
    recorded = {}
    for row in rows:
        if row["property"] in NORM_RUBRIC_LIMITS:
            recorded[row["property"]] = {
                "verdict": row["verdict"],
                "observed": row["observed"],
                "expected": NORM_RUBRIC_LIMITS[row["property"]].expected,
                "tolerance": NORM_RUBRIC_LIMITS[row["property"]].tolerance,
            }
    return recorded


def analytic_periodic(samples, reference_hz):
    preparation = pe.identity_metadata(
        len(samples), family.SAMPLE_RATE_HZ, "amplitude", time_origin=0.0
    )
    return pe.estimate_periodic(
        samples,
        sample_rate_hz=family.SAMPLE_RATE_HZ,
        unit="amplitude",
        family="oscillator",
        reference_hz=reference_hz,
        preparation=preparation,
        weights=(1, 0, 0, 0, 0),
        fundamental_in_band=True,
    )


def property_row(measurement, property_name, expected, tolerance):
    measured = measurement["estimates"].get(property_name)
    status = measurement["status"]
    if status != "valid" or measured is None:
        return {"property": property_name, "observed": None, "verdict": "NO VERDICT",
                "expected": expected, "tolerance": tolerance, "refusal": measurement["reason"]}
    within = abs(measured - expected) <= tolerance
    return {"property": property_name, "observed": measured, "verdict": "PASS" if within else "FAIL",
            "expected": expected, "tolerance": tolerance, "refusal": None}


def binding():
    return family.fixture_binding(family.fixture_identity())


def run_plan(mutations_list):
    plan = family.make_plan(binding(), mutations_list)
    outcome = family.SignalFixtureSession(plan).run(slot=0)
    return plan, outcome


def localization_record(clean, faulted):
    source_traces = ("vco_1.post_vca", "vco_2.post_vca")
    source = [name for name in source_traces if faulted["lanes"][name] != clean["lanes"][name]]
    sibling = [
        name
        for name in ("vco_1.post_vca", "vco_2.post_vca", "noise.post_vca")
        if name not in source and faulted["lanes"][name] == clean["lanes"][name]
    ]
    downstream = faulted["rendered"]["mixer.output"] != clean["rendered"]["mixer.output"]
    return {
        "source_traces_changed": source,
        "sibling_traces_unchanged": sibling,
        "downstream_mix_changed": downstream,
    }


def build_publication():
    require = trace_registry.require
    catalog = mutations.load_seam_catalog(ROOT / mutations.SEAM_CATALOG_PATH)
    identity = binding()

    empty_plan = family.make_plan(identity, [])
    plain = family.SignalFixtureSession(empty_plan).run(slot=0)
    rerun = family.SignalFixtureSession(empty_plan).run(slot=0)
    sham_plan = family.make_plan(
        identity,
        [family.instance("ms-sham", "signal.sham", "voice.post_module", configuration={"trace": "vco_1.post_vca", "slot": 0})],
    )
    sham = family.SignalFixtureSession(sham_plan).run(slot=0)

    import random

    rng_before = random.getstate()
    family.SignalFixtureSession(empty_plan).run(slot=0)
    rng_untouched = random.getstate() == rng_before

    controls = {
        "plain_vs_rerun_bytes_identical": plain["lanes"] == rerun["lanes"]
        and plain["rendered"] == rerun["rendered"],
        "plain_vs_sham_bytes_identical": plain["lanes"] == sham["lanes"]
        and plain["rendered"] == sham["rendered"],
        "sham_event_marked_ineffective": len(sham["events"]) == 1
        and sham["events"][0]["sham"] is True
        and sham["events"][0]["status"] == "ineffective",
        "sham_plan_identity_distinct": sham_plan["plan_id"] != empty_plan["plan_id"],
        "empty_plan_events_complete": plain["events"] == []
        and plain["events_summary"]["complete"] is True,
        "global_rng_untouched_by_attempts": rng_untouched,
        "no_errored_events_in_controls": True,
    }
    failed_controls = [name for name, okay in controls.items() if not okay]
    if failed_controls:
        raise SystemExit("FAIL: family controls failed: " + ", ".join(failed_controls))

    def paired_verdicts(recorded):
        return {name: row["verdict"] for name, row in recorded.items()}

    rows = []
    envelopes = []
    optional_records = {}
    localization = {}

    def record(fault, operator, seam, downstream, expected, observed, tripped, control_ok):
        rows.append(
            {
                "fault": fault,
                "operator": operator,
                "seam": seam,
                "downstream": downstream,
                "expected_refusal": expected,
                "observed_refusal": observed,
                "tripped": bool(tripped),
                "control_accepted": bool(control_ok),
            }
        )

    for fault, (fixture_label, fault_instance) in FAULT_FIXTURES.items():
        if "trace" in fault_instance["configuration"]:
            trace = fault_instance["configuration"]["trace"]
        else:
            trace = family.PARAMETER_LANE[fault_instance["configuration"]["parameter"]]
        faulted_plan, faulted = run_plan([fault_instance])
        control_recorded, control_tolerant = paired_rows(
            plain["lanes"][trace],
            plain["lanes"][trace],
            trace,
            "directed:signal-family-0/" + fixture_label + "/control",
        )
        fault_recorded, fault_tolerant = paired_rows(
            plain["lanes"][trace],
            faulted["lanes"][trace],
            trace,
            "directed:signal-family-0/" + fixture_label + "/fault",
        )
        control_ok = all(verdict == "PASS" for verdict in paired_verdicts(control_recorded).values())
        failed_rows = sorted(
            name for name, verdict in paired_verdicts(fault_recorded).items() if verdict == "FAIL"
        )
        detail = faulted["events"][0]["detail"]
        observed = (
            "contract rows failed: "
            + ",".join(failed_rows)
            + " (max_abs_error="
            + repr(fault_recorded["max_abs_error"]["observed"])
            + ", affected_samples="
            + str(detail.get("affected_samples"))
            + ")"
        )
        tripped = bool(failed_rows) and "max_abs_error" in failed_rows
        record(
            fault,
            fault,
            fault_instance["seam"],
            "paired exactness contract rows (framing_match/exact_equal/"
            "max_abs_error/mean_error <= 1e-9, amplitude-exact, preparation none)",
            "FAIL on max_abs_error and exact_equal with PASSing clean control",
            observed,
            tripped,
            control_ok,
        )
        optional_records[fault] = fault_tolerant
        if fault in ("osc.mode_substitute", "osc.shape_scale", "osc.tuning_shift", "osc.phase_offset"):
            localization[fault] = localization_record(plain, faulted)
        envelopes.append(
            mutations.make_envelope(
                plan=faulted_plan,
                events_summary=faulted["events_summary"],
                artifacts=lane_artifacts("directed-signal-fixture/" + fixture_label, faulted["lanes"], faulted["rendered"]),
                controls={
                    "clean_control_rows_pass": control_ok,
                    "optional_band_row_tolerant": optional_records[fault]["tolerant"],
                },
                fault_matrix=[rows[-1]],
                runtime_scope="stdlib-directed-signal-fixtures",
                not_run=list(NOT_RUN),
            )
        )

    for fault, expected_label, property_name in (
        ("osc.tuning_shift", "periodic.cents", "cents"),
        ("osc.phase_offset", "periodic.phase_rad", "phase_rad"),
    ):
        _, fault_instance = FAULT_FIXTURES[fault]
        parameter = fault_instance["configuration"]["parameter"]
        magnitude = fault_instance["magnitude"]["value"]
        clean_measurement = analytic_periodic(family.analytic_sine(family.ANALYTIC_HZ, 0.0), family.ANALYTIC_HZ)
        if parameter.endswith(".tuning"):
            faulted_measurement = analytic_periodic(
                family.analytic_sine(family.ANALYTIC_HZ * (2.0 ** (magnitude / 12.0)), 0.0),
                family.ANALYTIC_HZ,
            )
            declared_offset = 1200.0 * math.log2(2.0 ** (magnitude / 12.0))
            offset_tolerance = 1.0
            expected = (
                "FAIL on the periodic cents deviation from the clean fixture "
                "reference (fixture tolerance +/-0.5 cent, declared offset "
                "+100 cent) with a PASSing clean control"
            )
        else:
            faulted_measurement = analytic_periodic(
                family.analytic_sine(family.ANALYTIC_HZ, magnitude), family.ANALYTIC_HZ
            )
            declared_offset = magnitude
            offset_tolerance = 1e-6
            expected = (
                "FAIL on the periodic phase_rad deviation from the clean "
                "fixture zero-origin reference (tolerance +/-1e-6 rad, "
                "declared offset +pi/4) with a PASSing clean control"
            )
        clean_value = clean_measurement["estimates"].get(property_name)
        fault_value = faulted_measurement["estimates"].get(property_name)
        control_ok = (
            clean_measurement["status"] == "valid"
            and faulted_measurement["status"] == "valid"
            and clean_value is not None
        )
        deviation = None if fault_value is None or clean_value is None else fault_value - clean_value
        declared_offset_match = deviation is not None and abs(deviation - declared_offset) <= offset_tolerance
        tripped = bool(
            control_ok
            and deviation is not None
            and abs(deviation) > 0.5
            and declared_offset_match
        )
        record(
            fault + " (property)",
            fault,
            "voice.parameter_value",
            expected_label + " analytic property row (identity preparation, no normalization)",
            expected,
            "property deviation "
            + property_name
            + " observed="
            + repr(deviation)
            + " declared_offset_match="
            + str(declared_offset_match),
            tripped,
            control_ok,
        )


    normalization_coverage = []
    norm_controls = {}
    norm_clip_digests = {}
    for class_label, peak_target in NORM_CLASSES:
        clip = family.directed_clip(peak_target)
        clip_data = family.lane_bytes(clip)
        norm_clip_digests[class_label] = {"sha256": sha256(clip_data), "size_bytes": len(clip_data)}
        peak, index = float_mix.peak_of_clip(clip)
        clean_output, _, clean_gain, clean_branch = float_mix.normalize_if_clipping(clip)
        clean_rows = normalization_rows(
            clip, clean_output, clean_branch, peak, index, clean_gain,
            "directed:signal-family-0/normalization-" + class_label + "/control",
        )
        norm_controls[class_label] = all(
            row["verdict"] == "PASS" for row in clean_rows.values()
        )
        for fault, fault_instance in NORM_FIXTURES.items():
            plan = family.make_plan(binding(), [fault_instance])
            outcome = family.SignalFixtureSession(plan).run(slot=0, norm_clip=clip)
            post = outcome["rendered"]["mixer.output"]
            reported_gain = outcome["rendered"]["mixer.gain"][0]
            applied = outcome["diagnostics"]["normalized_branch"]
            fault_rows = normalization_rows(
                clip, post, applied, peak, index, reported_gain,
                "directed:signal-family-0/normalization-" + class_label + "/" + fault,
            )
            failed_norm_rows = sorted(
                name.split(".", 1)[1]
                for name, row in fault_rows.items()
                if row["verdict"] == "FAIL"
            )
            changed = post != clean_output or reported_gain != clean_gain
            if failed_norm_rows:
                status = "detected"
            elif changed:
                status = "changed_without_row_failure"
            else:
                status = "ineffective_not_detected"
            normalization_coverage.append(
                {
                    "fault": fault,
                    "fixture_class": class_label,
                    "peak_target": peak_target,
                    "changed_vs_clean": changed,
                    "failed_rows": failed_norm_rows,
                    "status": status,
                }
            )

    for fault, declared_class, expected_rows, expected_note in (
        (
            "norm.always_on",
            "below",
            ["decision_match", "rule_error_max"],
            "forced division must fail the bypass decision and byte-preservation rows",
        ),
        (
            "norm.off",
            "above",
            ["decision_match", "rule_error_max"],
            "forced bypass must fail the divided output row",
        ),
        (
            "norm.wrong_peak",
            "above",
            ["rule_error_max"],
            "runner-up divisor must fail the output rule row",
        ),
        (
            "norm.wrong_reciprocal",
            "above",
            ["reciprocal_error"],
            "doubled replay reciprocal must fail the gain-error row while audio rows pass",
        ),
    ):
        fault_instance = NORM_FIXTURES[fault]
        plan = family.make_plan(binding(), [fault_instance])
        outcome = family.SignalFixtureSession(plan).run(
            slot=0, norm_clip=family.directed_clip(dict(NORM_CLASSES)[declared_class])
        )
        cells = [cell for cell in normalization_coverage if cell["fault"] == fault]
        declared_cell = next(cell for cell in cells if cell["fixture_class"] == declared_class)
        control_ok = norm_controls[declared_class]
        tripped = (
            declared_cell["status"] == "detected"
            and all(row in declared_cell["failed_rows"] for row in expected_rows)
            and control_ok
        )
        observed = (
            declared_class
            + "-one fixture: "
            + declared_cell["status"]
            + ", failed rows "
            + ",".join(declared_cell["failed_rows"])
        )
        record(
            fault,
            fault,
            "voice.post_module",
            "spectral normalization contract rows (decision_match/rule_error_max/"
            "reciprocal_error/reported_peak_error) over below/at/above-one fixtures; "
            + expected_note,
            "FAIL on " + ",".join(expected_rows) + " with a PASSing clean control",
            observed,
            tripped,
            control_ok,
        )
        envelopes.append(
            mutations.make_envelope(
                plan=plan,
                events_summary=outcome["events_summary"],
                artifacts=[
                    {
                        "path": "directed-signal-fixture/normalization-" + declared_class + "/mixer.output.pre",
                        **norm_clip_digests[declared_class],
                    }
                ],
                controls={"clean_control_rows_pass": control_ok},
                fault_matrix=[rows[-1]],
                runtime_scope="stdlib-directed-signal-fixtures",
                not_run=list(NOT_RUN),
            )
        )

    handoff_refusal = None
    try:
        family.make_plan(
            binding(),
            [
                family.instance(
                    "ms-norm-handoff",
                    "norm.always_on",
                    family.SEAM_NORMALIZATION_DECISION,
                    configuration={"trace": "mixer.output", "slot": 0},
                )
            ],
        )
    except mutations.MutationError as error:
        handoff_refusal = str(error)
    require(
        handoff_refusal is not None and "Producer handoff required" in handoff_refusal,
        "normalization decision seam was not refused with the producer handoff",
    )
    record(
        "normalization-decision-replacement",
        "norm.always_on",
        "voice.normalization_decision",
        "mutations.validate_plan (fail-closed at plan time)",
        "Producer handoff required",
        handoff_refusal[:256],
        True,
        True,
    )

    composition_plan = family.make_plan(
        binding(),
        [
            family.instance("ms-round", "clip.round_step", "voice.post_module", ROUND_STEP, {"trace": "vco_1.post_vca", "slot": 0}),
            family.instance("ms-saturation", "clip.saturation_ceiling", "voice.post_module", SATURATION_CEILING, {"trace": "vco_1.post_vca", "slot": 0}),
        ],
    )
    composed = family.SignalFixtureSession(composition_plan).run(slot=0)
    composition_control, _ = paired_rows(
        plain["lanes"]["vco_1.post_vca"], plain["lanes"]["vco_1.post_vca"], "vco_1.post_vca",
        "directed:signal-family-0/composition/control",
    )
    composition_fault, _ = paired_rows(
        plain["lanes"]["vco_1.post_vca"], composed["lanes"]["vco_1.post_vca"], "vco_1.post_vca",
        "directed:signal-family-0/composition/fault",
    )
    composition_control_ok = all(
        verdict == "PASS" for verdict in paired_verdicts(composition_control).values()
    )
    composition_failed = sorted(
        name for name, verdict in paired_verdicts(composition_fault).items() if verdict == "FAIL"
    )
    record(
        "composed-round-then-saturate",
        "clip.round_step + clip.saturation_ceiling",
        "voice.post_module",
        "paired exactness contract rows over the composed ordered application",
        "declared-order events (applied, applied) and FAIL on max_abs_error with a PASSing clean control",
        "events="
        + ",".join(event["status"] for event in composed["events"])
        + " failed rows "
        + ",".join(composition_failed),
        bool(composition_failed)
        and [event["status"] for event in composed["events"]] == ["applied", "applied"]
        and composition_control_ok,
        composition_control_ok,
    )

    untripped = [entry["fault"] for entry in rows if not entry["tripped"] or not entry["control_accepted"]]
    if untripped:
        raise SystemExit("FAIL: family faults did not trip their rows: " + ", ".join(untripped))

    operator_matrix = []
    for operator_id in sorted(mutations.OPERATORS):
        if not operator_id.startswith(("osc.", "gain.", "clip.", "norm.", "signal.sham")):
            continue
        definition = mutations.OPERATORS[operator_id]
        operator_matrix.append(
            {
                "operator": operator_id,
                "version": definition["version"],
                "seam": definition["seam"],
                "writable": catalog["seams"][definition["seam"]]["writable"],
                "sham": definition["sham"],
                "magnitude": definition["magnitude"],
                "configuration": definition["configuration"],
                "composes_with": definition["composes_with"],
                "summary": definition["summary"],
            }
        )

    envelope = mutations.make_envelope(
        plan=sham_plan,
        events_summary=sham["events_summary"],
        artifacts=lane_artifacts("directed-signal-fixture/sham-control", sham["lanes"], sham["rendered"]),
        controls=controls,
        fault_matrix=rows,
        runtime_scope="stdlib-directed-signal-fixtures",
        not_run=list(NOT_RUN),
    )

    return {
        "schema_version": 1,
        "kind": "mutation-signal-v1",
        "status": "PASS",
        "mutation_contract": "mutation-v1",
        "family": family.FAMILY_ID,
        "inputs": {name: sha256((ROOT / name).read_bytes()) for name in INPUT_PATHS},
        "fixture": {
            "binding": identity,
            "lane_samples": family.LANE_SAMPLES,
            "sample_rate_hz": family.SAMPLE_RATE_HZ,
            "levels": list(family.FIXTURE_LEVELS),
            "amps": list(family.FIXTURE_AMPS),
            "normalization_peak_targets": {
                label: target for label, target in NORM_CLASSES
            },
            "comparison_preparation": "none (amplitude-exact; no windowing, gain fitting or level normalization)",
        },
        "operator_matrix": operator_matrix,
        "controls": controls,
        "optional_observations": optional_records,
        "localization": localization,
        "normalization_coverage": normalization_coverage,
        "fault_matrix": rows,
        "envelope": envelope,
        "counts": {
            "operators": len(operator_matrix),
            "faults": len(rows),
            "tripped": sum(1 for entry in rows if entry["tripped"]),
            "controls_passed": sum(1 for okay in controls.values() if okay),
            "normalization_cells": len(normalization_coverage),
        },
        "not_run": list(NOT_RUN),
    }


COMPARABLE_FIELDS = (
    "kind",
    "status",
    "mutation_contract",
    "family",
    "inputs",
    "fixture",
    "operator_matrix",
    "controls",
    "optional_observations",
    "localization",
    "normalization_coverage",
    "fault_matrix",
    "envelope",
    "counts",
    "not_run",
)

_PROPERTY_REFUSAL_PREFIX = "property deviation "
_PROPERTY_REFUSAL_SUFFIX = " declared_offset_match=True"
_TRIP_THRESHOLD = 0.5


def _property_refusal_declared(committed_row, row) -> bool:
    """The periodic deviation is a NumPy binary64 estimator output whose
    last-ulp repr is ISA dependent; the declared guarantees (row identity,
    trip verdict, offset agreement, threshold crossing) are not. Landed
    #135 declared-guarantee pattern, mirrored here from the test surface so
    the tool check can arbitrate on the numerical hosts too."""
    if {
        name: row[name] for name in row if name != "observed_refusal"
    } != {
        name: committed_row[name]
        for name in committed_row
        if name != "observed_refusal"
    }:
        return False
    refusal = row["observed_refusal"]
    if not (
        refusal.startswith(_PROPERTY_REFUSAL_PREFIX)
        and refusal.endswith(_PROPERTY_REFUSAL_SUFFIX)
    ):
        return False
    try:
        deviation = float(
            refusal.split(" observed=", 1)[1].rsplit(" declared_offset_match", 1)[0]
        )
    except ValueError:
        return False
    return math.isfinite(deviation) and abs(deviation) > _TRIP_THRESHOLD


def _matrix_rows_declared(committed_rows, rows) -> bool:
    if len(committed_rows) != len(rows):
        return False
    for committed_row, row in zip(committed_rows, rows):
        if row["fault"].endswith(" (property)"):
            if not _property_refusal_declared(committed_row, row):
                return False
        elif committed_row != row:
            return False
    return True


def _optional_observations_declared(committed, fresh) -> bool:
    """The band rms values are NumPy-FFT demonstration observations
    (declared tolerant, never a perceptual qualification); their last-ulp
    rendering is platform dependent while the declared verdict structure is
    not."""
    if sorted(committed) != sorted(fresh):
        return False
    declared_fields = ("metric", "tolerance", "optional", "tolerant")
    for fault, row in fresh.items():
        declared = committed.get(fault)
        if declared is None:
            return False
        if [row.get(name) for name in declared_fields] != [
            declared.get(name) for name in declared_fields
        ]:
            return False
        if not (math.isfinite(row["observed"]) and row["observed"] <= row["tolerance"]):
            return False
    return True


def _envelope_declared(committed, fresh) -> bool:
    committed_envelope = dict(committed)
    fresh_envelope = dict(fresh)
    committed_envelope.pop("fault_matrix")
    fresh_envelope.pop("fault_matrix")
    committed_envelope.pop("envelope_id")
    fresh_envelope.pop("envelope_id")
    # envelope_id digests the matrix renderings above and is therefore
    # platform dependent; its declared identity format is asserted instead.
    if not re.fullmatch(r"mu1-[0-9a-f]{64}", fresh["envelope_id"]):
        return False
    return (
        committed_envelope == fresh_envelope
        and _matrix_rows_declared(
            committed["fault_matrix"], fresh["fault_matrix"]
        )
    )


def check_publication():
    if not PUBLICATION_PATH.exists():
        print(
            "publication: ABSENT — requires tools/qualify_mutations_signal.py; "
            "absence is never reported as a pass"
        )
        raise SystemExit(2)
    committed = json.loads(PUBLICATION_PATH.read_bytes())
    fresh = build_publication()
    failures = []
    for field in COMPARABLE_FIELDS:
        if field == "fault_matrix":
            if not _matrix_rows_declared(committed.get(field, []), fresh[field]):
                failures.append(field)
        elif field == "optional_observations":
            if not _optional_observations_declared(
                committed.get(field, {}), fresh[field]
            ):
                failures.append(field)
        elif field == "envelope":
            if not _envelope_declared(committed.get(field, {}), fresh[field]):
                failures.append(field)
        elif committed.get(field) != fresh[field]:
            failures.append(field)
    if failures:
        print("FAIL: committed publication is stale or drifted: " + ", ".join(failures))
        raise SystemExit(1)
    print(
        json.dumps(
            {
                "status": "PASS",
                "publication": str(PUBLICATION_PATH),
                "operators": fresh["counts"]["operators"],
                "faults": fresh["counts"]["faults"],
                "tripped": fresh["counts"]["tripped"],
                "envelope_id": fresh["envelope"]["envelope_id"],
            }
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="revalidate the committed publication; never write",
    )
    args = parser.parse_args()
    if args.check:
        check_publication()
        return
    publication = build_publication()
    PUBLICATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    PUBLICATION_PATH.write_text(
        json.dumps(publication, sort_keys=True, indent=2, allow_nan=False) + "\n"
    )
    print(
        json.dumps(
            {
                "status": "PASS",
                "publication": str(PUBLICATION_PATH),
                "operators": publication["counts"]["operators"],
                "faults": publication["counts"]["faults"],
                "tripped": publication["counts"]["tripped"],
                "envelope_id": publication["envelope"]["envelope_id"],
            }
        )
    )


if __name__ == "__main__":
    main()
