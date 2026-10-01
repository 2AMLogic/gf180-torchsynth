#!/usr/bin/env python3
"""Issue #79: integrated mixer -> replay-controller chain, end to end.

``python3 tb/run_oneshot.py [--profile regression|full] [--workdir DIR]``

Runs the first integrated composition of landed RTL (``tb/sv/
one_shot_tail_top.sv``: the #76 audio VCA + pre-normalization mixer feeding
the #77 normalization replay controller) over the frozen fixed model's
cases, compares every sample of both passes and the output clip plus every
status/counter register exactly (no tolerance), localizes the first mismatch
by trace/cycle/sample, proves two clean simulations artifact-hash identical,
and requires every planted negative control to be detected.

This is deliberately a separate entry point from ``tb/run_tb.py``: it is not
(yet) a lane of the #78 aggregate qualification gate, whose lint ledger
(``tb/rtl-lint-baseline.json``) is calibrated to CI's toolchain and would
need a matching re-baseline. See spec/ONESHOT-E2E.md for scope, the
runtime/regression partition and the evidence commands. Scope honesty: the
chain's sources and amplitude columns are host-fed from the frozen model;
this is NOT the whole-voice top and makes no synthesis, layout, signoff,
hardware, or sound-fidelity claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_tb import (  # noqa: E402
    CONSTANTS_PKG_SV, MIX_DUT_SV, MIX_FIXTURE_CASES, MIX_RTL_MUTATIONS,
    NORMREPLAY_ALWAYS_OFF_MUTANT, NORMREPLAY_ALWAYS_ON_ANCHOR,
    NORMREPLAY_ALWAYS_ON_MUTANT, NORMREPLAY_DUT_SV,
    NORMREPLAY_WRONG_PEAK_ANCHOR, NORMREPLAY_WRONG_PEAK_MUTANT,
    NORMREPLAY_WRONG_RECIPROCAL_ANCHOR, NORMREPLAY_WRONG_RECIPROCAL_MUTANT,
    ROOT, TB_ROOT, VCO_RECEIPT_PATH, ChoiceNotAccepted, StickyCounters, _run,
    dr, mix_derive_case, mix_expected_ops, mix_load_fixtures,
    mix_load_receipt, mix_write_case, mutate_sv, mx, nrg,
)

ONESHOT_TOP_SV = TB_ROOT / "sv/one_shot_tail_top.sv"
ONESHOT_TB_SV = TB_ROOT / "sv/tb_one_shot_tail.sv"
#: Run profiles (the declared runtime/regression partition, spec/ONESHOT-E2E.md):
#: ``regression`` is the bounded CI/PR set; ``full`` is every param-committed
#: receipt case plus the declared directed fixtures. Both walk the complete
#: 176,400-sample clip twice (two passes) per case; neither caps a walk.
ONESHOT_PROFILE = "regression"
ONESHOT_REGRESSION_CASES = (
    "normalization:above",
    "normalization:below",
    "normalization:tie",
    "normalization-stress:anchor-3.9478583336",
    "source:noise",
    "special:silence",
    "special:stress",        # divide branch (saturating)
    "oneshot:divide-distinct-levels",  # divide branch, three distinct levels
)
#: Declared derived fixtures (not receipt cases: the receipt's divide-branch
#: cases are the digest-custody ``global-*`` corpus items, whose physical
#: parameters are never committed, so no RTL stimulus can be built from them).
#: Each is a base directed fixture plus physical-map overrides; the model-vs-
#: mirror equality proof in ``mix_derive_case`` still applies, but there is no
#: frozen-receipt pin for these (the report says so per case).
ONESHOT_DERIVED_CASES = {
    "oneshot:divide-distinct-levels": (
        "special:stress", {"mixer.vco_2": 0.75, "mixer.noise": 0.5},
    ),
}
#: The divide-branch case the normalization/gain/shuffle/noise/interpolation/
#: missing-sample mutations are demonstrated on (it divides, so a gain/
#: normalization fault is load-bearing, and it has three distinct level words
#: so a parameter shuffle is observable), and the bypass-branch case the
#: always-on mutation is demonstrated on.
ONESHOT_MUTATION_DIVIDE_CASE = "oneshot:divide-distinct-levels"
ONESHOT_MUTATION_BYPASS_CASE = "normalization:below"
#: Pair of cases whose two clean simulations must be artifact-hash identical.
ONESHOT_HASH_CASES = ("oneshot:divide-distinct-levels", "normalization:below")
#: Sample the missing-sample link mutation drops (same index the #75 slip
#: mutations use).
ONESHOT_DROP_SAMPLE = 1000
ONESHOT_LINK_ANCHOR = "assign link_valid = mix_out_valid;"
ONESHOT_LINK_MISSING_SAMPLE = (
    "reg [31:0] drop_count;\n"
    "    always @(posedge clk) begin\n"
    "        if (rst) drop_count <= 32'd0;\n"
    "        else if (mix_out_valid) drop_count <= drop_count + 32'd1;\n"
    "    end\n"
    "    assign link_valid = mix_out_valid && !(drop_count == 32'd%d);"
    "  // MUTANT: one sample dropped at the link" % ONESHOT_DROP_SAMPLE
)
ONESHOT_ARTIFACTS = (
    "params", "streams", "mixcap", "outcap", "status", "ops",
)
#: Where raw artifacts of a failed committed run are retained (a fresh
#: directory under the system temp dir, printed on failure).
ONESHOT_RETAIN_PREFIX = "oneshot-failed-"


def oneshot_simulate(workdir: Path, runs: int, top_sv: Path = None,
                     mixer_sv: Path = None, replay_sv: Path = None) -> list:
    """Compile + run the integrated top's tb; return per-run captures."""

    top_sv = top_sv or ONESHOT_TOP_SV
    mixer_sv = mixer_sv or MIX_DUT_SV
    replay_sv = replay_sv or NORMREPLAY_DUT_SV
    (workdir / "runs.txt").write_text("%d\n" % runs, encoding="utf-8")
    vvp = workdir / "oneshot.vvp"
    _run(
        [
            "iverilog", "-g2012", "-o", str(vvp), str(CONSTANTS_PKG_SV),
            str(mixer_sv), str(replay_sv), str(top_sv), str(ONESHOT_TB_SV),
        ],
        cwd=workdir,
    )
    _run(["vvp", "-n", str(vvp)], cwd=workdir)
    captures = []
    for run in range(runs):
        def rows(name, width):
            out = []
            for line in (
                workdir / ("run%d_%s.txt" % (run, name))
            ).read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                fields = line.split()
                if len(fields) != width:
                    raise SystemExit(
                        "%s row %r does not carry %d fields" % (name, line, width)
                    )
                try:
                    out.append([int(f) for f in fields])
                except ValueError:
                    out.append(None)  # an x-state emission: a mismatch
            return out
        mixcap = rows("mixcap", 3)
        capture = {
            "mix1": [(row[0], row[2]) if row else (None, None)
                     for row in mixcap if row is None or row[1] == 1],
            "mix2": [(row[0], row[2]) if row else (None, None)
                     for row in mixcap if row is not None and row[1] == 2],
            "out": [tuple(row) if row else (None, None)
                    for row in rows("outcap", 2)],
        }
        status = (workdir / ("run%d_status.txt" % run)).read_text(
            encoding="utf-8").split()
        capture["status"] = [
            int(v) if v.lstrip("-").isdigit() else None for v in status
        ]
        ops = {}
        for line in (workdir / ("run%d_ops.txt" % run)).read_text(
                encoding="utf-8").splitlines():
            if line.strip():
                fields = line.split()
                ops[fields[0]] = [
                    int(v) if v.lstrip("-").isdigit() else None
                    for v in fields[1:]
                ]
        capture["ops"] = ops
        captures.append(capture)
    return captures


