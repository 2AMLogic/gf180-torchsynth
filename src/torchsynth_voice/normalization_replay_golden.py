"""Host mirror + full-voice case resolution for the normalization replay
controller and one-shot top (issue #77).

The bit-exactness target is the frozen fixed model's own
``normalize_words()`` (``src/torchsynth_voice/fixed_voice.py:195-235``): the
strict ``peak_int > 2^21`` branch on the pre-normalization Q2.21 mix, the
U1.22 reciprocal gain word (``div_round(2^43, peak, HALF_EVEN)``, site S5),
and the declared ``mul(...)`` narrowing on the divide branch (identity on
the bypass branch). This module adds no reimplementation of that algorithm
-- :func:`mirror_normalize` calls ``normalize_words`` directly, so the
equivalence chain the tb asserts is frozen model -> RTL, with no
harness-parallel arithmetic anywhere.

It also adds :func:`resolve_full_voice_case`, which rebuilds one of the
frozen ``fixed-voice-golden-v1`` receipt's normalization-family cases
(``normalization:above``, ``normalization:below``, ``normalization:tie``,
``normalization-stress:anchor-3.9478583336``) through the composed
``FixedVoiceModel.render()`` exactly as
``tools/generate_fixed_voice_golden.py`` constructs them -- the directed
base + per-case overrides, or (for the anchor case) the declared DR-0006
release-anchor mixer-level scale -- so this module's RTL testbench can
drive the resulting ``mixer.pre_normalization`` word stream as the #77
engine's own declared input interface (DR-0010's mixer-output interface,
"1 compare + 1 abs-select folded into the mixer output") without depending
on issue #76's RTL landing first. The caller is responsible for verifying
the regenerated case's traces against the frozen receipt's own per-trace
digests before trusting it as an RTL fixture -- this module makes no
digest-authority claim of its own.

The isolated directed Q2.21 case grid this issue's own boundary tests use
(the "below/at/above-one, peak tie, silence, extrema" fixtures) already
lives in :mod:`torchsynth_voice.normalization_replay`
(:func:`normalization_replay.directed_cases`,
:func:`normalization_replay.anchored_cases`, issue #52); this module does
not duplicate it.

This module makes no synthesis, layout, signoff, hardware-playback, or
sound-fidelity claim.
"""

from __future__ import annotations

import hashlib
import json
from fractions import Fraction
from typing import Any, Dict, List, Sequence, Tuple

from . import float_interfaces as fi
from . import float_sources as fs
from .contract import repository_root
from .fixed_voice import AcceptedFormats, FixedVoiceModel, normalize_words
from .fixedpoint.counters import StickyCounters

__all__ = [
    "NormalizationReplayGoldenError",
    "digest_words",
    "mirror_normalize",
    "resolve_full_voice_case",
    "ANCHOR_CASE_ID",
    "FULL_VOICE_CASES",
]

ROOT = repository_root()
DIRECTED_PATH = ROOT / "spec/reference/directed-voice-v1.json"

#: The DR-0006 canonical-runtime release anchor that divides (the "loud"
#: anchor; `spec/decision-records/0006-canonical-runtime.md:89`), matching
#: both `torchsynth_voice.normalization_replay.ANCHOR_LOUD` and
#: `tools/generate_fixed_voice_golden.py`'s `ANCHOR_SCALE`.
ANCHOR_SCALE = "3.9478583336"
ANCHOR_CASE_ID = "normalization-stress:anchor-3.9478583336"

#: The frozen receipt's normalization-family cases (fixture mapping source
#: 1, per issue #77's curated body): the three directed branch cases plus
#: the release-anchor stress case.
FULL_VOICE_CASES = (
    "normalization:above",
    "normalization:below",
    "normalization:tie",
    ANCHOR_CASE_ID,
)

_MIX_LANES = ("mixer.vco_1", "mixer.vco_2", "mixer.noise")


class NormalizationReplayGoldenError(ValueError):
    """Raised when this module cannot resolve or mirror a requested case."""


def digest_words(words: Sequence[int]) -> str:
    """The #54 golden-receipt trace-digest convention over an integer word list."""

    blob = json.dumps(
        list(words), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def mirror_normalize(
    mix_words: Sequence[int],
    formats: AcceptedFormats,
    counters: StickyCounters = None,
) -> Tuple[List[int], Dict[str, Any]]:
    """The host mirror: the frozen model's own ``normalize_words``, verbatim.

    Returns ``(output_words, diagnostics)`` exactly as ``normalize_words``
    does (``peak_word``, ``gain_word``, ``normalized_branch``).
    """

    if counters is None:
        counters = StickyCounters()
    return normalize_words(list(mix_words), formats.audio, formats.gain, counters)


def _load_directed() -> Dict[str, Any]:
    return json.loads(DIRECTED_PATH.read_bytes())


def resolve_full_voice_case(
    case_id: str, formats: AcceptedFormats = None
) -> Tuple[Dict[str, List[int]], Dict[str, Any]]:
    """Rebuild one normalization-family case through the composed fixed model.

    Replicates ``tools/generate_fixed_voice_golden.py``'s per-case request
    construction: the directed base physical map plus the case's own
    overrides for ``normalization:above/below/tie``, or (for
    ``normalization-stress:anchor-3.9478583336``) the pure base map with
    every mixer level scaled by the exact DR-0006 anchor constant --
    ``float(Fraction(anchor) * Fraction(level))``, the same exact-rational
    then one binary64 round the generator tool performs. Noise is always
    the canonical seed-13, slot-0, sound_index-0 stream (matching every
    directed-family case in the frozen receipt).

    Returns the fixed model's full checkpoint set and diagnostics
    (``fixed["mixer.pre_normalization"]`` is this issue's own declared RTL
    input interface); the caller must verify the digest of every trace it
    intends to trust against the frozen receipt before using it as an RTL
    fixture.
    """

    if formats is None:
        formats = AcceptedFormats()
    if case_id not in FULL_VOICE_CASES:
        raise NormalizationReplayGoldenError(
            "not a normalization-family full-voice case: %r" % (case_id,)
        )
    directed = _load_directed()
    base_p = {name: entry["physical"] for name, entry in directed["base"].items()}
    physical = dict(base_p)

    if case_id == ANCHOR_CASE_ID:
        for lane in _MIX_LANES:
            physical[lane] = float(
                Fraction(ANCHOR_SCALE) * Fraction(str(physical[lane]))
            )
    else:
        by_id = {case["id"]: case for case in directed["cases"]}
        case = by_id.get(case_id)
        if case is None:
            raise NormalizationReplayGoldenError(
                "case id not present in %s: %r" % (DIRECTED_PATH, case_id)
            )
        for name, override in case["overrides"].items():
            physical[name] = override["physical"]

    noise_bytes = fs.NoiseSource.resolve(0)
    request = fi.ResolvedRequest(
        0,
        {name: 0.0 for name in physical},
        {
            "seed": 13,
            "slot": 0,
            "sample_count": 176400,
            "sha256": hashlib.sha256(noise_bytes).hexdigest(),
            "samples": noise_bytes,
        },
        physical=physical,
        execution_status="canonical-batched",
    )
    model = FixedVoiceModel(request, formats)
    fixed, diagnostics = model.render()
    return fixed, diagnostics
