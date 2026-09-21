#!/usr/bin/env python3
"""PDK-free RTL testbench runner (issue #68; the AGENTS.md tb convenience).

Two commands:

``selftest`` proves the golden-vector harness end to end against a tiny
synthetic SystemVerilog DUT using an open-source simulator (Icarus
Verilog; no PDK, no vendor tooling):

1. generate a synthetic stimulus and its matching golden vector,
2. compile and run tb/sv/synth_dut.sv + tb/sv/tb_synth_dut.sv,
3. load the captured stream and assert the pass path (reporter: no
   mismatch), then
4. compare against an intentionally corrupted vector and assert the
   first-mismatch reporter names the exact cycle/sample/trace/expected/
   actual row of the planted fault.

``anchor`` graduates the harness to the REAL golden vector
(``sim/reference/golden-vector-fixed-anchor.json``, frozen by #54):

1. load the anchor vector through the landed schema/loader,
2. verify the accepted-contract hash binding (constants package, choice
   register, DR-0008 record; refusal on any mismatch — interface changes
   fail the check instead of silently recompiling),
3. build the accepted quarter-wave table through the DR-0008 refusal gate
   and check it against the vector's bound table digest,
4. derive the DUT control words (the phase-increment word via the declared
   binary64 shadow-site policy and the S1-entry mixer-level word) from the
   vector's own declared scalars and parameters,
5. run the format-true DUT (tb/sv/lut_sine_dut.sv, imported constants
   package) over the full 176,400-sample clip,
6. replay the integer dataflow host-side with the model's own primitives
   and require it to match the frozen vector trace exactly,
7. require the RTL capture to match both streams sample-exactly, and
8. plant a fault into the captured stream and require the reporter to name
   the exact cycle/sample/trace/expected/actual row on the real vector.

Exit 0 only if every step passes. The declared shadow sites are replayed
host-side exactly as DR-0008 declares them open items — no RTL claim is
made for them, and nothing here claims synthesis, layout, signoff, or
hardware anything.
"""

from __future__ import annotations

import argparse
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TB_ROOT = Path(__file__).resolve().parent
ROOT = TB_ROOT.parent
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import golden_vectors as gv  # noqa: E402
from torchsynth_voice.fixed_voice import (  # noqa: E402
    AcceptedFormats,
    entry_quantize,
    shadow_half_even,
)
from torchsynth_voice.fixedpoint.choices import ChoiceNotAccepted  # noqa: E402
from torchsynth_voice.fixedpoint.counters import StickyCounters  # noqa: E402
from torchsynth_voice.fixedpoint.ops import OverflowPolicy, mul, rescale  # noqa: E402
from torchsynth_voice.fixedpoint.rounding import (  # noqa: E402
    RoundingMode,
    div_round,
)

DUT_SV = TB_ROOT / "sv/synth_dut.sv"
TB_SV = TB_ROOT / "sv/tb_synth_dut.sv"
ANCHOR_DUT_SV = TB_ROOT / "sv/lut_sine_dut.sv"
ANCHOR_TB_SV = TB_ROOT / "sv/tb_lut_sine_dut.sv"
CONSTANTS_PKG_SV = TB_ROOT / "sv/gf180_rtl_constants_pkg.sv"

#: Trace name from the canonical registry used for the synthetic stream.
SYNTH_TRACE = "mixer.output"
SYNTH_PARAMETER = "adsr_1.alpha"
CYCLE_START = 0

STIMULUS = [0, 1, 2, 165, 255, 128, 7, 42]
XOR_MASK = 0xA5

#: Real-vector fail-path plant: flip the LSB of this captured sample.
ANCHOR_FAIL_INDEX = 4096

#: DUT-checked trace of the anchor vector (the LUT sine path's output).
ANCHOR_TRACE = "vco_1.raw"


def synth_model(value: int) -> int:
    """Software mirror of the synthetic DUT's registered XOR."""
    return (value & 0xFF) ^ XOR_MASK


