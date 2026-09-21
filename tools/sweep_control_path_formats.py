#!/usr/bin/env python3
"""Sweep candidate fixed formats for the control path (issue #50).

Stdlib-only host-side driver. For every preregistered candidate
(``torchsynth_voice.format_sweep.CandidateSpec``) and every preregistered
fixture, this tool renders the landed float control path
(``torchsynth_voice.control_path``) and the candidate fixed model
(``torchsynth_voice.format_sweep.FixedControlPath``, composed from the landed
fixedpoint primitives) from the *same* physical map, then records per-trace
max-abs / RMSE / SNR rows, saturation and rounding counter totals, and the
per-trace verdict against the declared band.

Preregistration (normative text: ``spec/CONTROL-FORMAT-SWEEP.md``):

- Fixtures: every ``--directed-stride``-th case of
  ``spec/reference/directed-voice-v1.json`` (file order; physical maps are
  the base map plus the case physical overrides) plus ``--draws``
  deterministic corpus-style draws (``random.Random(4001 + i)``) over the
  per-parameter min/max bounds observed across all 392 directed effective
  maps. The committed 96-case development corpus' physical maps are
  digest-custody (operator-retained, never committed) and are NOT read here;
  the holdout is not read (zero reads). Normalized maps are validation-only
  at the resolved-request seam: the model consumes the physical map, and
  both sides always receive identical physical inputs.
- Bands: the landed frozen float-frame per-trace tolerances of
  ``spec/reference/control-path-rubric-v1.json`` for the 20 buffer traces;
  the two keyboard scalar traces are consumed-identity rows judged against
  the candidate's own entry-format half-LSB. Final R-L3 limits remain a #53
  ratification output (measure, then preregister; DR-0008 Section 10).
- Screening vs certification: every non-baseline candidate renders the five
  upsampled traces on the declared sample grid (stride 100 plus the exact
  endpoints). Full-length certification covers the baseline plus, per axis,
  the smallest-footprint candidate whose every grid row meets its band.
  Control-rate traces are always full length.
- Axis winner rule: among a axis's band-passing members, the smallest
  footprint by (word-width sum, fractional-bit sum, table entries,
  up-fraction bits) -- a format-size proxy, not a PPA claim.

Every recommendation this tool emits is CANDIDATE-pending-#53-ratification,
never accepted. It makes no RTL, synthesis, layout, signoff,
hardware-playback, or sound-fidelity claim.
"""

import argparse
import hashlib
import json
import math
import random
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import float_interfaces  # noqa: E402
from torchsynth_voice import control_path as float_cp  # noqa: E402
from torchsynth_voice.format_sweep import (  # noqa: E402
    NUMERIC_CONTRACT,
    SWEEP_SCHEMA,
    CandidateSpec,
    FixedControlPath,
    UndefinedControlState,
    trace_word_format,
)
from torchsynth_voice.fixedpoint.formats import parse_identity  # noqa: E402
from torchsynth_voice.fixedpoint.rounding import RoundingMode  # noqa: E402

DIRECTED_PATH = ROOT / "spec" / "reference" / "directed-voice-v1.json"
RUBRIC_PATH = ROOT / "spec" / "reference" / "control-path-rubric-v1.json"
GRID_STRIDE = 100
BASELINE = CandidateSpec(
    midi_format=parse_identity("Q10.21"),
    ctrl_format=parse_identity("Q2.21"),
    pitch_format=parse_identity("Q2.21"),
    up_frac_bits=31,
    lut_entries=4096,
    mode=RoundingMode.HALF_EVEN,
)
# One-factor-at-a-time axes around the baseline (the C1-C10
# selected-pending shapes). Member overrides are applied to the baseline.
AXES = (
    (
        "midi-domain",
        "C4 Q10.21 S1 entry word vs wider/narrower words; every candidate "
        "must hold the directed parameter range, and MIDI 151 feasibility "
        "is recorded as a range column",
        ("midi=Q12.19", "midi=Q8.23", "midi=Q7.16", "midi=Q9.6"),
    ),
    (
        "control-signal",
        "C1-shape Q2.21 control-rate word vs wider/narrower words; run-2 "
        "evidence: the LFO phase integrates envelope quantization, so "
        "fractional bits (not integer bits) bind — the 32-bit Q2.29 member "
        "was added before any recommendation was recorded",
        ("ctrl=Q1.22", "ctrl=Q3.20", "ctrl=Q2.15", "ctrl=Q2.9", "ctrl=Q2.29"),
    ),
    (
        "pitch-interface",
        "pitch-route column word at the control-to-VCO boundary; C3's "
        "Hz-valued word formation is #41 scope",
        ("pitch=Q2.29", "pitch=Q2.13", "pitch=Q2.9"),
    ),
    (
        "upsample-coordinate",
        "quantized interpolation fraction vs the exact rational; endpoints "
        "stay exact by construction; blend rounding variants are covered by "
        "the rounding-mode axis (the model applies one mode globally)",
        ("up=24", "up=16"),
    ),
    (
        "lfo-table",
        "C5 4096x24 quarter-wave table vs 8192/2048/1024-entry linear "
        "tables (the 1K quadratic fallback uses a different interpolator "
        "and is not instantiated here; 1K-linear bounds the table-size axis)",
        ("lut=8192", "lut=2048", "lut=1024"),
    ),
    (
        "rounding-mode",
        "C6 half-even vs truncation at every declared narrowing site",
        ("mode=trunc",),
    ),
)
ZERO_TOLERANCE_TRACES = ("keyboard.midi_f0", "keyboard.duration")
SCREEN_GRID = tuple(list(range(0, 176400, GRID_STRIDE)) + [176399])
NOISE_BYTES = b"\x00" * (176400 * 4)