def oneshot_expected(formats, case: dict) -> dict:
    """The fixed model's expected chain behavior for one derived case."""

    counters = StickyCounters()
    mix = case["streams"]["mix"]
    out, diag = nrg.mirror_normalize(mix, formats, counters)
    n = len(mix)
    normalized = bool(diag["normalized_branch"])
    truth = {"streams": case["streams"], "aux": case["aux"]}
    return {
        "mix": mix,
        "out": out,
        "diag": diag,
        "status": [
            0, 0, diag["peak_word"], diag["gain_word"],
            1 if normalized else 0, 1, 3,
            n, n, 1 if normalized else 0,
            n if normalized else 0, n if normalized else 0,
            counters.total(), diag["peak_word"],
        ],
        "mix_ops": mix_expected_ops(n, truth),
    }


ONESHOT_STATUS_NAMES = (
    "error", "error_code", "norm.peak", "norm.gain", "branch", "done",
    "pass_index", "norm.compares", "norm.selects", "norm.recip_divs",
    "norm.mults", "norm.narrows", "norm.saturations", "mixer.peak_feed",
)


def oneshot_rows(capture: dict, expected: dict) -> list:
    """Every first-mismatch row: (trace, cycle, sample, expected, actual).

    Exact comparison, no tolerance anywhere. Sequences are compared word by
    word (the first differing sample is named with the capture's own cycle),
    then every status register and both passes' mixer counters.
    """

    found = []

    def sequence(trace, got, want):
        for index in range(max(len(got), len(want))):
            g = got[index] if index < len(got) else None
            w = want[index] if index < len(want) else None
            if g is None or w is None or g[1] != w:
                found.append(
                    (trace, g[0] if g else None, index, w,
                     g[1] if g else None)
                )
                return

    sequence("mixer.pre_normalization[pass1]", capture["mix1"], expected["mix"])
    sequence("mixer.pre_normalization[pass2]", capture["mix2"], expected["mix"])
    sequence("mixer.output", capture["out"], expected["out"])
    got_status = capture["status"]
    for name, want, got in zip(
        ONESHOT_STATUS_NAMES, expected["status"], got_status
    ):
        if got != want:
            found.append(("status." + name, None, None, want, got))
    if len(got_status) != len(expected["status"]):
        found.append(("status.width", None, None, len(expected["status"]),
                      len(got_status)))
    for pas in ("P1", "P2"):
        got = capture["ops"].get(pas)
        if got != expected["mix_ops"]:
            found.append(("mixer.ops[%s]" % pas, None, None,
                          expected["mix_ops"], got))
    return found


