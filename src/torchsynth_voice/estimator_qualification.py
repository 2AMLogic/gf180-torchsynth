"""Version 1 estimator qualification ledger: validity, floors, and refusals.

A consumer/publication integration over the landed producer family grids
(#25 paired metrics, #26 periodic, #27 envelope/routes, #28 spectral/noise/
mix, #86 signal preparation). It owns no estimator algorithm, derives no
tolerance from fixed-point, holdout, or future RTL data, and never rewrites
producer evidence. Refusals serialize as ``NO VERDICT`` (DR-0004); coverage
and floor-limited cases are counted separately from PASS/FAIL.

See spec/ESTIMATOR-QUALIFICATION.md for the publication contract and
spec/reference/estimator-obligations-v1.json for the reviewed expected
inventory this module reconciles against.
"""

from __future__ import annotations

import hashlib
import json
import copy
from collections import Counter
from pathlib import Path
from typing import Any

from .scorecard import ScorecardError, validate_row

LEDGER_SCHEMA_VERSION = 1
LEDGER_ID = "estimator-qualification"
LEDGER_VERSION = "1"
FAMILIES = ("paired", "periodic", "envelope", "spectral", "preparation")
VERDICTS = ("PASS", "FAIL", "NO VERDICT", "MISSING EVIDENCE")
PREPARATION_MODULE = "src/torchsynth_voice/preparation.py"
SOURCE_TARGET = "2b0964d4c6c3d472a2a0d54d91b408caaeffca6d"

ARTIFACT_PATHS = {
    "periodic": "sim/qualification/periodic-v1.json",
    "envelope": "sim/qualification/envelope-routes-v1.json",
    "spectral": "sim/qualification/spectral-noise-mix-v1.json",
    "preparation": "sim/qualification/preparation-v1.json",
}
FAMILY_MODULES = {
    "paired": "src/torchsynth_voice/paired_metrics.py",
    "periodic": "src/torchsynth_voice/periodic_estimators.py",
    "envelope": "src/torchsynth_voice/envelope_estimators.py",
    "spectral": "src/torchsynth_voice/spectral_estimators.py",
    "preparation": PREPARATION_MODULE,
}
# Families whose published rows depend on the shared preparation layer
# qualifying (DR-0004 apparatus rule: a correct estimator behind an invalid
# apparatus is an invalid judge).
PREPARATION_DEPENDENT = ("periodic", "envelope", "spectral")

LEDGER_FIELDS = {
    "schema_version",
    "ledger",
    "ledger_version",
    "scope",
    "source_target",
    "preparation",
    "families",
    "totals",
    "artifacts",
    "provenance",
}
FAMILY_ENTRY_FIELDS = {
    "estimator",
    "artifact",
    "artifact_sha256",
    "validity_predicate",
    "grid",
    "coverage",
    "verdicts",
    "floors",
    "floor_limited",
    "refusals",
    "limitations",
    "preparation_dependent",
}
COVERAGE_FIELDS = ("requested", "present", "qualified", "refused", "missing")

_SCOPE = (
    "analytic development qualification of estimator validity, floors, and "
    "refusals over landed producer grids; holdout sealed; no fixed-point, "
    "runtime, Voice, fidelity, or hardware claim; no tolerance derived from "
    "holdout or future RTL data"
)


class EstimatorQualificationError(ValueError):
    """The ledger, an artifact, or the reviewed inventory fails completeness."""


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


