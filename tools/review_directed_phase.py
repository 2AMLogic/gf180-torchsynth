#!/usr/bin/env python3
"""Phase review of the directed-fixture and scorecard contract (issue #5).

Re-derives the phase-level closure evidence for the four landed work units
(#16 parameter inventory, #17 directed manifest, #18 scorecard row schema,
#87 case registry and evidence board) instead of trusting their own
statements: parameter and trace coverage are recomputed by set comparison,
the manifest is rebuilt and compared byte for byte, the scorecard refusals
are executed as live negative controls, and every corpus/runtime figure is
arithmetic over pinned measured receipts.

The review also answers the phase question none of the four leaves owned:
expected corpus size and runtime, and which preregistered fixtures cannot
have analytic truth. The truth classification is declared separately in
``spec/reference/directed-truth-basis-v1.json``; this tool consumes it,
checks it covers exactly the canonical trace names, and joins it to
mechanically derived per-fixture activation facts.

Stdlib only. Read-only apart from the report it writes. It renders no audio,
runs no estimator, imports no Torch, computes no tolerance, and issues no
pass/fail for any Voice, fixed-point or hardware claim.
"""

from __future__ import annotations

import argparse
import copy
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import directed  # noqa: E402
from torchsynth_voice.digest import sha256_bytes as digest  # noqa: E402
from torchsynth_voice.inventory import load_json  # noqa: E402
from torchsynth_voice.scorecard import (  # noqa: E402
    ScorecardError,
    row_from_json,
    summarize_rows,
    validate_row,
)
from torchsynth_voice.trace_registry import load_registry  # noqa: E402

SCHEMA = "torchsynth-directed-phase-review"
SCHEMA_VERSION = 1
REVIEW = "issue-5-directed-fixtures-and-scorecard-contract"
SOURCE_TARGET = "2b0964d4c6c3d472a2a0d54d91b408caaeffca6d"

REPORT_PATH = "spec/reference/directed-phase-review-v1.json"
BASIS_PATH = "spec/reference/directed-truth-basis-v1.json"
INVENTORY_PATH = "spec/reference/parameter-inventory-v1.json"
MANIFEST_PATH = "spec/reference/directed-voice-v1.json"
COVERAGE_PATH = "spec/reference/directed-coverage-v1.json"
TRACE_REGISTRY_PATH = "spec/reference/trace-registry-v1.json"
CASE_REGISTRY_PATH = "spec/reference/case-registry-v1.json"
BOARD_PATH = "docs/scorecard.json"
CAPTURE_PATH = "sim/reference/trace-capture.json"
CORPUS_RECEIPT_PATH = "sim/reference/development-corpus-first.json"

SCHEMA_PATHS = (
    "spec/schemas/case-registry-v1.schema.json",
    "spec/schemas/directed-voice-v1.schema.json",
    "spec/schemas/scorecard-report-v1.schema.json",
    "spec/schemas/scorecard-row-v1.schema.json",
)

# Landed evidence records that reference preregistered directed fixture IDs.
# A reviewed snapshot, deliberately not a glob: discovering consumers
# automatically would silently change what this review counted.
CONSUMER_PATHS = (
    "sim/reference/adsr-golden-v1/frozen-envelope-receipt.json",
    "sim/reference/fixed-voice-golden-v1.json",
    "sim/reference/float-sources-v1.json",
    "sim/reference/float-voice-v1.json",
    "sim/reference/lfo-vca-golden-v1/frozen-lfo-receipt.json",
    "sim/reference/mod-matrix-golden-v1/frozen-mod-matrix-receipt.json",
    "sim/reference/scalar-execution.json",
    "sim/reference/trace-capture.json",
)

INPUT_PATHS = (
    "tools/review_directed_phase.py",
    BASIS_PATH,
    INVENTORY_PATH,
    MANIFEST_PATH,
    COVERAGE_PATH,
    TRACE_REGISTRY_PATH,
    CASE_REGISTRY_PATH,
    BOARD_PATH,
    CORPUS_RECEIPT_PATH,
    *SCHEMA_PATHS,
    *CONSUMER_PATHS,
)

