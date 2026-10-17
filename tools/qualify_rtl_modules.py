#!/usr/bin/env python3
"""Aggregate RTL-module conformance regression, diagnostic gate and record (issue #78).

Issues #69-#77 each landed their own bit-exact per-module conformance flow as
a ``tb/run_tb.py`` lane, and ``.github/workflows/tb-sim.yml`` runs every lane
individually. What none of them owns is the *aggregation and gating* layer
this module provides:

1. **One bounded regression.** ``--lanes`` runs a declared set of
   ``tb/run_tb.py`` lanes through ``tools/run_fast_tests.py``'s existing
   returncode-driven aggregation (AND-of-zero, never a stdout substring) and
   reports one overall verdict for the set.
2. **A diagnostic gate with an explicit waiver ledger.** ``--lint`` compiles
   every lane's own source set under ``verilator --lint-only -Wall`` and
   ``iverilog -g2012 -Wall``, classifies each diagnostic, and fails on any
   diagnostic that is not covered, *by count*, by a justified waiver in
   ``tb/rtl-lint-baseline.json``. The same classifier is applied to each
   lane's captured transcript for runtime diagnostics. This is a ratchet:
   the currently-known diagnostics are waived at their present counts with a
   stated reason, and any new or increased diagnostic is a failure.
3. **Coverage reporting with declared, justified exclusions.** Per lane, the
   functional obligations and negative-control (planted mutation) count that
   lane must demonstrate are declared in ``COVERAGE_MODEL`` and checked
   against what the lane actually printed. Code (line/toggle) coverage and
   SVA assertion coverage are declared, justified exclusions -- neither
   Icarus Verilog nor the Verilator lint pass collects them, and adding a
   coverage-instrumented second build of every testbench is out of this
   layer's scope. See ``spec/RTL-MODULE-QUALIFICATION.md``.
4. **One indexed evidence record.** ``--record`` writes a
   ``spec/schemas/capability-evidence-v1.schema.json``-conformant record for
   the ``rtl-modules`` capability node: the aggregate transcript's path and
   SHA-256, every covered input's SHA-256 (the vector/source hashes), the
   registered check's command and exit code, and the required
   ``one-bit-mutation`` negative control's observed outcome.

The record is deliberately **not** stamped into ``spec/capabilities-v1.json``'s
``rtl-modules.evidence`` pointer yet: that node depends on ``fixed-model``,
which has no evidence, so an attached record would evaluate BLOCKED and
unhealthy under ``tools/compile_capabilities.py --strict``. Registering
``rtl-module-qualification-v1`` in ``capabilities.CHECKS`` moves the node from
NOT RUN ("planned check is not registered") to READY, which is the honest
state. ``spec/RTL-MODULE-QUALIFICATION.md`` records the attachment
precondition.

Usage::

    python3 tools/qualify_rtl_modules.py --lint                  # fast gate
    python3 tools/qualify_rtl_modules.py --lanes fast            # bounded set
    python3 tools/qualify_rtl_modules.py --lanes all --record out/rec.json
    python3 tools/qualify_rtl_modules.py --lint --update-baseline   # CI toolchain only
    python3 tools/qualify_rtl_modules.py --lint --integrated all \\
        --integrated-artifacts out/integrated-artifacts \\
        --producer-result oneshot-tail-chain=success \\
        --producer-result oneshot-whole-voice=success

``--integrated`` folds in the standalone one-shot harnesses
(``tb/run_oneshot.py``, ``tb/run_voice.py``; issue #264) -- see the
"Integrated one-shot lanes" section of ``spec/RTL-MODULE-QUALIFICATION.md``.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
SV_DIR = "tb/sv"
BASELINE_PATH = "tb/rtl-lint-baseline.json"
POLICY_DOC = "spec/RTL-MODULE-QUALIFICATION.md"
CHECK_NAME = "rtl-module-qualification-v1"
NODE_ID = "rtl-modules"

PKG = "gf180_rtl_constants_pkg.sv"

#: Lane names, in the exact order ``tb/run_tb.py``'s ``command`` argument
#: declares them. ``tests/test_rtl_module_qualification.py`` re-derives this
#: from ``tb/run_tb.py``'s own ``choices=[...]`` list and fails if it drifts,
#: so a newly added lane cannot silently escape this gate.
LANES: tuple[str, ...] = (
    "selftest", "anchor", "adsr", "patch", "lfo", "modmatrix",
    "vco", "vco2", "noise", "mix", "normreplay",
)

#: The lanes CI runs as the *bounded* aggregate regression (AC6). Measured
#: wall-clock on this repo's own hosts is minutes, not hours; the long lanes
#: (modmatrix/vco/vco2/noise/mix) keep their own per-lane tb-sim.yml steps
#: with their own timeouts rather than being folded into one job.
FAST_LANES: tuple[str, ...] = ("selftest", "adsr", "patch", "lfo", "normreplay")

#: Lint units, mirroring the exact source sets ``tb/run_tb.py`` hands to
#: ``iverilog`` per lane. ``normreplay-binding`` is the second compile the
#: normreplay lane performs (issue #211's ``render_binding_top`` integration),
#: which is why lint units are keyed separately from lanes.
LINT_UNITS: dict[str, tuple[str, ...]] = {
    "selftest": ("synth_dut.sv", "tb_synth_dut.sv"),
    "anchor": (PKG, "lut_sine_dut.sv", "tb_lut_sine_dut.sv"),
    "adsr": (PKG, "adsr_engine.sv", "tb_adsr_engine.sv"),
    "patch": (PKG, "patch_control.sv", "tb_patch_control.sv"),
    "lfo": (PKG, "lfo_vca_engine.sv", "tb_lfo_vca_engine.sv"),
    "modmatrix": (
        PKG, "mod_matrix_engine.sv", "upsample_engine.sv",
        "tb_mod_matrix_upsample.sv",
    ),
    "vco": (PKG, "sine_vco_engine.sv", "tb_sine_vco_engine.sv"),
    "vco2": (
        PKG, "quarter_wave_lut.sv", "square_saw_vco_engine.sv",
        "tb_square_saw_vco.sv",
    ),
    "noise": (PKG, "noise_stream_dut.sv", "tb_noise_stream_dut.sv"),
    "mix": (PKG, "audio_mix_engine.sv", "tb_audio_mix_engine.sv"),
    "normreplay": (
        PKG, "normalization_replay_engine.sv",
        "tb_normalization_replay_engine.sv",
    ),
    "normreplay-binding": (
        PKG, "patch_control.sv", "normalization_replay_engine.sv",
        "render_binding_top.sv", "tb_render_binding.sv",
    ),
}

#: Lane -> lint units that cover it. Every lane must be covered.
LANE_LINT_UNITS: dict[str, tuple[str, ...]] = {
    lane: (("normreplay", "normreplay-binding") if lane == "normreplay" else (lane,))
    for lane in LANES
}


@dataclass(frozen=True)
class LaneObligations:
    """What one lane must demonstrate, and how much of it."""

    issue: int
    #: Conformance axes from the issue's own acceptance criteria that this
    #: lane's declared cases carry.
    axes: tuple[str, ...]
    #: Minimum number of planted mutations the lane must report as DETECTED.
    #: Baselined from an actual clean run; a lane that stops proving its own
    #: mutations fails the gate instead of quietly shrinking.
    min_mutations: int
    #: Minimum number of ``-> OK`` case/fixture verdicts the lane must print.
    min_cases: int


#: The declared conformance axes (issue #78 AC2). Every axis must be claimed
#: by at least one lane; ``tests/test_rtl_module_qualification.py`` enforces it.
AXES: tuple[str, ...] = (
    "reset-replay", "min-max", "ties", "overflow-saturation", "discrete-modes",
)

COVERAGE_MODEL: dict[str, LaneObligations] = {
    "selftest": LaneObligations(
        68, ("reset-replay",), min_mutations=0, min_cases=0),
    "anchor": LaneObligations(
        68, ("min-max",), min_mutations=1, min_cases=0),
    "adsr": LaneObligations(
        70, ("reset-replay", "min-max", "ties", "discrete-modes"),
        min_mutations=3, min_cases=6),
    "patch": LaneObligations(
        69, ("reset-replay", "discrete-modes"), min_mutations=1, min_cases=1),
    "lfo": LaneObligations(
        71, ("reset-replay", "min-max", "ties", "discrete-modes"),
        min_mutations=1, min_cases=1),
    "modmatrix": LaneObligations(
        72, ("reset-replay", "min-max", "overflow-saturation"),
        min_mutations=1, min_cases=1),
    "vco": LaneObligations(
        73, ("min-max", "ties", "overflow-saturation"),
        min_mutations=1, min_cases=1),
    "vco2": LaneObligations(
        74, ("min-max", "ties", "discrete-modes", "overflow-saturation"),
        min_mutations=1, min_cases=1),
    "noise": LaneObligations(
        75, ("reset-replay", "min-max"), min_mutations=1, min_cases=1),
    "mix": LaneObligations(
        76, ("reset-replay", "min-max", "overflow-saturation"),
        min_mutations=1, min_cases=1),
    "normreplay": LaneObligations(
        77, ("reset-replay", "min-max", "ties", "overflow-saturation"),
        min_mutations=1, min_cases=1),
}

#: Declared, justified coverage exclusions reported in the evidence record.
#: These are the AC3 "justified exclusions", stated once, here and in
#: ``spec/RTL-MODULE-QUALIFICATION.md``.
COVERAGE_EXCLUSIONS: dict[str, str] = {
    "code-coverage": (
        "Icarus Verilog exposes no line/toggle/branch coverage instrument "
        "(iverilog 13.0 -h lists no coverage flag), and the lanes' bit-exact "
        "comparisons run under Icarus. Collecting Verilator --coverage would "
        "require a second, separately-elaborated build of every testbench "
        "whose numbers would not describe the simulator that arbitrates "
        "conformance. Excluded; functional and negative-control coverage are "
        "reported instead."
    ),
    "assertion-coverage": (
        "The testbenches carry no SVA cover properties; their checks are "
        "procedural bit-exact comparisons plus exported op-counter equality. "
        "Assertion coverage is therefore reported as negative-control "
        "coverage -- every planted mutation a lane declares must be observed "
        "DETECTED -- not as SVA cover-point percentages."
    ),
    "width-sign-diagnostics-at-current-count": (
        "Verilator's WIDTHTRUNC/WIDTHEXPAND diagnostics on the landed engines "
        "describe the DR-0006 arithmetic profile's deliberate widening and "
        "narrowing of intermediate products. Silencing them would change "
        "declared arithmetic, which spec/ owns and which needs a decision "
        "record, not an RTL edit from a verification-tooling change. They are "
        "waived at their committed counts in tb/rtl-lint-baseline.json; any "
        "new or increased width/sign diagnostic fails the gate."
    ),
}

# --------------------------------------------------------------------------
# Diagnostic classification
# --------------------------------------------------------------------------

#: Verilator/Icarus diagnostic classes excluded from the gate entirely, each
#: with the reason it is excluded. A class absent from this map is GATED --
#: the gate is fail-closed, so a Verilator release that adds a new diagnostic
#: class surfaces as a gate failure rather than as silence.
EXCLUDED_CLASSES: dict[str, str] = {
    "UNUSEDPARAM": (
        "every engine imports the single generated gf180_rtl_constants "
        "package wholesale and uses a subset; the package is the "
        "refusal-gated single source of truth (tools/generate_rtl_constants.py)"
    ),
    "UNUSEDSIGNAL": (
        "testbench scaffolding and exported op-counter taps are read by the "
        "Python side through files, not by the SV hierarchy"
    ),
    "DECLFILENAME": (
        "gf180_rtl_constants_pkg.sv deliberately carries the _pkg suffix; "
        "renaming it would churn every lane's compile list"
    ),
    "TIMESCALEMOD": (
        "the constants package declares no timescale because a package has "
        "no time behaviour; Icarus reports the same thing under -Wtimescale"
    ),
    "IMPORTSTAR": (
        "import gf180_rtl_constants::* at $unit scope is the declared "
        "convention for the generated constants package"
    ),
    "PROCASSINIT": (
        "testbench initial blocks seed variables the stimulus loop then "
        "drives; this is bench scaffolding, not synthesised logic"
    ),
    "PINCONNECTEMPTY": (
        "render_binding_top deliberately leaves declared-but-unused ports "
        "unconnected so the wiring seam issue #211 mutates stays visible"
    ),
    "VARHIDDEN": (
        "loop variables shadowed inside testbench generate/for scopes"
    ),
    "IVERILOG-TIMESCALE": (
        "Icarus's own no-explicit-time-unit report for the constants "
        "package; the Verilator counterpart is TIMESCALEMOD above"
    ),
}

#: Default waiver reasons, keyed by diagnostic class, used to fill the ledger
#: on ``--update-baseline``. A ledger entry whose reason is UNJUSTIFIED fails
#: the gate: "explicitly waived" means a human wrote down why.
UNJUSTIFIED = "UNJUSTIFIED -- review this diagnostic and state why it is waived"

WAIVER_REASONS: dict[str, str] = {
    "WIDTHTRUNC": COVERAGE_EXCLUSIONS["width-sign-diagnostics-at-current-count"],
    "WIDTHEXPAND": COVERAGE_EXCLUSIONS["width-sign-diagnostics-at-current-count"],
    "BLKSEQ": (
        "blocking assignments inside sequential blocks are used where the "
        "fixed model's own evaluation order is being mirrored intra-cycle; "
        "the lanes' bit-exact comparison is the arbiter of that order"
    ),
    "MULTIDRIVENPROC": (
        "patch_control's register file is written from more than one "
        "procedural block by design (host writes vs. reset defaults); the "
        "bit-exact lane plus issue #203's resync-count assertion cover it"
    ),
    "IVERILOG-SENSITIVITY": (
        "Icarus reports that an @* block is sensitive to every word of an "
        "array; that whole-array sensitivity is what the decode is supposed "
        "to have, and the lane's cycle-exact Python mirror arbitrates it"
    ),
    "BLKANDNBLK": (
        "Verilator 5.020 (Ubuntu 24.04's apt package, CI's installed "
        "version) reports 'Unsupported: Blocked and non-blocking "
        "assignments to same variable' for patch_control.sv's testbench-"
        "side register mirror (c_entry_off/c_stream_len/c_kpad/c_total/"
        "c_slot/c_entry_len), driven procedurally by the bench rather than "
        "the DUT; Verilator 5.052 lints the identical source without this "
        "diagnostic. This is a lint-front-end version limitation, not an "
        "RTL defect -- Icarus Verilog (this repo's bit-exact arbiter) "
        "elaborates and simulates it correctly, and the patch/normreplay "
        "lanes both pass sample-exactly on the committed tree"
    ),
    "BLKLOOPINIT": (
        "Verilator 5.020 reports 'Unsupported: Delayed assignment to array "
        "inside for loops' for patch_control.sv; Verilator 5.052 supports "
        "the identical construct. Same lint-front-end version-limitation "
        "reasoning as BLKANDNBLK above -- Icarus and the bit-exact "
        "patch/normreplay lanes are the arbiters and both pass"
    ),
    "INITIALDLY": (
        "tb_patch_control.sv / tb_render_binding.sv seed bench-driven "
        "mirror state with non-blocking assignments inside initial/final "
        "blocks -- ordinary testbench-scaffolding idiom, not synthesised "
        "RTL; Verilator 5.020 flags it under -Wall, Verilator 5.052 does "
        "not report it for this source"
    ),
    "VERILATOR-EXIT": (
        "Verilator 5.020 exits non-zero on patch_control.sv's BLKANDNBLK/"
        "BLKLOOPINIT unsupported-construct errors above even under "
        "-Wno-fatal (those are %Error-class diagnostics, not %Warning); "
        "the exit itself is lint_unit()'s own synthetic marker for that, "
        "waived alongside the errors that cause it for the same "
        "lint-front-end version-limitation reason -- Icarus and the "
        "bit-exact lanes are unaffected and both pass"
    ),
    "TB-WARN": (
        "tb_adsr_engine.sv:194 / tb_lfo_vca_engine.sv:181 print TB-WARN when "
        "out_valid stays low, which is exactly what the planted off-by-one "
        "timing mutation makes happen; these lines come from the lane's own "
        "negative-control runs, whose failure is the point"
    ),
    "SIM-WARNING": (
        "the observed instances are Icarus's own runtime "
        "'WARNING: <file>:<line>: $readmemh(...): Not enough words in the "
        "file for the requested range' report: tb_normalization_replay_"
        "engine.sv declares its mix-stream buffers at the canonical "
        "176,400-sample clip length regardless of how long the loaded "
        "directed/mutation vector actually is, so $readmemh legitimately "
        "leaves the untouched tail at its declared default. The lane's own "
        "bit-exact comparison only reads the vector's declared sample "
        "range, so the truncation report describes expected per-vector "
        "framing, not a defect. The same pattern occurs in the integrated "
        "whole-voice lane (issue #264): tb_one_shot_voice.sv sizes its "
        "host-replayed stream buffers (fq1/fq2/sqq/lqq) at "
        "SCHED_SAMPLES_PER_PASS = 176,400 words and its C8 noise-byte "
        "buffer at 4x that, while the regression profile's prefix-capped "
        "simulations (binding baseline, capped mutants, replay pair) load "
        "shorter files; the bench refuses a walk longer than the buffer "
        "(WALK > AUDIO_MAX fails) and only reads indices below the "
        "declared walk / byte count. Icarus's classifier bucket for a "
        "'warning:'-prefixed line is this generic class rather than the "
        "dedicated READMEM class (see RUNTIME_PATTERNS's match order); "
        "waived at its committed per-lane count, so any new or increased "
        "occurrence -- including one that is NOT this readmemh pattern -- "
        "still gates."
    ),
    "SYNCASYNCNET": (
        "issue #264's integrated one-shot benches (tb_one_shot_tail.sv, "
        "tb_one_shot_voice.sv) drive a single bench reg 'rst' into engines "
        "that were each landed and lane-qualified with their own reset "
        "style: audio_mix_engine / mod_matrix_engine / upsample_engine / "
        "sine_vco_engine / square_saw_vco_engine reset asynchronously "
        "(posedge rst), adsr_engine / lfo_vca_engine / noise_stream_dut / "
        "normalization_replay_engine synchronously. Composing them makes "
        "Verilator report the shared net as flopped both ways. In "
        "simulation the bench asserts rst from time zero and releases it "
        "once, on a falling clock edge, so there is no reset-release race "
        "for the bit-exact comparison to miss. Whether a taped-out top "
        "needs one reset style (and reset synchronisers) is an "
        "implementation/synthesis question this verification waiver does "
        "not answer and does not claim to"
    ),
}

#: Runtime diagnostic patterns scanned over each lane's captured transcript.
#: Keyed by class; the gate treats a match as a diagnostic that must be
#: covered by a justified waiver in the ledger.
RUNTIME_PATTERNS: tuple[tuple[str, str], ...] = (
    ("TB-WARN", r"^TB-WARN\b"),
    ("SIM-WARNING", r"(?:^|\s)(?:warning|WARNING):"),
    ("SIM-ERROR", r"(?:^|\s)(?:%Error|ERROR):"),
    ("XZ-STATE", r"(?i)\b(?:x-state|z-state|is ambiguous|unknown value)\b"),
    ("READMEM", r"\$readmem[hb]\b[^\n]*(?:Not enough|Extra|inconsistency)"),
)

#: Transcript lines that are never scanned: ``tb/run_tb.py``'s ``_run()``
#: echoes the full iverilog/vvp command line, which embeds absolute temp
#: paths and would otherwise match SIM-* patterns by accident.
TRANSCRIPT_SKIP = re.compile(r"^\+ ")

_VERILATOR_LINE = re.compile(
    r"^%(?P<severity>Warning|Error)(?:-(?P<code>[A-Z0-9_]+))?:\s*"
    r"(?:(?P<file>[^\s:]+):(?P<line>\d+):(?P<col>\d+):\s*)?(?P<message>.*)$"
)
_IVERILOG_LINE = re.compile(
    r"^(?:(?P<file>[^\s:]+):(?P<line>\d+):\s*)?warning:\s*(?P<message>.*)$"
)
#: Icarus warning text -> class. Anything unmatched becomes IVERILOG-OTHER,
#: which has no declared exclusion and therefore gates.
_IVERILOG_CLASSES: tuple[tuple[str, str], ...] = (
    ("IVERILOG-TIMESCALE", r"no explicit time unit|time precision"),
    ("IVERILOG-IMPLICIT", r"implicitly defining"),
    ("IVERILOG-SENSITIVITY", r"@\*? ?is sensitive to"),
    ("IVERILOG-PORTBIND", r"port .* of "),
    ("IVERILOG-SELRANGE", r"out of (?:bounds|range)|part select"),
)

_DIGITS = re.compile(r"\d+")
_ABSPATH = re.compile(r"(?:/[A-Za-z0-9_.+-]+)+/")


@dataclass(frozen=True)
class Diagnostic:
    """One classified diagnostic, reduced to a stable ledger key."""

    tier: str      # "lint" or "runtime"
    tool: str      # "verilator", "iverilog" or "lane"
    where: str     # repo-relative source path, or the lane name
    code: str      # diagnostic class
    message: str   # normalised message text, for the ledger's own record
    unit: str      # the lint unit or lane this diagnostic was observed in

    @property
    def key(self) -> str:
        return "%s|%s|%s" % (self.tool, self.where, self.code)

    @property
    def excluded(self) -> bool:
        return self.code in EXCLUDED_CLASSES


def normalise_message(text: str) -> str:
    """Strip absolute paths and digits so a message is a stable fingerprint."""

    return _DIGITS.sub("N", _ABSPATH.sub("", text)).strip()[:200]


def _relative(path: str, root: Path) -> str:
    candidate = Path(path)
    try:
        if candidate.is_absolute():
            return candidate.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return candidate.name
    # verilator reports paths as given on the command line; we always pass
    # repo-relative paths, so a relative path is already the answer.
    return candidate.as_posix()


def parse_verilator(output: str, root: Path, unit: str) -> list[Diagnostic]:
    found: list[Diagnostic] = []
    for line in output.splitlines():
        match = _VERILATOR_LINE.match(line)
        if match is None:
            continue
        code = match.group("code")
        message = match.group("message")
        if code is None:
            # "%Error: Exiting due to N warning(s)" is a run summary, not a
            # diagnostic; a codeless %Error with any other text is real.
            if "Exiting due to" in message:
                continue
            code = "VERILATOR-ERROR"
        where = (
            _relative(match.group("file"), root)
            if match.group("file")
            else "%s/%s" % (SV_DIR, unit)
        )
        found.append(
            Diagnostic(
                "lint", "verilator", where, code, normalise_message(message), unit
            )
        )
    return found


def parse_iverilog(output: str, root: Path, unit: str) -> list[Diagnostic]:
    found: list[Diagnostic] = []
    for line in output.splitlines():
        match = _IVERILOG_LINE.match(line)
        if match is None:
            continue
        message = match.group("message")
        code = "IVERILOG-OTHER"
        for name, pattern in _IVERILOG_CLASSES:
            if re.search(pattern, message):
                code = name
                break
        where = (
            _relative(match.group("file"), root)
            if match.group("file")
            else "%s/%s" % (SV_DIR, unit)
        )
        found.append(
            Diagnostic(
                "lint", "iverilog", where, code, normalise_message(message), unit
            )
        )
    return found


def scan_transcript(lane: str, transcript: str) -> list[Diagnostic]:
    """Classify runtime diagnostics in one lane's captured output."""

    found: list[Diagnostic] = []
    for line in transcript.splitlines():
        if TRANSCRIPT_SKIP.match(line):
            continue
        for code, pattern in RUNTIME_PATTERNS:
            if re.search(pattern, line):
                found.append(
                    Diagnostic(
                        "runtime", "lane", lane, code, normalise_message(line), lane
                    )
                )
                break
    return found


