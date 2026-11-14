#!/usr/bin/env python3
"""Stored-evidence manifests: git-side schema guard (issue #409).

New simulation and signoff evidence bytes go to the fleet artifact store through
``klt artifact put`` (see ``tools/store_evidence.py``); git keeps a manifest per
run under ``manifests/artifacts/<bench>-<run-id>.manifest.json`` and a Markdown
summary. Evidence bytes are private and manifests are public.

``check [--tree REV] [--repo PATH]`` reads every manifest committed in ``REV``
(blob contents via git, so a sparse working tree does not matter) and fails on:
a malformed manifest, an unknown or missing field, a ``uri`` that does not start
``artifact:``, a key that does not end in the entry's sha256, a duplicate name,
a ``name`` that is also a tracked path in ``REV`` (the stored bytes must not
also be committed), and any ``s3://`` or ``arn:aws:`` string.

``check`` never contacts the broker: schema-valid is not hash-verified. To
verify bytes run ``klt artifact verify <manifest>`` with ``$KLT_ARTIFACT_BROKER``
set.

Exit codes: 0 ok; 1 a manifest is invalid; 2 usage or git error.

Stdlib only; needs ``git`` on PATH.
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import subprocess
import sys

MANIFEST_DIR = "manifests/artifacts/"
MANIFEST_RE = re.compile(r"^manifests/artifacts/[A-Za-z0-9][A-Za-z0-9._-]*\.manifest\.json$")
DIR_FILES_ALLOWED = {"manifests/artifacts/README.md"}

SCHEMA_VERSION = 1
KIND = "evidence"
URI_PREFIX = "artifact:"
REQUIRED = ("sha256", "size", "kind", "producer", "uri")
OPTIONAL = ("name", "media_type")
PRODUCER_FIELDS = ("tool", "version", "loom.repo", "commit")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
STORAGE_RE = re.compile(r"s3://|arn:aws:")

EXIT_OK, EXIT_INVALID, EXIT_ERROR = 0, 1, 2


class ToolError(Exception):
    """Git failure: exit 2, fail closed."""


def valid_name(name) -> bool:
    """A relative POSIX path with no ``..``, absolute or odd components."""
    return (isinstance(name, str) and name != ""
            and "\\" not in name and "\0" not in name
            and not name.startswith("/")
            and posixpath.normpath(name) == name
            and not {"", ".", ".."} & set(name.split("/")))


def entry_problems(entry) -> list[str]:
    if not isinstance(entry, dict):
        return ["entry is not an object"]
    problems = []
    missing = [f for f in REQUIRED if f not in entry]
    extra = sorted(set(entry) - set(REQUIRED) - set(OPTIONAL))
    if missing:
        problems.append(f"missing {', '.join(missing)}")
    if extra:
        problems.append(f"unknown field(s) {', '.join(extra)}")
    sha = entry.get("sha256")
    if not isinstance(sha, str) or not SHA256_RE.match(sha):
        problems.append("sha256 must be 64 lowercase hex digits")
    size = entry.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size < 1:
        problems.append("size must be a positive integer")
    if entry.get("kind") != KIND:
        problems.append(f"kind must be {KIND!r}")
    producer = entry.get("producer")
    if not isinstance(producer, dict) or set(producer) != set(PRODUCER_FIELDS):
        problems.append(f"producer must carry exactly {', '.join(PRODUCER_FIELDS)}")
    else:
        for f in ("tool", "version", "loom.repo"):
            if not isinstance(producer.get(f), str) or not producer[f]:
                problems.append(f"producer.{f} must be a non-empty string")
        if producer.get("commit") is not None and not isinstance(producer["commit"], str):
            problems.append("producer.commit must be a string or null")
    uri = entry.get("uri")
    if not isinstance(uri, str) or not uri.startswith(URI_PREFIX):
        problems.append(f"uri must start with {URI_PREFIX!r}")
    elif uri[len(URI_PREFIX):].rsplit("/", 1)[-1] != sha:
        problems.append("uri key does not end in the entry's sha256")
    if "name" not in entry:
        problems.append("name is required here (the logical path of the bytes)")
    elif not valid_name(entry["name"]):
        problems.append("name must be a relative path with no '..' or absolute parts")
    if "media_type" in entry and not isinstance(entry["media_type"], str):
        problems.append("media_type must be a string")
    return problems


def manifest_problems(manifest) -> list[str]:
    if (not isinstance(manifest, dict) or set(manifest) != {"schema_version", "entries"}
            or manifest.get("schema_version") != SCHEMA_VERSION
            or not isinstance(manifest.get("entries"), list)):
        return [f"not {{schema_version: {SCHEMA_VERSION}, entries: [...]}}"]
    if not manifest["entries"]:
        return ["has no entries"]
    problems = []
    seen = set()
    for i, entry in enumerate(manifest["entries"]):
        problems += [f"entry {i}: {p}" for p in entry_problems(entry)]
        name = entry.get("name") if isinstance(entry, dict) else None
        if isinstance(name, str):
            if name in seen:
                problems.append(f"entry {i}: duplicate name {name!r}")
            seen.add(name)
    return problems


def git(repo: str, *args: str) -> bytes:
    try:
        p = subprocess.run(["git", "-C", repo, *args], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, check=False,
                           env={**os.environ, "GIT_NO_LAZY_FETCH": "1"})
    except OSError as exc:
        raise ToolError(f"cannot run git: {exc}") from exc
    if p.returncode != 0:
        raise ToolError(f"git {' '.join(args)} failed: "
                        f"{p.stderr.decode('utf-8', 'replace').strip()}")
    return p.stdout


def tracked_paths(repo: str, tree: str) -> dict[str, tuple[str, str]]:
    """``{path: (mode, type)}`` of every entry in ``tree`` (recursive)."""
    out = {}
    for rec in git(repo, "ls-tree", "-r", "-z", tree).split(b"\0"):
        if not rec:
            continue
        meta, _, name = rec.partition(b"\t")
        mode, typ, _ = meta.decode().split(" ")
        out[name.decode("utf-8", "surrogateescape")] = (mode, typ)
    return out


def check(repo: str, rev: str) -> int:
    if rev.startswith("-"):
        raise ToolError(f"invalid revision {rev!r}")
    tree = git(repo, "rev-parse", "--verify", "--quiet", "--end-of-options",
               f"{rev}^{{tree}}").decode().strip()
    if not tree:
        raise ToolError(f"revision {rev!r} is unavailable")
    paths = tracked_paths(repo, tree)
    errors: list[str] = []
    manifests = entries = 0
    for path in sorted(p for p in paths if p.startswith(MANIFEST_DIR)):
        mode, typ = paths[path]
        if path in DIR_FILES_ALLOWED:
            continue
        if not MANIFEST_RE.match(path) or typ != "blob" or mode == "120000":
            errors.append(f"{path}: only <bench>-<run-id>.manifest.json files (and "
                          "README.md) belong in manifests/artifacts/")
            continue
        raw = git(repo, "show", f"{tree}:{path}")
        text = raw.decode("utf-8", "replace")
        if STORAGE_RE.search(text):
            errors.append(f"{path}: names a storage location (s3:// or arn:aws:); "
                          "manifests carry artifact: URIs only")
            continue
        try:
            manifest = json.loads(text)
        except ValueError as exc:
            errors.append(f"{path}: not valid JSON: {exc}")
            continue
        problems = manifest_problems(manifest)
        errors += [f"{path}: {p}" for p in problems]
        if problems:
            continue
        manifests += 1
        for entry in manifest["entries"]:
            entries += 1
            if entry["name"] in paths:
                errors.append(
                    f"{path}: {entry['name']} is committed in the tree; stored "
                    "evidence bytes must not also be committed (no blob fallback)")
    if errors:
        print(f"FAIL: {len(errors)} stored-evidence manifest problem(s) in {rev}:",
              file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        return EXIT_INVALID
    print(f"OK: {manifests} manifest(s), {entries} entr{'y' if entries == 1 else 'ies'} "
          f"in {MANIFEST_DIR} of {rev}: schema valid, no stored bytes committed. "
          "Schema-valid is not hash-verified: no broker was contacted; run "
          "`klt artifact verify <manifest>` with $KLT_ARTIFACT_BROKER set.")
    return EXIT_OK


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="schema + no-committed-blob guard for a tree")
    c.add_argument("--tree", default="HEAD", help="commit or tree (default HEAD)")
    c.add_argument("--repo", default=".", help="repository path (default .)")
    args = ap.parse_args(argv)
    try:
        return check(args.repo, args.tree)
    except ToolError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