BASIS_CODES = ("closed-form", "closed-form-limited", "reference-capture-only")
AMPLITUDE_COLUMNS = {
    "noise": "noise_amp",
    "vco_1": "vco_1_amp",
    "vco_2": "vco_2_amp",
}
CONTROL_SOURCES = ("adsr_1", "adsr_2", "lfo_1", "lfo_2")
SAMPLES_PER_CLIP = 176400


class ReviewError(ValueError):
    """The review refuses rather than emitting an unsupported figure."""


def require(condition: object, reason: str) -> None:
    if not condition:
        raise ReviewError(reason)


def encode(document: dict) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def read_bytes(relative: str) -> bytes:
    path = ROOT / relative
    require(path.is_file(), f"missing reviewed input: {relative}")
    return path.read_bytes()


def binary32_exact(value: float) -> bool:
    """True when the binary64 value survives a binary32 round trip exactly."""
    return struct.unpack("<f", struct.pack("<f", value))[0] == value


def median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def synthetic_row(verdict: str = "PASS", **changes: object) -> dict:
    """A synthetic scorecard row for live negative controls, never a measurement."""
    statuses = {"PASS": "valid", "FAIL": "valid", "NO VERDICT": "insufficient"}
    measured = verdict in ("PASS", "FAIL")
    row = {
        "schema_version": 1,
        "case": {"id": "synthetic-review-control", "partition": "development"},
        "trace": "synthetic-output",
        "property": "frequency",
        "estimator": {"name": "synthetic-review-estimator", "version": "1"},
        "rubric": {"id": "synthetic-review-only", "version": "1"},
        "unit": "Hz",
        "expected": {"value": 100, "source": "synthetic review control definition"},
        "observed": (100 if verdict == "PASS" else 102) if measured else None,
        "tolerance": {"value": 1, "source": "synthetic review control rubric"},
        "validity": {
            "status": statuses[verdict],
            "reason": "Synthetic phase-review control, not a TorchSynth or RTL measurement",
        },
        "coverage": "complete",
        "verdict": verdict,
        "artifact": {
            "identity": "synthetic-review-control-record",
            "sha256": digest(b"synthetic phase-review control, not audio"),
        },
    }
    row.update(changes)
    return row


def refusal(label: str, call) -> dict:
    """Execute a negative control and record the refusal it must produce."""
    try:
        call()
    except (ScorecardError, ValueError) as error:
        return {"probe": label, "refused": True, "reason": str(error)}
    raise ReviewError(f"negative control was accepted: {label}")


def audible_sources(patch: dict) -> tuple[str, ...]:
    """Sources that can reach the mix: nonzero mixer gain and nonzero amp column."""
    active = []
    for source, column in sorted(AMPLITUDE_COLUMNS.items()):
        gain = patch[f"mixer.{source}"]["normalized"]
        routed = sum(
            patch[f"mod_matrix.{name}->{column}"]["normalized"] for name in CONTROL_SOURCES
        )
        if gain > 0 and routed > 0:
            active.append(source)
    return tuple(active)


def coverage_case_ids(coverage: dict) -> set[str]:
    """Every case ID reachable from a coverage intent, per declared section shape."""
    found: set[str] = set()
    for variants in coverage["parameters"].values():
        found.update(variants.values())
    for section in ("routes", "sources", "waveforms", "envelopes", "special"):
        found.update(coverage[section].values())
    for entry in coverage["normalization"].values():
        found.add(entry["case"])
    for entry in coverage["traces"].values():
        found.update(entry["cases"])
    return found


def parameter_criterion(coverage: dict, rows: dict, case_ids: set[str]) -> dict:
    names = set(rows)
    planned = set(coverage["parameters"])
    missing = sorted(names - planned)
    variant_mismatch = sorted(
        name
        for name, cases in coverage["parameters"].items()
        if set(cases) != set(directed.boundary_points(rows[name]))
    )
    unknown = sorted(
        case for cases in coverage["parameters"].values() for case in cases.values()
    )
    dangling = sorted(set(unknown) - case_ids)
    modes = coverage["discrete_modes"]
    require(not missing, f"parameters with no planned fixture: {missing}")
    require(not variant_mismatch, f"boundary variants disagree with the inventory: {variant_mismatch}")
    require(not dangling, f"coverage references absent cases: {dangling}")
    require(isinstance(modes["count"], int), "discrete mode count must be an integer")
    require(modes["reason"].strip(), "discrete mode count needs an explicit rationale")
    return {
        "id": "AC1",
        "statement": "Every parameter and discrete mode maps to at least one planned fixture or has an explicit rationale.",
        "method": "Set comparison of the coverage report's parameter keys against the landed inventory, per-parameter boundary-variant equality, and the declared discrete-mode rationale.",
        "observed": {
            "inventory_parameters": len(names),
            "parameters_with_planned_fixtures": len(planned),
            "boundary_fixtures": len(unknown),
            "discrete_modes": modes["count"],
            "discrete_mode_rationale": modes["reason"],
        },
        "status": "SATISFIED",
    }


