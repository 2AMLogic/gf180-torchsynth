"""Bounded #32 family qualification and evidence check (stdlib host).

Default mode runs the timing/interpolation/envelope/modulation fault family
of spec/MUTATIONS.md in-process over the landed #30 framework: the
ordinary/empty-plan/sham controls, RNG isolation, crash cleanup, the full
family fault x detector matrix (every fault must trip its landed detector
refusal while the clean control passes the same check), the declared
sensitivity/floor probes, and the degenerate-envelope and high-rate
coverage cases. It writes the bounded publication
``sim/reference/mutation-timing-v1.json`` binding ``mu1-`` evidence
envelopes per fault case.

``--check`` strictly revalidates the committed publication against a fresh
in-memory rerun and the current input digests. Absence is reported as
absent, never as a pass; staleness (any participating input changed)
fails.

Analytic apparatus domain only: this tool never renders the Voice graph,
never touches holdout, and never substitutes for the DR-0006 gated
actual-Voice runtime evidence, which is recorded not-run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import mutations, mutations_timing  # noqa: E402

PUBLICATION_PATH = ROOT / "sim/reference/mutation-timing-v1.json"

INPUT_PATHS = (
    "src/torchsynth_voice/mutations_timing.py",
    "src/torchsynth_voice/mutations.py",
    "src/torchsynth_voice/mutation_runtime.py",
    "src/torchsynth_voice/paired_metrics.py",
    "src/torchsynth_voice/envelope_estimators.py",
    "src/torchsynth_voice/periodic_estimators.py",
    "src/torchsynth_voice/scorecard.py",
    "src/torchsynth_voice/artifacts.py",
    "src/torchsynth_voice/trace_capture.py",
    "src/torchsynth_voice/trace_registry.py",
    "spec/reference/mutation-seams-v1.json",
    "sim/qualification/periodic-v1.json",
    "spec/ENVELOPE-ESTIMATORS.md",
    "spec/PERIODIC-ESTIMATORS.md",
    "spec/CONTROL-PATH.md",
)

NOT_RUN = (
    "actual-Voice runtime injection of family operators (DR-0006 gated "
    "host run; the landed voice-runtime bridge evidence is the #30 "
    "bridge.* test-only proofs, never family qualification)",
    "detector qualification of production traces (analytic apparatus "
    "domain only; no sound-fidelity claim)",
    "holdout partitions (sealed; development-only constructed fixtures)",
    "detector-family matrix publication (#34 consumes plans, events and "
    "raw evidence)",
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_publication():
    mutations_timing.register_family()
    controls = mutations_timing.qualification_controls(ROOT)
    failed_controls = [name for name, okay in controls.items() if not okay]
    if failed_controls:
        raise SystemExit("FAIL: family controls failed: " + ", ".join(failed_controls))

    matrix, case_records = mutations_timing.fault_matrix(ROOT)
    untripped = [
        entry["fault"] for entry in matrix
        if not entry["tripped"] or not entry["control_accepted"]
    ]
    if untripped:
        raise SystemExit(
            "FAIL: family faults did not trip their detectors or lacked a "
            "passing control: " + ", ".join(untripped)
        )

    floor_probes = mutations_timing.sensitivity_floor(ROOT)
    wrong_probes = [
        probe["probe"] for probe in floor_probes
        if probe["detected"] != probe["expected_detected"]
    ]
    if wrong_probes:
        raise SystemExit("FAIL: floor probes diverged: " + ", ".join(wrong_probes))
    detected_probes = [probe for probe in floor_probes if probe["detected"]]
    if not detected_probes:
        raise SystemExit("FAIL: no small mutation demonstrated measured sensitivity")

    coverage = mutations_timing.coverage_cases(ROOT)
    degenerate = [
        row for row in coverage["envelope_and_high_rate"]
        if row["domain"] == "short-degenerate-envelope"
    ]
    if not degenerate or any(row["verdict"] not in ("NO VERDICT", "FAIL")
                             for row in degenerate):
        raise SystemExit("FAIL: degenerate envelope coverage is not honest")

    envelopes = []
    for record in case_records:
        attempt = record["attempt"]
        envelope = mutations.make_envelope(
            plan=record["plan"],
            events_summary=attempt.events_summary,
            artifacts=[
                {
                    "path": path,
                    "sha256": sha256(data),
                    "size_bytes": len(data),
                }
                for path, data in sorted(attempt.store.items())
            ],
            controls=controls,
            fault_matrix=[
                entry for entry in matrix if entry["fault"] in record["faults"]
            ],
            runtime_scope="stdlib-apparatus-analytic-fixtures",
            not_run=list(NOT_RUN),
        )
        envelopes.append(envelope)

    return {
        "schema_version": 1,
        "kind": "mutation-timing-v1",
        "status": "PASS",
        "mutation_contract": "mutation-v1",
        "inputs": {
            name: sha256((ROOT / name).read_bytes()) for name in INPUT_PATHS
        },
        "trace_registry_sha256": sha256(
            (ROOT / "spec/reference/trace-registry-v1.json").read_bytes()
        ),
        "controls": controls,
        "fault_matrix": matrix,
        "floor_probes": floor_probes,
        "coverage": coverage,
        "envelopes": envelopes,
        "counts": {
            "faults": len(matrix),
            "tripped": sum(1 for entry in matrix if entry["tripped"]),
            "controls_passed": sum(1 for okay in controls.values() if okay),
            "floor_probes": len(floor_probes),
            "coverage_cases": len(coverage["envelope_and_high_rate"]),
            "envelopes": len(envelopes),
        },
        "not_run": list(NOT_RUN),
    }


def check_publication():
    if not PUBLICATION_PATH.exists():
        print(
            "publication: ABSENT — requires tools/qualify_mutations_timing.py; "
            "absence is never reported as a pass"
        )
        raise SystemExit(2)
    committed = json.loads(PUBLICATION_PATH.read_bytes())
    fresh = build_publication()
    failures = [
        field
        for field in (
            "kind",
            "status",
            "mutation_contract",
            "controls",
            "fault_matrix",
            "floor_probes",
            "coverage",
            "envelopes",
            "counts",
            "not_run",
            "inputs",
            "trace_registry_sha256",
        )
        if committed.get(field) != fresh[field]
    ]
    if failures:
        print("FAIL: committed publication is stale or drifted: " + ", ".join(failures))
        raise SystemExit(1)
    print(
        json.dumps(
            {
                "status": "PASS",
                "publication": str(PUBLICATION_PATH),
                "faults": fresh["counts"]["faults"],
                "envelope_ids": [
                    envelope["envelope_id"] for envelope in fresh["envelopes"]
                ],
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
                "faults": publication["counts"]["faults"],
                "envelope_ids": [
                    envelope["envelope_id"] for envelope in publication["envelopes"]
                ],
            }
        )
    )


if __name__ == "__main__":
    main()