def candidate_from_override(member: str) -> CandidateSpec:
    key, value = member.split("=", 1)
    if key == "midi":
        return replace(BASELINE, midi_format=parse_identity(value))
    if key == "ctrl":
        return replace(BASELINE, ctrl_format=parse_identity(value))
    if key == "pitch":
        return replace(BASELINE, pitch_format=parse_identity(value))
    if key == "up":
        if ":" in value:
            bits, mode = value.split(":")
            return replace(BASELINE, up_frac_bits=int(bits), mode=RoundingMode(mode))
        return replace(BASELINE, up_frac_bits=int(value))
    if key == "lut":
        return replace(BASELINE, lut_entries=int(value))
    if key == "mode":
        return replace(BASELINE, mode=RoundingMode(value))
    raise ValueError("unknown axis override: " + member)


def build_candidates():
    """The preregistered grid: baseline plus one-factor axis variants."""

    return [BASELINE] + [
        candidate_from_override(member)
        for _name, _rationale, members in AXES
        for member in members
    ]


def axis_of(spec):
    if spec == BASELINE:
        return "baseline"
    for name, _rationale, members in AXES:
        for member in members:
            if candidate_from_override(member) == spec:
                return name
    return "unassigned"


def footprint(spec):
    """Format-size proxy for the axis winner rule (not a PPA claim)."""

    return (
        spec.midi_format.width + spec.ctrl_format.width + spec.pitch_format.width,
        spec.midi_format.frac_bits
        + spec.ctrl_format.frac_bits
        + spec.pitch_format.frac_bits,
        spec.lut_entries,
        spec.up_frac_bits,
    )


def load_directed_fixtures(stride):
    document = json.loads(DIRECTED_PATH.read_text())
    base = document["base"]
    base_physical = {k: v["physical"] for k, v in base.items()}
    base_normalized = {k: v["normalized"] for k, v in base.items()}
    cases = document["cases"][::stride]
    fixtures = []
    for case in cases:
        physical = dict(base_physical)
        normalized = dict(base_normalized)
        for name, pair in case["overrides"].items():
            physical[name] = pair["physical"]
            normalized[name] = pair["normalized"]
        fixtures.append(("directed:" + case["id"], normalized, physical))
    return fixtures, base_normalized, len(document["cases"])


AMPLITUDE_ENVELOPE = 3.9
MAX_DRAW_ATTEMPTS = 12


def amplitude_ok(float_traces):
    """Declared draw bound: control-chain amplitudes stay inside +-3.9.

    Synthetic co-extreme draws (per-parameter marginal extrema composed
    simultaneously) are not calibrated upstream patches; a draw whose float
    reference leaves the declared envelope is rejected and redrawn, and the
    rejection is recorded. Keyboard scalars are MIDI/seconds units, not
    amplitude-class, and are excluded; upsampled traces are linear in their
    columns and covered by the control-rate bound.
    """

    for name, values in float_traces.items():
        if name.startswith("keyboard.") or name.startswith("control_upsample."):
            continue
        for value in values:
            if not (-AMPLITUDE_ENVELOPE <= value <= AMPLITUDE_ENVELOPE):
                return False
    return True