def trace_criterion(coverage: dict, registry: dict, capture: dict) -> dict:
    planned = coverage["traces"]
    canonical = {trace["name"] for trace in registry["traces"]}
    require(set(planned) == canonical, "planned traces and the canonical registry disagree")
    require(
        not [name for name in canonical if name.startswith("analytic.")],
        "canonical trace names must not collide with analytic estimator lanes",
    )
    limited = sorted(
        name for name, entry in planned.items() if "cannot" in entry["isolation"].lower()
    )
    for name, entry in planned.items():
        require(entry["cases"], f"planned trace without capture cases: {name}")
        require(entry["isolation"].strip(), f"planned trace without isolation note: {name}")
        require(entry["status"] == "planned", f"unexpected trace status for {name}")
    captured = sorted(
        case["case"]["directed"] for case in capture["cases"] if "directed" in case["case"]
    )
    inventories = {
        case["case"]["directed"]: sorted(item["name"] for item in case["full_inventory"])
        for case in capture["cases"]
        if "directed" in case["case"]
    }
    for case, names in inventories.items():
        require(set(names) == canonical, f"captured inventory is not the canonical set: {case}")
    return {
        "id": "AC2",
        "statement": "Every named trace in the measurement plan is exercised in isolation where the graph permits.",
        "method": "Set equality between the planned capture intents and the canonical trace registry, per-trace isolation and case checks, and the executed upstream capture inventory.",
        "observed": {
            "canonical_traces": len(canonical),
            "planned_traces": len(planned),
            "traces_declaring_a_graph_limitation": limited,
            "directed_fixtures_with_executed_upstream_capture": captured,
            "captured_traces_per_case": len(canonical),
        },
        "status": "SATISFIED-AS-PREPARATION",
    }


def normalization_criterion(coverage: dict, capture: dict) -> dict:
    targets = coverage["normalization"]
    require(set(targets) == set(directed.PEAKS), "normalization relations are incomplete")
    observed = {}
    for relation, entry in sorted(targets.items()):
        peak = entry["target_peak"]
        require(peak == directed.PEAKS[relation], f"{relation} target peak drifted")
        require(binary32_exact(peak), f"{relation} target peak is not exactly binary32")
        require("tie retains input" in entry["render_obligation"], f"{relation} omits the tie policy")
        require(entry["peak_status"] == "unmeasured", f"{relation} peak status changed")
        observed[relation] = peak
    require(targets["below"]["target_peak"] < targets["tie"]["target_peak"], "below is not under the tie")
    require(targets["above"]["target_peak"] > targets["tie"]["target_peak"], "above is not over the tie")
    branches = {
        case["case"]["directed"]: case["normalization_branch"]["peak_gt_one"]
        for case in capture["cases"]
        if "directed" in case["case"]
    }
    require(branches.get("normalization:above") is True, "captured above case did not exceed one")
    require(branches.get("normalization:tie") is False, "captured tie case took the division branch")
    require(branches.get("normalization:below") is False, "captured below case took the division branch")
    return {
        "id": "AC3",
        "statement": "Normalization boundary cases include representable values on both sides and a tie policy question.",
        "method": "Exact binary32 round-trip of each declared target, ordering of the three targets, the recorded tie policy text, and the captured strict-inequality branch decision on the pinned upstream.",
        "observed": {
            "target_peaks": observed,
            "peak_status_in_manifest_coverage": "unmeasured",
            "captured_peak_greater_than_one": dict(sorted(branches.items())),
            "tie_policy": "strict peak > 1 normalizes; an exact tie retains the input",
        },
        "status": "SATISFIED",
    }


