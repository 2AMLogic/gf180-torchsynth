"""LFO lane of the gf180 testbench harness (issue #71, split out by #561).

``tb/run_tb.py`` re-exports selected names such as ``LFO_DUT_SV`` and
calls :func:`bind_core` right after defining the shared symbols this lane
needs (``CONSTANTS_PKG_SV``, ``_emitted_constants_match``), so the lane reads
them from the one live ``run_tb`` module (including when it runs as
``__main__``) instead of copying them or importing a second copy.
"""

from __future__ import annotations

import sys
from pathlib import Path

TB_ROOT = Path(__file__).resolve().parent
ROOT = TB_ROOT.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(TB_ROOT))

from tb_harness import (  # noqa: E402
    _compile_iverilog,
    _run,
    check_owner_row_budget,
    load_golden_vectors,
    mutate_sv,
    read_ops_rows,
    run_rtl_mutation,
    write_lut_memh,
)
from torchsynth_voice import adsr_golden as ag  # noqa: E402
from torchsynth_voice import golden_vectors as gv  # noqa: E402
from torchsynth_voice import lfo_golden as lgo  # noqa: E402
from torchsynth_voice.fixed_voice import AcceptedFormats  # noqa: E402
from torchsynth_voice.fixedpoint import schedule as sched  # noqa: E402
from torchsynth_voice.fixedpoint.choices import ChoiceNotAccepted  # noqa: E402
from torchsynth_voice.fixedpoint.counters import StickyCounters  # noqa: E402
from torchsynth_voice.fixedpoint.schedule import ScheduleNotAccepted  # noqa: E402
from torchsynth_voice.format_sweep import FixedControlPath  # noqa: E402

_core = None


def bind_core(core) -> None:
    """Bind the ``run_tb`` module that owns the shared core symbols."""

    global _core
    _core = core


LFO_DUT_SV = TB_ROOT / "sv/lfo_vca_engine.sv"
LFO_TB_SV = TB_ROOT / "sv/tb_lfo_vca_engine.sv"

#: Committed LFO + control-VCA golden vectors (issue #71).
LFO_VECTOR_DIR = ROOT / "sim/reference/lfo-vca-golden-v1"
#: Cases the RTL mutations are demonstrated on: the default-envelope
#: receipt (blend/shape/depth), a non-zero first-increment wrap case
#: (phase), and the negative-rate zero-clamp case (clamp removal).
LFO_MUTATION_CASE = "frozen-lfo-receipt"
LFO_PHASE_MUTATION_CASE = "frequency-phase-extremes"
LFO_CLAMP_MUTATION_CASE = "depth-negative-clamp"
#: Replay-independence pair: LFO phase/weight state from run 0 must not
#: leak into run 1.
LFO_REPLAY_PAIR = ("frozen-lfo-receipt", "shape-single-sweep")


def lfo_load_vectors():
    """Load and contract-verify every committed LFO/VCA golden vector."""

    return load_golden_vectors(LFO_VECTOR_DIR, "LFO")


