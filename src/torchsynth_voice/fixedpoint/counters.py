"""Sticky, named per-site numeric-event counters.

Every narrowing, rounding or saturation site can emit a named counter event.
Counters are sticky: once noted they stay set until an explicit
:meth:`StickyCounters.reset`, matching the DR-0008 Section 6 requirement that
modules export sticky saturate/overflow counters as model and RTL traces.

Counter records are plain data and serialize deterministically so they can
participate in trace identities. This module makes no RTL claim.
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, Tuple

SATURATION = "saturation"
ROUNDING = "rounding"
OVERFLOW = "overflow"


class StickyCounters:
    """Sticky per-(site, kind) event counts with deterministic serialization."""

    def __init__(self) -> None:
        self._counts: Dict[Tuple[str, str], int] = {}

    def note(self, site: str, kind: str = SATURATION) -> None:
        """Record one event at a named site. Sticky: no automatic clearing."""
        key = (site, kind)
        self._counts[key] = self._counts.get(key, 0) + 1

    def count(self, site: str, kind: str = SATURATION) -> int:
        return self._counts.get((site, kind), 0)

    def total(self, kind: str = SATURATION) -> int:
        return sum(v for (s, k), v in self._counts.items() if k == kind)

    def sites(self) -> Tuple[str, ...]:
        return tuple(sorted({s for (s, _k) in self._counts}))

    def reset(self, site: str = None, kind: str = None) -> None:
        """Explicitly clear all counters, one kind, or one (site, kind)."""
        if site is None and kind is None:
            self._counts.clear()
            return
        for key in list(self._counts):
            if (site is None or key[0] == site) and (kind is None or key[1] == kind):
                del self._counts[key]

    def as_json(self) -> Dict[str, Any]:
        """Deterministic JSON-serializable snapshot (sorted keys, no floats)."""
        records = [
            {"site": site, "kind": kind, "count": count}
            for (site, kind), count in sorted(self._counts.items())
        ]
        return {
            "schema": "gf180-torchsynth/sticky-counters-v1",
            "sticky": True,
            "total_saturation": self.total(SATURATION),
            "total_rounding": self.total(ROUNDING),
            "total_overflow": self.total(OVERFLOW),
            "records": records,
        }

    def __iter__(self) -> Iterator[Tuple[str, str, int]]:
        for (site, kind), count in sorted(self._counts.items()):
            yield site, kind, count

    def __len__(self) -> int:
        return len(self._counts)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, StickyCounters):
            return NotImplemented
        return self._counts == other._counts

    def __repr__(self) -> str:
        return f"StickyCounters({self._counts!r})"
