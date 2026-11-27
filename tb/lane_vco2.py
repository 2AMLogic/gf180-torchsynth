"""Square/saw VCO lane of the gf180 testbench harness (issue #74, split
out by #585).

Moved verbatim from ``tb/run_tb.py``, which re-exports every public name here
so ``import run_tb`` / ``from run_tb import VCO2_DUT_SV`` consumers keep
working. ``tb/run_tb.py`` stays the lane registry plus the shared core; it
calls :func:`bind_core` right after defining the shared symbols this lane
needs (``CONSTANTS_PKG_SV``, ``check_clip_budget``), so the lane reads
them from the one live ``run_tb`` module (including when it runs as
``__main__``) instead of copying them or importing a second copy.

``vc`` (``torchsynth_voice.vco2_golden``) moves with the lane -- its only
reader inside ``tb/run_tb.py`` was this lane -- and is re-exported alongside
the lane names because ``tb/run_voice.py`` imports it off ``run_tb`` (the
same way ``ag``/``lgo`` travel with ``tb/lane_lfo.py``).
"""

from __future__ import annotations

import copy
import hashlib
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
    write_lut_memh,
)
from torchsynth_voice import golden_vectors as gv  # noqa: E402
from torchsynth_voice import vco2_golden as vc  # noqa: E402
from torchsynth_voice.fixed_voice import AcceptedFormats  # noqa: E402
from torchsynth_voice.fixedpoint import schedule as sched  # noqa: E402
from torchsynth_voice.fixedpoint.choices import ChoiceNotAccepted  # noqa: E402
from torchsynth_voice.fixedpoint.schedule import ScheduleNotAccepted  # noqa: E402

_core = None


def bind_core(core) -> None:
    """Bind the ``run_tb`` module that owns the shared core symbols."""

    global _core
    _core = core


#: Committed square/saw VCO golden vectors (issue #74).
VCO2_DUT_SV = TB_ROOT / "sv/square_saw_vco_engine.sv"
VCO_LUT_SV = TB_ROOT / "sv/quarter_wave_lut.sv"
VCO2_TB_SV = TB_ROOT / "sv/tb_square_saw_vco.sv"
VCO_VECTOR_DIR = ROOT / "sim/reference/square-saw-vco-golden-v1"
#: Frozen-binding vectors carry this prefix and bind the retained
#: fixed-voice-golden-v1 vco_2.raw sidecar words (direct RTL-vs-frozen
#: word validation, no mirror in the loop).
VCO_FROZEN_PREFIX = "frozen:"
#: The case the wrong-shape-mix RTL mutation is demonstrated on: shape 1.0
#: makes the inverted right-branch coefficient maximally observable.
VCO_SHAPE_MUTATION_CASE = "frozen:waveform:vco_2:saw"
#: Cases the stimulus mutations are demonstrated on: the intermediate-mix
#: case carries a nontrivial tanh fanout (shadow-word corruption must bite
#: and localize) and an unclamped partials word (the one-ULP constant
#: error must localize to the driven stream alone).
VCO_VECTOR_MUTATION_CASE = "shape:intermediate-half"
#: The sample whose replayed left_q shadow word the corruption flips.
VCO_SHADOW_MUTATION_INDEX = 12345
#: Mutation-simulation walk cap: every mutation demonstrably bites within
#: the first 24,000 samples, so the three mutation sims walk a fraction of
#: the grid. Committed-case runs always walk the full 176,400 samples.
VCO2_MUTATION_WALK_CAP = 24000
#: Replay-independence pair: static words, phase, and counters are
#: per-trigger; run 0 state must not leak into run 1.
VCO2_REPLAY_PAIR = ("frozen:waveform:vco_2:saw", "shape:intermediate-half")


def vco_load_vectors():
    """Load and contract-verify every committed square/saw VCO vector."""

    return load_golden_vectors(VCO_VECTOR_DIR, "square/saw VCO")