def load_draw_fixtures(count, base_normalized, attempts_record):
    document = json.loads(DIRECTED_PATH.read_text())
    base_physical = {k: v["physical"] for k, v in document["base"].items()}
    bounds = {name: [value, value] for name, value in base_physical.items()}
    for case in document["cases"]:
        for name, pair in case["overrides"].items():
            value = pair["physical"]
            bounds[name] = [min(bounds[name][0], value), max(bounds[name][1], value)]
    fixtures = []
    names = sorted(bounds)
    for index in range(count):
        for attempt in range(MAX_DRAW_ATTEMPTS):
            rng = random.Random((4001 + index) * 16 + attempt)
            physical = {
                name: rng.uniform(bounds[name][0], bounds[name][1]) for name in names
            }
            float_traces = render_float(dict(base_normalized), physical)
            if amplitude_ok(float_traces):
                fixtures.append(
                    (
                        "draw:seed-%d" % (4001 + index),
                        dict(base_normalized),
                        physical,
                    )
                )
                attempts_record.append(
                    {"draw": 4001 + index, "attempts": attempt + 1, "accepted": True}
                )
                break
            attempts_record.append(
                {
                    "draw": 4001 + index,
                    "attempt": attempt + 1,
                    "accepted": False,
                    "reason": "float control chain exceeds the declared +-%s amplitude envelope" % AMPLITUDE_ENVELOPE,
                }
            )
        else:
            raise SystemExit(
                "draw %d: no attempt inside the declared amplitude envelope"
                % (4001 + index)
            )
    return fixtures, bounds


def render_float(normalized, physical):
    request = float_interfaces.ResolvedRequest(
        0,
        normalized,
        {
            "seed": 13,
            "slot": 0,
            "sample_count": 176400,
            "sha256": hashlib.sha256(NOISE_BYTES).hexdigest(),
            "samples": NOISE_BYTES,
        },
        physical=physical,
        execution_status="canonical-batched",
    )
    return float_cp.ControlPathModel(request).render()


def scale_map(spec):
    """Per-trace word scale for exact binary64 word decoding."""

    return {
        trace: trace_word_format(spec, trace).scale
        for trace in float_cp.owned_control_traces()
    }


def sample_stats(float_trace, word_trace, scale, indices=None):
    """max abs, error sum sq, signal sum sq over one paired trace.

    ``indices`` aligns screened rows: ``word_trace[k]`` is the value at
    audio index ``indices[k]`` and is compared against
    ``float_trace[indices[k]]``. Full-length rows pass ``None`` (1:1).
    """

    max_abs = 0.0
    sum_sq = 0.0
    sig_sq = 0.0
    if indices is None:
        pairs = zip(float_trace, word_trace)
    else:
        pairs = ((float_trace[j], word) for j, word in zip(indices, word_trace))
    for reference, word in pairs:
        # word / scale is exact in binary64 for every swept format
        # (power-of-two scale, |word| < 2^33): no comparator rounding.
        error = reference - word / scale
        magnitude = error if error >= 0.0 else -error
        if magnitude > max_abs:
            max_abs = magnitude
        sum_sq += error * error
        sig_sq += reference * reference
    return max_abs, sum_sq, sig_sq


def blank_record(trace_names):
    return {
        "counters": {"max_total_saturation": 0, "max_total_rounding": 0},
        "traces": {
            name: {
                "max_abs": 0.0,
                "max_abs_fixture": None,
                "sum_sq": 0.0,
                "sig_sq": 0.0,
                "count": 0,
                "min_snr_db": None,
                "upsample_full_length": False,
            }
            for name in trace_names
        },
    }


