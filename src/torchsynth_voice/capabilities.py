"""Evidence-derived capability graph. Offline, stdlib-only, never executes evidence.

Graph declarations are trusted, reviewable policy; evidence is untrusted data.
Representation validators do not establish execution truth. This compiler also
checks execution attestations, current byte coverage, references and live controls.
It cannot authenticate a dishonest producer; see the generated CAPABILITIES.md.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .artifacts import canonical_bytes, loads, validate_artifact
from .contract import repository_root
from .scorecard import validate_row


class CapabilityError(ValueError):
    """Invalid graph policy or unusable evidence."""


class StaleEvidence(CapabilityError):
    """A recorded digest no longer matches its covered bytes or definition."""


@dataclass(frozen=True)
class Check:
    command: tuple[str, ...]
    claim_class: str
    engine: str
    layer: str
    scope: str
    inputs: tuple[str, ...]
    controls: dict[str, str]


# Adding a supported check is a code review boundary. Merely declaring a name,
# command, or existing file in the graph/evidence cannot register an evaluator.
CHECKS = {
    "contract-tests-v1": Check(
        (
            "python3",
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            "test_contract.py",
            "-v",
        ),
        "source-contract",
        "stdlib",
        "reference-contract",
        "manifest-consistency",
        ("tests/test_contract.py", "tools/check_contract.py"),
        {"missing-and-changed-files": "hash-mismatch"},
    ),
    "capability-compiler-v1": Check(
        (
            "python3",
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            "test_capabilities.py",
            "-v",
        ),
        "compiler-behavior",
        "stdlib",
        "verification",
        "synthetic-only",
        ("tests/test_capabilities.py", "tools/compile_capabilities.py"),
        {"stale-input": "hash-mismatch"},
    ),
}

# These implementation/spec bytes always participate, including dirty files.
# The graph's own node declaration is covered separately, excluding its evidence
# pointer to avoid a circular digest. Unrelated node edits do not stale siblings.
EVALUATOR_INPUTS = (
    "src/torchsynth_voice/capabilities.py",
    "src/torchsynth_voice/artifacts.py",
    "src/torchsynth_voice/scorecard.py",
    "src/torchsynth_voice/contract.py",
    "src/torchsynth_voice/identity.py",
    "spec/schemas/capability-graph-v1.schema.json",
    "spec/schemas/capability-evidence-v1.schema.json",
    "spec/schemas/render-artifact-v1.schema.json",
    "spec/schemas/scorecard-row-v1.schema.json",
    "spec/reference/upstream.json",
    "spec/VOICE-CONTRACT.md",
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CapabilityError(message)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _structure(value: Any, schema: dict, where: str = "$") -> None:
    """Evaluate exactly the JSON Schema subset used by the two local schemas."""
    if "oneOf" in schema:
        matches = 0
        for option in schema["oneOf"]:
            try:
                _structure(value, option, where)
                matches += 1
            except CapabilityError:
                pass
        _require(matches == 1, f"{where}: expected one allowed form")
        return
    kinds = {
        "object": dict,
        "array": list,
        "string": str,
        "integer": int,
        "boolean": bool,
        "null": type(None),
    }
    if "type" in schema:
        _require(type(value) is kinds[schema["type"]], f"{where}: wrong type")
    if "const" in schema:
        _require(
            canonical_bytes(value) == canonical_bytes(schema["const"]),
            f"{where}: wrong constant",
        )
    if "enum" in schema:
        _require(value in schema["enum"], f"{where}: unknown value")
    if isinstance(value, dict):
        _require(
            set(schema.get("required", [])) <= value.keys(),
            f"{where}: missing required field",
        )
        _require(
            len(value) >= schema.get("minProperties", 0), f"{where}: too few fields"
        )
        for key, child in value.items():
            if "propertyNames" in schema:
                _structure(key, schema["propertyNames"], where + ".<key>")
            properties = schema.get("properties", {})
            additional = schema.get("additionalProperties", {})
            _require(
                key in properties or additional is not False,
                f"{where}: unknown field {key}",
            )
            _structure(child, properties.get(key, additional), where + "." + key)
    if isinstance(value, list):
        _require(len(value) >= schema.get("minItems", 0), f"{where}: too few items")
        if schema.get("uniqueItems"):
            _require(
                len({canonical_bytes(item) for item in value}) == len(value),
                f"{where}: duplicate item",
            )
        for child in value:
            _structure(child, schema["items"], where + "[]")
    if isinstance(value, str) and "pattern" in schema:
        _require(
            re.fullmatch(schema["pattern"], value) is not None, f"{where}: invalid text"
        )


def _validate(value: Any, kind: str) -> None:
    # canonical_bytes rejects Python non-JSON values and non-finite values too.
    canonical_bytes(value)
    schema = loads(
        (
            repository_root() / f"spec/schemas/capability-{kind}-v1.schema.json"
        ).read_bytes()
    )
    _structure(value, schema)


def _relative(path: str) -> None:
    parts = path.split("/")
    _require(
        bool(path)
        and not PurePosixPath(path).is_absolute()
        and all(part not in ("", ".", "..") for part in parts)
        and re.fullmatch(r"[A-Za-z0-9_./-]+", path) is not None,
        "unsafe relative path",
    )


def _path(root: Path, relative: str) -> Path:
    _relative(relative)
    root = root.resolve()
    target = root / relative
    # Reject aliases as well as escapes, including symlinked parent directories.
    _require(
        not any(
            part.is_symlink() for part in (target, *target.parents) if part != root
        ),
        f"symlink reference: {relative}",
    )
    _require(
        target.resolve().is_relative_to(root), f"reference escapes root: {relative}"
    )
    return target


def validate_graph(graph: dict) -> None:
    try:
        _validate(graph, "graph")
        nodes = graph["nodes"]
        indexed = {node["id"]: node for node in nodes}
        _require(len(indexed) == len(nodes), "duplicate node ID")
        for node in nodes:
            _require(
                set(node["dependencies"]) <= indexed.keys(),
                f"{node['id']}: unknown dependency",
            )
            cheaper = node["cheaper_predecessor"]
            _require(
                not node["expensive"] or cheaper in node["dependencies"],
                f"{node['id']}: expensive work needs a cheaper direct predecessor",
            )
            _require(
                cheaper is None or cheaper in node["dependencies"],
                "invalid cheaper predecessor",
            )
            refs = node["coverage"]["references"]
            _require(
                len({ref["id"] for ref in refs}) == len(refs), "duplicate reference ID"
            )
            for path in node["coverage"]["inputs"] + [ref["path"] for ref in refs]:
                _relative(path)
            if node["evidence"] is not None:
                _relative(node["evidence"]["path"])
            check = CHECKS.get(node["check"])
            if check:
                for field in ("claim_class", "engine", "layer", "scope"):
                    _require(
                        node[field] == getattr(check, field),
                        f"registered check cannot establish {field}",
                    )
                _require(
                    check.controls.items() <= node["negative_controls"].items(),
                    "required check controls omitted",
                )
        _ordered(graph)
    except (ValueError, OSError) as error:
        raise CapabilityError(str(error)) from error


def _ordered(graph: dict) -> list[dict]:
    indexed = {node["id"]: node for node in graph["nodes"]}
    done, active, ordered = set(), set(), []

    def visit(name):
        _require(name not in active, f"dependency cycle at {name}")
        if name in done:
            return
        active.add(name)
        for dependency in sorted(indexed[name]["dependencies"]):
            visit(dependency)
        active.remove(name)
        done.add(name)
        ordered.append(indexed[name])

    for name in sorted(indexed):
        visit(name)
    return ordered


def load_graph(path: Path) -> dict:
    try:
        graph = loads(path.read_bytes())
        validate_graph(graph)
        return graph
    except (ValueError, OSError) as error:
        raise CapabilityError(str(error)) from error


def node_digest(node: dict) -> str:
    return _digest(
        canonical_bytes(
            {key: value for key, value in node.items() if key != "evidence"}
        )
    )


def coverage_hashes(node: dict, root: Path) -> dict[str, str]:
    """Hash actual files/trees, never git ancestry. Trees include untracked files."""

    def tree_files(directory):
        # rglob suppresses directory-read errors on recent Python versions. An
        # unreadable subtree must not disappear from a supposedly current hash.
        for child in sorted(directory.iterdir()):
            _path(root, child.relative_to(root.resolve()).as_posix())
            if child.is_dir():
                yield from tree_files(child)
            else:
                _require(child.is_file(), "covered input is not a regular file")
                yield child

    check = CHECKS.get(node["check"])
    paths = set(node["coverage"]["inputs"]) | set(EVALUATOR_INPUTS)
    if check:
        paths.update(check.inputs)
    result = {}
    for relative in sorted(paths):
        path = _path(root, relative)
        if path.is_dir():
            files = {
                child.relative_to(path).as_posix(): _digest(child.read_bytes())
                for child in tree_files(path)
            }
            result[relative] = _digest(canonical_bytes(files))
        else:
            _require(path.is_file(), f"covered input missing: {relative}")
            result[relative] = _digest(path.read_bytes())
    return result


def _read_reference(root: Path, ref: dict) -> bytes:
    path = _path(root, ref["path"])
    _require(path.is_file(), f"missing reference: {ref['path']}")
    data = path.read_bytes()
    if _digest(data) != ref["sha256"]:
        raise StaleEvidence(f"SHA-256 mismatch: {ref['path']}")
    return data


def _references(node: dict, record: dict, root: Path) -> list[str]:
    """Resolve metadata bytes and explicitly adapt artifact_id -> identity.

    All locators (including artifact payload refs) are relative to the supplied
    evidence root. No metadata is reserialized before computing file hashes.
    """
    declared = {ref["id"]: ref for ref in node["coverage"]["references"]}
    _require(
        record["references"].keys() == declared.keys(), "reference coverage mismatch"
    )
    artifacts, rows = {}, []
    for name, ref in declared.items():
        sha = record["references"][name]
        value = loads(_read_reference(root, {"path": ref["path"], "sha256": sha}))
        if ref["kind"] == "render-artifact":
            validate_artifact(value)
            identity = value["artifact_id"]
            _require(identity not in artifacts, "duplicate artifact identity")
            artifacts[identity] = sha
            runtime_lock = _path(root, record["provenance"]["runtime_lock"])
            _require(
                value["inputs"]["value"]["runtime"]["lock_sha256"]
                == _digest(runtime_lock.read_bytes()),
                "artifact runtime lock differs from evidence provenance",
            )
            files = [
                value["audio"]["value"]["file"],
                *value["traces"]["value"].values(),
            ]
            for blob in files:
                _require("ref" in blob, "artifact payload has no resolvable reference")
                data = _read_reference(
                    root, {"path": blob["ref"], "sha256": blob["sha256"]}
                )
                _require(
                    len(data) == blob["size_bytes"], "artifact payload size mismatch"
                )
        else:
            validate_row(value)
            rows.append(value)
    for row in rows:
        # Existing schemas deliberately have different field names. This is an
        # adapter, not a schema change or a content_id-as-file-hash shortcut.
        identity = row["artifact"]["identity"]
        _require(
            identity in artifacts,
            "scorecard artifact.identity does not resolve to artifact_id",
        )
        _require(
            row["artifact"]["sha256"] == artifacts[identity],
            "scorecard metadata SHA-256 mismatch",
        )
    return [row["verdict"] for row in rows]


@dataclass(frozen=True)
class Result:
    state: str
    local_state: str
    reasons: tuple[str, ...]
    healthy: bool


def _local(node: dict, root: Path, indexed: dict) -> tuple[str, str, bool]:
    ref, check = node["evidence"], CHECKS.get(node["check"])
    if ref is None:
        return (
            ("READY", "registered check available; no evidence attached", True)
            if check
            else (
                "NOT RUN",
                "planned check is not registered; no evidence attached",
                True,
            )
        )
    try:
        if not _path(root, ref["path"]).exists():
            return "NOT RUN", "declared evidence file is missing", False
        record = loads(_read_reference(root, ref))
        _validate(record, "evidence")
        _require(record["node_id"] == node["id"], "evidence node ID mismatch")
        if record["node_sha256"] != node_digest(node):
            raise StaleEvidence("node declaration changed")
        _require(check is not None, "unsupported check cannot stamp evidence")
        execution = record["execution"]
        _require(execution["check"] == node["check"], "check identity mismatch")
        _require(
            execution["command"] == list(check.command), "unregistered check command"
        )
        for field in ("engine", "layer", "scope"):
            _require(execution[field] == node[field], f"execution {field} mismatch")
        datetime.strptime(record["provenance"]["recorded_at"], "%Y-%m-%dT%H:%M:%SZ")
        try:
            current = coverage_hashes(node, root)
        except (OSError, CapabilityError) as error:
            raise StaleEvidence(str(error)) from error
        _require(
            record["inputs"].keys() == current.keys(), "covered input set mismatch"
        )
        changed = sorted(
            path for path in current if current[path] != record["inputs"][path]
        )
        if changed:
            raise StaleEvidence("covered bytes changed: " + ", ".join(changed))
        runtime = record["provenance"]["runtime_lock"]
        runtime_path = _path(root, runtime)
        _require(
            runtime_path.is_file()
            and any(
                runtime == covered
                or runtime.startswith(covered + "/")
                and _path(root, covered).is_dir()
                for covered in current
            ),
            "runtime lock is not covered",
        )
        _require(
            record["dependencies"].keys() == set(node["dependencies"]),
            "dependency evidence set mismatch",
        )
        for name, sha in record["dependencies"].items():
            dependency = indexed[name]["evidence"]
            if dependency is None or dependency["sha256"] != sha:
                raise StaleEvidence(f"prerequisite evidence changed: {name}")
        _read_reference(root, execution["log"])
        verdicts = _references(node, record, root)
        _require(
            execution["executed"] == (execution["exit_code"] is not None),
            "execution and exit code disagree",
        )
        if not execution["executed"]:
            return "NOT RUN", "record explicitly reports no execution", False
        _require(
            record["controls"].keys() == node["negative_controls"].keys(),
            "required controls missing or unexpected",
        )
        for name, fault in sorted(node["negative_controls"].items()):
            control = record["controls"][name]
            _require(control["fault"] == fault, f"control fault mismatch: {name}")
            _read_reference(root, control["log"])
            if not control["executed"] or not control["detected"]:
                return (
                    "FAIL",
                    f"required control did not detect its fault: {name}",
                    False,
                )
        if (
            execution["exit_code"] != 0
            or record["result"]["verdict"] == "FAIL"
            or "FAIL" in verdicts
        ):
            return (
                "FAIL",
                "check or referenced measurement failed: " + record["result"]["reason"],
                False,
            )
        if record["result"]["verdict"] == "NO VERDICT" or any(
            v != "PASS" for v in verdicts
        ):
            return "NO VERDICT", record["result"]["reason"], False
        return (
            "PASS",
            "current provenance, execution, covered bytes, references and controls validated",
            True,
        )
    except StaleEvidence as error:
        return "STALE", str(error), False
    except (ValueError, OSError) as error:
        return "NO VERDICT", str(error), False


def evaluate(graph: dict, root: Path) -> dict[str, Result]:
    validate_graph(graph)
    root = root.resolve()
    indexed = {node["id"]: node for node in graph["nodes"]}
    results = {}
    for node in _ordered(graph):
        local, reason, healthy = _local(node, root, indexed)
        blocked = [
            f"{dep}={results[dep].state}"
            for dep in sorted(node["dependencies"])
            if results[dep].state != "PASS"
        ]
        reasons = (reason,)
        if blocked:
            reasons += ("prerequisites not demonstrated: " + ", ".join(blocked),)
        results[node["id"]] = Result(
            "BLOCKED" if blocked else local,
            local,
            reasons,
            healthy and not (blocked and node["evidence"] is not None),
        )
    return results


def render_markdown(graph: dict, results: dict[str, Result]) -> str:
    """Deterministic views only; no independent prose status source."""

    def cell(value):
        return (
            str(value)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("|", "&#124;")
            .replace("\n", " ")
        )

    lines = [
        "# Capability evidence",
        "",
        "<!-- Generated by tools/compile_capabilities.py; edit spec/capabilities-v1.json, never this view. -->",
        "",
        "The canonical graph states demonstrated capabilities; the Loom issue DAG schedules work. "
        "Issue closure, code existence and a successful downstream node never establish a prerequisite.",
        "",
        "The hardware product is one four-second, 44.1 kHz, default-nebula sound per trigger. "
        "Canonical batching is a qualification fixture protocol. Resolved scalar execution may use "
        "batch_size=1/reproducible=False; scalar/batched equality needs its own measurement. "
        "The normative TorchSynth commit remains `2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`.",
        "",
        "Run `python3 tools/compile_capabilities.py` to regenerate; `--check` checks graph/view agreement; "
        "`--strict` additionally fails on unhealthy declared evidence (including blocked attempted claims). "
        "Planned nodes without evidence are allowed, explicitly unrun, and never counted as passes. "
        "Neither mode executes checks or evidence commands. No Torch installation is required.",
        "",
        "Evidence follows `spec/schemas/capability-evidence-v1.schema.json`. PASS requires a registered "
        "check with matching class/engine/layer/scope, recorded execution and log, provenance with the "
        "pinned upstream commit and a covered runtime lock, the current node digest, every covered input "
        "digest, exact prerequisite evidence digests, and every named negative control executed and "
        "detecting the declared fault. Unknown future checks cannot pass. Synthetic tests establish "
        "compiler behavior only; legacy smoke records are not qualified evidence.",
        "",
        "Coverage hashes current stored bytes, including dirty and untracked covered files. Directory "
        "coverage hashes a sorted relative-file/digest map, without ignoring files. Symlinks and path "
        "escapes are refused. Compiler, validators, their schemas and the pinned profile/manifest are "
        "implicit covered inputs; registered checks add their implementation files. The node digest "
        "covers its declaration except the evidence pointer, so changing an unrelated node does not "
        "invalidate its siblings. Evidence and log digests cover exact stored bytes.",
        "",
        "All reference paths, including artifact payload locators, are relative to the supplied repository "
        "root. The explicit adapter matches scorecard `artifact.identity` to render `artifact_id` and "
        "checks the scorecard digest against exact stored render metadata bytes, never reserialized JSON "
        "or a render content ID. Metadata, payload sizes/hashes and runtime lock must resolve; scorecard "
        "representation validation alone is not a measurement. Their existing schemas are unchanged.",
        "",
        "These are integrity-checked producer attestations, not cryptographic authentication or an "
        "independent rerun. A producer that fabricates matching records and logs is outside this trust "
        "boundary. New real qualification checks require reviewed registration and scope-specific "
        "measurement validation. No current hardware, fidelity, distribution or auditory claim is inferred "
        "from the compiler's synthetic fixtures.",
        "",
        "Status precedence: evidence is inspected independently, then any prerequisite other than PASS "
        "makes the effective state BLOCKED (the local state and reason remain visible). Locally: absent "
        "evidence is READY for a registered check or NOT RUN for a planned check; a missing declared "
        "record is NOT RUN and unhealthy. Invalid records/references are NO VERDICT; digest changes "
        "are STALE. Coverage and references are checked before execution/control outcomes. Explicit "
        "nonexecution is NOT RUN; an undetected control or failed check is FAIL; refused measurements "
        "are NO VERDICT; only the remaining valid case is PASS. An expensive node is cancellable "
        "whenever its named cheaper predecessor is not PASS.",
        "",
        "| Node | Claim class | State | Local evidence | Reason |",
        "| --- | --- | --- | --- | --- |",
    ]
    nodes = _ordered(graph)
    for node in nodes:
        result = results[node["id"]]
        lines.append(
            "| "
            + " | ".join(
                map(
                    cell,
                    [
                        node["id"],
                        node["claim_class"],
                        result.state,
                        result.local_state,
                        "; ".join(result.reasons),
                    ],
                )
            )
            + " |"
        )
    lines += ["", "```mermaid", "graph TD"]
    for i, node in enumerate(nodes):
        lines.append(f'  n{i}["{node["id"]}: {results[node["id"]].state}"]')
    indices = {node["id"]: i for i, node in enumerate(nodes)}
    for node in nodes:
        for dependency in sorted(node["dependencies"]):
            lines.append(f"  n{indices[dependency]} --> n{indices[node['id']]}")
    lines += ["```", ""]
    for node in nodes:
        check = CHECKS.get(node["check"])
        lines += [
            f"## {node['id']}",
            "",
            cell(node["claim"]),
            "",
            f"- Engine/layer/scope: `{node['engine']}` / `{node['layer']}` / `{node['scope']}`.",
            f"- Check: `{node['check']}`; "
            + (
                "`" + " ".join(check.command) + "`."
                if check
                else "planned, no registered runnable implementation."
            ),
            "- Dependencies: " + (", ".join(node["dependencies"]) or "none") + ".",
            "- Inputs (plus implicit coverage): "
            + ", ".join(f"`{path}`" for path in node["coverage"]["inputs"])
            + ".",
            "- References: "
            + (
                ", ".join(
                    f"{r['id']} ({r['kind']}): `{r['path']}`"
                    for r in node["coverage"]["references"]
                )
                or "none declared"
            )
            + ".",
            "- Evidence: "
            + (
                f"`{node['evidence']['path']}` (SHA-256 `{node['evidence']['sha256']}`)"
                if node["evidence"]
                else "none"
            )
            + ".",
            "- Required controls: "
            + ", ".join(
                f"{key} detects {value}"
                for key, value in sorted(node["negative_controls"].items())
            )
            + ".",
            "- Cheaper cancellation gate: "
            + (node["cheaper_predecessor"] or "not an expensive node")
            + ".",
            "- Excludes: " + "; ".join(map(cell, node["exclusions"])) + ".",
            "",
        ]
    return "\n".join(lines)
