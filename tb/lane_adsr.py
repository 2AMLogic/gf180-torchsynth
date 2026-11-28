"""ADSR lane of the gf180 testbench harness (issue #70, split out by #476).

``tb/run_tb.py`` re-exports selected names such as ``ADSR_DUT_SV`` and
calls :func:`bind_core` right after defining the shared symbols this lane
needs (``CONSTANTS_PKG_SV``, ``_emitted_constants_match``), so the lane reads
them from the one live ``run_tb`` module (including when it runs as
``__main__``) instead of copying them or importing a second copy.
"""

from __future__ import annotations

import sys
import tempfile
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
)
from torchsynth_voice import adsr_golden as ag  # noqa: E402
from torchsynth_voice import golden_vectors as gv  # noqa: E402
from torchsynth_voice.fixed_voice import AcceptedFormats  # noqa: E402
from torchsynth_voice.fixedpoint import schedule as sched  # noqa: E402
from torchsynth_voice.fixedpoint.choices import ChoiceNotAccepted  # noqa: E402
from torchsynth_voice.fixedpoint.counters import StickyCounters  # noqa: E402
from torchsynth_voice.fixedpoint.schedule import ScheduleNotAccepted  # noqa: E402
from torchsynth_voice.fixedpoint.rounding import RoundingMode  # noqa: E402
from torchsynth_voice.format_sweep import FixedControlPath  # noqa: E402

_core = None


def bind_core(core) -> None:
    """Bind the ``run_tb`` module that owns the shared core symbols."""

    global _core
    _core = core


ADSR_DUT_SV = TB_ROOT / "sv/adsr_engine.sv"
ADSR_TB_SV = TB_ROOT / "sv/tb_adsr_engine.sv"

#: Committed ADSR golden vectors (issue #70).
ADSR_VECTOR_DIR = ROOT / "sim/reference/adsr-golden-v1"
#: The case mutations are demonstrated on (default-envelope sustain 0.75).
ADSR_MUTATION_CASE = "frozen-envelope-receipt"
#: Replay-independence pair: envelope state from run 0 must not leak into run 1.
ADSR_REPLAY_PAIR = ("frozen-envelope-receipt", "boundary-tie")


def adsr_load_vectors():
    """Load and contract-verify every committed ADSR golden vector."""

    return load_golden_vectors(ADSR_VECTOR_DIR, "ADSR")


def adsr_derive_case(fcp, formats, vector):
    """Model-derived stimulus for one case: formations + shadow streams.

    Every value comes from the model's own code (``adsr_golden`` calls the
    frozen composition's control path); the shadow rows are asserted equal
    to ``FixedControlPath._ramp`` inside :func:`ag.mirror_ramp`.
    """

    counters = StickyCounters()
    words = ag.quantize_entries(vector["parameters"], formats.midi, formats.mode, counters)
    eps60 = ag.eps60_word(formats.mode)
    cases = {}
    for prefix in ag.ADSR_PREFIXES:
        formation = ag.derive_formation(fcp, words, prefix)
        shadow = {}
        value_rows = {}
        for stage in ("attack", "decay", "release"):
            start_q = (
                formation["attack_q"] if stage == "decay"
                else (formation["duration_q"] if stage == "release" else 0)
            )
            values, shape_words = ag.mirror_ramp(
                fcp,
                formation[stage + "_q"],
                formation[stage + "_exact"],
                start_q,
                stage != "attack",
                formation["alpha"],
            )
            shadow[stage] = shape_words
            value_rows[stage] = values
        golden = fcp._adsr(words, prefix)
        committed = None
        for trace in vector["traces"]:
            if trace["name"] == ag.trace_name(prefix):
                committed = trace["values"]
                break
        if committed is None:
            raise SystemExit("vector %r carries no %s trace" % (vector, prefix))
        if committed != golden:
            raise SystemExit(
                "committed vector trace %s no longer matches the live model; "
                "regenerate the vectors" % ag.trace_name(prefix)
            )
        # The combine-over-rows replication must reproduce _adsr exactly
        # when fed the model's own rows (used only by the mutated-curve
        # vector below, but proven on the clean case).
        combined = ag.combine_from_shadow(
            fcp, shadow["attack"], shadow["decay"], shadow["release"],
            formation["sustain_q"],
        )
        if combined != golden:
            raise SystemExit(
                "combine-from-shadow replication drifted from _adsr on %s" % prefix
            )
        cases[prefix] = {
            "formation": formation,
            "shadow": shadow,
            "value_rows": value_rows,
            "golden": golden,
            "sustain_entry": words[prefix + "sustain"],
        }
    return cases


