"""Shared committed-publication check for the mutation qualifier tools (#484).

The five ``check_publication()`` entry points under ``tools/`` carried the
same copy-pasted skeleton: an absent committed publication prints the
``ABSENT`` line and exits 2 (absence is never reported as a pass), a stale
or drifted one names the differing fields and exits 1, and a valid one
prints a ``PASS`` JSON summary. This module owns that flow once; each tool
keeps its ``check_publication()`` name and passes in its publication path,
its repo-relative tool path (as printed in the ``ABSENT`` line), its
``build_publication()``, a field comparator and its family-specific PASS
summary fields.

The exit codes and message texts are part of the verification contract and
are byte-identical to the per-tool originals. Only the field comparator
genuinely differs per family — a plain ``!=`` over ``COMPARABLE_FIELDS``
for framework, identity and matrix; declared-identity comparisons for the
signal ``fault_matrix`` / ``optional_observations`` / ``envelope`` fields
and the timing ``envelopes`` field (platform-dependent digests are asserted
by declared format, not by bytes) — so the comparator is injected and each
family's special cases stay next to its own code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence


def plain_differing_fields(
    fields: Sequence[str],
) -> Callable[[Dict[str, Any], Dict[str, Any]], List[str]]:
    """Compare each named field with ``!=``; return the names that differ."""

    def compare(committed: Dict[str, Any], fresh: Dict[str, Any]) -> List[str]:
        return [field for field in fields if committed.get(field) != fresh[field]]

    return compare


def require_publication(publication_path: Path, tool_name: str) -> None:
    """Fail visibly when the committed publication is missing.

    The single construction site of the ``ABSENT`` line: a missing
    publication prints that line — naming ``tool_name`` as the owning
    tool and stating that absence is never reported as a pass — and
    raises ``SystemExit(2)``. A present publication falls through so
    each caller continues with its own stale/PASS flow; a present but
    unreadable one is not handled here either, so the caller's
    ``json.loads`` failure surfaces untouched instead of being reported
    as absence.
    """
    if not publication_path.exists():
        print(
            f"publication: ABSENT — requires {tool_name}; "
            "absence is never reported as a pass"
        )
        raise SystemExit(2)


def check_committed_publication(
    publication_path: Path,
    tool_name: str,
    build_publication: Callable[[], Dict[str, Any]],
    differing_fields: Callable[[Dict[str, Any], Dict[str, Any]], List[str]],
    summary_fields: Callable[[Dict[str, Any]], Dict[str, Any]],
) -> None:
    """Run the shared absent/stale/PASS flow over a committed publication.

    A missing publication exits 2 naming the owning tool — absence is never
    reported as a pass. A stale or drifted publication names the differing
    fields and exits 1. Otherwise the ``PASS`` JSON summary is printed with
    ``summary_fields(fresh)`` appended after the shared ``status`` and
    ``publication`` keys, preserving the original key order.
    """
    require_publication(publication_path, tool_name)
    committed = json.loads(publication_path.read_bytes())
    fresh = build_publication()
    failures = differing_fields(committed, fresh)
    if failures:
        print("FAIL: committed publication is stale or drifted: " + ", ".join(failures))
        raise SystemExit(1)
    print(
        json.dumps(
            {
                "status": "PASS",
                "publication": str(publication_path),
                **summary_fields(fresh),
            }
        )
    )