def vco2_derive_case(formats, vector):
    """Model-derived stimulus and truth for one square/saw VCO case.

    The host mirror (``torchsynth_voice.vco2_golden``) re-walks the frozen
    model's vco_2 lane with the model's own primitives; its ``vco_2.raw``
    digest must equal the committed evidence row — and, on frozen-binding
    vectors, the frozen golden's trace digest plus the retained sidecar
    words. The declared binary64 shadow sites (midi->Hz exp2, the
    partials constant, the tanh fanout) are computed host-side here
    exactly as DR-0008/DR-0010 declare them open approximation items —
    a harness input derivation, never an RTL claim.
    """

    provenance = vector["provenance"]
    derivation = vc.derive_case(formats, vector["parameters"])
    streams = derivation["streams"]
    digest = vc.voice_digest(streams["v2"])
    row = provenance["vco_2_raw"]
    if digest != row["words_sha256"]:
        raise SystemExit(
            "square/saw VCO mirror digest drift for %s: regenerated %s but "
            "the committed vector declares %s -- regenerate the vectors, "
            "do not recompile"
            % (provenance["case_id"], digest, row["words_sha256"])
        )
    for j_str, word in row["jitter"].items():
        if streams["v2"][int(j_str)] != word:
            raise SystemExit(
                "jitter drift for %s at j=%s against the committed vector"
                % (provenance["case_id"], j_str)
            )

    frozen_words = None
    binding = provenance.get("frozen_binding")
    if binding:
        payload = (ROOT / binding["sidecar"]["file"]).read_bytes()
        if hashlib.sha256(payload).hexdigest() != binding["sidecar"]["sha256"]:
            raise SystemExit(
                "frozen sidecar bytes drifted for %s" % provenance["case_id"]
            )
        frozen_words = vc.unpack_words_f32le(payload)
        if vc.voice_digest(frozen_words) != binding["trace_digest"]:
            raise SystemExit(
                "frozen sidecar words digest drift for %s"
                % provenance["case_id"]
            )
        if digest != binding["trace_digest"]:
            raise SystemExit(
                "mirror does not reproduce the frozen vco_2.raw digest "
                "for %s" % provenance["case_id"]
            )

    return {
        "case_id": provenance["case_id"],
        "statics": {
            "keyboard.midi_f0": derivation["words"]["keyboard.midi_f0"],
            "vco_2.tuning": derivation["words"]["vco_2.tuning"],
            "vco_2.mod_depth": derivation["words"]["vco_2.mod_depth"],
            "vco_2.shape": derivation["words"]["vco_2.shape"],
            "partials_word": derivation["partials"].word,
            "init_phase_word": derivation["init_word"],
        },
        "up_pitch": derivation["up_pitch"],
        "fq": streams["fq"],
        "square_q": streams["square_q"],
        "left_q": streams["left_q"],
        "streams": streams,
        "sat_total": streams["counters"]["total_saturation"],
        "frozen_words": frozen_words,
    }


def vco2_write_case(workdir: Path, run: int, case: dict, walk_cap: int = None):
    """Write one run's stimulus files (statics + per-sample stream)."""

    s = case["statics"]
    n = len(case["up_pitch"]) if walk_cap is None else min(
        walk_cap, len(case["up_pitch"])
    )
    (workdir / ("run%d_statics.txt" % run)).write_text(
        "%d %d %d %d %d %d %d\n"
        % (
            s["keyboard.midi_f0"] & 0xFFFFFFFF,
            s["vco_2.tuning"] & 0xFFFFFFFF,
            s["vco_2.mod_depth"] & 0xFFFFFFFF,
            s["vco_2.shape"] & 0xFFFFFFFF,
            s["partials_word"] & 0xFFFFFFFF,
            s["init_phase_word"] & 0xFFFFFFFF,
            n,
        ),
        encoding="utf-8",
    )
    rows = []
    for i in range(n):
        rows.append(
            "%d %d %d %d"
            % (
                case["up_pitch"][i] & 0xFFFFFFFF,
                case["fq"][i] & 0xFFFFFFFF,
                case["square_q"][i] & 0xFFFFFFFF,
                case["left_q"][i] & 0xFFFFFFFF,
            )
        )
    (workdir / ("run%d_stream.txt" % run)).write_text(
        "".join(row + "\n" for row in rows), encoding="utf-8"
    )