def manifest_criterion(manifest: dict, rows: dict, registry_document: dict) -> dict:
    directed.validate_manifest(manifest)
    rebuilt = directed.encode(directed.build_manifest())
    committed = read_bytes(MANIFEST_PATH)
    require(rebuilt == committed, "the manifest does not rebuild to its committed bytes")
    require(
        directed.encode(directed.coverage_report(manifest)) == read_bytes(COVERAGE_PATH),
        "the coverage report does not rebuild to its committed bytes",
    )
    positional = []
    for case in manifest["cases"]:
        if not isinstance(case["overrides"], dict):
            positional.append(case["id"])
        resolved = directed.resolve_patch(manifest, case)
        if set(resolved) != set(rows):
            positional.append(case["id"])
    require(not positional, f"positional or incomplete patches: {sorted(set(positional))}")
    require(isinstance(manifest["base"], dict), "the base patch must be name-keyed")
    identity = directed.manifest_identity(manifest)
    require(identity == manifest["identity"], "the recorded manifest identity is stale")
    mutated = copy.deepcopy(manifest)
    first = mutated["cases"][0]
    name = sorted(first["overrides"])[0]
    mutated["cases"][0]["overrides"][name] = dict(
        mutated["cases"][0]["overrides"][name], physical=0.0
    )
    changed = directed.manifest_identity(mutated)
    require(changed["sha256"] != identity["sha256"], "a changed patch did not change the digest")
    require(changed["version"] != identity["version"], "a changed patch did not change the version")
    pinned = {
        source["family"]: source["manifest"]
        for source in registry_document["sources"]
    }
    require(
        pinned["directed"]["sha256"] == digest(committed),
        "the case registry's directed pin does not match the committed manifest bytes",
    )
    require(
        pinned["directed"]["identity"] == identity["version"],
        "the case registry's directed identity does not match the manifest identity",
    )
    schemas = {}
    for relative in SCHEMA_PATHS:
        document = json.loads(read_bytes(relative))
        require("2020-12" in document.get("$schema", ""), f"schema is not 2020-12: {relative}")
        require(document.get("title", "").strip(), f"schema without a title: {relative}")
        # Registered documents carry a URN; the local structural schema does not.
        schemas[relative] = document.get("$id", document["title"])
    return {
        "id": "AC4",
        "statement": "Fixture manifests are deterministic, name-keyed, schema-validated, and contain no positional patches.",
        "method": "Rebuild the manifest and coverage report and compare committed bytes, resolve all patches by canonical name, recompute the sealed identity, mutate one patch to confirm the identity moves, and confirm the case registry pins those exact bytes.",
        "observed": {
            "cases": len(manifest["cases"]),
            "canonical_names_per_case": len(rows),
            "resolved_name_value_pairs": len(manifest["cases"]) * len(rows),
            "manifest_identity": identity["version"],
            "manifest_sha256": digest(committed),
            "schema_ids": dict(sorted(schemas.items())),
        },
        "status": "SATISFIED",
    }


def scorecard_criterion() -> dict:
    valid = synthetic_row()
    validate_row(valid)
    missing_unit = {key: value for key, value in valid.items() if key != "unit"}
    missing_validity = {key: value for key, value in valid.items() if key != "validity"}
    nan_text = json.dumps(valid).replace('"observed": 100', '"observed": NaN')
    other_property = synthetic_row("FAIL", property="amplitude", unit="1")
    probes = [
        refusal("missing unit", lambda: validate_row(missing_unit)),
        refusal("missing validity", lambda: validate_row(missing_validity)),
        refusal("NaN as a result value", lambda: row_from_json(nan_text)),
        refusal(
            "aggregate of incompatible properties",
            lambda: summarize_rows([valid, other_property]),
        ),
    ]
    return {
        "id": "AC5",
        "statement": "Scorecard schema rejects missing units, missing validity, NaN-as-result, and an aggregate of incompatible properties.",
        "method": "Four live negative controls through the landed scorecard API in this run; each must refuse with an explicit reason.",
        "observed": {"negative_controls": probes},
        "status": "SATISFIED",
    }


