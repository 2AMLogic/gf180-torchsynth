"""Audio VCA and pre-normalization mixer lane of the PDK-free testbench.

The runner binds its live core so receipt and constants checks are shared
when the lane is dispatched by the CLI or imported by the one-shot runners.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

TB_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(TB_ROOT.parent / "src"))
sys.path.insert(0, str(TB_ROOT))

from tb_harness import _compile_iverilog, _run, mutate_sv  # noqa: E402
from torchsynth_voice import directed as dr  # noqa: E402
from torchsynth_voice import golden_vectors as gv  # noqa: E402
from torchsynth_voice import mix_golden as mx  # noqa: E402
from torchsynth_voice.fixed_voice import entry_quantize  # noqa: E402
from torchsynth_voice.fixedpoint import schedule as sched  # noqa: E402
from torchsynth_voice.fixedpoint.choices import ChoiceNotAccepted  # noqa: E402
from torchsynth_voice.fixedpoint.counters import StickyCounters  # noqa: E402
from torchsynth_voice.fixedpoint.schedule import ScheduleNotAccepted  # noqa: E402

_core = None


def bind_core(core) -> None:
    """Bind the run_tb module that owns the shared receipt and constants."""

    global _core
    _core = core


MIX_DUT_SV = TB_ROOT / "sv/audio_mix_engine.sv"
MIX_TB_SV = TB_ROOT / "sv/tb_audio_mix_engine.sv"
#: The lane's declared output traces, in the model's registry naming.
MIX_STREAM_TRACES = (
    "vco_1.post_vca",
    "vco_2.post_vca",
    "noise.post_vca",
    "mixer.pre_normalization",
)
#: Declared directed fixtures (spec/reference/directed-voice-v1.json, the
#: manifest the frozen receipt's own case selection draws from). The
#: ``special`` family supplies the fixture classes the receipt's
#: param-committed cases cannot: total silence (every mixer level zero)
#: and the all-source / high-depth stress patch (all three mixer levels,
#: all twenty matrix routes, and every modulation/rate depth at 1.0).
MIX_FIXTURE_CASES = ("special:silence", "special:near-silence", "special:stress")
#: Per-lane curve evidence: the declared case whose recorded (normalized,
#: physical) pair makes that lane's upstream input-curve exponent
#: observable (``physical = normalized ** (1 / curve)``; a pair at 0 or 1
#: observes nothing). Independent per lane: the noise lane's power-40
#: curve and the two oscillators' linear curves are each read from their
#: own case, never inferred from one another.
MIX_CURVE_EVIDENCE = {
    "vco_1": "special:near-silence",
    "vco_2": "source:vco_2",
    "noise": "source:noise",
}
#: Receipt cases carrying the remaining declared fixture classes:
#: single-source isolation per audio path, the two high-depth boundaries,
#: and the declared peak-stress anchor variant.
MIX_SINGLE_SOURCE_CASES = ("source:vco_1", "source:vco_2", "source:noise")
MIX_HIGH_DEPTH_CASES = (
    "boundary:vco_1.mod_depth:upper",
    "boundary:vco_2.mod_depth:upper",
)
MIX_STRESS_CASE = "normalization-stress:anchor-3.9478583336"
#: Replay-independence pair: the level words, the peak register, and the
#: counters are per-trigger; run 0 state must not leak into run 1 (the
#: pair routes different lanes, so a leaked level word is visible).
MIX_REPLAY_PAIR = ("source:noise", "source:vco_1")
#: Mutation-simulation walk cap (+max_n): every mutation below
#: demonstrably bites within the first 24,000 audio samples (~0.54 s), so
#: the mutation sims walk a fraction of the grid. Committed-case and
#: fixture runs always walk the full 176,400 samples.
MIX_MUTATION_WALK_CAP = 24000

#: Anchored RTL mutation seams (mutate_sv refuses if an anchor moved).
MIX_RTL_MUTATIONS = {
    "level-route-swap": (
        "    wire signed [C1_WIDTH-1:0] lvl1_eff = lvl1_r;",
        "    wire signed [C1_WIDTH-1:0] lvl1_eff = lvl2_r;"
        "  // MUTANT: mixer level route swapped",
    ),
    "vca-2-polarity": (
        "    wire signed [95:0] vca2_eff = vca2_sat\n"
        "        ? ((vca2_narrow > 96'sd0) ? C1_MAX96 : C1_MIN96) : vca2_narrow;",
        "    wire signed [95:0] vca2_eff = -(vca2_sat\n"
        "        ? ((vca2_narrow > 96'sd0) ? C1_MAX96 : C1_MIN96) : vca2_narrow);"
        "  // MUTANT: vco_2 VCA polarity flipped",
    ),
    "mixer-dc-offset": (
        "    wire signed [95:0] mix_out = mix_eff;",
        "    wire signed [95:0] mix_out = mix_eff + 96'sd1;"
        "  // MUTANT: DC offset on the mix word",
    ),
    "mixer-truncate": (
        "    wire signed [95:0] mix_narrow =\n"
        "        div_half_even_pow2_signed(acc_sum, C1_FRAC_BITS);",
        "    wire signed [95:0] mix_narrow = (acc_sum >>> C1_FRAC_BITS);"
        "  // MUTANT: truncation instead of half-even",
    ),
    "mixer-wrap": (
        "    wire signed [95:0] mix_eff = mix_sat\n"
        "        ? ((mix_narrow > 96'sd0) ? C1_MAX96 : C1_MIN96) : mix_narrow;",
        "    wire signed [95:0] mix_eff = mix_narrow;"
        "  // MUTANT: C7 saturation dropped at the mixer narrowing",
    ),
    "dropped-noise-source": (
        "    wire signed [95:0] acc_sum = term1 + term2 + termn;",
        "    wire signed [95:0] acc_sum = term1 + term2;"
        "  // MUTANT: noise term dropped from the accumulator",
    ),
}


def mix_load_receipt():
    """The frozen receipt + accepted formats + the #76 declared cases.

    Reuses the sine lane's binding verification verbatim (same receipt,
    same live accepted contract: DR-0008 status, hash-linked C5 table,
    emitted constants package) and additionally requires every case this
    flow names.
    """

    payload, formats, cases = _core.vco_load_receipt()
    required = (
        MIX_SINGLE_SOURCE_CASES + MIX_HIGH_DEPTH_CASES
        + (MIX_STRESS_CASE,) + MIX_REPLAY_PAIR
    )
    for case_id in required:
        if case_id not in cases:
            raise SystemExit(
                "receipt carries no param-committed case %r" % case_id
            )
    return payload, formats, cases


def mix_load_fixtures():
    """The declared directed fixtures (physical + normalized maps).

    The manifest is validated through its own landed validator (identity
    seal + upstream-commit pin), so a drifted manifest refuses the run
    instead of silently feeding a different patch.
    """

    document = json.loads(dr.MANIFEST_PATH.read_text(encoding="utf-8"))
    dr.validate_manifest(document)
    by_id = {case["id"]: case for case in document["cases"]}
    fixtures = {}
    wanted = set(MIX_FIXTURE_CASES) | set(MIX_CURVE_EVIDENCE.values())
    for case_id in sorted(wanted):
        if case_id not in by_id:
            raise SystemExit(
                "declared directed manifest carries no fixture %r" % case_id
            )
        patch = dr.resolve_patch(document, by_id[case_id])
        fixtures[case_id] = {
            "physical": {name: pair["physical"] for name, pair in patch.items()},
            "normalized": {
                name: pair["normalized"] for name, pair in patch.items()
            },
        }
    return document, fixtures


def mix_derive_case(formats, case_id: str, physical: dict,
                    normalized: dict = None, frozen: dict = None):
    """Model-derived stimulus + truth for one case, frozen-pinned if committed.

    ``mx.derive_case`` renders the frozen composed model and refuses
    unless the lane mirror reproduces the model's own rows and per-site
    counter records. When the receipt commits this case's trace digests,
    the model's rows are additionally pinned to them here: the RTL is then
    held to the frozen evidence, not to a fresh render.
    """

    case = mx.derive_case(formats, physical, 0, normalized)
    if frozen is not None:
        for name in mx.MIX_TRACES:
            digest = mx.digest_words(case["model_traces"][name])
            if digest != frozen["traces"][name]:
                raise SystemExit(
                    "frozen-trace drift for %s on %s: rendered %s but the "
                    "receipt declares %s"
                    % (name, case_id, digest, frozen["traces"][name])
                )
    case["id"] = case_id
    case["frozen"] = frozen is not None
    return case


def mix_prefix_truth(formats, case: dict, cap: int):
    """The mirror truth for a capped prefix walk of an already-derived case."""

    streams, aux = mx.mirror_mix_lane(
        formats, case["raw"], case["amp"], case["levels"], samples=cap
    )
    return {"streams": streams, "aux": aux}


def mix_write_case(workdir: Path, run: int, case: dict, levels: dict = None,
                   amp: dict = None):
    """Write one run's stimulus files (level words + per-sample streams).

    ``levels`` / ``amp`` override the derived stimulus for the declared
    stimulus-side mutations (a wrong gain word, a swapped route); the
    committed runs always pass the derived maps.
    """

    use_levels = case["levels"] if levels is None else levels
    use_amp = case["amp"] if amp is None else amp
    (workdir / ("run%d_params.txt" % run)).write_text(
        " ".join(
            str(int(use_levels[lane]) & 0xFFFFFFFF) for lane in mx.LANES
        ) + "\n",
        encoding="utf-8",
    )
    raw = case["raw"]
    lines = []
    for n in range(case["samples"]):
        lines.append(
            "%d %d %d %d %d %d"
            % (
                raw["vco_1"][n] & 0xFFFFFFFF,
                raw["vco_2"][n] & 0xFFFFFFFF,
                raw["noise"][n] & 0xFFFFFFFF,
                use_amp["vco_1"][n] & 0xFFFFFFFF,
                use_amp["vco_2"][n] & 0xFFFFFFFF,
                use_amp["noise"][n] & 0xFFFFFFFF,
            )
        )
    (workdir / ("run%d_streams.txt" % run)).write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def mix_simulate(workdir: Path, simulator: str, runs: int, dut_sv: Path,
                 max_n: int = None) -> list:
    """Compile and run the file-driven tb; return per-run captures."""

    (workdir / "runs.txt").write_text("%d\n" % runs, encoding="utf-8")
    vvp = _compile_iverilog(
        workdir, simulator, "audio_mix.vvp",
        [_core.CONSTANTS_PKG_SV, dut_sv, MIX_TB_SV],
    )
    command = ["vvp", "-n", str(vvp)]
    if max_n is not None:
        command.append("+max_n=%d" % max_n)
    _run(command, cwd=workdir)
    captures = []
    for run in range(runs):
        rows = {
            "vco_1.post_vca": [],
            "vco_2.post_vca": [],
            "noise.post_vca": [],
            "mixer.pre_normalization": [],
            "abs": [],
            "peak": [],
        }
        order = list(rows)
        for line in (
            workdir / ("run%d_captured.txt" % run)
        ).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            fields = line.split()
            if len(fields) != len(order):
                raise SystemExit(
                    "captured row %r does not carry %d words"
                    % (line, len(order))
                )
            for name, raw in zip(order, fields):
                try:
                    rows[name].append(int(raw))
                except ValueError:
                    rows[name].append(None)  # an x-state emission: a mismatch
        cycles = int(
            (workdir / ("run%d_cycles.txt" % run))
            .read_text(encoding="utf-8").strip()
        )
        ops = None
        for line in (
            workdir / ("run%d_ops.txt" % run)
        ).read_text(encoding="utf-8").splitlines():
            if line.strip():
                ops = []
                for raw in line.split()[1:]:
                    try:
                        ops.append(int(raw))
                    except ValueError:
                        ops.append(None)  # an x-state counter: a mismatch
        capture = dict(rows)
        capture["cycles"] = cycles
        capture["ops"] = ops
        captures.append(capture)
    return captures


def mix_expected_streams(truth: dict) -> dict:
    """The mirror's captured-stream expectations, keyed like the capture."""

    streams = truth["streams"]
    return {
        "vco_1.post_vca": streams["post_vca"]["vco_1"],
        "vco_2.post_vca": streams["post_vca"]["vco_2"],
        "noise.post_vca": streams["post_vca"]["noise"],
        "mixer.pre_normalization": streams["mix"],
        "abs": streams["abs"],
        "peak": streams["peak"],
    }


