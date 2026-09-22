#!/usr/bin/env python3
"""Generate the LFO + control-VCA golden vectors from the frozen fixed model (#71).

Every expected value is the frozen composition's own control path output:
``FixedControlPath(control_spec(accepted register))._lfo`` and
``._control_vca`` over S1-quantized entries — the exact class the frozen
whole-voice model instantiates. The frozen-lfo-receipt case additionally
re-derives the frozen receipt's per-trace digests
(``sim/reference/fixed-voice-golden-v1.json``, PR #156) for the four
LFO/VCA traces and refuses on any drift, binding these vectors to the
committed #54 golden evidence.

Case families (the body's ACs): the frozen receipt (mod-depth boundary),
single-shape sweeps and continuous blends (never selectors), weight
boundaries (tiny-weight normalization, equal-weight fsum ties),
mod-depth extremes including the negative-rate zero clamp, frequency and
phase extremes with multi-turn wrapping, rate/amplitude modulation
drives, and min/max/degenerate states (saturating entries, zero rate).
All-zero shape weights are the model's declared undefined state: the
generator refuses them rather than repairing them.

The vectors are contract-bound through ``golden_vectors`` (constants
package, choice register, DR-0008 record digests) and validated through
the standard loader. ``--check`` regenerates in memory and byte-compares
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
from torchsynth_voice import lfo_golden as lg  # noqa: E402
from torchsynth_voice.fixed_voice import AcceptedFormats  # noqa: E402
from torchsynth_voice.format_sweep import (  # noqa: E402
    FixedControlPath,
    quantize_params,
)
from torchsynth_voice.fixedpoint.counters import StickyCounters  # noqa: E402

OUT_DIR = ROOT / "sim/reference/lfo-vca-golden-v1"
DIRECTED_PATH = ROOT / "spec/reference/directed-voice-v1.json"
RECEIPT_PATH = gv.RECEIPT_PATH
FROZEN_CASE_ID = "boundary:lfo_1.mod_depth:center"

STAGE_NAMES = ("attack", "decay", "sustain", "alpha", "release")
LFO_STAGE_PARAMS = ("frequency", "mod_depth", "initial_phase") + tuple(
    "sin tri saw rsaw sqr".split()
)
ULP_Q1021 = 2.0**-21  # one LSB of the C4 Q10.21 entry word
PINNED_PI = 3.1415927410125732  # the model's own pinned binary32 pi literal
PINNED_TWO_PI = 2.0 * PINNED_PI


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
    """Only the parameters the LFO + control-VCA path consumes, in order."""

    names = ["keyboard.duration"]
    for prefix in ag.ADSR_PREFIXES:
        names.extend(prefix + stage for stage in STAGE_NAMES)
    for side in lg.LFO_SIDES:
        names.extend(side + param for param in LFO_STAGE_PARAMS)
    return {name: physical[name] for name in names}


def adsr_prefix(side: str, role: str) -> str:
    """The canonical envelope prefix of one LFO's rate/amp ADSR.

    ``lfo_1.`` + role ``rate`` -> ``lfo_1_rate_adsr.`` (the registry's
    own naming, as in ``FixedControlPath.render_words``).
    """

    return side[:-1] + "_" + role + "_adsr."


def apply_overrides(physical: dict, overrides: dict) -> dict:
    merged = dict(physical)
    merged.update(overrides)
    return merged


def per_side(mapping: dict, physical: dict) -> dict:
    """Overrides {side: {param: value}} -> full parameter-name map."""

    merged = dict(physical)
    for side, params in mapping.items():
        for name, value in params.items():
            merged[side + name] = value
    return merged


def owned_traces(fcp: FixedControlPath, words: dict) -> list:
    """Render the four owned traces through the model."""

    traces = []
    for side in lg.LFO_SIDES:
        rate_env = fcp._adsr(words, adsr_prefix(side, "rate"))
        amp_env = fcp._adsr(words, adsr_prefix(side, "amp"))
        raw = fcp._lfo(words, side, rate_env)
        post = fcp._control_vca(raw, amp_env)
        traces.append(
            {
                "name": lg.raw_trace(side),
                "kind": "control",
                "encoding": "synthetic-int",
                "cycle_start": 0,
                "values": raw,
            }
        )
        traces.append(
            {
                "name": lg.vca_trace(side),
                "kind": "control",
                "encoding": "synthetic-int",
                "cycle_start": 0,
                "values": post,
            }
        )
    return traces


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

    traces = owned_traces(fcp, words)

    provenance = {
        "generator": "tools/generate_lfo_vca_golden.py",
        "issue": 71,
        "model_identity": "fixed-voice-v1 (frozen composition control path, PR #156)",
        "dr_0008_status": "Accepted (reviewed merge; 2026-09-21)",
        "dr_0008_constants_package_sha256": gv.sha256_file(
            gv.CONSTANTS_PACKAGE_PATH
        ),
        "choices_register_sha256": gv.sha256_file(gv.CHOICES_REGISTER_PATH),
        "dr_0008_record_sha256": frozen_receipt_record_digest(),
        "derivation": (
            "FixedControlPath(accepted control_spec)._lfo/_control_vca over "
            "quantize_params(S1) entries; shape-weight **2.718281828 shadow "
            "and the S3 initial-turn word replayed host-side via "
            "torchsynth_voice.lfo_golden (asserted row-equal to the model's "
            "own _lfo loop by mirror_lfo)"
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
    """Re-derive the frozen receipt's four LFO/VCA digests for one document."""

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

    def sides(**per_side_values) -> dict:
        """{side.param: value} on dotted names, ready for apply_overrides."""

        return dict(per_side_values)

    return [
        (
            "frozen-lfo-receipt",
            {},  # the frozen case verbatim
            True,
        ),
        (
            "shape-single-sweep",
            sides(
                **{
                    "lfo_1.sin": 1.0, "lfo_1.tri": 0.0, "lfo_1.saw": 0.0,
                    "lfo_1.rsaw": 0.0, "lfo_1.sqr": 0.0,
                    "lfo_2.sin": 0.0, "lfo_2.tri": 1.0, "lfo_2.saw": 0.0,
                    "lfo_2.rsaw": 0.0, "lfo_2.sqr": 0.0,
                }
            ),
            False,
        ),
        (
            "shape-second-sweep",
            sides(
                **{
                    "lfo_1.sin": 0.0, "lfo_1.tri": 0.0, "lfo_1.saw": 1.0,
                    "lfo_1.rsaw": 0.0, "lfo_1.sqr": 0.0,
                    "lfo_2.sin": 0.0, "lfo_2.tri": 0.0, "lfo_2.saw": 0.0,
                    "lfo_2.rsaw": 1.0, "lfo_2.sqr": 0.0,
                }
            ),
            False,
        ),
        (
            "shape-square-equal-blend",
            sides(
                **{
                    "lfo_1.sin": 0.0, "lfo_1.tri": 0.0, "lfo_1.saw": 0.0,
                    "lfo_1.rsaw": 0.0, "lfo_1.sqr": 1.0,
                    "lfo_2.sin": 0.2, "lfo_2.tri": 0.2, "lfo_2.saw": 0.2,
                    "lfo_2.rsaw": 0.2, "lfo_2.sqr": 0.2,
                }
            ),
            False,
        ),
        (
            "shape-blend-boundary",
            sides(
                **{
                    # equal-weight fsum tie on lfo_1; tiny-weight
                    # normalization on lfo_2 (one ULP against one full weight)
                    "lfo_1.sin": 0.5, "lfo_1.tri": 0.5,
                    "lfo_1.saw": 0.0, "lfo_1.rsaw": 0.0, "lfo_1.sqr": 0.0,
                    "lfo_2.sin": ULP_Q1021, "lfo_2.tri": 1.0,
                    "lfo_2.saw": 0.0, "lfo_2.rsaw": 0.0, "lfo_2.sqr": 0.0,
                }
            ),
            False,
        ),
        (
            "shape-blend-mixed",
            sides(
                **{
                    "lfo_1.sin": 0.0, "lfo_1.tri": 0.0, "lfo_1.saw": 0.3,
                    "lfo_1.rsaw": 0.0, "lfo_1.sqr": 0.7,
                    "lfo_2.sin": 0.11, "lfo_2.tri": 0.22, "lfo_2.saw": 0.33,
                    "lfo_2.rsaw": 0.24, "lfo_2.sqr": 0.10,
                }
            ),
            False,
        ),
        (
            "shape-rsaw-dominant",
            sides(
                **{
                    "lfo_1.sin": 0.0, "lfo_1.tri": 0.0, "lfo_1.saw": 0.0,
                    "lfo_1.rsaw": 1.0, "lfo_1.sqr": 0.0,
                    "lfo_2.sin": 0.2, "lfo_2.tri": 0.0, "lfo_2.saw": 0.4,
                    "lfo_2.rsaw": 0.4, "lfo_2.sqr": 0.0,
                }
            ),
            False,
        ),
        (
            "depth-extremes",
            sides(
                **{
                    "lfo_1.mod_depth": 0.0,          # no rate modulation
                    "lfo_2.mod_depth": ULP_Q1021,    # one-LSB depth
                }
            ),
            False,
        ),
        (
            "depth-negative-clamp",
            sides(
                **{
                    # negative depth x envelope drives the modulated rate
                    # below zero: the declared pre-accumulation zero clamp
                    "lfo_1.frequency": 0.5, "lfo_1.mod_depth": -2.0,
                    "lfo_2.frequency": 2.0, "lfo_2.mod_depth": -10.0,
                }
            ),
            False,
        ),
        (
            "frequency-phase-extremes",
            sides(
                **{
                    "lfo_1.frequency": 0.0,          # frozen phase
                    "lfo_1.initial_phase": PINNED_PI,
                    "lfo_2.frequency": 220.0,        # 880 turns: heavy wrap
                    "lfo_2.initial_phase": 0.0,
                }
            ),
            False,
        ),
        (
            "phase-boundary",
            sides(
                **{
                    # 2*pi turns -> exactly 2^32: a half-even boundary word
                    # reduced to 0; negative phase wraps below the circle
                    "lfo_1.initial_phase": PINNED_TWO_PI,
                    "lfo_2.initial_phase": -PINNED_PI,
                    "lfo_1.frequency": 10.0,
                    "lfo_2.frequency": 10.0,
                }
            ),
            False,
        ),
        (
            "rate-amp-modulation",
            sides(
                **{
                    # rate-ADSR drives the modulated rate across its range
                    "lfo_1_rate_adsr.attack": 1.7,
                    "lfo_1_rate_adsr.decay": 0.5,
                    "lfo_1_rate_adsr.sustain": 1.0,
                    "lfo_1_rate_adsr.release": 1.0,
                    "lfo_1_rate_adsr.alpha": 0.5,
                    # amp-ADSR zeroes the VCA gain outright on lfo_2
                    "lfo_2_amp_adsr.attack": 0.0,
                    "lfo_2_amp_adsr.decay": 0.0,
                    "lfo_2_amp_adsr.sustain": 0.0,
                    "lfo_2_amp_adsr.release": 0.0,
                }
            ),
            False,
        ),
        (
            "min-max-degenerate",
            sides(
                **{
                    # saturating C4 entries (2000 > Q10.21 max) on lfo_1
                    "lfo_1.frequency": 2000.0,
                    "lfo_1.mod_depth": 2000.0,
                    # degenerate zero-rate lfo_2: rate 0, increment 0,
                    # phase frozen at the initial-turn word
                    "lfo_2.frequency": 0.0,
                    "lfo_2.mod_depth": 0.0,
                    "lfo_2.initial_phase": 1.0,
                    "lfo_2_amp_adsr.sustain": 1.0,
                    "lfo_2_amp_adsr.alpha": 0.1,
                }
            ),
            False,
        ),
    ]


def build_all(documents_out: dict = None) -> int:
    formats = AcceptedFormats()
    fcp = FixedControlPath(formats.control_spec)
    frozen_physical = load_frozen_physical()
    failures = 0
    for case_id, overrides, is_frozen in case_table(frozen_physical):
        physical = apply_overrides(frozen_physical, overrides)
        document = build_case_document(
            formats,
            fcp,
            physical,
            case_id,
            frozen_receipt_binding_document(formats, fcp, physical, case_id)
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
    document = {"traces": owned_traces(fcp, words)}
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
                "tools/generate_lfo_vca_golden.py and commit" % path
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
