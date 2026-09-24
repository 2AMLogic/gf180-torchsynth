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
from torchsynth_voice import vco2_golden as vg  # noqa: E402
from torchsynth_voice.fixed_voice import (  # noqa: E402
    AcceptedFormats,
    FixedVoiceModel,
)

DIRECTED_PATH = ROOT / "spec/reference/directed-voice-v1.json"
FROZEN_GOLDEN = ROOT / "sim/reference/fixed-voice-golden-v1.json"
TRACE_DIR = ROOT / "sim/reference/fixed-voice-golden-v1-traces"
CONSTANTS_PACKAGE = ROOT / "tb/sv/gf180_rtl_constants_pkg.sv"
CHOICES_REGISTER = ROOT / "spec/reference/fixedpoint-choices-v1.json"
OUT_DIR = ROOT / "sim/reference/square-saw-vco-golden-v1"

ISSUE = 74

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


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load_json(path: Path):
    return json.loads(path.read_bytes())


def render_fixed_vco2(formats: AcceptedFormats, physical: dict):
    """Render one case through the frozen whole-voice fixed model.

    The normalized entries are placeholders: the fixed model consumes the
    physical map only (the normalized map is the float-side seam), and the
    committed evidence here is the fixed model's own vco_2.raw words.
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
    return fixed["vco_2.raw"]


def evidence_row(words: list) -> dict:
    """The committed evidence row for one vco_2.raw stream (mm convention)."""

    pins = {0: words[0], 1: words[1], 2: words[2]}
    for j in (99, 441, 1764, 17640, 88200, 105840, 176397, 176398, 176399):
        pins[j] = words[j]
    return {
        "sample_count": len(words),
        "words_sha256": vg.voice_digest(words),
        "first_word": words[0],
        "last_word": words[-1],
        "jitter": {str(j): w for j, w in sorted(pins.items())},
    }


def build_vector(
    case_id: str,
    physical: dict,
    formats: AcceptedFormats,
    frozen: dict = None,
    rendered_words: list = None,
) -> dict:
    """One committed vector: frozen-binding or dedicated-regime."""

    derivation = vg.derive_case(formats, physical)
    mirror_digest = vg.voice_digest(derivation["streams"]["v2"])

    provenance = {
        "issue": ISSUE,
        "generator": "tools/generate_square_saw_vco_golden.py",
        "model_identity": "fixed-voice-v1",
        "case_id": case_id,
        "shadow_boundary": SHADOW_BOUNDARY,
        "dr_0008_status": "Accepted",
        "dr_0008_constants_package_sha256": file_digest(CONSTANTS_PACKAGE),
        "choices_register_sha256": file_digest(CHOICES_REGISTER),
        "static_words": {
            "keyboard.midi_f0": derivation["words"]["keyboard.midi_f0"],
            "vco_2.tuning": derivation["words"]["vco_2.tuning"],
            "vco_2.mod_depth": derivation["words"]["vco_2.mod_depth"],
            "vco_2.shape": derivation["words"]["vco_2.shape"],
            "partials_word": derivation["partials"].word,
            "init_phase_word": derivation["init_word"],
        },
        "vco_2_raw": None,
        "frozen_binding": None,
    }

    if frozen is not None:
        if mirror_digest != frozen["digest"]:
            raise SystemExit(
                "mirror does not reproduce the frozen vco_2.raw digest for "
                "%s: mirror %s frozen %s"
                % (case_id, mirror_digest, frozen["digest"])
            )
        sidecar_payload = (ROOT / frozen["sidecar_file"]).read_bytes()
        if sha256_bytes(sidecar_payload) != frozen["sidecar_sha256"]:
            raise SystemExit(
                "frozen sidecar bytes drifted for %s (%s); regenerate"
                % (case_id, frozen["sidecar_file"])
            )
        words = vg.unpack_words_f32le(sidecar_payload)
        if vg.voice_digest(words) != frozen["digest"]:
            raise SystemExit(
                "frozen sidecar words digest drift for %s" % case_id
            )
        row = evidence_row(words)
        if row["words_sha256"] != frozen["digest"]:
            raise SystemExit("evidence digest mismatch for " + case_id)
        provenance["vco_2_raw"] = row
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
        if vg.voice_digest(rendered_words) != mirror_digest:
            raise SystemExit(
                "mirror does not reproduce the model's own vco_2.raw render "
                "for %s" % case_id
            )
        provenance["vco_2_raw"] = evidence_row(rendered_words)
        provenance["derivation"] = (
            "FixedVoiceModel.render of the declared parameters; the "
            "committed digest is the model's own vco_2.raw words; the host "
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


def build_all(formats: AcceptedFormats) -> dict:
    directed = load_json(DIRECTED_PATH)
    base_p = {name: entry["physical"] for name, entry in directed["base"].items()}
    frozen_doc = load_json(FROZEN_GOLDEN)
    frozen_cases = {case["id"]: case for case in frozen_doc["cases"]}

    vectors = {}

    for case_id in FROZEN_SIDECAR_CASES:
        case = frozen_cases[case_id]
        sidecar_file = "sim/reference/fixed-voice-golden-v1-traces/%s.vco_2.raw.f32le" % case_id
        digest = case["traces"]["vco_2.raw"]
        vectors["frozen:" + case_id] = build_vector(
            "frozen:" + case_id,
            case["parameters"],
            formats,
            frozen={
                "golden_case_id": case_id,
                "digest": digest,
                "sidecar_file": sidecar_file,
                "sidecar_sha256": file_digest(ROOT / sidecar_file),
            },
        )

    for case_id, overrides in DEDICATED_CASES:
        physical = dict(base_p)
        physical.update(overrides)
        words = render_fixed_vco2(formats, physical)
        vectors[case_id] = build_vector(
            case_id, physical, formats, rendered_words=words
        )

    return vectors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed vectors against a fresh in-memory build",
    )
    args = parser.parse_args()

    try:
        formats = AcceptedFormats()
    except Exception as error:  # noqa: BLE001 - reported, never a silent pass
        print("REFUSED: accepted register refused: %s" % error)
        return 1

    vectors = build_all(formats)

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
        if mismatches:
            for case_id, why in mismatches:
                print("CHECK FAIL: %s: %s" % (case_id, why))
            return 1
        print(
            "CHECK OK: %d committed square/saw VCO vectors regenerate "
            "exactly (deterministic)" % len(vectors)
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
    print(
        "wrote %d vectors (%d frozen-binding, %d dedicated regime)"
        % (
            len(vectors),
            len(FROZEN_SIDECAR_CASES),
            len(DEDICATED_CASES),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
