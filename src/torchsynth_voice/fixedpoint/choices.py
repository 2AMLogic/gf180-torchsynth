"""Machine-readable DR-0008 choice-register access with refusal gates.

The choice register (DR-0008 Section 12) is data. This module loads
``spec/reference/fixedpoint-choices-v1.json`` and enforces the consumer rule
the record states: a choice is accepted only while DR-0008's status is
Accepted AND the entry itself carries ``status == "accepted"``; every other
state is refused wherever the contract requires accepted ones.

Status vocabulary follows DR-0008 Section 12 (``proposed`` / ``selected`` /
``accepted`` / ``rejected``). Since the issue #53 ratification (reviewed
merge, 2026-09-21) every entry carries ``status = "accepted"`` (the bare
token the gates check) plus the ``accepted_via`` annotation recording the
acceptance event.

:func:`require_accepted` is the structural refusal gate: it raises unless a
choice is contract-accepted, so candidate instantiations cannot silently be
consumed as contract-accepted parameters. This module makes no RTL claim.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[3]
CHOICES_PATH = ROOT / "spec/reference/fixedpoint-choices-v1.json"

ACCEPTED_STATUS = "accepted"
DR_ACCEPTED_STATUS = "Accepted"


class ChoiceNotAccepted(Exception):
    """Raised when a choice is consumed where the contract requires accepted."""


def load_choices(path: Path = None) -> Dict[str, Any]:
    """Load and validate the choice-config file."""
    path = Path(path) if path is not None else CHOICES_PATH
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    validate_choices(payload)
    return payload


def validate_choices(payload: Dict[str, Any]) -> None:
    """Structural validation: vocabulary, statuses, and the refusal invariant."""
    if payload.get("schema") != "gf180-torchsynth/fixedpoint-choices-v1":
        raise ValueError(f"unsupported choices schema: {payload.get('schema')!r}")
    dr_status = payload.get("dr_status")
    if dr_status is None:
        raise ValueError("choices file must declare dr_status")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("choices file must carry a non-empty choices list")
    seen = set()
    for choice in choices:
        cid = choice.get("id")
        if not cid:
            raise ValueError(f"every choice must carry an id")
        if cid in seen:
            raise ValueError(f"duplicate choice id: {cid}")
        seen.add(cid)
        status = choice.get("status", "")
        if status == ACCEPTED_STATUS:
            # The accepted state is only valid for the whole register at once:
            # an accepted entry in a Proposed (or otherwise non-Accepted) DR is
            # the exact contradiction the consumer rule forbids.
            if dr_status != DR_ACCEPTED_STATUS:
                raise ValueError(
                    f"choice {cid} carries status {status!r} while DR-0008 status "
                    f"is {dr_status!r}; refusing: a choice is accepted only when "
                    "the record reaches Accepted"
                )
            if choice.get("accepted") is not True:
                raise ValueError(
                    f"choice {cid}: status {status!r} requires accepted: true"
                )
        elif "accepted" in status and not status.startswith("selected"):
            raise ValueError(f"choice {cid}: unexpected accepted-flavored status {status!r}")
        if choice.get("accepted") is True:
            if dr_status != DR_ACCEPTED_STATUS:
                raise ValueError(
                    f"choice {cid} marked accepted while DR-0008 status is {dr_status!r}; "
                    "refusing: a choice is accepted only when the record reaches Accepted"
                )
            if status != ACCEPTED_STATUS:
                raise ValueError(
                    f"choice {cid}: accepted: true requires status {ACCEPTED_STATUS!r}, "
                    f"got {status!r}"
                )
        register_term = choice.get("register_term")
        vocabulary = payload.get("status_vocabulary", {}).get("register_terms", [])
        if register_term not in vocabulary:
            raise ValueError(
                f"choice {cid}: register_term {register_term!r} outside "
                f"DR-0008 Section 12 vocabulary {vocabulary!r}"
            )


def get(choice_id: str, payload: Dict[str, Any] = None) -> Dict[str, Any]:
    """Return one choice record (data; not a claim of acceptance)."""
    payload = payload if payload is not None else load_choices()
    for choice in payload["choices"]:
        if choice["id"] == choice_id:
            return choice
    raise KeyError(f"unknown choice id: {choice_id}")


def accepted_choices(payload: Dict[str, Any] = None) -> Dict[str, Dict[str, Any]]:
    """Return only choices whose status is ``accepted`` (empty unless Accepted)."""
    payload = payload if payload is not None else load_choices()
    if payload.get("dr_status") != DR_ACCEPTED_STATUS:
        return {}
    return {
        choice["id"]: choice
        for choice in payload["choices"]
        if choice.get("status") == ACCEPTED_STATUS
    }


def require_accepted(choice_id: str, payload: Dict[str, Any] = None) -> Dict[str, Any]:
    """Consumer refusal gate: return the choice only if contract-accepted.

    Raises :class:`ChoiceNotAccepted` unless DR-0008 is Accepted AND the
    entry carries ``status == "accepted"``, per the consumer rule in
    DR-0008 Section 12 and issue #53 ("Choices are data").
    """
    payload = payload if payload is not None else load_choices()
    if payload.get("dr_status") != DR_ACCEPTED_STATUS:
        raise ChoiceNotAccepted(
            f"choice {choice_id} cannot be consumed: DR-0008 status is "
            f"{payload.get('dr_status')!r}, not {DR_ACCEPTED_STATUS!r}; "
            "refusing to consume it where the contract requires an accepted "
            "value (DR-0008 Section 12; issue #53)"
        )
    choice = get(choice_id, payload)
    if choice.get("status") != ACCEPTED_STATUS:
        raise ChoiceNotAccepted(
            f"choice {choice_id} is {choice.get('status')!r}, not accepted; "
            "refusing to consume it where the contract requires an accepted value "
            "(DR-0008 Section 12; issue #53)"
        )
    return choice
