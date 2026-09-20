"""Shared machinery for the issue 44 listening scaffolding (inert by design).

This module parameterizes the mechanical, agent-doable scaffolding declared in
the issue 44 protocol-draft comment: a config-driven stimulus generator and a
preregistered analysis skeleton. Everything perceptual (ladder values, trial
counts, level conditions, stopping rules, the ethics box) lives in a protocol
config document validated against
``spec/reference/listening-protocol-config-v1.schema.json``; nothing is
hardcoded to one protocol design here.

Inertness contract:

- ``ratified: false`` configs (or any refusal) can never silently produce
  protocol evidence. The generator stamps every output
  ``not_protocol_evidence`` unless the config is adopted; the analyzer refuses
  unratified configs outright.
- The development/holdout boundary is refused structurally: the only
  selectable pool is the landed corpus's development partition; any
  holdout-split case in scope refuses the run.
- Custody is receipts-only: stimulus audio is never written inside the
  repository, and receipts carry SHA-256 identities, not samples.

Plan-identity honesty: the landed mutation plan contract (``mp1-``) binds
executed magnitudes on declared fixture seams. Corpus audio is a final mix
output, not a declared ``voice.post_module`` trace, so an honest mp1- binding
attempt for corpus-audio stimuli is refused by the landed contract; receipts
record that refusal verbatim alongside a separate domain-separated ``ls1-``
stimulus identity. Declaring a corpus-audio mix-output seam is an
operator/architect decision at ratification, not something this scaffold
invents silently.

Stdlib only; imports the landed ``torchsynth_voice`` mutation modules for
operator applications and magnitude ranges. No Torch, no audio files read or
written by this module itself.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import struct
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

CONFIG_SCHEMA_PATH = (
    ROOT / "spec" / "reference" / "listening-protocol-config-v1.schema.json"
)
MATRIX_PATH = ROOT / "sim" / "reference" / "mutation-matrix-v1.json"
UPSTREAM_PATH = ROOT / "spec" / "reference" / "upstream.json"

CONFIG_SCHEMA = "torchsynth-listening-protocol-config"
STIMULUS_MANIFEST_SCHEMA = "torchsynth-listening-stimulus-manifest"
RESPONSES_SCHEMA = "torchsynth-listening-responses"
ANALYSIS_SCHEMA = "torchsynth-listening-analysis"

RENDER_CORPUS_AUDIO = "corpus-audio"
RENDER_HOST_RUN_GATED = "host-run-gated"
RENDER_TARGETS = (RENDER_CORPUS_AUDIO, RENDER_HOST_RUN_GATED)

# Development partition only; the holdout stays sealed (measurement plan).
DEV_POOL = "development-corpus"

# Ladder operators excluded from listening scope by the protocol draft's
# family routing: apparatus gates whose provenance refusals occur before any
# perceptual row, family shams, and the whole framework family.
NON_AUDIO_IDENTITY_OPERATORS = frozenset(
    {
        "identity.batch_shift",
        "identity.train_test_flip",
        "param.positional_shuffle",
        "param.conversion_substitute",
    }
)
SHAM_OPERATORS = frozenset({"signal.sham", "noop.sham", "bridge.sham_slot"})

SUPPORTED_SELECTORS = (
    "exact_silence",
    "near_silence",
    "normalization_inactive",
    "unqualified_pitch_property_tag",
)

ETHICS_FIELDS = (
    "consent_text",
    "identity_handling",
    "raw_response_retention",
    "access_control",
    "publication_policy",
    "compensation",
)

LADDER_POINTS = 5
PATTERN_OPERATOR = "^[A-Za-z0-9._]+$"


class ProtocolConfigError(ValueError):
    """Refusal while loading or validating a protocol config."""


class CustodyRefusal(RuntimeError):
    """Refusal of an operation that would break receipts-only custody."""


# ---------------------------------------------------------------------------
# Canonical serialization and identities
# ---------------------------------------------------------------------------


def canonical_bytes(document: Any) -> bytes:
    """Deterministic JSON bytes for identity hashing (sorted keys, no spaces)."""
    return json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def config_identity(cfg: Dict[str, Any]) -> str:
    """Content identity of a parsed config (independent of file whitespace)."""
    return sha256_hex(canonical_bytes(cfg))


def stimulus_code(
    config_id_hash: str, case_id: str, operator: str, step: Optional[int]
) -> str:
    """Domain-separated opaque stimulus code.

    Codes are derived deterministically so the same config+seed reproduces the
    same receipts; they are opaque (they do not encode the condition), while
    the full code-to-condition map lives only in generator custody receipts.
    """
    key = "|".join(
        [
            "listening-stimulus-code-v1",
            config_id_hash,
            case_id,
            operator,
            "" if step is None else str(step),
        ]
    )
    return "ls1-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def ls1_stimulus_identity(
    config_id_hash: str,
    matrix_sha256: str,
    implementation_sha256: str,
    case_id: str,
    operator: str,
    step: Optional[int],
    magnitude: Any,
) -> str:
    """Deterministic binding of one executed stimulus condition.

    Separate domain from ``mp1-`` plan identities: this is a listening-scaffold
    identity over the exact executed condition, used because the landed mp1-
    contract refuses corpus-audio mix-output seams (see module docstring).
    """
    payload = canonical_bytes(
        {
            "domain": "listening-stimulus-identity-v1",
            "config_identity": config_id_hash,
            "matrix_sha256": matrix_sha256,
            "implementation_sha256": implementation_sha256,
            "case_id": case_id,
            "operator": operator,
            "ladder_step": step,
            "magnitude": magnitude,
        }
    )
    return "ls1i-" + sha256_hex(payload)


# ---------------------------------------------------------------------------
# Matrix grounding
# ---------------------------------------------------------------------------


def matrix_operator_specs(matrix: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Map operator -> declared magnitude spec and families from the matrix."""
    specs: Dict[str, Dict[str, Any]] = {}
    families = matrix.get("registry", {}).get("families", {})
    op_family: Dict[str, str] = {}
    for family, names in families.items():
        for name in names:
            op_family[name] = family
    for row in matrix.get("faults_to_tests", []):
        for entry in row.get("magnitude", []):
            op = entry.get("operator")
            spec = entry.get("magnitude")
            if op is None or not isinstance(spec, dict):
                continue
            current = specs.setdefault(
                op,
                {
                    "unit": spec.get("unit"),
                    "type": spec.get("type"),
                    "minimum": spec.get("minimum"),
                    "maximum": spec.get("maximum"),
                    "family": op_family.get(op),
                },
            )
            if current["unit"] != spec.get("unit") or current["type"] != spec.get(
                "type"
            ):
                raise ProtocolConfigError(
                    "matrix declares inconsistent magnitude specs for operator %r" % op
                )
    return specs


