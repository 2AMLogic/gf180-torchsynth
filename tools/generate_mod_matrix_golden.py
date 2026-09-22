#!/usr/bin/env python3
"""Generate the modulation-matrix + endpoint-aligned-upsample golden vectors
from the frozen fixed model (#72).

Every expected value is the frozen composition's own control path output:
``FixedControlPath(accepted control_spec)._mod_matrix`` and ``._upsample``
over S1-quantized entries — the exact class the frozen whole-voice model
instantiates. The frozen-mod-matrix-receipt case additionally re-derives
the frozen receipt's ten mod-matrix/upsample trace digests
(``sim/reference/fixed-voice-golden-v1.json``, PR #156) and refuses on any
drift, binding these vectors to the committed #54 golden evidence; it also
emits the five full-length audio streams as f32le sidecars under
``sim/reference/mod-matrix-golden-v1-traces/`` (the #54 convention: Q2.21
words carried losslessly through division by 2^21). Every case's audio
truth is digest-bound in provenance (``sha256`` over the packed words and
over the exact integer word list) with word-exact endpoint/jitter samples
inline; the tb regenerates full streams from the model and refuses on any
digest drift.

Case families (the body's ACs): the frozen receipt (mod-depth boundary),
route extremes in both signs (all twenty depths at +1.0 / -1.0, exercising
the no-clamp matrix output and Q2.21 saturation policy), zero depth (all
columns identically zero; endpoint exactness on the zero stream), mixed
signs with a distinct depth signature per route (route-localization teeth),
and an LFO shape-sweep column case (saw/rsaw and sqr/sin blends feeding the
matrix so the audio-rate columns carry rich dynamics).

Declared semantics under test: the 4x5 matrix is a plain weighted sum in
the pinned input order (main ADSR1, main ADSR2, post-VCA LFO1, post-VCA
LFO2) with NO clamp at any output; upsampling is endpoint-aligned linear
interpolation reading the exact rational coordinate ``j*1763/176399``
(align_corners=True; endpoints are exact copies of control indices 0 and
1763) with uQ.31 fraction words and one declared half-even blend narrowing
per interior sample. ZOH, off-endpoint (``j*1763/176400``), clamped-output
and selector mutations are negative controls only: each must fail.

The vectors are contract-bound through ``golden_vectors`` (constants
package, choice register, DR-0008 record digests) and validated through the
standard loader. ``--check`` regenerates in memory and byte-compares
against the committed files (determinism gate).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import golden_vectors as gv  # noqa: E402
from torchsynth_voice.format_sweep import (  # noqa: E402
    CONTROL_PREFIXES,
    CONTROL_SAMPLES,
    CONTROL_STAGE_PARAMS,
    FixedControlPath,
    LFO_PARAM_NAMES,
    consumed_parameter_names,
    quantize_params,
)
from torchsynth_voice.fixed_voice import AcceptedFormats  # noqa: E402
from torchsynth_voice.fixedpoint.counters import StickyCounters  # noqa: E402
from torchsynth_voice import mod_matrix_golden as mm  # noqa: E402

OUT_DIR = ROOT / "sim/reference/mod-matrix-golden-v1"
SIDECAR_DIR = ROOT / "sim/reference/mod-matrix-golden-v1-traces"
DIRECTED_PATH = ROOT / "spec/reference/directed-voice-v1.json"
RECEIPT_PATH = gv.RECEIPT_PATH
FROZEN_CASE_ID = "boundary:lfo_1.mod_depth:center"

#: The audio traces committed as f32le sidecars (the #54 byte-custody
#: convention). Remaining cases bind audio through provenance digests.
SIDECAR_CASES = ("frozen-mod-matrix-receipt",)

#: Word-exact interior/jitter audio indices committed inline per route
#: (endpoints 0 and 176399 included; control-grid multiples included).
JITTER_INDICES = (
    0, 1, 2, 99, 100, 441, 1763, 1764, 3527, 3528, 8819, 8820,
    17639, 17640, 35279, 35280, 52919, 52920, 70559, 70560,
    88199, 88200, 105839, 105840, 123479, 123480, 141119, 141120,
    158759, 158760, 176397, 176398, 176399,
)

LFO_SIDES = ("lfo_1.", "lfo_2.")


def load_frozen_physical() -> dict:
    """The frozen receipt case's public physical map (all consumed names)."""

    directed = json.loads(DIRECTED_PATH.read_text(encoding="utf-8"))
    base = {name: entry["physical"] for name, entry in directed["base"].items()}
    by_id = {case["id"]: case for case in directed["cases"]}
    case = by_id[FROZEN_CASE_ID]
    physical = dict(base)
    for name, override in case["overrides"].items():
        physical[name] = override["physical"]
    return physical