def independence_criterion(board: dict) -> dict:
    passing = synthetic_row("PASS")
    failing = synthetic_row("FAIL")
    refused = synthetic_row("NO VERDICT")
    summaries = {
        "pass_and_fail": summarize_rows([passing, failing]),
        "pass_and_no_verdict": summarize_rows([passing, refused]),
    }
    require(
        summaries["pass_and_fail"]["coverage"] == summaries["pass_and_no_verdict"]["coverage"],
        "coverage counts changed with the verdict",
    )
    require(
        summaries["pass_and_fail"]["verdicts"] != summaries["pass_and_no_verdict"]["verdicts"],
        "verdict counts did not change between the two control sets",
    )
    development = board["partitions"]["development"]
    require(
        sum(development["raw_row_counts"].values()) == 0,
        "the board already carries accepted rows; re-derive this review",
    )
    require(development["expected_rows"] > 0, "the board has no coverage denominator")
    return {
        "id": "AC6",
        "statement": "Coverage is reportable independently from error and verdict.",
        "method": "Live summaries over synthetic control rows whose verdicts differ and coverage does not, plus the generated board's coverage denominators with zero accepted rows.",
        "observed": {
            "control_summaries": summaries,
            "board_development_expected_rows": development["expected_rows"],
            "board_development_raw_row_counts": development["raw_row_counts"],
            "board_development_outcomes": development["outcomes"],
        },
        "status": "SATISFIED",
    }


def corpus_accounting(manifest: dict, rows: dict, board: dict, registry_document: dict) -> dict:
    kinds: dict[str, int] = {}
    for case in manifest["cases"]:
        kinds[case["kind"]] = kinds.get(case["kind"], 0) + 1
    development = board["partitions"]["development"]
    holdout = board["partitions"]["holdout"]
    required = len(registry_document["required_rows"])
    require(
        development["expected_rows"] == development["allocated_cases"] * required,
        "board development rows disagree with the required-row inventory",
    )
    return {
        "directed_cases": len(manifest["cases"]),
        "directed_cases_by_kind": dict(sorted(kinds.items())),
        "canonical_names_per_case": len(rows),
        "resolved_name_value_pairs": len(manifest["cases"]) * len(rows),
        "required_rows_per_case": required,
        "registry_development_cases": development["allocated_cases"],
        "registry_development_expected_rows": development["expected_rows"],
        "registry_holdout_cases": holdout["allocated_cases"],
        "registry_holdout_expected_rows": holdout["expected_rows"],
        "directed_share_of_development_rows": len(manifest["cases"]) * required,
        "samples_per_clip": SAMPLES_PER_CLIP,
    }


def projection(manifest: dict, capture: dict, receipt: dict) -> dict:
    cases = len(manifest["cases"])
    costs = capture["measured_costs"]["by_case"]
    directed_cases = sorted(
        case["case"]["directed"] for case in capture["cases"] if "directed" in case["case"]
    )
    require(directed_cases, "the capture receipt has no directed cases to measure")
    full = [costs[case]["full"] for case in directed_cases]
    seconds = [entry["render_seconds"] for entry in full]
    retained = {entry["retained_capture_bytes"] for entry in full}
    require(len(retained) == 1, "captured full-mode retained bytes are not uniform")
    partial = {costs[case]["partial"]["retained_capture_bytes"] for case in directed_cases}
    require(len(partial) == 1, "captured partial-mode retained bytes are not uniform")
    attempt = receipt["timing"]["per_attempt_seconds"]
    full_bytes = retained.pop()
    partial_bytes = partial.pop()
    return {
        "basis": {
            "capture_host": capture["host"]["cpu"],
            "capture_receipt": CAPTURE_PATH,
            "capture_runtime_profile": capture["runtime_profile"],
            "corpus_receipt": CORPUS_RECEIPT_PATH,
            "corpus_receipt_cases": receipt["counts"]["observed"],
            "corpus_host": receipt["host"]["host_cpu"],
            "measured_directed_cases": directed_cases,
        },
        "render_seconds_per_case": {
            "maximum": max(seconds),
            "median": median(seconds),
            "minimum": min(seconds),
        },
        "projected_render_seconds": {
            "maximum": round(cases * max(seconds), 3),
            "median": round(cases * median(seconds), 3),
            "minimum": round(cases * min(seconds), 3),
        },
        "publishing_seconds_per_case": {
            "maximum": attempt["max"],
            "median": attempt["median"],
            "minimum": attempt["min"],
        },
        "projected_publishing_seconds": {
            "maximum": round(cases * attempt["max"], 3),
            "median": round(cases * attempt["median"], 3),
            "minimum": round(cases * attempt["min"], 3),
        },
        "retained_bytes_per_case": {"full": full_bytes, "partial": partial_bytes},
        "projected_retained_bytes": {
            "full": cases * full_bytes,
            "partial": cases * partial_bytes,
        },
        "limits": [
            "Derived arithmetic over pinned measured receipts, not a measured directed-corpus run.",
            "Render seconds come from three batch-32 directed captures with full trace capture; publishing seconds come from a 96-case random-corpus run that also validated and published artifacts. Both receipts record one host CPU under the release runtime profile, so neither is a multi-host or CI budget.",
            "Projections exclude container start-up, source validation, estimator execution and evidence validation.",
        ],
    }