def lfo_derive_case(fcp, formats, vector):
    """Model-derived stimulus for one case: envelopes, weights, words.

    Every value comes from the model's own code: the rate/amp envelopes
    are the frozen control path's ``_adsr`` outputs (the #70 engines'
    declared stream outputs, consumed here as inputs per the declared
    amplitude-envelope interface), the weight words replay the declared
    binary64 shadow via :func:`lgo.weight_shadow`, the initial-turn word
    is the declared S3 formation via :func:`lgo.init_word`, and
    :func:`lgo.mirror_lfo`/:func:`lgo.mirror_vca` assert the integer
    replication equals the model's own ``_lfo``/``_control_vca`` rows.
    The committed vector traces must equal the live model's outputs.
    """

    counters = StickyCounters()
    words = ag.quantize_entries(vector["parameters"], formats.midi, formats.mode, counters)
    cases = {}
    for side in lgo.LFO_SIDES:
        rate_env = fcp._adsr(words, side[:-1] + "_rate_adsr.")
        amp_env = fcp._adsr(words, side[:-1] + "_amp_adsr.")
        weight_q = lgo.weight_shadow(fcp, words, side)
        raw, clamps = lgo.mirror_lfo(fcp, words, side, rate_env, weight_q)
        post = lgo.mirror_vca(fcp, raw, amp_env)
        for trace_name, live in (
            (lgo.raw_trace(side), raw),
            (lgo.vca_trace(side), post),
        ):
            committed = None
            for trace in vector["traces"]:
                if trace["name"] == trace_name:
                    committed = trace["values"]
                    break
            if committed is None:
                raise SystemExit(
                    "vector carries no %s trace" % trace_name
                )
            if committed != live:
                raise SystemExit(
                    "committed vector trace %s no longer matches the live "
                    "model; regenerate the vectors" % trace_name
                )
        cases[side] = {
            "freq": words[side + "frequency"],
            "depth": words[side + "mod_depth"],
            "init": lgo.init_word(fcp, words, side),
            "weights": weight_q,
            "rate_env": rate_env,
            "gain": amp_env,
            "raw": raw,
            "post": post,
            "clamps": clamps,
        }
    return cases


def _word32(value: int) -> int:
    """A signed word's 32-bit two's-complement decimal (tb bit pattern)."""

    return value & 0xFFFFFFFF


def lfo_write_case(workdir: Path, run: int, cases: dict):
    """Write one run's stimulus files (params + per-instance streams)."""

    sides = list(cases)
    params = []
    for side in sides:
        case = cases[side]
        params.append(
            [
                _word32(int(case["freq"])),
                _word32(int(case["depth"])),
                _word32(int(case["init"])),
            ]
            + [_word32(int(w)) for w in case["weights"]]
        )
    (workdir / ("run%d_params.txt" % run)).write_text(
        "".join(" ".join(str(v) for v in row) + "\n" for row in params),
        encoding="utf-8",
    )
    for index, side in enumerate(sides):
        case = cases[side]
        lines = []
        for tick in range(gv.CANONICAL_CONTROL_COUNT):
            lines.append(
                "%d %d" % (case["rate_env"][tick], case["gain"][tick])
            )
        (workdir / ("run%d_streams%d.txt" % (run, index))).write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
    return sides


def lfo_simulate(workdir: Path, simulator: str, runs: int, dut_sv: Path) -> list:
    """Compile and run the two-instance tb; return per-run captures."""

    (workdir / "runs.txt").write_text("%d\n" % runs, encoding="utf-8")
    vvp = _compile_iverilog(
        workdir, simulator, "lfo_vca_engine.vvp",
        [_core.CONSTANTS_PKG_SV, dut_sv, LFO_TB_SV],
    )
    # Bare name + cwd=workdir: the DUT's lut_file buffer holds 128
    # characters, so an absolute path can truncate (#278).
    _run(["vvp", "-n", str(vvp), "+lut=lut.memh"], cwd=workdir)
    captures = []
    for run in range(runs):
        streams = []
        for index in range(2):
            pairs = []
            for line in (
                workdir / ("run%d_captured%d.txt" % (run, index))
            ).read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                raw_word, post_word = line.split()
                pairs.append((int(raw_word), int(post_word)))
            streams.append(pairs)
        captures.append(streams)
    return captures


def check_lfo_budget(schedule) -> bool:
    """Op-count conformance to the DR-0010 #71 owner row + emission check.

    DR-0010's owner row for #71: "control rate: <= ~14 multiply-class ops
    + 2 C5-class LUT interps + 2 phase steps per tick" (both LFOs and
    both control VCAs). The engine declares 6 multiply-class ops (5 shape
    products + 1 VCA) + 10 declared narrowings (rate, increment, LUT
    interp, sin/saw/tri/sqr, blend, raw, post) + 1 phase step per
    instance-tick, so the two-instance totals are 12 / 20 / 2 per control
    tick. The tb counts them as exported sticky counters and they are
    hard-asserted here. The serialized single-MAC cycle mapping remains
    the integration lanes; this is an op-count check, not a PPA/fit
    claim.
    """

    per_tick = {
        "multiply-class ops": (12, 14),
        "declared narrowings": (20, None),
        "C5-class LUT interps": (2, 2),
        "phase steps": (2, 2),
    }
    return check_owner_row_budget(
        schedule, per_tick, "DR-0010 #71", _core._emitted_constants_match
    )