def vco2_simulate(workdir: Path, simulator: str, runs: int, formats,
                 dut_sv: Path = None) -> list:
    """Compile and run the square/saw VCO tb; return per-run captures.

    Writes the accepted quarter-wave table memh into the working directory
    (the +lut payload, hash-linked to the accepted register exactly as the
    anchor flow does).
    """

    (workdir / "runs.txt").write_text("%d\n" % runs, encoding="utf-8")
    lut_memh = workdir / "lut_quarter_cos.memh"
    write_lut_memh(formats.table, lut_memh)
    vvp = _compile_iverilog(
        workdir, simulator, "square_saw_vco.vvp",
        [_core.CONSTANTS_PKG_SV, VCO_LUT_SV, dut_sv or VCO2_DUT_SV, VCO2_TB_SV],
    )
    _run(
        ["vvp", "-n", str(vvp), "+lut=%s" % lut_memh.name],
        cwd=workdir,
    )
    captures = []
    for run in range(runs):
        rows = []
        for line in (
            workdir / ("run%d_out.txt" % run)
        ).read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append([int(word) for word in line.split()])
        ops = []
        for line in (
            workdir / ("run%d_ops.txt" % run)
        ).read_text(encoding="utf-8").splitlines():
            if line.strip():
                ops.append(line.split())
        cycles = int(
            (workdir / ("run%d_cycles.txt" % run))
            .read_text(encoding="utf-8").strip()
        )
        captures.append({"rows": rows, "ops": ops, "cycles": cycles})
    return captures


#: The engine's four exported streams, in capture-column order.
VCO_STREAM_NAMES = ("vco_2.pitch", "vco_2.driven", "vco_2.right_q",
                    "vco_2.raw")


def vco_mismatch_trace_set(capture_rows, case, prefix: bool = False) -> set:
    """Trace names where the capture differs from the mirror truth.

    ``prefix`` compares each stream over the captured prefix only — used
    by the capped-walk mutation demonstrations, where a shorter capture is
    not itself a mismatch but any differing sample inside the captured
    prefix is. On frozen-binding cases the vco_2.raw capture must also
    match the frozen sidecar words directly (no mirror in the loop).
    """

    mirror = (case["streams"]["m2"], case["streams"]["driven"],
              case["streams"]["right_q"], case["streams"]["v2"])
    bad = set()
    for index, name in enumerate(VCO_STREAM_NAMES):
        got = [row[index] for row in capture_rows]
        want = mirror[index]
        if prefix:
            want = want[: len(got)]
        if got != want:
            bad.add(name)
    if case["frozen_words"] is not None:
        got = [row[3] for row in capture_rows]
        want = case["frozen_words"]
        if prefix:
            want = want[: len(got)]
        if got != want:
            bad.add("vco_2.raw(frozen sidecar)")
    return bad


def vco_report_first_mismatch(capture_rows, case) -> None:
    """Print the first differing (stream, sample, expected, actual) row."""

    mirror = (case["streams"]["m2"], case["streams"]["driven"],
              case["streams"]["right_q"], case["streams"]["v2"])
    for index, name in enumerate(VCO_STREAM_NAMES):
        got = [row[index] for row in capture_rows]
        want = mirror[index]
        for i in range(max(len(got), len(want))):
            a = got[i] if i < len(got) else None
            b = want[i] if i < len(want) else None
            if a != b:
                print(
                    gv.format_mismatch(
                        gv.Mismatch(cycle=i, sample=i, trace=name,
                                    expected=b, actual=a)
                    )
                )
                return


def vco2_check_case(capture, case, case_id: str) -> bool:
    """Sample-exact verification for one run (mirror + frozen words)."""

    if not capture["rows"]:
        print("square/saw VCO case %s: EMPTY capture" % case_id)
        return False
    bad = vco_mismatch_trace_set(capture["rows"], case)
    if bad:
        print("square/saw VCO case %s: mismatch in %s" % (case_id, sorted(bad)))
        vco_report_first_mismatch(capture["rows"], case)
        return False
    frozen_note = " + frozen sidecar words" if case["frozen_words"] else ""
    print(
        "case %s: %d samples x %d streams, RTL sample-exact%s -> OK"
        % (case_id, len(capture["rows"]), len(VCO_STREAM_NAMES), frozen_note)
    )
    return True


def vco2_expected_ops(sample_count: int, sat_total: int) -> list:
    """The DR-0010 #74 owner-row op-count expectation for one run.

    Per sample: 8 multiply-class products (depth-mod, 2x LUT interp x2,
    driven, right shape, combine), 8 add/sub-class ops (pitch sum x2,
    phase add, 2x interp sub+add, right-branch add), 7 declared narrowing
    sites (pitch depth-mod, increment division, 2x interpolation rounding,
    driven, right, combine), plus the model mirror's own sticky saturation
    total across the declared C7 sites.
    """

    return [
        "M",
        8 * sample_count,
        8 * sample_count,
        7 * sample_count,
        sat_total,
    ]