def mix_mismatch_rows(capture: dict, truth: dict, prefix: bool = False) -> list:
    """Every (stream, index, expected, actual) first-mismatch row.

    With ``prefix`` (the capped mutation walks) a shorter capture is not
    itself a mismatch, but any differing sample inside the captured
    prefix is.
    """

    expected = mix_expected_streams(truth)
    rows = []
    for name, want in expected.items():
        got = capture[name]
        if prefix:
            want = want[: len(got)]
        for index in range(max(len(got), len(want))):
            a = got[index] if index < len(got) else None
            b = want[index] if index < len(want) else None
            if a != b:
                rows.append((name, index, b, a))
                break
    return rows


def mix_check_case(capture: dict, truth: dict, case_id: str,
                   prefix: bool = False) -> bool:
    """Sample-exact verification of all six captured streams for one run."""

    rows = mix_mismatch_rows(capture, truth, prefix=prefix)
    for name, index, want, got in rows:
        print(
            "AUDIO-MIX FAILED: %s differs (%s[%d]: expected %s got %s)"
            % (name, case_id, index, want, got)
        )
    return not rows


def mix_expected_ops(walked: int, truth: dict) -> list:
    """Expected exported counters for one full-or-capped walk.

    Static per-sample owner-row ops (DR-0010 #76: 6 mults + 2 adds + 4
    narrow sites); the C7 saturation and C6 rounding totals are the
    mirror's measured per-site counts; the peak compare is one per sample
    (charged to #77's row, exported here); Q6.42 accumulator-band faults
    must be zero.
    """

    aux = truth["aux"]
    return [
        6 * walked,
        2 * walked,
        4 * walked,
        aux["sats"],
        aux["rounds"],
        walked,
        0,
    ]


