#!/usr/bin/env python3
"""Generate the square/saw VCO engine golden vectors (issue #74).

Two vector classes, both through the frozen model's own code:

1. Frozen-binding vectors (``frozen-*``): the fixed-voice-golden-v1 cases
   whose ``vco_2.raw`` words are retained as exact f32le sidecars. The
   vector re-declares the case's physical parameters and binds the frozen
   trace digest plus the sidecar file, so the RTL is validated directly
   against the frozen word stream (no harness mirror in the loop).

2. Dedicated regime vectors: full-clip renders of ``FixedVoiceModel``
   itself at vco_2 selector/extrema points the frozen selection does not
   carry (intermediate shape mixes, half-even selector-word ties, the
   least-significant shape step, near-full-turn phase wrap). The digest of
   the model's own ``vco_2.raw`` render is the committed truth; at
   generation time the host mirror (``torchsynth_voice.vco2_golden``) is
   required to reproduce every digest exactly, so the vectors carry the
   model's bits, not the mirror's.

Every committed vector declares the shadow boundary (DR-0008/DR-0010 open
approximation items replayed host-side; no RTL transcendental claimed).
``--check`` regenerates everything in memory and refuses any drift against
the committed files (determinism gate). No synthesis, layout, signoff,
hardware-playback, or sound-fidelity claim is made.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import golden_vectors as gv  # noqa: E402
from torchsynth_voice import vco2_golden as vg  # noqa: E402
from torchsynth_voice.fixed_voice import (  # noqa: E402
    AcceptedFormats,
)

DIRECTED_PATH = ROOT / "spec/reference/directed-voice-v1.json"
OUT_DIR = ROOT / "sim/reference/square-saw-vco-golden-v1"

ISSUE = 74
GENERATOR = "tools/generate_square_saw_vco_golden.py"

#: The frozen cases whose retained vco_2.raw sidecars the engine validates
#: against directly (the "landed cases").
FROZEN_SIDECAR_CASES = (
    "waveform:vco_2:saw",
    "boundary:vco_2.mod_depth:upper",
    "boundary:vco_1.tuning:upper",
    "boundary:vco_1.mod_depth:upper",
    "boundary:vco_1.initial_phase:upper",
)

#: Dedicated regime cases: id -> vco_2-focused physical overrides applied to
#: the directed base. Values are binary32-exact by construction (powers of
#: two, small integers, or the binary32 pi/2pi).
#: 2^-21 = 4.76837158203125e-07 (the least Q10.21 shape step, word +1);
#: 2^-22 -> the half-even tie-to-even-down selector word 0;
#: 3*2^-22 -> the half-even tie-to-even-up selector word 2;
#: 1 - 2^-21 -> the word just below the saw boundary.
DEDICATED_CASES = (
    ("shape:intermediate-half", {"vco_2.shape": 0.5}),
    ("shape:intermediate-quarter", {"vco_2.shape": 0.25}),
    ("shape:three-quarters", {"vco_2.shape": 0.75}),
    ("shape:lsb-step", {"vco_2.shape": 2.0 ** -21}),
    ("shape:lsb-tie-down", {"vco_2.shape": 2.0 ** -22}),
    ("shape:lsb-tie-up", {"vco_2.shape": 3.0 * 2.0 ** -22}),
    ("shape:one-minus-lsb", {"vco_2.shape": 1.0 - 2.0 ** -21}),
    ("mod:depth-max-shape-half", {"vco_2.mod_depth": 96.0, "vco_2.shape": 0.5}),
    ("phase:quarter-turn", {"vco_2.initial_phase": 1.5707963705062866}),
    # 6.0 rad is just below a full turn (word just below 2^32); the
    # binary32 2*pi is one rounding step ABOVE the binary64 2*pi the model
    # divides by, so phase:near-full-turn wraps past the top to a tiny word
    # (the exact wrap corner, not a near-top word).
    ("phase:near-top", {"vco_2.initial_phase": 6.0}),
    ("phase:near-full-turn", {"vco_2.initial_phase": 6.2831854820251465}),
)

SHADOW_BOUNDARY = (
    "Declared shadow boundary (DR-0008/DR-0010 open approximation items, "
    "the #74 declaration class): the midi->Hz exp2, the partials_constant, "
    "and the tanh distortion shadow are computed host-side and replayed to "
    "the RTL as deterministic words (fq2 per sample, partials_word per "
    "clip, square_q/left_q per sample). No RTL transcendental is "
    "implemented or claimed; every exact-integer site around the boundary "
    "(pitch sum, clamp, increment division, phase wrap, both LUT "
    "interps, driven, right narrowing, combine, saturations) is RTL-owned."
)


def build_vector(
    case_id: str,
    physical: dict,
    formats: AcceptedFormats,
    frozen: dict = None,
    rendered_words: list = None,
) -> dict:
    """One committed vector: frozen-binding or dedicated-regime.

    The sidecar custody verification, the dedicated-render check, and the
    vector assembly are shared in ``golden_vectors.build_vco_vector``
    (#535); this wrapper supplies the square/saw lane's derivation hook
    and static words.
    """

    return gv.build_vco_vector(
        case_id=case_id,
        physical=physical,
        formats=formats,
        trace_name="vco_2.raw",
        mirror_stream_key="v2",
        derive_case=vg.derive_case,
        static_words=lambda derivation: {
            "keyboard.midi_f0": derivation["words"]["keyboard.midi_f0"],
            "vco_2.tuning": derivation["words"]["vco_2.tuning"],
            "vco_2.mod_depth": derivation["words"]["vco_2.mod_depth"],
            "vco_2.shape": derivation["words"]["vco_2.shape"],
            "partials_word": derivation["partials"].word,
            "init_phase_word": derivation["init_word"],
        },
        issue=ISSUE,
        generator=GENERATOR,
        shadow_boundary=SHADOW_BOUNDARY,
        frozen=frozen,
        rendered_words=rendered_words,
    )


def build_all(formats: AcceptedFormats, verbose: bool = False) -> dict:
    """The lane's full vector set, via ``golden_vectors.build_vco_vectors``.

    The frozen-sidecar iteration and the dedicated-case renders run in
    the shared builder (#535); ``verbose`` is the shared driver's
    harmless opt-in.
    """

    return gv.build_vco_vectors(
        formats=formats,
        directed_path=DIRECTED_PATH,
        trace_name="vco_2.raw",
        frozen_sidecar_cases=FROZEN_SIDECAR_CASES,
        regime_cases=DEDICATED_CASES,
        build_case_vector=build_vector,
        verbose=verbose,
    )


def main() -> int:
    return gv.run_vco_generator(
        None,
        description=__doc__,
        out_dir=OUT_DIR,
        build_all=build_all,
        tool_name="square/saw VCO",
        frozen_count=len(FROZEN_SIDECAR_CASES),
        regime_label="dedicated",
    )


if __name__ == "__main__":
    sys.exit(main())