def vco2_run_mutation(workdir: Path, simulator: str, formats, label: str,
                     anchor: str, replacement: str, case_id: str,
                     case_dirs: dict):
    """Plant one RTL mutation on one case and require it to be DETECTED."""

    case = case_dirs[case_id][1]

    def write_mutant(mut_dir):
        source = VCO2_DUT_SV.read_text(encoding="utf-8")
        if anchor not in source:
            raise SystemExit("mutation anchor not found for %s" % label)
        (mut_dir / "square_saw_vco_engine_mut.sv").write_text(
            mutate_sv(source, anchor, replacement, label), encoding="utf-8"
        )

    def write_case(mut_dir):
        vco2_write_case(mut_dir, 0, case, walk_cap=VCO2_MUTATION_WALK_CAP)

    def simulate(mut_dir):
        return vco2_simulate(
            mut_dir, simulator, 1, formats,
            dut_sv=mut_dir / "square_saw_vco_engine_mut.sv",
        )[0]

    def is_detected(capture):
        bad = vco_mismatch_trace_set(capture["rows"], case, prefix=True)
        return bool(bad), " (mismatch traces: %s)" % sorted(bad)

    return run_rtl_mutation(
        workdir, label, case_id, write_mutant, write_case, simulate, is_detected
    )


def vco_run_vector_mutation(workdir: Path, simulator: str, formats,
                            label: str, case_id: str, case_dirs: dict,
                            rewrite_stimulus, expect_traces: set):
    """Plant a stimulus-side mutation and require sharp localization.

    ``rewrite_stimulus(case)`` mutates the replayed stimulus words in place
    on a deep copy; the expectations stay pristine, so the capture must
    diverge exactly inside ``expect_traces``.
    """

    mutated = copy.deepcopy(case_dirs[case_id][1])
    rewrite_stimulus(mutated)

    def write_mutant(mut_dir):
        pass  # stimulus-side mutation: the DUT itself is never mutated.

    def write_case(mut_dir):
        vco2_write_case(mut_dir, 0, mutated, walk_cap=VCO2_MUTATION_WALK_CAP)

    def simulate(mut_dir):
        return vco2_simulate(mut_dir, simulator, 1, formats)[0]

    def is_detected(capture):
        bad = vco_mismatch_trace_set(capture["rows"], mutated, prefix=True)
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
        kind="stimulus",
    )