def check_mix_budget(schedule, walked: int, truth: dict, ops_row: list) -> bool:
    """Op-count conformance to the DR-0010 #76 owner row + emission check.

    DR-0010's owner row for #76: "audio rate: 6 mults + 2 adds + 4 narrow
    sites; 48-bit-class accumulator; S4 rescale to the pre-normalization
    mix word", split in the module table as "3x audio VCA: 3 / 0 / 3" plus
    "Mixer (level mults, accumulate, S4): 3 / 2 / 1". The engine declares
    exactly those counts per audio sample. The peak-feed compare is
    DR-0010's #77 row ("pass 1 peak tracking: 1 compare + 1 abs-select
    folded into the mixer output") and is reported against that row, never
    counted in this lane's adds. The complete clip schedule is asserted:
    the walked sample count must equal the emitted SCHED_SAMPLES_PER_PASS
    over one full pass. The serialized single-MAC cycle mapping remains
    the integration lanes; this is an op-count check, not a PPA/fit claim.
    """

    ok = True
    per_sample = {
        "multiply-class ops (3 VCA + 3 level)": (6, 6),
        "accumulator adds": (2, 2),
        "declared narrowings (3 VCA S4 + 1 mixer S4)": (4, 4),
    }
    for name, (actual, limit) in per_sample.items():
        verdict = "OK" if actual <= limit else "FAIL"
        ok = ok and actual <= limit
        print(
            "  budget: %d %s per sample vs DR-0010 #76 owner-row cap %d -> %s"
            % (actual, name, limit, verdict)
        )
    print(
        "  budget: 1 peak-feed compare per sample -> reported against the "
        "DR-0010 #77 owner row (pass-1 peak tracking), never folded into "
        "this lane's adds"
    )
    print(
        "  accumulator: three 24x24 -> 48-bit products summed exactly in "
        "%s (no intermediate rounding before the declared narrowing); "
        "observed band [%d, %d]"
        % (truth["aux"]["product_format"], truth["aux"]["acc_min"],
           truth["aux"]["acc_max"])
    )
    try:
        matches = _core._emitted_constants_match(schedule)
        ok = ok and matches
    except Exception as error:  # noqa: BLE001 - reported, never a silent pass
        print("  budget constants: emission failed -> %s" % error)
        return False
    samples_per_pass = sched.constant(schedule, "samples_per_pass")
    complete = (walked == samples_per_pass == gv.CANONICAL_SAMPLE_COUNT)
    print(
        "  complete clip schedule: walked %d samples == emitted "
        "samples_per_pass %d == canonical %d -> %s"
        % (walked, samples_per_pass, gv.CANONICAL_SAMPLE_COUNT,
           "OK" if complete else "FAIL")
    )
    ok = ok and complete
    expected = mix_expected_ops(walked, truth)
    counters_ok = ops_row == expected
    print(
        "  exported counters over the walk: %r vs expected %r -> %s"
        % (ops_row, expected, "OK" if counters_ok else "FAIL")
    )
    return ok and counters_ok