# --------------------------------------------------------------------------
# Lint execution
# --------------------------------------------------------------------------


def _sources(unit: str, root: Path) -> list[str]:
    names = LINT_UNITS[unit] if unit in LINT_UNITS else INTEGRATED_LINT_UNITS[unit]
    return ["%s/%s" % (SV_DIR, name) for name in names]


def lint_unit(unit: str, root: Path) -> tuple[list[Diagnostic], list[str]]:
    """Lint one unit with every available linter. Returns (diags, transcript)."""

    diagnostics: list[Diagnostic] = []
    transcript: list[str] = []
    sources = _sources(unit, root)
    missing = [name for name in sources if not (root / name).is_file()]
    if missing:
        raise SystemExit(
            "ERROR: lint unit %r names missing sources: %s"
            % (unit, ", ".join(missing))
        )
    if shutil.which("verilator"):
        command = [
            "verilator", "--lint-only", "-Wall", "-Wno-fatal", "--timing",
            "-sv", "-I%s" % SV_DIR, *sources,
        ]
        completed = subprocess.run(
            command, cwd=root, capture_output=True, text=True
        )
        output = completed.stdout + completed.stderr
        transcript += ["+ " + " ".join(command), output]
        diagnostics += parse_verilator(output, root, unit)
        if completed.returncode != 0:
            diagnostics.append(
                Diagnostic(
                    "lint", "verilator", "%s/%s" % (SV_DIR, unit),
                    "VERILATOR-EXIT",
                    "verilator exited %d with -Wno-fatal" % completed.returncode,
                    unit,
                )
            )
    else:
        transcript.append("verilator not installed: lint tier skipped for " + unit)
    if shutil.which("iverilog"):
        command = [
            "iverilog", "-g2012", "-Wall", "-t", "null", "-I%s" % SV_DIR, *sources,
        ]
        completed = subprocess.run(
            command, cwd=root, capture_output=True, text=True
        )
        output = completed.stdout + completed.stderr
        transcript += ["+ " + " ".join(command), output]
        diagnostics += parse_iverilog(output, root, unit)
        if completed.returncode != 0:
            diagnostics.append(
                Diagnostic(
                    "lint", "iverilog", "%s/%s" % (SV_DIR, unit), "IVERILOG-EXIT",
                    "iverilog exited %d" % completed.returncode, unit,
                )
            )
    else:
        transcript.append("iverilog not installed: lint tier skipped for " + unit)
    return diagnostics, transcript


