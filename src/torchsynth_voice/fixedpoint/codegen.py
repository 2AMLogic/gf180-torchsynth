"""Choice-register and schedule-register to SystemVerilog constants emitter.

Numeric choices are consumed exclusively through
:func:`torchsynth_voice.fixedpoint.choices.require_accepted`, the landed
structural refusal gate. Since the issue #53 ratification (reviewed merge,
2026-09-21) DR-0008 is Accepted and every register entry carries
``status == "accepted"``, so the emitter emits a SystemVerilog package whose
``localparam`` values are taken verbatim from the register's ``parameters``
payload. No width, rate, or threshold is hardcoded anywhere in this module.

The ratified one-shot schedule constants (issue #68's final increment) are
consumed the same way from the machine-readable DR-0010 schedule register
(``spec/reference/rtl-schedule-v1.json``) through
:func:`torchsynth_voice.fixedpoint.schedule.require_accepted_schedule`:
each emitted ``SCHED_*`` localparam cites DR-0010's Accepted schedule
section, and the block is emitted only while that register is Accepted.
The two sources gate independently — a refusal of either source names it,
and the package is emitted only if at least one source emits. The schedule
is a schedule-candidate: no clock is selected and no implementability or
PPA/fit claim is made by emitting it.

Parameters this emitter cannot represent (lists, nested objects) produce a
per-choice emit refusal naming the parameter, so an accepted register can
never be silently truncated into RTL constants. (The register's C6 sites
and C7 never-saturate words are stored as comma-joined strings for exactly
this reason; the names are DR-0008 Section 6's.)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from torchsynth_voice.fixedpoint import choices as choices_module
from torchsynth_voice.fixedpoint import schedule as schedule_module

PACKAGE_NAME = "gf180_rtl_constants"


@dataclass
class EmitResult:
    """Outcome of one emitter run over the choice register.

    ``package_text`` is ``None`` exactly when nothing was emittable — the
    normal state while both sources refuse. ``refusals`` names every choice
    (and the schedule) that could not be emitted and why.
    """

    package_text: Optional[str]
    emitted_ids: List[str] = field(default_factory=list)
    refusals: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def refused_all(self) -> bool:
        return not self.emitted_ids


def _sanitize(name: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in name).upper()


def _localparams(choice: Dict[str, Any]) -> List[str]:
    lines = []
    parameters = choice.get("parameters") or {}
    if not isinstance(parameters, dict):
        raise ValueError(
            "choice %s: parameters must be an object" % choice.get("id")
        )
    for key in sorted(parameters):
        value = parameters[key]
        ident = "C%s_%s" % (str(choice["id"]).lstrip("C"), _sanitize(key))
        if isinstance(value, bool):
            # SystemVerilog has no bool; the faithful payload mapping is 1/0.
            lines.append("    localparam int %s = %d;" % (ident, 1 if value else 0))
        elif isinstance(value, int):
            lines.append("    localparam int %s = %d;" % (ident, value))
        elif isinstance(value, str):
            lines.append(
                '    localparam string %s = "%s";' % (ident, value)
            )
        else:
            # Floats included: a float parameter needs DR-0008 to decide a
            # fixed-point rendering first — emitting raw reals would quietly
            # invent numeric semantics.
            raise ValueError(
                "choice %s: parameter %r of type %s is not directly "
                "representable as an RTL constant; an explicit mapping "
                "decision is required before it can be emitted"
                % (choice["id"], key, type(value).__name__)
            )
    return lines


def _schedule_block(schedule: Dict[str, Any]) -> List[str]:
    """Render the DR-0010 schedule block, every line citing its source."""
    section = schedule["source_section"]
    lines = [
        "  // %s (Accepted) — \"%s\" (issue #63, reviewed merge 2026-09-21);"
        % (schedule["dr"], section),
        "  // machine-readable source: spec/reference/rtl-schedule-v1.json.",
        "  // Budget equation: C = C_counted + T; N_clip = %d x C. T is the"
        % schedule_module.constant(schedule, "clip_sample_slots"),
        "  // declared allowance for 2x exp2 + 1x tanh — an elaboration-time",
        "  // parameter of the consuming lane, never a ratified constant.",
        "  // Schedule-candidate honesty: no clock is selected; the T <= %d"
        % schedule_module.constant(schedule, "t_max_at_25mhz"),
        "  // bound at %d MHz is the refutable <= 1x-real-time bound; no"
        % schedule_module.constant(schedule, "bound_clock_mhz"),
        "  // implementability, synthesis, PPA, fit, or hardware claim is made.",
    ]
    for entry in schedule["constants"]:
        ident = "SCHED_%s" % _sanitize(entry["name"])
        value = entry["value"]
        citation = "DR-0010 (Accepted) Schedule"
        if isinstance(value, str):
            lines.append(
                '    localparam string %s = "%s";  // %s' % (ident, value, citation)
            )
        else:
            lines.append(
                "    localparam int %s = %d;  // %s" % (ident, value, citation)
            )
    return lines


def emit(
    payload: Optional[Dict[str, Any]] = None,
    schedule_payload: Optional[Dict[str, Any]] = None,
) -> EmitResult:
    """Emit the constants package for the accepted sources, if any.

    ``payload`` defaults to the live choice register
    (``spec/reference/fixedpoint-choices-v1.json``) and ``schedule_payload``
    to the live schedule register (``spec/reference/rtl-schedule-v1.json``).
    A test may pass hand-built payloads to exercise refusal states without
    touching the landed files. Each source gates independently; a refusal
    of either is named in ``refusals``.
    """
    payload = payload if payload is not None else choices_module.load_choices()
    emitted: List[Tuple[str, List[str]]] = []
    emitted_ids: List[str] = []
    refusals: List[Tuple[str, str]] = []
    for choice in payload["choices"]:
        choice_id = choice["id"]
        try:
            accepted = choices_module.require_accepted(choice_id, payload)
        except choices_module.ChoiceNotAccepted as exc:
            refusals.append((choice_id, str(exc)))
            continue
        try:
            lines = _localparams(accepted)
        except ValueError as exc:
            refusals.append((choice_id, str(exc)))
            continue
        header = "  // %s: %s" % (choice["id"], choice["choice"])
        emitted.append((header, lines))
        emitted_ids.append(choice_id)

    try:
        schedule = schedule_module.require_accepted_schedule(schedule_payload)
    except schedule_module.ScheduleNotAccepted as exc:
        refusals.append((schedule_module.SCHEDULE_ID, str(exc)))
    else:
        emitted.append(("  // ---- DR-0010 schedule ----", _schedule_block(schedule)))
        emitted_ids.append(schedule_module.SCHEDULE_ID)

    if not emitted:
        return EmitResult(package_text=None, emitted_ids=[], refusals=refusals)

    blocks = []
    for header, lines in emitted:
        if header:
            blocks.append(header)
        blocks.extend(lines)
    text = "package %s;\n%s\nendpackage\n" % (PACKAGE_NAME, "\n".join(blocks))
    return EmitResult(
        package_text=text,
        emitted_ids=emitted_ids,
        refusals=refusals,
    )