def mix_report_mutation(label: str, kind: str, case_id: str, rows: list,
                        ops_bad: bool, ops_row, expected_traces: set) -> bool:
    """Require one planted fault DETECTED, localized to the declared traces."""

    hit = {name for name, _i, _w, _g in rows}
    detected = bool(rows) or ops_bad
    localized = hit == expected_traces
    surface = []
    if rows:
        surface.append(
            "trace rows %s" % ",".join(
                "%s[%d]" % (name, index) for name, index, _w, _g in rows
            )
        )
    if ops_bad:
        surface.append("property rows (counters %r)" % (ops_row,))
    print(
        "mutation %s (%s, case %s): %s via %s; localization %s "
        "(expected %s)"
        % (
            label, kind, case_id,
            "DETECTED (test fails the mutant)" if detected
            else "NOT DETECTED",
            " + ".join(surface) if surface else "neither",
            "OK" if localized else "FAIL",
            ",".join(sorted(expected_traces)) if expected_traces else "(none)",
        )
    )
    return detected and localized


def mix_run_rtl_mutation(workdir: Path, simulator: str, label: str,
                         case: dict, truth: dict, expected_traces: set) -> bool:
    """Plant one anchored RTL mutation on one case; require DETECTED."""

    anchor, replacement = MIX_RTL_MUTATIONS[label]
    mut_dir = workdir / ("mut-" + label)
    mut_dir.mkdir(parents=True, exist_ok=True)
    mutated = mutate_sv(
        MIX_DUT_SV.read_text(encoding="utf-8"), anchor, replacement, label
    )
    (mut_dir / "audio_mix_engine_mut.sv").write_text(mutated, encoding="utf-8")
    mix_write_case(mut_dir, 0, case)
    captures = mix_simulate(
        mut_dir, simulator, 1, mut_dir / "audio_mix_engine_mut.sv",
        max_n=truth["aux"]["samples"],
    )
    capture = captures[0]
    rows = mix_mismatch_rows(capture, truth, prefix=True)
    walked = len(capture["mixer.pre_normalization"])
    ops_bad = capture["ops"] != mix_expected_ops(walked, truth)
    return mix_report_mutation(
        label, "RTL", case["id"], rows, ops_bad, capture["ops"],
        expected_traces,
    )