def truth_classification(manifest: dict, basis: dict, registry: dict) -> dict:
    canonical = {trace["name"] for trace in registry["traces"]}
    declared = basis["traces"]
    require(set(declared) == canonical, "the truth basis does not cover exactly the canonical traces")
    codes: dict[str, int] = {code: 0 for code in BASIS_CODES}
    for name, entry in sorted(declared.items()):
        require(entry["basis"] in BASIS_CODES, f"unknown truth basis for {name}")
        require(entry["condition"].strip(), f"truth basis without a condition for {name}")
        codes[entry["basis"]] += 1
    require(
        set(basis["family_rules"]) == {"analytically_silent", "noise_audible", "normalization_targets"},
        "the declared family rules changed; re-review before regenerating",
    )
    silent: list[str] = []
    noise: list[str] = []
    combinations: dict[str, int] = {}
    for case in manifest["cases"]:
        active = audible_sources(directed.resolve_patch(manifest, case))
        key = "+".join(active) if active else "none"
        combinations[key] = combinations.get(key, 0) + 1
        if not active:
            silent.append(case["id"])
        if "noise" in active:
            noise.append(case["id"])
    return {
        "basis_version": basis["version"],
        "traces_by_basis": codes,
        "trace_basis": {name: entry["basis"] for name, entry in sorted(declared.items())},
        "audible_source_combinations": dict(sorted(combinations.items())),
        "fixtures_without_analytic_final_output": {
            "count": len(noise),
            "ids": sorted(noise),
            "rule": basis["family_rules"]["noise_audible"]["rule"],
        },
        "fixtures_with_analytically_silent_output": {
            "count": len(silent),
            "ids": sorted(silent),
            "limit": basis["family_rules"]["analytically_silent"]["limit"],
            "rule": basis["family_rules"]["analytically_silent"]["rule"],
        },
        "fixtures_whose_peak_must_be_measured": {
            "count": len(manifest["cases"]) - len(silent),
            "rule": "Every fixture with any audible source: a whole-clip extremum has no closed form.",
        },
    }


def referenced_fixtures(manifest: dict) -> dict:
    ids = {case["id"]: case["kind"] for case in manifest["cases"]}
    per_record: dict[str, list[str]] = {}
    union: set[str] = set()
    for relative in CONSUMER_PATHS:
        text = read_bytes(relative).decode("utf-8")
        found = sorted(case for case in ids if f'"{case}"' in text)
        per_record[relative] = found
        union.update(found)
    by_kind: dict[str, dict[str, int]] = {}
    for case, kind in sorted(ids.items()):
        entry = by_kind.setdefault(kind, {"preregistered": 0, "referenced": 0})
        entry["preregistered"] += 1
        if case in union:
            entry["referenced"] += 1
    return {
        "note": "A name-reference count over a reviewed snapshot of landed records. A reference is not a render, a verdict, or a qualification.",
        "records": dict(sorted(per_record.items())),
        "referenced_by_kind": by_kind,
        "referenced_fixtures": sorted(union),
        "referenced_total": len(union),
        "unreferenced_total": len(ids) - len(union),
    }


