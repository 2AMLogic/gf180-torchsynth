#!/usr/bin/env python3
"""Internal-consistency validator for the frozen verification rubric (issue #48).

Checks, per the rubric's own contract:

1. every cited source digest matches the landed file (recomputed SHA-256);
2. every assertion cites a landed source (a row without a source is an error);
3. every assertion against a JSON source resolves in that source and matches
   the recorded value; prose (markdown) sources carry digest pins only;
4. open rows are explicit: no assertions, a concrete open reason, a declared
   threshold policy, and a NO VERDICT verdict rule -- never invented limits;
5. no aggregate score: forbidden scalar keys anywhere in the document, the
   release rule is a conjunction, and the summary is navigation counts only;
6. the holdout seal holds: sealed policy, zero holdout artifacts read, no
   holdout source, and the seal cross-binds to the corpus, registry and audit
   artifacts;
7. preregistration and change-control rules are present and correctly worded
   (measure-then-preregister, never increase after freeze, miss is a
   mismatch, changes create rubric v1);
8. structural integrity: unique row ids, declared families/levels match rows,
   appendix rows are non-gating, summary counts recompute exactly.

Stdlib only. Exit 0 when consistent, 1 with a list of findings otherwise.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

SEMANTIC_VERSION = "rubric-v0"  # default; the expected version is derived per file (issue #53: rubric-v1 binds the calibrated rows)


def expected_semantic_version(rubric_path: Path) -> str:
    """The rubric's semantic_version is its own file stem (rubric-v0.json ->
    'rubric-v0'; rubric-v1.json -> 'rubric-v1')."""
    return rubric_path.stem
FORBIDDEN_AGGREGATE_KEYS = {
    "aggregate",
    "aggregate_score",
    "overall",
    "overall_score",
    "weighted",
    "weighted_score",
    "fidelity_score",
    "total_score",
    "mean_score",
    "score",
}
REQUIRED_FIELDS = (
    "id",
    "family",
    "level",
    "claim",
    "unit",
    "applicability",
    "estimator",
    "source_id",
    "status",
    "mandatory",
    "verdict_rule",
    "failure_semantics",
)
REQUIRED_PREREGISTRATION_SUBSTRINGS = (
    "smallest power of two",
    "2x",
    "never increase",
    "mismatch, never a tolerance problem",
)
REQUIRED_CHANGE_SUBSTRINGS = ("rubric-v1", "holdout policy", "never rewrite")


class Findings:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def add(self, message: str) -> None:
        self.errors.append(message)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_path(node, path: str) -> list:
    """Resolve a '/'-separated path against one node.

    Lists are carried as single nodes; '*' expands a list into its items or a
    dict into its values; integer segments index lists.
    """
    nodes = [node]
    for seg in path.split("/"):
        nxt: list = []
        for cur in nodes:
            if seg == "*":
                if isinstance(cur, list):
                    nxt.extend(cur)
                elif isinstance(cur, dict):
                    nxt.extend(cur.values())
                else:
                    raise ValueError("wildcard over scalar")
            elif isinstance(cur, list):
                nxt.append(cur[int(seg)])
            elif isinstance(cur, dict) and seg in cur:
                nxt.append(cur[seg])
            else:
                raise KeyError(seg)
        nodes = nxt
    return nodes


def flatten(nodes: list) -> list:
    """Expand top-level list nodes into their items (one level, recursively applied)."""
    out: list = []
    for n in nodes:
        if isinstance(n, list):
            out.extend(flatten(n))
        else:
            out.append(n)
    return out


def get_field(node, dotted: str):
    cur = node
    for seg in dotted.split("/"):
        if isinstance(cur, list):
            cur = cur[int(seg)]
        else:
            cur = cur[seg]
    return cur


def _stages(query: dict) -> list[dict]:
    if "stages" in query:
        return query["stages"]
    stage: dict = {"path": query["path"]}
    if "where" in query:
        stage["where"] = query["where"]
    return [stage]


def eval_query(source_data, query: dict) -> tuple[object, str]:
    """Resolve a query to (value-or-count, human description)."""
    nodes: list = [source_data]
    desc: list[str] = []
    for stage in _stages(query):
        nxt: list = []
        for cur in nodes:
            nxt.extend(resolve_path(cur, stage["path"]))
        nxt = flatten(nxt)
        where = stage.get("where")
        if where:
            nxt = [
                n for n in nxt if all(get_field(n, k) == v for k, v in where.items())
            ]
        nodes = nxt
        desc.append(stage["path"] + (f" where {where}" if where else ""))
    if "field" in query:
        hits = sum(1 for n in nodes if get_field(n, query["field"]) == query["equals"])
        return hits, f"count({query['field']} == {query['equals']!r}) over {' -> '.join(desc)}"
    if query.get("count") or any("where" in st for st in _stages(query)):
        return len(nodes), f"count(nodes at {' -> '.join(desc)})"
    if len(nodes) != 1:
        raise ValueError(f"expected exactly 1 node at {query['path']}, found {len(nodes)}")
    return nodes[0], f"value at {' -> '.join(desc)}"


def resolve_single(source_data, source_ref: dict) -> object:
    nodes: list = [source_data]
    for stage in _stages(source_ref["query"]):
        nxt: list = []
        for cur in nodes:
            nxt.extend(resolve_path(cur, stage["path"]))
        nxt = flatten(nxt)
        where = stage.get("where")
        if where:
            nxt = [
                n for n in nxt if all(get_field(n, k) == v for k, v in where.items())
            ]
        nodes = nxt
    if len(nodes) != 1:
        raise ValueError(f"expected exactly 1 node, found {len(nodes)}")
    if "field" in source_ref["query"]:
        return get_field(nodes[0], source_ref["query"]["field"])
    return nodes[0]


def load_source(root: Path, rel: str):
    path = root / rel
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def validate_rubric(root: Path, rubric_rel: str = "spec/reference/rubric-v0.json") -> list[str]:
    f = Findings()
    rubric_path = root / rubric_rel
    with rubric_path.open(encoding="utf-8") as fh:
        doc = json.load(fh)

    # 1. document identity ---------------------------------------------------
    if doc.get("schema") != "torchsynth-verification-rubric":
        f.add("schema is not torchsynth-verification-rubric")
    if doc.get("schema_version") != 1:
        f.add("schema_version is not 1")
    if doc.get("semantic_version") != expected_semantic_version(rubric_path):
        f.add(
            f"semantic_version is not {expected_semantic_version(rubric_path)}"
        )
    if doc.get("status") != "frozen":
        f.add("rubric status is not frozen")
    if doc.get("release_rule", {}).get("form") != "conjunction":
        f.add("release_rule.form is not 'conjunction'")
    if doc.get("release_rule", {}).get("omnibus_scalar") is not False:
        f.add("release_rule.omnibus_scalar must be false")

    # 2. preregistration and change control ----------------------------------
    prereg = json.dumps(doc.get("preregistration", {}), sort_keys=True).lower()
    for needle in REQUIRED_PREREGISTRATION_SUBSTRINGS:
        if needle.lower() not in prereg:
            f.add(f"preregistration block missing required rule: {needle!r}")
    change = json.dumps(doc.get("change_control", {}), sort_keys=True).lower()
    for needle in REQUIRED_CHANGE_SUBSTRINGS:
        if needle.lower() not in change:
            f.add(f"change_control block missing required rule: {needle!r}")

    # 3. sources -------------------------------------------------------------
    sources = doc.get("sources", [])
    if not sources:
        f.add("no sources declared")
    seen_ids: set[str] = set()
    source_files: dict[str, Path] = {}
    for src in sources:
        sid, rel = src.get("id"), src.get("path")
        if not sid or not rel:
            f.add(f"source entry missing id/path: {src!r}")
            continue
        if sid in seen_ids:
            f.add(f"duplicate source id {sid}")
        seen_ids.add(sid)
        rel_path = Path(rel)
        if ".." in rel_path.parts or rel_path.is_absolute():
            f.add(f"source {sid}: unsafe path {rel!r}")
            continue
        path = root / rel_path
        if not path.is_file():
            f.add(f"source {sid}: file not found: {rel}")
            continue
        source_files[sid] = path
        digest = src.get("sha256", "")
        if len(digest) != 64 or digest != digest.lower() or any(
            c not in "0123456789abcdef" for c in digest
        ):
            f.add(f"source {sid}: sha256 is not 64 lowercase hex digits")
        elif digest != sha256_file(path):
            f.add(f"source {sid}: sha256 does not match landed file {rel}")
    if "holdout" in json.dumps([s.get("path") for s in sources]):
        f.add("a source path references a holdout artifact")

    # 4. rows ----------------------------------------------------------------
    rows = doc.get("rows", [])
    families = doc.get("families", {})
    seen_rows: set[str] = set()
    levels_seen: set[int] = set()
    families_seen: set[str] = set()
    source_data_cache: dict[str, object] = {}

    def source_data(sid: str):
        if sid not in source_data_cache:
            path = source_files.get(sid)
            if path is None:
                raise KeyError(sid)
            if path.suffix != ".json":
                raise ValueError(f"{sid} is not JSON")
            source_data_cache[sid] = load_source(root, str(path.relative_to(root)))
        return source_data_cache[sid]

    for row in rows:
        rid = row.get("id", "<missing>")
        for field in REQUIRED_FIELDS:
            if field not in row or row[field] in (None, "", []):
                f.add(f"row {rid}: missing required field {field}")
        if rid in seen_rows:
            f.add(f"duplicate row id {rid}")
        seen_rows.add(rid)
        fam, level = row.get("family"), row.get("level")
        if fam not in families:
            f.add(f"row {rid}: unknown family {fam!r}")
        elif families[fam].get("level") != level:
            f.add(f"row {rid}: level {level} disagrees with family {fam} level {families[fam].get('level')}")
        if not isinstance(level, int) or level < 0 or level > 6:
            f.add(f"row {rid}: level must be an integer 0..6")
        else:
            levels_seen.add(level)
            families_seen.add(fam)
        status, mandatory = row.get("status"), row.get("mandatory")
        if status not in ("landed", "open"):
            f.add(f"row {rid}: status must be 'landed' or 'open'")
        if not isinstance(mandatory, bool):
            f.add(f"row {rid}: mandatory must be boolean")
        sid = row.get("source_id")
        if sid not in seen_ids:
            f.add(f"row {rid}: source_id {sid!r} is not a declared source")
            continue
        assertions = row.get("assertions", [])
        if status == "open":
            if assertions:
                f.add(f"row {rid}: open row carries assertions (invented limits?)")
            if not row.get("open_reason"):
                f.add(f"row {rid}: open row lacks open_reason")
            if not row.get("threshold_policy"):
                f.add(f"row {rid}: open row lacks threshold_policy")
            if "NO VERDICT" not in row.get("verdict_rule", ""):
                f.add(f"row {rid}: open row verdict_rule must state NO VERDICT")
            continue
        if not assertions:
            f.add(f"row {rid}: landed row has no assertion backed by a source")
        for a in assertions:
            metric = a.get("metric", "<missing>")
            if a.get("op") == "cross_equal":
                sides = {"left": a.get("left"), "right": a.get("right")}
                values: dict[str, object] = {}
                ok = True
                for side_name, side in sides.items():
                    ssid = (side or {}).get("source_id")
                    if ssid not in seen_ids or ssid not in source_files:
                        f.add(f"row {rid}/{metric}: cross {side_name} source {ssid!r} is not a declared JSON source")
                        ok = False
                        continue
                    if source_files[ssid].suffix != ".json":
                        f.add(f"row {rid}/{metric}: cross {side_name} source {ssid!r} is not JSON")
                        ok = False
                        continue
                    try:
                        values[side_name] = resolve_single(source_data(ssid), side)
                    except (KeyError, ValueError, IndexError, TypeError) as exc:
                        f.add(f"row {rid}/{metric}: cross {side_name} query does not resolve in {ssid}: {exc}")
                        ok = False
                if ok and values["left"] != values["right"]:
                    f.add(
                        f"row {rid}/{metric}: cross-source binding mismatch: "
                        f"{values['left']!r} != {values['right']!r}"
                    )
                continue
            asid = a.get("source_id")
            if asid not in seen_ids:
                f.add(f"row {rid}/{metric}: assertion source {asid!r} is not declared")
                continue
            if asid not in source_files:
                f.add(f"row {rid}/{metric}: assertion source {asid!r} failed to load")
                continue
            query = a.get("query") or {}
            if source_files[asid].suffix != ".json":
                if query.get("path") or query.get("stages"):
                    f.add(f"row {rid}/{metric}: prose source must not carry a resolvable query")
                if not a.get("note"):
                    f.add(f"row {rid}/{metric}: prose-source assertion lacks a note")
                if a.get("value") is not True:
                    f.add(f"row {rid}/{metric}: prose-source assertion must assert the declared obligation (true)")
                continue
            try:
                data = source_data(asid)
                resolved, desc = eval_query(data, query)
                if resolved != a.get("value"):
                    f.add(
                        f"row {rid}/{metric}: recorded value {a.get('value')!r} does not match "
                        f"landed source {asid} ({desc} resolved to {resolved!r})"
                    )
            except (KeyError, ValueError, IndexError, TypeError) as exc:
                f.add(f"row {rid}/{metric}: query does not resolve in {asid}: {exc}")

        # appendix discipline: non-mandatory rows must be declared appendices
        if mandatory is False:
            allowed = set(doc.get("perceptual_bounded_role", {}).get("appendix_rows", []))
            if rid not in allowed:
                f.add(f"row {rid}: non-mandatory row outside the declared appendix set")
            if row.get("role") != "appendix-auxiliary-flag-only":
                f.add(f"row {rid}: non-mandatory row must carry the flag-only appendix role")

    for level in range(0, 7):
        if level not in levels_seen:
            f.add(f"no row at level {level}")
    for fam in families:
        if fam not in families_seen:
            f.add(f"family {fam} has no rows")

    # 5. no aggregate score ---------------------------------------------------
    def scan(node, trail: str) -> None:
        if isinstance(node, dict):
            for key, val in node.items():
                if str(key).lower() in FORBIDDEN_AGGREGATE_KEYS:
                    f.add(f"forbidden aggregate key {key!r} at {trail}")
                scan(val, f"{trail}/{key}")
        elif isinstance(node, list):
            for i, val in enumerate(node):
                scan(val, f"{trail}[{i}]")

    scan(doc, "$")

    # 6. holdout seal ---------------------------------------------------------
    seal = doc.get("holdout_seal", {})
    if seal.get("policy") != "sealed-until-thresholds-frozen":
        f.add("holdout_seal.policy is not sealed-until-thresholds-frozen")
    if seal.get("holdout_artifacts_read") != []:
        f.add("holdout_seal.holdout_artifacts_read is not empty")
    checks = (
        ("case-registry-v1", "configuration/holdout_policy", seal.get("policy"), "registry holdout policy"),
        ("corpus-v0", "rules/holdout_count", seal.get("holdout_count"), "corpus holdout count"),
        ("corpus-v0", "rules/holdout_is_blind_until_thresholds_are_frozen", True, "corpus blind rule"),
        ("development-corpus-coverage", "denominator/holdout_identities_read", 0, "audit holdout-read counter"),
    )
    for sid, path, expected, what in checks:
        if sid not in source_files:
            f.add(f"holdout seal cross-check source {sid} missing")
            continue
        try:
            resolved = get_field(source_data(sid), path)
            if resolved != expected:
                f.add(f"holdout seal cross-check failed ({what}): {resolved!r} != {expected!r}")
        except (KeyError, TypeError) as exc:
            f.add(f"holdout seal cross-check ({what}) does not resolve: {exc}")

    # 7. summary counts recompute ---------------------------------------------
    summary = doc.get("summary", {})
    recomputed = {
        "rows_total": len(rows),
        "rows_landed": sum(1 for r in rows if r.get("status") == "landed"),
        "rows_open": sum(1 for r in rows if r.get("status") == "open"),
        "rows_mandatory": sum(1 for r in rows if r.get("mandatory") is True),
        "rows_appendix": sum(1 for r in rows if r.get("mandatory") is False),
    }
    for key, val in recomputed.items():
        if summary.get(key) != val:
            f.add(f"summary.{key} declares {summary.get(key)!r}, recomputed {val}")
    by_level: dict[str, int] = {}
    by_family: dict[str, int] = {}
    for r in rows:
        by_level[str(r.get("level"))] = by_level.get(str(r.get("level")), 0) + 1
        by_family[r.get("family")] = by_family.get(r.get("family"), 0) + 1
    if summary.get("by_level") != by_level:
        f.add("summary.by_level does not recompute")
    if summary.get("by_family") != by_family:
        f.add("summary.by_family does not recompute")

    return f.errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rubric",
        default="spec/reference/rubric-v0.json",
        help="rubric path relative to the repository root",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    errors = validate_rubric(root, args.rubric)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        print(f"rubric validation FAILED with {len(errors)} finding(s)")
        return 1
    print(
        f"rubric {expected_semantic_version(Path(args.rubric))} is internally consistent: "
        "every assertion cites a landed source digest, "
        "every source digest matches the landed file, no row lacks a source, no aggregate score exists"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