def mix_run_stimulus_mutation(workdir: Path, simulator: str, label: str,
                              case: dict, truth: dict, expected_traces: set,
                              levels: dict = None, amp: dict = None) -> bool:
    """Feed one mutated stimulus through the pristine RTL; require DETECTED."""

    mut_dir = workdir / ("mut-" + label)
    mut_dir.mkdir(parents=True, exist_ok=True)
    mix_write_case(mut_dir, 0, case, levels=levels, amp=amp)
    captures = mix_simulate(
        mut_dir, simulator, 1, MIX_DUT_SV, max_n=truth["aux"]["samples"]
    )
    capture = captures[0]
    rows = mix_mismatch_rows(capture, truth, prefix=True)
    walked = len(capture["mixer.pre_normalization"])
    ops_bad = capture["ops"] != mix_expected_ops(walked, truth)
    return mix_report_mutation(
        label, "stimulus", case["id"], rows, ops_bad, capture["ops"],
        expected_traces,
    )


def mix(workdir: Path, simulator: str) -> int:
    """Issue #76 flow: the audio VCA + mixer engine vs the frozen model."""

    try:
        receipt, formats, cases = mix_load_receipt()
    except ChoiceNotAccepted as error:
        print("AUDIO-MIX REFUSED: accepted register refused: %s" % error)
        return 1
    try:
        schedule = sched.require_accepted_schedule()
    except ScheduleNotAccepted as error:
        print(
            "AUDIO-MIX REFUSED: DR-0010 schedule register refused: %s" % error
        )
        return 1
    manifest, fixtures = mix_load_fixtures()

    print(
        "Loaded the frozen whole-voice receipt (%d cases, %d "
        "param-committed), bindings verified (DR-0008 %s, lut %s); plus %d "
        "declared directed fixtures to run and %d curve-evidence patches "
        "from %s (identity %s)"
        % (len(receipt["cases"]), len(cases),
           receipt["bindings"]["dr_0008_status"],
           receipt["bindings"]["lut_sha256"][:12] + "...",
           len(MIX_FIXTURE_CASES),
           len(set(MIX_CURVE_EVIDENCE.values()) - set(MIX_FIXTURE_CASES)),
           dr.MANIFEST_PATH.name,
           manifest["identity"]["sha256"][:12] + "...")
    )

    ok = True
    derived = {}

    # 1. Sample-exact engine runs over the full 176,400-sample clip: every
    #    param-committed receipt case (frozen-pinned) plus the declared
    #    silence / near-silence / all-source-stress fixtures. The exported
    #    op-counter property row must equal the model-measured counts on
    #    every clean run (an x-state counter is a named failure, never a
    #    silent pass).
    plan = [(case_id, cases[case_id]) for case_id in sorted(cases)]
    plan += [(case_id, None) for case_id in MIX_FIXTURE_CASES]
    for case_id, frozen in plan:
        case_dir = workdir / ("case-" + case_id)
        case_dir.mkdir(parents=True, exist_ok=True)
        if frozen is None:
            physical = fixtures[case_id]["physical"]
            normalized = fixtures[case_id]["normalized"]
        else:
            physical = frozen["parameters"]
            normalized = None
        case = mix_derive_case(
            formats, case_id, physical, normalized, frozen
        )
        truth = {"streams": case["streams"], "aux": case["aux"]}
        mix_write_case(case_dir, 0, case)
        captures = mix_simulate(case_dir, simulator, 1, MIX_DUT_SV)
        case_ok = mix_check_case(captures[0], truth, case_id)
        expected_ops = mix_expected_ops(case["samples"], truth)
        if captures[0]["ops"] != expected_ops:
            case_ok = False
            print(
                "AUDIO-MIX FAILED: exported counters %r != expected %r (%s)"
                % (captures[0]["ops"], expected_ops, case_id)
            )
        if not case_ok:
            ok = False
        case["ops"] = captures[0]["ops"]
        aux = case["aux"]
        print(
            "case %s: %d samples, levels %s (frozen %s), sats %d, rounds "
            "%d, peak %d@%d, RTL sample-exact -> %s"
            % (case_id, case["samples"],
               [case["levels"][lane] for lane in mx.LANES],
               case["frozen"], aux["sats"], aux["rounds"], aux["peak_word"],
               aux["peak_index"], "OK" if case_ok else "FAIL")
        )
        derived[case_id] = case

    # 2. The declared fixture classes, named against the cases that carry
    #    them (an unnamed class is never reported as covered).
    silence = derived["special:silence"]
    fixture_rows = [
        ("silence", "special:silence",
         all(word == 0 for word in silence["levels"].values())
         and all(word == 0 for word in silence["streams"]["mix"])
         and silence["aux"]["peak_word"] == 0),
        ("near-silence", "special:near-silence",
         derived["special:near-silence"]["levels"]["vco_1"] > 0
         and derived["special:near-silence"]["aux"]["peak_word"] > 0),
        ("single-source", ",".join(MIX_SINGLE_SOURCE_CASES),
         all(
             sum(1 for lane in mx.LANES if derived[cid]["levels"][lane] != 0) == 1
             for cid in MIX_SINGLE_SOURCE_CASES
         )),
        ("all-source", "special:stress",
         all(
             derived["special:stress"]["levels"][lane] != 0
             for lane in mx.LANES
         )),
        ("high-depth", ",".join(MIX_HIGH_DEPTH_CASES + ("special:stress",)),
         all(derived[cid]["aux"]["rounds"] > 0
             for cid in MIX_HIGH_DEPTH_CASES)),
        ("stress (mixer C7 saturation exercised)",
         "%s,special:stress" % MIX_STRESS_CASE,
         derived["special:stress"]["aux"]["sites"]["mixer.S4"]["saturation"] > 0),
    ]
    for label, carriers, holds in fixture_rows:
        print(
            "fixture class %s: carried by %s -> %s"
            % (label, carriers, "OK" if holds else "FAIL")
        )
        ok = ok and holds

    # 2b. The mixer input curves: the noise lane's recorded upstream curve
    #     is observable in the declared fixtures' own physical/normalized
    #     pair, and it enters the engine through the same S1 entry site as
    #     the oscillator gains (never as an RTL gain law).
    curve_ok = True
    for lane in mx.LANES:
        curve = mx.MIXER_INPUT_CURVES[lane]
        evidence_id = MIX_CURVE_EVIDENCE[lane]
        observed = mx.observed_curve_exponent(
            fixtures[evidence_id]["physical"],
            fixtures[evidence_id]["normalized"],
            lane,
        )
        expected = 1.0 / curve
        holds = observed is not None and abs(observed - expected) <= 1e-6 * expected
        print(
            "  mixer input curve %s: recorded curve %r (exponent %g), "
            "observed exponent %r from %s -> %s"
            % (lane, curve, expected, observed, evidence_id,
               "OK" if holds else "FAIL")
        )
        curve_ok = curve_ok and holds
        if evidence_id in cases:
            name = "mixer." + lane
            bound = (
                fixtures[evidence_id]["physical"][name]
                == cases[evidence_id]["parameters"][name]
            )
            print(
                "    binding: manifest %s physical == the receipt case's own "
                "committed parameter -> %s"
                % (name, "OK" if bound else "FAIL")
            )
            curve_ok = curve_ok and bound
    ok = ok and curve_ok

    # 3. Reset/replay: a second trigger cannot retain prior lane state.
    first, second = MIX_REPLAY_PAIR
    replay_dir = workdir / "replay"
    replay_dir.mkdir(parents=True, exist_ok=True)
    mix_write_case(replay_dir, 0, derived[first])
    mix_write_case(replay_dir, 1, derived[second])
    replay_captures = mix_simulate(replay_dir, simulator, 2, MIX_DUT_SV)
    solo_dir = workdir / "solo"
    solo_dir.mkdir(parents=True, exist_ok=True)
    mix_write_case(solo_dir, 0, derived[second])
    solo_captures = mix_simulate(solo_dir, simulator, 1, MIX_DUT_SV)
    replay_ok = True
    for name in list(MIX_STREAM_TRACES) + ["abs", "peak"]:
        if replay_captures[1][name] != solo_captures[0][name]:
            print(
                "AUDIO-MIX FAILED: run-after-run %s differs from the solo "
                "run - prior lane state leaked" % name
            )
            replay_ok = False
    if replay_captures[1]["ops"] != solo_captures[0]["ops"]:
        print(
            "AUDIO-MIX FAILED: run-after-run counters %r != solo %r - "
            "counters are not per-trigger"
            % (replay_captures[1]["ops"], solo_captures[0]["ops"])
        )
        replay_ok = False
    second_truth = {
        "streams": derived[second]["streams"],
        "aux": derived[second]["aux"],
    }
    replay_ok = replay_ok and mix_check_case(
        replay_captures[1], second_truth, second
    )
    print(
        "reset/replay: trigger-to-trigger back-to-back runs (%s -> %s) "
        "reproduce the solo golden run -> %s"
        % (first, second, "OK" if replay_ok else "FAIL")
    )
    ok = ok and replay_ok

    # 4. Budget: owner-row op counts + emission + complete clip schedule.
    budget_case = derived["source:vco_1"]
    ok = ok and check_mix_budget(
        schedule,
        budget_case["samples"],
        {"streams": budget_case["streams"], "aux": budget_case["aux"]},
        budget_case["ops"],
    )

    # 5. Mutations: each planted fault MUST be detected AND localized.
    cap = MIX_MUTATION_WALK_CAP
    prefix_truth = {
        case_id: mix_prefix_truth(formats, derived[case_id], cap)
        for case_id in (
            "source:vco_1", "source:vco_2", "source:noise",
            "special:silence", "special:stress",
        )
    }
    mutations_ok = True
    mix_only = {"mixer.pre_normalization", "abs", "peak"}

    # 5a. Route: the mixer level words swapped (the only nonzero level
    #     reaches the wrong lane). The VCA outputs are untouched.
    mutations_ok = mix_run_rtl_mutation(
        workdir, simulator, "level-route-swap",
        derived["source:vco_1"], prefix_truth["source:vco_1"], mix_only,
    ) and mutations_ok

    # 5b. Polarity: the vco_2 VCA output negated. A pure sign inversion is
    #     INVISIBLE to the magnitude feed (|mix| and the running peak are
    #     sign-blind, and this case saturates nowhere, so the negated mix
    #     has identical magnitudes), so it must be caught on the signed
    #     lane word and the signed mix word — exactly the localization
    #     asserted here. A lane that only checked the peak feed would miss
    #     it.
    mutations_ok = mix_run_rtl_mutation(
        workdir, simulator, "vca-2-polarity",
        derived["source:vco_2"], prefix_truth["source:vco_2"],
        {"vco_2.post_vca", "mixer.pre_normalization"},
    ) and mutations_ok

    # 5c. DC: a constant offset on the mix word, caught on the silence
    #     fixture where the true mix is identically zero.
    mutations_ok = mix_run_rtl_mutation(
        workdir, simulator, "mixer-dc-offset",
        derived["special:silence"], prefix_truth["special:silence"], mix_only,
    ) and mutations_ok

    # 5d. Rounding: truncation instead of half-even at the mixer narrowing.
    mutations_ok = mix_run_rtl_mutation(
        workdir, simulator, "mixer-truncate",
        derived["source:vco_1"], prefix_truth["source:vco_1"], mix_only,
    ) and mutations_ok

    # 5e. Saturation: C7 saturation dropped, caught on the stress fixture
    #     whose accumulator genuinely exceeds the C1 band.
    mutations_ok = mix_run_rtl_mutation(
        workdir, simulator, "mixer-wrap",
        derived["special:stress"], prefix_truth["special:stress"], mix_only,
    ) and mutations_ok

    # 5f. Route: the noise term dropped from the accumulator, caught on the
    #     noise-only case.
    mutations_ok = mix_run_rtl_mutation(
        workdir, simulator, "dropped-noise-source",
        derived["source:noise"], prefix_truth["source:noise"], mix_only,
    ) and mutations_ok

    # 5g. Gain: the recorded noise input curve dropped — the case's own
    #     NORMALIZED noise level fed as though the lane were linear
    #     (curve 1.0) instead of its recorded power-40 conversion.
    #     Stimulus-side: the curve lives at the measured-observation seam,
    #     never in RTL.
    noise_case = derived["source:noise"]
    counters = StickyCounters()
    dropped_curve = dict(noise_case["levels"])
    dropped_curve["noise"] = entry_quantize(
        float(fixtures["source:noise"]["normalized"]["mixer.noise"]),
        formats.audio, counters, "tb.mix.curve_dropped",
    )
    if dropped_curve["noise"] == noise_case["levels"]["noise"]:
        raise SystemExit(
            "the curve-dropped noise level word equals the committed one; "
            "this mutation would prove nothing"
        )
    mutations_ok = mix_run_stimulus_mutation(
        workdir, simulator, "noise-curve-dropped", noise_case,
        prefix_truth["source:noise"], mix_only, levels=dropped_curve,
    ) and mutations_ok

    # 5h. Gain: a one-ULP noise level word error (the gain is load-bearing
    #     at ULP resolution, not merely at audible scale).
    one_ulp = dict(noise_case["levels"])
    one_ulp["noise"] = one_ulp["noise"] + 1
    mutations_ok = mix_run_stimulus_mutation(
        workdir, simulator, "noise-gain-one-ulp", noise_case,
        prefix_truth["source:noise"], mix_only, levels=one_ulp,
    ) and mutations_ok

    # 5i. Route: the vco_1 / vco_2 amplitude columns swapped upstream (the
    #     #72 lane's routes crossed). Both VCA outputs move; the mix
    #     follows.
    vco1_case = derived["source:vco_1"]
    swapped_amp = dict(vco1_case["amp"])
    swapped_amp["vco_1"], swapped_amp["vco_2"] = (
        vco1_case["amp"]["vco_2"], vco1_case["amp"]["vco_1"]
    )
    mutations_ok = mix_run_stimulus_mutation(
        workdir, simulator, "amp-route-swap", vco1_case,
        prefix_truth["source:vco_1"],
        {"vco_1.post_vca", "vco_2.post_vca"} | mix_only,
        amp=swapped_amp,
    ) and mutations_ok

    ok = ok and mutations_ok

    if not ok:
        print("AUDIO-MIX RUN FAILED")
        return 1
    print(
        "AUDIO-MIX RUN PASSED (frozen-binding + %d receipt cases and %d "
        "declared fixtures sample-exact over the complete clip + "
        "reset/replay independence + owner-row/op-count asserts + all 9 "
        "mutations detected and localized)"
        % (len(cases), len(MIX_FIXTURE_CASES))
    )
    return 0
