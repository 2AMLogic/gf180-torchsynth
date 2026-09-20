"""Bounded #31 family qualification and evidence check (stdlib host).

Default mode runs the identity/parameter/noise negative-control family of
spec/MUTATIONS.md in-process over the landed #30 framework: the
ordinary/empty-plan/sham controls, RNG isolation, crash cleanup, the full
family fault x detector matrix (every fault must trip its landed detector
refusal while the clean control passes the same check), each fault on the
fixed representative non-directed cases, and the landed-interface
(ResolvedRequest) acceptance witnesses. It writes the bounded publication
``sim/reference/mutation-identity-v1.json`` binding ``mu1-`` evidence
envelopes per directed fault case and preserving the detector-row receipts,
including the statistical noise rows that stay plausible under wrong-slot
and wrong-seed streams.

``--check`` strictly revalidates the committed publication against a fresh
in-memory rerun and the current input digests. Absence is reported as
absent, never as a pass; staleness (any participating input changed)
fails.

Analytic apparatus domain only: this tool never renders the Voice graph,
never touches holdout, ratifies no numeric format, and never substitutes
for the DR-0006 gated actual-Voice runtime evidence, which is recorded
not-run. The landed ``bridge.*`` operators stay #30-owned test-only proofs
and are never published as family qualification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import mutations, mutations_identity  # noqa: E402

PUBLICATION_PATH = ROOT / "sim/reference/mutation-identity-v1.json"

INPUT_PATHS = (
    "src/torchsynth_voice/mutations_identity.py",
    "src/torchsynth_voice/mutations.py",
    "src/torchsynth_voice/mutation_runtime.py",
    "src/torchsynth_voice/paired_metrics.py",
    "src/torchsynth_voice/spectral_estimators.py",
    "src/torchsynth_voice/scorecard.py",
    "src/torchsynth_voice/artifacts.py",
    "src/torchsynth_voice/identity.py",
    "src/torchsynth_voice/directed.py",
    "src/torchsynth_voice/float_interfaces.py",
    "src/torchsynth_voice/float_sources.py",
    "src/torchsynth_voice/inventory.py",
    "src/torchsynth_voice/trace_registry.py",
    "spec/reference/mutation-seams-v1.json",
    "spec/reference/parameter-inventory-v1.json",
    "spec/schemas/parameter-inventory-v1.schema.json",
    "spec/SPECTRAL-ESTIMATORS.md",
    "spec/PARAMETER-INVENTORY.md",
    "spec/FLOAT-INTERFACES.md",
    "spec/FLOAT-SOURCES.md",
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
    "numeric-format selection or ratification (the numeric contract stays "
    "unbound; #53/DR-0008)",
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_publication():
    mutations_identity.register_family()
    controls = mutations_identity.qualification_controls(ROOT)
    failed_controls = [name for name, okay in controls.items() if not okay]
    if failed_controls:
        raise SystemExit("FAIL: family controls failed: " + ", ".join(failed_controls))

    matrix, case_records = mutations_identity.fault_matrix(ROOT)
    untripped = [
        entry["fault"] for entry in matrix
        if not entry["tripped"] or not entry["control_accepted"]
    ]
    if untripped:
        raise SystemExit(
            "FAIL: family faults did not trip their detectors or lacked a "
            "passing control: " + ", ".join(untripped)
        )

    random_receipts = mutations_identity.representative_random_receipts(ROOT)
    untripped_random = [
        receipt for receipt in random_receipts
        if not receipt["tripped"] or not receipt["control_accepted"]
    ]
    if untripped_random:
        raise SystemExit(
            "FAIL: representative-case faults did not trip their detectors: "
            + ", ".join(
                receipt["case"] + "/" + receipt["operator"]
                for receipt in untripped_random
            )
        )

    # Every directed noise fault must keep its statistical rows plausible:
    # the preserved rows are exactly the false positives a statistical
    # detector would raise against a wrong stream.
    implausible = []
    for record in case_records:
        for receipt in record["rows"]:
            if receipt["property"].startswith("noise.ac.") and receipt[
                "verdict"
            ] not in ("PASS",):
                implausible.append(record["case"] + ":" + receipt["property"])
    if implausible:
        raise SystemExit(
            "FAIL: statistical noise rows are not preserved as plausible on "
            "faulted cases: " + ", ".join(sorted(set(implausible)))
        )

    envelopes = []
    case_receipts = []
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
        case_receipts.append(
            {
                "case": record["case"],
                "fault": record["faults"][0],
                "plan_id": record["plan"]["plan_id"],
                "gate": record["gate"],
                "witness": record["witness"],
                "detector_rows": record["rows"],
                "artifacts": [
                    {"path": path, "sha256": sha256(data), "size_bytes": len(data)}
                    for path, data in sorted(attempt.store.items())
                ],
                "envelope_id": envelope["envelope_id"],
            }
        )

    return {
        "schema_version": 1,
        "kind": "mutation-identity-v1",
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
        "random_receipts": random_receipts,
        "case_receipts": case_receipts,
        "envelopes": envelopes,
        "counts": {
            "faults": len(matrix),
            "tripped": sum(1 for entry in matrix if entry["tripped"]),
            "controls_passed": sum(1 for okay in controls.values() if okay),
            "directed_cases": len(mutations_identity.DIRECTED_CASES),
            "representative_cases": len(mutations_identity.REPRESENTATIVE_INDICES),
            "random_receipts": len(random_receipts),
            "envelopes": len(envelopes),
        },
        "not_run": list(NOT_RUN),
    }


def check_publication():
    if not PUBLICATION_PATH.exists():
        print(
            "publication: ABSENT — requires tools/qualify_mutations_identity.py; "
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
            "random_receipts",
            "case_receipts",
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
