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

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import float_interfaces as fi  # noqa: E402
from torchsynth_voice import float_sources as fs  # noqa: E402
from torchsynth_voice import golden_vectors as gv  # noqa: E402
from torchsynth_voice import mod_matrix_golden as mm  # noqa: E402
from torchsynth_voice import vco_golden as vg  # noqa: E402
from torchsynth_voice.fixed_voice import (  # noqa: E402
    AcceptedFormats,
    FixedVoiceModel,
)

DIRECTED_PATH = ROOT / "spec/reference/directed-voice-v1.json"
FROZEN_GOLDEN = ROOT / "sim/reference/fixed-voice-golden-v1.json"
TRACE_DIR = ROOT / "sim/reference/fixed-voice-golden-v1-traces"
CONSTANTS_PACKAGE = ROOT / "tb/sv/gf180_rtl_constants_pkg.sv"
CHOICES_REGISTER = ROOT / "spec/reference/fixedpoint-choices-v1.json"
OUT_DIR = ROOT / "sim/reference/sine-vco-golden-v1"

ISSUE = 73

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
    # Negative mod_depth against an LFO-driven (bipolar) pitch column at
    # the top of the keyboard band: the signed depth minimum, and the
    # upper MIDI clamp arm.
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


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load_json(path: Path):
    return json.loads(path.read_bytes())


def render_fixed_vco1(formats: AcceptedFormats, physical: dict):
    """Render one case through the frozen whole-voice fixed model.

    The normalized entries are placeholders: the fixed model consumes the
    physical map only (the normalized map is the float-side seam), and the
    committed evidence here is the fixed model's own vco_1.raw words.
    """

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
        physical=dict(physical),
        execution_status="canonical-batched",
    )
    model = FixedVoiceModel(request, formats)
    fixed, _diagnostics = model.render()
    return fixed["vco_1.raw"]


def evidence_row(words: list) -> dict:
    """The committed evidence row for one vco_1.raw stream (#74 convention)."""

    pins = {0: words[0], 1: words[1], 2: words[2]}
    for j in (99, 441, 1764, 17640, 88200, 105840, 176397, 176398, 176399):
        pins[j] = words[j]
    return {
        "sample_count": len(words),
        "words_sha256": vg.digest_words(words),
        "first_word": words[0],
        "last_word": words[-1],
        "jitter": {str(j): w for j, w in sorted(pins.items())},
    }