def lfo_detects(captures_run, cases, vector):
    """True iff the captured streams mismatch the vector's expectations."""

    captured_by_trace = {}
    for index, side in enumerate(cases):
        captured_by_trace[lgo.raw_trace(side)] = [raw for raw, _ in captures_run[index]]
        captured_by_trace[lgo.vca_trace(side)] = [post for _, post in captures_run[index]]
    return gv.first_mismatch(vector, captured_by_trace) is not None


def lfo_run_mutation(
    workdir: Path,
    simulator: str,
    label: str,
    anchor: str,
    replacement: str,
    case_id: str,
    vectors: dict,
    case_dirs: dict,
    dut_sv: Path = None,
):
    """Plant one RTL mutation on one case and require it to be DETECTED."""

    def write_mutant(mut_dir):
        source = (dut_sv or LFO_DUT_SV).read_text(encoding="utf-8")
        mutated = mutate_sv(source, anchor, replacement, label)
        (mut_dir / "lfo_vca_engine_mut.sv").write_text(mutated, encoding="utf-8")
        (mut_dir / "lut.memh").write_bytes(
            (case_dirs[case_id][0] / "lut.memh").read_bytes()
        )

    def write_case(mut_dir):
        lfo_write_case(mut_dir, 0, case_dirs[case_id][1])

    def simulate(mut_dir):
        return lfo_simulate(
            mut_dir, simulator, 1, mut_dir / "lfo_vca_engine_mut.sv"
        )[0]

    def is_detected(capture):
        return lfo_detects(capture, case_dirs[case_id][1], vectors[case_id]), ""

    return run_rtl_mutation(
        workdir, label, case_id, write_mutant, write_case, simulate, is_detected
    )


