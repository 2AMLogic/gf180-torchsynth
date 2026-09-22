#!/usr/bin/env python3
"""Generate the ADSR engine golden vectors from the frozen fixed model (#70).

Every expected value is the frozen composition's own control path output:
``FixedControlPath(control_spec(accepted register))._adsr`` over S1-quantized
entries — the exact class the frozen whole-voice model instantiates. The
frozen-envelope-receipt case additionally re-derives the frozen receipt's
per-trace digests (``sim/reference/fixed-voice-golden-v1.json``, PR #156)
for all six envelope traces and refuses on any drift, binding these vectors
to the committed #54 golden evidence.

Case families (AC-2): frozen receipt, attack sweep (short/long/overlap/
exact-zero/degenerate-ULP), decay+release sweep, sustain+alpha sweep,
boundary-tie (sustain 0.5 over an odd decay word ties every factor
narrowing; alpha 1.0 linear boundary), min/max/degenerate extremes.

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
from torchsynth_voice import adsr_golden as ag  # noqa: E402
from torchsynth_voice.fixed_voice import AcceptedFormats  # noqa: E402
from torchsynth_voice.format_sweep import (  # noqa: E402
    FixedControlPath,
    quantize_params,
)
from torchsynth_voice.fixedpoint.counters import StickyCounters  # noqa: E402

OUT_DIR = ROOT / "sim/reference/adsr-golden-v1"
DIRECTED_PATH = ROOT / "spec/reference/directed-voice-v1.json"
RECEIPT_PATH = gv.RECEIPT_PATH
FROZEN_CASE_ID = "boundary:adsr_1.alpha:lower"

STAGE_NAMES = ("attack", "decay", "sustain", "alpha", "release")
ULP_Q1630 = 2.0**-30  # one LSB of the Q16.30 stage-time entry word, in seconds


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
    """Only the parameters the ADSR engine consumes, in canonical order."""

    names = ["keyboard.duration"]
    for prefix in ag.ADSR_PREFIXES:
        names.extend(prefix + stage for stage in STAGE_NAMES)
    return {name: physical[name] for name in names}


def apply_overrides(physical: dict, overrides: dict) -> dict:
    merged = dict(physical)
    merged.update(overrides)
    return merged


def per_instance(physical: dict, stage_values: dict) -> dict:
    """Overrides for one case: {prefix: {stage: value}} -> full name map."""

    overrides = {}
    for prefix, stages in stage_values.items():
        for stage, value in stages.items():
            overrides[prefix + stage] = value
    return apply_overrides(physical, overrides)


def build_case_document(
    formats: AcceptedFormats,
    fcp: FixedControlPath,
    physical: dict,
    case_id: str,
    frozen_receipt: dict = None,
) -> dict:
    """Render one case through the model and assemble the vector document."""

    counters = StickyCounters()
    words = quantize_params(physical, formats.midi, formats.mode, counters)

    traces = []
    formations = {}
    for prefix in ag.ADSR_PREFIXES:
        formation = ag.derive_formation(fcp, words, prefix)
        formations[prefix] = formation
        shadow_rows = {}
        for stage in ("attack", "decay", "release"):
            _values, shape_row = ag.mirror_ramp(
                fcp,
                formation[stage + "_q"],
                formation[stage + "_exact"],
                formation["attack_q"] if stage == "decay"
                else (formation["duration_q"] if stage == "release" else 0),
                stage != "attack",
                formation["alpha"],
            )
            shadow_rows[stage] = shape_row
        envelope = fcp._adsr(words, prefix)
        traces.append(
            {
                "name": ag.trace_name(prefix),
                "kind": "control",
                "encoding": "synthetic-int",
                "cycle_start": 0,
                "values": envelope,
            }
        )
        formations[prefix]["shadow"] = shadow_rows

    provenance = {
        "generator": "tools/generate_adsr_golden.py",
        "issue": 70,
        "model_identity": "fixed-voice-v1 (frozen composition control path, PR #156)",
        "dr_0008_status": "Accepted (reviewed merge; 2026-09-21)",
        "dr_0008_constants_package_sha256": gv.sha256_file(
            gv.CONSTANTS_PACKAGE_PATH
        ),
        "choices_register_sha256": gv.sha256_file(gv.CHOICES_REGISTER_PATH),
        "dr_0008_record_sha256": frozen_receipt_record_digest(),
        "derivation": (
            "FixedControlPath(accepted control_spec)._adsr over "
            "quantize_params(S1) entries; per-tick **alpha shadow words "
            "replayed host-side via torchsynth_voice.adsr_golden.mirror_ramp "
            "(asserted equal to the model's own _ramp rows)"
        ),
        "case_id": case_id,
    }
    if frozen_receipt is not None:
        provenance["frozen_receipt_binding"] = frozen_receipt

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


def frozen_receipt_record_digest() -> str:
    receipt = json.loads(RECEIPT_PATH.read_text(encoding="utf-8"))
    return receipt["bindings"]["dr_0008_record_sha256"]


def frozen_receipt_binding(document: dict) -> dict:
    """Re-derive the frozen receipt's six ADSR digests for one document."""

    receipt = json.loads(RECEIPT_PATH.read_text(encoding="utf-8"))
    case = next(c for c in receipt["cases"] if c["id"] == FROZEN_CASE_ID)
    digests = {}
    for trace in document["traces"]:
        name = trace["name"]
        declared = case["traces"][name]
        blob = json.dumps(
            trace["values"], sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        actual = hashlib.sha256(blob).hexdigest()
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


def case_table(frozen_physical: dict) -> list:
    """The committed case families, in file order."""

    defaults = {
        p + s: frozen_physical[p + s]
        for p in ag.ADSR_PREFIXES
        for s in STAGE_NAMES
    }

    def stages(**values) -> dict:
        resolved = {}
        for prefix in ag.ADSR_PREFIXES:
            resolved[prefix] = {
                name: values.get(name, defaults[prefix + name])
                for name in STAGE_NAMES
            }
        return resolved

    def per_prefix(mapping: dict) -> dict:
        resolved = {}
        for prefix in ag.ADSR_PREFIXES:
            merged = {name: defaults[prefix + name] for name in STAGE_NAMES}
            merged.update(mapping.get(prefix, {}))
            resolved[prefix] = merged
        return resolved

    attacks = [0.0, ULP_Q1630, 0.001, 0.02, 1.7, 10.0]
    decay_release = [
        (0.0, 0.0),
        (ULP_Q1630, ULP_Q1630),
        (0.1, 0.2),
        (10.0, 10.0),
        (0.5, 0.02),
        (0.02, 0.5),
    ]
    sustains = [0.0, 0.25, 0.5, 0.75, 1.0, 0.5]
    alphas = [0.1, 0.5, 0.9999999687075615, 0.31622776260375977, 0.9, 0.7]

    return [
        (
            "frozen-envelope-receipt",
            {},  # the frozen case verbatim
            True,
        ),
        (
            "attack-sweep",
            per_prefix({
                ag.ADSR_PREFIXES[i]: {"attack": attacks[i]} for i in range(6)
            }),
            False,
        ),
        (
            "decay-release-sweep",
            per_prefix({
                ag.ADSR_PREFIXES[i]: {
                    "decay": decay_release[i][0],
                    "release": decay_release[i][1],
                }
                for i in range(6)
            }),
            False,
        ),
        (
            "sustain-alpha-sweep",
            per_prefix({
                ag.ADSR_PREFIXES[i]: {
                    "sustain": sustains[i],
                    "alpha": alphas[i],
                }
                for i in range(6)
            }),
            False,
        ),
        (
            "boundary-tie",
            per_prefix({
                ag.ADSR_PREFIXES[0]: {"sustain": 0.5},
                ag.ADSR_PREFIXES[1]: {"sustain": 0.5, "alpha": 1.0},
                ag.ADSR_PREFIXES[2]: {"alpha": 1.0},
                ag.ADSR_PREFIXES[3]: {"sustain": 0.5, "decay": ULP_Q1630},
                ag.ADSR_PREFIXES[4]: {"sustain": 0.5, "release": ULP_Q1630},
                ag.ADSR_PREFIXES[5]: {
                    "sustain": 0.5,
                    "attack": ULP_Q1630,
                    "alpha": 1.0,
                },
            }),
            False,
        ),
        (
            "min-max-degenerate",
            per_prefix({
                ag.ADSR_PREFIXES[0]: {
                    "attack": 0.0,
                    "decay": 0.0,
                    "release": 0.0,
                },
                ag.ADSR_PREFIXES[1]: {
                    "attack": ULP_Q1630,
                    "decay": ULP_Q1630,
                    "release": ULP_Q1630,
                },
                ag.ADSR_PREFIXES[2]: {"sustain": 1.0, "alpha": 0.9999999687075615},
                ag.ADSR_PREFIXES[3]: {"sustain": 0.0, "alpha": 0.1},
                ag.ADSR_PREFIXES[4]: {
                    "attack": 10.0,
                    "decay": 10.0,
                    "release": 10.0,
                    "alpha": 0.1,
                },
                ag.ADSR_PREFIXES[5]: {
                    "alpha": ULP_Q1630,
                },
            }),
            False,
        ),
    ]


def build_all(documents_out: dict = None) -> int:
    formats = AcceptedFormats()
    fcp = FixedControlPath(formats.control_spec)
    frozen_physical = load_frozen_physical()
    failures = 0
    for case_id, stage_values, is_frozen in case_table(frozen_physical):
        physical = apply_overrides(frozen_physical, stage_values)
        document = build_case_document(
            formats,
            fcp,
            physical,
            case_id,
            frozen_receipt=frozen_receipt_binding_document(formats, fcp, physical, case_id)
            if is_frozen
            else None,
        )
        payload = json.dumps(document, indent=1, sort_keys=True) + "\n"
        if documents_out is not None:
            documents_out[case_id] = payload
            continue
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        path = OUT_DIR / (case_id + ".json")
        path.write_text(payload, encoding="utf-8")
        print(
            "wrote %s (%d traces x %d values)"
            % (path, len(document["traces"]), len(document["traces"][0]["values"]))
        )
    return failures


def frozen_receipt_binding_document(
    formats: AcceptedFormats, fcp: FixedControlPath, physical: dict, case_id: str
) -> dict:
    counters = StickyCounters()
    words = quantize_params(physical, formats.midi, formats.mode, counters)
    document = {
        "traces": [
            {
                "name": ag.trace_name(prefix),
                "values": fcp._adsr(words, prefix),
            }
            for prefix in ag.ADSR_PREFIXES
        ]
    }
    return frozen_receipt_binding(document)


def check_committed() -> int:
    """Byte-compare a fresh in-memory regeneration against the commits."""

    documents = {}
    build_all(documents_out=documents)
    ok = True
    for case_id, payload in sorted(documents.items()):
        path = OUT_DIR / (case_id + ".json")
        if not path.exists():
            print("CHECK FAIL: missing %s" % path)
            ok = False
            continue
        committed = json.loads(path.read_text(encoding="utf-8"))
        fresh = json.loads(payload)
        if gv.compute_content_hash(committed) != gv.compute_content_hash(fresh):
            print(
                "CHECK FAIL: %s does not match regeneration; rerun "
                "tools/generate_adsr_golden.py and commit" % path
            )
            ok = False
        else:
            print("CHECK OK: %s" % path.name)
    return 0 if ok else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        nargs="?",
        default="generate",
        choices=["generate", "check"],
        help="'generate' (re)writes the committed vector files; "
        "'check' byte-compares regeneration against them",
    )
    args = parser.parse_args(argv)
    try:
        AcceptedFormats()
    except Exception as error:  # noqa: BLE001 - refusal must be explicit
        print("GENERATION REFUSED: accepted register refused: %s" % error)
        return 1
    if args.command == "check":
        return check_committed()
    return build_all()


if __name__ == "__main__":
    sys.exit(main())