def linters_available() -> tuple[str, ...]:
    return tuple(
        name for name in ("verilator", "iverilog") if shutil.which(name)
    )


# --------------------------------------------------------------------------
# The waiver ledger (ratchet)
# --------------------------------------------------------------------------


def tally(diagnostics: Iterable[Diagnostic]) -> dict[str, dict]:
    """Reduce diagnostics to ``{key: {"count": n, "code": c, ...}}``.

    The count is the **worst single unit's** count, not the sum across units.
    A shared source (``gf180_rtl_constants_pkg.sv``, ``patch_control.sv``) is
    compiled by several lint units, so summing would make the committed
    counts depend on how many units happen to include a file -- adding a lint
    unit would then fail the ratchet for sources nobody touched.
    """

    per_unit: dict[tuple[str, str], int] = {}
    meta: dict[str, Diagnostic] = {}
    for diagnostic in diagnostics:
        if diagnostic.excluded:
            continue
        slot = (diagnostic.unit, diagnostic.key)
        per_unit[slot] = per_unit.get(slot, 0) + 1
        meta.setdefault(diagnostic.key, diagnostic)
    result: dict[str, dict] = {}
    for (unit, key), count in sorted(per_unit.items()):
        entry = result.setdefault(
            key,
            {
                "count": 0,
                "code": meta[key].code,
                "tier": meta[key].tier,
                "example": meta[key].message,
                "units": [],
            },
        )
        entry["count"] = max(entry["count"], count)
        entry["units"].append(unit)
    return result


