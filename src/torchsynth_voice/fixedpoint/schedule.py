"""Machine-readable DR-0010 schedule register access with refusal gates.

The ratified one-shot schedule (DR-0010, Accepted by the reviewed merge of
the issue #63 ratification PR, 2026-09-21) is data. This module loads
``spec/reference/rtl-schedule-v1.json`` and enforces the same consumer
discipline as the DR-0008 choice register
(:mod:`torchsynth_voice.fixedpoint.choices`): the schedule constants are
consumed only through :func:`require_accepted_schedule`, which refuses
unless the register declares ``dr_status == "Accepted"``.

The loader also verifies the register's internal consistency at load time,
so a self-inconsistent budget file is a load error, never a silent
constant source:

- ``clip_sample_slots == passes_per_clip x samples_per_pass``;
- ``slots_per_realtime_second == clip_sample_slots x sample_rate_hz /
  samples_per_pass`` (the DR-0010 ``f/88,200`` denominator);
- ``t_max_at_25mhz == floor(bound_clock_mhz x 1e6 /
  slots_per_realtime_second) - counted_cycles_per_sample``;
- the register's own worked example recomputes:
  ``n_clip == clip_sample_slots x (C_counted + T)``.

T is deliberately NOT a constant: it is the declared transcendental
allowance (2x exp2 + 1x tanh), an elaboration-time parameter of the
consuming lane. :func:`cycles_per_sample` and :func:`clip_cycles` are the
parameterization. No claim of implementability, PPA, fit, or hardware
behavior is made anywhere in this module.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[3]
SCHEDULE_PATH = ROOT / "spec/reference/rtl-schedule-v1.json"

SCHEMA = "gf180-torchsynth/rtl-schedule-v1"
DR_ACCEPTED_STATUS = "Accepted"

#: Identifier used for the schedule block in emitter reports.
SCHEDULE_ID = "SCHED"


class ScheduleNotAccepted(Exception):
    """Raised when the schedule is consumed where acceptance is required."""


def _int_field(payload: Dict[str, Any], dotted: str) -> int:
    node: Any = payload
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise ValueError(f"schedule register is missing {dotted!r}")
        node = node[part]
    if not isinstance(node, int) or isinstance(node, bool):
        raise ValueError(f"schedule register field {dotted!r} must be an integer")
    return node


def load_schedule(path: Path = None) -> Dict[str, Any]:
    """Load and validate the DR-0010 schedule register."""
    path = Path(path) if path is not None else SCHEDULE_PATH
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    validate_schedule(payload)
    return payload


def validate_schedule(payload: Dict[str, Any]) -> None:
    """Structural validation plus the register's internal-consistency math."""
    if payload.get("schema") != SCHEMA:
        raise ValueError(f"unsupported schedule schema: {payload.get('schema')!r}")
    if payload.get("dr_status") is None:
        raise ValueError("schedule register must declare dr_status")
    if payload.get("dr") != "DR-0010":
        raise ValueError(f"unexpected schedule record: {payload.get('dr')!r}")
    source = payload.get("source")
    section = payload.get("source_section")
    if not isinstance(source, str) or not source:
        raise ValueError("schedule register must cite its source record path")
    if not isinstance(section, str) or "Schedule" not in section:
        raise ValueError(
            "schedule register must cite DR-0010's Schedule section as source"
        )

    passes = _int_field(payload, "profile.passes_per_clip")
    samples = _int_field(payload, "profile.samples_per_pass")
    rate = _int_field(payload, "profile.sample_rate_hz")
    if passes <= 0 or samples <= 0 or rate <= 0:
        raise ValueError("schedule profile fields must be positive")

    constants = payload.get("constants")
    if not isinstance(constants, list) or not constants:
        raise ValueError("schedule register must carry a non-empty constants list")
    by_name = {}
    seen = set()
    for entry in constants:
        name = entry.get("name")
        if not name or name in seen:
            raise ValueError(f"bad or duplicate schedule constant name: {name!r}")
        seen.add(name)
        by_name[name] = entry
        if "value" not in entry or "source" not in entry:
            raise ValueError(
                f"schedule constant {name!r} must carry value and source "
                "(the DR-0010 citation)"
            )

    def int_constant(name: str) -> int:
        value = by_name.get(name, {}).get("value")
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"schedule constant {name!r} must be an integer")
        return value

    slots = int_constant("clip_sample_slots")
    if slots != passes * samples:
        raise ValueError(
            "schedule register inconsistent: clip_sample_slots %d != "
            "passes_per_clip %d x samples_per_pass %d"
            % (slots, passes, samples)
        )

    counted = int_constant("counted_cycles_per_sample")
    if counted <= 0:
        raise ValueError("counted_cycles_per_sample must be a positive integer")

    bound = payload.get("refutable_bound")
    if not isinstance(bound, dict):
        raise ValueError("schedule register must carry the refutable_bound object")
    slots_per_second = bound.get("slots_per_realtime_second")
    if slots_per_second != slots * rate // samples:
        raise ValueError(
            "schedule register inconsistent: slots_per_realtime_second %r != "
            "clip_sample_slots x sample_rate_hz / samples_per_pass (%d)"
            % (slots_per_second, slots * rate // samples)
        )
    t_max = int_constant("t_max_at_25mhz")
    bound_mhz = int_constant("bound_clock_mhz")
    expected_t_max = bound_mhz * 1000000 // slots_per_second - counted
    if t_max != expected_t_max:
        raise ValueError(
            "schedule register inconsistent: t_max_at_25mhz %r != "
            "floor(%d MHz / %d) - %d = %d"
            % (t_max, bound_mhz, slots_per_second, counted, expected_t_max)
        )

    example = (payload.get("budget_equation") or {}).get("worked_example")
    if not isinstance(example, dict):
        raise ValueError("budget_equation.worked_example is required")
    n_clip = example.get("n_clip")
    expected_n = slots * (counted + example.get("t"))
    if n_clip != expected_n:
        raise ValueError(
            "schedule register inconsistent: worked example n_clip %r != "
            "clip_sample_slots x (C_counted + T) = %d" % (n_clip, expected_n)
        )


def require_accepted_schedule(payload: Dict[str, Any] = None) -> Dict[str, Any]:
    """Consumer refusal gate: return the register only if DR-0010 is Accepted.

    Raises :class:`ScheduleNotAccepted` unless the register declares
    ``dr_status == "Accepted"``, so a draft or amended-pending schedule can
    never be consumed as a ratified budget.
    """
    payload = payload if payload is not None else load_schedule()
    if payload.get("dr_status") != DR_ACCEPTED_STATUS:
        raise ScheduleNotAccepted(
            "the DR-0010 schedule cannot be consumed: register dr_status is "
            "%r, not %r; refusing to emit or consume schedule constants "
            "without the ratified record" % (payload.get("dr_status"), DR_ACCEPTED_STATUS)
        )
    return payload


def constant(schedule: Dict[str, Any], name: str) -> Any:
    """Return one schedule constant's value by name."""
    for entry in schedule["constants"]:
        if entry["name"] == name:
            return entry["value"]
    raise KeyError(f"unknown schedule constant: {name}")


def cycles_per_sample(schedule: Dict[str, Any], t: int) -> int:
    """The budget equation's per-sample term: C = C_counted + T."""
    return constant(schedule, "counted_cycles_per_sample") + t


def clip_cycles(schedule: Dict[str, Any], t: int) -> int:
    """The clip budget: N_clip = 352,800 x (C_counted + T)."""
    return constant(schedule, "clip_sample_slots") * cycles_per_sample(schedule, t)