def vco2(workdir: Path, simulator: str) -> int:
    """Issue #74 flow: the square/saw VCO engine vs the frozen vectors."""

    try:
        formats = AcceptedFormats()
    except ChoiceNotAccepted as error:
        print("SQUARE-SAW VCO REFUSED: accepted register refused: %s" % error)
        return 1
    try:
        schedule = sched.require_accepted_schedule()
    except ScheduleNotAccepted as error:
        print("SQUARE-SAW VCO REFUSED: DR-0010 schedule register refused: %s" % error)
        return 1

    vectors = vco_load_vectors()
    frozen_count = sum(
        1 for name in vectors if name.startswith(VCO_FROZEN_PREFIX)
    )
    print(
        "Loaded %d square/saw VCO golden vectors (%d frozen-binding), "
        "contract bindings verified" % (len(vectors), frozen_count)
    )
    print(
        "Declared shadow boundary: exp2/partials/tanh replayed host-side "
        "(DR-0008/DR-0010 open approximation items); no RTL transcendental "
        "is implemented or claimed."
    )

    ok = True

    # 1. Sample-exact engine runs, one committed case per invocation.
    case_dirs = {}
    for case_id in sorted(vectors):
        case_dir = workdir / ("case-" + case_id.replace(":", "_"))
        case_dir.mkdir(parents=True, exist_ok=True)
        case = vco2_derive_case(formats, vectors[case_id])
        vco2_write_case(case_dir, 0, case)
        captures = vco2_simulate(case_dir, simulator, 1, formats)
        if not vco2_check_case(captures[0], case, case_id):
            ok = False
        case_dirs[case_id] = (case_dir, case)

    # 2. Reset/replay: a second trigger cannot retain prior state (static
    #    words, phase, and op counters are per-trigger).
    first, second = VCO2_REPLAY_PAIR
    replay_dir = workdir / "replay"
    replay_dir.mkdir(parents=True, exist_ok=True)
    case_first = vco2_derive_case(formats, vectors[first])
    case_second = vco2_derive_case(formats, vectors[second])
    vco2_write_case(replay_dir, 0, case_first)
    vco2_write_case(replay_dir, 1, case_second)
    replay_captures = vco2_simulate(replay_dir, simulator, 2, formats)
    solo_dir = workdir / "solo"
    solo_dir.mkdir(parents=True, exist_ok=True)
    vco2_write_case(solo_dir, 0, case_second)
    solo_captures = vco2_simulate(solo_dir, simulator, 1, formats)
    replay_ok = True
    if replay_captures[1]["rows"] != solo_captures[0]["rows"]:
        print(
            "SQUARE-SAW VCO FAILED: run-after-run capture differs from the "
            "solo run - prior run state leaked"
        )
        replay_ok = False
    if not vco2_check_case(replay_captures[1], case_second, second):
        replay_ok = False
    print(
        "reset/replay: trigger-to-trigger back-to-back runs (%s -> %s) "
        "reproduce the solo golden run -> %s"
        % (first, second, "OK" if replay_ok else "FAIL")
    )
    ok = ok and replay_ok

    # 3. Budget: exported op counters + the DR-0010 #74 owner row.
    ops_ok = True
    for run_index, case in enumerate((case_first, case_second)):
        parsed = replay_captures[run_index]["ops"][0]
        n = len(replay_captures[run_index]["rows"])
        expected = vco2_expected_ops(n, case["sat_total"])
        if parsed != [str(x) for x in expected]:
            print(
                "SQUARE-SAW VCO FAILED: op counters %r != expected %r "
                "(run %d)" % (parsed, expected, run_index)
            )
            ops_ok = False
    print(
        "budget: exported counters over both replay runs -> %s (per sample "
        "8 mult / 8 add / 7 narrow = the DR-0010 #74 owner row's pitch row "
        "+ two LUT interps + shape row; sats = the model mirror's sticky "
        "saturation total)" % ("OK" if ops_ok else "FAIL")
    )
    ok = ok and ops_ok and _core.check_clip_budget(
        solo_captures[0]["cycles"], len(solo_captures[0]["rows"])
    )

    # 4. Mutations: each planted fault MUST be detected (AC-5).
    mutations_ok = True

    # 4a. Wrong shape mix (RTL): the right branch's shape coefficient is
    #     inverted to (1 - shape), the classic square/saw mix confusion.
    mutations_ok &= vco2_run_mutation(
        workdir, simulator, formats, "wrong-shape-mix",
        "wire signed [95:0] right_prod = $signed(s_shape) * $signed(cos2);",
        "wire signed [95:0] right_prod = (96'sd2097152 - $signed(s_shape))"
        " * $signed(cos2);  // MUTANT: wrong shape mix (1-shape)",
        VCO_SHAPE_MUTATION_CASE, case_dirs,
    )

    # 4b. Partials-constant error (stimulus): one ULP on the replayed
    #     s14.17 partials word must localize to the driven stream alone
    #     (v2 is downstream of the tanh shadow, which the host owns).
    def _partials_ulp(case):
        case["statics"]["partials_word"] = (
            case["statics"]["partials_word"] + 1
        )

    mutations_ok &= vco_run_vector_mutation(
        workdir, simulator, formats, "partials-constant-error",
        VCO_VECTOR_MUTATION_CASE, case_dirs, _partials_ulp,
        {"vco_2.driven"},
    )

    # 4c. Shadow-word corruption (stimulus): one flipped LSB in the
    #     replayed left_q tanh fanout must localize to vco_2.raw alone at
    #     exactly that sample.
    def _shadow_flip(case):
        case["left_q"][VCO_SHADOW_MUTATION_INDEX] ^= 1

    mutations_ok &= vco_run_vector_mutation(
        workdir, simulator, formats, "shadow-word-corruption",
        VCO_VECTOR_MUTATION_CASE, case_dirs, _shadow_flip,
        {"vco_2.raw"},
    )
    ok = ok and mutations_ok

    if not ok:
        print("SQUARE-SAW VCO RUN FAILED")
        return 1
    print(
        "SQUARE-SAW VCO RUN PASSED (contract binding + %d cases "
        "sample-exact incl. %d frozen-binding sidecar cases + "
        "reset/replay independence + budget/op-count asserts + all "
        "mutations detected)" % (len(vectors), frozen_count)
    )
    return 0