def listening_scope_refusals(matrix: Dict[str, Any]) -> Dict[str, str]:
    """Operators outside listening scope, with the reason (draft section 2)."""
    refusals: Dict[str, str] = {}
    families = matrix.get("registry", {}).get("families", {})
    for name in families.get("framework", []):
        refusals[name] = "framework family: #30 apparatus proof, not audio"
    for name in NON_AUDIO_IDENTITY_OPERATORS:
        refusals[name] = (
            "identity apparatus gate: provenance refusal occurs before perceptual rows"
        )
    for name in SHAM_OPERATORS:
        refusals[name] = "sham control: never a fault, never a stimulus"
    return refusals


def _within_declared_range(value: float, spec: Dict[str, Any]) -> bool:
    low, high = spec.get("minimum"), spec.get("maximum")
    if low is None or high is None:
        return False
    tol = 1e-12 * max(1.0, abs(low), abs(high))
    return (low - tol) <= value <= (high + tol)


# ---------------------------------------------------------------------------
# Config validation (stdlib enforcement of the JSON Schema contract)
# ---------------------------------------------------------------------------


def _object(
    value: Any,
    where: str,
    errors: List[str],
    required: Sequence[str],
    optional: Sequence[str] = (),
) -> Dict[str, Any]:
    if not isinstance(value, dict):
        errors.append("%s: expected object" % where)
        return {}
    allowed = set(required) | set(optional)
    extra = sorted(set(value) - allowed)
    if extra:
        errors.append("%s: unexpected key(s) %s" % (where, ", ".join(extra)))
    missing = sorted(set(required) - set(value))
    if missing:
        errors.append("%s: missing key(s) %s" % (where, ", ".join(missing)))
    return value


def _finite_number(value: Any, where: str, errors: List[str]) -> Optional[float]:
    if type(value) not in (int, float) or isinstance(value, bool):
        errors.append("%s: expected finite number" % where)
        return None
    if not math.isfinite(value):
        errors.append("%s: must be finite" % where)
        return None
    return float(value)


