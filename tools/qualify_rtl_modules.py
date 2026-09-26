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
    python3 tools/qualify_rtl_modules.py --lint --update-baseline
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
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
        "framing, not a defect. Icarus's classifier bucket for a "
        "'warning:'-prefixed line is this generic class rather than the "
        "dedicated READMEM class (see RUNTIME_PATTERNS's match order); "
        "waived at its committed per-lane count, so any new or increased "
        "occurrence -- including one that is NOT this readmemh pattern -- "
        "still gates."
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
    return ["%s/%s" % (SV_DIR, name) for name in LINT_UNITS[unit]]


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
    observed: dict[str, dict], previous: dict, *, tiers: Sequence[str]
) -> dict:
    """Rewrite the ledger for the tiers that ran, keeping the others intact.

    An entry for a tier this run did not execute is preserved verbatim: a
    ``--lint --update-baseline`` run must never delete the runtime tier's
    waivers just because it did not run any lanes.
    """

    waivers = {
        key: value
        for key, value in previous.get("waivers", {}).items()
        if value.get("tier") not in tiers
    }
    for key, entry in observed.items():
        if entry["tier"] in tiers:
            waivers[key] = _waiver(entry)
    return {
        "schema_version": 1,
        "generated_by": "tools/qualify_rtl_modules.py --update-baseline",
        "policy": POLICY_DOC,
        "excluded_classes": dict(sorted(EXCLUDED_CLASSES.items())),
        "waivers": {key: waivers[key] for key in sorted(waivers)},
    }


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
    if not args.lint and not lanes:
        print(
            "ERROR: nothing selected; pass --lint and/or --lanes all|fast|<list>"
        )
        return 2

    transcript: list[str] = [
        "tools/qualify_rtl_modules.py aggregate RTL-module qualification run",
        "lint units: %s" % (", ".join(sorted(LINT_UNITS)) if args.lint else "none"),
        "lanes: %s" % (", ".join(lanes) if lanes else "none"),
        "linters available: %s" % (", ".join(linters_available()) or "none"),
    ]
    diagnostics: list[Diagnostic] = []
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

    observed_tally = tally(diagnostics)
    tiers = tuple(
        tier for tier, selected in (("lint", args.lint), ("runtime", bool(lanes)))
        if selected
    )

    if args.update_baseline:
        baseline = build_baseline(observed_tally, load_baseline(root), tiers=tiers)
        (root / BASELINE_PATH).write_text(
            json.dumps(baseline, indent=2) + "\n", encoding="utf-8"
        )
        print("wrote %s (%d waiver(s))" % (BASELINE_PATH, len(baseline["waivers"])))

    baseline = load_baseline(root)
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
    complete = args.lint and set(lanes) == set(LANES) and check_exit == 0
    verdict = "PASS" if (ok and complete) else "FAIL" if not ok else "NO VERDICT"
    reason = (
        "lint units %d, lanes %d/%d, diagnostics %d after declared exclusions, "
        "mutations detected %d, undetected %d"
        % (len(LINT_UNITS) if args.lint else 0, len(lanes), len(LANES),
           sum(entry["count"] for entry in observed_tally.values()),
           detected_total, undetected_total)
    )
    if verdict == "NO VERDICT":
        reason += "; a partial selection is never a pass for the whole node"
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