def accumulate(
    record,
    model,
    word_traces,
    float_traces,
    scales,
    full_upsample,
    physical,
    upsample_indices=None,
):
    counters = model.counters.as_json()
    record["counters"]["max_total_saturation"] = max(
        record["counters"]["max_total_saturation"], counters["total_saturation"]
    )
    record["counters"]["max_total_rounding"] = max(
        record["counters"]["max_total_rounding"], counters["total_rounding"]
    )
    for name in scales:
        if name in ZERO_TOLERANCE_TRACES:
            # Consumed-identity rows: the reference is the binary64
            # physical value, so the row measures the pure S1 entry
            # quantization error of the candidate's own word.
            reference = [physical[name]]
            row_indices = None
        else:
            reference = float_traces[name]
            row_indices = (
                upsample_indices if name.startswith("control_upsample.") else None
            )
        max_abs, sum_sq, sig_sq = sample_stats(
            reference, word_traces[name], scales[name], row_indices
        )
        row = record["traces"][name]
        if max_abs > row["max_abs"]:
            row["max_abs"] = max_abs
            row["max_abs_fixture"] = current_fixture_id[0]
        row["sum_sq"] += sum_sq
        row["sig_sq"] += sig_sq
        count = len(word_traces[name])
        row["count"] += count
        if name.startswith("control_upsample.") and full_upsample:
            row["upsample_full_length"] = True
        err_rms = math.sqrt(sum_sq / count)
        sig_rms = math.sqrt(sig_sq / count)
        if err_rms > 0.0 and sig_rms > 0.0:
            snr = 20.0 * math.log10(sig_rms / err_rms)
            if row["min_snr_db"] is None or snr < row["min_snr_db"]:
                row["min_snr_db"] = snr