def build_vector(values, registry, inventory) -> dict:
    vector = {
        "schema": gv.VECTOR_SCHEMA,
        "schema_version": gv.SCHEMA_VERSION,
        "provenance": {
            "generator": "tb/run_tb.py selftest",
            "note": (
                "synthetic-bringup vectors for the harness self-test; not "
                "default-nebula clip content and not DR-0008-conformant"
            ),
        },
        "trace_registry_version": registry["semantic_version"],
        "parameter_inventory_commit": inventory["source"]["commit"],
        "clip": {
            "profile": gv.SYNTHETIC_PROFILE,
            "sample_rate_hz": gv.CANONICAL_SAMPLE_RATE_HZ,
            "sample_count": len(values),
            "control_rate_hz": gv.CANONICAL_CONTROL_RATE_HZ,
            "control_count": len(values),
        },
        "parameters": {SYNTH_PARAMETER: 0.5},
        "traces": [
            {
                "name": SYNTH_TRACE,
                "kind": "audio",
                "encoding": "synthetic-int",
                "cycle_start": CYCLE_START,
                "values": list(values),
            }
        ],
    }
    vector["content_hash"] = gv.compute_content_hash(vector)
    return vector


def _run(command: list, cwd: Path) -> None:
    print("+ %s" % " ".join(command))
    subprocess.run(command, cwd=cwd, check=True)