def load_baseline(root: Path) -> dict:
    path = root / BASELINE_PATH
    if not path.is_file():
        return {"schema_version": 1, "waivers": {}}
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _waiver(entry: dict) -> dict:
    return {
        "count": entry["count"],
        "tier": entry["tier"],
        "units": sorted(set(entry.get("units", []))),
        "reason": WAIVER_REASONS.get(entry["code"], UNJUSTIFIED),
        "example": entry["example"],
    }


def build_baseline(
    observed: dict[str, dict],
    previous: dict,
    *,
    tiers: Sequence[str],
    provenance: Optional[dict] = None,
    integrated: Optional[dict] = None,
    runtime_units: Optional[Sequence[str]] = None,
) -> dict:
    """Rewrite the ledger for the tiers that ran, keeping the others intact.

    An entry for a tier this run did not execute is preserved verbatim: a
    ``--lint --update-baseline`` run must never delete the runtime tier's
    waivers just because it did not run any lanes.
    """

    def replaced(value: dict) -> bool:
        if value.get("tier") not in tiers:
            return False
        if value.get("tier") == "runtime" and runtime_units is not None:
            # only the lanes that actually ran are re-observed; the others'
            # runtime waivers are carried over, not silently deleted
            return bool(set(value.get("units", [])) & set(runtime_units))
        return True

    waivers = {
        key: value
        for key, value in previous.get("waivers", {}).items()
        if not replaced(value)
    }
    for key, entry in observed.items():
        if entry["tier"] in tiers and (
            entry["tier"] != "runtime" or runtime_units is None
            or set(entry.get("units", [])) & set(runtime_units)
        ):
            waivers[key] = _waiver(entry)
    result = {
        "schema_version": 1,
        "generated_by": "tools/qualify_rtl_modules.py --update-baseline",
        "policy": POLICY_DOC,
        "excluded_classes": dict(sorted(EXCLUDED_CLASSES.items())),
        "waivers": {key: waivers[key] for key in sorted(waivers)},
    }
    # Provenance and the integrated-lane registry are carried over verbatim
    # unless this run produced new ones: a lint-only update must never erase
    # what an earlier run established about the runtime tier.
    previous_provenance = previous.get("provenance")
    if provenance is not None:
        result["provenance"] = provenance
    elif previous_provenance is not None:
        result["provenance"] = previous_provenance
    registry = {
        name: dict(entry)
        for name, entry in previous.get(INTEGRATED_BASELINE_KEY, {}).items()
    }
    for name, entry in (integrated or {}).items():
        merged = dict(registry.get(name, {}))
        merged.update(entry)
        registry[name] = merged
    if registry:
        result[INTEGRATED_BASELINE_KEY] = {
            name: registry[name] for name in sorted(registry)
        }
    return result


def gate_diagnostics(
    observed: dict[str, dict], baseline: dict, *, tiers: Sequence[str]
) -> tuple[list[str], list[str]]:
    """Compare observed diagnostics against the ledger.

    Returns ``(failures, notes)``. A failure is an ungated-diagnostic
    regression: a key the ledger does not waive, a count above the waived
    count, or a waiver whose reason was never justified. A note is a
    ledger entry that is now over-stated (the tree improved) or unobserved.
    """

    waivers = baseline.get("waivers", {})
    failures: list[str] = []
    notes: list[str] = []
    for key in sorted(observed):
        entry = observed[key]
        if entry["tier"] not in tiers:
            continue
        waiver = waivers.get(key)
        if waiver is None:
            failures.append(
                "unwaived diagnostic %s x%d (%s); add a justified waiver to %s "
                "or fix the source" % (key, entry["count"], entry["example"],
                                       BASELINE_PATH)
            )
            continue
        reason = str(waiver.get("reason", ""))
        if not reason or reason.startswith("UNJUSTIFIED"):
            failures.append(
                "waiver %s has no justification; %s requires a stated reason"
                % (key, BASELINE_PATH)
            )
            continue
        allowed = int(waiver.get("count", 0))
        if entry["count"] > allowed:
            failures.append(
                "diagnostic %s increased to %d (waived at %d): %s"
                % (key, entry["count"], allowed, entry["example"])
            )
        elif entry["count"] < allowed:
            notes.append(
                "diagnostic %s improved to %d (waived at %d); lower the "
                "ledger with --update-baseline" % (key, entry["count"], allowed)
            )
    for key in sorted(waivers):
        waiver = waivers[key]
        if waiver.get("tier") not in tiers:
            continue
        if key not in observed:
            notes.append(
                "waiver %s no longer observed; remove it with --update-baseline"
                % key
            )
    return failures, notes


# --------------------------------------------------------------------------
# Lane execution and coverage
# --------------------------------------------------------------------------