def build_review() -> dict:
    basis = load_json(ROOT / BASIS_PATH)
    manifest = load_json(ROOT / MANIFEST_PATH)
    coverage = load_json(ROOT / COVERAGE_PATH)
    registry = load_registry()
    case_registry = load_json(ROOT / CASE_REGISTRY_PATH)
    board = load_json(ROOT / BOARD_PATH)
    capture = load_json(ROOT / CAPTURE_PATH)
    receipt = load_json(ROOT / CORPUS_RECEIPT_PATH)
    rows = directed.inventory_rows()
    require(basis["source_target"] == SOURCE_TARGET, "the truth basis pins another upstream")
    case_ids = coverage_case_ids(coverage)
    manifest_ids = {case["id"] for case in manifest["cases"]}
    require(
        case_ids == manifest_ids,
        "coverage intents and manifest cases are not the same set of fixtures",
    )
    criteria = [
        parameter_criterion(coverage, rows, manifest_ids),
        trace_criterion(coverage, registry, capture),
        normalization_criterion(coverage, capture),
        manifest_criterion(manifest, rows, case_registry),
        scorecard_criterion(),
        independence_criterion(board),
        {
            "id": "AC7",
            "statement": "Review documents expected corpus size/runtime and identifies fixtures that cannot have analytic truth.",
            "method": "This review: corpus accounting recomputed from the manifest and registry, runtime and storage projected from pinned measured receipts, and the declared truth basis joined to per-fixture activation facts.",
            "observed": {
                "document": "spec/DIRECTED-PHASE-REVIEW.md",
                "report": REPORT_PATH,
                "truth_basis": BASIS_PATH,
            },
            "status": "SATISFIED",
        },
    ]
    return {
        "corpus": corpus_accounting(manifest, rows, board, case_registry),
        "criteria": criteria,
        "evidence_references": referenced_fixtures(manifest),
        "inputs": {relative: digest(read_bytes(relative)) for relative in sorted(INPUT_PATHS)},
        "limits": [
            "A preparation and contract review. It renders no audio, runs no estimator, and establishes no Voice, fixed-point, synthesis, layout, signoff or playback claim.",
            "Criterion status is a documentation judgement over landed artifacts, never a scorecard verdict; SATISFIED-AS-PREPARATION means the plan is complete while execution is not.",
            "The manifest coverage report still declares its trace claims planned and its normalization peaks unmeasured; executed captures live in their own receipts and are cited, not merged into it.",
            "No tolerance, error metric, estimator qualification or rubric is introduced, changed, or implied.",
        ],
        "outstanding": [
            {
                "item": "audio_render",
                "state": coverage["audio_render"],
                "note": "The manifest's own coverage report is a static preparation result; three normalization fixtures have an executed upstream capture recorded in sim/reference/trace-capture.json.",
            },
            {
                "item": "expected_values_and_tolerances",
                "state": "null with declared reasons",
                "note": "All required registry rows are paired reference-versus-candidate comparisons under an unfrozen rubric; no row carries an analytic expected value today.",
            },
            {
                "item": "candidate_engine",
                "state": case_registry["inputs"]["implementation"]["identity"],
                "note": "The registered engine is unimplemented, so every development case is NOT RUN by construction.",
            },
        ],
        "projection": projection(manifest, capture, receipt),
        "review": REVIEW,
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "scope": "Phase-level closure review of issue #5 over the landed work of #16, #17, #18 and #87: coverage synthesis, corpus size and runtime expectation, and analytic-truth availability.",
        "source_target": SOURCE_TARGET,
        "truth": truth_classification(manifest, basis, registry),
        "work_units": [16, 17, 18, 87],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", default=REPORT_PATH, help="output path for the review report")
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare the committed report byte for byte and write nothing",
    )
    arguments = parser.parse_args(argv)
    target = Path(arguments.report)
    if not target.is_absolute():
        target = ROOT / target
    try:
        document = encode(build_review())
    except ReviewError as error:
        print(f"ERROR: {error}")
        return 1
    if arguments.check:
        if not target.is_file():
            print(f"ERROR: missing report {target}")
            return 1
        if target.read_bytes() != document:
            print(f"ERROR: {arguments.report} differs from the regenerated review")
            return 1
        print(f"review report is identical: {arguments.report}")
        return 0
    target.write_bytes(document)
    print(f"wrote {arguments.report} ({digest(document)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
