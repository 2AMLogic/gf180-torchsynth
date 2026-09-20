#!/usr/bin/env python3
"""Preregistered listening-response analysis skeleton for the issue 44 protocol.

Agent-doable scaffolding declared in the issue 44 protocol-draft comment
(item 5: analysis tooling). The analysis specification is read from the
ratified protocol config; nothing here decides thresholds or scores.

Fail-closed gates, in order:

1. The protocol config must be ratified (``ratified: true`` with an adopted
   ratification block). Unratified configs are refused outright: this tool
   never previews what a protocol "would" show.
2. The operator's AC6 ethics decision box must be complete (all fields
   non-null); nulls keep analysis blocked even post-ratification.
3. The response set must bind the exact config content identity
   (``protocol_config_sha256``); mismatched or unknown stimulus codes are
   refused, and codes are re-derived from the config to catch condition-map
   drift.

Analyses (per the config's preregistration; rows only, no aggregate score):

- Per condition (operator x ladder step): detection proportion with an exact
  Clopper-Pearson interval, plus the independently recorded same-patch
  identity proportion (AC3; never merged with detection).
- Per ladder (per case and per operator): Spearman rank correlation between
  the ground-truth ladder position (L1..L5 array rank in the config) and
  judged detection, plus a monotonicity check on detection across L1..L5.
  A non-monotone ladder is flagged ``invalid_anchor`` — reported, never
  smoothed or reinterpreted.
- Identity red flags: identity failures at at-chance (sub-audible) steps are
  emitted as distinct red-flag rows.
- Listener validity: catch-trial false-positive rate and session-anchor
  failures against the config's invalidation limits.
- MUSHRA blocks: bracket validation (hidden reference/anchor must bracket)
  and per-item rating rows.

Response-set contract (written by the later blinded-presentation harness,
scaffold item 4; schema ``torchsynth-listening-responses``):

- ``protocol_config_sha256``: the ratified config's content identity.
- ``unblinded``: true — analysis runs only post-unblind (OBS section 6).
- ``condition_map``: list of ``{stimulus_code, case_id, operator,
  ladder_step, audio_sha256}``; codes are re-derived from the config and any
  drift refuses the run.
- ``listeners``: list of ``{listener_code, session_anchor_passed,
  abx_trials: [{stimulus_code, detected, identity_same_patch}],
  catch_trials: [{stimulus_code, false_positive}],
  mushra_blocks: [{block_id, ratings: [{stimulus_code, rating}]}]}``.
  ``detected`` is the 3AFC quality response; ``identity_same_patch`` is the
  independent forced identity response (AC3). The two are never merged.

Exit codes: 0 success, 2 refusal (gate/binding), 1 unexpected error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from listening_protocol_common import (  # noqa: E402
    ANALYSIS_SCHEMA,
    RESPONSES_SCHEMA,
    ProtocolConfigError,
    canonical_bytes,
    clopper_pearson,
    config_identity,
    declared_operators,
    ethics_complete,
    load_config,
    mean,
    spearman,
    stimulus_code,
)

REQUIRED_TOP = {
    "schema",
    "schema_version",
    "protocol_config_sha256",
    "unblinded",
    "condition_map",
    "listeners",
}


class AnalysisRefusal(RuntimeError):
    pass


def _condition_index(
    cfg: Dict[str, Any], cfg_id_hash: str, condition_map: Any
) -> Dict[str, Dict[str, Any]]:
    """Validated stimulus_code -> condition, with codes re-derived from config."""
    if not isinstance(condition_map, list) or not condition_map:
        raise AnalysisRefusal("response set condition_map: expected nonempty list")
    declared = declared_operators(cfg)
    index: Dict[str, Dict[str, Any]] = {}
    for entry in condition_map:
        if not isinstance(entry, dict):
            raise AnalysisRefusal("condition_map entry: expected object")
        code = entry.get("stimulus_code")
        case_id = entry.get("case_id")
        operator = entry.get("operator") or ""
        step = entry.get("ladder_step")
        if not isinstance(code, str) or not isinstance(case_id, str):
            raise AnalysisRefusal(
                "condition_map entry: stimulus_code and case_id required"
            )
        expected = stimulus_code(cfg_id_hash, case_id, operator, step)
        if code != expected:
            raise AnalysisRefusal(
                "condition_map drift: stimulus_code %r does not match the config-derived code %r "
                "for (case=%s, operator=%r, step=%r); refusing unblinded analysis of an "
                "unverifiable map" % (code, expected, case_id, operator, step)
            )
        if operator:
            spec = declared.get(operator)
            if spec is None:
                raise AnalysisRefusal(
                    "condition_map operator %r is not declared in the ratified config"
                    % operator
                )
            if spec["kind"] == "ladder":
                if type(step) is not int or not (1 <= step <= len(spec["points"])):
                    raise AnalysisRefusal(
                        "condition_map step %r out of ladder range for %r"
                        % (step, operator)
                    )
                magnitude = spec["points"][step - 1]
            else:
                if step is not None:
                    raise AnalysisRefusal(
                        "binary trial %r must not carry a ladder step" % operator
                    )
                magnitude = None
            index[code] = {
                "case_id": case_id,
                "operator": operator,
                "kind": spec["kind"],
                "ladder_step": step,
                "magnitude": magnitude,
                "unit": spec.get("unit"),
                "audio_sha256": entry.get("audio_sha256"),
            }
        else:
            if step is not None:
                raise AnalysisRefusal(
                    "reference condition must not carry a ladder step"
                )
            index[code] = {
                "case_id": case_id,
                "operator": "",
                "kind": "reference",
                "ladder_step": None,
                "magnitude": None,
                "unit": None,
                "audio_sha256": entry.get("audio_sha256"),
            }
    return index


def _load_responses(responses_path: Path, cfg_id_hash: str) -> Dict[str, Any]:
    try:
        responses = json.loads(responses_path.read_bytes().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AnalysisRefusal(
            "responses %s is not valid UTF-8 JSON: %s" % (responses_path, error)
        ) from error
    if not isinstance(responses, dict) or set(responses) != REQUIRED_TOP:
        missing = sorted(
            REQUIRED_TOP - set(responses if isinstance(responses, dict) else {})
        )
        extra = sorted(
            set(responses if isinstance(responses, dict) else {}) - REQUIRED_TOP
        )
        raise AnalysisRefusal(
            "response set key set mismatch (missing: %s; unexpected: %s)"
            % (", ".join(missing) or "none", ", ".join(extra) or "none")
        )
    if responses["schema"] != RESPONSES_SCHEMA:
        raise AnalysisRefusal(
            "response set schema %r is not %r" % (responses["schema"], RESPONSES_SCHEMA)
        )
    if responses["schema_version"] != 1:
        raise AnalysisRefusal("response set schema_version must be 1")
    if responses.get("unblinded") is not True:
        raise AnalysisRefusal(
            "response set unblinded is not true: analysis is post-unblind only (OBS section 6)"
        )
    binding = responses.get("protocol_config_sha256")
    if binding != cfg_id_hash:
        raise AnalysisRefusal(
            "response set binds config identity %r but the ratified config is %r; refusing to "
            "analyze responses from a different protocol" % (binding, cfg_id_hash)
        )
    return responses


def _at_chance(low: float, high: float, chance: float) -> bool:
    return low <= chance <= high


def analyze(
    cfg: Dict[str, Any], cfg_id_hash: str, responses: Dict[str, Any]
) -> Dict[str, Any]:
    alpha = cfg["analysis"]["detection"]["alpha"]
    chance = cfg["analysis"]["detection"]["chance_rate"]
    red_flag_below = cfg["analysis"]["identity_question"][
        "red_flag_same_patch_rate_below"
    ]
    catch_max = cfg["session"]["invalidation"]["catch_false_positive_rate_max"]
    condition = _condition_index(cfg, cfg_id_hash, responses["condition_map"])

    detection_acc: Dict[Tuple[str, Optional[int], str], List[bool]] = {}
    identity_acc: Dict[Tuple[str, Optional[int], str], List[bool]] = {}
    for listener in responses["listeners"]:
        listener_code = listener.get("listener_code")
        if not isinstance(listener_code, str) or not listener_code:
            raise AnalysisRefusal("listener without listener_code")
        for trial in listener.get("abx_trials") or []:
            code = trial.get("stimulus_code")
            if code not in condition:
                raise AnalysisRefusal(
                    "abx trial references unknown stimulus code %r" % (code,)
                )
            cond = condition[code]
            if cond["kind"] == "reference":
                continue
            key = (cond["operator"], cond["ladder_step"], cond["case_id"])
            detection_acc.setdefault(key, []).append(bool(trial.get("detected")))
            identity = trial.get("identity_same_patch")
            if identity is not None:
                identity_acc.setdefault(key, []).append(bool(identity))
        # listener validity rows are computed below from the same trials
    listener_rows: List[Dict[str, Any]] = []
    for listener in responses["listeners"]:
        catches = listener.get("catch_trials") or []
        false_positives = sum(1 for trial in catches if trial.get("false_positive"))
        rate = false_positives / len(catches) if catches else 0.0
        anchor_ok = listener.get("session_anchor_passed")
        invalidated = rate > catch_max or anchor_ok is False
        row = {
            "listener_code": listener["listener_code"],
            "catch_trials": len(catches),
            "catch_false_positives": false_positives,
            "catch_false_positive_rate": rate,
            "catch_limit": catch_max,
            "session_anchor_passed": anchor_ok,
            "invalidated": invalidated,
            "replacement_allowed": invalidated,
        }
        listener_rows.append(row)

    # Per (operator, step): aggregated across cases and listeners.
    detection_rows: List[Dict[str, Any]] = []
    per_case: Dict[Tuple[str, str], Dict[int, List[float]]] = {}
    keys = sorted(detection_acc, key=lambda k: (k[0], k[1] is None, k[1] or 0, k[2]))
    for operator, step, case_id in keys:
        detections = detection_acc[(operator, step, case_id)]
        n = len(detections)
        successes = sum(1 for value in detections if value)
        low, high = clopper_pearson(successes, n, alpha)
        identities = identity_acc.get((operator, step, case_id)) or []
        identity_rate = (
            (sum(1 for value in identities if value) / len(identities))
            if identities
            else None
        )
        declared = declared_operators(cfg).get(operator)
        magnitude = None
        if declared and declared["kind"] == "ladder" and step is not None:
            magnitude = declared["points"][step - 1]
        detection_rows.append(
            {
                "operator": operator,
                "ladder_step": step,
                "magnitude": magnitude,
                "unit": (declared or {}).get("unit"),
                "case_id": case_id,
                "n": n,
                "detections": successes,
                "proportion": successes / n,
                "ci_low": low,
                "ci_high": high,
                "ci_alpha": alpha,
                "at_chance": _at_chance(low, high, chance),
                "identity_n": len(identities),
                "identity_same_patch_proportion": identity_rate,
            }
        )
        bucket = per_case.setdefault((operator, case_id), {})
        if step is not None:
            bucket.setdefault(step, []).append(successes / n)

    ladder_rows: List[Dict[str, Any]] = []
    monotonicity_rows: List[Dict[str, Any]] = []
    for (operator, case_id), bucket in sorted(per_case.items()):
        steps = sorted(bucket)
        if len(steps) < 2:
            continue
        severity = [float(step) for step in steps]
        observed = [mean(bucket[step]) for step in steps]
        rho = spearman(severity, observed)
        violations = [
            {
                "at_step": steps[i + 1],
                "rate_drop_from": observed[i],
                "rate_to": observed[i + 1],
            }
            for i in range(len(observed) - 1)
            if observed[i + 1] < observed[i] - 1e-12
        ]
        ladder_rows.append(
            {
                "operator": operator,
                "case_id": case_id,
                "spearman_rho": rho,
                "ladder_positions": steps,
                "detection_rates": observed,
                "monotone": not violations,
                "note": "ground-truth axis is the config ladder position rank, not the numeric magnitude",
            }
        )
        if violations:
            monotonicity_rows.append(
                {
                    "operator": operator,
                    "case_id": case_id,
                    "verdict": "invalid_anchor",
                    "violations": violations,
                    "resolution": "repair in a new protocol version; never smoothed or reinterpreted",
                }
            )

    group_monotonicity_rows: List[Dict[str, Any]] = []
    for operator in sorted({op for op, _ in per_case}):
        steps_all: Dict[int, List[float]] = {}
        for (op, _case), bucket in per_case.items():
            if op != operator:
                continue
            for step, values in bucket.items():
                steps_all.setdefault(step, []).extend(values)
        steps = sorted(steps_all)
        if len(steps) < 2:
            continue
        observed = [mean(steps_all[step]) for step in steps]
        violations = [
            {
                "at_step": steps[i + 1],
                "rate_drop_from": observed[i],
                "rate_to": observed[i + 1],
            }
            for i in range(len(observed) - 1)
            if observed[i + 1] < observed[i] - 1e-12
        ]
        rho = spearman([float(s) for s in steps], observed)
        group_monotonicity_rows.append(
            {
                "operator": operator,
                "scope": "all_cases",
                "spearman_rho": rho,
                "ladder_positions": steps,
                "detection_rates": observed,
                "monotone": not violations,
                "verdict": "valid_anchor" if not violations else "invalid_anchor",
            }
        )

    red_flag_rows: List[Dict[str, Any]] = []
    for row in detection_rows:
        if (
            row["identity_same_patch_proportion"] is not None
            and row["identity_same_patch_proportion"] < red_flag_below
            and row["at_chance"]
        ):
            red_flag_rows.append(
                {
                    "operator": row["operator"],
                    "ladder_step": row["ladder_step"],
                    "magnitude": row["magnitude"],
                    "case_id": row["case_id"],
                    "reason": "identity_failure_at_subaudible_magnitude",
                    "identity_same_patch_proportion": row[
                        "identity_same_patch_proportion"
                    ],
                    "detection_at_chance": True,
                    "note": "identity failures before detectability: AC3 red flag, reported not merged",
                }
            )

    mushra_rows: List[Dict[str, Any]] = []
    if cfg["method"]["mushra"] and "mushra" in cfg["method"]["parts"]:
        scale_low, scale_high = cfg["method"]["mushra"]["scale"]
        blocks: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for listener in responses["listeners"]:
            for block in listener.get("mushra_blocks") or []:
                block_id = block.get("block_id")
                if not isinstance(block_id, str) or not block_id:
                    raise AnalysisRefusal("mushra block without block_id")
                for rating in block.get("ratings") or []:
                    code = rating.get("stimulus_code")
                    if code not in condition:
                        raise AnalysisRefusal(
                            "mushra rating references unknown stimulus code %r"
                            % (code,)
                        )
                    value = rating.get("rating")
                    if type(value) not in (int, float) or not (
                        scale_low <= value <= scale_high
                    ):
                        raise AnalysisRefusal(
                            "mushra rating %r outside declared scale [%r, %r]"
                            % (value, scale_low, scale_high)
                        )
                    entry = blocks.setdefault(
                        (block_id, code), {"ratings": [], "roles": set()}
                    )
                    entry["ratings"].append(float(value))
                    entry["roles"].add(condition[code]["kind"])
        for (block_id, code), entry in sorted(blocks.items()):
            cond = condition[code]
            kind = "hidden_reference" if cond["kind"] == "reference" else "degraded"
            ratings = entry["ratings"]
            mushra_rows.append(
                {
                    "block_id": block_id,
                    "stimulus_code": code,
                    "operator": cond["operator"] or None,
                    "ladder_step": cond["ladder_step"],
                    "role": kind,
                    "n": len(ratings),
                    "mean_rating": sum(ratings) / len(ratings),
                }
            )
        bracket_rows: List[Dict[str, Any]] = []
        by_block: Dict[str, Dict[str, List[float]]] = {}
        for row in mushra_rows:
            role_bucket = by_block.setdefault(row["block_id"], {})
            role_bucket.setdefault(row["role"], []).append(row["mean_rating"])
        for block_id, roles in sorted(by_block.items()):
            ref_ratings = roles.get("hidden_reference") or []
            anchor_candidate = roles.get("degraded") or []
            valid = bool(ref_ratings)
            if valid:
                ref_mean = max(ref_ratings)
                others = [value for value in anchor_candidate if value < ref_mean]
                valid = (
                    bool(others)
                    if cfg["method"]["mushra"]["bracket_validation"]
                    else valid
                )
            bracket_rows.append(
                {
                    "block_id": block_id,
                    "bracket_valid": valid,
                    "hidden_reference_mean": max(ref_ratings) if ref_ratings else None,
                    "note": "hidden reference and anchor must bracket the block ratings or the block is invalidated",
                }
            )
        mushra_rows = mushra_rows + bracket_rows

    analysis = {
        "schema": ANALYSIS_SCHEMA,
        "schema_version": 1,
        "protocol_config": {
            "config_id": cfg["config_id"],
            "config_identity_sha256": cfg_id_hash,
            "ratified": cfg["ratified"],
        },
        "score_policy": "rows_only_no_aggregate",
        "detection_rows": detection_rows,
        "ladder_rows": ladder_rows,
        "monotonicity_rows": monotonicity_rows + group_monotonicity_rows,
        "red_flag_rows": red_flag_rows,
        "listener_validity_rows": listener_rows,
        "mushra_rows": mushra_rows,
        "gates": {
            "ratified": cfg["ratified"],
            "ethics_box_complete": not ethics_complete(cfg),
            "unblinded": responses["unblinded"],
        },
    }
    return analysis


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--config", required=True, type=Path, help="ratified protocol config JSON"
    )
    parser.add_argument(
        "--responses", required=True, type=Path, help="response set JSON (post-unblind)"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="write analysis JSON here instead of stdout",
    )
    args = parser.parse_args(argv)
    try:
        matrix_path = (
            Path(__file__).resolve().parents[1]
            / "sim"
            / "reference"
            / "mutation-matrix-v1.json"
        )
        matrix = json.loads(matrix_path.read_bytes().decode("utf-8"))
        cfg = load_config(args.config, matrix=matrix)
        if cfg["ratified"] is not True:
            raise AnalysisRefusal(
                "protocol config %r is not ratified: analysis is inert until the operator ratifies "
                "the listening protocol (fail-closed; no preview analyses)."
                % cfg["config_id"]
            )
        pending = ethics_complete(cfg)
        if pending:
            raise AnalysisRefusal(
                "AC6 ethics decision box incomplete (null fields: %s); no collection or analysis "
                "may run until the operator fills and approves it." % ", ".join(pending)
            )
        cfg_id_hash = config_identity(cfg)
        responses = _load_responses(args.responses, cfg_id_hash)
        analysis = analyze(cfg, cfg_id_hash, responses)
    except (ProtocolConfigError, AnalysisRefusal) as refusal:
        print("REFUSED: %s" % refusal, file=sys.stderr)
        return 2
    payload = canonical_bytes(analysis) + b"\n"
    if args.out is not None:
        args.out.write_bytes(payload)
        print("wrote %s" % args.out)
    else:
        sys.stdout.buffer.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