def case_parameters(physical: dict) -> dict:
    """Exactly the consumed parameter names, in fixed order."""

    return {name: physical[name] for name in consumed_parameter_names()}


def apply_overrides(physical: dict, overrides: dict) -> dict:
    merged = dict(physical)
    merged.update(overrides)
    return merged


def depth_overrides(values) -> dict:
    """{route: value} or scalar -> all twenty ``mod_matrix.*->`` names."""

    overrides = {}
    for source in mm.MOD_SOURCES:
        for route in mm.MOD_ROUTES:
            value = values(route, source) if callable(values) else values
            overrides["mod_matrix." + source + "->" + route] = value
    return overrides


def route_signatures() -> dict:
    """A distinct signed depth pattern per route and source."""

    return {
        "vco_1_pitch": {"adsr_1": 1.0, "adsr_2": 0.5, "lfo_1": 0.25, "lfo_2": -0.125},
        "vco_1_amp": {"adsr_1": -0.5, "adsr_2": 0.25, "lfo_1": -1.0, "lfo_2": 0.75},
        "vco_2_pitch": {"adsr_1": 0.75, "adsr_2": -1.0, "lfo_1": 0.5, "lfo_2": -0.25},
        "vco_2_amp": {"adsr_1": -0.25, "adsr_2": -0.75, "lfo_1": 1.0, "lfo_2": 0.5},
        "noise_amp": {"adsr_1": 0.5, "adsr_2": 1.0, "lfo_1": -0.5, "lfo_2": -1.0},
    }


def mixed_sign_depths() -> dict:
    overrides = {}
    for route, pattern in route_signatures().items():
        for source, value in pattern.items():
            overrides["mod_matrix." + source + "->" + route] = value
    return overrides


def case_table(frozen_physical: dict) -> list:
    """The committed case families, in file order."""

    frozen_depths = {
        name: frozen_physical[name]
        for name in consumed_parameter_names()
        if name.startswith("mod_matrix.")
    }
    return [
        (
            "frozen-mod-matrix-receipt",
            {},  # the frozen case verbatim
            True,
        ),
        (
            "route-extremes-positive",
            depth_overrides(1.0),
            False,
        ),
        (
            "route-extremes-negative",
            depth_overrides(-1.0),
            False,
        ),
        (
            "zero-depth",
            depth_overrides(0.0),
            False,
        ),
        (
            "mixed-sign-routes",
            mixed_sign_depths(),
            False,
        ),
        (
            "shape-sweep-columns",
            apply_overrides(
                {name: frozen_depths[name] for name in frozen_depths},
                {
                    "lfo_1.saw": 0.6, "lfo_1.rsaw": 0.4,
                    "lfo_1.sin": 0.0, "lfo_1.tri": 0.0, "lfo_1.sqr": 0.0,
                    "lfo_2.sqr": 0.5, "lfo_2.sin": 0.5,
                    "lfo_2.tri": 0.0, "lfo_2.saw": 0.0, "lfo_2.rsaw": 0.0,
                    "lfo_1.frequency": 7.3, "lfo_2.frequency": 11.9,
                    "lfo_1.mod_depth": 8.0, "lfo_2.mod_depth": 10.0,
                    "mod_matrix.lfo_1->vco_2_pitch": 0.75,
                    "mod_matrix.lfo_2->noise_amp": 0.9,
                },
            ),
            False,
        ),
    ]