def _load_run_fast_tests():
    """Import ``tools/run_fast_tests.py`` without requiring a package."""

    path = ROOT / "tools" / "run_fast_tests.py"
    spec = importlib.util.spec_from_file_location("run_fast_tests", path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise SystemExit("ERROR: cannot load %s" % path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MUTATION = re.compile(r"\bmutation\b.*?:\s*(NOT DETECTED|DETECTED)")
_VERDICT_OK = re.compile(r"->\s*OK\b")
_VERDICT_FAIL = re.compile(r"->\s*FAIL\b")


def lane_coverage(transcript: str) -> dict:
    """Observed functional/negative-control coverage for one lane."""

    detected = 0
    undetected = 0
    for match in _MUTATION.finditer(transcript):
        if match.group(1) == "DETECTED":
            detected += 1
        else:
            undetected += 1
    return {
        "cases_ok": len(_VERDICT_OK.findall(transcript)),
        "cases_failed": len(_VERDICT_FAIL.findall(transcript)),
        "mutations_detected": detected,
        "mutations_undetected": undetected,
        "tb_completions": len(re.findall(r"^TB-DONE\b", transcript, re.M)),
    }


def gate_coverage(lane: str, observed: dict) -> list[str]:
    """Check one lane's observed coverage against its declared obligations."""

    obligations = COVERAGE_MODEL[lane]
    failures: list[str] = []
    if observed["cases_failed"]:
        failures.append(
            "lane %s printed %d '-> FAIL' verdict(s)"
            % (lane, observed["cases_failed"])
        )
    if observed["mutations_undetected"]:
        failures.append(
            "lane %s reported %d NOT DETECTED mutation(s)"
            % (lane, observed["mutations_undetected"])
        )
    if observed["mutations_detected"] < obligations.min_mutations:
        failures.append(
            "lane %s demonstrated %d mutation(s), below its declared minimum "
            "of %d" % (lane, observed["mutations_detected"],
                       obligations.min_mutations)
        )
    if observed["cases_ok"] < obligations.min_cases:
        failures.append(
            "lane %s printed %d '-> OK' verdict(s), below its declared "
            "minimum of %d" % (lane, observed["cases_ok"], obligations.min_cases)
        )
    return failures


def resolve_lanes(selector: str) -> list[str]:
    if selector == "none":
        return []
    if selector == "all":
        return list(LANES)
    if selector == "fast":
        return list(FAST_LANES)
    chosen: list[str] = []
    for name in (part.strip() for part in selector.split(",")):
        if not name:
            continue
        if name not in LANES:
            raise SystemExit(
                "ERROR: unknown lane %r (choices: %s, or all/fast/none)"
                % (name, ", ".join(LANES))
            )
        if name not in chosen:
            chosen.append(name)
    if not chosen:
        raise SystemExit("ERROR: --lanes selected nothing")
    return chosen


# --------------------------------------------------------------------------
# Integrated one-shot lanes (issue #264)
# --------------------------------------------------------------------------
#
# ``tb/run_oneshot.py`` (the #76 -> #77 tail chain) and ``tb/run_voice.py`` (the
# integrated whole voice) are standalone harnesses: they are NOT ``tb/run_tb.py``
# commands, so they are deliberately kept out of ``LANES`` (whose equality with
# ``run_tb.py``'s ``choices=[...]`` is still enforced). They own their own
# conformance checks; this layer only *dispatches* them (or consumes the
# evidence record they already wrote) and *validates* that record. It never
# re-implements a simulation check and never parses their stdout for a verdict:
# the verdict is the harness exit status plus the structured evidence record,
# validated by ``tools/verify_oneshot_evidence.py``.

INTEGRATED_SCHEMA_PREFIX = "gf180-torchsynth/"


@dataclass(frozen=True)
class IntegratedLane:
    """Metadata and dispatch for one standalone one-shot harness."""

    name: str
    harness: str                    # repo-relative script
    schema: str                     # evidence ``schema`` the harness writes
    evidence_name: str              # file the harness writes under --workdir
    profiles: tuple[str, ...]       # the harness's own --profile choices
    lint_sources: tuple[str, ...]   # the exact iverilog source set, in order
    scope: str                      # what the lane does and does not cover
    timeout_seconds: int            # per-lane wall-clock budget when executed
    #: profile -> (minimum committed cases, minimum DETECTED controls). Taken
    #: from the committed evidence records and the harness's own plan.
    obligations: dict
    issue: int = 79


INTEGRATED_LANES: dict[str, IntegratedLane] = {
    "oneshot-tail-chain": IntegratedLane(
        name="oneshot-tail-chain",
        harness="tb/run_oneshot.py",
        schema=INTEGRATED_SCHEMA_PREFIX + "oneshot-tail-chain-evidence-v1",
        evidence_name="oneshot-evidence.json",
        profiles=("regression", "full"),
        lint_sources=(
            PKG, "audio_mix_engine.sv", "normalization_replay_engine.sv",
            "one_shot_tail_top.sv", "tb_one_shot_tail.sv",
        ),
        scope=(
            "tail chain only: mixer (#76) -> replay controller (#77) over "
            "HOST-FED source/amplitude streams; NOT the whole-voice top"
        ),
        timeout_seconds=150 * 60,
        obligations={"regression": (8, 10), "full": (30, 10)},
    ),
    "oneshot-whole-voice": IntegratedLane(
        name="oneshot-whole-voice",
        harness="tb/run_voice.py",
        schema=INTEGRATED_SCHEMA_PREFIX + "oneshot-whole-voice-evidence-v1",
        evidence_name="voice-evidence.json",
        profiles=("regression", "directed", "full"),
        lint_sources=(
            PKG, "adsr_engine.sv", "lfo_vca_engine.sv", "mod_matrix_engine.sv",
            "upsample_engine.sv", "quarter_wave_lut.sv", "sine_vco_engine.sv",
            "square_saw_vco_engine.sv", "noise_stream_dut.sv",
            "audio_mix_engine.sv", "normalization_replay_engine.sv",
            "one_shot_voice_top.sv", "tb_one_shot_voice.sv",
        ),
        scope=(
            "integrated whole-voice one-shot top (#70-#77); the host still "
            "supplies the ratified host-replayed shadow words, S1 entry words, "
            "C8 noise bytes and phase enables"
        ),
        timeout_seconds=120 * 60,
        obligations={"regression": (2, 13), "directed": (5, 13),
                     "full": (31, 13)},
    ),
}
INTEGRATED_NAMES: tuple[str, ...] = tuple(INTEGRATED_LANES)

#: Lint units for the integrated lanes. Keyed by lane name (a unit and its lane
#: share a name). Kept apart from ``LINT_UNITS`` so the module-lane inventory
#: invariants are untouched.
INTEGRATED_LINT_UNITS: dict[str, tuple[str, ...]] = {
    name: lane.lint_sources for name, lane in INTEGRATED_LANES.items()
}

#: The toolchain the ledger is calibrated to (``tb-sim.yml``'s
#: ``ubuntu-24.04`` apt packages). ``--update-baseline`` refuses any other
#: toolchain unless ``--allow-foreign-toolchain`` is given, because a ledger
#: generated on a different linter build shifts which diagnostics it waives.
CI_TOOLCHAIN_PREFIXES: dict[str, str] = {
    "iverilog": "Icarus Verilog version 12.",
    "verilator": "Verilator 5.020",
}

INTEGRATED_BASELINE_KEY = "integrated_lanes"


def resolve_integrated(selector: str) -> list[str]:
    if selector == "none":
        return []
    if selector == "all":
        return list(INTEGRATED_NAMES)
    chosen: list[str] = []
    for name in (part.strip() for part in selector.split(",")):
        if not name:
            continue
        if name not in INTEGRATED_LANES:
            raise SystemExit(
                "ERROR: unknown integrated lane %r (choices: %s, or all/none)"
                % (name, ", ".join(INTEGRATED_NAMES))
            )
        if name not in chosen:
            chosen.append(name)
    if not chosen:
        raise SystemExit("ERROR: --integrated selected nothing")
    return chosen


def integrated_command(
    lane: IntegratedLane, profile: str, workdir: Path
) -> list[str]:
    """The exact command that runs one standalone harness."""

    return [
        sys.executable, lane.harness, "--profile", profile,
        "--workdir", str(workdir),
    ]


def integrated_label(lane: IntegratedLane, profile: str) -> str:
    """Honest profile/scope label for one lane's result."""

    extent = (
        "full profile" if profile == "full"
        else "%s profile only (bounded; NOT full-profile qualification)"
        % profile
    )
    return "%s, %s; %s" % (lane.name, extent, lane.scope)


@dataclass
class IntegratedResult:
    """What happened to one integrated lane. ``exit_code`` None = never ran."""

    lane: str
    mode: str                         # "executed" or "artifact"
    exit_code: Optional[int]
    transcript: Optional[str]
    evidence_path: Optional[Path]
    errors: list = None               # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


def run_integrated_lane(
    lane: IntegratedLane,
    profile: str,
    root: Path,
    workdir: Path,
    *,
    which=shutil.which,
    runner=subprocess.run,
) -> IntegratedResult:
    """Dispatch one standalone harness; every non-zero path is a failure.

    Missing simulator, timeout, a non-zero exit (including the harnesses'
    own 3 = no iverilog, 4 = inconclusive corrupt capture) all leave
    ``exit_code`` non-zero or None and an explanatory error -- never a pass.
    """

    result = IntegratedResult(
        lane.name, "executed", None, None, workdir / lane.evidence_name
    )
    if which("iverilog") is None:
        result.errors.append(
            "iverilog is not installed; %s cannot execute here and an unrun "
            "lane is never a pass" % lane.name
        )
        return result
    workdir.mkdir(parents=True, exist_ok=True)
    command = integrated_command(lane, profile, workdir)
    try:
        completed = runner(
            command, cwd=root, capture_output=True, text=True,
            timeout=lane.timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        partial = error.stdout or ""
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", "replace")
        result.transcript = partial
        result.errors.append(
            "%s timed out after %ds; the run is incomplete, not a pass"
            % (lane.name, lane.timeout_seconds)
        )
        return result
    result.exit_code = completed.returncode
    result.transcript = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode != 0:
        result.errors.append(
            "%s exited %d%s" % (
                lane.name, completed.returncode,
                " (inconclusive: corrupt capture, no verdict)"
                if completed.returncode == 4 else "",
            )
        )
    return result


def consume_integrated_artifact(
    lane: IntegratedLane,
    artifacts: Path,
    producer_results: dict,
) -> IntegratedResult:
    """Bind a producer job's artifact to this run, failing closed.

    ``artifacts/<lane>/<evidence file>`` must exist and the producer job must
    have reported ``success`` -- an absent producer result is not success.
    """

    folder = artifacts / lane.name
    result = IntegratedResult(
        lane.name, "artifact", None, None, folder / lane.evidence_name
    )
    producer = producer_results.get(lane.name)
    if producer != "success":
        result.errors.append(
            "producer job for %s reported %r, not 'success'; its artifact "
            "cannot be trusted" % (lane.name, producer)
        )
        return result
    # The producer job succeeded, which is exactly the harness's exit 0.
    result.exit_code = 0
    transcript = folder / "transcript.log"
    if transcript.is_file():
        result.transcript = transcript.read_text(
            encoding="utf-8", errors="replace"
        )
    return result


def _verifier():
    tools = str(ROOT / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import verify_oneshot_evidence  # noqa: E402

    return verify_oneshot_evidence


def load_integrated_evidence(
    lane: IntegratedLane,
    path: Optional[Path],
    profile: str,
    *,
    expect_head: Optional[str],
    tree_root: Optional[Path],
) -> tuple[Optional[dict], list]:
    """Read and validate one harness evidence record. Returns (record, errors).

    Absent, unreadable, malformed, wrong-schema, wrong-profile, failed, dirty,
    stale (source digests no longer match ``tree_root``) and wrong-revision
    records are all errors. ``expect_head=None`` skips the revision binding
    and is for tests only; the CLI always binds to ``git rev-parse HEAD``.
    """

    if path is None or not path.is_file():
        return None, ["%s evidence record %s is absent" % (lane.name, path)]
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return None, ["%s evidence record is unreadable/malformed: %s"
                      % (lane.name, error)]
    if not isinstance(record, dict):
        return None, ["%s evidence record is not a JSON object" % lane.name]
    errors: list = []
    if record.get("schema") != lane.schema:
        errors.append(
            "%s evidence schema is %r, expected %r"
            % (lane.name, record.get("schema"), lane.schema)
        )
    if record.get("profile") != profile:
        errors.append(
            "%s evidence profile is %r but %r was required; a different "
            "profile's record never satisfies this run"
            % (lane.name, record.get("profile"), profile)
        )
    try:
        errors += _verifier().verify(record, expect_head, repo_root=tree_root)
    except Exception as error:  # noqa: BLE001 - a malformed record must not crash the gate
        errors.append("%s evidence record failed validation: %s"
                      % (lane.name, error))
    return record, errors


def integrated_coverage(lane: IntegratedLane, record: Optional[dict]) -> dict:
    """Counts the aggregate ledger carries, read from the harness's record."""

    if not isinstance(record, dict):
        return {}
    detected = undetected = 0
    mutations = record.get("mutations")
    if isinstance(mutations, dict):
        for value in mutations.values():
            verdict = value.get("verdict") if isinstance(value, dict) else value
            if verdict == "DETECTED":
                detected += 1
            else:
                undetected += 1
    branches = record.get("branch_coverage")
    branches = branches if isinstance(branches, dict) else {}
    return {
        "profile": record.get("profile"),
        "result": record.get("result"),
        "cases": record.get("case_count"),
        "mutations_detected": detected,
        "mutations_undetected": undetected,
        "branch_divide": branches.get("divide") is True,
        "branch_bypass": branches.get("bypass") is True,
        "scope": record.get("scope"),
    }


def gate_integrated(
    lane: IntegratedLane, profile: str, coverage: dict
) -> list[str]:
    """Check a lane's evidence against its declared obligations."""

    if not coverage:
        return ["%s produced no usable evidence record" % lane.name]
    failures: list[str] = []
    min_cases, min_mutations = lane.obligations[profile]
    if coverage["result"] != "PASS":
        failures.append(
            "%s evidence result is %r, not PASS" % (lane.name, coverage["result"])
        )
    if not isinstance(coverage["cases"], int) or coverage["cases"] < min_cases:
        failures.append(
            "%s covered %r case(s), below the %s profile's declared minimum "
            "of %d" % (lane.name, coverage["cases"], profile, min_cases)
        )
    if coverage["mutations_undetected"]:
        failures.append(
            "%s reported %d undetected required mutation(s)"
            % (lane.name, coverage["mutations_undetected"])
        )
    if coverage["mutations_detected"] < min_mutations:
        failures.append(
            "%s demonstrated %d mutation(s), below the declared minimum of %d"
            % (lane.name, coverage["mutations_detected"], min_mutations)
        )
    if not (coverage["branch_divide"] and coverage["branch_bypass"]):
        failures.append(
            "%s did not reach both normalization branches (divide %s, "
            "bypass %s)" % (lane.name, coverage["branch_divide"],
                            coverage["branch_bypass"])
        )
    return failures


def integrated_baselined(baseline: dict, name: str) -> dict:
    """The ledger's record that this lane's diagnostics were baselined on CI."""

    entry = baseline.get(INTEGRATED_BASELINE_KEY, {}).get(name)
    return entry if isinstance(entry, dict) else {}


def integrated_baseline_gaps(
    baseline: dict, name: str, *, lint: bool, runtime_observed: bool
) -> list[str]:
    """Why an integrated lane's diagnostics cannot yet be enforced (empty = ready).

    A lane is enforceable only when the ledger's own provenance says it was
    generated on CI's toolchain AND records that this lane's lint units (and
    runtime transcript) were baselined there. Until then the verdict is
    withheld; it is never a pass and the diagnostics are never silently waived.
    """

    gaps: list[str] = []
    toolchain = (baseline.get("provenance") or {}).get("toolchain") or {}
    entry = integrated_baselined(baseline, name)
    if toolchain_matches_ci(toolchain):
        gaps.append(
            "%s: %s carries no CI-toolchain provenance (Icarus 12.0 / "
            "Verilator 5.020); verdict withheld" % (name, BASELINE_PATH)
        )
    if not (lint and entry.get("lint_baselined")):
        gaps.append(
            "%s lint units are not baselined in %s (or --lint was not "
            "run); verdict withheld" % (name, BASELINE_PATH)
        )
    if not (runtime_observed and entry.get("runtime_baselined")):
        gaps.append(
            "%s runtime transcript is unobserved or its runtime diagnostics "
            "are not baselined; verdict withheld" % name
        )
    return gaps


def tool_versions() -> dict:
    """First-line ``--version`` of each linter actually installed."""

    versions: dict = {}
    for tool, flag in (("iverilog", "-V"), ("verilator", "--version")):
        if shutil.which(tool) is None:
            versions[tool] = None
            continue
        completed = subprocess.run(
            [tool, flag], capture_output=True, text=True
        )
        lines = (completed.stdout + completed.stderr).splitlines()
        versions[tool] = lines[0].strip() if lines else None
    return versions


#: GitHub Actions' own run identifiers, recorded verbatim into the ledger's
#: provenance when a baseline is generated on a hosted runner, so a reader can
#: find the exact run (and runner image) that produced it. Absent locally.
CI_RUN_ENV: dict[str, str] = {
    "repository": "GITHUB_REPOSITORY",
    "workflow": "GITHUB_WORKFLOW",
    "job": "GITHUB_JOB",
    "run_id": "GITHUB_RUN_ID",
    "run_attempt": "GITHUB_RUN_ATTEMPT",
    "event": "GITHUB_EVENT_NAME",
    "sha": "GITHUB_SHA",
    "ref": "GITHUB_REF",
    "runner_image_os": "ImageOS",
    "runner_image_version": "ImageVersion",
}


def ci_run_provenance(environ: Optional[dict] = None) -> dict:
    """The GitHub Actions run this process is executing in ({} if none)."""

    env = os.environ if environ is None else environ
    if env.get("GITHUB_ACTIONS") != "true":
        return {}
    return {
        key: env[name] for key, name in CI_RUN_ENV.items() if env.get(name)
    }


def toolchain_matches_ci(versions: dict) -> list[str]:
    """Reasons the local toolchain is not the ledger's CI toolchain."""

    problems: list[str] = []
    for tool, prefix in CI_TOOLCHAIN_PREFIXES.items():
        found = versions.get(tool)
        if not found or not found.startswith(prefix):
            problems.append(
                "%s is %r; the ledger is calibrated to %r*"
                % (tool, found, prefix)
            )
    return problems


def compute_verdict(
    *,
    ok: bool,
    lint: bool,
    lanes: Sequence[str],
    check_exit: Optional[int],
    integrated: Sequence[str],
    integrated_complete: bool,
) -> tuple[str, list[str]]:
    """Return ``(verdict, withheld reasons)``.

    PASS needs the lint tier, every module lane, the registered check AND
    every integrated lane executed/consumed, baselined and passing. Anything
    partial is ``NO VERDICT`` -- never a pass for the whole node.
    """

    withheld: list[str] = []
    if not lint:
        withheld.append("lint tier not selected")
    if set(lanes) != set(LANES):
        withheld.append("module lanes %d/%d" % (len(set(lanes)), len(LANES)))
    if check_exit != 0:
        withheld.append("registered check not run/passed")
    if set(integrated) != set(INTEGRATED_NAMES):
        withheld.append(
            "integrated lanes %d/%d" % (len(set(integrated)),
                                        len(INTEGRATED_NAMES))
        )
    elif not integrated_complete:
        withheld.append(
            "integrated lanes' diagnostics baseline/transcript incomplete"
        )
    if not ok:
        return "FAIL", withheld
    return ("NO VERDICT" if withheld else "PASS"), withheld


# --------------------------------------------------------------------------
# Evidence record
# --------------------------------------------------------------------------


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _project_commit(root: Path) -> Optional[str]:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True
    )
    value = completed.stdout.strip()
    return value if completed.returncode == 0 and len(value) == 40 else None


def build_record(
    *,
    node: dict,
    node_sha256: str,
    inputs: dict[str, str],
    project_commit: str,
    run_id: str,
    recorded_at: str,
    command: Sequence[str],
    exit_code: Optional[int],
    log_path: str,
    log_sha256: str,
    verdict: str,
    reason: str,
    control_executed: bool,
    control_detected: bool,
) -> dict:
    """Assemble a capability-evidence-v1 record for the rtl-modules node.

    ``dependencies`` is deliberately empty: this node depends on
    ``fixed-model``, which carries no evidence, so there is no prerequisite
    digest to cite. The record is therefore a complete, honest report of what
    this run establishes and is NOT attachable to the graph yet -- see
    ``spec/RTL-MODULE-QUALIFICATION.md``.
    """

    fault = node["negative_controls"]["one-bit-mutation"]
    return {
        "schema_version": 1,
        "node_id": node["id"],
        "node_sha256": node_sha256,
        "provenance": {
            "upstream_commit": "2b0964d4c6c3d472a2a0d54d91b408caaeffca6d",
            "project_commit": project_commit,
            "producer": "qualify-rtl-modules",
            "run_id": run_id,
            "recorded_at": recorded_at,
            "runtime_lock": "uv.lock",
        },
        "inputs": dict(sorted(inputs.items())),
        "dependencies": {},
        "execution": {
            "check": CHECK_NAME,
            "command": list(command),
            "engine": node["engine"],
            "layer": node["layer"],
            "scope": node["scope"],
            "executed": exit_code is not None,
            "exit_code": exit_code,
            "log": {"path": log_path, "sha256": log_sha256},
        },
        "result": {"verdict": verdict, "reason": reason},
        "controls": {
            "one-bit-mutation": {
                "fault": fault,
                "executed": control_executed,
                "detected": control_detected,
                "log": {"path": log_path, "sha256": log_sha256},
            }
        },
        "references": {},
    }


def _import_capabilities(root: Path):
    sys.path.insert(0, str(root / "src"))
    from torchsynth_voice import capabilities  # noqa: E402

    return capabilities


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=ROOT, help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--lint", action="store_true",
        help="run the compile-time diagnostic gate over every lint unit",
    )
    parser.add_argument(
        "--lanes", default="none",
        help="lane selection for the aggregate regression: 'all', 'fast' "
        "(%s), 'none', or a comma-separated list" % ", ".join(FAST_LANES),
    )
    parser.add_argument(
        "--parallel", action="store_true",
        help="run the selected lanes concurrently",
    )
    parser.add_argument("--jobs", type=int, default=None)
    parser.add_argument(
        "--update-baseline", action="store_true",
        help="rewrite %s from what this run observed (review the diff!)"
        % BASELINE_PATH,
    )
    parser.add_argument(
        "--integrated", default="none",
        help="standalone one-shot harnesses to include: 'all', 'none', or a "
        "comma-separated list of %s (issue #264). Executed via their own "
        "scripts unless --integrated-artifacts is given" %
        ", ".join(INTEGRATED_NAMES),
    )
    parser.add_argument(
        "--integrated-profile", default="regression",
        help="the harness profile the integrated lanes must have run "
        "(default: regression, the bounded CI profile)",
    )
    parser.add_argument(
        "--integrated-workdir", type=Path, default=Path("out/integrated"),
        help="per-lane --workdir root when the harnesses are executed",
    )
    parser.add_argument(
        "--integrated-artifacts", type=Path, default=None,
        help="consume the producer jobs' downloaded artifacts "
        "(<dir>/<lane>/<evidence file> [+ transcript.log]) instead of "
        "re-running the long simulations; bound to this revision and to "
        "--producer-result, failing closed on anything absent or stale",
    )
    parser.add_argument(
        "--producer-result", action="append", default=[],
        metavar="LANE=RESULT",
        help="the producing CI job's result for a lane (must be 'success'); "
        "required with --integrated-artifacts",
    )
    parser.add_argument(
        "--baseline-output", type=Path, default=None,
        help="with --update-baseline, write the regenerated ledger HERE "
        "instead of over %s, and compare this run against it. The "
        "committed ledger is left byte-identical, so a CI job can emit a "
        "reviewable candidate without ever mutating what it enforced"
        % BASELINE_PATH,
    )
    parser.add_argument(
        "--allow-foreign-toolchain", action="store_true",
        help="let --update-baseline run on a toolchain that is not CI's; the "
        "recorded provenance then keeps integrated lanes unbaselined",
    )
    parser.add_argument(
        "--log", type=Path, default=None,
        help="write the aggregate transcript here (required with --record)",
    )
    parser.add_argument(
        "--record", type=Path, default=None,
        help="write a capability-evidence-v1 qualification record here",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:  # noqa: C901
    args = parse_args(argv)
    root = args.root.resolve()
    lanes = resolve_lanes(args.lanes)
    integrated = resolve_integrated(args.integrated)
    if not args.lint and not lanes and not integrated:
        print(
            "ERROR: nothing selected; pass --lint and/or --lanes "
            "all|fast|<list> and/or --integrated all|<list>"
        )
        return 2
    if args.integrated_profile not in {
        p for lane in INTEGRATED_LANES.values() for p in lane.profiles
    }:
        print("ERROR: unknown --integrated-profile %r" % args.integrated_profile)
        return 2
    for name in integrated:
        if args.integrated_profile not in INTEGRATED_LANES[name].profiles:
            print("ERROR: %s has no %r profile (choices: %s)" % (
                name, args.integrated_profile,
                ", ".join(INTEGRATED_LANES[name].profiles)))
            return 2
    if args.baseline_output is not None and not args.update_baseline:
        print("ERROR: --baseline-output only applies with --update-baseline")
        return 2
    producer_results: dict = {}
    for item in args.producer_result:
        lane_name, _, value = item.partition("=")
        producer_results[lane_name] = value

    transcript: list[str] = [
        "tools/qualify_rtl_modules.py aggregate RTL-module qualification run",
        "lint units: %s" % (", ".join(sorted(LINT_UNITS)) if args.lint else "none"),
        "lanes: %s" % (", ".join(lanes) if lanes else "none"),
        "integrated lanes: %s%s" % (
            ", ".join(integrated) if integrated else "none",
            " (profile %s)" % args.integrated_profile if integrated else "",
        ),
        "linters available: %s" % (", ".join(linters_available()) or "none"),
    ]
    diagnostics: list[Diagnostic] = []
    integrated_diagnostics: list[Diagnostic] = []
    failures: list[str] = []
    notes: list[str] = []
    lane_results: dict[str, dict] = {}

    if args.lint:
        if not linters_available():
            failures.append(
                "neither verilator nor iverilog is installed; the lint tier "
                "did not run and an unrun gate is never a pass"
            )
        transcript.append("")
        transcript.append("== lint tier ==")
        for unit in sorted(LINT_UNITS):
            unit_diagnostics, unit_transcript = lint_unit(unit, root)
            diagnostics += unit_diagnostics
            transcript.append("-- unit %s" % unit)
            transcript += unit_transcript
            print(
                "lint %-20s %d diagnostic(s) after declared exclusions"
                % (unit, len(tally(unit_diagnostics)))
            )
        for unit in INTEGRATED_NAMES:
            unit_diagnostics, unit_transcript = lint_unit(unit, root)
            integrated_diagnostics += unit_diagnostics
            transcript.append("-- integrated unit %s" % unit)
            transcript += unit_transcript
            print(
                "lint %-20s %d diagnostic(s) after declared exclusions "
                "(integrated unit)" % (unit, len(tally(unit_diagnostics)))
            )

    if lanes:
        module = _load_run_fast_tests()
        transcript.append("")
        transcript.append("== lane tier ==")
        started = time.monotonic()
        results = module.run_lanes(
            lanes, root / "tb" / "run_tb.py", "iverilog", None,
            args.parallel, args.jobs,
        )
        elapsed = time.monotonic() - started
        for result in results:
            text = (result.stdout or "") + (result.stderr or "")
            diagnostics += scan_transcript(result.lane, text)
            observed = lane_coverage(text)
            observed["exit_code"] = result.returncode
            observed["seconds"] = round(result.elapsed, 1)
            lane_results[result.lane] = observed
            if result.returncode != 0:
                failures.append(
                    "lane %s exited %d" % (result.lane, result.returncode)
                )
            failures += gate_coverage(result.lane, observed)
            transcript.append("-- lane %s (exit %d)" % (result.lane, result.returncode))
            transcript.append(text)
            print(
                "lane %-12s exit %d in %5.1fs: %d OK, %d FAIL, %d mutation(s) "
                "detected, %d NOT DETECTED"
                % (result.lane, result.returncode, result.elapsed,
                   observed["cases_ok"], observed["cases_failed"],
                   observed["mutations_detected"],
                   observed["mutations_undetected"])
            )
        transcript.append("lane tier wall-clock: %.1fs" % elapsed)

    # ---- integrated one-shot lanes (issue #264) ------------------------
    integrated_ledger: dict[str, dict] = {}
    integrated_runtime: dict[str, bool] = {}
    if integrated:
        head = _project_commit(root)
        if head is None:
            failures.append(
                "cannot resolve the project commit; integrated evidence "
                "cannot be bound to a revision"
            )
        transcript.append("")
        transcript.append("== integrated lane tier ==")
        for name in integrated:
            lane = INTEGRATED_LANES[name]
            if args.integrated_artifacts is not None:
                result = consume_integrated_artifact(
                    lane, args.integrated_artifacts, producer_results
                )
            else:
                result = run_integrated_lane(
                    lane, args.integrated_profile, root,
                    root / args.integrated_workdir / name,
                )
            record, evidence_errors = (None, [])
            if result.exit_code is not None:
                record, evidence_errors = load_integrated_evidence(
                    lane, result.evidence_path, args.integrated_profile,
                    expect_head=head if head else "0" * 40, tree_root=root,
                )
            result.errors += evidence_errors
            coverage = integrated_coverage(lane, record)
            if result.exit_code is not None and not evidence_errors:
                result.errors += gate_integrated(
                    lane, args.integrated_profile, coverage
                )
            failures += result.errors
            if result.transcript is not None:
                diagnostics_seen = scan_transcript(name, result.transcript)
                integrated_diagnostics += diagnostics_seen
                integrated_runtime[name] = True
            else:
                integrated_runtime[name] = False
            integrated_ledger[name] = {
                "harness": lane.harness,
                "command": " ".join(
                    integrated_command(lane, args.integrated_profile,
                                       Path("<workdir>"))
                ),
                "profile": args.integrated_profile,
                "status": result.mode if result.exit_code is not None
                else "not-run",
                "exit_code": result.exit_code,
                "coverage": coverage,
                "lint_units": list(lane.lint_sources),
                "label": integrated_label(lane, args.integrated_profile),
                "errors": list(result.errors),
            }
            transcript.append("-- integrated lane %s (%s, exit %s)" % (
                name, result.mode, result.exit_code))
            if result.transcript:
                transcript.append(result.transcript)
            print("integrated %-20s %s exit %s: %s" % (
                name, result.mode, result.exit_code,
                "FAIL (%d)" % len(result.errors) if result.errors
                else integrated_label(lane, args.integrated_profile)))

    selected_tiers = (
        ("lint", args.lint),
        ("runtime", bool(lanes) or any(integrated_runtime.values())),
    )
    tiers = tuple(tier for tier, selected in selected_tiers if selected)

    if args.update_baseline:
        integrated_errors = [
            error for name in integrated
            for error in integrated_ledger[name]["errors"]
        ]
        if integrated_errors:
            print(
                "ERROR: refusing --update-baseline: an integrated lane "
                "failed/was stale, and a baseline is never generated from a "
                "run that did not pass: " + "; ".join(integrated_errors)
            )
            return 2
        update_tally = tally(diagnostics + integrated_diagnostics)
        versions = tool_versions()
        foreign = toolchain_matches_ci(versions)
        if foreign and not args.allow_foreign_toolchain:
            print(
                "ERROR: refusing --update-baseline on a non-CI toolchain "
                "(%s). Generate it on the CI toolchain (see %s) or pass "
                "--allow-foreign-toolchain, which keeps the integrated "
                "lanes' baseline unrecognised." % ("; ".join(foreign),
                                                    POLICY_DOC)
            )
            return 2
        provenance = {
            "toolchain": versions,
            "revision": _project_commit(root),
            "command": "python3 tools/qualify_rtl_modules.py "
            + " ".join(sys.argv[1:] if argv is None else list(argv)),
            "generated_at": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"),
        }
        ci_run = ci_run_provenance()
        if ci_run:
            provenance["ci_run"] = ci_run
        registry = {}
        for name in INTEGRATED_NAMES:
            entry: dict = {}
            if args.lint:
                entry["lint_baselined"] = True
                entry["lint_units"] = list(INTEGRATED_LINT_UNITS[name])
            if integrated_runtime.get(name):
                entry["runtime_baselined"] = True
                entry["profile"] = args.integrated_profile
            if entry:
                registry[name] = entry
        baseline = build_baseline(
            update_tally, load_baseline(root), tiers=tiers,
            provenance=provenance, integrated=registry,
            runtime_units=list(lanes) + [
                n for n in integrated if integrated_runtime.get(n)],
        )
        output = (
            args.baseline_output if args.baseline_output is not None
            else root / BASELINE_PATH
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(baseline, indent=2) + "\n", encoding="utf-8"
        )
        print("wrote %s (%d waiver(s))" % (output, len(baseline["waivers"])))

    if args.baseline_output is not None:
        # Compare against the candidate just written: the committed ledger
        # was neither read for enforcement nor modified by this run.
        with args.baseline_output.open(encoding="utf-8") as handle:
            baseline = json.load(handle)
    else:
        baseline = load_baseline(root)
    # An integrated lane's diagnostics are only GATED once the ledger records
    # that they were baselined on CI's own toolchain. Until then they are
    # reported and the verdict is withheld: neither silently waived nor
    # enforced against a baseline nobody generated for them.
    ledger_toolchain = (baseline.get("provenance") or {}).get("toolchain") or {}
    ci_baseline = not toolchain_matches_ci(ledger_toolchain)
    integrated_complete = bool(integrated)
    gated_integrated: list[Diagnostic] = []
    for diagnostic in integrated_diagnostics:
        entry = integrated_baselined(baseline, diagnostic.unit)
        flag = "lint_baselined" if diagnostic.tier == "lint" else "runtime_baselined"
        if ci_baseline and entry.get(flag):
            gated_integrated.append(diagnostic)
    pending = [d for d in integrated_diagnostics if d not in gated_integrated]
    for name in integrated:
        gaps = integrated_baseline_gaps(
            baseline, name, lint=args.lint,
            runtime_observed=integrated_runtime.get(name, False),
        )
        if gaps:
            integrated_complete = False
            notes += gaps
    if pending:
        for key, entry in sorted(tally(pending).items()):
            notes.append(
                "pending (ungated) integrated diagnostic %s x%d: %s"
                % (key, entry["count"], entry["example"])
            )
    observed_tally = tally(diagnostics + gated_integrated)
    gate_failures, gate_notes = gate_diagnostics(
        observed_tally, baseline, tiers=tiers
    )
    failures += gate_failures
    notes += gate_notes

    transcript.append("")
    transcript.append("== coverage ==")
    for name, reason in sorted(COVERAGE_EXCLUSIONS.items()):
        transcript.append("excluded %s: %s" % (name, reason))
    for lane in lanes:
        transcript.append("%s: %r" % (lane, lane_results.get(lane)))
    if integrated_ledger:
        transcript.append("")
        transcript.append("== integrated lanes ==")
        for name in integrated:
            transcript.append(
                "%s: %s" % (name, json.dumps(integrated_ledger[name],
                                             sort_keys=True))
            )

    detected_total = sum(
        entry["mutations_detected"] for entry in lane_results.values()
    )
    undetected_total = sum(
        entry["mutations_undetected"] for entry in lane_results.values()
    )

    # The registered check (this gate's own evaluator test) is what the
    # evidence record's execution block describes, so run it here -- inside
    # the same transcript -- rather than citing a command nobody invoked.
    capabilities = None
    check_exit: Optional[int] = None
    if args.record is not None:
        capabilities = _import_capabilities(root)
        command = list(capabilities.CHECKS[CHECK_NAME].command)
        completed = subprocess.run(
            command, cwd=root, capture_output=True, text=True
        )
        check_exit = completed.returncode
        transcript.append("")
        transcript.append("== registered check ==")
        transcript.append("+ " + " ".join(command))
        transcript.append(completed.stdout + completed.stderr)
        print("registered check %s exited %d" % (CHECK_NAME, check_exit))
        if check_exit != 0:
            failures.append(
                "registered check %s exited %d" % (CHECK_NAME, check_exit)
            )

    transcript.append("")
    transcript.append("== verdict ==")
    for note in notes:
        transcript.append("NOTE: " + note)
        print("NOTE: " + note)
    for failure in failures:
        transcript.append("FAIL: " + failure)
        print("FAIL: " + failure)
    ok = not failures
    verdict, withheld = compute_verdict(
        ok=ok, lint=args.lint, lanes=lanes, check_exit=check_exit,
        integrated=integrated, integrated_complete=integrated_complete,
    )
    reason = (
        "lint units %d, lanes %d/%d, diagnostics %d after declared exclusions, "
        "mutations detected %d, undetected %d"
        % (len(LINT_UNITS) if args.lint else 0, len(lanes), len(LANES),
           sum(entry["count"] for entry in observed_tally.values()),
           detected_total, undetected_total)
    )
    if integrated:
        reason += "; integrated lanes (%s): %s" % (
            ", ".join(integrated),
            "full profile" if args.integrated_profile == "full"
            else "%s profile only, NOT full-profile qualification"
            % args.integrated_profile,
        )
    if verdict == "NO VERDICT":
        reason += (
            "; a partial selection is never a pass for the whole node "
            "(withheld: %s)" % "; ".join(withheld)
        )
    transcript.append("%s: %s" % (verdict, reason))
    print("%s: %s" % (verdict, reason))

    text = "\n".join(transcript) + "\n"
    if args.log is not None:
        args.log.parent.mkdir(parents=True, exist_ok=True)
        args.log.write_text(text, encoding="utf-8")
        print("wrote transcript %s" % args.log)

    if args.record is not None:
        if args.log is None:
            print("ERROR: --record needs --log so the record can index it")
            return 2
        assert capabilities is not None
        graph = capabilities.load_graph(root / "spec/capabilities-v1.json")
        node = next(n for n in graph["nodes"] if n["id"] == NODE_ID)
        commit = _project_commit(root)
        if commit is None:
            print("ERROR: cannot resolve the project commit for the record")
            return 2
        try:
            log_relative = args.log.resolve().relative_to(root)
        except ValueError:
            print(
                "ERROR: --log must live inside the repository so the record "
                "can reference it relatively"
            )
            return 2
        record = build_record(
            node=node,
            node_sha256=capabilities.node_digest(node),
            inputs=capabilities.coverage_hashes(node, root),
            project_commit=commit,
            run_id="run-" + _sha256_text(text)[:16],
            recorded_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            command=capabilities.CHECKS[CHECK_NAME].command,
            exit_code=check_exit,
            log_path=log_relative.as_posix(),
            log_sha256=_sha256_text(text),
            verdict=verdict,
            reason=reason,
            control_executed=detected_total + undetected_total > 0,
            control_detected=detected_total > 0 and undetected_total == 0,
        )
        capabilities._validate(record, "evidence")
        args.record.parent.mkdir(parents=True, exist_ok=True)
        args.record.write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print("wrote record %s" % args.record)

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