def validate_config(
    cfg: Any, matrix: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Validate a parsed config document; raise ProtocolConfigError on refusal.

    Mirrors listening-protocol-config-v1.schema.json fail-closed in stdlib
    (the repo's tooling is stdlib-first), plus matrix-grounded checks the
    schema cannot express: declared ranges, listening-scope family routing,
    and level-condition assignment referents.
    """
    errors: List[str] = []
    if not isinstance(cfg, dict):
        raise ProtocolConfigError("config: expected object")
    required_top = [
        "schema",
        "schema_version",
        "config_id",
        "ratified",
        "ratification",
        "method",
        "ladders",
        "binary_trials",
        "selection",
        "blinding",
        "level_conditions",
        "session",
        "analysis",
        "ethics",
    ]
    _object(cfg, "config", errors, required_top, [])
    if errors:
        raise ProtocolConfigError("; ".join(errors))
    if cfg["schema"] != CONFIG_SCHEMA:
        errors.append("config.schema: must be %r" % CONFIG_SCHEMA)
    if cfg["schema_version"] != 1:
        errors.append("config.schema_version: must be 1")
    config_id = cfg["config_id"]
    if not isinstance(config_id, str) or not config_id:
        errors.append("config.config_id: expected nonempty string")
    elif not all(c.isalnum() or c in "._-" for c in config_id):
        errors.append("config.config_id: must match %s" % PATTERN_OPERATOR)

    # ratified + ratification coherence
    ratified = cfg["ratified"]
    if type(ratified) is not bool:
        errors.append("config.ratified: expected boolean")
    rat = _object(
        cfg["ratification"],
        "ratification",
        errors,
        ["status", "decided_by", "decided_utc", "protocol_version", "notes"],
    )
    if rat:
        status = rat.get("status")
        if status not in ("draft", "adopted"):
            errors.append("ratification.status: must be 'draft' or 'adopted'")
        if ratified is True:
            if status != "adopted":
                errors.append("ratified=true requires ratification.status 'adopted'")
            for field in ("decided_by", "decided_utc"):
                value = rat.get(field)
                if not isinstance(value, str) or not value:
                    errors.append(
                        "ratified=true requires nonempty ratification.%s" % field
                    )

    # method
    method = _object(cfg["method"], "method", errors, ["parts", "abx", "mushra"])
    parts = method.get("parts")
    if not isinstance(parts, list) or not parts or len(parts) != len(set(parts)):
        errors.append("method.parts: expected nonempty list of unique entries")
    else:
        for part in parts:
            if part not in ("abx", "mushra"):
                errors.append("method.parts: unknown part %r" % (part,))
    abx = _object(
        method.get("abx"),
        "method.abx",
        errors,
        ["format", "catch_trial_rate", "session_anchor"],
    )
    if abx:
        if abx.get("format") != "3afc-ax":
            errors.append("method.abx.format: must be '3afc-ax'")
        rate = _finite_number(
            abx.get("catch_trial_rate"), "method.abx.catch_trial_rate", errors
        )
        if rate is not None and not (0.0 <= rate <= 0.5):
            errors.append("method.abx.catch_trial_rate: must be within [0, 0.5]")
        if type(abx.get("session_anchor")) is not bool:
            errors.append("method.abx.session_anchor: expected boolean")
    mushra = _object(
        method.get("mushra"),
        "method.mushra",
        errors,
        [
            "items_per_block",
            "scale",
            "anchor_operator",
            "anchor_magnitude",
            "bracket_validation",
        ],
    )
    anchor_points: Tuple[str, float] | None = None
    if mushra:
        items = mushra.get("items_per_block")
        if type(items) is not int or items < 4:
            errors.append("method.mushra.items_per_block: expected integer >= 4")
        scale = mushra.get("scale")
        if not isinstance(scale, list) or len(scale) != 2:
            errors.append("method.mushra.scale: expected [low, high]")
        else:
            for i, bound in enumerate(scale):
                if _finite_number(bound, "method.mushra.scale[%d]" % i, errors) is None:
                    pass
            if len(scale) == 2 and scale[0] >= scale[1]:
                errors.append("method.mushra.scale: low bound must be < high bound")
        anchor_op = mushra.get("anchor_operator")
        if not isinstance(anchor_op, str) or not anchor_op:
            errors.append("method.mushra.anchor_operator: expected nonempty string")
        anchor_mag = _finite_number(
            mushra.get("anchor_magnitude"), "method.mushra.anchor_magnitude", errors
        )
        if anchor_op and anchor_mag is not None:
            anchor_points = (anchor_op, anchor_mag)
        if type(mushra.get("bracket_validation")) is not bool:
            errors.append("method.mushra.bracket_validation: expected boolean")

    # ladders and binary trials (structure first; matrix checks below)
    ladders = cfg["ladders"]
    if not isinstance(ladders, dict) or not ladders:
        errors.append("ladders: expected nonempty object")
    declared_ops: Dict[str, Dict[str, Any]] = {}
    if isinstance(ladders, dict):
        for op, spec in ladders.items():
            where = "ladders[%s]" % op
            if not isinstance(op, str) or not op:
                errors.append("%s: operator key must be a nonempty string" % where)
                continue
            body = _object(spec, where, errors, ["render", "unit", "points"], ["note"])
            if not body:
                continue
            if body.get("render") not in RENDER_TARGETS:
                errors.append(
                    "%s.render: must be one of %s" % (where, ", ".join(RENDER_TARGETS))
                )
            unit = body.get("unit")
            if not isinstance(unit, str) or not unit:
                errors.append("%s.unit: expected nonempty string" % where)
            points = body.get("points")
            if not isinstance(points, list) or len(points) != LADDER_POINTS:
                errors.append(
                    "%s.points: expected exactly %d magnitudes L1..L5"
                    % (where, LADDER_POINTS)
                )
            else:
                for i, point in enumerate(points):
                    if (
                        _finite_number(point, "%s.points[%d]" % (where, i), errors)
                        is None
                    ):
                        pass
                else:
                    declared_ops[op] = {
                        "kind": "ladder",
                        "unit": unit,
                        "points": points,
                        "render": body.get("render"),
                    }
    binaries = cfg["binary_trials"]
    if not isinstance(binaries, dict):
        errors.append("binary_trials: expected object")
    elif isinstance(binaries, dict):
        for op, spec in binaries.items():
            where = "binary_trials[%s]" % op
            body = _object(spec, where, errors, ["render"], ["note"])
            if not body:
                continue
            if body.get("render") not in RENDER_TARGETS:
                errors.append(
                    "%s.render: must be one of %s" % (where, ", ".join(RENDER_TARGETS))
                )
            else:
                declared_ops[op] = {"kind": "binary", "render": body.get("render")}
    overlap = sorted(
        set(ladders if isinstance(ladders, dict) else {})
        & set(binaries if isinstance(binaries, dict) else {})
    )
    if overlap:
        errors.append(
            "operator declared as both ladder and binary trial: %s" % ", ".join(overlap)
        )

    # matrix-grounded cross-checks
    if matrix is not None:
        specs = matrix_operator_specs(matrix)
        refusals = listening_scope_refusals(matrix)
        unknown = sorted(set(declared_ops) - set(specs))
        if unknown:
            errors.append(
                "operators not declared in the mutation matrix: %s" % ", ".join(unknown)
            )
        for op in sorted(declared_ops):
            if op in refusals:
                errors.append("ladders/binary_trials[%s]: %s" % (op, refusals[op]))
        if anchor_points is not None:
            anchor_op, anchor_mag = anchor_points
            anchor_in_ladders = isinstance(ladders, dict) and anchor_op in ladders
            if anchor_in_ladders:
                points = ladders[anchor_op].get("points")
                if isinstance(points, list) and not any(
                    type(p) in (int, float) and float(p) == anchor_mag for p in points
                ):
                    errors.append(
                        "method.mushra.anchor_magnitude %r is not a ladder point of %r"
                        % (anchor_mag, anchor_op)
                    )
            else:
                errors.append(
                    "method.mushra.anchor_operator %r is not a declared ladder"
                    % anchor_op
                )
            for name, spec in sorted(declared_ops.items()):
                matrix_spec = specs.get(name)
                if matrix_spec is None:
                    continue
                declared_unit = (
                    ladders.get(name, {}).get("unit") if name in ladders else None
                )
                if declared_unit is not None and declared_unit != matrix_spec["unit"]:
                    errors.append(
                        "ladders[%s].unit %r disagrees with the declared matrix unit %r"
                        % (name, declared_unit, matrix_spec["unit"])
                    )
                if spec["kind"] == "ladder":
                    for i, point in enumerate(spec["points"]):
                        if not _within_declared_range(float(point), matrix_spec):
                            errors.append(
                                "ladders[%s].points[%d]=%r outside the declared matrix range [%r, %r] %s"
                                % (
                                    name,
                                    i,
                                    point,
                                    matrix_spec["minimum"],
                                    matrix_spec["maximum"],
                                    matrix_spec["unit"],
                                )
                            )
                elif not (
                    matrix_spec["type"] == "null"
                    or (
                        matrix_spec["minimum"] is not None
                        and matrix_spec["minimum"] == matrix_spec["maximum"]
                    )
                ):
                    errors.append(
                        "binary_trials[%s]: the matrix declares a ranged magnitude, not a single point"
                        % name
                    )

    # selection
    sel = _object(
        cfg["selection"],
        "selection",
        errors,
        ["pool", "cases_per_operator", "seed", "stratification", "exclusions"],
    )
    if sel:
        if sel.get("pool") != DEV_POOL:
            errors.append(
                "selection.pool: the only selectable pool is %r (holdout sealed)"
                % DEV_POOL
            )
        count = sel.get("cases_per_operator")
        if type(count) is not int or not (1 <= count <= 16):
            errors.append("selection.cases_per_operator: expected integer in [1, 16]")
        if type(sel.get("seed")) is not int:
            errors.append("selection.seed: expected integer")
        strat = _object(
            sel.get("stratification"),
            "selection.stratification",
            errors,
            ["normalization_active_required_for"],
        )
        if strat:
            for op in strat.get("normalization_active_required_for") or []:
                if not isinstance(op, str) or not op:
                    errors.append(
                        "selection.stratification entry: expected operator name"
                    )
        exclusions = sel.get("exclusions")
        if not isinstance(exclusions, list) or not exclusions:
            errors.append("selection.exclusions: expected nonempty list")
        else:
            for i, entry in enumerate(exclusions):
                body = _object(
                    entry,
                    "selection.exclusions[%d]" % i,
                    errors,
                    ["selector", "source"],
                    ["applies_to"],
                )
                if body.get("selector") not in SUPPORTED_SELECTORS:
                    errors.append(
                        "selection.exclusions[%d].selector: unsupported selector %r (supported: %s)"
                        % (i, body.get("selector"), ", ".join(SUPPORTED_SELECTORS))
                    )
                if not isinstance(body.get("source"), str) or not body["source"]:
                    errors.append(
                        "selection.exclusions[%d].source: expected nonempty string" % i
                    )
                applies = body.get("applies_to")
                if applies is not None:
                    if not isinstance(applies, list) or not applies:
                        errors.append(
                            "selection.exclusions[%d].applies_to: expected nonempty list"
                            % i
                        )
                    else:
                        for pattern in applies:
                            if not isinstance(pattern, str) or not pattern:
                                errors.append(
                                    "selection.exclusions[%d].applies_to: expected patterns"
                                    % i
                                )

    # blinding
    blinding = _object(
        cfg["blinding"],
        "blinding",
        errors,
        ["condition_map", "randomization_seed", "same_ladder_points_per_case"],
    )
    if blinding:
        if blinding.get("condition_map") != "sealed":
            errors.append("blinding.condition_map: must be 'sealed'")
        if type(blinding.get("randomization_seed")) is not int:
            errors.append("blinding.randomization_seed: expected integer")
        if blinding.get("same_ladder_points_per_case") is not True:
            errors.append(
                "blinding.same_ladder_points_per_case: must be true (OBS 4.2.1 same-choice control)"
            )

    # level conditions
    levels = _object(
        cfg["level_conditions"], "level_conditions", errors, ["C1", "C2", "assignment"]
    )
    if levels:
        c1 = _object(
            levels.get("C1"), "level_conditions.C1", errors, ["name", "rms_matched"]
        )
        if c1 and (
            c1.get("name") != "rms-matched" or c1.get("rms_matched") is not True
        ):
            errors.append("level_conditions.C1: must be rms-matched/rms_matched=true")
        c2 = _object(
            levels.get("C2"), "level_conditions.C2", errors, ["name", "native"]
        )
        if c2 and (c2.get("name") != "native" or c2.get("native") is not True):
            errors.append("level_conditions.C2: must be native/native=true")
        assign = _object(
            levels.get("assignment"),
            "level_conditions.assignment",
            errors,
            ["default", "operators"],
        )
        if assign:
            if assign.get("default") not in ("C1", "C2"):
                errors.append(
                    "level_conditions.assignment.default: must be 'C1' or 'C2'"
                )
            operators = assign.get("operators")
            if not isinstance(operators, dict):
                errors.append("level_conditions.assignment.operators: expected object")
            else:
                for op, condition in operators.items():
                    if condition not in ("C1", "C2"):
                        errors.append(
                            "level_conditions.assignment.operators[%s]: must be 'C1' or 'C2'"
                            % op
                        )
                    if op not in declared_ops:
                        errors.append(
                            "level_conditions.assignment.operators[%s]: not a declared ladder/binary operator"
                            % op
                        )

    # session
    session = _object(
        cfg["session"],
        "session",
        errors,
        ["pilot", "main", "stopping_rule", "invalidation"],
    )
    if session:
        pilot = _object(
            session.get("pilot"), "session.pilot", errors, ["listeners", "purpose"]
        )
        if pilot and (
            type(pilot.get("listeners")) is not int
            or pilot.get("purpose") != "harness-validation-only"
        ):
            errors.append(
                "session.pilot: expected integer listeners and purpose 'harness-validation-only'"
            )
        main = _object(
            session.get("main"),
            "session.main",
            errors,
            ["listeners", "min_non_author_musicians"],
        )
        if main:
            if type(main.get("listeners")) is not int or main["listeners"] < 2:
                errors.append("session.main.listeners: expected integer >= 2")
            if type(main.get("min_non_author_musicians")) is not int:
                errors.append("session.main.min_non_author_musicians: expected integer")
        if session.get("stopping_rule") != "fixed_count":
            errors.append(
                "session.stopping_rule: must be 'fixed_count' (no adaptive extension)"
            )
        invalidation = _object(
            session.get("invalidation"),
            "session.invalidation",
            errors,
            [
                "catch_false_positive_rate_max",
                "anchor_failure_invalidates",
                "replacement_limit",
            ],
        )
        if invalidation:
            rate = _finite_number(
                invalidation.get("catch_false_positive_rate_max"),
                "session.invalidation.catch_false_positive_rate_max",
                errors,
            )
            if rate is not None and not (0.0 < rate <= 1.0):
                errors.append(
                    "session.invalidation.catch_false_positive_rate_max: must be in (0, 1]"
                )
            if invalidation.get("anchor_failure_invalidates") is not True:
                errors.append(
                    "session.invalidation.anchor_failure_invalidates: must be true"
                )
            if (
                type(invalidation.get("replacement_limit")) is not int
                or invalidation["replacement_limit"] < 1
            ):
                errors.append(
                    "session.invalidation.replacement_limit: expected integer >= 1"
                )

    # analysis (fail-closed shape)
    analysis = _object(
        cfg["analysis"],
        "analysis",
        errors,
        ["ladder_validation", "detection", "identity_question", "aggregate_score"],
    )
    if analysis:
        ladder_val = _object(
            analysis.get("ladder_validation"),
            "analysis.ladder_validation",
            errors,
            ["statistic", "magnitude_axis", "monotonicity_required"],
        )
        if ladder_val:
            if ladder_val.get("statistic") != "spearman":
                errors.append(
                    "analysis.ladder_validation.statistic: must be 'spearman'"
                )
            if ladder_val.get("magnitude_axis") != "ladder_position_rank":
                errors.append(
                    "analysis.ladder_validation.magnitude_axis: must be 'ladder_position_rank'"
                )
            if ladder_val.get("monotonicity_required") is not True:
                errors.append(
                    "analysis.ladder_validation.monotonicity_required: must be true"
                )
        detection = _object(
            analysis.get("detection"),
            "analysis.detection",
            errors,
            ["interval", "chance_rate", "alpha"],
        )
        if detection:
            if detection.get("interval") != "clopper_pearson_exact":
                errors.append(
                    "analysis.detection.interval: must be 'clopper_pearson_exact'"
                )
            if detection.get("chance_rate") != 0.5:
                errors.append(
                    "analysis.detection.chance_rate: must be 0.5 (3AFC AX with hidden reference)"
                )
            alpha = _finite_number(
                detection.get("alpha"), "analysis.detection.alpha", errors
            )
            if alpha is not None and not (0.0 < alpha < 1.0):
                errors.append("analysis.detection.alpha: must be in (0, 1)")
        identity_q = _object(
            analysis.get("identity_question"),
            "analysis.identity_question",
            errors,
            ["required", "red_flag_same_patch_rate_below"],
        )
        if identity_q:
            if identity_q.get("required") is not True:
                errors.append("analysis.identity_question.required: must be true (AC3)")
            threshold = _finite_number(
                identity_q.get("red_flag_same_patch_rate_below"),
                "analysis.identity_question.red_flag_same_patch_rate_below",
                errors,
            )
            if threshold is not None and not (0.0 < threshold < 1.0):
                errors.append(
                    "analysis.identity_question.red_flag_same_patch_rate_below: must be in (0, 1)"
                )
        if analysis.get("aggregate_score") is not False:
            errors.append(
                "analysis.aggregate_score: must be false; this scaffold emits rows only"
            )

    # ethics box (AC6)
    ethics = _object(cfg["ethics"], "ethics", errors, list(ETHICS_FIELDS))
    for field in ETHICS_FIELDS:
        if (
            field in ethics
            and ethics[field] is not None
            and not isinstance(ethics[field], str)
        ):
            errors.append("ethics.%s: expected string or null" % field)

    if errors:
        raise ProtocolConfigError("; ".join(errors))
    return cfg


def load_config(path: Path, matrix: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = Path(path).read_bytes()
    try:
        cfg = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProtocolConfigError(
            "config %s is not valid UTF-8 JSON: %s" % (path, error)
        ) from error
    return validate_config(cfg, matrix=matrix)


def ethics_complete(cfg: Dict[str, Any]) -> List[str]:
    """Names of the AC6 decision-box fields still null (empty list = complete)."""
    ethics = cfg.get("ethics", {})
    return [
        field
        for field in ETHICS_FIELDS
        if not isinstance(ethics.get(field), str) or not ethics[field]
    ]


# ---------------------------------------------------------------------------
# Corpus selection (development partition only; holdout refused)
# ---------------------------------------------------------------------------


def _case_sort_key(case_id: str) -> Tuple[Any, ...]:
    prefix, _, suffix = case_id.rpartition("-")
    return (prefix, int(suffix) if suffix.isdigit() else suffix)


def _selector_applies(patterns: Optional[List[str]], operator: str) -> bool:
    if not patterns:
        return True
    for pattern in patterns:
        if pattern == operator or (
            pattern.endswith("*") and operator.startswith(pattern[:-1])
        ):
            return True
    return False


def development_cases(receipt: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Validated development-partition case rows from the corpus receipt.

    Refuses any case whose split is not 'development' (holdout stays sealed)
    and any receipt that is not the PASS development corpus receipt.
    """
    if receipt.get("schema") != "torchsynth-development-corpus-evidence":
        raise ProtocolConfigError(
            "corpus receipt schema %r is not torchsynth-development-corpus-evidence"
            % receipt.get("schema")
        )
    if receipt.get("status") != "PASS":
        raise ProtocolConfigError(
            "corpus receipt status %r is not PASS" % receipt.get("status")
        )
    cases = (receipt.get("index") or {}).get("cases")
    if not isinstance(cases, list) or not cases:
        raise ProtocolConfigError("corpus receipt has no index cases")
    for case in cases:
        split = case.get("split")
        if split != "development":
            raise ProtocolConfigError(
                "holdout refusal: case %r has split %r; only the development partition is selectable"
                % (case.get("case_id"), split)
            )
        if case.get("status") != "complete":
            raise ProtocolConfigError(
                "case %r status %r is not complete"
                % (case.get("case_id"), case.get("status"))
            )
    return sorted(cases, key=lambda case: _case_sort_key(case["case_id"]))


def coverage_facts(coverage: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """case_id -> {exact_silence, near_silence, normalization_active} from the coverage receipt."""
    families = coverage.get("families") or {}
    facts: Dict[str, Dict[str, Any]] = {}
    for row in (families.get("audio_facts") or {}).get("rows") or []:
        facts.setdefault(row["case_id"], {}).update(
            exact_silence=bool(row.get("exact_silence")),
            near_silence=bool(row.get("near_silence")),
        )
    for row in (families.get("normalization_seam") or {}).get("rows") or []:
        facts.setdefault(row["case_id"], {}).update(
            normalization_active=row.get("classification") == "active"
        )
    return facts


def select_cases(
    cfg: Dict[str, Any],
    cases: Sequence[Dict[str, Any]],
    facts: Dict[str, Dict[str, Any]],
    declared_ops: Dict[str, Dict[str, Dict[str, Any]]],
) -> Dict[str, Dict[str, Any]]:
    """Deterministically draw development cases per operator after exclusions.

    Returns operator -> {"cases": [case_id...], "eligible": int, "excluded": {...},
    "listen_only": [case_id...]} drawn with the config's recorded seed. The
    same config+seed always yields the same selection.
    """
    exclusions = cfg["selection"]["exclusions"]
    per_op = cfg["selection"]["cases_per_operator"]
    seed = cfg["selection"]["seed"]
    strat = cfg["selection"]["stratification"]["normalization_active_required_for"]
    all_ids = [case["case_id"] for case in cases]
    selection: Dict[str, Dict[str, Any]] = {}
    rng = random.Random(seed)
    for op in sorted(declared_ops):
        eligible = list(all_ids)
        excluded: Dict[str, int] = {}
        listen_only = False
        for entry in exclusions:
            selector = entry["selector"]
            patterns = entry.get("applies_to")
            if not _selector_applies(patterns, op):
                continue
            if selector in ("exact_silence", "near_silence"):
                kept = []
                for case_id in eligible:
                    row = facts.get(case_id, {})
                    if row.get(selector):
                        excluded[selector] = excluded.get(selector, 0) + 1
                    else:
                        kept.append(case_id)
                eligible = kept
            elif selector == "normalization_inactive":
                if op in strat or _selector_applies(patterns, op):
                    kept = []
                    for case_id in eligible:
                        if not facts.get(case_id, {}).get(
                            "normalization_active", False
                        ):
                            excluded[selector] = excluded.get(selector, 0) + 1
                        else:
                            kept.append(case_id)
                    eligible = kept
            elif selector == "unqualified_pitch_property_tag":
                # A claim-scope tag (all 96 corpus cases are pitch-unqualified),
                # not a stimulus exclusion: drawn cases are marked listen-only.
                listen_only = True
        if len(eligible) < per_op:
            raise ProtocolConfigError(
                "operator %r: %d eligible development cases after preregistered exclusions, need %d"
                % (op, len(eligible), per_op)
            )
        drawn = sorted(rng.sample(eligible, per_op))
        selection[op] = {
            "cases": drawn,
            "eligible": len(eligible),
            "excluded": excluded,
            "listen_only": drawn if listen_only else [],
        }
    return selection


def declared_operators(cfg: Dict[str, Any]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Flatten the config's declared ladders + binary trials (validated form)."""
    ops: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for op, spec in cfg["ladders"].items():
        ops[op] = {
            "kind": "ladder",
            "render": spec["render"],
            "unit": spec["unit"],
            "points": spec["points"],
        }
    for op, spec in cfg["binary_trials"].items():
        ops[op] = {
            "kind": "binary",
            "render": spec["render"],
            "unit": None,
            "points": None,
        }
    return ops


# ---------------------------------------------------------------------------
# Landed operator applications over corpus audio
# ---------------------------------------------------------------------------

_CORPUS_AUDIO_APPLICATIONS: Dict[
    str, Callable[[List[float], Any], Tuple[List[float], Dict[str, Any]]]
] = {}


def corpus_audio_applications() -> Dict[
    str, Callable[[List[float], Any], Tuple[List[float], Dict[str, Any]]]
]:
    """Landed sample-domain applications available for corpus-audio stimuli.

    Drawn from the registered signal-family implementations
    (``torchsynth_voice.mutations_signal``); nothing here re-derives mutation
    semantics. Operators without a landed sample-domain application are not
    present and must be declared ``host-run-gated`` in the config.
    """
    if _CORPUS_AUDIO_APPLICATIONS:
        return dict(_CORPUS_AUDIO_APPLICATIONS)
    from torchsynth_voice import mutations_signal

    _CORPUS_AUDIO_APPLICATIONS.update(
        {
            "gain.db": lambda samples, magnitude: mutations_signal.apply_gain_db(
                samples, magnitude
            ),
            "gain.dc_offset": lambda samples,
            magnitude: mutations_signal.apply_dc_offset(samples, magnitude),
            "clip.round_step": lambda samples,
            magnitude: mutations_signal.apply_round_step(samples, magnitude),
            "clip.saturation_ceiling": lambda samples,
            magnitude: mutations_signal.apply_saturation(samples, magnitude),
            "gain.polarity": lambda samples, magnitude: mutations_signal.apply_polarity(
                samples
            ),
        }
    )
    return dict(_CORPUS_AUDIO_APPLICATIONS)


def implementation_sha256() -> str:
    """SHA-256 of the landed mutations_signal module backing the applications."""
    from torchsynth_voice import mutations_signal

    return sha256_hex(Path(mutations_signal.__file__).read_bytes())


def mp1_plan_binding_attempt(
    case_id: str, operator: str, magnitude: Any
) -> Dict[str, Any]:
    """Attempt the honest mp1- plan binding for a corpus-audio stimulus.

    The landed plan contract refuses a corpus-audio mix-output trace (it is
    not a declared ``voice.post_module`` enum member), so this returns the
    verbatim refusal instead of a plan. Kept as an explicit, recorded attempt
    so receipts document exactly why the mp1- identity is null.
    """
    from torchsynth_voice import mutations, mutations_signal

    try:
        instance = mutations_signal.instance(
            "listening-stimulus",
            operator,
            mutations_signal.SEAM_POST_MODULE,
            magnitude,
            {"trace": "mixer.output", "slot": 0},
        )
        plan = mutations_signal.make_plan(
            {
                "case_id": case_id,
                "partition": "development",
                "fixture_identity": "0" * 64,
            },
            [instance],
        )
        return {
            "mp1_plan_identity": mutations.plan_identity(plan),
            "mp1_plan_refusal": None,
        }
    except mutations.MutationError as error:
        return {
            "mp1_plan_identity": None,
            "mp1_plan_refusal": "%s: %s" % (type(error).__name__, error),
        }


# ---------------------------------------------------------------------------
# Audio framing helpers (float32 little-endian, the corpus store format)
# ---------------------------------------------------------------------------


def f32le_decode(data: bytes) -> List[float]:
    if len(data) % 4 != 0:
        raise ProtocolConfigError("float32le payload length is not a multiple of 4")
    return list(struct.unpack("<%df" % (len(data) // 4), data))


def f32le_encode(samples: Sequence[float]) -> bytes:
    return struct.pack("<%df" % len(samples), *samples)


def rms(samples: Sequence[float]) -> float:
    if not samples:
        return 0.0
    return math.sqrt(
        math.fsum(float(value) * float(value) for value in samples) / len(samples)
    )


# ---------------------------------------------------------------------------
# Preregistered statistics (stdlib; deterministic)
# ---------------------------------------------------------------------------


def _log_binomial_pmf(n: int, k: int) -> float:
    if k < 0 or k > n:
        return float("-inf")
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def binomial_cdf(k: int, n: int, p: float) -> float:
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 0.0
    total = 0.0
    for i in range(0, k + 1):
        total += math.exp(
            _log_binomial_pmf(n, i) + i * math.log(p) + (n - i) * math.log1p(-p)
        )
    return min(1.0, max(0.0, total))


def clopper_pearson(successes: int, n: int, alpha: float) -> Tuple[float, float]:
    """Exact binomial confidence interval via bisection on the CDF."""
    if n <= 0:
        return (0.0, 1.0)
    successes = max(0, min(n, successes))

    def lower() -> float:
        if successes == 0:
            return 0.0
        low, high = 0.0, 1.0
        for _ in range(200):
            mid = (low + high) / 2.0
            if binomial_cdf(successes - 1, n, mid) > 1.0 - alpha / 2.0:
                low = mid
            else:
                high = mid
        return (low + high) / 2.0

    def upper() -> float:
        if successes == n:
            return 1.0
        low, high = 0.0, 1.0
        for _ in range(200):
            mid = (low + high) / 2.0
            if binomial_cdf(successes, n, mid) > alpha / 2.0:
                low = mid
            else:
                high = mid
        return (low + high) / 2.0

    return (lower(), upper())


def _mean(values: Sequence[float]) -> float:
    return math.fsum(values) / len(values) if values else 0.0


mean = _mean


def _rankdata(values: Sequence[float]) -> List[float]:
    """Average ranks (1-based), ties share the mean rank."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        average = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = average
        i = j + 1
    return ranks


def spearman(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    """Spearman rank correlation (average ranks); None when undefined."""
    if len(x) != len(y) or len(x) < 2:
        return None
    rx, ry = _rankdata(list(x)), _rankdata(list(y))
    mx, my = _mean(rx), _mean(ry)
    num = math.fsum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = math.sqrt(math.fsum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(math.fsum((b - my) ** 2 for b in ry))
    if dx == 0.0 or dy == 0.0:
        return None
    return num / (dx * dy)