def render_case(fcp, formats, physical):
    """Model-render one case: source columns, matrix words, audio streams.

    Everything comes from the frozen composition's own control path; the
    mirrors assert the integer re-walks equal the model's own rows.
    """

    counters = StickyCounters()
    words = quantize_params(physical, formats.midi, formats.mode, counters)

    rate_1 = fcp._adsr(words, "lfo_1_rate_adsr.")
    rate_2 = fcp._adsr(words, "lfo_2_rate_adsr.")
    amp_1 = fcp._adsr(words, "lfo_1_amp_adsr.")
    amp_2 = fcp._adsr(words, "lfo_2_amp_adsr.")
    lfo_1 = fcp._lfo(words, "lfo_1.", rate_1)
    lfo_2 = fcp._lfo(words, "lfo_2.", rate_2)
    post_1 = fcp._control_vca(lfo_1, amp_1)
    post_2 = fcp._control_vca(lfo_2, amp_2)
    adsr_1 = fcp._adsr(words, "adsr_1.")
    adsr_2 = fcp._adsr(words, "adsr_2.")

    signals = [adsr_1, adsr_2, post_1, post_2]
    columns, matrix_counters = mm.mirror_mod_matrix(fcp, words, signals)
    audio = {}
    audio_counters = {}
    for route in mm.MOD_ROUTES:
        stream, route_counters = mm.mirror_upsample(fcp, columns[route], route)
        audio[route] = stream
        audio_counters[route] = route_counters
    return {
        "words": words,
        "signals": signals,
        "columns": columns,
        "audio": audio,
        "matrix_counters": matrix_counters,
        "audio_counters": audio_counters,
    }