def _word_halves(word: int):
    return word & 0xFFFFFFFF, word >> 32


def adsr_write_case(workdir: Path, run: int, cases: dict, linear_curve: bool = False):
    """Write one run's stimulus files (params + per-instance shadows)."""

    prefixes = list(cases)
    params = []
    for prefix in prefixes:
        f = cases[prefix]["formation"]
        ints = []
        for name in ("duration_q", "attack_q", "decay_q", "release_q"):
            lo, hi = _word_halves(int(f[name]))
            ints.extend((lo, hi))
        ints.extend(
            (
                1 if f["duration_zero"] else 0,
                1 if f["attack_zero"] else 0,
                1 if f["decay_zero"] else 0,
                1 if f["release_zero"] else 0,
            )
        )
        ints.append(int(cases[prefix]["sustain_entry"]))
        lo, hi = _word_halves(int(ag.eps60_word(RoundingMode.HALF_EVEN)))
        ints.extend((lo, hi))
        params.append(ints)
    (workdir / ("run%d_params.txt" % run)).write_text(
        "".join(" ".join(str(v) for v in row) + "\n" for row in params),
        encoding="utf-8",
    )
    for index, prefix in enumerate(prefixes):
        lines = []
        for tick in range(gv.CANONICAL_CONTROL_COUNT):
            if linear_curve:
                row = [
                    cases[prefix]["value_rows"][stage][tick] >> 30
                    for stage in ("attack", "decay", "release")
                ]
            else:
                row = [
                    cases[prefix]["shadow"][stage][tick]
                    for stage in ("attack", "decay", "release")
                ]
            lines.append("%d %d %d" % tuple(row))
        (workdir / ("run%d_shadow%d.txt" % (run, index))).write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
    return prefixes