def oneshot_print_rows(label: str, rows: list) -> None:
    for trace, cycle, sample, want, got in rows:
        print(
            "ONESHOT FAILED: %s first mismatch: trace=%s cycle=%s sample=%s "
            "expected=%s actual=%s" % (label, trace, cycle, sample, want, got)
        )


def oneshot_artifact_hashes(workdir: Path, runs: int = 1) -> dict:
    """sha256 of every raw artifact one clean simulation produced."""

    hashes = {}
    for run in range(runs):
        for name in ONESHOT_ARTIFACTS:
            path = workdir / ("run%d_%s.txt" % (run, name))
            hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def oneshot_pin_to_receipt(case_id: str, expected: dict, frozen: dict) -> bool:
    """Bind the expected chain outputs to the frozen receipt's trace digests."""

    if frozen is None:
        return True
    want = frozen["traces"]
    checks = (
        ("mixer.output", nrg.digest_words(expected["out"])),
        ("mixer.gain", nrg.digest_words([expected["diag"]["gain_word"]])),
        ("mixer.peak", nrg.digest_words([expected["diag"]["peak_word"]])),
    )
    ok = True
    for name, digest in checks:
        if digest != want[name]:
            print("ONESHOT FAILED: %s %s digest %s != frozen receipt %s"
                  % (case_id, name, digest[:12], want[name][:12]))
            ok = False
    if bool(expected["diag"]["normalized_branch"]) != bool(
            frozen["branch"]["fixed"]):
        print("ONESHOT FAILED: %s branch %s != frozen receipt branch.fixed %s"
              % (case_id, expected["diag"]["normalized_branch"],
                 frozen["branch"]["fixed"]))
        ok = False
    return ok


