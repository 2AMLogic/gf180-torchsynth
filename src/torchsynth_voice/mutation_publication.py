"""Shared publication write-and-report for the mutation qualifier tools.

The five qualifier CLIs under ``tools/`` carried two copies of the same
skeleton. ``--check`` revalidated the committed publication (#484): an
absent committed publication prints the ``ABSENT`` line and exits 2
(absence is never reported as a pass), a stale or drifted one names the
differing fields and exits 1, and a valid one prints a ``PASS`` JSON
summary. The default command then hand-built the same PASS summary a
second time after writing the publication, so a change to a family's
summary fields had to be made in two places. This module now owns both
flows once; each tool keeps its ``check_publication()`` name and passes
in its publication path, its repo-relative tool path (as printed in the
``ABSENT`` line), its ``build_publication()``, a field comparator and
its family-specific PASS summary fields — the same ``_summary_fields``
serves both modes.

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


def print_pass_summary(
    publication_path: Path,
    fresh: Dict[str, Any],
    summary_fields: Callable[[Dict[str, Any]], Dict[str, Any]],
) -> None:
    """Print the single ``PASS`` JSON line shared by both modes.

    The line starts with the shared ``status`` and ``publication`` keys
    and appends ``summary_fields(fresh)`` in the family's declared order
    — the one construction site of the summary each tool's ``--check``
    and default command print.
    """
    print(
        json.dumps(
            {
                "status": "PASS",
                "publication": str(publication_path),
                **summary_fields(fresh),
            }
        )
    )


def write_publication(
    publication_path: Path,
    build_publication: Callable[[], Dict[str, Any]],
    summary_fields: Callable[[Dict[str, Any]], Dict[str, Any]],
) -> None:
    """Build the publication, write it, then report it (default command).

    The default-command body the five qualifier CLIs previously duplicated:
    build the publication, create missing parent directories and write it
    as sorted two-space-indented JSON with finite values (``allow_nan=False``
    rejects non-finite numbers) and a trailing newline, then print the same
    ``PASS`` summary ``check_committed_publication`` prints for the same
    inputs. A failure anywhere before the write completes — including a
    non-finite value failing serialization — propagates, so a failed write
    never prints a PASS line.
    """
    fresh = build_publication()
    publication_path.parent.mkdir(parents=True, exist_ok=True)
    publication_path.write_text(
        json.dumps(fresh, sort_keys=True, indent=2, allow_nan=False) + "\n"
    )
    print_pass_summary(publication_path, fresh, summary_fields)


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
    print_pass_summary(publication_path, fresh, summary_fields)