def build_vector(
    case_id: str,
    physical: dict,
    formats: AcceptedFormats,
    regime: dict,
    frozen: dict = None,
    rendered_words: list = None,
) -> dict:
    """One committed vector: frozen-binding or directed-regime."""

    derivation = vg.derive_case(formats, physical)
    mirror_words = derivation["streams"]["vco"]
    mirror_digest = vg.digest_words(mirror_words)

    provenance = {
        "issue": ISSUE,
        "generator": "tools/generate_sine_vco_golden.py",
        "model_identity": "fixed-voice-v1",
        "case_id": case_id,
        "regime": dict(regime),
        "shadow_boundary": SHADOW_BOUNDARY,
        "dr_0008_status": "Accepted",
        "dr_0008_constants_package_sha256": file_digest(CONSTANTS_PACKAGE),
        "choices_register_sha256": file_digest(CHOICES_REGISTER),
        "static_words": {
            "keyboard.midi_f0": derivation["words"]["keyboard.midi_f0"],
            "vco_1.tuning": derivation["words"]["vco_1.tuning"],
            "vco_1.mod_depth": derivation["words"]["vco_1.mod_depth"],
            "init_phase_word": derivation["init_word"],
        },
        "measured": {
            "midi_clamps": derivation["clamps"],
            "saturations": sum(
                record["count"]
                for record in derivation["counters"]["records"]
                if record["kind"] == "saturation"
            ),
        },
        "vco_1_raw": None,
        "frozen_binding": None,
    }

    if frozen is not None:
        if mirror_digest != frozen["digest"]:
            raise SystemExit(
                "mirror does not reproduce the frozen vco_1.raw digest for "
                "%s: mirror %s frozen %s"
                % (case_id, mirror_digest, frozen["digest"])
            )
        sidecar_payload = (ROOT / frozen["sidecar_file"]).read_bytes()
        if sha256_bytes(sidecar_payload) != frozen["sidecar_sha256"]:
            raise SystemExit(
                "frozen sidecar bytes drifted for %s (%s); regenerate"
                % (case_id, frozen["sidecar_file"])
            )
        words = mm.unpack_words_f32le(sidecar_payload)
        if vg.digest_words(words) != frozen["digest"]:
            raise SystemExit(
                "frozen sidecar words digest drift for %s" % case_id
            )
        row = evidence_row(words)
        if row["words_sha256"] != frozen["digest"]:
            raise SystemExit("evidence digest mismatch for " + case_id)
        provenance["vco_1_raw"] = row
        provenance["frozen_binding"] = {
            "verified": True,
            "golden_document": "sim/reference/fixed-voice-golden-v1.json",
            "case_id": frozen["golden_case_id"],
            "trace_digest": frozen["digest"],
            "sidecar": {
                "file": frozen["sidecar_file"],
                "sha256": frozen["sidecar_sha256"],
            },
        }
    else:
        if vg.digest_words(rendered_words) != mirror_digest:
            raise SystemExit(
                "mirror does not reproduce the model's own vco_1.raw render "
                "for %s" % case_id
            )
        provenance["vco_1_raw"] = evidence_row(rendered_words)
        provenance["derivation"] = (
            "FixedVoiceModel.render of the declared parameters; the "
            "committed digest is the model's own vco_1.raw words; the host "
            "mirror reproduced it exactly at generation time"
        )

    vector = {
        "schema": gv.VECTOR_SCHEMA,
        "schema_version": gv.SCHEMA_VERSION,
        "trace_registry_version": gv.load_registry_document()["semantic_version"],
        "parameter_inventory_commit": gv.load_inventory_document()["source"]["commit"],
        "clip": {
            "profile": gv.CANONICAL_PROFILE,
            "sample_rate_hz": gv.CANONICAL_SAMPLE_RATE_HZ,
            "sample_count": gv.CANONICAL_SAMPLE_COUNT,
            "control_rate_hz": gv.CANONICAL_CONTROL_RATE_HZ,
            "control_count": gv.CANONICAL_CONTROL_COUNT,
        },
        "parameters": dict(physical),
        "traces": [
            {
                "name": "keyboard.midi_f0",
                "kind": "scalar",
                "encoding": "synthetic-int",
                "cycle_start": 0,
                "values": [derivation["words"]["keyboard.midi_f0"]],
            }
        ],
        "provenance": provenance,
    }
    vector["content_hash"] = gv.compute_content_hash(vector)
    return vector


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
    directed = load_json(DIRECTED_PATH)
    base_p = {name: entry["physical"] for name, entry in directed["base"].items()}
    frozen_doc = load_json(FROZEN_GOLDEN)
    frozen_cases = {case["id"]: case for case in frozen_doc["cases"]}

    vectors = {}

    for case_id in FROZEN_SIDECAR_CASES:
        case = frozen_cases[case_id]
        sidecar_file = (
            "sim/reference/fixed-voice-golden-v1-traces/%s.vco_1.raw.f32le"
            % case_id
        )
        digest = case["traces"]["vco_1.raw"]
        vectors["frozen:" + case_id] = build_vector(
            "frozen:" + case_id,
            case["parameters"],
            formats,
            frozen_regime(case["parameters"]),
            frozen={
                "golden_case_id": case_id,
                "digest": digest,
                "sidecar_file": sidecar_file,
                "sidecar_sha256": file_digest(ROOT / sidecar_file),
            },
        )
        if verbose:
            print("built frozen:%s" % case_id)

    for case_id, overrides, regime in directed_cases():
        physical = dict(base_p)
        physical.update(overrides)
        words = render_fixed_vco1(formats, physical)
        vectors[case_id] = build_vector(
            case_id, physical, formats, regime, rendered_words=words
        )
        if verbose:
            print(
                "built %s (clamps %d)"
                % (case_id, vectors[case_id]["provenance"]["measured"]
                   ["midi_clamps"])
            )

    check_matrix_non_vacuity(vectors)
    return vectors


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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed vectors against a fresh in-memory build",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="report each built case"
    )
    args = parser.parse_args()

    try:
        formats = AcceptedFormats()
    except Exception as error:  # noqa: BLE001 - reported, never a silent pass
        print("REFUSED: accepted register refused: %s" % error)
        return 1

    vectors = build_all(formats, verbose=args.verbose)

    if args.check:
        mismatches = []
        for case_id, vector in sorted(vectors.items()):
            path = OUT_DIR / (case_id + ".json")
            if not path.exists():
                mismatches.append((case_id, "missing committed file"))
                continue
            committed = gv.load_vector(path)
            if committed != vector:
                mismatches.append((case_id, "regeneration differs"))
        extra = sorted(
            path.stem for path in OUT_DIR.glob("*.json")
            if path.stem not in vectors
        )
        for case_id in extra:
            mismatches.append((case_id, "committed file has no generator case"))
        if mismatches:
            for case_id, why in mismatches:
                print("CHECK FAIL: %s: %s" % (case_id, why))
            return 1
        print(
            "CHECK OK: %d committed sine VCO vectors regenerate exactly "
            "(deterministic)" % len(vectors)
        )
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for case_id, vector in sorted(vectors.items()):
        path = OUT_DIR / (case_id + ".json")
        path.write_text(
            json.dumps(vector, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print("wrote %s" % path.relative_to(ROOT))
    directed_count = len(vectors) - len(FROZEN_SIDECAR_CASES)
    print(
        "wrote %d vectors (%d frozen-binding, %d directed regime)"
        % (len(vectors), len(FROZEN_SIDECAR_CASES), directed_count)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
