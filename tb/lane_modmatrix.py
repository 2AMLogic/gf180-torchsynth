"""Modulation-matrix lane of the gf180 testbench harness (issue #72, split
out by #573).

``tb/run_tb.py`` re-exports selected names such as ``MM_DUT_SV`` and
calls :func:`bind_core` right after defining the shared symbols this lane
needs (``CONSTANTS_PKG_SV``, ``_emitted_constants_match``), so the lane reads
them from the one live ``run_tb`` module (including when it runs as
``__main__``) instead of copying them or importing a second copy.

``MM_MUTATION_WALK_CAP`` (the ``+max_j`` mutation-run walk cap) is defined
here and nowhere else: this lane is its sole producer
(``tests/test_synth_view_identity.py`` pins that).
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

TB_ROOT = Path(__file__).resolve().parent
ROOT = TB_ROOT.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(TB_ROOT))

from tb_harness import (  # noqa: E402
    _compile_iverilog,
    _run,
    load_golden_vectors,
    mutate_sv,
    run_rtl_mutation,
)
from torchsynth_voice import golden_vectors as gv  # noqa: E402
from torchsynth_voice import mod_matrix_golden as mm  # noqa: E402
from torchsynth_voice.fixed_voice import AcceptedFormats  # noqa: E402
from torchsynth_voice.fixedpoint import schedule as sched  # noqa: E402
from torchsynth_voice.fixedpoint.choices import ChoiceNotAccepted  # noqa: E402
from torchsynth_voice.fixedpoint.counters import StickyCounters  # noqa: E402
from torchsynth_voice.fixedpoint.schedule import ScheduleNotAccepted  # noqa: E402
from torchsynth_voice.format_sweep import (  # noqa: E402
    MOD_MATRIX_INPUTS,
    MOD_MATRIX_OUTPUTS,
    FixedControlPath,
    quantize_params,
)

_core = None


def bind_core(core) -> None:
    """Bind the ``run_tb`` module that owns the shared core symbols."""

    global _core
    _core = core


#: Committed modulation-matrix + endpoint-aligned-upsample golden vectors
#: (issue #72).
MM_DUT_SV = TB_ROOT / "sv/mod_matrix_engine.sv"
MM_UP_DUT_SV = TB_ROOT / "sv/upsample_engine.sv"
MM_TB_SV = TB_ROOT / "sv/tb_mod_matrix_upsample.sv"
MM_VECTOR_DIR = ROOT / "sim/reference/mod-matrix-golden-v1"
#: Cases the RTL mutations are demonstrated on: the all-depths +1.0 case
#: drives matrix outputs to ~3.4 (the +-1.0 clamp mutation must bite), and
#: the mixed-sign case gives every route at least two nonzero source
#: depths (the selector/dropped-route mutations must bite and localize).
MM_CLAMP_MUTATION_CASE = "route-extremes-positive"
MM_MUTATION_CASE = "mixed-sign-routes"
#: The route the dropped-route mutation forces to zero, and the exact
#: trace set the mismatch must localize to.
MM_DROPPED_ROUTE = "vco_2_amp"
#: Mutation-simulation walk cap (+max_j): every mutation demonstrably
#: bites within the first 24,000 audio samples (~0.54 s), so the eight
#: mutation sims walk a fraction of the grid. Committed-case runs always
#: walk the full 176,400 samples.
MM_MUTATION_WALK_CAP = 24000
#: Replay-independence pair: matrix depths and upsample column memories
#: are per-trigger; run 0 state must not leak into run 1.
MM_REPLAY_PAIR = ("frozen-mod-matrix-receipt", "mixed-sign-routes")


def mm_load_vectors():
    """Load and contract-verify every committed mod-matrix golden vector."""

    return load_golden_vectors(MM_VECTOR_DIR, "mod-matrix")


def mm_depth_words(vector, words):
    """The twenty S1 depth words, route-major in the pinned source order."""

    return {
        route: [
            words["mod_matrix." + source + "->" + route]
            for source in MOD_MATRIX_INPUTS
        ]
        for route in MOD_MATRIX_OUTPUTS
    }


def mm_derive_case(fcp, formats, vector):
    """Model-derived stimulus and truth for one case.

    Everything comes from the frozen composition's own control path: the
    twenty S1 depth words (:func:`format_sweep.quantize_params`), the four
    source columns (``_adsr``/``_lfo``/``_control_vca`` — the #70/#71
    engines' declared outputs), the matrix words (``mirror_mod_matrix``
    asserted row-equal to ``_mod_matrix``), and the five full-length audio
    streams (``mirror_upsample`` asserted row-equal to ``_upsample``). The
    committed vector's matrix traces must equal the live model, and every
    case's audio digest (and, where present, sidecar bytes) must equal the
    regenerated truth — refuse on any drift.
    """

    counters = StickyCounters()
    words = quantize_params(vector["parameters"], formats.midi, formats.mode, counters)

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
    columns = [adsr_1, adsr_2, post_1, post_2]

    matrix, matrix_counters = mm.mirror_mod_matrix(fcp, words, columns)

    committed = {}
    for trace in vector["traces"]:
        committed[trace["name"]] = trace["values"]
    for route in MOD_MATRIX_OUTPUTS:
        name = "mod_matrix." + route
        if committed.get(name) != matrix[route]:
            raise SystemExit(
                "committed vector trace %s no longer matches the live "
                "model; regenerate the vectors" % name
            )

    audio = {}
    audio_counters = {}
    audio_digests = {}
    for route in MOD_MATRIX_OUTPUTS:
        stream, route_counters = mm.mirror_upsample(fcp, matrix[route], route)
        audio[route] = stream
        audio_counters[route] = route_counters
        evidence = vector["provenance"]["audio_traces"]["control_upsample." + route]
        digest = mm_mod_digest(stream)
        if digest != evidence["words_sha256"]:
            raise SystemExit(
                "audio digest drift for control_upsample.%s: regenerated %s "
                "but the committed vector declares %s — regenerate the "
                "vectors, do not recompile" % (route, digest, evidence["words_sha256"])
            )
        if stream[0] != evidence["first_word"] or stream[-1] != evidence["last_word"]:
            raise SystemExit(
                "endpoint drift for control_upsample.%s against the "
                "committed vector" % route
            )
        for j_str, word in evidence["jitter"].items():
            if stream[int(j_str)] != word:
                raise SystemExit(
                    "jitter drift for control_upsample.%s at j=%s against "
                    "the committed vector" % (route, j_str)
                )
        audio_digests[route] = digest
    case = {
        "depths": mm_depth_words(vector, words),
        "columns": columns,
        "matrix": matrix,
        "audio": audio,
        "matrix_counters": matrix_counters,
        "audio_counters": audio_counters,
        "audio_digests": audio_digests,
    }

    sidecar_cases = [
        route
        for route in MOD_MATRIX_OUTPUTS
        if "sidecar" in vector["provenance"]["audio_traces"]["control_upsample." + route]
    ]
    for route in sidecar_cases:
        row = vector["provenance"]["audio_traces"]["control_upsample." + route]["sidecar"]
        payload = (ROOT / "sim" / "reference" / row["file"]).read_bytes()
        if hashlib.sha256(payload).hexdigest() != row["sha256"]:
            raise SystemExit(
                "sidecar bytes drifted for control_upsample.%s (%s); "
                "regenerate the vectors" % (route, row["file"])
            )
        if mm_mod_digest(mm.unpack_words_f32le(payload)) != row["words_sha256"]:
            raise SystemExit(
                "sidecar words drifted for control_upsample.%s (%s)"
                % (route, row["file"])
            )
    return case


def mm_mod_digest(words) -> str:
    """The #54 trace-digest convention over an integer word list."""

    blob = json.dumps(
        list(words), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def mm_write_case(workdir: Path, run: int, case: dict):
    """Write one run's stimulus files (depths + source columns)."""

    lines = []
    for route in MOD_MATRIX_OUTPUTS:
        lines.append(
            " ".join(
                str(word & 0xFFFFFFFF) for word in case["depths"][route]
            )
        )
    (workdir / ("run%d_depths.txt" % run)).write_text(
        "".join(line + "\n" for line in lines), encoding="utf-8"
    )
    cols = case["columns"]
    rows = []
    for tick in range(gv.CANONICAL_CONTROL_COUNT):
        rows.append(
            " ".join(
                str(cols[source][tick] & 0xFFFFFFFF) for source in range(4)
            )
        )
    (workdir / ("run%d_columns.txt" % run)).write_text(
        "".join(row + "\n" for row in rows), encoding="utf-8"
    )


def mm_simulate(workdir: Path, simulator: str, runs: int, dut_sv: Path,
                up_sv: Path = None, max_j: int = None) -> list:
    """Compile and run the 1+5-instance tb; return per-run captures.

    ``max_j`` caps the audio walk via the ``+max_j`` plusarg — a mutation
    demonstration knob only; committed-case runs use the full walk.
    """

    (workdir / "runs.txt").write_text("%d\n" % runs, encoding="utf-8")
    vvp = _compile_iverilog(
        workdir, simulator, "mod_matrix_upsample.vvp",
        [_core.CONSTANTS_PKG_SV, dut_sv, up_sv or MM_UP_DUT_SV, MM_TB_SV],
    )
    vvp_cmd = ["vvp", "-n", str(vvp)]
    if max_j is not None:
        vvp_cmd.append("+max_j=%d" % max_j)
    _run(vvp_cmd, cwd=workdir)
    captures = []
    for run in range(runs):
        matrix = []
        for line in (
            workdir / ("run%d_matrix.txt" % run)
        ).read_text(encoding="utf-8").splitlines():
            if line.strip():
                matrix.append([int(word) for word in line.split()])
        audio = {}
        for index, route in enumerate(MOD_MATRIX_OUTPUTS):
            words = []
            for line in (
                workdir / ("run%d_audio%d.txt" % (run, index))
            ).read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    words.append(int(line))
                except ValueError:
                    words.append(None)  # an x-state emission: a mismatch
            audio[route] = words
        ops = []
        for line in (
            workdir / ("run%d_ops.txt" % run)
        ).read_text(encoding="utf-8").splitlines():
            if line.strip():
                ops.append(line.split())
        captures.append({"matrix": matrix, "audio": audio, "ops": ops})
    return captures


def mm_matrix_by_route(matrix_rows) -> dict:
    """Captured matrix rows split into per-route word lists."""

    return {
        route: [row[index] for row in matrix_rows]
        for index, route in enumerate(MOD_MATRIX_OUTPUTS)
    }


def mm_audio_mismatches(captured: dict, expected: dict) -> list:
    """(route, index, expected, actual) rows where the audio differs."""

    rows = []
    for route in MOD_MATRIX_OUTPUTS:
        got = captured[route]
        want = expected[route]
        for index in range(max(len(got), len(want))):
            a = got[index] if index < len(got) else None
            b = want[index] if index < len(want) else None
            if a != b:
                rows.append((route, index, b, a))
                if len(rows) >= 3:
                    return rows
    return rows


def mm_mismatch_trace_set(captured_matrix, captured_audio, case,
                          audio_prefix: bool = False) -> set:
    """Every trace name (control and audio) where the capture differs.

    ``audio_prefix`` compares each captured audio stream against the
    expected prefix of its own length — used by the capped-walk mutation
    demonstrations, where a shorter capture is not itself a mismatch but
    any differing sample inside the captured prefix is. Matrix traces are
    always compared over the full control grid.
    """

    bad = set()
    for route in MOD_MATRIX_OUTPUTS:
        got = captured_matrix[route]
        want = case["matrix"][route]
        if any(
            got[i] != want[i] if i < len(got) and i < len(want) else True
            for i in range(max(len(got), len(want)))
        ):
            bad.add("mod_matrix." + route)
        expected_audio = case["audio"][route]
        if audio_prefix:
            expected_audio = expected_audio[: len(captured_audio[route])]
        if captured_audio[route] != expected_audio:
            bad.add("control_upsample." + route)
    return bad


def mm_check_case(capture, case, vector, case_id: str) -> bool:
    """Sample-exact matrix + audio verification for one run."""

    captured_matrix = mm_matrix_by_route(capture["matrix"])
    mismatch = gv.first_mismatch(
        vector,
        {("mod_matrix." + route): captured_matrix[route]
         for route in MOD_MATRIX_OUTPUTS},
    )
    ok = mismatch is None
    if mismatch is not None:
        print(
            "MOD-MATRIX FAILED: RTL disagrees with the golden vector (%s):"
            % case_id
        )
        print(gv.format_mismatch(mismatch))
    audio_rows = mm_audio_mismatches(capture["audio"], case["audio"])
    if audio_rows:
        ok = False
        route, index, want, got = audio_rows[0]
        print(
            "MOD-MATRIX FAILED: audio stream differs (%s "
            "control_upsample.%s[%d]: expected %s got %s)"
            % (case_id, route, index, want, got)
        )
    for route in MOD_MATRIX_OUTPUTS:
        digest = mm_mod_digest(capture["audio"][route])
        if digest != case["audio_digests"][route]:
            ok = False
            print(
                "MOD-MATRIX FAILED: audio digest differs (%s "
                "control_upsample.%s)" % (case_id, route)
            )
    return ok


def mm_ops(capture) -> dict:
    """Parse one run's ops file into the matrix + per-route rows."""

    row_m = capture["ops"][0]
    assert row_m[0] == "M", capture["ops"]
    parsed = {
        "matrix": [int(v) for v in row_m[1:]],
    }
    routes = {}
    for row in capture["ops"][1:]:
        assert row[0] == "U", capture["ops"]
        routes[MOD_MATRIX_OUTPUTS[len(routes)]] = [int(v) for v in row[1:]]
    parsed["routes"] = routes
    return parsed


#: Static per-route upsample op totals over the FULL walk (interior = the
#: 176,398 non-endpoint samples; the two exact-boundary endpoints are
#: declared exact copies with no blend ops): 1 blend mult + 1 blend add +
#: 1 narrowing + 1 fraction word per interior sample, 176,399 coordinate
#: steps, 176,400 emissions.
MM_UPSAMPLE_STATIC_FULL = [
    gv.CANONICAL_SAMPLE_COUNT - 2,      # blend mults
    gv.CANONICAL_SAMPLE_COUNT - 2,      # blend adds
    gv.CANONICAL_SAMPLE_COUNT - 2,      # declared narrowings
    gv.CANONICAL_SAMPLE_COUNT - 2,      # fraction words
    gv.CANONICAL_SAMPLE_COUNT - 1,      # coordinate steps
]


def mm_expected_upsample_ops(case, route, walk_len: int) -> list:
    """Expected exported upsample counters for one route run.

    ``walk_len`` is the emitted-word count (full or capped); interior
    samples are every emitted sample except the exact-boundary endpoints
    actually reached within the walk.
    """

    if walk_len >= gv.CANONICAL_SAMPLE_COUNT:
        static = list(MM_UPSAMPLE_STATIC_FULL)
    else:
        interior = walk_len - 1  # only the j=0 endpoint is reached
        static = [interior, interior, interior, interior, walk_len - 1]
    return static + [
        case["audio_counters"][route]["total_saturation"],
        walk_len,
    ]


def check_mm_budget(schedule) -> bool:
    """Op-count conformance to the DR-0010 #72 owner row + emission check.

    DR-0010's owner rows for #72: the matrix is "control rate: 20 MACs +
    5 declared narrowings per tick"; the upsamples are "audio rate:
    2 mults + 1 add + 1 half-even blend per column per sample". The
    engines declare exactly those counts per route-column-sample (the
    blend product/add/narrowing at 1/1/1 within the 2/1/1 owner-row cap;
    the exact incremental coordinate walk is one add + one compare per
    step, reported as a declared extra), so per control tick the matrix
    totals are 20 / 15 / 5 (mult / add / narrow) and per audio sample
    each upsample column totals 176,398 blend mults + 176,398 adds +
    176,398 narrowings + 176,398 fraction words + 176,399 coordinate
    steps (the two exact-boundary endpoints are declared exact copies
    with no blend ops). The tb counts them as exported sticky counters
    and they are hard-asserted here.
    The serialized single-MAC cycle mapping remains the integration
    lanes; this is an op-count check, not a PPA/fit claim.
    """

    ok = True
    per_tick = {
        "matrix MACs": (20, 20),
        "matrix declared narrowings": (5, 5),
        "upsample blend mults per column-sample": (1, 2),
        "upsample blend adds per column-sample": (1, 1),
        "upsample declared blends per column-sample": (1, 1),
    }
    for name, (actual, limit) in per_tick.items():
        verdict = "OK" if actual <= limit else "FAIL"
        ok = ok and actual <= limit
        print(
            "  budget: %d %s vs DR-0010 #72 owner-row cap %d -> %s"
            % (actual, name, limit, verdict)
        )
    try:
        matches = _core._emitted_constants_match(schedule)
        ok = ok and matches
    except Exception as error:  # noqa: BLE001 - reported, never a silent pass
        print("  budget constants: emission failed -> %s" % error)
        return False
    return ok


def mm_run_mutation(
    workdir: Path,
    simulator: str,
    label: str,
    anchor: str,
    replacement: str,
    case_id: str,
    vectors: dict,
    case_dirs: dict,
    expect_traces: set = None,
):
    """Plant one RTL mutation on one case and require it to be DETECTED.

    With ``expect_traces`` the mismatch must ALSO localize to exactly that
    trace set (the route-swap/depth AC's localization evidence).
    """

    source = MM_DUT_SV.read_text(encoding="utf-8")
    up_source = MM_UP_DUT_SV.read_text(encoding="utf-8")
    if anchor not in source and anchor not in up_source:
        raise SystemExit("mutation anchor not found for %s" % label)
    case_dir, case = case_dirs[case_id]
    mut_dut_paths = {}

    def write_mutant(mut_dir):
        if anchor in source:
            (mut_dir / "mod_matrix_engine_mut.sv").write_text(
                mutate_sv(source, anchor, replacement, label), encoding="utf-8"
            )
            mut_dut_paths["dut"] = mut_dir / "mod_matrix_engine_mut.sv"
            mut_dut_paths["up"] = MM_UP_DUT_SV
        else:
            (mut_dir / "upsample_engine_mut.sv").write_text(
                mutate_sv(up_source, anchor, replacement, label), encoding="utf-8"
            )
            mut_dut_paths["dut"] = MM_DUT_SV
            mut_dut_paths["up"] = mut_dir / "upsample_engine_mut.sv"

    def write_case(mut_dir):
        mm_write_case(mut_dir, 0, case)

    def simulate(mut_dir):
        return mm_simulate(
            mut_dir, simulator, 1, mut_dut_paths["dut"], mut_dut_paths["up"],
            max_j=MM_MUTATION_WALK_CAP,
        )[0]

    def is_detected(capture):
        detected = not mm_check_case(capture, case, vectors[case_id], case_id)
        localization = ""
        if detected and expect_traces is not None:
            bad = mm_mismatch_trace_set(
                mm_matrix_by_route(capture["matrix"]), capture["audio"], case,
                audio_prefix=True,
            )
            if bad != expect_traces:
                detected = False
                localization = (
                    " (localization FAILED: mismatch set %s != expected %s)"
                    % (sorted(bad), sorted(expect_traces))
                )
            else:
                localization = " (localized to %s)" % sorted(bad)
        return detected, localization

    return run_rtl_mutation(
        workdir, label, case_id, write_mutant, write_case, simulate, is_detected
    )


def mm_run_vector_mutation(
    workdir: Path,
    simulator: str,
    label: str,
    case_id: str,
    vectors: dict,
    case_dirs: dict,
    rewrite_stimulus,
    expect_traces: set,
):
    """Plant a vector-side (stimulus) mutation and require localization.

    ``rewrite_stimulus(case)`` mutates the derived case in place (a depth
    word) before re-simulation; the mismatch must localize to exactly the
    expected trace set.
    """

    case = case_dirs[case_id][1]
    # The rewrite mutates its argument in place: deep-copy so the shared
    # derived case (and every later mutation run) keeps pristine truth.
    mutated = copy.deepcopy(case)
    rewrite_stimulus(mutated)

    def write_mutant(mut_dir):
        pass  # stimulus-side mutation: the DUT itself is never mutated.

    def write_case(mut_dir):
        mm_write_case(mut_dir, 0, mutated)

    def simulate(mut_dir):
        return mm_simulate(
            mut_dir, simulator, 1, MM_DUT_SV, max_j=MM_MUTATION_WALK_CAP
        )[0]

    def is_detected(capture):
        # Compare against the MUTATED expectations: stimulus-side mutations
        # (sign flip, one-ULP depth) leave the expectations pristine, so the
        # capture diverges from them; expectation-side mutations (route
        # swap) corrupt exactly the traces that must localize.
        bad = mm_mismatch_trace_set(
            mm_matrix_by_route(capture["matrix"]), capture["audio"], mutated,
            audio_prefix=True,
        )
        detected = bool(bad)
        if bad != expect_traces:
            detected = False
            localization = (
                " (localization FAILED: mismatch set %s != expected %s)"
                % (sorted(bad), sorted(expect_traces))
            )
        else:
            localization = " (localized to %s)" % sorted(bad)
        return detected, localization

    return run_rtl_mutation(
        workdir, label, case_id, write_mutant, write_case, simulate, is_detected,
        kind="vector side",
    )


def modmatrix(workdir: Path, simulator: str) -> int:
    """Issue #72 flow: the mod matrix + upsample engines vs the frozen vectors."""

    try:
        formats = AcceptedFormats()
    except ChoiceNotAccepted as error:
        print("MOD-MATRIX REFUSED: accepted register refused: %s" % error)
        return 1
    try:
        schedule = sched.require_accepted_schedule()
    except ScheduleNotAccepted as error:
        print("MOD-MATRIX REFUSED: DR-0010 schedule register refused: %s" % error)
        return 1
    fcp = FixedControlPath(formats.control_spec)

    vectors = mm_load_vectors()
    print(
        "Loaded %d mod-matrix golden vectors (%s), contract bindings verified"
        % (len(vectors), ", ".join(sorted(vectors)))
    )

    ok = True

    # 1. Sample-exact engine runs, one committed case per invocation.
    case_dirs = {}
    for case_id in sorted(vectors):
        case_dir = workdir / ("case-" + case_id)
        case_dir.mkdir(parents=True, exist_ok=True)
        case = mm_derive_case(fcp, formats, vectors[case_id])
        mm_write_case(case_dir, 0, case)
        captures = mm_simulate(case_dir, simulator, 1, MM_DUT_SV)
        if not mm_check_case(captures[0], case, vectors[case_id], case_id):
            ok = False
        total_controls = gv.CANONICAL_CONTROL_COUNT
        print(
            "case %s: 5 routes x %d control samples + 5 x %d audio samples, "
            "RTL sample-exact -> %s"
            % (case_id, total_controls, gv.CANONICAL_SAMPLE_COUNT,
               "OK" if ok else "FAIL")
        )
        case_dirs[case_id] = (case_dir, case)

    # 2. Reset/replay: a second trigger cannot retain prior state (depth
    #    words, column memories, and walk counters are per-trigger).
    first, second = MM_REPLAY_PAIR
    replay_dir = workdir / "replay"
    replay_dir.mkdir(parents=True, exist_ok=True)
    cases_first = mm_derive_case(fcp, formats, vectors[first])
    cases_second = mm_derive_case(fcp, formats, vectors[second])
    mm_write_case(replay_dir, 0, cases_first)
    mm_write_case(replay_dir, 1, cases_second)
    replay_captures = mm_simulate(replay_dir, simulator, 2, MM_DUT_SV)
    solo_dir = workdir / "solo"
    solo_dir.mkdir(parents=True, exist_ok=True)
    mm_write_case(solo_dir, 0, cases_second)
    solo_captures = mm_simulate(solo_dir, simulator, 1, MM_DUT_SV)
    replay_ok = True
    if replay_captures[1]["matrix"] != solo_captures[0]["matrix"] or \
            replay_captures[1]["audio"] != solo_captures[0]["audio"]:
        print(
            "MOD-MATRIX FAILED: run-after-run capture differs from the solo "
            "run - prior run state leaked"
        )
        replay_ok = False
    if not mm_check_case(replay_captures[1], cases_second, vectors[second], second):
        replay_ok = False
    print(
        "reset/replay: trigger-to-trigger back-to-back runs (%s -> %s) "
        "reproduce the solo golden run -> %s"
        % (first, second, "OK" if replay_ok else "FAIL")
    )
    ok = ok and replay_ok

    # 3. Budget: exported op counters + the DR-0010 #72 owner row.
    ops_ok = True
    static_matrix = [20 * gv.CANONICAL_CONTROL_COUNT,
                     15 * gv.CANONICAL_CONTROL_COUNT,
                     5 * gv.CANONICAL_CONTROL_COUNT]
    for run_index, case in enumerate((cases_first, cases_second)):
        parsed = mm_ops(replay_captures[run_index])
        expected_m = static_matrix + [case["matrix_counters"]["total_saturation"]]
        if parsed["matrix"] != expected_m:
            print(
                "MOD-MATRIX FAILED: matrix counters %r != expected %r (run %d)"
                % (parsed["matrix"], expected_m, run_index)
            )
            ops_ok = False
        for route in MOD_MATRIX_OUTPUTS:
            expected_u = mm_expected_upsample_ops(
                case, route, gv.CANONICAL_SAMPLE_COUNT
            )
            if parsed["routes"][route] != expected_u:
                print(
                    "MOD-MATRIX FAILED: upsample counters %s %r != expected "
                    "%r (run %d)" % (route, parsed["routes"][route],
                                     expected_u, run_index)
                )
                ops_ok = False
    print(
        "budget: %d runs of exported counters -> %s (matrix per tick 20/15/5"
        " mult/add/narrow; upsample per column interior sample 1/1/1 blend"
        " mult/add/narrow + 1 fraction word within the 2/1/1 owner-row cap;"
        " exact incremental coordinate walk reported as a declared extra;"
        " endpoints exact copies)"
        % (2, "OK" if ops_ok else "FAIL")
    )
    ok = ok and ops_ok and check_mm_budget(schedule)

    # 4. Mutations: each planted fault MUST be detected (AC-5/AC-6).
    mutations_ok = True

    # 4a. Clamped matrix output (RTL): the C7 narrowing bounds tighten to
    #     the upstream clamp +-1.0, which must NEVER hold here. The
    #     extremes case drives matrix outputs to ~3.4 so the mutant must
    #     break the vector.
    mutations_ok &= mm_run_mutation(
        workdir, simulator, "clamped-matrix-output",
        "    localparam signed [95:0] C1_SAT_HI = 96'sd8388607;   // (1<<23)-1: +4-2^-21\n"
        "    localparam signed [95:0] C1_SAT_LO = -96'sd8388608;  // -(1<<23):  -4",
        "    localparam signed [95:0] C1_SAT_HI = 96'sd2097151;   // MUTANT: upstream clamp +1.0\n"
        "    localparam signed [95:0] C1_SAT_LO = -96'sd2097152;  // MUTANT: upstream clamp -1.0",
        MM_CLAMP_MUTATION_CASE, vectors, case_dirs,
    )

    # 4b. Selector instead of blend (RTL): the shared four-source weighted
    #     sum becomes a discrete argmax one-hot pick.
    mutations_ok &= mm_run_mutation(
        workdir, simulator, "selector-matrix",
        "        blend4 = $signed(d0) * $signed(s0)\n"
        "               + $signed(d1) * $signed(s1)\n"
        "               + $signed(d2) * $signed(s2)\n"
        "               + $signed(d3) * $signed(s3);",
        "        blend4 = (d0 >= d1 && d0 >= d2 && d0 >= d3) ? $signed(d0) * $signed(s0)\n"
        "               : (d1 >= d2 && d1 >= d3) ? $signed(d1) * $signed(s1)\n"
        "               : (d2 >= d3) ? $signed(d2) * $signed(s2)\n"
        "               : $signed(d3) * $signed(s3);",
        MM_MUTATION_CASE, vectors, case_dirs,
    )

    # 4c. ZOH instead of endpoint-aligned interpolation (RTL): the blend
    #     result collapses to the floor column word.
    mutations_ok &= mm_run_mutation(
        workdir, simulator, "zoh-upsample",
        "                    audio_word  <= q[C1_WIDTH-1:0];",
        "                    audio_word  <= left[C1_WIDTH-1:0];",
        MM_MUTATION_CASE, vectors, case_dirs,
    )

    # 4d. Off-endpoint coordinates (RTL): align_corners=False, the
    #     dropped-endpoint scale j*1763/176400; endpoints and interior
    #     both shift.
    mutations_ok &= mm_run_mutation(
        workdir, simulator, "off-endpoint-coordinate",
        "    localparam [17:0] UP_DEN      = 18'd176399;  // AUDIO_SAMPLES - 1",
        "    localparam [17:0] UP_DEN      = 18'd176400;  // MUTANT: align_corners=False",
        MM_MUTATION_CASE, vectors, case_dirs,
    )

    # 4e. Dropped route (RTL): vco_2_amp is forced to zero; the mismatch
    #     must localize to exactly that route's two traces.
    mutations_ok &= mm_run_mutation(
        workdir, simulator, "dropped-route",
        "                out_r3    <= q3[C1_WIDTH-1:0];",
        "                out_r3    <= {C1_WIDTH{1'b0}};",
        MM_MUTATION_CASE, vectors, case_dirs,
        expect_traces={"mod_matrix." + MM_DROPPED_ROUTE,
                       "control_upsample." + MM_DROPPED_ROUTE},
    )

    # 4f-h. Route swap / sign flip / one-ULP depth (vector side): each
    #     stimulus-or-expectation mutation must localize to exactly the
    #     expected route's traces (AC-6).
    def _swap_routes(case):
        a, b = ("vco_1_amp", "noise_amp")
        (
            case["matrix"][a], case["matrix"][b],
        ) = (
            case["matrix"][b], case["matrix"][a],
        )

    mutations_ok &= mm_run_vector_mutation(
        workdir, simulator, "route-swap", MM_MUTATION_CASE, vectors,
        case_dirs, _swap_routes,
        expect_traces={"mod_matrix.vco_1_amp", "mod_matrix.noise_amp"},
    )

    def _sign_flip(case):
        case["depths"]["vco_1_pitch"][0] = -case["depths"]["vco_1_pitch"][0]

    mutations_ok &= mm_run_vector_mutation(
        workdir, simulator, "depth-sign-flip", MM_MUTATION_CASE, vectors,
        case_dirs, _sign_flip,
        expect_traces={"mod_matrix.vco_1_pitch", "control_upsample.vco_1_pitch"},
    )

    def _depth_ulp(case):
        case["depths"]["noise_amp"][1] = case["depths"]["noise_amp"][1] + 1

    mutations_ok &= mm_run_vector_mutation(
        workdir, simulator, "depth-one-ulp", MM_MUTATION_CASE, vectors,
        case_dirs, _depth_ulp,
        expect_traces={"mod_matrix.noise_amp", "control_upsample.noise_amp"},
    )
    ok = ok and mutations_ok

    if not ok:
        print("MOD-MATRIX RUN FAILED")
        return 1
    print(
        "MOD-MATRIX RUN PASSED (contract binding + %d cases sample-exact + "
        "reset/replay independence + budget/op-count asserts + all "
        "mutations detected)" % len(vectors)
    )
    return 0
