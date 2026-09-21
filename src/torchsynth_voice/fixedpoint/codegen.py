"""DR-0008 choice-register to SystemVerilog constants emitter (issue #68).

Every choice is consumed exclusively through
:func:`torchsynth_voice.fixedpoint.choices.require_accepted`, the landed
structural refusal gate. While DR-0008's status is ``Proposed`` the register
refuses all of C1-C10, so this emitter produces **no package at all** —
that refusal is the honest deliverable state, not a stub awaiting content.

Since the issue #53 ratification (reviewed merge, 2026-09-21) DR-0008 is
Accepted and every register entry carries ``status == "accepted"``, so the
emitter emits a SystemVerilog package whose ``localparam`` values are taken
verbatim from the register's ``parameters`` payload. No width, rate, or
threshold is hardcoded anywhere in this module.

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

PACKAGE_NAME = "gf180_rtl_constants"


@dataclass
class EmitResult:
    """Outcome of one emitter run over the choice register.

    ``package_text`` is ``None`` exactly when nothing was emittable — the
    normal state while DR-0008 is Proposed. ``refusals`` names every choice
    that could not be emitted and why.
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


def emit(
    payload: Optional[Dict[str, Any]] = None,
) -> EmitResult:
    """Emit the constants package for the accepted choices, if any.

    ``payload`` defaults to the live register file
    (``spec/reference/fixedpoint-choices-v1.json``). A test may pass a
    hand-built payload (e.g. a post-ratification register) to exercise the
    admit path without touching the landed file.
    """
    payload = payload if payload is not None else choices_module.load_choices()
    emitted: List[Dict[str, Any]] = []
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
        emitted.append((choice, lines))

    if not emitted:
        return EmitResult(package_text=None, emitted_ids=[], refusals=refusals)

    blocks = []
    for choice, lines in emitted:
        blocks.append("  // %s: %s" % (choice["id"], choice["choice"]))
        blocks.extend(lines)
    text = "package %s;\n%s\nendpackage\n" % (PACKAGE_NAME, "\n".join(blocks))
    return EmitResult(
        package_text=text,
        emitted_ids=[choice["id"] for choice, _ in emitted],
        refusals=refusals,
    )