def lfo(workdir: Path, simulator: str) -> int:
    """Issue #71 flow: the LFO + control-VCA engine vs the frozen vectors."""

    try:
        formats = AcceptedFormats()
    except ChoiceNotAccepted as error:
        print("LFO REFUSED: accepted register refused: %s" % error)
        return 1
    try:
        schedule = sched.require_accepted_schedule()
    except ScheduleNotAccepted as error:
        print("LFO REFUSED: DR-0010 schedule register refused: %s" % error)
        return 1
    fcp = FixedControlPath(formats.control_spec)

    vectors = lfo_load_vectors()
    print(
        "Loaded %d LFO golden vectors (%s), contract bindings verified"
        % (len(vectors), ", ".join(sorted(vectors)))
    )

    lut_memh = workdir / "lut.memh"
    # The LFO's table is the frozen control path's own C5-shape sweep table
    # (format_sweep.LUT_ENTRY_FORMAT Q1.23, 25-bit words), NOT the accepted
    # C5 audio-path table (formats.table, 24-bit) the anchor flow loads.
    write_lut_memh(fcp.table, lut_memh)

    ok = True

    # 1. Sample-exact engine runs, one committed case per invocation.
    case_dirs = {}
    for case_id in sorted(vectors):
        case_dir = workdir / ("case-" + case_id)
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "lut.memh").write_bytes(lut_memh.read_bytes())
        cases = lfo_derive_case(fcp, formats, vectors[case_id])
        lfo_write_case(case_dir, 0, cases)
        captures = lfo_simulate(case_dir, simulator, 1, LFO_DUT_SV)
        sides = list(cases)
        mismatch = gv.first_mismatch(
            vectors[case_id],
            {
                lgo.raw_trace(side): [raw for raw, _ in captures[0][index]]
                for index, side in enumerate(sides)
            }
            | {
                lgo.vca_trace(side): [post for _, post in captures[0][index]]
                for index, side in enumerate(sides)
            },
        )
        if mismatch is not None:
            print(
                "LFO FAILED: RTL disagrees with the golden vector (%s):"
                % case_id
            )
            print(gv.format_mismatch(mismatch))
            ok = False
        clamp_total = sum(cases[side]["clamps"] for side in sides)
        print(
            "case %s: %d instances x %d control samples (rate clamps %d), "
            "RTL sample-exact -> %s"
            % (case_id, len(sides), gv.CANONICAL_CONTROL_COUNT, clamp_total,
               "OK" if ok else "FAIL")
        )
        case_dirs[case_id] = (case_dir, cases)

    # 2. Reset/replay: a second trigger cannot retain prior LFO state
    #    (phase accumulators and weights are per-trigger).
    first, second = LFO_REPLAY_PAIR
    replay_dir = workdir / "replay"
    replay_dir.mkdir(parents=True, exist_ok=True)
    (replay_dir / "lut.memh").write_bytes(lut_memh.read_bytes())
    cases_first = lfo_derive_case(fcp, formats, vectors[first])
    cases_second = lfo_derive_case(fcp, formats, vectors[second])
    lfo_write_case(replay_dir, 0, cases_first)
    lfo_write_case(replay_dir, 1, cases_second)
    replay_captures = lfo_simulate(replay_dir, simulator, 2, LFO_DUT_SV)
    solo_dir = workdir / "solo"
    solo_dir.mkdir(parents=True, exist_ok=True)
    (solo_dir / "lut.memh").write_bytes(lut_memh.read_bytes())
    lfo_write_case(solo_dir, 0, cases_second)
    solo_captures = lfo_simulate(solo_dir, simulator, 1, LFO_DUT_SV)
    replay_ok = True
    for index in range(2):
        if replay_captures[1][index] != solo_captures[0][index]:
            print(
                "LFO FAILED: run-after-run capture differs from the solo "
                "run (instance %d) - prior LFO state leaked" % index
            )
            replay_ok = False
    second_sides = list(cases_second)
    mismatch = gv.first_mismatch(
        vectors[second],
        {
            lgo.raw_trace(side): [raw for raw, _ in replay_captures[1][index]]
            for index, side in enumerate(second_sides)
        }
        | {
            lgo.vca_trace(side): [post for _, post in replay_captures[1][index]]
            for index, side in enumerate(second_sides)
        },
    )
    if mismatch is not None:
        print("LFO FAILED: replay run disagrees with the golden vector:")
        print(gv.format_mismatch(mismatch))
        replay_ok = False
    print(
        "reset/replay: trigger-to-trigger back-to-back runs (%s -> %s) "
        "reproduce the solo golden run -> %s"
        % (first, second, "OK" if replay_ok else "FAIL")
    )
    ok = ok and replay_ok

    # 3. Budget: exported op counters per instance-run + emission check.
    ops = read_ops_rows(replay_dir, 2)
    expected_static = [6 * 1764, 10 * 1764, 1764]
    counters_ok = True
    for run_index, run_cases in enumerate((cases_first, cases_second)):
        for inst_index, side in enumerate(run_cases):
            row = ops[run_index * 2 + inst_index]
            expected = expected_static + [run_cases[side]["clamps"]]
            if row != expected:
                print(
                    "LFO FAILED: exported counters %r != expected %r "
                    "(run %d instance %d)" % (row, expected, run_index, inst_index)
                )
                counters_ok = False
    print(
        "budget: %d instance-runs of exported counters -> %s "
        "(per instance-run %s + measured clamps; per tick x2: "
        "12 mult / 20 narrow / 2 LUT interp / 2 phase steps)"
        % (len(ops), "OK" if counters_ok else "FAIL", expected_static)
    )
    ok = ok and counters_ok and check_lfo_budget(schedule)

    # 4. Mutations: each planted fault MUST be detected (AC-6).
    mutations_ok = True

    # 4a. Selector instead of blend (RTL): the continuous shape-weight
    #     merge becomes a discrete argmax one-hot.
    mutations_ok &= lfo_run_mutation(
        workdir, simulator, "selector-instead-of-blend",
        "    wire signed [95:0] merged =\n"
        "        $signed(w_eff0) * $signed(sin_q30)\n"
        "      + $signed(w_eff1) * $signed(tri_q30)\n"
        "      + $signed(w_eff2) * $signed(saw_q30)\n"
        "      + $signed(w_eff3) * $signed(rsaw_q30)\n"
        "      + $signed(w_eff4) * $signed(sqr_q30);",
        "    wire signed [95:0] merged =\n"
        "        (w_eff0 >= w_eff1 && w_eff0 >= w_eff2 && w_eff0 >= w_eff3 && w_eff0 >= w_eff4)"
        " ? $signed(w_eff0) * $signed(sin_q30)\n"
        "        : (w_eff1 >= w_eff2 && w_eff1 >= w_eff3 && w_eff1 >= w_eff4)"
        " ? $signed(w_eff1) * $signed(tri_q30)\n"
        "        : (w_eff2 >= w_eff3 && w_eff2 >= w_eff4)"
        " ? $signed(w_eff2) * $signed(saw_q30)\n"
        "        : (w_eff3 >= w_eff4) ? $signed(w_eff3) * $signed(rsaw_q30)\n"
        "        : $signed(w_eff4) * $signed(sqr_q30);",
        LFO_MUTATION_CASE, vectors, case_dirs,
    )

    # 4b. Wrong shape table (RTL): the sine table's quadrant negation is
    #     dropped, corrupting the C5 content for half the circle.
    mutations_ok &= lfo_run_mutation(
        workdir, simulator, "wrong-shape-table",
        "    wire signed [24:0] entry_word = negate ? -lut_word : lut_word;",
        "    wire signed [24:0] entry_word = lut_word;",
        LFO_MUTATION_CASE, vectors, case_dirs,
    )

    # 4c. Depth modulation dropped (RTL): the rate becomes the bare
    #     frequency word; the envelope never modulates.
    mutations_ok &= lfo_run_mutation(
        workdir, simulator, "depth-modulation-dropped",
        "    wire signed [95:0] rate_numer = $signed(freq_r) * (96'sd1 << 21) + depth_term;",
        "    wire signed [95:0] rate_numer = $signed(freq_r) * (96'sd1 << 21);",
        LFO_MUTATION_CASE, vectors, case_dirs,
    )

    # 4d. Phase first-increment skipped (RTL): sample zero loses the
    #     leading increment the model accumulates before the initial
    #     phase; the whole trace shifts by one increment.
    mutations_ok &= lfo_run_mutation(
        workdir, simulator, "phase-first-increment-skipped",
        "                phase <= phase_next;",
        "                phase <= (index == 11'd0) ? phase : phase_next;",
        LFO_PHASE_MUTATION_CASE, vectors, case_dirs,
    )

    # 4e. Rate clamp removed (RTL): negative modulated rates enter the
    #     increment site instead of clamping at zero. Only meaningful on
    #     a case whose host mirror counted actual clamps.
    clamp_cases = case_dirs[LFO_CLAMP_MUTATION_CASE][1]
    clamp_total = sum(clamp_cases[side]["clamps"] for side in clamp_cases)
    if clamp_total <= 0:
        print(
            "LFO FAILED: %s carries no rate clamps; the clamp mutation "
            "would be vacuous" % LFO_CLAMP_MUTATION_CASE
        )
        mutations_ok = False
    else:
        mutations_ok &= lfo_run_mutation(
            workdir, simulator, "rate-clamp-removed",
            "    wire [46:0]        rate_eff = rate_clamped ? 47'd0 : rate_signed;",
            "    wire [46:0]        rate_eff = rate_signed;",
            LFO_CLAMP_MUTATION_CASE, vectors, case_dirs,
        )
    ok = ok and mutations_ok

    if not ok:
        print("LFO RUN FAILED")
        return 1
    print(
        "LFO RUN PASSED (contract binding + %d cases sample-exact + "
        "reset/replay independence + budget/op-count asserts + all "
        "mutations detected)" % len(vectors)
    )
    return 0