def oneshot_tool_identity() -> dict:
    """Exact commit/tool identity for an evidence record (no PDK, no float)."""

    def git(*args):
        try:
            return subprocess.run(
                ["git", "-C", str(ROOT)] + list(args), check=True,
                capture_output=True, text=True,
            ).stdout.strip()
        except Exception:  # noqa: BLE001 - identity is best effort, flagged
            return "unavailable"

    try:
        iverilog = subprocess.run(
            ["iverilog", "-V"], capture_output=True, text=True
        ).stdout.splitlines()[0]
    except Exception:  # noqa: BLE001
        iverilog = "unavailable"

    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    return {
        "git_head": git("rev-parse", "HEAD"),
        "git_tree_dirty": bool(git("status", "--porcelain", "--", "tb", "src",
                                   "spec/reference")),
        "iverilog": iverilog,
        "rtl_sha256": {
            path.name: digest(path)
            for path in (MIX_DUT_SV, NORMREPLAY_DUT_SV, ONESHOT_TOP_SV,
                         ONESHOT_TB_SV, CONSTANTS_PKG_SV)
        },
        "fixed_vector_sha256": {
            "fixed-voice-golden-v1.json": digest(VCO_RECEIPT_PATH),
            "directed-voice-v1.json": digest(dr.MANIFEST_PATH),
        },
    }