def adsr_simulate(workdir: Path, simulator: str, runs: int, dut_sv: Path) -> list:
    """Compile and run the six-instance tb; return per-run captured streams."""

    (workdir / "runs.txt").write_text("%d\n" % runs, encoding="utf-8")
    vvp = _compile_iverilog(
        workdir, simulator, "adsr_engine.vvp",
        [_core.CONSTANTS_PKG_SV, dut_sv, ADSR_TB_SV],
    )
    _run(["vvp", "-n", str(vvp)], cwd=workdir)
    captures = []
    for run in range(runs):
        streams = []
        for index in range(6):
            path = workdir / ("run%d_captured%d.txt" % (run, index))
            streams.append(
                [
                    int(line)
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            )
        captures.append(streams)
    return captures


def check_adsr_budget(schedule) -> bool:
    """Op-count conformance to the DR-0010 #70 owner row + emission check.

    DR-0010's owner row for #70: "control rate: <= ~30 multiply-class ops +
    <= 18 declared ``**alpha`` shadow sites per tick across all 6
    envelopes" (spec/decision-records/0010-one-shot-rtl-microarchitecture.md,
    module table). The engine declares 4 multiply-class ops + 2 declared
    narrowings + 3 shadow words + 3 ramp divisions per instance-tick, so
    the six-instance totals are 24 / 12 / 18 / 18 per control tick. The tb
    counts them as exported sticky counters and they are hard-asserted
    here. The serialized single-MAC cycle mapping remains the integration
    lanes; this is an op-count check, not a PPA/fit claim.
    """

    per_tick = {
        "multiply-class ops": (24, 30),
        "declared narrowings": (12, 30),
        "`**alpha` shadow words": (18, 18),
        "ramp divisions (declared extra)": (18, None),
    }
    return check_owner_row_budget(
        schedule, per_tick, "DR-0010", _core._emitted_constants_match
    )


def adsr_detects(captures_run, cases, vector):
    """True iff the captured streams mismatch the vector's expectations."""

    captured_by_trace = {
        ag.trace_name(prefix): captures_run[index]
        for index, prefix in enumerate(cases)
    }
    return gv.first_mismatch(vector, captured_by_trace) is not None


def adsr(workdir: Path, simulator: str) -> int:
    """Issue #70 flow: the ADSR engine vs the frozen fixed model's vectors."""

    try:
        formats = AcceptedFormats()
    except ChoiceNotAccepted as error:
        print("ADSR REFUSED: accepted register refused: %s" % error)
        return 1
    try:
        schedule = sched.require_accepted_schedule()
    except ScheduleNotAccepted as error:
        print("ADSR REFUSED: DR-0010 schedule register refused: %s" % error)
        return 1
    fcp = FixedControlPath(formats.control_spec)

    vectors = adsr_load_vectors()
    print(
        "Loaded %d ADSR golden vectors (%s), contract bindings verified"
        % (len(vectors), ", ".join(sorted(vectors)))
    )

    ok = True

    # 1. Sample-exact engine runs, one committed case per invocation.
    case_dirs = {}
    for case_id in sorted(vectors):
        case_dir = workdir / ("case-" + case_id)
        case_dir.mkdir(parents=True, exist_ok=True)
        cases = adsr_derive_case(fcp, formats, vectors[case_id])
        adsr_write_case(case_dir, 0, cases)
        captures = adsr_simulate(case_dir, simulator, 1, ADSR_DUT_SV)
        prefixes = list(cases)
        captured_by_trace = {
            ag.trace_name(prefix): captures[0][index]
            for index, prefix in enumerate(prefixes)
        }
        mismatch = gv.first_mismatch(vectors[case_id], captured_by_trace)
        if mismatch is not None:
            print(
                "ADSR FAILED: RTL disagrees with the golden vector (%s):"
                % case_id
            )
            print(gv.format_mismatch(mismatch))
            ok = False
        print(
            "case %s: %d instances x %d control samples, RTL sample-exact "
            "-> %s" % (case_id, len(prefixes), gv.CANONICAL_CONTROL_COUNT,
                       "OK" if ok else "FAIL")
        )
        case_dirs[case_id] = (case_dir, cases)

    # 2. Reset/replay: a second trigger cannot retain prior envelope state.
    first, second = ADSR_REPLAY_PAIR
    replay_dir = workdir / "replay"
    replay_dir.mkdir(parents=True, exist_ok=True)
    cases_first = adsr_derive_case(fcp, formats, vectors[first])
    cases_second = adsr_derive_case(fcp, formats, vectors[second])
    adsr_write_case(replay_dir, 0, cases_first)
    adsr_write_case(replay_dir, 1, cases_second)
    replay_captures = adsr_simulate(replay_dir, simulator, 2, ADSR_DUT_SV)
    solo_dir = workdir / "solo"
    solo_dir.mkdir(parents=True, exist_ok=True)
    adsr_write_case(solo_dir, 0, cases_second)
    solo_captures = adsr_simulate(solo_dir, simulator, 1, ADSR_DUT_SV)
    replay_ok = True
    second_prefixes = list(cases_second)
    for index in range(6):
        if replay_captures[1][index] != solo_captures[0][index]:
            print(
                "ADSR FAILED: run-after-run capture differs from the solo "
                "run (instance %d) - prior envelope state leaked" % index
            )
            replay_ok = False
    mismatch = gv.first_mismatch(
        vectors[second],
        {
            ag.trace_name(prefix): replay_captures[1][index]
            for index, prefix in enumerate(second_prefixes)
        },
    )
    if mismatch is not None:
        print("ADSR FAILED: replay run disagrees with the golden vector:")
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
    expected_totals = [4 * 1764, 2 * 1764, 3 * 1764, 3 * 1764]
    counters_ok = all(row == expected_totals for row in ops)
    print(
        "budget: %d instance-runs of exported counters -> %s "
        "(per instance-run %s, per tick x6: 24 mult / 12 narrow / 18 shadow)"
        % (len(ops), "OK" if counters_ok else "FAIL", expected_totals)
    )
    ok = ok and counters_ok and check_adsr_budget(schedule)

    # 4. Mutations: each planted fault MUST be detected (AC-6).
    mutations_ok = True

    # 4a. Wrong sustain level (RTL): sustain entry negated.
    mut_dir = workdir / "mut-sustain"
    mut_dir.mkdir(parents=True, exist_ok=True)
    mutated = mutate_sv(
        ADSR_DUT_SV.read_text(encoding="utf-8"),
        "wire signed [C4_WIDTH-1:0] sustain_eff = sustain_entry;",
        "wire signed [C4_WIDTH-1:0] sustain_eff = -sustain_entry;",
        "wrong-sustain-level",
    )
    (mut_dir / "adsr_engine_mut.sv").write_text(mutated, encoding="utf-8")
    adsr_write_case(mut_dir, 0, case_dirs[ADSR_MUTATION_CASE][1])
    mut_captures = adsr_simulate(
        mut_dir, simulator, 1, mut_dir / "adsr_engine_mut.sv"
    )
    detected = adsr_detects(
        mut_captures[0],
        case_dirs[ADSR_MUTATION_CASE][1],
        vectors[ADSR_MUTATION_CASE],
    )
    print(
        "mutation wrong-sustain-level (RTL): %s"
        % ("DETECTED (test fails the mutant)" if detected else "NOT DETECTED")
    )
    mutations_ok = mutations_ok and detected

    # 4b. Linear-vs-exponential curve confusion (vector side): regenerate
    # the shadow stream with the linearized power and compare the true RTL
    # against the corrupted expectation.
    lin_dir = workdir / "mut-linear"
    lin_dir.mkdir(parents=True, exist_ok=True)
    adsr_write_case(lin_dir, 0, case_dirs[ADSR_MUTATION_CASE][1], linear_curve=True)
    lin_captures = adsr_simulate(lin_dir, simulator, 1, ADSR_DUT_SV)
    detected = adsr_detects(
        lin_captures[0],
        case_dirs[ADSR_MUTATION_CASE][1],
        vectors[ADSR_MUTATION_CASE],
    )
    print(
        "mutation linear-vs-exp curve (vector side): %s"
        % ("DETECTED (vectors discriminate the curve)" if detected else "NOT DETECTED")
    )
    mutations_ok = mutations_ok and detected

    # 4c. Off-by-one timing (RTL): first trigger tick lands on index 1.
    mut_dir = workdir / "mut-timing"
    mut_dir.mkdir(parents=True, exist_ok=True)
    mutated = mutate_sv(
        ADSR_DUT_SV.read_text(encoding="utf-8"),
        "                index   <= {IDX_BITS{1'b0}};",
        "                index   <= 11'd1;",
        "off-by-one-timing",
    )
    (mut_dir / "adsr_engine_mut.sv").write_text(mutated, encoding="utf-8")
    adsr_write_case(mut_dir, 0, case_dirs[ADSR_MUTATION_CASE][1])
    mut_captures = adsr_simulate(
        mut_dir, simulator, 1, mut_dir / "adsr_engine_mut.sv"
    )
    detected = adsr_detects(
        mut_captures[0],
        case_dirs[ADSR_MUTATION_CASE][1],
        vectors[ADSR_MUTATION_CASE],
    )
    print(
        "mutation off-by-one timing (RTL): %s"
        % ("DETECTED (test fails the mutant)" if detected else "NOT DETECTED")
    )
    mutations_ok = mutations_ok and detected
    ok = ok and mutations_ok

    if not ok:
        print("ADSR RUN FAILED")
        return 1
    print(
        "ADSR RUN PASSED (contract binding + %d cases sample-exact + "
        "reset/replay independence + budget/op-count asserts + all "
        "mutations detected)" % len(vectors)
    )
    return 0
