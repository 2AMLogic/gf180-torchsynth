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


def case_parameters(physical: dict) -> dict:
    """Only the parameters the ADSR engine consumes, in canonical order."""

    names = ["keyboard.duration"]
    for prefix in ag.ADSR_PREFIXES:
        names.extend(prefix + stage for stage in STAGE_NAMES)
    return {name: physical[name] for name in names}


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

    provenance = gv.frozen_provenance(
        "tools/generate_adsr_golden.py",
        70,
        "FixedControlPath(accepted control_spec)._adsr over "
        "quantize_params(S1) entries; per-tick **alpha shadow words "
        "replayed host-side via torchsynth_voice.adsr_golden.mirror_ramp "
        "(asserted equal to the model's own _ramp rows)",
        case_id,
        frozen_receipt,
        RECEIPT_PATH,
    )
    return gv.build_vector_document(
        case_parameters(physical), traces, provenance, validate=True
    )


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
    return gv.frozen_receipt_binding(document, FROZEN_CASE_ID, RECEIPT_PATH)


def main(argv=None) -> int:
    return gv.run_case_generator(
        argv,
        description=__doc__,
        tool_name="generate_adsr_golden.py",
        out_dir=OUT_DIR,
        directed_path=DIRECTED_PATH,
        frozen_case_id=FROZEN_CASE_ID,
        case_table=case_table,
        build_case_document=build_case_document,
        frozen_receipt_document=frozen_receipt_binding_document,
    )


if __name__ == "__main__":
    sys.exit(main())