def simulate(workdir: Path, simulator: str) -> list:
    stim = workdir / "stimulus.txt"
    captured = workdir / "captured.txt"
    stim.write_text("".join("%d\n" % v for v in STIMULUS), encoding="utf-8")
    if simulator == "iverilog":
        vvp = workdir / "synth_selftest.vvp"
        _run(
            ["iverilog", "-g2012", "-o", str(vvp), str(DUT_SV), str(TB_SV)],
            cwd=workdir,
        )
        _run(["vvp", "-n", str(vvp)], cwd=workdir)
    else:
        raise SystemExit(
            "simulator %r is not wired up; this runner is PDK-free and "
            "currently supports iverilog" % simulator
        )
    values = [
        int(line)
        for line in captured.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return values


def selftest(workdir: Path, simulator: str) -> int:
    registry = gv.load_registry_document()
    inventory = gv.load_inventory_document()
    expected = [synth_model(v) for v in STIMULUS]

    captured = simulate(workdir, simulator)

    ok = True
    if captured != expected:
        print("SELFTEST BROKEN: captured stream does not match the model")
        ok = False

    golden = build_vector(expected, registry, inventory)
    gv.load_vector(golden, registry=registry, inventory=inventory)

    mismatch = gv.first_mismatch(
        golden, {SYNTH_TRACE: captured}
    )
    if mismatch is not None:
        print("FAIL path: unexpected mismatch on the clean golden vector:")
        print(gv.format_mismatch(mismatch))
        ok = False
    else:
        print(
            "PASS path verified: clean golden vector vs captured stream -> "
            "no mismatch (reporter silent)."
        )

    corrupted = build_vector(expected, registry, inventory)
    corrupted["traces"][0]["values"][3] = (expected[3] + 1) & 0xFF
    corrupted["content_hash"] = gv.compute_content_hash(corrupted)
    # The corrupted vector is the EXPECTED side of the comparison and the
    # captured sim stream is the ACTUAL side: the vector claims one value,
    # the DUT delivered another, and the reporter must name that row.
    planted = gv.Mismatch(
        cycle=CYCLE_START + 3,
        sample=3,
        trace=SYNTH_TRACE,
        expected=corrupted["traces"][0]["values"][3],
        actual=expected[3],
    )
    observed = gv.first_mismatch(corrupted, {SYNTH_TRACE: captured})
    if observed != planted:
        print(
            "FAIL path: reporter did not name the planted fault; observed %r"
            % (observed,)
        )
        ok = False
    else:
        print("Intentional-fail path verified; reporter reports:")
        print(gv.format_mismatch(observed))

    if not ok:
        print("SELFTEST FAILED")
        return 1
    print("SELFTEST PASSED (pass path + intentional-fail path)")
    return 0


def focus_vector(vector: dict, trace_name: str) -> dict:
    """A single-trace view of a loaded vector, revalidated by the loader.

    Lets the first-mismatch reporter run against one DUT-covered trace of
    the real vector while keeping every schema, registry, and hash check.
    """
    traces = [t for t in vector["traces"] if t["name"] == trace_name]
    if len(traces) != 1:
        raise SystemExit("vector has no unique trace %r" % trace_name)
    focused = {k: v for k, v in vector.items() if k != "traces"}
    focused["traces"] = traces
    focused["content_hash"] = gv.compute_content_hash(focused)
    return gv.load_vector(focused)


def write_lut_memh(table, path: Path) -> None:
    """Emit the accepted quarter-wave table as hex for $readmemh.

    Layout: C5_N_ENTRIES entry words, then the quarter-turn endpoint entry
    at index C5_N_ENTRIES (the DUT's endpoint branch reads it there).
    """
    lines = ["%06x" % entry for entry in table.entries]
    lines.append("%06x" % table.endpoint)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def derive_anchor_control_words(vector: dict, formats: AcceptedFormats):
    """Derive the DUT control words from the vector's own declared data.

    The declared binary64 shadow sites are replayed host-side here (the
    midi->Hz exp2 is DR-0008's declared open approximation item — this is
    a harness input derivation, not an RTL claim). Everything consumed is
    the vector's own declared scalar trace and parameters:

    - the phase-increment word: m1 = the vector's ``keyboard.midi_f0``
      scalar word (the anchor case's tuning/mod_depth entry-quantize to
      zero, asserted below), then the declared shadow policy
      ``hz = 440 * 2^((m/2^21 - 69)/12)`` -> Q16.15 word -> half-even
      increment over the C2 unit circle;
    - the mixer-level word: the C6/S1 entry quantization of the vector's
      ``mixer.vco_1`` parameter into the accepted C1 audio format.
    """
    counters = StickyCounters()
    for name in ("vco_1.tuning", "vco_1.mod_depth"):
        word = entry_quantize(
            float(vector["parameters"][name]), formats.midi, counters,
            "tb.anchor.entry:" + name,
        )
        if word != 0:
            raise SystemExit(
                "anchor derivation requires %s to entry-quantize to zero; "
                "got %d (not the zero-modulation case)" % (name, word)
            )
    midi_word = None
    for trace in vector["traces"]:
        if trace["name"] == "keyboard.midi_f0":
            midi_word = trace["values"][0]
    if midi_word is None:
        raise SystemExit("anchor vector carries no keyboard.midi_f0 scalar")

    midi_scale = float(formats.midi.scale)
    sample_rate_hz = int(vector["clip"]["sample_rate_hz"])
    frequency_scale = int(formats.frequency.scale)
    # Declared shadow site (binary64 on the quantized control word).
    hz = 440.0 * math.exp2((midi_word / midi_scale - 69.0) / 12.0)
    freq_word = shadow_half_even(hz * float(frequency_scale))
    phase_step = div_round(
        freq_word * formats.phase_units_per_turn,
        frequency_scale * sample_rate_hz,
        RoundingMode.HALF_EVEN,
    )
    level_word = entry_quantize(
        float(vector["parameters"]["mixer.vco_1"]), formats.audio, counters,
        "tb.anchor.entry:mixer.vco_1",
    )
    return phase_step, level_word


def anchor_mirror(formats: AcceptedFormats, phase_step: int, level_word: int,
                  sample_count: int):
    """Replay the DUT's integer dataflow with the model's own primitives.

    Uses the landed fixedpoint ops (half-even, saturate) and the accepted
    hash-linked table, so a match against the frozen vector trace is a
    model-primitive match, not a harness-parallel implementation.
    """
    counters = StickyCounters()
    audio = formats.audio
    entry_fmt = formats.table.spec.entry_format
    modulus = 1 << formats.phase_width
    phase = 0
    vco_words = []
    mix_words = []
    for _ in range(sample_count):
        phase = (phase + phase_step) % modulus
        entry = formats.table.evaluate(phase, formats.mode)
        vco = rescale(entry, entry_fmt, audio, formats.mode,
                      OverflowPolicy.SATURATE, counters, "tb.anchor.vco")
        mix = mul(vco, audio, level_word, audio, audio, formats.mode,
                  OverflowPolicy.SATURATE, counters, "tb.anchor.mix")
        vco_words.append(vco)
        mix_words.append(mix)
    return vco_words, mix_words


def simulate_anchor(workdir: Path, simulator: str, phase_step: int,
                    level_word: int, sample_count: int, lut_memh: Path):
    """Compile the constants package + format-true DUT and capture streams."""
    stim = workdir / "stimulus.txt"
    captured = workdir / "captured.txt"
    stim.write_text(
        "%d\n%d\n%d\n" % (phase_step, level_word, sample_count),
        encoding="utf-8",
    )
    if simulator == "iverilog":
        vvp = workdir / "lut_sine.vvp"
        _run(
            [
                "iverilog", "-g2012", "-o", str(vvp),
                str(CONSTANTS_PKG_SV), str(ANCHOR_DUT_SV), str(ANCHOR_TB_SV),
            ],
            cwd=workdir,
        )
        _run(
            ["vvp", "-n", str(vvp), "+lut=%s" % lut_memh],
            cwd=workdir,
        )
    else:
        raise SystemExit(
            "simulator %r is not wired up; this runner is PDK-free and "
            "currently supports iverilog" % simulator
        )
    vco_words = []
    mix_words = []
    for line in captured.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        v_word, m_word = line.split()
        vco_words.append(int(v_word))
        mix_words.append(int(m_word))
    return vco_words, mix_words


def anchor(workdir: Path, simulator: str) -> int:
    try:
        formats = AcceptedFormats()
    except ChoiceNotAccepted as error:
        print("ANCHOR REFUSED: accepted register refused: %s" % error)
        return 1

    vector = gv.load_vector(gv.SENTINEL_VECTOR_PATH)
    print(
        "Loaded real golden vector %s (%d traces, content_hash verified)"
        % (gv.SENTINEL_VECTOR_PATH.name, len(vector["traces"]))
    )

    verified = gv.verify_accepted_contract(vector)
    print(
        "Accepted-contract binding verified (dr_0008=%(dr_0008_record_sha256)s\n"
        "  register=%(choices_register_sha256)s\n"
        "  package=%(constants_package_sha256)s\n"
        "  record digest source: %(dr_0008_record_source)s)" % verified
    )

    table = formats.table
    declared_lut = vector["provenance"].get("lut_sha256")
    live_lut = table.sha256()
    if declared_lut != live_lut:
        print(
            "ANCHOR REFUSED: accepted quarter-wave table hashes to %s but "
            "the vector binds %s" % (live_lut, declared_lut)
        )
        return 1
    print("Accepted LUT table digest verified: %s" % live_lut)

    sample_count = int(vector["clip"]["sample_count"])
    phase_step, level_word = derive_anchor_control_words(vector, formats)
    print(
        "Derived control words: phase_step=%d level=%d over %d samples "
        "(shadow-site replay, host-side)" % (phase_step, level_word, sample_count)
    )

    lut_memh = workdir / "lut_quarter_cos.memh"
    write_lut_memh(table, lut_memh)
    captured_vco, captured_mix = simulate_anchor(
        workdir, simulator, phase_step, level_word, sample_count, lut_memh
    )
    if len(captured_vco) != sample_count or len(captured_mix) != sample_count:
        print(
            "ANCHOR FAILED: captured %d/%d streams, expected %d samples"
            % (len(captured_vco), len(captured_mix), sample_count)
        )
        return 1

    focus = focus_vector(vector, ANCHOR_TRACE)
    expected_vco = focus["traces"][0]["values"]

    ok = True

    mirror_vco, mirror_mix = anchor_mirror(
        formats, phase_step, level_word, sample_count
    )
    mirror_mismatch = gv.first_mismatch(focus, {ANCHOR_TRACE: mirror_vco})
    if mirror_mismatch is not None:
        print("ANCHOR FAILED: harness mirror disagrees with the frozen vector:")
        print(gv.format_mismatch(mirror_mismatch))
        ok = False
    else:
        print(
            "Vector-truth path verified: model-primitive mirror reproduces "
            "the frozen %s trace over all %d samples."
            % (ANCHOR_TRACE, sample_count)
        )

    rtl_mismatch = gv.first_mismatch(focus, {ANCHOR_TRACE: captured_vco})
    if rtl_mismatch is not None:
        print("ANCHOR FAILED: RTL capture disagrees with the frozen vector:")
        print(gv.format_mismatch(rtl_mismatch))
        ok = False
    else:
        print(
            "RTL path verified: format-true DUT reproduces the frozen %s "
            "trace over all %d samples." % (ANCHOR_TRACE, sample_count)
        )

    # The mixer-level product stream is a derived DUT stream (the vector's
    # mixer.output additionally carries the control-path VCA column), so it
    # is checked against the model-primitive mirror, not the vector.
    derived_trace = "derived.vco_1.raw*mixer.vco_1.level"
    for index, (expected, actual) in enumerate(zip(mirror_mix, captured_mix)):
        if expected != actual:
            print("ANCHOR FAILED: derived mixer-level product stream diverges:")
            print(
                gv.format_mismatch(
                    gv.Mismatch(
                        cycle=index, sample=index, trace=derived_trace,
                        expected=expected, actual=actual,
                    )
                )
            )
            ok = False
            break
    else:
        print(
            "Derived path verified: mixer-level product matches the "
            "model-primitive mirror over all %d samples." % sample_count
        )

    planted_value = (expected_vco[ANCHOR_FAIL_INDEX] ^ 1) & 0xFFFFFF
    faulted = list(captured_vco)
    faulted[ANCHOR_FAIL_INDEX] = planted_value
    planted = gv.Mismatch(
        cycle=ANCHOR_FAIL_INDEX,
        sample=ANCHOR_FAIL_INDEX,
        trace=ANCHOR_TRACE,
        expected=expected_vco[ANCHOR_FAIL_INDEX],
        actual=planted_value,
    )
    observed = gv.first_mismatch(focus, {ANCHOR_TRACE: faulted})
    if observed != planted:
        print(
            "ANCHOR FAILED: reporter did not name the planted fault on the "
            "real vector; observed %r" % (observed,)
        )
        ok = False
    else:
        print("Intentional-fail path verified on the real vector; reporter reports:")
        print(gv.format_mismatch(observed))

    if not ok:
        print("ANCHOR RUN FAILED")
        return 1
    print("ANCHOR RUN PASSED (contract binding + vector truth + RTL sample-exact + fail path)")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        nargs="?",
        default="selftest",
        choices=["selftest", "anchor"],
        help="'selftest' proves the harness; 'anchor' runs the real "
        "golden-vector flow through the format-true DUT",
    )
    parser.add_argument(
        "--simulator",
        default="iverilog",
        choices=["iverilog"],
        help="open-source simulator (PDK-free)",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=None,
        help="keep artifacts here instead of a temp dir",
    )
    args = parser.parse_args(argv)

    if shutil.which(args.simulator) is None:
        print(
            "ERROR: %s is not installed; the %s run cannot execute here. "
            "CI (.github/workflows/tb-sim.yml) arbitrates on a host that "
            "installs it. An unrun check is never a pass."
            % (args.simulator, args.command)
        )
        return 3

    command = selftest if args.command == "selftest" else anchor

    if args.workdir is not None:
        workdir = args.workdir
        workdir.mkdir(parents=True, exist_ok=True)
        return command(workdir, args.simulator)

    with tempfile.TemporaryDirectory(prefix="tb-%s-" % args.command) as tmp:
        return command(Path(tmp), args.simulator)


if __name__ == "__main__":
    sys.exit(main())
