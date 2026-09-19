"""Bounded mutation-framework qualification and evidence check (stdlib-only).

Default mode runs the #30 apparatus fault-injection qualification in-process:
the ordinary/empty-plan/sham controls, the deterministic clean rerun after a
producer crash, RNG isolation, and the full fault×downstream refusal matrix
against the landed validators. It writes the bounded publication
``sim/reference/mutation-framework-v1.json``. Every fault must trip its named
downstream refusal and every control must pass, or the run fails without
writing evidence.

``--check`` strictly revalidates the committed publication against a fresh
in-memory rerun and the current input digests. Absence is reported as absent,
never as a pass; staleness (any participating input changed) fails.

This qualification never renders audio and never touches the Voice graph:
actual-Voice runtime fault injection is owned by a later qualified-runtime
pass and is recorded under ``not_run`` rather than substituted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import mutations, mutation_runtime  # noqa: E402
from torchsynth_voice import trace_registry  # noqa: E402

PUBLICATION_PATH = ROOT / "sim/reference/mutation-framework-v1.json"

INPUT_PATHS = (
    "src/torchsynth_voice/mutations.py",
    "src/torchsynth_voice/mutation_runtime.py",
    "src/torchsynth_voice/trace_capture.py",
    "src/torchsynth_voice/trace_registry.py",
    "src/torchsynth_voice/scorecard.py",
    "src/torchsynth_voice/artifacts.py",
    "src/torchsynth_voice/case_registry.py",
    "spec/reference/mutation-seams-v1.json",
    "spec/schemas/mutation-v1.schema.json",
)

NOT_RUN = (
    "actual Voice runtime fault injection at #23 capture seams "
    "(requires the Torch release-era runtime; synthetic apparatus proofs "
    "never count as runtime evidence)",
    "fault-operator families #31/#32/#33 (they register through this API in "
    "their own issues; no operator family is qualified here)",
    "detector qualification #34 matrix publication",
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_publication():
    catalog = mutations.load_seam_catalog(ROOT / mutations.SEAM_CATALOG_PATH)
    document = trace_registry.load_registry()
    matrix = mutation_runtime.fault_matrix(document)
    failed = [
        entry["fault"]
        for entry in matrix
        if not entry["tripped"] or not entry["control_accepted"]
    ]
    if failed:
        raise SystemExit(
            "FAIL: injected faults did not trip their downstream refusals: "
            + ", ".join(failed)
        )
    controls = mutation_runtime.qualification_controls(document)
    failed_controls = [name for name, okay in controls.items() if not okay]
    if failed_controls:
        raise SystemExit("FAIL: framework controls failed: " + ", ".join(failed_controls))

    harness = mutation_runtime.MutationHarness(ROOT, document)
    sham_plan = harness.sham_plan()
    sham_attempt = harness.attempt(sham_plan)
    envelope = mutations.make_envelope(
        plan=sham_plan,
        events_summary=sham_attempt.events_summary,
        artifacts=[
            {"path": path, "sha256": sha256(data), "size_bytes": len(data)}
            for path, data in sorted(sham_attempt.store.items())
        ],
        controls=controls,
        fault_matrix=matrix,
        runtime_scope="stdlib-apparatus-only",
        not_run=list(NOT_RUN),
    )
    return {
        "schema_version": 1,
        "kind": "mutation-framework-v1",
        "status": "PASS",
        "mutation_contract": "mutation-v1",
        "inputs": {
            name: sha256((ROOT / name).read_bytes()) for name in INPUT_PATHS
        },
        "trace_registry_sha256": sha256(trace_registry.REGISTRY_PATH.read_bytes()),
        "controls": controls,
        "fault_matrix": matrix,
        "envelope": envelope,
        "counts": {
            "faults": len(matrix),
            "tripped": sum(1 for entry in matrix if entry["tripped"]),
            "controls_passed": sum(1 for okay in controls.values() if okay),
        },
        "not_run": list(NOT_RUN),
    }


def check_publication():
    if not PUBLICATION_PATH.exists():
        print(
            "publication: ABSENT — requires tools/qualify_mutations.py; "
            "absence is never reported as a pass"
        )
        raise SystemExit(2)
    committed = json.loads(PUBLICATION_PATH.read_bytes())
    fresh = build_publication()
    failures = []
    for field in ("kind", "status", "mutation_contract", "controls", "fault_matrix", "envelope", "counts", "not_run", "inputs", "trace_registry_sha256"):
        if committed.get(field) != fresh[field]:
            failures.append(field)
    if failures:
        print("FAIL: committed publication is stale or drifted: " + ", ".join(failures))
        raise SystemExit(1)
    print(
        json.dumps(
            {
                "status": "PASS",
                "publication": str(PUBLICATION_PATH),
                "faults": fresh["counts"]["faults"],
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
                "faults": publication["counts"]["faults"],
                "envelope_id": publication["envelope"]["envelope_id"],
            }
        )
    )


if __name__ == "__main__":
    main()
