"""Mechanical pre-unseal gate for the one-shot holdout (issue #55).

Standard library only. The gate *verifies* that the rubric, corpus manifest,
case registry, fixed-model golden record and model code are exactly the bytes
pinned by ``spec/reference/holdout-seal-v0.json`` and that the pinned commit is
published history, instead of trusting an operator attestation. It never reads
holdout artifacts, never creates a ledger and never renders anything. It is
read-only apart from ``git`` queries and the injected rubric validator.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

SEAL_PATH = "spec/reference/holdout-seal-v0.json"
SEAL_SCHEMA = "torchsynth-holdout-seal"
RUBRIC_PATH = "spec/reference/rubric-v0.json"
CORPUS_PATH = "spec/reference/corpus-v0.json"
REGISTRY_PATH = "spec/reference/case-registry-v1.json"
GOLDEN_PATH = "sim/reference/fixed-voice-golden-v1.json"
UPSTREAM_PATH = "spec/reference/upstream.json"
PINNED_FILES = (RUBRIC_PATH, CORPUS_PATH, REGISTRY_PATH, GOLDEN_PATH)
MODEL_FILE = "src/torchsynth_voice/fixed_voice.py"
MODEL_PACKAGE = "src/torchsynth_voice/fixedpoint"
# Transitive behavior-affecting inputs of ``fixed_voice`` outside the package:
# the import closure (``torchsynth_voice/__init__`` through ``float_sources``,
# ``format_sweep``, ``control_path`` ...) and the JSON registers those modules
# and ``fixedpoint`` load at import or run time. Editing any of them changes
# the evaluated model, so each is hashed and cleanliness-checked like the model.
MODEL_DEPENDENCIES = (
    "src/torchsynth_voice/__init__.py",
    "src/torchsynth_voice/control_path.py",
    "src/torchsynth_voice/float_interfaces.py",
    "src/torchsynth_voice/float_mix.py",
    "src/torchsynth_voice/float_sources.py",
    "src/torchsynth_voice/float_voice.py",
    "src/torchsynth_voice/format_sweep.py",
    "src/torchsynth_voice/identity.py",
    "src/torchsynth_voice/trace_registry.py",
    "spec/reference/fixedpoint-choices-v1.json",
    "spec/reference/rtl-schedule-v1.json",
    "spec/reference/float-checkpoints-v1.json",
    "spec/reference/parameter-inventory-v1.json",
    "spec/reference/trace-registry-v1.json",
    "spec/reference/upstream.json",
    "spec/schemas/float-interface-v1.schema.json",
    "spec/schemas/trace-registry-v1.schema.json",
)
MODEL_EXTRA_FILES = (MODEL_FILE,) + MODEL_DEPENDENCIES
HOLDOUT_INDICES = tuple(range(96, 128))
FROZEN_SCHEMA = "torchsynth-frozen-rubric"


class SealRefusal(Exception):
    """Raised when the gate refuses; ``checks`` holds every evaluated check."""

    def __init__(self, checks):
        self.checks = checks
        failed = [c["check"] for c in checks if not c["ok"]]
        super().__init__("holdout seal gate refused: " + ", ".join(failed))


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def tree_sha256(files):
    """Hash of a ``{relative path: file sha256}`` mapping, order independent."""
    text = "".join("%s  %s\n" % (files[p], p) for p in sorted(files))
    return sha256_hex(text.encode("utf-8"))


def _git(root, *args):
    done = subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, check=False
    )
    return done.returncode, done.stdout


def _commit_bytes(root, commit, path):
    code, out = _git(root, "show", "%s:%s" % (commit, path))
    return out if code == 0 else None


def _commit_model_paths(root, commit):
    code, out = _git(root, "ls-tree", "-r", "--name-only", commit, "--", MODEL_PACKAGE)
    if code != 0:
        return None
    paths = [p for p in out.decode("utf-8").splitlines() if p]
    return sorted(set(paths) | set(MODEL_EXTRA_FILES))


def _worktree_model_paths(root):
    package = Path(root) / MODEL_PACKAGE
    found = [
        p.relative_to(root).as_posix()
        for p in package.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    ]
    return sorted(set(found) | set(MODEL_EXTRA_FILES))


def build_seal(root, commit):
    """Compute a seal manifest from the content of ``commit`` (not the worktree)."""
    root = Path(root)
    code, full = _git(root, "rev-parse", "--verify", commit + "^{commit}")
    if code != 0:
        raise ValueError("unknown commit " + commit)
    full = full.decode().strip()
    files = {}
    for path in PINNED_FILES:
        data = _commit_bytes(root, full, path)
        if data is None:
            raise ValueError("%s missing at %s" % (path, full))
        files[path] = sha256_hex(data)
    model = {}
    for path in _commit_model_paths(root, full):
        data = _commit_bytes(root, full, path)
        if data is None:
            raise ValueError("%s missing at %s" % (path, full))
        model[path] = sha256_hex(data)
    upstream = json.loads(_commit_bytes(root, full, UPSTREAM_PATH))["target_commit"]
    return {
        "schema": SEAL_SCHEMA,
        "schema_version": 1,
        "semantic_version": "holdout-seal-v0",
        "issue": 55,
        "pinned_commit": full,
        "upstream_pin": upstream,
        "files": files,
        "model": {"files": model, "tree_sha256": tree_sha256(model)},
        "holdout": {
            "first_index": 96,
            "last_index": 127,
            "count": 32,
            "holdout_identities_read": 0,
        },
    }


def _load_seal(root, seal_path):
    data = (Path(root) / seal_path).read_bytes()
    seal = json.loads(data)
    ok = (
        type(seal) is dict
        and seal.get("schema") == SEAL_SCHEMA
        and seal.get("schema_version") == 1
        and set(seal)
        == {
            "schema",
            "schema_version",
            "semantic_version",
            "issue",
            "pinned_commit",
            "upstream_pin",
            "files",
            "model",
            "holdout",
        }
        and type(seal["files"]) is dict
        and set(seal["files"]) == set(PINNED_FILES)
        and type(seal["model"]) is dict
        and set(seal["model"]) == {"files", "tree_sha256"}
        and seal["holdout"]
        == {
            "first_index": 96,
            "last_index": 127,
            "count": 32,
            "holdout_identities_read": 0,
        }
    )
    if not ok:
        raise ValueError("seal manifest is malformed")
    return seal, sha256_hex(data)


def check_pinned_hashes(root, seal):
    """Offline half of the gate: current worktree bytes versus the seal."""
    root = Path(root)
    problems = []
    for path, expected in sorted(seal["files"].items()):
        target = root / path
        if not target.is_file():
            problems.append("%s missing" % path)
        elif sha256_hex(target.read_bytes()) != expected:
            problems.append("%s differs from pinned sha256" % path)
    current = {}
    for path in _worktree_model_paths(root):
        target = root / path
        if target.is_file():
            current[path] = sha256_hex(target.read_bytes())
    if current != seal["model"]["files"]:
        problems.append("model file set or contents differ from pin")
    if tree_sha256(seal["model"]["files"]) != seal["model"]["tree_sha256"]:
        problems.append("seal model tree hash is internally inconsistent")
    if tree_sha256(current) != seal["model"]["tree_sha256"]:
        problems.append("model tree sha256 differs from pin")
    return problems


def default_rubric_validator(root):
    done = subprocess.run(
        [sys.executable, str(Path(root) / "tools/validate_rubric.py")],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    return done.returncode == 0, (done.stdout + done.stderr).strip()[-400:]


def verify_holdout_seal(
    root,
    *,
    indices,
    corpus_manifest_sha256,
    frozen_rubric,
    audit_root,
    resume=False,
    seal_path=SEAL_PATH,
    origin_ref="origin/main",
    rubric_validator=default_rubric_validator,
):
    """Run every check; return the report or raise ``SealRefusal``.

    Pure verification: nothing is created, no renderer, artifact store or
    holdout payload is touched. ``resume`` is the single documented exception
    to the no-prior-ledger rule (same-run continuation of the one exposure).
    """
    root = Path(root)
    checks = []

    def record(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    try:
        seal, seal_sha = _load_seal(root, seal_path)
    except (OSError, ValueError, KeyError, TypeError) as error:
        record("seal-manifest", False, str(error))
        raise SealRefusal(checks)
    record("seal-manifest", True, "sha256 " + seal_sha)

    problems = check_pinned_hashes(root, seal)
    record("pinned-hashes", not problems, "; ".join(problems))

    commit = seal["pinned_commit"]
    code, out = _git(root, "rev-parse", "--verify", str(commit) + "^{commit}")
    exists = code == 0 and out.decode().strip() == commit
    record("pinned-commit-exists", exists, commit)
    if exists:
        mismatch = []
        for path, expected in sorted(seal["files"].items()):
            data = _commit_bytes(root, commit, path)
            if data is None or sha256_hex(data) != expected:
                mismatch.append(path)
        at_commit = {}
        for path in _commit_model_paths(root, commit) or []:
            data = _commit_bytes(root, commit, path)
            if data is not None:
                at_commit[path] = sha256_hex(data)
        if at_commit != seal["model"]["files"]:
            mismatch.append("model")
        record(
            "pinned-commit-content",
            not mismatch,
            "pinned hashes do not match content at pinned commit: "
            + ", ".join(mismatch)
            if mismatch
            else "",
        )
        for name, ref in (("head", "HEAD"), ("origin-main", origin_ref)):
            code, _ = _git(root, "merge-base", "--is-ancestor", commit, ref)
            record("pinned-commit-ancestor-of-" + name, code == 0, ref)
    else:
        for name in (
            "pinned-commit-content",
            "pinned-commit-ancestor-of-head",
            "pinned-commit-ancestor-of-origin-main",
        ):
            record(name, False, "pinned commit unavailable")

    paths = sorted(set(seal["files"]) | set(seal["model"]["files"]) | {MODEL_PACKAGE})
    code, out = _git(root, "status", "--porcelain", "--untracked-files=all", "--", *paths)
    record(
        "pinned-paths-clean",
        code == 0 and not out.strip(),
        out.decode("utf-8", "replace").strip()[:300],
    )

    try:
        upstream = json.loads((root / UPSTREAM_PATH).read_bytes())["target_commit"]
    except (OSError, ValueError, KeyError, TypeError):
        upstream = None
    record(
        "upstream-pin",
        upstream is not None and upstream == seal["upstream_pin"],
        "seal %s versus %s" % (seal["upstream_pin"], upstream),
    )

    ok, detail = rubric_validator(root)
    record("rubric-validator", ok, "" if ok else detail)

    record(
        "corpus-manifest-pinned",
        corpus_manifest_sha256 == seal["files"][CORPUS_PATH],
        "supplied manifest sha256 " + str(corpus_manifest_sha256),
    )

    record(
        "index-set",
        indices is not None
        and type(indices) in (list, tuple)
        and sorted(indices) == list(HOLDOUT_INDICES),
        "holdout run must select exactly identities 96-127",
    )

    freeze_detail, freeze_ok = "", False
    try:
        data = Path(frozen_rubric).read_bytes()
        frozen = json.loads(data)
        committed = json.loads((root / RUBRIC_PATH).read_bytes())
        freeze_ok = (
            type(frozen) is dict
            and set(frozen) == {"schema", "schema_version", "frozen", "rubric"}
            and frozen["schema"] == FROZEN_SCHEMA
            and frozen["schema_version"] == 1
            and frozen["frozen"] is True
            and frozen["rubric"] == committed
        )
        freeze_detail = (
            "freeze sha256 %s (recomputed)" % sha256_hex(data)
            if freeze_ok
            else "frozen rubric content differs from committed rubric-v0"
        )
    except (OSError, ValueError, TypeError) as error:
        freeze_detail = "frozen rubric unreadable: %s" % type(error).__name__
    record("frozen-rubric-equals-committed", freeze_ok, freeze_detail)

    ledger = Path(audit_root) if audit_root is not None else None
    if ledger is None:
        record("ledger-state", False, "audit root missing")
    else:
        try:
            inside = Path(ledger).resolve().is_relative_to(root.resolve())
        except OSError:
            inside = True
        record("ledger-outside-repository", not inside, "")
        entry = ledger / (str(corpus_manifest_sha256) + ".json")
        if resume:
            record("ledger-state", entry.is_file(), "same-run resume needs the ledger")
        else:
            record(
                "ledger-state",
                not entry.exists(),
                "a holdout admission already exists for this corpus manifest"
                if entry.exists()
                else "",
            )

    if not all(c["ok"] for c in checks):
        raise SealRefusal(checks)
    return {"status": "verified", "seal_sha256": seal_sha, "checks": checks}