def oneshot_stimulus_mutations(case: dict) -> dict:
    """Mutated stimuli for the host-side negative controls on one case."""

    lanes = mx.LANES
    levels = case["levels"]
    if len({levels[lane] for lane in lanes}) != 3:
        raise SystemExit(
            "case %s needs three distinct level words for the parameter-"
            "shuffle control" % case["id"]
        )
    shuffled = {lanes[i]: levels[lanes[(i + 1) % 3]] for i in range(3)}
    rotated_noise = dict(case["raw"])
    noise = case["raw"]["noise"]
    rotated_noise["noise"] = noise[1:] + noise[:1]
    held = {
        lane: [
            case["amp"][lane][(n // 100) * 100]
            for n in range(len(case["amp"][lane]))
        ]
        for lane in lanes
    }
    one_ulp = dict(levels)
    one_ulp["noise"] = one_ulp["noise"] + 1
    return {
        "parameter-shuffle": {"levels": shuffled},
        "wrong-noise": {"raw": rotated_noise},
        "interpolation-zoh": {"amp": held},
        "gain-one-ulp": {"levels": one_ulp},
    }


def oneshot_write_case(workdir: Path, run: int, case: dict, levels=None,
                       raw=None, amp=None) -> None:
    patched = dict(case)
    if raw is not None:
        patched["raw"] = raw
    mix_write_case(workdir, run, patched, levels=levels, amp=amp)


def oneshot(workdir: Path, simulator: str) -> int:
    """Issue #79 flow: integrated mixer -> replay chain vs the frozen model."""

    profile = ONESHOT_PROFILE
    try:
        receipt, formats, cases = mix_load_receipt()
    except ChoiceNotAccepted as error:
        print("ONESHOT REFUSED: accepted register refused: %s" % error)
        return 1
    manifest, fixtures = mix_load_fixtures()
    if profile == "full":
        plan = [(cid, cases[cid]) for cid in sorted(cases)]
        plan += [(cid, None) for cid in MIX_FIXTURE_CASES]
        plan += [(cid, None) for cid in sorted(ONESHOT_DERIVED_CASES)]
    else:
        plan = [
            (cid, cases.get(cid)) for cid in ONESHOT_REGRESSION_CASES
        ]
    print(
        "ONESHOT profile %s: %d cases through the integrated mixer -> "
        "replay-controller top (two 176,400-sample passes each); frozen "
        "receipt bindings verified (DR-0008 %s)"
        % (profile, len(plan), receipt["bindings"]["dr_0008_status"])
    )
    ok = True
    derived = {}
    expectations = {}
    divide_seen = bypass_seen = False

    # 1. Committed cases: every one bit-exact, status/trace sequences exact.
    for case_id, frozen in plan:
        case_dir = workdir / ("case-" + case_id.replace(":", "_"))
        case_dir.mkdir(parents=True, exist_ok=True)
        if case_id in ONESHOT_DERIVED_CASES:
            base_id, overrides = ONESHOT_DERIVED_CASES[case_id]
            physical = dict(fixtures[base_id]["physical"])
            physical.update(overrides)
            normalized = fixtures[base_id]["normalized"]
        elif frozen is None:
            physical = fixtures[case_id]["physical"]
            normalized = fixtures[case_id]["normalized"]
        else:
            physical = frozen["parameters"]
            normalized = None
        case = mix_derive_case(formats, case_id, physical, normalized, frozen)
        expected = oneshot_expected(formats, case)
        case_ok = oneshot_pin_to_receipt(case_id, expected, frozen)
        oneshot_write_case(case_dir, 0, case)
        capture = oneshot_simulate(case_dir, 1)[0]
        rows = oneshot_rows(capture, expected)
        count_ok = (
            len(capture["mix1"]) == len(capture["mix2"])
            == len(capture["out"]) == case["samples"] == 176400
        )
        if not count_ok:
            print("ONESHOT FAILED: %s sample counts pass1/pass2/out = "
                  "%d/%d/%d (need exactly 176400)"
                  % (case_id, len(capture["mix1"]), len(capture["mix2"]),
                     len(capture["out"])))
        oneshot_print_rows(case_id, rows)
        case_ok = case_ok and count_ok and not rows
        if not case_ok:
            keep = Path(tempfile.mkdtemp(prefix=ONESHOT_RETAIN_PREFIX))
            shutil.copytree(case_dir, keep / case_dir.name)
            print("ONESHOT: raw artifacts of failed case %s retained at %s"
                  % (case_id, keep / case_dir.name))
        ok = ok and case_ok
        derived[case_id] = case
        expectations[case_id] = expected
        divide_seen = divide_seen or expected["diag"]["normalized_branch"]
        bypass_seen = bypass_seen or not expected["diag"]["normalized_branch"]
        print(
            "case %s: 176400 samples x 2 passes, branch %s, peak %d, gain %d, "
            "frozen-pinned %s -> %s"
            % (case_id,
               "divide" if expected["diag"]["normalized_branch"] else "bypass",
               expected["diag"]["peak_word"], expected["diag"]["gain_word"],
               frozen is not None, "OK" if case_ok else "FAIL")
        )
    branches_ok = divide_seen and bypass_seen
    print("branch coverage: divide %s, bypass %s -> %s"
          % (divide_seen, bypass_seen, "OK" if branches_ok else "FAIL"))
    ok = ok and branches_ok

    # 2. Back-to-back triggers with no reset: run 1 must reproduce its solo
    #    sample stream byte-for-byte (no datapath state survives a trigger).
    #    The replay engine's op counters are free-running until reset, so the
    #    second run's counters must equal solo + the first run's counters; the
    #    comparison removes the first run's contribution explicitly.
    first, second = ONESHOT_HASH_CASES
    for cid in (first, second):
        if cid not in derived:
            raise SystemExit("hash/replay case %r is not in this profile" % cid)
    replay_dir = workdir / "replay"
    replay_dir.mkdir(parents=True, exist_ok=True)
    for index, cid in enumerate((first, second)):
        oneshot_write_case(replay_dir, index, derived[cid])
    pair = oneshot_simulate(replay_dir, 2)
    solo_dir = workdir / "solo"
    solo_dir.mkdir(parents=True, exist_ok=True)
    oneshot_write_case(solo_dir, 0, derived[second])
    solo = oneshot_simulate(solo_dir, 1)[0]
    second_run = dict(pair[1])
    second_run["status"] = [
        (v - u) if 7 <= i <= 12 and v is not None and u is not None else v
        for i, (v, u) in enumerate(zip(pair[1]["status"], pair[0]["status"]))
    ]
    replay_ok = (
        len(pair[1]["status"]) == len(pair[0]["status"])
        and bool(pair[1]["mix1"])
        and [w for _c, w in pair[1]["mix1"]] == [w for _c, w in solo["mix1"]]
        and [w for _c, w in pair[1]["out"]] == [w for _c, w in solo["out"]]
        and second_run["status"] == solo["status"]
        and not oneshot_rows(second_run, expectations[second])
        and not oneshot_rows(pair[0], expectations[first])
    )
    print("replay: %s then %s back-to-back (no reset) reproduces the solo "
          "run and both match the model -> %s"
          % (first, second, "OK" if replay_ok else "FAIL"))
    ok = ok and replay_ok

    # 3. Two clean simulations are artifact-hash identical.
    hash_rows = {}
    hash_ok = True
    for cid in ONESHOT_HASH_CASES:
        digests = []
        for attempt in (1, 2):
            d = workdir / ("hash-%s-%d" % (cid.replace(":", "_"), attempt))
            d.mkdir(parents=True, exist_ok=True)
            oneshot_write_case(d, 0, derived[cid])
            oneshot_simulate(d, 1)
            digests.append(oneshot_artifact_hashes(d))
        same = digests[0] == digests[1]
        hash_ok = hash_ok and same
        hash_rows[cid] = digests[0]
        print("artifact hashes %s: two clean simulations %s (%d files) -> %s"
              % (cid, "identical" if same else "DIFFER", len(digests[0]),
                 "OK" if same else "FAIL"))
    ok = ok and hash_ok

    # 4. Negative controls: each planted fault MUST be detected + localized.
    divide = derived[ONESHOT_MUTATION_DIVIDE_CASE]
    bypass = derived[ONESHOT_MUTATION_BYPASS_CASE]
    mutants = []

    def sim_mutant(label, kind, case, mutate=None, **stim):
        mut_dir = workdir / ("mut-" + label)
        mut_dir.mkdir(parents=True, exist_ok=True)
        kwargs = {}
        if mutate is not None:
            target, anchor, replacement = mutate
            path = mut_dir / ("mutant_" + target.name)
            path.write_text(
                mutate_sv(target.read_text(encoding="utf-8"), anchor,
                          replacement, label),
                encoding="utf-8",
            )
            if target == ONESHOT_TOP_SV:
                kwargs["top_sv"] = path
            elif target == NORMREPLAY_DUT_SV:
                kwargs["replay_sv"] = path
            else:
                kwargs["mixer_sv"] = path
        oneshot_write_case(mut_dir, 0, case, **stim)
        capture = oneshot_simulate(mut_dir, 1, **kwargs)[0]
        rows = oneshot_rows(capture, expectations[case["id"]])
        detected = bool(rows)
        first_row = rows[0] if rows else None
        print(
            "mutation %s (%s, case %s): %s%s"
            % (label, kind, case["id"],
               "DETECTED (test fails the mutant)" if detected
               else "NOT DETECTED",
               "; first mismatch trace=%s cycle=%s sample=%s expected=%s "
               "actual=%s" % first_row if first_row else "")
        )
        mutants.append((label, detected))
        return detected

    stimulus = oneshot_stimulus_mutations(divide)
    mut_ok = True
    for label in ("parameter-shuffle", "wrong-noise", "interpolation-zoh",
                  "gain-one-ulp"):
        mut_ok = sim_mutant(label, "stimulus", divide, **stimulus[label]) \
            and mut_ok
    for label, target, anchor, replacement, case in (
        ("normalization-always-off", NORMREPLAY_DUT_SV,
         NORMREPLAY_ALWAYS_ON_ANCHOR, NORMREPLAY_ALWAYS_OFF_MUTANT, divide),
        ("normalization-always-on", NORMREPLAY_DUT_SV,
         NORMREPLAY_ALWAYS_ON_ANCHOR, NORMREPLAY_ALWAYS_ON_MUTANT, bypass),
        ("normalization-wrong-reciprocal", NORMREPLAY_DUT_SV,
         NORMREPLAY_WRONG_RECIPROCAL_ANCHOR,
         NORMREPLAY_WRONG_RECIPROCAL_MUTANT, divide),
        ("normalization-wrong-peak", NORMREPLAY_DUT_SV,
         NORMREPLAY_WRONG_PEAK_ANCHOR, NORMREPLAY_WRONG_PEAK_MUTANT, divide),
        ("missing-sample", ONESHOT_TOP_SV, ONESHOT_LINK_ANCHOR,
         ONESHOT_LINK_MISSING_SAMPLE, divide),
        ("mixer-truncate", MIX_DUT_SV, MIX_RTL_MUTATIONS["mixer-truncate"][0],
         MIX_RTL_MUTATIONS["mixer-truncate"][1], divide),
    ):
        mut_ok = sim_mutant(label, "RTL", case,
                            mutate=(target, anchor, replacement)) and mut_ok
    ok = ok and mut_ok

    record = {
        "schema": "gf180-torchsynth/oneshot-tail-chain-evidence-v1",
        "profile": profile,
        "result": "PASS" if ok else "FAIL",
        "cases": [cid for cid, _ in plan],
        "case_count": len(plan),
        "samples_per_case": 176400,
        "passes_per_case": 2,
        "float_tolerance": None,
        "branch_coverage": {"divide": divide_seen, "bypass": bypass_seen},
        "mutations": {label: ("DETECTED" if det else "NOT DETECTED")
                      for label, det in mutants},
        "artifact_hashes": hash_rows,
        "identity": oneshot_tool_identity(),
        "scope": "mixer(#76) -> replay controller(#77) chain over host-fed "
                 "source/amplitude streams; NOT the whole-voice top",
    }
    (workdir / "oneshot-evidence.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("ONESHOT evidence record written to %s"
          % (workdir / "oneshot-evidence.json"))
    if not ok:
        print("ONESHOT RUN FAILED")
        return 1
    print(
        "ONESHOT RUN PASSED (profile %s: %d cases bit-exact over 2 x 176400 "
        "samples + exact status/trace sequences + reset/replay independence "
        "+ two-clean-sim artifact-hash identity + %d mutations detected; "
        "tail chain only, NOT the whole-voice top)"
        % (profile, len(plan), len(mutants))
    )
    return 0



def main(argv=None) -> int:
    global ONESHOT_PROFILE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="regression",
                        choices=["regression", "full"])
    parser.add_argument("--workdir", type=Path, default=None,
                        help="keep raw artifacts here instead of a temp dir")
    args = parser.parse_args(argv)
    ONESHOT_PROFILE = args.profile
    if shutil.which("iverilog") is None:
        print("ERROR: iverilog is not installed; the oneshot run cannot "
              "execute here. An unrun check is never a pass.")
        return 3
    if args.workdir is not None:
        args.workdir.mkdir(parents=True, exist_ok=True)
        return oneshot(args.workdir, "iverilog")
    with tempfile.TemporaryDirectory(prefix="tb-oneshot-") as tmp:
        return oneshot(Path(tmp), "iverilog")


if __name__ == "__main__":
    sys.exit(main())