def words_digest(values: list) -> str:
    """The #54 trace-digest convention over the exact integer word list."""

    blob = json.dumps(
        values, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def audio_evidence(case_id: str, audio: dict) -> dict:
    """Provenance evidence block for one case's five audio streams."""

    evidence = {}
    for route in mm.MOD_ROUTES:
        trace = "control_upsample." + route
        stream = audio[route]
        row = {
            "sample_count": len(stream),
            "first_word": stream[0],
            "last_word": stream[-1],
            "words_sha256": words_digest(stream),
            "jitter": {str(j): stream[j] for j in JITTER_INDICES},
        }
        evidence[trace] = row
    return evidence


def write_sidecars(case_id: str, audio: dict, existing: dict, write: bool = True) -> dict:
    """Compute (and optionally write) the case's five sidecar custody rows."""

    if write:
        SIDECAR_DIR.mkdir(parents=True, exist_ok=True)
    custody = {}
    for route in mm.MOD_ROUTES:
        trace = "control_upsample." + route
        name = "%s.%s.f32le" % (case_id, trace)
        payload = mm.pack_words_f32le(audio[route])
        if write and existing.get(name) != payload:
            (SIDECAR_DIR / name).write_bytes(payload)
        custody[trace] = {
            "file": "mod-matrix-golden-v1-traces/" + name,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "words_sha256": words_digest(audio[route]),
        }
    return custody


def frozen_receipt_binding(rendered: dict) -> dict:
    """Re-derive the frozen receipt's ten mod-matrix/upsample digests."""

    receipt = json.loads(RECEIPT_PATH.read_text(encoding="utf-8"))
    case = next(c for c in receipt["cases"] if c["id"] == FROZEN_CASE_ID)
    digests = {}
    for route in mm.MOD_ROUTES:
        for prefix, values in (
            ("mod_matrix.", rendered["columns"][route]),
            ("control_upsample.", rendered["audio"][route]),
        ):
            name = prefix + route
            actual = words_digest(values)
            declared = case["traces"][name]
            if actual != declared:
                raise SystemExit(
                    "FROZEN RECEIPT DRIFT: %s digests to %s but the frozen "
                    "receipt (#54, PR #156) declares %s — the model or its "
                    "inputs moved; regenerate, do not overwrite the binding"
                    % (name, actual, declared)
                )
            digests[name] = declared
    return {
        "receipt": "sim/reference/fixed-voice-golden-v1.json",
        "case_id": FROZEN_CASE_ID,
        "trace_digests": digests,
        "verified": True,
    }


def frozen_receipt_record_digest() -> str:
    receipt = json.loads(RECEIPT_PATH.read_text(encoding="utf-8"))
    return receipt["bindings"]["dr_0008_record_sha256"]


def build_case_document(
    formats: AcceptedFormats,
    fcp: FixedControlPath,
    physical: dict,
    case_id: str,
    sidecar_existing: dict,
    rendered: dict = None,
    receipt: dict = None,
) -> dict:
    """Render one case through the model and assemble the vector document."""

    if rendered is None:
        rendered = render_case(fcp, formats, physical)

    traces = []
    for route in mm.MOD_ROUTES:
        traces.append(
            {
                "name": "mod_matrix." + route,
                "kind": "control",
                "encoding": "synthetic-int",
                "cycle_start": 0,
                "values": rendered["columns"][route],
            }
        )

    audio_traces = audio_evidence(case_id, rendered["audio"])
    if case_id in SIDECAR_CASES:
        custody = write_sidecars(
            case_id, rendered["audio"], sidecar_existing, write=False
        )
        for trace, row in custody.items():
            if audio_traces[trace]["words_sha256"] != row["words_sha256"]:
                raise SystemExit("sidecar custody digest mismatch: " + trace)
            audio_traces[trace]["sidecar"] = row

    provenance = {
        "generator": "tools/generate_mod_matrix_golden.py",
        "issue": 72,
        "model_identity": "fixed-voice-v1 (frozen composition control path, PR #156)",
        "dr_0008_status": "Accepted (reviewed merge; 2026-09-21)",
        "dr_0008_constants_package_sha256": gv.sha256_file(
            gv.CONSTANTS_PACKAGE_PATH
        ),
        "choices_register_sha256": gv.sha256_file(gv.CHOICES_REGISTER_PATH),
        "dr_0008_record_sha256": frozen_receipt_record_digest(),
        "derivation": (
            "FixedControlPath(accepted control_spec)._mod_matrix/._upsample "
            "over quantize_params(S1) entries; the exact integer mirrors in "
            "torchsynth_voice.mod_matrix_golden are asserted row-equal to "
            "the model's own rows. Audio truth is digest-bound per route "
            "(sha256 over the exact word list and over the packed f32le "
            "bytes where a sidecar exists) with word-exact endpoint/jitter "
            "samples inline; endpoints j=0 and j=176399 are exact copies of "
            "control indices 0 and 1763 (align_corners=True, coordinates "
            "j*1763/176399). No clamp exists at any matrix output."
        ),
        "case_id": case_id,
        "audio_traces": audio_traces,
        "matrix_counters": rendered["matrix_counters"],
    }
    if receipt is not None:
        provenance["frozen_receipt_binding"] = receipt

    document = {
        "schema": gv.VECTOR_SCHEMA,
        "schema_version": gv.SCHEMA_VERSION,
        "provenance": provenance,
        "trace_registry_version": None,  # filled below
        "parameter_inventory_commit": None,
        "clip": {
            "profile": gv.CANONICAL_PROFILE,
            "sample_rate_hz": gv.CANONICAL_SAMPLE_RATE_HZ,
            "sample_count": gv.CANONICAL_SAMPLE_COUNT,
            "control_rate_hz": gv.CANONICAL_CONTROL_RATE_HZ,
            "control_count": gv.CANONICAL_CONTROL_COUNT,
        },
        "parameters": case_parameters(physical),
        "traces": traces,
    }
    registry = gv.load_registry_document()
    inventory = gv.load_inventory_document()
    document["trace_registry_version"] = registry["semantic_version"]
    document["parameter_inventory_commit"] = inventory["source"]["commit"]
    document["content_hash"] = gv.compute_content_hash(document)

    gv.load_vector(document, registry=registry, inventory=inventory)
    gv.verify_accepted_contract(document)
    return document


def existing_sidecars() -> dict:
    """Current sidecar bytes by file name, so unchanged files keep mtime."""

    existing = {}
    if SIDECAR_DIR.exists():
        for path in sorted(SIDECAR_DIR.glob("*.f32le")):
            existing[path.name] = path.read_bytes()
    return existing


def build_all(emit_sidecars: bool = False) -> dict:
    """Every committed case document, keyed by case id (plus renders)."""

    formats = AcceptedFormats()
    fcp = FixedControlPath(formats.control_spec)
    frozen_physical = load_frozen_physical()
    sidecar_existing = existing_sidecars()

    documents = {}
    rendered_map = {}
    for case_id, overrides, binds_receipt in case_table(frozen_physical):
        physical = apply_overrides(frozen_physical, overrides)
        rendered = render_case(fcp, formats, physical)
        rendered_map[case_id] = rendered
        receipt = None
        if binds_receipt:
            receipt = frozen_receipt_binding(rendered)
        documents[case_id] = build_case_document(
            formats, fcp, physical, case_id, sidecar_existing, rendered, receipt
        )
    if emit_sidecars:
        for case_id in SIDECAR_CASES:
            write_sidecars(case_id, rendered_map[case_id]["audio"], sidecar_existing)
    return documents, rendered_map


def document_bytes(document: dict) -> bytes:
    return (
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    ).encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="regenerate in memory and byte-compare the committed files",
    )
    args = parser.parse_args()

    documents, rendered_map = build_all(emit_sidecars=False)

    if args.check:
        failures = []
        sidecar_existing = existing_sidecars()
        for case_id, document in sorted(documents.items()):
            path = OUT_DIR / (case_id + ".json")
            if not path.exists():
                failures.append("missing vector file: %s" % path)
            elif path.read_bytes() != document_bytes(document):
                failures.append("stale vector file: %s" % path)
            for trace, row in document["provenance"]["audio_traces"].items():
                if row["words_sha256"] != words_digest(rendered_map[case_id]["audio"][trace.split(".", 1)[1]]):
                    failures.append("audio digest drift: %s %s" % (case_id, trace))
                sidecar = row.get("sidecar")
                if sidecar is not None:
                    payload = mm.pack_words_f32le(
                        rendered_map[case_id]["audio"][trace.split(".", 1)[1]]
                    )
                    name = sidecar["file"].split("/", 1)[1]
                    if hashlib.sha256(payload).hexdigest() != sidecar["sha256"]:
                        failures.append("sidecar custody drift: %s %s" % (case_id, trace))
                    if sidecar_existing.get(name) not in (None, payload):
                        failures.append("committed sidecar bytes drifted: %s" % name)
        if failures:
            print("MOD-MATRIX GOLDEN CHECK FAILED:")
            for row in failures:
                print("  " + row)
            return 1
        print(
            "MOD-MATRIX GOLDEN CHECK OK (%d vectors + %d sidecar streams "
            "byte-exact against the live frozen model)"
            % (len(documents), len(SIDECAR_CASES) * len(mm.MOD_ROUTES))
        )
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for case_id, document in sorted(documents.items()):
        path = OUT_DIR / (case_id + ".json")
        path.write_bytes(document_bytes(document))
        print("wrote %s" % path)
    sidecar_existing = existing_sidecars()
    for case_id in SIDECAR_CASES:
        write_sidecars(case_id, rendered_map[case_id]["audio"], sidecar_existing)
    print(
        "wrote %d case vectors (%d sidecar streams under %s)"
        % (len(documents), len(SIDECAR_CASES) * len(mm.MOD_ROUTES), SIDECAR_DIR.name)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
