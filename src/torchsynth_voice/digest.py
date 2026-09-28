"""Shared SHA-256 digest helpers (stdlib only).

Consolidates the ``hashlib.sha256(<bytes>).hexdigest()`` idiom (issue #247)
that had been reimplemented as a standalone top-level function ~30 times
across this package and ``tools/``, under nine different names (``sha256``,
``sha256_bytes``, ``sha256_file``, ``sha256_of``, ``sha256_of_bytes``,
``sha256_hex``, ``sha256_json``, ``digest``, ``digest_words``).

Some copies deliberately stay where they are, because editing the file
would invalidate evidence this refactor is not entitled to regenerate:

* the copy embedded in ``tools/capture_float_sources.py``'s
  ``WORKER_SOURCE`` string -- that worker must stay self-contained inside
  the release-era image, where this package is not importable;
* every file whose *own* source digest is pinned by a committed record
  under ``spec/`` or ``sim/`` -- ``periodic_estimators.py``,
  ``case_registry.py``, ``mutation_runtime.py``,
  ``tools/qualify_mutations_identity.py``,
  ``tools/qualify_mutations_timing.py``,
  ``tools/qualify_spectral_estimators.py``,
  ``tools/review_directed_phase.py`` and ``tools/probe_trace_registry.py``
  (the last one is additionally recapture-gated on the DR-0006 measurement
  host; see ``DISPATCH_RECAPTURE_GATED`` in
  ``tools/check_reference_consolidation.py``).

Only the four operations below are named here; a call site that already
used one of the other spellings keeps it with an import alias
(``from .digest import sha256_bytes as digest``) rather than this module
re-exporting nine names for one function.

``digest_words`` is "the #54 trace-digest convention over an integer word
list", moved verbatim from ``vco_golden.py`` / ``mix_golden.py`` /
``normalization_replay_golden.py`` (which carried byte-identical bodies),
and ``sha256_json`` is that same canonical JSON encoding generalized to an
arbitrary JSON-serializable value (moved from
``generate_fixed_voice_golden.py``). Neither changes the convention itself
-- same input in, same hex digest out.

Importing this module must stay dependency-free beyond the standard
library: several callers (``tools/validate_rubric.py``,
``tools/qualify_trace_capture.py --check-inputs``) document themselves as
stdlib-only modes for ordinary CI hosts.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Sequence, Union

#: Chunk size for :func:`sha256_file`; matches the largest chunk any of the
#: consolidated file-reading copies used, and the digest is independent of it.
_FILE_CHUNK_BYTES = 1024 * 1024


def sha256_bytes(data: bytes) -> str:
    """SHA-256 hex digest of ``data``."""

    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Union[str, Path]) -> str:
    """SHA-256 hex digest of a file's contents, read in fixed-size chunks."""

    hasher = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(_FILE_CHUNK_BYTES), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def sha256_json(value: Any) -> str:
    """SHA-256 hex digest of ``value`` under the canonical JSON encoding.

    Encoding: ``json.dumps(value, sort_keys=True, separators=(",", ":"),
    ensure_ascii=True)``, hashed as UTF-8 bytes.
    """

    blob = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return sha256_bytes(blob)


def digest_words(words: Sequence[int]) -> str:
    """The #54 trace-digest convention over an integer word list.

    The same canonical form the frozen receipt's per-trace digests use.
    """

    return sha256_json(list(words))


__all__ = ["sha256_bytes", "sha256_file", "sha256_json", "digest_words"]
