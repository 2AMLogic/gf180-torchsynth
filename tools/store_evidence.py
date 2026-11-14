#!/usr/bin/env python3
"""Store a scratch directory of run outputs in the fleet artifact store (issue #409).

Usage: ``store_evidence.py SCRATCH_DIR BENCH RUN_ID [--summary PATH]``

Every regular file under SCRATCH_DIR goes through ``klt artifact put``; git keeps
only ``manifests/artifacts/<BENCH>-<RUN_ID>.manifest.json`` and a Markdown
summary (default ``sim/<BENCH>/<RUN_ID>-stored-evidence.md``) listing name, size
and sha256 per object. Scratch directories belong under ``sim/stage/`` (git-ignored).

Fail closed: if ``$KLT_ARTIFACT_BROKER`` is unset, ``klt`` has no ``artifact put``,
the broker is unreachable or refuses, or a put fails part-way, the exit is nonzero
and nothing is written to the repo. There is no fallback that commits bytes.
The broker URL is read by ``klt`` from the environment and never printed.

Stdlib only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ENV_BROKER = "KLT_ARTIFACT_BROKER"
MANIFEST_DIR = Path("manifests") / "artifacts"
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
PUT_TIMEOUT_S = 900
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


class StoreError(RuntimeError):
    """Evidence could not be stored; nothing was written (fail closed)."""


def sha256_file(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    n = 0
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n


def preflight(*, klt: str = "klt", env=None, which=shutil.which, run=subprocess.run) -> str:
    env = os.environ if env is None else env
    if not env.get(ENV_BROKER):
        raise StoreError(f"no artifact broker configured (${ENV_BROKER} is unset)")
    exe = which(klt)
    if exe is None:
        raise StoreError(f"`{klt}` is not on PATH")
    try:
        ok = run([exe, "artifact", "put", "--help"], capture_output=True, text=True,
                 timeout=60, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    if not ok:
        raise StoreError(f"`{klt}` has no `artifact put` verb; need klayout-tools 7e3561f or later")
    return exe


def stage_files(scratch: Path) -> list[tuple[Path, str]]:
    """Regular files under ``scratch`` as ``(path, relative-name)``; symlinks refused."""
    if not scratch.is_dir():
        raise StoreError(f"{scratch} is not a directory")
    out = []
    for path in sorted(scratch.rglob("*")):
        if path.is_symlink():
            raise StoreError(f"scratch contains a symlink: {path.name}")
        if path.is_file():
            out.append((path, path.relative_to(scratch).as_posix()))
    return out


def _klt_error(stderr: str) -> str:
    try:
        err = json.loads(stderr)["error"]
        return f"{err.get('code', 'error')}: {err.get('message', '')}".strip()
    except (ValueError, KeyError, TypeError):
        lines = [ln for ln in stderr.strip().splitlines() if ln.strip()]
        return lines[-1][:300] if lines else "no diagnostic"


def put_files(files, repo_root: Path, *, klt: str, run=subprocess.run) -> tuple[dict, list[str]]:
    """Store each ``(path, name)``; return ``(manifest, empty_names)``.

    The manifest is built in a private temp dir, never in the repo. Zero-byte
    files cannot be stored (their content is known) and are listed separately.
    """
    expected: dict[str, tuple[str, int]] = {}
    empty: list[str] = []
    with tempfile.TemporaryDirectory(prefix="evidence-manifest-") as tmp:
        tmp_manifest = os.path.join(tmp, "manifest.json")
        for path, name in files:
            sha, size = sha256_file(path)
            if size == 0:
                empty.append(name)
                continue
            expected[name] = (sha, size)
            cmd = [klt, "artifact", "put", str(path), "--name", name,
                   "--manifest", tmp_manifest, "--format", "json"]
            try:
                p = run(cmd, cwd=str(repo_root), capture_output=True, text=True,
                        timeout=PUT_TIMEOUT_S, check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise StoreError(f"klt artifact put {name}: {type(exc).__name__}") from None
            if p.returncode != 0:
                raise StoreError(f"klt artifact put {name} failed "
                                 f"(exit {p.returncode}): {_klt_error(p.stderr)}")
        if not expected:
            raise StoreError("nothing to store: no non-empty files in the scratch directory")
        try:
            with open(tmp_manifest, encoding="utf-8") as fh:
                manifest = json.load(fh)
        except (OSError, ValueError) as exc:
            raise StoreError(f"klt wrote no readable manifest: {exc}") from None
    entries = manifest.get("entries") if isinstance(manifest, dict) else None
    if not isinstance(entries, list) or manifest.get("schema_version") != 1:
        raise StoreError("klt manifest is not {schema_version: 1, entries: [...]}")
    by_name = {e.get("name"): e for e in entries if isinstance(e, dict)}
    if set(by_name) != set(expected):
        raise StoreError("klt manifest entries do not match the staged files")
    for name, (sha, size) in expected.items():
        e = by_name[name]
        if (e.get("sha256"), e.get("size")) != (sha, size):
            raise StoreError(f"klt manifest entry for {name} does not match the local sha256/size")
        if not str(e.get("uri", "")).startswith("artifact:"):
            raise StoreError(f"klt manifest entry for {name} has a non-artifact: uri")
    manifest["entries"] = sorted(entries, key=lambda e: e["name"])
    return manifest, sorted(empty)


def summary_markdown(bench: str, rid: str, manifest_rel: str, manifest: dict,
                     empty: list[str]) -> str:
    total = sum(e["size"] for e in manifest["entries"])
    lines = [
        f"# Stored evidence: {bench} {rid}", "",
        f"Bytes are in the fleet artifact store, not in git. Manifest: `{manifest_rel}` "
        f"({len(manifest['entries'])} objects, {total} B). Verify with "
        f"`klt artifact verify {manifest_rel}`; fetch one with "
        f"`klt artifact get {manifest_rel}#<name> -o <out>`.", "",
        "| name | size (B) | sha256 |", "|---|---:|---|",
    ]
    for e in manifest["entries"]:
        lines.append(f"| `{e['name']}` | {e['size']} | `{e['sha256']}` |")
    for name in empty:
        lines.append(f"| `{name}` | 0 | `{EMPTY_SHA256}` (empty, not stored) |")
    return "\n".join(lines) + "\n"


def store(scratch: Path, bench: str, rid: str, *, repo_root: Path, summary: Path | None = None,
          klt: str = "klt", env=None, which=shutil.which, run=subprocess.run) -> Path:
    for part in (bench, rid):
        if not ID_RE.match(part):
            raise StoreError(f"invalid bench or run id {part!r}")
    exe = preflight(klt=klt, env=env, which=which, run=run)
    mpath = repo_root / MANIFEST_DIR / f"{bench}-{rid}.manifest.json"
    spath = summary or repo_root / "sim" / bench / f"{rid}-stored-evidence.md"
    for p in (mpath, spath):
        if os.path.lexists(p):
            raise StoreError(f"{p} already exists; refusing to overwrite evidence")
    files = stage_files(scratch)
    if not files:
        raise StoreError("nothing staged to store")
    manifest, empty = put_files(files, repo_root, klt=exe, run=run)
    mrel = mpath.relative_to(repo_root).as_posix()
    text = summary_markdown(bench, rid, mrel, manifest, empty)
    mpath.parent.mkdir(parents=True, exist_ok=True)
    spath.parent.mkdir(parents=True, exist_ok=True)
    with open(mpath, "x", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")
    with open(spath, "x", encoding="utf-8") as fh:
        fh.write(text)
    return mpath


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("scratch", type=Path)
    ap.add_argument("bench")
    ap.add_argument("run_id")
    ap.add_argument("--summary", type=Path, help="summary path (default sim/<bench>/<run-id>-stored-evidence.md)")
    args = ap.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        mpath = store(args.scratch, args.bench, args.run_id, repo_root=root, summary=args.summary)
    except StoreError as exc:
        print(f"ERROR: {exc}; nothing was written", file=sys.stderr)
        return 1
    print(f"stored; commit {mpath.relative_to(root)} and its summary (not the scratch bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