current_fixture_id = [None]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directed-stride", type=int, default=4)
    parser.add_argument("--draws", type=int, default=32)
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "sim" / "reference" / "control-format-sweep-v1.json",
    )
    parser.add_argument("--smoke", action="store_true", help="3 fixtures, baseline only")
    args = parser.parse_args()

    started = time.time()
    if args.smoke:
        args.directed_stride = 130  # three directed cases
        args.draws = 1
    specs = build_candidates() if not args.smoke else [BASELINE]
    trace_names = float_cp.owned_control_traces()
    scales = {spec.identity: scale_map(spec) for spec in specs}

    directed_fixtures, base_normalized, total_directed = load_directed_fixtures(
        args.directed_stride
    )
    draw_attempts = []
    draw_fixtures, bounds = load_draw_fixtures(
        args.draws, base_normalized, draw_attempts
    )
    fixtures = directed_fixtures + draw_fixtures

    rubric = json.loads(RUBRIC_PATH.read_text())
    tolerances = {
        name: item["limits"]["max_abs_error"]["tolerance"]
        for name, item in rubric["traces"].items()
    }

    records = {spec.identity: blank_record(trace_names) for spec in specs}
    refusals = []

    # Pass 1: every candidate; baseline at full length, others on the grid.
    for fixture_index, (fixture_id, normalized, physical) in enumerate(fixtures):
        current_fixture_id[0] = fixture_id
        float_traces = render_float(normalized, physical)
        for spec in specs:
            record = records[spec.identity]
            model = FixedControlPath(spec)
            try:
                word_traces = model.render_words(
                    physical,
                    None if spec == BASELINE else SCREEN_GRID,
                )
            except UndefinedControlState as error:
                refusals.append(
                    {
                        "fixture": fixture_id,
                        "identity": spec.identity,
                        "reason": str(error),
                    }
                )
                continue
            accumulate(
                record,
                model,
                word_traces,
                float_traces,
                scales[spec.identity],
                spec == BASELINE,
                physical,
                None if spec == BASELINE else SCREEN_GRID,
            )
        print(
            "[%d/%d] %s (%.1fs)"
            % (fixture_index + 1, len(fixtures), fixture_id, time.time() - started),
            flush=True,
        )

    def band_passes(spec):
        record = records[spec.identity]
        for name in trace_names:
            if name in ZERO_TOLERANCE_TRACES:
                bound = float(trace_word_format(spec, name).lsb / 2)
                if record["traces"][name]["max_abs"] > bound:
                    return False
                continue
            if record["traces"][name]["max_abs"] > tolerances[name]:
                return False
        return True

    # Certification set: the baseline (already full length) plus, per axis,
    # the smallest-footprint member whose every screen row meets its band.
    certified = [BASELINE]
    for axis_name, _rationale, members in [] if args.smoke else AXES:
        passing = [
            candidate_from_override(member)
            for member in members
            if band_passes(candidate_from_override(member))
        ]
        if passing:
            winner = min(passing, key=footprint)
            if winner != BASELINE:
                certified.append(winner)

    # Pass 2: full-length certification of non-baseline winners.
    for fixture_index, (fixture_id, normalized, physical) in enumerate(fixtures):
        winners = [spec for spec in certified if spec != BASELINE]
        if not winners:
            break
        current_fixture_id[0] = fixture_id
        float_traces = render_float(normalized, physical)
        for spec in winners:
            record = records[spec.identity]
            model = FixedControlPath(spec)
            try:
                word_traces = model.render_words(physical, None)
            except UndefinedControlState as error:
                refusals.append(
                    {
                        "fixture": fixture_id,
                        "identity": spec.identity,
                        "reason": str(error),
                    }
                )
                continue
            accumulate(
                record,
                model,
                word_traces,
                float_traces,
                scales[spec.identity],
                True,
                physical,
                None,
            )
        print(
            "[cert %d/%d] %s (%.1fs)"
            % (fixture_index + 1, len(fixtures), fixture_id, time.time() - started),
            flush=True,
        )

    # Assemble the evidence document.
    candidate_docs = []
    for spec in specs:
        record = records[spec.identity]
        rows = {}
        verdicts = []
        for name in trace_names:
            row = record["traces"][name]
            if name in ZERO_TOLERANCE_TRACES:
                entry_fmt = trace_word_format(spec, name)
                band_source = (
                    "consumed-identity bound: half-LSB of the candidate's "
                    "own %s word (the landed zero tolerance encodes the "
                    "float verbatim-consumption seam, not a word-width bound)"
                    % entry_fmt.identity
                )
                tolerance = float(entry_fmt.lsb / 2)
                row_class = "consumed-identity"
                samples_per_fixture = 1
            else:
                band_source = (
                    "landed frozen float frame (control-path-rubric-v1.json "
                    "max_abs_error tolerance, composed by rubric-v0.json)"
                )
                tolerance = float(tolerances[name])
                row_class = "landed-band"
                samples_per_fixture = (
                    176400
                    if (spec == BASELINE or row["upsample_full_length"])
                    and name.startswith("control_upsample.")
                    else (len(SCREEN_GRID) if name.startswith("control_upsample.") else 1764)
                )
            full_length_row = not name.startswith("control_upsample.") or (
                spec == BASELINE or row["upsample_full_length"]
            )
            passed = row["max_abs"] <= tolerance
            verdicts.append(passed)
            err_rms = math.sqrt(row["sum_sq"] / row["count"]) if row["count"] else None
            sig_rms = math.sqrt(row["sig_sq"] / row["count"]) if row["count"] else None
            rows[name] = {
                "row_class": row_class,
                "band_source": band_source,
                "tolerance": tolerance,
                "samples_per_fixture": samples_per_fixture,
                "sample_class": "full-length" if full_length_row else "screen-grid",
                "fixtures": len(fixtures),
                "max_abs": row["max_abs"],
                "max_abs_fixture": row["max_abs_fixture"],
                "rmse_pooled": err_rms,
                "min_snr_db": row["min_snr_db"],
                "verdict": "PASS" if passed else "FAIL",
            }
        candidate_docs.append(
            {
                "identity": spec.identity,
                "axis": axis_of(spec),
                "footprint_proxy": list(footprint(spec)),
                "spec": spec.to_json(),
                "counters": record["counters"],
                "certified_full_length": spec in certified,
                "rows": rows,
                "all_rows_pass": all(verdicts),
            }
        )

    baseline_doc = next(doc for doc in candidate_docs if doc["axis"] == "baseline")
    axis_winners = {}
    for axis_name, rationale, members in AXES:
        passing = [
            doc for doc in candidate_docs if doc["axis"] == axis_name and doc["all_rows_pass"]
        ]
        winner = (
            min(
                (doc for doc in passing if doc["identity"] != BASELINE.identity),
                key=lambda doc: tuple(doc["footprint_proxy"]),
                default=None,
            )
        )
        axis_winners[axis_name] = {
            "rationale": rationale,
            "members": list(members),
            "baseline_identity": BASELINE.identity,
            "baseline_all_rows_pass": baseline_doc["all_rows_pass"],
            "passing_members": [doc["identity"] for doc in passing],
            "smallest_passing_member": winner["identity"] if winner else None,
        }

    document = {
        "schema": SWEEP_SCHEMA,
        "issue": 50,
        "numeric_contract": NUMERIC_CONTRACT,
        "dr_0008_status": "Proposed",
        "recommendation_status": "CANDIDATE-pending-#53-ratification; never accepted",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
        "preregistration": {
            "candidate_grid": [doc["identity"] for doc in candidate_docs],
            "axes": {
                name: {
                    "rationale": rationale,
                    "members": list(members),
                    "baseline": BASELINE.identity,
                }
                for name, rationale, members in AXES
            },
            "fixture_rule": (
                "every %d-th directed case of directed-voice-v1.json in file "
                "order (base physical map plus case physical overrides) plus "
                "%d deterministic corpus-style draws (random.Random seed "
                "(4001+i)*16+attempt) over per-parameter min/max bounds of "
                "the 392 directed effective maps, rejected and redrawn when "
                "the float control chain leaves the declared +-%s amplitude "
                "envelope (synthetic co-extreme composition is not a "
                "calibrated upstream draw; the out-of-envelope saturation "
                "pathology observed in the superseded first run is recorded "
                "in spec/CONTROL-FORMAT-SWEEP.md); the ramp epsilon/length "
                "shadow carries Q2.60 words (run-2 granularity finding, see "
                "the spec); holdout reads: 0; committed corpus-case reads: 0"
                % (args.directed_stride, args.draws, AMPLITUDE_ENVELOPE)
            ),
            "draw_attempts": draw_attempts,
            "amplitude_envelope": AMPLITUDE_ENVELOPE,
            "directed_total": total_directed,
            "directed_used": len(directed_fixtures),
            "draws_used": len(draw_fixtures),
            "draw_bounds": bounds,
            "screen_grid_stride": GRID_STRIDE,
            "screen_grid_points": len(SCREEN_GRID),
            "certified_full_length_identities": [spec.identity for spec in certified],
            "holdout_reads": 0,
            "committed_corpus_cases_read": 0,
            "axis_winner_rule": (
                "smallest footprint (word-width sum, fractional-bit sum, "
                "table entries, up-fraction bits) among band-passing axis "
                "members; a format-size proxy, not a PPA claim"
            ),
            "band_policy": {
                "buffer_traces": (
                    "landed frozen float-frame per-trace power-of-two "
                    "tolerances (control-path-rubric-v1.json), the frame "
                    "rubric-v0.json composes; final R-L3 limits stay a #53 "
                    "measure-then-preregister output and are never loosenable "
                    "after freeze"
                ),
                "keyboard_scalars": (
                    "consumed-identity rows: the reference is the binary64 "
                    "physical value, so the row measures the pure S1 entry "
                    "quantization error against the candidate's own "
                    "half-LSB; the landed zero tolerance encodes the float "
                    "verbatim-consumption seam, not a width bound"
                ),
                "verdict_rule": (
                    "per-trace conjunction; no aggregate score selects a "
                    "candidate; per-module failures stay visible per row"
                ),
            },
        },
        "candidates": candidate_docs,
        "axis_winners": axis_winners,
        "recommendation": {
            "composed_identity": BASELINE.identity,
            "status": "CANDIDATE-pending-#53-ratification; never accepted",
            "baseline_all_rows_pass": next(
                doc["all_rows_pass"] for doc in candidate_docs if doc["axis"] == "baseline"
            ),
            "note": (
                "the composed C1-C10 selected-pending baseline is the "
                "recommended candidate iff every one of its rows passes; each "
                "axis separately records its smallest passing member with "
                "measured evidence; nothing here is accepted and no R-L3 "
                "threshold is set by this sweep"
            ),
        },
        "refusals": refusals,
        "limitations": [
            (
                "corpus-style draws stand in for the committed 96-case "
                "development corpus: its physical maps are digest-custody "
                "(operator-retained, never committed) and were not read; the "
                "committed-corpus leg belongs to the #53 ratification evidence"
            ),
            (
                "the ADSR alpha power and LFO weight exponent run in binary64 "
                "shadow on quantized operands (open #74-class approximation "
                "items, not swept format axes); the LUT table-size axis is swept"
            ),
            (
                "upsampled-trace rows of non-certified candidates are "
                "screen-grid rows, not full-length maxima; certified "
                "candidates carry full-length rows"
            ),
            (
                "no area/storage/operation proxy beyond the declared "
                "format-size winner rule is reported, and no synthesized-PPA "
                "claim of any kind is made"
            ),
        ],
        "receipts": {
            "elapsed_seconds": round(time.time() - started, 1),
            "command": "tools/sweep_control_path_formats.py " + " ".join(sys.argv[1:]),
            "smoke": bool(args.smoke),
        },
    }
    args.out.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n")
    print("wrote", args.out)
    failing = [doc["identity"] for doc in candidate_docs if not doc["all_rows_pass"]]
    print(
        "candidates: %d, all-rows-pass: %d, failing: %s"
        % (
            len(candidate_docs),
            len(candidate_docs) - len(failing),
            ",".join(failing) if failing else "none",
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
