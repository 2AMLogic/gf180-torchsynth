"""Deterministic derived views; no new evidence policy or check execution."""

from __future__ import annotations

import json
import re
from collections import Counter

from .capabilities import CapabilityError, Result, _ordered, render_mermaid

BEGIN = "<!-- CAPABILITIES:BEGIN -->"
END = "<!-- CAPABILITIES:END -->"
STATES = ("READY", "BLOCKED", "NOT RUN", "PASS", "FAIL", "NO VERDICT", "STALE")


def counts(results: dict[str, Result]) -> dict[str, int]:
    observed = Counter(result.state for result in results.values())
    return {state: observed[state] for state in STATES}


def render_json(graph: dict, results: dict[str, Result]) -> str:
    """Machine-readable projection, not another editable graph or evidence record."""
    nodes = []
    for node in _ordered(graph):
        result = results[node["id"]]
        nodes.append(
            {
                **node,
                "dependencies": sorted(node["dependencies"]),
                "state": result.state,
                "local_state": result.local_state,
                "reasons": list(result.reasons),
                "evidence_healthy": result.healthy,
            }
        )
    report = {
        "schema_version": 1,
        "kind": "generated-capability-view",
        "counts": counts(results),
        "evidence_healthy": all(result.healthy for result in results.values()),
        "nodes": nodes,
    }
    return json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"


def render_readme(graph: dict, results: dict[str, Result]) -> str:
    """Small root-README view with the exact same states/edges as the full report."""
    totals = counts(results)
    return "\n".join(
        [
            "## Evidence-derived capability status",
            "",
            "Generated from [canonical node declarations](spec/capabilities-v1.json) and validated "
            "evidence. [Full claims, reasons and exclusions](docs/CAPABILITIES.md) · "
            "[machine-readable status](docs/capabilities.json) · "
            "[case/property scorecard](docs/SCORECARD.md).",
            "",
            "| " + " | ".join(STATES) + " |",
            "| " + " | ".join("---:" for _ in STATES) + " |",
            "| " + " | ".join(str(totals[state]) for state in STATES) + " |",
            "",
            "These are claim counts, not a completion percentage or a quality score. "
            "READY is unrun, not PASS. BLOCKED retains its local evidence state in the full report. "
            "An unattached planned node is allowed by the health check, but establishes no capability. "
            "Existing implementations, bounded measurements, closed issues and Loom labels do not "
            "stamp PASS; an accepted, current qualification record must cover the exact claim.",
            "",
            render_mermaid(graph, results),
            "",
            "Only a node's stated scope is covered by its PASS: implementation identity, "
            "auditory/distribution evidence, FPGA, synthesis, routing and silicon are separate claims. "
            "The compiler does not run measurements, unseal holdout data, or infer hardware playback.",
            "",
            "Regenerate all three views with `python3 tools/compile_capabilities.py`; "
            "`--check` checks agreement without writing, and `--strict` additionally rejects unhealthy "
            "declared evidence. See [refresh and evidence policy](docs/CAPABILITY-WORKFLOW.md).",
            "",
        ]
    )


def replace_readme(original: bytes, generated: str) -> bytes:
    """Replace exactly one marked region, preserving all other bytes verbatim."""
    markers = [marker.encode("ascii") for marker in (BEGIN, END)]
    positions = []
    for marker in markers:
        if original.count(marker) != 1:
            raise CapabilityError("README needs exactly one pair of capability markers")
        match = re.search(rb"(?m)^" + re.escape(marker) + rb"\r?$", original)
        if match is None:
            raise CapabilityError(
                "README capability markers must occupy their own lines"
            )
        positions.append(match.start())
    start = positions[0] + len(markers[0])
    end = positions[1]
    if start >= end:
        raise CapabilityError("README capability markers are reversed")
    return (
        original[:start]
        + b"\n"
        + generated.encode("utf-8").rstrip(b"\n")
        + b"\n"
        + original[end:]
    )