def _reject_constant(value: str) -> None:
    raise EstimatorQualificationError(f"non-finite JSON number: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EstimatorQualificationError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def load_artifact(path: str | Path) -> dict[str, Any]:
    """Strictly load a producer artifact; duplicates and NaN refuse."""
    document = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(
            document,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except (ValueError, TypeError) as error:
        raise EstimatorQualificationError(f"{path}: {error}") from error
    if not isinstance(data, dict):
        raise EstimatorQualificationError(f"{path} must be a JSON object")
    return data


def _exact(value: Any, expected: Any, context: str) -> None:
    if value != expected:
        raise EstimatorQualificationError(
            f"{context}: recomputed {value!r} != expected {expected!r}"
        )


def _verdict_counter(rows: list[dict[str, Any]], context: str) -> dict[str, int]:
    for index, row in enumerate(rows):
        try:
            validate_row(row)
        except ScorecardError as error:
            raise EstimatorQualificationError(
                f"{context}[{index}] is not a scorecard v1 row: {error}"
            ) from error
    counts = Counter(row["verdict"] for row in rows)
    return {verdict: counts.get(verdict, 0) for verdict in VERDICTS}


def _total(counter: dict[str, int]) -> int:
    return sum(counter.values())


def census_paired(periodic: dict[str, Any]) -> dict[str, Any]:
    """Paired-metric apparatus census (#25) from the periodic sentinels."""
    sentinels = periodic["paired_sentinels"]
    rows: list[dict[str, Any]] = []
    for index, sentinel in enumerate(sentinels):
        sentinel_rows = sentinel["rows"]
        if not sentinel_rows:
            raise EstimatorQualificationError(
                f"paired sentinel[{index}] carries no rows"
            )
        rows.extend(sentinel_rows)
    verdicts = _verdict_counter(rows, "paired sentinel rows")
    return {
        "sentinels": len(sentinels),
        "rows": _total(verdicts),
        "verdicts": verdicts,
        "native_floors": 0,
    }


def census_periodic(artifact: dict[str, Any]) -> dict[str, Any]:
    """Periodic estimator census (#26): recomputed, never trusted from summary."""
    counts = artifact["counts"]
    records = artifact["records"]
    statuses = Counter(record["measurement"]["status"] for record in records)
    recomputed = {
        "cases": len(records),
        "valid_cases": statuses.get("valid", 0),
        "refused_cases": statuses.get("invalid", 0),
    }
    _exact(recomputed["cases"], counts["cases"], "periodic case count")
    _exact(recomputed["valid_cases"], counts["valid"], "periodic valid cases")
    _exact(recomputed["refused_cases"], counts["refused"], "periodic refused cases")
    unknown = set(statuses) - {"valid", "invalid"}
    if unknown:
        raise EstimatorQualificationError(f"periodic unknown case statuses: {unknown}")
    main_rows: list[dict[str, Any]] = []
    for record in records:
        main_rows.extend(record["rows"])
    verdicts = _verdict_counter(main_rows, "periodic main rows")
    cells = artifact["range_cells"]
    if not cells:
        raise EstimatorQualificationError("periodic range_cells must be nonempty")
    algorithms = {cell["algorithm"] for cell in cells.values()}
    if len(algorithms) != 1:
        raise EstimatorQualificationError(
            f"periodic cells mix algorithm versions: {sorted(algorithms)}"
        )
    cap_failures = sum(len(cell["cap_failures"]) for cell in cells.values())
    qualified_cells = sum(1 for cell in cells.values() if cell["qualified"] is True)
    return {
        "cases": recomputed["cases"],
        "valid_cases": recomputed["valid_cases"],
        "refused_cases": recomputed["refused_cases"],
        "range_cells": len(cells),
        "qualified_cells": qualified_cells,
        "cap_failures": cap_failures,
        "algorithm": algorithms.pop(),
        "rows": _total(verdicts),
        "verdicts": verdicts,
    }


def census_envelope(artifact: dict[str, Any]) -> dict[str, Any]:
    """Envelope/routes census (#27) with exact obligation-to-row reconciliation."""
    report = artifact["report"]
    rows = report["rows"]
    from .scorecard import validate_report

    try:
        validate_report(report)
    except ScorecardError as error:
        raise EstimatorQualificationError(f"envelope report invalid: {error}") from error
    verdicts = Counter(row["verdict"] for row in rows)
    obligations = artifact["obligations"]
    if not obligations:
        raise EstimatorQualificationError("envelope obligations must be nonempty")
    obligation_keys: set[tuple[str, str]] = set()
    for index, obligation in enumerate(obligations):
        key = (obligation["case_id"], obligation["property"])
        if key in obligation_keys:
            raise EstimatorQualificationError(
                f"envelope duplicate obligation {key}"
            )
        obligation_keys.add(key)
        if obligation["observed_verdict"] != obligation["expected_verdict"]:
            raise EstimatorQualificationError(
                f"envelope obligation {key} observed "
                f"{obligation['observed_verdict']} != expected "
                f"{obligation['expected_verdict']}"
            )
        if obligation["satisfied"] is not True:
            raise EstimatorQualificationError(
                f"envelope obligation {key} is unsatisfied"
            )
    row_keys = {(row["case"]["id"], row["property"]) for row in rows}
    unmatched = obligation_keys - row_keys
    if unmatched:
        raise EstimatorQualificationError(
            f"envelope {len(unmatched)} obligations lack any report row, "
            f"first: {sorted(unmatched)[0]}"
        )
    expected_counts = Counter(o["expected_verdict"] for o in obligations)
    floors = artifact["floors"]
    if not floors:
        raise EstimatorQualificationError("envelope floors must be nonempty")
    floor_limited = sum(int(entry.get("refused", 0)) for entry in floors.values())
    shared = artifact["shared_preparation"]
    if shared.get("status") != "executed":
        raise EstimatorQualificationError(
            "envelope shared_preparation must record executed status"
        )
    return {
        "records": len(artifact["records"]),
        "cases": len({row["case"]["id"] for row in rows}),
        "rows": len(rows),
        "verdicts": {v: verdicts.get(v, 0) for v in VERDICTS},
        "obligations": len(obligations),
        "obligation_verdicts": {
            verdict: expected_counts.get(verdict, 0) for verdict in VERDICTS
        },
        "obligations_without_rows": len(unmatched),
        "rows_beyond_obligations": len(row_keys - obligation_keys),
        "floors": len(floors),
        "floor_limited_cases": floor_limited,
        "shared_preparation_cases": len(shared["cases"]),
        "shared_preparation_source_sha256": shared["source_sha256"],
    }


def census_spectral(artifact: dict[str, Any]) -> dict[str, Any]:
    """Spectral/noise/mix census (#28) with detector-obligation accounting."""
    report = artifact["scorecard"]
    from .scorecard import validate_report

    try:
        validate_report(report)
    except ScorecardError as error:
        raise EstimatorQualificationError(f"spectral report invalid: {error}") from error
    rows = report["rows"]
    verdicts = Counter(row["verdict"] for row in rows)
    refusals = artifact["refusals"]
    if not refusals:
        raise EstimatorQualificationError("spectral refusal taxonomy must be nonempty")
    refusal_rows = sum(refusals.values())
    if refusal_rows <= 0:
        raise EstimatorQualificationError("spectral refusal rows must be positive")
    mutations = artifact["mutations"]
    exercised = 0
    for index, mutation in enumerate(mutations):
        detectors = mutation["expected_detectors"]
        observed = mutation["observed"]
        detected = any(
            observed.get(detector) == "FAIL" for detector in detectors
        )
        if detected:
            exercised += 1
        if mutation["detected"] and not detected:
            raise EstimatorQualificationError(
                f"spectral mutation[{index}] claims detection without an "
                "assigned detector FAIL"
            )
        if not mutation["detected"] and detected:
            raise EstimatorQualificationError(
                f"spectral mutation[{index}] hides an assigned detector FAIL"
            )
    floors = artifact["floors"]
    if not floors:
        raise EstimatorQualificationError("spectral floors must be nonempty")
    exceeding = sum(
        1
        for entry in floors
        if entry["max_measured_absolute_error"] > entry["preregistered_bound"]
    )
    preparation = artifact["preparation"]
    if preparation.get("status") != "measured":
        raise EstimatorQualificationError(
            "spectral preparation probe must record its measured state"
        )
    return {
        "cases": len(artifact["cases"]),
        "rows": len(rows),
        "verdicts": {v: verdicts.get(v, 0) for v in VERDICTS},
        "refusal_reasons": len(refusals),
        "refusal_rows": refusal_rows,
        "mutations": len(mutations),
        "mutations_exercised": exercised,
        "floors": len(floors),
        "floors_exceeding_bound": exceeding,
        "controls": len(preparation["checks"]),
        "ensemble_seeds": len(artifact["finite_ensemble"]["seeds"]),
    }


def census_preparation(artifact: dict[str, Any]) -> dict[str, Any]:
    """Shared preparation census (#86): analytic checks and runtime receipts."""
    checks = artifact["checks"]
    if not checks:
        raise EstimatorQualificationError("preparation checks must be nonempty")
    passed = sum(1 for check in checks if check["status"] == "pass")
    failed = len(checks) - passed
    integrations = artifact.get("actual_integrations", [])
    validated = sum(1 for item in integrations if item["evidence_status"] == "VALIDATED")
    no_verdict = sum(1 for item in integrations if item["status"] == "NO_VERDICT")
    pins = artifact["implementation_sha256"]
    return {
        "checks": len(checks),
        "checks_passed": passed,
        "checks_failed": failed,
        "status": artifact["status"],
        "analytic_runtime_integration": artifact["runtime_integration"]["status"],
        "actual_integrations": len(integrations),
        "integrations_validated": validated,
        "integrations_production_no_verdict": no_verdict,
        "module_sha256": pins[PREPARATION_MODULE],
    }


def _family_entry(
    *,
    name: str,
    module: str,
    module_digest: str,
    artifact_path: str,
    artifact_digest: str,
    algorithm_version: str,
    validity_predicate: str,
    grid: dict[str, Any],
    census: dict[str, Any],
    verdict_rows: dict[str, int],
    floors: Any,
    floor_limited: int,
    refusals: dict[str, Any],
    limitations: list[str],
    preparation_dependent: bool,
) -> dict[str, Any]:
    present = census.get("rows_present", grid.get("cases", 0))
    entry = {
        "estimator": {
            "name": name,
            "module": module,
            "module_sha256": module_digest,
            "algorithm_version": algorithm_version,
        },
        "artifact": artifact_path,
        "artifact_sha256": artifact_digest,
        "validity_predicate": validity_predicate,
        "grid": grid,
        "coverage": {
            "requested": grid["cases"],
            "present": present,
            "qualified": census.get("qualified", 0),
            "refused": census.get("refused", 0),
            "missing": census.get("missing", 0),
        },
        "verdicts": {verdict: verdict_rows.get(verdict, 0) for verdict in VERDICTS},
        "floors": floors,
        "floor_limited": floor_limited,
        "refusals": refusals,
        "limitations": limitations,
        "preparation_dependent": preparation_dependent,
    }
    if name == "paired":
        entry["coverage"] = {
            "requested": grid["sentinel_rows"],
            "present": present,
            "qualified": verdict_rows.get("PASS", 0),
            "refused": verdict_rows.get("NO VERDICT", 0),
            "missing": 0,
        }
    return entry


def build_ledger(
    root: str | Path, *, artifact_root: str | Path | None = None
) -> dict[str, Any]:
    """Build the versioned ledger from landed producer artifacts on disk.

    Recomputes every count from validated raw rows, re-derives module
    digests from the actual tree, and refuses on any completeness gap.
    ``artifact_root`` relocates the producer artifacts (executed replay)
    while module and spec digests stay bound to ``root``.
    """
    root = Path(root)
    artifacts_root = Path(artifact_root) if artifact_root else root
    artifacts: dict[str, dict[str, Any]] = {}
    digests: dict[str, str] = {}
    for name, relative in ARTIFACT_PATHS.items():
        path = artifacts_root / relative
        artifacts[name] = load_artifact(path)
        digests[name] = sha256_file(path)

    module_digests = {
        name: sha256_file(root / module)
        for name, module in FAMILY_MODULES.items()
    }

    periodic_census = census_periodic(artifacts["periodic"])
    envelope_census = census_envelope(artifacts["envelope"])
    spectral_census = census_spectral(artifacts["spectral"])
    preparation_census = census_preparation(artifacts["preparation"])
    paired_census = census_paired(artifacts["periodic"])

    recomputed_preparation = module_digests["preparation"]
    declared = {
        "envelope_shared_preparation": envelope_census[
            "shared_preparation_source_sha256"
        ],
        "preparation_artifact": preparation_census["module_sha256"],
        "spectral_probe": artifacts["spectral"]["preparation"]["source_sha256"],
    }
    inconsistent = {
        name: digest
        for name, digest in declared.items()
        if digest != recomputed_preparation
    }
    preparation_status = "consistent" if not inconsistent else "inconsistent"

    # Prepare spec-described digest resolution (SIGNAL-PREPARATION.md).
    spec_digest = _preparation_digest_from_spec(root / "spec/SIGNAL-PREPARATION.md")

    floor_limited_envelope = envelope_census["floor_limited_cases"]
    families = {
        "paired": _family_entry(
            name="paired",
            module=FAMILY_MODULES["paired"],
            module_digest=module_digests["paired"],
            artifact_path=ARTIFACT_PATHS["periodic"],
            artifact_digest=digests["periodic"],
            algorithm_version="paired-metrics-v1",
            validity_predicate=(
                "Every family row is a strict scorecard v1 row produced through "
                "the public paired_metrics adapters; the two periodic paired "
                "sentinels exercise the apparatus end to end. No native floor: "
                "the apparatus is the comparison layer, not a judged estimator."
            ),
            grid={"cases": paired_census["sentinels"], "sentinel_rows": paired_census["rows"]},
            census={"rows_present": paired_census["rows"]},
            verdict_rows=paired_census["verdicts"],
            floors=[],
            floor_limited=0,
            refusals={
                "framing": "count/rate mismatches remain producer-side refusals",
                "rows": None,
            },
            limitations=[
                "Apparatus only; framing refusals are producer-visible and are "
                "never preparation crops.",
            ],
            preparation_dependent=False,
        ),
        "periodic": _family_entry(
            name="periodic",
            module=FAMILY_MODULES["periodic"],
            module_digest=module_digests["periodic"],
            artifact_path=ARTIFACT_PATHS["periodic"],
            artifact_digest=digests["periodic"],
            algorithm_version=periodic_census["algorithm"],
            validity_predicate=(
                "A case qualifies when its measured absolute errors stay within "
                "the preregistered per-property caps and its range cell reports "
                "qualified=true with no cap failure; refusals are recorded per "
                "case with concrete reasons and never counted as passes."
            ),
            grid={
                "cases": periodic_census["cases"],
                "range_cells": periodic_census["range_cells"],
                "qualified_cells": periodic_census["qualified_cells"],
                "cap_failures": periodic_census["cap_failures"],
                "sentinel_cases": paired_census["sentinels"],
            },
            census={
                "rows_present": periodic_census["rows"],
                "qualified": periodic_census["valid_cases"],
                "refused": periodic_census["refused_cases"],
                "missing": 0,
            },
            verdict_rows=periodic_census["verdicts"],
            floors=[],
            floor_limited=periodic_census["cap_failures"],
            refusals={
                "cases": periodic_census["refused_cases"],
                "rows": periodic_census["verdicts"]["NO VERDICT"],
            },
            limitations=[
                "The artifact's production_preparation field records a historical "
                "'pending #86 runtime evidence' statement; this publication layer "
                "supersedes it with the landed #103 receipts recorded under the "
                "preparation family, without rewriting the producer artifact.",
                "Analytic development only; no Voice or runtime integration.",
            ],
            preparation_dependent=True,
        ),
        "envelope": _family_entry(
            name="envelope",
            module=FAMILY_MODULES["envelope"],
            module_digest=module_digests["envelope"],
            artifact_path=ARTIFACT_PATHS["envelope"],
            artifact_digest=digests["envelope"],
            algorithm_version="envelope-routes-qualification-v1",
            validity_predicate=(
                "Each preregistered obligation must be observed with its expected "
                "verdict and satisfied=true, and must own at least one report row; "
                "the full report validates as scorecard v1. Floors are empirical "
                "per-domain statistics kept distinct from the producer's declared "
                "limits; comparisons at the floor refuse instead of loosening it."
            ),
            grid={
                "cases": envelope_census["cases"],
                "obligations": envelope_census["obligations"],
                "shared_preparation_cases": envelope_census[
                    "shared_preparation_cases"
                ],
            },
            census={
                "rows_present": envelope_census["rows"],
                "qualified": envelope_census["obligation_verdicts"]["PASS"],
                "refused": envelope_census["obligation_verdicts"]["NO VERDICT"],
                "missing": 0,
            },
            verdict_rows=envelope_census["verdicts"],
            floors={
                "count": envelope_census["floors"],
                "unit_bound": "per-domain empirical statistics; see artifact",
            },
            floor_limited=floor_limited_envelope,
            refusals={
                "obligations_expected_no_verdict": envelope_census[
                    "obligation_verdicts"
                ]["NO VERDICT"],
                "floor_limited_cases": floor_limited_envelope,
            },
            limitations=[
                "The report carries "
                f"{envelope_census['rows_beyond_obligations']} (case, property) "
                "row keys beyond the preregistered obligations; they are counted "
                "as producer census, not silently dropped.",
                "Numeric field replay follows the producer's declared replay "
                "policy v2; this ledger asserts case/row/obligation identity, "
                "not cross-host byte portability.",
            ],
            preparation_dependent=True,
        ),
        "spectral": _family_entry(
            name="spectral",
            module=FAMILY_MODULES["spectral"],
            module_digest=module_digests["spectral"],
            artifact_path=ARTIFACT_PATHS["spectral"],
            artifact_digest=digests["spectral"],
            algorithm_version=artifacts["spectral"]["version"],
            validity_predicate=(
                "A case qualifies when every preregistered property check stays "
                "inside its analytic bound; expected refusals are exercised "
                "detection only when an assigned valid detector actually FAILs; "
                "floor entries compare measured maxima against independently "
                "preregistered bounds, kept distinct fields."
            ),
            grid={
                "cases": spectral_census["cases"],
                "mutation_detector_obligations": spectral_census["mutations"],
                "controls": spectral_census["controls"],
                "ensemble_seeds": spectral_census["ensemble_seeds"],
            },
            census={
                "rows_present": spectral_census["rows"],
                "qualified": spectral_census["verdicts"]["PASS"],
                "refused": spectral_census["refusal_rows"],
                "missing": spectral_census["verdicts"]["MISSING EVIDENCE"],
            },
            verdict_rows=spectral_census["verdicts"],
            floors={
                "count": spectral_census["floors"],
                "exceeding_preregistered_bound": spectral_census[
                    "floors_exceeding_bound"
                ],
            },
            floor_limited=spectral_census["refusal_rows"],
            refusals={
                "reasons": spectral_census["refusal_reasons"],
                "rows": spectral_census["refusal_rows"],
            },
            limitations=[
                "MISSING EVIDENCE rows and refusal rows are retained verbatim; "
                "production status is MISSING EVIDENCE and holdout stays sealed.",
                "The 21 mutation detector obligations are exercised detection "
                "counts, not fidelity margins.",
            ],
            preparation_dependent=True,
        ),
        "preparation": _family_entry(
            name="preparation",
            module=FAMILY_MODULES["preparation"],
            module_digest=module_digests["preparation"],
            artifact_path=ARTIFACT_PATHS["preparation"],
            artifact_digest=digests["preparation"],
            algorithm_version=artifacts["preparation"]["schema"],
            validity_predicate=(
                "All 70 analytic checks pass with exact record bytes; actual "
                "#12/#88 runtime receipts carry evidence_status VALIDATED while "
                "production status stays NO_VERDICT because the scalar oracle "
                "is diagnostic (DR-0007). Apparatus failure forces every "
                "dependent family row to NO VERDICT."
            ),
            grid={
                "cases": preparation_census["checks"],
                "actual_integrations": preparation_census["actual_integrations"],
            },
            census={
                "rows_present": preparation_census["checks"],
                "qualified": preparation_census["checks_passed"],
                "refused": preparation_census["checks_failed"],
                "missing": 0,
            },
            verdict_rows={
                "PASS": preparation_census["checks_passed"],
                "FAIL": preparation_census["checks_failed"],
                "NO VERDICT": 0,
                "MISSING EVIDENCE": 0,
            },
            floors=[],
            floor_limited=0,
            refusals={
                "production_no_verdict_receipts": preparation_census[
                    "integrations_production_no_verdict"
                ],
                "reason": "diagnostic oracle cannot qualify normative production rows",
            },
            limitations=[
                "The analytic artifact's runtime_integration field records "
                "PENDING; it is superseded by the four VALIDATED actual-"
                "integration receipts recorded in the same artifact, per the "
                "final disposition note in spec/SIGNAL-PREPARATION.md.",
                "No normative production oracle is claimed; scalar status stays "
                "diagnostic regardless of byte-equivalence PASS.",
            ],
            preparation_dependent=False,
        ),
    }
    for name in FAMILIES:
        entry = families[name]
        missing = FAMILY_ENTRY_FIELDS - set(entry)
        extra = set(entry) - FAMILY_ENTRY_FIELDS
        if missing or extra:
            raise EstimatorQualificationError(
                f"family {name} field mismatch: missing={missing} extra={extra}"
            )

    totals_verdicts = {
        verdict: sum(families[name]["verdicts"][verdict] for name in FAMILIES)
        for verdict in VERDICTS
    }
    ledger: dict[str, Any] = {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "ledger": LEDGER_ID,
        "ledger_version": LEDGER_VERSION,
        "scope": _SCOPE,
        "source_target": SOURCE_TARGET,
        "preparation": {
            "module": PREPARATION_MODULE,
            "recomputed_sha256": recomputed_preparation,
            "declared": declared,
            "spec_described_sha256": spec_digest,
            "status": preparation_status,
            "dependent_families": list(PREPARATION_DEPENDENT),
        },
        "families": families,
        "totals": {
            "rows": _total(totals_verdicts),
            "verdicts": totals_verdicts,
            "floor_limited": sum(families[n]["floor_limited"] for n in FAMILIES),
        },
        "artifacts": digests,
        "provenance": {
            "shared_preparation": {
                "recomputed_sha256": recomputed_preparation,
                "status": (
                    "consistent"
                    if preparation_status == "consistent"
                    and (spec_digest in (None, recomputed_preparation))
                    else "mismatch"
                ),
                "historical_snapshot_sha256": (
                    "52d42960f1fec157a48e9ba0bc35fb13619c0c6b3f4274fa711cd22c58a99426"
                ),
                "note": (
                    "The 52d42960 digest was recorded by PR101's pre-merge "
                    "snapshot only; it appears in no landed artifact. "
                    "Recomputation against the landed tree reproduces "
                    "0f8e9e6e in spec prose, the preparation-v1 pin, and the "
                    "envelope shared_preparation pin. The snapshot is retained "
                    "as history and not rewritten."
                ),
            }
        },
    }
    validate_ledger(ledger)
    return ledger


def _preparation_digest_from_spec(path: Path) -> str | None:
    text = path.read_text(encoding="utf-8")
    marker = "SHA-256\n`0f8e9e6e"
    if marker in text:
        start = text.index(marker) + len("SHA-256\n`")
        return text[start : start + 64]
    return None


def validate_ledger(ledger: dict[str, Any]) -> None:
    """Structural and arithmetic validation of a ledger document."""
    if not isinstance(ledger, dict) or set(ledger) != LEDGER_FIELDS:
        raise EstimatorQualificationError(
            f"ledger requires exactly these fields: {', '.join(sorted(LEDGER_FIELDS))}"
        )
    if ledger["schema_version"] != LEDGER_SCHEMA_VERSION:
        raise EstimatorQualificationError("unsupported ledger schema_version")
    if ledger["ledger"] != LEDGER_ID:
        raise EstimatorQualificationError("unknown ledger identity")
    if ledger["ledger_version"] != LEDGER_VERSION:
        raise EstimatorQualificationError("unknown ledger version")
    if not isinstance(ledger["scope"], str) or not ledger["scope"].strip():
        raise EstimatorQualificationError("scope must be nonblank text")
    if ledger["source_target"] != SOURCE_TARGET:
        raise EstimatorQualificationError("unexpected source target")
    if set(ledger["families"]) != set(FAMILIES):
        raise EstimatorQualificationError(
            f"families must be exactly {FAMILIES}"
        )
    for name, entry in ledger["families"].items():
        if set(entry) != FAMILY_ENTRY_FIELDS:
            raise EstimatorQualificationError(
                f"family {name} requires exactly {sorted(FAMILY_ENTRY_FIELDS)}"
            )
        estimator = entry["estimator"]
        if set(estimator) != {
            "name",
            "module",
            "module_sha256",
            "algorithm_version",
        }:
            raise EstimatorQualificationError(
                f"family {name} estimator identity malformed"
            )
        if estimator["name"] != name:
            raise EstimatorQualificationError(
                f"family {name} carries estimator name {estimator['name']}"
            )
        digest = estimator["module_sha256"]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise EstimatorQualificationError(
                f"family {name} module_sha256 must be 64 hex digits"
            )
        coverage = entry["coverage"]
        if set(coverage) != set(COVERAGE_FIELDS):
            raise EstimatorQualificationError(
                f"family {name} coverage requires {COVERAGE_FIELDS}"
            )
        if any(type(v) is not int or v < 0 for v in coverage.values()):
            raise EstimatorQualificationError(
                f"family {name} coverage counts must be nonnegative integers"
            )
        verdicts = entry["verdicts"]
        if set(verdicts) != set(VERDICTS):
            raise EstimatorQualificationError(
                f"family {name} verdicts require exactly {VERDICTS}"
            )
        if any(type(v) is not int or v < 0 for v in verdicts.values()):
            raise EstimatorQualificationError(
                f"family {name} verdict counts must be nonnegative integers"
            )
        rows = sum(verdicts.values())
        if rows != coverage["present"]:
            raise EstimatorQualificationError(
                f"family {name} verdict total {rows} != present {coverage['present']}"
            )
        if not entry["limitations"] or not all(
            isinstance(item, str) and item.strip()
            for item in entry["limitations"]
        ):
            raise EstimatorQualificationError(
                f"family {name} limitations must be nonempty text list"
            )
        if type(entry["floor_limited"]) is not int or entry["floor_limited"] < 0:
            raise EstimatorQualificationError(
                f"family {name} floor_limited must be a nonnegative integer"
            )
        if entry["preparation_dependent"] != (name in PREPARATION_DEPENDENT):
            raise EstimatorQualificationError(
                f"family {name} preparation_dependent misdeclared"
            )
    totals = ledger["totals"]
    if set(totals) != {"rows", "verdicts", "floor_limited"}:
        raise EstimatorQualificationError("totals fields malformed")
    expected = {
        verdict: sum(f["verdicts"][verdict] for f in ledger["families"].values())
        for verdict in VERDICTS
    }
    _exact(totals["verdicts"], expected, "totals.verdicts")
    _exact(
        totals["rows"],
        sum(expected.values()),
        "totals.rows",
    )
    preparation = ledger["preparation"]
    if set(preparation) != {
        "module",
        "recomputed_sha256",
        "declared",
        "spec_described_sha256",
        "status",
        "dependent_families",
    }:
        raise EstimatorQualificationError("preparation block malformed")
    if preparation["status"] not in ("consistent", "inconsistent"):
        raise EstimatorQualificationError("preparation status must be consistent/inconsistent")
    provenance = ledger["provenance"]
    if set(provenance) != {"shared_preparation"}:
        raise EstimatorQualificationError("provenance block malformed")


def ledger_to_json(ledger: dict[str, Any]) -> str:
    validate_ledger(ledger)
    return json.dumps(ledger, allow_nan=False, sort_keys=True, indent=2) + "\n"


def ledger_from_json(document: str) -> dict[str, Any]:
    try:
        ledger = json.loads(
            document,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except (ValueError, TypeError) as error:
        raise EstimatorQualificationError(str(error)) from error
    validate_ledger(ledger)
    return ledger


def gate_dependent_families(
    ledger: dict[str, Any], override_reason: str | None = None
) -> dict[str, Any]:
    """Publication view: apparatus failure gates dependent rows to NO VERDICT.

    Raw rows, verdicts, and digests are preserved untouched; only the
    publication status of preparation-dependent families changes, with a
    concrete reason. Unrelated families keep their honest state.
    """
    validate_ledger(ledger)
    preparation_ok = (
        ledger["preparation"]["status"] == "consistent"
        and not override_reason
    )
    families: dict[str, dict[str, Any]] = {}
    for name in FAMILIES:
        entry = ledger["families"][name]
        if preparation_ok:
            families[name] = {"publication": "qualified"}
        elif name in PREPARATION_DEPENDENT:
            families[name] = {
                "publication": "NO VERDICT",
                "reason": override_reason
                or "shared preparation qualification is inconsistent",
            }
        else:
            families[name] = {"publication": "unaffected"}
    return {
        "preparation_status": ledger["preparation"]["status"],
        "families": families,
        "note": (
            "Raw producer rows and diagnostics are preserved; this view never "
            "erases a failure or manufactures evidence."
        ),
    }


def check_inventory(ledger: dict[str, Any], inventory: dict[str, Any]) -> None:
    """Exact expected-set comparison against the reviewed obligations inventory.

    Compares recomputed family censuses, digest pins, and artifact digests
    with exact key sets and multiplicities; lengths and prefixes never pass.
    """
    if inventory.get("schema_version") != LEDGER_SCHEMA_VERSION:
        raise EstimatorQualificationError("inventory schema_version must be 1")
    if inventory.get("inventory") != "estimator-obligations":
        raise EstimatorQualificationError("unknown inventory identity")
    expected_families = inventory["families"]
    if set(expected_families) != set(FAMILIES):
        raise EstimatorQualificationError(
            "inventory families must be exactly the mandatory set"
        )
    for name, expected in expected_families.items():
        if not isinstance(expected, dict) or not expected:
            raise EstimatorQualificationError(
                f"inventory family {name} must be a nonempty object"
            )
        actual = _ledger_inventory_view(ledger, name)
        extra = set(actual) - set(expected)
        if extra:
            raise EstimatorQualificationError(
                f"inventory family {name} recomputed unreviewed keys: {sorted(extra)}"
            )
        for key, value in expected.items():
            if key not in actual:
                raise EstimatorQualificationError(
                    f"inventory family {name} expects unknown key {key}"
                )
            if actual[key] != value:
                raise EstimatorQualificationError(
                    f"inventory mismatch {name}.{key}: "
                    f"recomputed {actual[key]!r} != reviewed {value!r}"
                )
    expected_artifacts = inventory["artifacts"]
    if expected_artifacts != ledger["artifacts"]:
        raise EstimatorQualificationError(
            "inventory artifact digests do not match the recomputed artifacts"
        )


def _ledger_inventory_view(ledger: dict[str, Any], name: str) -> dict[str, Any]:
    entry = ledger["families"][name]
    view: dict[str, Any] = {
        "verdicts": entry["verdicts"],
        "coverage": entry["coverage"],
        "grid": entry["grid"],
        "floors": entry["floors"],
        "floor_limited": entry["floor_limited"],
        "refusals": entry["refusals"],
        "module_sha256": entry["estimator"]["module_sha256"],
        "algorithm_version": entry["estimator"]["algorithm_version"],
    }
    return copy.deepcopy(view)
