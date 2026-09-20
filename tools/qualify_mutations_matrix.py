"""Bounded #34 bidirectional mutation-coverage matrix (stdlib host).

Default mode composes the three landed fault-operator families of
spec/MUTATIONS.md (#31 identity, #32 timing, #33 signal) into the single
bidirectional coverage matrix. The landed family runners are re-executed
first (``--check``: every fault re-trips its detector against a fresh
in-memory rerun, so the matrix is rerun, never grandfathered), the
framework and runtime-bridge publications are re-verified as substrate
liveness, and only then is the matrix composed from the just-reverified
publications. The runner itself injects no fault beyond plan-time
composition-gate proofs; its evidence is receipts-only and bounded to the
landed directed-fixture protocols.

Published bidirectional evidence (``sim/reference/mutation-matrix-v1.json``):
the faults-to-tests matrix (every fault row with magnitude, applicability,
validity, false-positive evidence, missed faults and raw row links) and the
tests-to-faults matrix (every mandatory detector with the faults it must
catch, wrong-then-right counts and baseline status). Below-floor probes,
degenerate coverage rows and out-of-applicability normalization cells are
labeled ``sensitivity-NO VERDICT`` and are never counted as passes or
detected faults. The deterministic CI subset covers identity, delay,
interpolation, gain and normalization. No scalar mutation score replaces
rows: counts are navigation aids only.

``--check`` strictly revalidates the committed matrix against a fresh
composition (including the family reruns) and the current input digests.
Absence is reported as absent, never as a pass; staleness fails.

Analytic apparatus domain only: holdout stays sealed, no actual-Voice
runtime injection is claimed, no numeric format is ratified, and the #44
blind-listening protocol and #48 rubric qualification are recorded not-run
(their consumption contracts are declared, not executed).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import mutations  # noqa: E402
from torchsynth_voice import mutation_runtime  # noqa: E402
from torchsynth_voice import mutations_identity  # noqa: E402
from torchsynth_voice import mutations_signal  # noqa: E402
from torchsynth_voice import mutations_timing  # noqa: E402

PUBLICATION_PATH = ROOT / "sim/reference/mutation-matrix-v1.json"

FAMILY_PUBLICATIONS = {
    "identity": "sim/reference/mutation-identity-v1.json",
    "timing": "sim/reference/mutation-timing-v1.json",
    "signal": "sim/reference/mutation-signal-v1.json",
}

RERUN_TOOLS = (
    ("framework", "tools/qualify_mutations.py", "--check"),
    ("runtime-bridge", "tools/qualify_mutations_runtime.py", "--check-publication"),
    ("identity", "tools/qualify_mutations_identity.py", "--check"),
    ("timing", "tools/qualify_mutations_timing.py", "--check"),
    ("signal", "tools/qualify_mutations_signal.py", "--check"),
)

INPUT_PATHS = (
    "src/torchsynth_voice/mutations.py",
    "src/torchsynth_voice/mutation_runtime.py",
    "src/torchsynth_voice/mutations_identity.py",
    "src/torchsynth_voice/mutations_timing.py",
    "src/torchsynth_voice/mutations_signal.py",
    "tools/qualify_mutations_identity.py",
    "tools/qualify_mutations_timing.py",
    "tools/qualify_mutations_signal.py",
    "spec/MUTATIONS.md",
    "spec/reference/mutation-seams-v1.json",
    "sim/reference/mutation-identity-v1.json",
    "sim/reference/mutation-timing-v1.json",
    "sim/reference/mutation-signal-v1.json",
)

NOT_RUN = (
    "actual-Voice runtime injection of family operators (DR-0006 gated host "
    "run; the landed runtime bridge stays the #30 test-only proofs)",
    "holdout partitions (sealed; directed development fixtures only and the "
    "case_registry access gate stays untouched)",
    "#44 blind-listening protocol execution (consumes selected mutations "
    "from this matrix; the selection contract is declared, not exercised)",
    "#48 rubric qualification (consumes this matrix's coverage; the "
    "coverage rows are published, the rubric verdict is not)",
    "numeric-format selection or ratification (declared fault magnitudes "
    "are mutation parameters; DR-0008 stays Proposed)",
    "cross-family composed fault execution (no cross-family pair is "
    "declared composable; the executed evidence is the plan-time refusal "
    "of undeclared cross-family compositions)",
)

CI_SUBSET_RULE = (
    "deterministic operator-prefix selection over the faults-to-tests rows: "
    "a row joins a category when any of its operators starts with one of the "
    "category prefixes; categories identity/delay/interpolation/gain/"
    "normalization must all be non-empty and every selected row must be "
    "detected with an accepted control"
)

CI_SUBSET_CATEGORIES = {
    "identity": ("identity.", "param.", "noise."),
    "delay": ("timing.delay_audio_sample", "timing.delay_control_sample"),
    "interpolation": ("interp.",),
    "gain": ("gain.",),
    "normalization": ("norm.",),
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def register_families() -> dict:
    """Register the landed families and attribute every operator to its owner."""
    base = sorted(mutations.OPERATORS)
    families = {"framework": base}
    for name, module in (
        ("identity", mutations_identity),
        ("timing", mutations_timing),
        ("signal", mutations_signal),
    ):
        module.register_family()
        families[name] = sorted(set(mutations.OPERATORS) - set(base))
        base = sorted(mutations.OPERATORS)
    return families


def run_family_reruns() -> dict:
    """Re-execute the landed verification entry points (rerun, not grandfathered)."""
    reruns = {}
    for name, tool, flag in RERUN_TOOLS:
        completed = subprocess.run(
            [sys.executable, str(ROOT / tool), flag],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            timeout=600,
        )
        if completed.returncode != 0:
            raise SystemExit(
                "FAIL: landed rerun "
                + tool
                + " "
                + flag
                + " exited "
                + str(completed.returncode)
                + ":\n"
                + completed.stdout[-2000:]
                + completed.stderr[-2000:]
            )
        summary = {"tool": tool + " " + flag, "status": "PASS"}
        for line in reversed(completed.stdout.strip().splitlines()):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                summary["counts"] = {
                    key: payload[key]
                    for key in sorted(payload)
                    if isinstance(payload[key], int)
                }
                break
        reruns[name] = summary
    return reruns


def operator_parts(row_operator: str) -> list:
    return [part.strip() for part in row_operator.split("+")]


def magnitude_domains(operators: list) -> list:
    domains = []
    for operator_id in operators:
        definition = mutations.OPERATORS[operator_id]
        domains.append(
            {
                "operator": operator_id,
                "magnitude": dict(definition["magnitude"]),
                "binding": (
                    "executed value is bound into the mp1- plan identity; "
                    "the plan identity changes if and only if the magnitude, "
                    "operator or selection changes"
                ),
            }
        )
    return domains


def envelope_links(publication: dict) -> dict:
    """Map each fault name to the evidence envelopes that bind it."""
    links = {}
    envelopes = list(publication.get("envelopes") or [])
    if "envelope" in publication:
        envelopes.append(publication["envelope"])
    for envelope in envelopes:
        for entry in envelope["fault_matrix"]:
            links.setdefault(entry["fault"], set()).add(envelope["envelope_id"])
    return links


def case_applicability(family: str, publication: dict, fault: str) -> dict:
    """The directed fixture surface each fault was executed over."""
    cases = set()
    if family == "identity":
        for receipt in publication["case_receipts"]:
            if receipt["fault"] == fault:
                cases.add(receipt["case"])
    elif family == "timing":
        for envelope in publication["envelopes"]:
            if any(entry["fault"] == fault for entry in envelope["fault_matrix"]):
                cases.add(envelope["source_binding"]["case_id"])
    else:
        for envelope in [publication["envelope"]]:
            if any(entry["fault"] == fault for entry in envelope["fault_matrix"]):
                cases.add(envelope["source_binding"]["case_id"])
    applicability = {"directed_cases": sorted(cases)}
    if not cases:
        applicability["note"] = (
            "executed on the family's directed lanes without a dedicated "
            "per-case mu1- envelope; the family fault-matrix row and its "
            "paired detector rows are the raw evidence"
        )
    if family == "identity":
        applicability["representative_random_cases"] = len(
            publication["random_receipts"]
        )
    if fault.startswith("norm."):
        applicability["normalization_classes"] = sorted(
            cell["fixture_class"]
            for cell in publication["normalization_coverage"]
            if cell["fault"] == fault
        )
    return applicability


def applicability_surfaces(publications: dict) -> dict:
    """The executed fixture surface per family, from the landed counts."""
    identity = publications["identity"]
    timing = publications["timing"]
    signal = publications["signal"]
    return {
        "identity": {
            "directed_cases": identity["counts"]["directed_cases"],
            "representative_random_cases": identity["counts"][
                "representative_cases"
            ],
            "random_receipts": identity["counts"]["random_receipts"],
        },
        "timing": {
            "coverage_cases": timing["counts"]["coverage_cases"],
            "floor_probes": timing["counts"]["floor_probes"],
            "envelopes": len(timing["envelopes"]),
        },
        "signal": {
            "directed_fixture": signal["fixture"]["binding"]["case_id"],
            "normalization_classes": sorted(
                {cell["fixture_class"] for cell in signal["normalization_coverage"]}
            ),
            "normalization_cells": signal["counts"]["normalization_cells"],
        },
    }


def false_positive_guards(publications: dict) -> dict:
    """Clean-input evidence that the detectors did not fire on controls."""
    identity = publications["identity"]
    plausible = sum(
        1
        for receipt in identity["case_receipts"]
        for row in receipt["detector_rows"]
        if row["property"].startswith("noise.ac.") and row["verdict"] == "PASS"
    )
    signal = publications["signal"]
    tolerant = sum(
        1
        for record in signal["optional_observations"].values()
        if record.get("tolerant")
    )
    timing = publications["timing"]
    return {
        "identity": {
            "statistical_noise_rows_plausible_under_faults": plausible,
            "controls_passed": identity["counts"]["controls_passed"],
        },
        "timing": {
            "clean_control_paired_rows": timing["counts"]["faults"],
            "controls_passed": timing["counts"]["controls_passed"],
        },
        "signal": {
            "tolerant_optional_rows_clean": tolerant,
            "controls_passed": signal["counts"]["controls_passed"],
        },
    }


def build_faults_to_tests(families: dict, publications: dict, catalog: dict) -> list:
    """One row per executed fault with the published evidence columns."""
    family_of_operator = {}
    for family, operator_ids in families.items():
        if family == "framework":
            continue
        for operator_id in operator_ids:
            family_of_operator[operator_id] = family
    rows = []
    for family in ("identity", "timing", "signal"):
        publication = publications[family]
        links = envelope_links(publication)
        controls_passed = publication["counts"]["controls_passed"]
        for entry in publication["fault_matrix"]:
            operators = operator_parts(entry["operator"])
            unknown = [op for op in operators if op not in mutations.OPERATORS]
            if unknown:
                raise SystemExit(
                    "FAIL: matrix row names unregistered operator(s): "
                    + ", ".join(unknown)
                )
            operator_family = {family_of_operator[op] for op in operators}
            if len(operator_family) != 1 or operator_family != {family}:
                raise SystemExit(
                    "FAIL: composed row crosses family ownership: "
                    + entry["operator"]
                )
            if "fail-closed at plan time" in entry["downstream"]:
                # Plan-time refusal row: the fault is caught by the plan
                # validator refusing the declared seam, so the injection
                # topology is the catalog's non-writability, not the
                # operator's registered seam.
                seam_entry = catalog["seams"].get(entry["seam"])
                if seam_entry is None or seam_entry["writable"]:
                    raise SystemExit(
                        "FAIL: plan-time refusal row does not name a "
                        "non-writable catalog seam: " + entry["fault"]
                    )
            else:
                seams = {mutations.OPERATORS[op]["seam"] for op in operators}
                if seams != {entry["seam"]}:
                    raise SystemExit(
                        "FAIL: row seam does not match the registered "
                        "injection topology: " + entry["fault"]
                    )
            detected = bool(entry["tripped"]) and bool(entry["control_accepted"])
            if not detected:
                raise SystemExit(
                    "FAIL: required fault is not caught by its mandatory "
                    "detector: " + entry["fault"]
                )
            rows.append(
                {
                    "family": family,
                    "fault": entry["fault"],
                    "operators": operators,
                    "seam": entry["seam"],
                    "magnitude": magnitude_domains(operators),
                    "applicability": case_applicability(
                        family, publication, entry["fault"]
                    ),
                    "validity": "detected",
                    "false_positives": {
                        "clean_control_accepted": bool(entry["control_accepted"]),
                        "family_controls_passed": controls_passed,
                        "expected_refusal": entry["expected_refusal"],
                    },
                    "missed_faults": [],
                    "links": {
                        "publication": FAMILY_PUBLICATIONS[family],
                        "downstream": entry["downstream"],
                        "observed_refusal": entry["observed_refusal"],
                        "envelope_ids": sorted(links.get(entry["fault"], ())),
                    },
                }
            )
    return rows


def build_tests_to_faults(faults_to_tests: list, guards: dict) -> list:
    """One row per mandatory detector with wrong-then-right counts."""
    grouped = {}
    for row in faults_to_tests:
        key = (row["family"], row["links"]["downstream"])
        grouped.setdefault(key, []).append(row)
    detector_rows = []
    for (family, downstream), rows in sorted(grouped.items()):
        wrong = sum(1 for row in rows if row["validity"] == "detected")
        right = sum(
            1 for row in rows if row["false_positives"]["clean_control_accepted"]
        )
        detector_rows.append(
            {
                "family": family,
                "detector": downstream,
                "expected_faults": sorted(row["fault"] for row in rows),
                "wrong_then_right": {
                    "wrong_observed": wrong,
                    "right_observed": right,
                    "note": (
                        "expected failing control observed (fault tripped the "
                        "detector) then clean case observed (paired control "
                        "accepted) for every bound fault"
                    ),
                },
                "expected_refusals": sorted(
                    {row["false_positives"]["expected_refusal"] for row in rows}
                ),
                "baseline": (
                    "no unexplained baseline failure: every bound fault row "
                    "carries an accepted clean control and every family "
                    "control passes"
                ),
                "false_positive_guards": guards[family],
                "links": {
                    "publications": sorted(
                        {row["links"]["publication"] for row in rows}
                    ),
                    "envelope_ids": sorted(
                        {
                            envelope
                            for row in rows
                            for envelope in row["links"]["envelope_ids"]
                        }
                    ),
                },
            }
        )
    return detector_rows


def build_sensitivity(publications: dict) -> dict:
    """Below-floor and out-of-applicability evidence: labeled, never passes."""
    floor_probes = [
        {
            "probe": probe["probe"],
            "operator": probe["operator"],
            "magnitude_control_samples": probe["magnitude_control_samples"],
            "qualified_resolution_control_samples": probe[
                "qualified_resolution_control_samples"
            ],
            "detected": probe["detected"],
            "validity": (
                "detected" if probe["detected"] else "sensitivity-NO VERDICT"
            ),
        }
        for probe in publications["timing"]["floor_probes"]
    ]
    if any(probe["validity"] != "sensitivity-NO VERDICT" for probe in floor_probes):
        below = [p for p in floor_probes if p["detected"]]
    else:
        below = []
    coverage = [
        {
            "coverage_case": row["coverage_case"],
            "domain": row["domain"],
            "property": row["property"],
            "verdict": row["verdict"],
            "validity": (
                "detected"
                if row["verdict"] == "FAIL"
                else "sensitivity-NO VERDICT"
            ),
        }
        for row in publications["timing"]["coverage"]["envelope_and_high_rate"]
    ]
    cells = [
        {
            "fault": cell["fault"],
            "fixture_class": cell["fixture_class"],
            "peak_target": cell["peak_target"],
            "status": cell["status"],
            "validity": (
                "detected"
                if cell["status"] == "detected"
                else "sensitivity-NO VERDICT"
            ),
        }
        for cell in publications["signal"]["normalization_coverage"]
    ]
    return {
        "policy": (
            "below-floor mutations, degenerate coverage rows and "
            "out-of-applicability normalization cells are labeled "
            "sensitivity-NO VERDICT; they are never counted as passes and "
            "never as detected faults"
        ),
        "timing_floor_probes": floor_probes,
        "timing_coverage_rows": coverage,
        "signal_normalization_cells": cells,
        "counts": {
            "sensitivity_no_verdict": (
                sum(1 for p in floor_probes if p["validity"] != "detected")
                + sum(1 for c in coverage if c["validity"] != "detected")
                + sum(1 for c in cells if c["validity"] != "detected")
            ),
            "detected_inside_applicability": (
                sum(1 for p in floor_probes if p["validity"] == "detected")
                + sum(1 for c in coverage if c["validity"] == "detected")
                + sum(1 for c in cells if c["validity"] == "detected")
            ),
        },
    }


def build_localization(families: dict, publications: dict) -> dict:
    """Trace localization must match the registered injection topology."""
    signal = publications["signal"]
    records = {}
    for fault, record in sorted(signal["localization"].items()):
        sources = record["source_traces_changed"]
        siblings = record["sibling_traces_unchanged"]
        all_traces = set(sources) | set(siblings)
        declared = set(mutations_signal.POST_VCA_TRACES)
        records[fault] = {
            "source_traces_changed": sources,
            "sibling_traces_unchanged": siblings,
            "downstream_mix_changed": record["downstream_mix_changed"],
            "topology_match": bool(
                len(sources) == 1
                and sources[0] in declared
                and all_traces == declared
                and record["downstream_mix_changed"]
            ),
        }
    identity = publications["identity"]
    timing = publications["timing"]
    return {
        "signal_trace_records": records,
        "sibling_invariance_controls": {
            "identity": identity["controls"]["sibling_case_bytes_unchanged_by_faulted_case"],
            "timing": timing["controls"]["sibling_case_bytes_unchanged_by_faulted_case"],
        },
        "row_seam_matches_registry": True,
    }


GATE_MAGNITUDES = {
    "identity.batch_shift": 32,
    "timing.drop_sample": 1,
    "gain.db": 1.0,
    "osc.tuning_shift": 1.0,
    "noise.seed_shift": 1,
    "noise.slot_shift": 1,
}


def composition_gate_proofs(catalog: dict) -> dict:
    """Plan-time proof that cross-family composition stays gated."""
    binding = {
        "case_id": "directed:mutation-matrix:composition-gate",
        "partition": "development",
        "fixture_identity": sha256(b"matrix-composition-gate"),
    }

    def gate_instance(tag, operator_id):
        return mutation_runtime.instance(
            "mmg-" + tag,
            operator_id,
            mutations.OPERATORS[operator_id]["seam"],
            GATE_MAGNITUDES[operator_id],
            _gate_configuration(operator_id),
        )

    def refused(left, right):
        try:
            mutations.make_plan(
                binding,
                [gate_instance("left", left), gate_instance("right", right)],
                catalog,
            )
        except mutations.MutationError as error:
            message = str(error)
            if "undeclared combination" not in message:
                raise SystemExit(
                    "FAIL: cross-family gate refused for the wrong reason: "
                    + message
                )
            return {"left": left, "right": right, "refusal": message[:256]}
        raise SystemExit(
            "FAIL: undeclared cross-family composition was accepted: "
            + left
            + " + "
            + right
        )

    wrong = [
        refused("identity.batch_shift", "timing.drop_sample"),
        refused("timing.drop_sample", "gain.db"),
        refused("osc.tuning_shift", "noise.seed_shift"),
    ]
    accepted_plan = mutations.make_plan(
        binding,
        [gate_instance("slot", "noise.slot_shift"), gate_instance("seed", "noise.seed_shift")],
        catalog,
    )
    return {
        "wrong_refused": wrong,
        "right_accepted": {
            "operators": ["noise.slot_shift", "noise.seed_shift"],
            "plan_id": accepted_plan["plan_id"],
            "note": (
                "the declared intra-family pair validates at plan time; "
                "plan-time validation only, no fault executed here"
            ),
        },
    }


def _gate_configuration(operator_id: str) -> dict:
    """Minimal valid configuration for a gate-proof instance."""
    schema = mutations.OPERATORS[operator_id]["configuration"]
    configuration = {}
    for key, allowed in schema.items():
        if type(allowed) is list:
            configuration[key] = allowed[0]
        else:
            configuration[key] = 0
    return configuration


def build_composition_coverage(families: dict, publications: dict, catalog: dict) -> dict:
    """Declared composition graph plus the executed composition evidence."""
    family_of_operator = {}
    for family, operator_ids in families.items():
        for operator_id in operator_ids:
            family_of_operator[operator_id] = family
    declared_pairs = []
    cross_family = []
    for operator_id in sorted(mutations.OPERATORS):
        for partner in mutations.OPERATORS[operator_id]["composes_with"]:
            pair = {
                "left": operator_id,
                "right": partner,
                "left_family": family_of_operator.get(operator_id, "framework"),
                "right_family": family_of_operator.get(partner, "framework"),
            }
            declared_pairs.append(pair)
            if pair["left_family"] != pair["right_family"]:
                cross_family.append(pair)
    composed_rows = []
    for family in ("identity", "timing", "signal"):
        for entry in publications[family]["fault_matrix"]:
            parts = operator_parts(entry["operator"])
            if len(parts) > 1:
                composed_rows.append(
                    {
                        "family": family,
                        "fault": entry["fault"],
                        "operators": parts,
                        "declared_order_evidence": entry["observed_refusal"],
                        "validity": "detected",
                    }
                )
    proofs = composition_gate_proofs(catalog)
    return {
        "declared_pairs": declared_pairs,
        "cross_family_declared_pairs": cross_family,
        "executed_composed_rows": composed_rows,
        "gate_proofs": proofs,
    }


def build_ci_subset(faults_to_tests: list) -> dict:
    """The deterministic CI subset: identity, delay, interpolation, gain, normalization."""
    categories = {}
    for category, prefixes in CI_SUBSET_CATEGORIES.items():
        rows = [
            row["fault"]
            for row in faults_to_tests
            if any(
                operator.startswith(prefixes)
                for operator in row["operators"]
            )
        ]
        if not rows:
            raise SystemExit(
                "FAIL: deterministic CI subset category is empty: " + category
            )
        categories[category] = sorted(set(rows))
    selected = sorted({fault for rows in categories.values() for fault in rows})
    by_fault = {row["fault"]: row for row in faults_to_tests}
    for fault in selected:
        if by_fault[fault]["validity"] != "detected":
            raise SystemExit(
                "FAIL: CI subset contains an undetected row: " + fault
            )
    return {
        "rule": CI_SUBSET_RULE,
        "categories": categories,
        "rows": selected,
        "all_detected": True,
    }


def build_publication() -> dict:
    families = register_families()
    catalog = mutations.load_seam_catalog(ROOT / mutations.SEAM_CATALOG_PATH)
    reruns = run_family_reruns()
    publications = {
        family: json.loads((ROOT / path).read_bytes())
        for family, path in FAMILY_PUBLICATIONS.items()
    }
    for family, publication in publications.items():
        if publication.get("status") != "PASS":
            raise SystemExit(
                "FAIL: family publication is not PASS: " + family
            )

    faults_to_tests = build_faults_to_tests(families, publications, catalog)
    guards = false_positive_guards(publications)
    surfaces = applicability_surfaces(publications)
    tests_to_faults = build_tests_to_faults(faults_to_tests, guards)
    sensitivity = build_sensitivity(publications)
    localization = build_localization(families, publications)
    composition = build_composition_coverage(families, publications, catalog)
    ci_subset = build_ci_subset(faults_to_tests)

    if any(pair for pair in composition["cross_family_declared_pairs"]):
        raise SystemExit(
            "FAIL: unexpected cross-family declared composition pair"
        )
    if not all(
        record["topology_match"]
        for record in localization["signal_trace_records"].values()
    ):
        raise SystemExit(
            "FAIL: trace localization does not match the injection topology"
        )

    family_counts = {
        family: {
            "faults": publications[family]["counts"]["faults"],
            "detected": sum(
                1
                for row in faults_to_tests
                if row["family"] == family and row["validity"] == "detected"
            ),
        }
        for family in ("identity", "timing", "signal")
    }
    registered = sum(
        len(operators)
        for family, operators in families.items()
        if family != "framework"
    )
    return {
        "schema_version": 1,
        "kind": "mutation-matrix-v1",
        "status": "PASS",
        "mutation_contract": "mutation-v1",
        "inputs": {
            name: sha256((ROOT / name).read_bytes()) for name in INPUT_PATHS
        },
        "registry": {
            "total_registered_operators": len(mutations.OPERATORS),
            "family_operators": registered,
            "families": {
                family: sorted(operators)
                for family, operators in sorted(families.items())
            },
            "note": (
                "framework operators are the #30 apparatus proofs and "
                "bridge.* are #30 test-only voice-runtime proofs; the "
                "matrix rows are the #31/#32/#33 family faults only"
            ),
        },
        "family_reruns": reruns,
        "applicability_surfaces": surfaces,
        "faults_to_tests": faults_to_tests,
        "tests_to_faults": tests_to_faults,
        "sensitivity": sensitivity,
        "localization_topology": localization,
        "composition_coverage": composition,
        "ci_subset": ci_subset,
        "score_policy": (
            "no scalar mutation score replaces rows; the bidirectional rows "
            "above are the evidence and all counts are navigation aids"
        ),
        "consumers": {
            "issue_44": {
                "consumes": "selected mutations for the blind-listening protocol",
                "selection_source": (
                    "faults_to_tests rows with validity=detected; audio-domain "
                    "rows carry their declared magnitudes for selection"
                ),
                "status": "not run here",
            },
            "issue_48": {
                "consumes": "this matrix's coverage for the rubric",
                "selection_source": "tests_to_faults and ci_subset coverage",
                "status": "not run here",
            },
        },
        "counts": {
            "fault_rows": len(faults_to_tests),
            "detected_rows": sum(
                1 for row in faults_to_tests if row["validity"] == "detected"
            ),
            "missed_faults": sum(
                len(row["missed_faults"]) for row in faults_to_tests
            ),
            "mandatory_detectors": len(tests_to_faults),
            "sensitivity_no_verdict": sensitivity["counts"][
                "sensitivity_no_verdict"
            ],
            "composition_declared_pairs": len(composition["declared_pairs"]),
            "composition_executed_rows": len(composition["executed_composed_rows"]),
            "ci_subset_rows": len(ci_subset["rows"]),
            "family_faults": family_counts,
        },
        "not_run": list(NOT_RUN),
    }


COMPARABLE_FIELDS = (
    "kind",
    "status",
    "mutation_contract",
    "inputs",
    "registry",
    "family_reruns",
    "applicability_surfaces",
    "faults_to_tests",
    "tests_to_faults",
    "sensitivity",
    "localization_topology",
    "composition_coverage",
    "ci_subset",
    "score_policy",
    "consumers",
    "counts",
    "not_run",
)


def check_publication() -> None:
    if not PUBLICATION_PATH.exists():
        print(
            "publication: ABSENT — requires tools/qualify_mutations_matrix.py; "
            "absence is never reported as a pass"
        )
        raise SystemExit(2)
    committed = json.loads(PUBLICATION_PATH.read_bytes())
    fresh = build_publication()
    failures = [
        field for field in COMPARABLE_FIELDS if committed.get(field) != fresh[field]
    ]
    if failures:
        print("FAIL: committed publication is stale or drifted: " + ", ".join(failures))
        raise SystemExit(1)
    print(
        json.dumps(
            {
                "status": "PASS",
                "publication": str(PUBLICATION_PATH),
                "fault_rows": fresh["counts"]["fault_rows"],
                "mandatory_detectors": fresh["counts"]["mandatory_detectors"],
                "ci_subset_rows": fresh["counts"]["ci_subset_rows"],
                "sensitivity_no_verdict": fresh["counts"]["sensitivity_no_verdict"],
            }
        )
    )


def main() -> None:
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
                "fault_rows": publication["counts"]["fault_rows"],
                "mandatory_detectors": publication["counts"]["mandatory_detectors"],
                "ci_subset_rows": publication["counts"]["ci_subset_rows"],
                "sensitivity_no_verdict": publication["counts"][
                    "sensitivity_no_verdict"
                ],
            }
        )
    )


if __name__ == "__main__":
    main()
