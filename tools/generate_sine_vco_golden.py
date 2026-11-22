#!/usr/bin/env python3
"""Generate the sine VCO engine directed golden vectors (issue #73).

The frozen whole-voice receipt (``sim/reference/fixed-voice-golden-v1.json``)
pins the sine lane at a single keyboard pitch (MIDI 69) and only at the
*upper* ends of tuning / mod_depth / initial_phase. Issue #73's first
acceptance criterion asks for **unmodulated and modulated min/mid/max
frequency/phase fixtures**, so this generator emits the missing directed
matrix, in the same two vector classes the #74 square/saw generator uses:

1. Frozen-binding vectors (``frozen:*``): the fixed-voice-golden-v1 cases
   whose ``vco_1.raw`` words are retained as exact f32le sidecars. The
   vector re-declares the case's physical parameters and binds the frozen
   trace digest plus the sidecar file, so the RTL is validated directly
   against the frozen word stream (no harness mirror in the loop).

2. Directed regime vectors: full-clip renders of ``FixedVoiceModel``
   itself at the frequency/phase/modulation extrema the frozen selection
   does not carry — keyboard MIDI 0 / 63.5 / 127 crossed with initial
   phase -pi / 0 / +pi, each unmodulated and modulated, plus the
   negative mod_depth, negative tuning, and both MIDI-clamp corners. The
   digest of the model's own ``vco_1.raw`` render is the committed truth;
   at generation time the host mirror (``torchsynth_voice.vco_golden``)
   is required to reproduce every digest exactly, so the vectors carry
   the model's bits, not the mirror's.

Every committed vector declares the shadow boundary (the DR-0008 open
approximation item replayed host-side; no RTL transcendental claimed).
``--check`` regenerates everything in memory and refuses any drift
against the committed files (determinism gate). No synthesis, layout,
signoff, hardware-playback, or sound-fidelity claim is made.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import golden_vectors as gv  # noqa: E402
from torchsynth_voice import vco_golden as vg  # noqa: E402
from torchsynth_voice.fixed_voice import (  # noqa: E402
    AcceptedFormats,
)

DIRECTED_PATH = ROOT / "spec/reference/directed-voice-v1.json"
OUT_DIR = ROOT / "sim/reference/sine-vco-golden-v1"

ISSUE = 73
GENERATOR = "tools/generate_sine_vco_golden.py"

#: The frozen cases whose retained vco_1.raw sidecars the engine validates
#: against directly (the "landed cases"). Every frozen case sits at
#: keyboard MIDI 69 (the mid frequency band) -- the directed matrix below
#: supplies the min/max bands the receipt does not carry.
FROZEN_SIDECAR_CASES = (
    "boundary:vco_1.initial_phase:upper",
    "boundary:vco_1.mod_depth:upper",
    "boundary:vco_1.tuning:upper",
    "boundary:vco_2.mod_depth:upper",
)

#: keyboard.midi_f0 axis: the inventory band is [0, 127]. 63.5 is the
#: exact midpoint (binary32-exact); 0 and 127 are the declared extrema.
FREQ_POINTS = (
    ("min", 0.0),
    ("mid", 63.5),
    ("max", 127.0),
)

#: vco_1.initial_phase axis: the inventory band is [-pi, +pi] at the
#: binary32 pi the parameter inventory declares. 0 is the midpoint.
PI_B32 = 3.1415927410125732
PHASE_POINTS = (
    ("min", -PI_B32),
    ("mid", 0.0),
    ("max", PI_B32),
)

#: The modulation route the "mod" cells open, at unit strength: the same
#: route the receipt's boundary:vco_1.mod_depth:upper case uses.
MOD_ROUTE = "mod_matrix.adsr_2->vco_1_pitch"

#: Full-depth modulation directed *into* the band. The frozen
#: composition's vco_1 pitch column is unipolar (an ADSR envelope in
#: [0, 1]), so +96 semitones sweeps the pitch upward: that is a genuine
#: sweep from the bottom and middle of the keyboard band, but at the top
#: of the band it saturates the MIDI clamp on every sample and renders a
#: trace identical to the unmodulated cell (a vacuous "modulated"
#: fixture). The top cell therefore takes the signed *minimum*, -96,
#: which sweeps downward across the band interior. Both band ends of
#: mod_depth are consequently exercised by the matrix itself, and the
#: generator asserts every modulated cell's trace differs from its
#: unmodulated sibling.
MOD_DEPTH_BY_FREQ = {"min": 96.0, "mid": 96.0, "max": -96.0}

#: Corners the frequency/phase matrix does not reach on its own: the
#: *lower* ends of the signed mod_depth and tuning bands (the receipt
#: only carries their upper ends) and both MIDI clamp arms.
CORNER_CASES = (
    # Negative mod_depth against the LFO-driven pitch column at the top
    # of the keyboard band: the signed depth minimum (-96), which the
    # frozen receipt does not carry. The LFO column measures
    # non-negative at this setting (its minimum word is exactly zero),
    # so -96 sweeps the pitch *downward* across the band interior and
    # this case clamps no sample -- its committed measurement is 0 MIDI
    # clamps, 0 saturations. The upper MIDI clamp arm is covered by
    # corner:tuning-max-freq-max below, not here.
    (
        "corner:depth-min-lfo-freq-max",
        {
            "keyboard.midi_f0": 127.0,
            "vco_1.mod_depth": -96.0,
            "mod_matrix.lfo_1->vco_1_pitch": 1.0,
        },
    ),
    # Negative mod_depth against the ADSR pitch column at the bottom of
    # the keyboard band: drives the pitch below 0 -> the lower MIDI clamp.
    (
        "corner:depth-min-freq-min",
        {
            "keyboard.midi_f0": 0.0,
            "vco_1.mod_depth": -96.0,
            "mod_matrix.adsr_2->vco_1_pitch": 1.0,
        },
    ),
    # tuning minimum (-24 semitones) at the bottom of the keyboard band:
    # a static negative pitch sum, clamped every sample.
    (
        "corner:tuning-min-freq-min",
        {"keyboard.midi_f0": 0.0, "vco_1.tuning": -24.0},
    ),
    # tuning maximum (+24 semitones) at the top of the keyboard band: a
    # static pitch sum above 127, clamped every sample.
    (
        "corner:tuning-max-freq-max",
        {"keyboard.midi_f0": 127.0, "vco_1.tuning": 24.0},
    ),
)

SHADOW_BOUNDARY = (
    "Declared shadow boundary (DR-0008 open approximation item): the "
    "midi->Hz exp2 is computed host-side and replayed to the RTL as a "
    "deterministic per-sample Q16.15 word (fq). No RTL transcendental is "
    "implemented or claimed; every exact-integer site around the boundary "
    "(depth product, pitch sum, MIDI clamp, increment division, u32 phase "
    "wrap, quarter-wave LUT address/interpolation, S4 narrowing, the "
    "saturations) is RTL-owned."
)


def build_vector(
    case_id: str,
    physical: dict,
    formats: AcceptedFormats,
    regime: dict,
    frozen: dict = None,
    rendered_words: list = None,
) -> dict:
    """One committed vector: frozen-binding or directed-regime.

    The sidecar custody verification, the dedicated-render check, and the
    vector assembly are shared in ``golden_vectors.build_vco_vector``
    (#535); this wrapper supplies the sine lane's derivation hook,
    static words, and regime/measured provenance.
    """

    def extra_provenance(derivation: dict) -> dict:
        return {
            "regime": dict(regime),
            "measured": {
                "midi_clamps": derivation["clamps"],
                "saturations": sum(
                    record["count"]
                    for record in derivation["counters"]["records"]
                    if record["kind"] == "saturation"
                ),
            },
        }

    return gv.build_vco_vector(
        case_id=case_id,
        physical=physical,
        formats=formats,
        trace_name="vco_1.raw",
        mirror_stream_key="vco",
        derive_case=vg.derive_case,
        static_words=lambda derivation: {
            "keyboard.midi_f0": derivation["words"]["keyboard.midi_f0"],
            "vco_1.tuning": derivation["words"]["vco_1.tuning"],
            "vco_1.mod_depth": derivation["words"]["vco_1.mod_depth"],
            "init_phase_word": derivation["init_word"],
        },
        issue=ISSUE,
        generator=GENERATOR,
        shadow_boundary=SHADOW_BOUNDARY,
        extra_provenance=extra_provenance,
        frozen=frozen,
        rendered_words=rendered_words,
    )


def frozen_regime(physical: dict) -> dict:
    """The regime row for a frozen-binding case (declared, not inferred)."""

    return {
        "frequency": "mid",
        "phase": phase_point_name(physical["vco_1.initial_phase"]),
        "modulation": (
            "mod" if float(physical["vco_1.mod_depth"]) != 0.0 else "unmod"
        ),
        "class": "frozen-binding",
    }


def phase_point_name(value: float) -> str:
    for name, point in PHASE_POINTS:
        if float(value) == point:
            return name
    return "other"


def directed_cases():
    """The declared directed matrix: (case_id, overrides, regime)."""

    for freq_name, midi in FREQ_POINTS:
        for phase_name, phase in PHASE_POINTS:
            for mod_name in ("unmod", "mod"):
                overrides = {
                    "keyboard.midi_f0": midi,
                    "vco_1.initial_phase": phase,
                }
                depth = 0.0
                if mod_name == "mod":
                    depth = MOD_DEPTH_BY_FREQ[freq_name]
                    overrides["vco_1.mod_depth"] = depth
                    overrides[MOD_ROUTE] = 1.0
                case_id = "freq:%s-phase:%s-%s" % (
                    freq_name, phase_name, mod_name
                )
                yield case_id, overrides, {
                    "frequency": freq_name,
                    "phase": phase_name,
                    "modulation": mod_name,
                    "mod_depth": depth,
                    "class": "directed-matrix",
                }
    for case_id, overrides in CORNER_CASES:
        yield case_id, overrides, {
            "frequency": "other",
            "phase": "mid",
            "modulation": (
                "mod" if "vco_1.mod_depth" in overrides else "unmod"
            ),
            "mod_depth": float(overrides.get("vco_1.mod_depth", 0.0)),
            "class": "directed-corner",
        }


def build_all(formats: AcceptedFormats, verbose: bool = False) -> dict:
    """The lane's full vector set, via ``golden_vectors.build_vco_vectors``.

    The frozen-sidecar iteration, the directed-case renders, and the
    non-vacuity refusal all run in the shared builder (#535).
    """

    return gv.build_vco_vectors(
        formats=formats,
        directed_path=DIRECTED_PATH,
        trace_name="vco_1.raw",
        frozen_sidecar_cases=FROZEN_SIDECAR_CASES,
        regime_cases=directed_cases(),
        build_case_vector=build_vector,
        frozen_case_extras=lambda case: (frozen_regime(case["parameters"]),),
        verbose=verbose,
        verbose_report=lambda case_id, vector: print(
            "built %s (clamps %d)"
            % (case_id, vector["provenance"]["measured"]["midi_clamps"])
        ),
        post_check=check_matrix_non_vacuity,
    )


def check_matrix_non_vacuity(vectors: dict) -> None:
    """Refuse a matrix whose "modulated" cells are trace-degenerate.

    A modulated cell whose ``vco_1.raw`` equals its unmodulated sibling
    proves nothing about the modulation path (it happens whenever the
    depth drives the pitch out of the MIDI band on every sample), so the
    generator refuses to commit one.
    """

    by_cell = {}
    for case_id, vector in vectors.items():
        regime = vector["provenance"]["regime"]
        if regime["class"] != "directed-matrix":
            continue
        by_cell[(regime["frequency"], regime["phase"], regime["modulation"])] \
            = vector["provenance"]["vco_1_raw"]["words_sha256"]
    for (freq, phase, mod), digest in sorted(by_cell.items()):
        if mod != "mod":
            continue
        plain = by_cell.get((freq, phase, "unmod"))
        if plain is None:
            raise SystemExit(
                "modulated cell freq=%s phase=%s has no unmodulated sibling"
                % (freq, phase)
            )
        if plain == digest:
            raise SystemExit(
                "vacuous modulated cell freq=%s phase=%s: its vco_1.raw is "
                "identical to the unmodulated cell (the depth leaves the "
                "pitch clamped on every sample); pick a depth that sweeps "
                "the band interior" % (freq, phase)
            )


def main() -> int:
    return gv.run_vco_generator(
        None,
        description=__doc__,
        out_dir=OUT_DIR,
        build_all=build_all,
        tool_name="sine VCO",
        frozen_count=len(FROZEN_SIDECAR_CASES),
        regime_label="directed",
    )


if __name__ == "__main__":
    sys.exit(main())
