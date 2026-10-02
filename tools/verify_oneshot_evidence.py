#!/usr/bin/env python3
"""Verify an issue-#79 evidence record is actually committable.

``tb/run_oneshot.py`` and ``tb/run_voice.py`` each write their own evidence
record (``oneshot-evidence.json`` / ``voice-evidence.json``) on every run,
whether it passed or not -- an unrun or failed check is never silently
upgraded to a pass. But "the flow produced a record" and "that record is
safe to commit as a citation of the exact commit under review" are different
questions. Per ``spec/ONESHOT-E2E.md`` ("Evidence identity"), a record is
citeable only if:

1. it reports ``result: PASS`` (a failed run proves nothing and must never
   be committed as if it had);
2. its ``identity.git_tree_dirty`` is ``false`` (a record generated from an
   uncommitted-change tree cites a tree state that no commit actually has);
3. its ``identity.git_head`` names the commit the caller intends to cite --
   normally the clean commit the flow was run on, checked with
   ``--expect-head``;
4. the sources it digests in ``identity.rtl_sha256`` /
   ``identity.fixed_vector_sha256`` are **still byte-identical in the tree
   being checked** (``--against-tree``, on by default). A record is a claim
   about specific RTL and specific frozen vectors; the moment one of them
   changes, the record stops describing the tree it sits in, and nothing
   about its own ``result``/``git_head``/``git_tree_dirty`` fields notices.
   That staleness is what this check refuses: an edited engine must be
   re-proven, not inherit the previous record's pass.
5. its ``identity.git_head`` is **reachable on the default branch**
   (``--require-reachable``). Checks 1-4 are all satisfied by a record
   produced on a perfectly clean *pull-request branch* commit -- and this
   repository squash-merges, so a branch's own commits never reach ``main``.
   Such a record cites a SHA no reader can ever resolve: a citation to
   nowhere that every other check here calls committable. Until this flag
   existed ``spec/ONESHOT-E2E.md`` could only tell a reader to check
   reachability by hand, which is exactly the kind of step that gets skipped
   (issue #79 open item 1 was blocked on precisely this for two increments).

Reachability has a third answer besides yes/no: *undecidable*, when the
checkout cannot see enough history to tell. A shallow clone (``git clone
--depth N``, or the default of ``actions/checkout@v4``, ``fetch-depth: 1``)
genuinely cannot say whether an absent commit was never created or merely
lies outside its fetched window -- and those two cases must not collapse into
one message, because only the first is a real defect in the record. This
tool tells them apart with ``git rev-parse --is-shallow-repository``: a
commit missing from a *shallow* checkout is reported ``UNDECIDABLE`` (exit
2), never read as a failure or, worse, a pass; a commit missing from a
*full* checkout is reported ``UNREACHABLE`` (exit 1) -- it was never created
under that SHA at all, and no amount of fetching will produce it. (Issue
#268: the first cut of ``--require-reachable`` folded both into one "a
shallow clone cannot answer this" message, which was accurate for neither
case on its own.)

This script is the mechanical form of that checklist, so "is this record
committable" is answered by an exit code rather than by eyeballing JSON.
It does not run any simulation itself and is simulator-free -- it only reads
an already-produced evidence file, hashes the named source files, and (with
``--require-reachable``) asks ``git`` an ancestry question.

Usage::

    python3 tools/verify_oneshot_evidence.py <evidence.json> [--expect-head SHA]
    python3 tools/verify_oneshot_evidence.py <evidence.json> --require-reachable
    python3 tools/verify_oneshot_evidence.py <evidence.json> --skip-tree-check

Exit 0 (``COMMITTABLE``) only if every check above passes (and, when given,
``--expect-head`` matches exactly). Exit 1 (``NOT COMMITTABLE``) if any check
establishes a definite failure, with every failing reason printed -- never
partial credit for a record that fails any one check. Exit 2
(``REACHABILITY UNDECIDABLE``) only when ``--require-reachable`` was given,
every other check passed, and reachability specifically could not be
decided (the shallow-clone case) -- distinct from both of the above so an
undecided answer can never be mistaken for either a pass or an established
failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

#: Recognized issue-#79 evidence schemas. A record under any other schema
#: name is refused rather than guessed at -- this tool must never silently
#: pass something it was not written to understand.
KNOWN_SCHEMAS = (
    "gf180-torchsynth/oneshot-tail-chain-evidence-v1",
    "gf180-torchsynth/oneshot-whole-voice-evidence-v1",
)

#: Repository-relative directories a digested source may live in. The flows
#: record digests under a bare file name (``path.name``), so freshness has to
#: resolve a name back to a path; searching a declared, short list of roots
#: keeps that resolution honest without duplicating either flow's own source
#: list (which would be free to drift out of agreement with it). A name that
#: resolves to nothing, or to more than one file, is an error -- never a skip.
SOURCE_ROOTS = ("tb/sv", "spec/reference", "sim/reference")

#: The identity sub-blocks whose entries are ``{file name: sha256}``.
DIGEST_BLOCKS = ("rtl_sha256", "fixed_vector_sha256")

#: Default repository root: this file lives in ``<root>/tools/``.
REPO_ROOT = Path(__file__).resolve().parents[1]

#: Spellings of "the default branch" the reachability check will accept,
#: tried in order. A bare clone has ``origin/main``; a checkout that is
#: itself ``main`` has ``main``; ``actions/checkout`` on a ``pull_request``
#: event leaves a detached HEAD at the merge commit but, at
#: ``fetch-depth: 0``, does create ``refs/remotes/origin/main``. The list is
#: declared rather than guessed so a failure can name what it looked for.
DEFAULT_BRANCH_REFS = ("origin/main", "refs/remotes/origin/main",
                       "main", "refs/heads/main")

#: The three answers ``reachability_status`` can give. ``REACHABLE`` is the
#: only one this tool treats as a pass; ``UNREACHABLE`` is an established
#: failure (the commit was checked and is not an ancestor, or never existed
#: at all in a checkout that could have seen it); ``UNDECIDABLE`` is neither
#: -- the checkout genuinely cannot answer (most commonly a shallow clone),
#: so it must read as "unknown", not as either of the other two.
REACHABLE = "reachable"
UNREACHABLE = "unreachable"
UNDECIDABLE = "undecidable"


def git_ok(repo_root: Path, *args: str) -> bool:
    """Whether ``git <args>`` exits 0 in ``repo_root`` (output discarded)."""

    try:
        completed = subprocess.run(
            ("git", "-C", str(repo_root)) + args,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return False
    return completed.returncode == 0


def git_capture(repo_root: Path, *args: str) -> str:
    """``git <args>`` stdout in ``repo_root``, or ``""`` if it fails."""

    try:
        completed = subprocess.run(
            ("git", "-C", str(repo_root)) + args,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            check=False, text=True,
        )
    except OSError:
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def resolve_default_branch(repo_root: Path,
                           refs=DEFAULT_BRANCH_REFS) -> str | None:
    """The first of ``refs`` that names a commit in ``repo_root``."""

    for ref in refs:
        if git_ok(repo_root, "rev-parse", "--verify", "--quiet",
                  "%s^{commit}" % ref):
            return ref
    return None


def is_shallow_clone(repo_root: Path) -> bool:
    """Whether ``repo_root`` is a shallow git clone.

    ``git rev-parse --is-shallow-repository`` prints ``true``/``false`` and
    is the documented way to ask this -- it is what distinguishes "this
    commit was never created" from "this commit exists, but this checkout's
    history does not reach far enough back to see it", which a bare "commit
    not found" cannot tell apart on its own.
    """

    return git_capture(repo_root, "rev-parse",
                       "--is-shallow-repository") == "true"


def reachability_status(record: dict, repo_root: Path,
                        ref: str | None = None) -> tuple[str, list[str]]:
    """Return ``(status, messages)`` for whether ``record``'s ``git_head``
    is citeable.

    "Citeable" means: a reader handed this record can `git show` the commit
    it names on the default branch. A clean record produced on a
    pull-request branch is *not* citeable -- this repository squash-merges,
    so that commit is never an ancestor of ``main`` and resolves for nobody
    but the agent that produced it.

    ``status`` is one of three mutually exclusive answers:

    - ``REACHABLE`` -- decided, and the commit is an ancestor of the default
      branch. ``messages`` is always empty.
    - ``UNREACHABLE`` -- decided, and the record is not citeable: malformed
      input, a commit the default branch does not contain, or a commit that
      is simply absent from a checkout that is *not* shallow (so the lack is
      not an artifact of a truncated history -- the commit was never created
      under that SHA at all).
    - ``UNDECIDABLE`` -- not decided, and cannot be from this checkout: no
      default-branch ref resolved, the path is not a repository at all, or
      the commit is absent specifically because the checkout is a *shallow*
      clone that may simply not have fetched that far back. This must never
      be reported, or exit, the same way as either of the other two --
      "cannot tell" is not "no" and is certainly not "yes".

    Every way of not knowing the answer carries a remedy in its message,
    because a check that could not run must never look like one that passed.
    """

    identity = record.get("identity")
    if not isinstance(identity, dict):
        return UNREACHABLE, [
            "record carries no identity block, so it names no commit"]
    git_head = identity.get("git_head")
    if not isinstance(git_head, str) or len(git_head) != 40:
        return UNREACHABLE, [
            "identity.git_head is not a 40-character commit SHA: %r"
            % git_head]
    if not git_ok(repo_root, "rev-parse", "--git-dir"):
        return UNDECIDABLE, [
            "%s is not a git repository, so reachability cannot be "
            "checked there -- point --against-tree at a real checkout"
            % repo_root]
    branch = ref or resolve_default_branch(repo_root)
    if branch is None:
        return UNDECIDABLE, [
            "no default-branch ref resolved in %s (tried %s), so there is "
            "nothing to measure reachability against -- deepen the clone "
            "(CI: actions/checkout with fetch-depth: 0) or pass the ref "
            "explicitly" % (repo_root, ", ".join(
                [ref] if ref else list(DEFAULT_BRANCH_REFS)))
        ]
    if not git_ok(repo_root, "cat-file", "-e", "%s^{commit}" % git_head):
        if is_shallow_clone(repo_root):
            return UNDECIDABLE, [
                "%s is a shallow clone and does not contain commit %s, so "
                "its reachability on %s cannot be decided here -- a shallow "
                "clone cannot distinguish a commit outside its fetched "
                "history from one that never existed; deepen it (CI: "
                "actions/checkout with fetch-depth: 0) or point "
                "--against-tree at a full checkout"
                % (repo_root, git_head, branch)
            ]
        return UNREACHABLE, [
            "%s does not contain commit %s at all, and is not a shallow "
            "clone -- this commit was never created under that SHA, in any "
            "branch, so deepening the clone cannot help. Check "
            "identity.git_head for a typo, or confirm the record was "
            "produced against this same repository"
            % (repo_root, git_head)
        ]
    if not git_ok(repo_root, "merge-base", "--is-ancestor", git_head, branch):
        return UNREACHABLE, [
            "identity.git_head %s is not reachable on %s -- a record may "
            "only cite a commit that has actually landed. A pull-request "
            "branch commit is the usual cause: this repository squash-merges,"
            " so branch commits never reach the default branch. Regenerate "
            "the record on an already-landed clean commit."
            % (git_head, branch)
        ]
    return REACHABLE, []


def reachability_errors(record: dict, repo_root: Path,
                        ref: str | None = None) -> list[str]:
    """Flat-list view of :func:`reachability_status`: ``[]`` iff citeable.

    Kept as its own function -- rather than inlining ``[1]`` at every call
    site -- because callers that only care "is this committable" (``verify``,
    and the real-record gate in ``tests/test_committed_oneshot_evidence.py``)
    outnumber the one caller (``main``, via ``--require-reachable``) that
    needs the ``UNREACHABLE``/``UNDECIDABLE`` distinction to pick an exit
    code; both readings must stay available without duplicating the checks
    themselves.
    """

    return reachability_status(record, repo_root, ref)[1]


def resolve_source(name: str, repo_root: Path) -> list[Path]:
    """Every file named ``name`` under the declared source roots."""

    found = []
    for root in SOURCE_ROOTS:
        candidate = repo_root / root / name
        if candidate.is_file():
            found.append(candidate)
    return found


def stale_sources(record: dict, repo_root: Path) -> list[str]:
    """Return every reason ``record``'s digests no longer describe the tree."""

    errors: list[str] = []
    identity = record.get("identity")
    if not isinstance(identity, dict):
        return ["record carries no identity block, so nothing can be hashed"]
    digested = 0
    for block in DIGEST_BLOCKS:
        entries = identity.get(block)
        if not isinstance(entries, dict) or not entries:
            errors.append(
                "identity.%s is %r, not a non-empty {name: sha256} map -- a "
                "record that pins no sources cannot be checked against a tree"
                % (block, entries)
            )
            continue
        for name, recorded in sorted(entries.items()):
            if not isinstance(recorded, str) or len(recorded) != 64:
                errors.append(
                    "identity.%s[%s] is %r, not a 64-character sha256 -- a "
                    "malformed digest can never be matched against a file"
                    % (block, name, recorded)
                )
                continue
            paths = resolve_source(name, repo_root)
            if not paths:
                errors.append(
                    "identity.%s names %s, which does not exist under any of "
                    "%s in %s -- the record describes a source this tree does "
                    "not have" % (block, name, ", ".join(SOURCE_ROOTS),
                                  repo_root)
                )
                continue
            if len(paths) > 1:
                errors.append(
                    "identity.%s names %s, which resolves ambiguously to %s"
                    % (block, name,
                       ", ".join(str(p.relative_to(repo_root)) for p in paths))
                )
                continue
            actual = hashlib.sha256(paths[0].read_bytes()).hexdigest()
            digested += 1
            if actual != recorded:
                errors.append(
                    "%s changed since this record was produced (recorded "
                    "%s, tree has %s) -- the record no longer describes this "
                    "tree and must be regenerated, not re-cited"
                    % (paths[0].relative_to(repo_root), recorded[:12],
                       actual[:12])
                )
    if not errors and digested == 0:
        errors.append("no source digest was checked at all")
    return errors


def verify(record: dict, expect_head: str | None,
           repo_root: Path | None = None) -> list[str]:
    """Return every reason ``record`` is not committable (empty = committable).

    ``repo_root`` opts the freshness check in; passing ``None`` checks only
    the record's self-consistent fields.

    Reachability is deliberately **not** one of the checks folded in here --
    unlike every other check, it has a third possible answer (undecidable,
    e.g. a shallow clone) that must not be reported the same way as an
    established failure, and a flat ``list[str]`` has no room to carry that
    distinction. Call :func:`reachability_status` directly (``main`` does,
    for ``--require-reachable``) and decide what an undecidable answer means
    for your own exit code -- never fold its messages into this list and
    treat it as a plain failure.
    """

    errors: list[str] = []
    schema = record.get("schema")
    if schema not in KNOWN_SCHEMAS:
        errors.append(
            "unrecognized schema %r (expected one of %s)"
            % (schema, ", ".join(KNOWN_SCHEMAS))
        )
    if record.get("result") != "PASS":
        errors.append(
            "result is %r, not PASS -- a failed or unrun check is never "
            "committable as evidence" % record.get("result")
        )
    identity = record.get("identity")
    if not isinstance(identity, dict):
        errors.append("record carries no identity block")
        return errors
    if identity.get("git_tree_dirty") is not False:
        errors.append(
            "identity.git_tree_dirty is %r, not False -- a record produced "
            "from an uncommitted-change tree cites a tree state no commit "
            "actually has" % identity.get("git_tree_dirty")
        )
    git_head = identity.get("git_head")
    if not isinstance(git_head, str) or len(git_head) != 40:
        errors.append("identity.git_head is not a 40-character commit SHA: %r"
                       % git_head)
    elif expect_head is not None and git_head != expect_head:
        errors.append(
            "identity.git_head is %s, not the expected %s -- this record "
            "does not cite the commit you intend to commit it against"
            % (git_head, expect_head)
        )
    if record.get("float_tolerance") is not None:
        errors.append(
            "float_tolerance is %r, not null -- this verifier only accepts "
            "exact-comparison evidence" % record.get("float_tolerance")
        )
    if repo_root is not None:
        errors.extend(stale_sources(record, repo_root))
    return errors


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path,
                         help="path to an oneshot-evidence.json or "
                              "voice-evidence.json file")
    parser.add_argument("--expect-head", default=None,
                         help="require identity.git_head to equal this "
                              "40-character commit SHA")
    parser.add_argument("--against-tree", type=Path, default=REPO_ROOT,
                         help="repository root the record's digested sources "
                              "are re-hashed against (default: this "
                              "checkout)")
    parser.add_argument("--skip-tree-check", action="store_true",
                         help="do not re-hash the digested sources. Only for "
                              "inspecting a record away from the tree it was "
                              "produced from; a record that passes only with "
                              "this flag is NOT established as describing any "
                              "tree you have")
    parser.add_argument("--require-reachable", action="store_true",
                         help="additionally require identity.git_head to be "
                              "reachable on the default branch of "
                              "--against-tree. Use this before committing a "
                              "record: a clean record produced on a "
                              "pull-request branch passes every other check "
                              "while citing a commit that, because this "
                              "repository squash-merges, never lands. If the "
                              "checkout is a shallow clone that cannot "
                              "decide the answer, exits 2 (REACHABILITY "
                              "UNDECIDABLE) rather than either passing or "
                              "failing it")
    parser.add_argument("--default-branch-ref", default=None,
                         help="the ref --require-reachable measures against "
                              "(default: the first of %s that resolves)"
                              % ", ".join(DEFAULT_BRANCH_REFS))
    args = parser.parse_args(argv)

    if not args.evidence.is_file():
        print("ERROR: %s does not exist -- nothing to verify" % args.evidence)
        return 1
    record = json.loads(args.evidence.read_text(encoding="utf-8"))
    repo_root = None if args.skip_tree_check else args.against_tree
    reachable_in = args.against_tree if args.require_reachable else None

    # Reachability is checked separately from verify()'s flat error list:
    # it is the one check with a third answer (undecidable) that must pick
    # its own exit code rather than collapsing into a plain pass/fail.
    reach_status: str | None = None
    reach_messages: list[str] = []
    if reachable_in is not None:
        reach_status, reach_messages = reachability_status(
            record, reachable_in, args.default_branch_ref)

    errors = verify(record, args.expect_head, repo_root=repo_root)

    if errors or reach_status == UNREACHABLE:
        print("NOT COMMITTABLE: %s" % args.evidence)
        for error in list(errors) + list(reach_messages):
            print("  - %s" % error)
        return 1
    if reach_status == UNDECIDABLE:
        # Every other check passed, but reachability itself could not be
        # decided from this checkout. This must never print or exit the way
        # either COMMITTABLE or NOT COMMITTABLE does -- "cannot tell" is a
        # third answer, not a softened version of either of the other two.
        print("REACHABILITY UNDECIDABLE: %s" % args.evidence)
        for message in reach_messages:
            print("  - %s" % message)
        print("This is NOT a pass -- every other check succeeded, but "
              "identity.git_head's reachability on the default branch could "
              "not be established from this checkout. Re-run against a "
              "checkout with enough history to decide (CI: "
              "actions/checkout with fetch-depth: 0) before treating this "
              "record as citeable.")
        return 2
    if repo_root is None:
        print("NOTE: --skip-tree-check was given; the digested sources were "
              "NOT re-hashed, so this run does not establish that the record "
              "describes any tree.")
        freshness = "tree check SKIPPED"
    else:
        freshness = "digests fresh against %s" % repo_root
    if reachable_in is None:
        print("NOTE: --require-reachable was not given; identity.git_head "
              "was NOT checked for reachability on the default branch, so "
              "this run does not establish that a reader can resolve it.")
        reachability = "reachability NOT checked"
    else:
        reachability = "git_head reachable on %s" % (
            args.default_branch_ref or resolve_default_branch(reachable_in))
    print("COMMITTABLE: %s (schema %s, git_head %s, %s, %s)"
          % (args.evidence, record["schema"], record["identity"]["git_head"],
             freshness, reachability))
    return 0


if __name__ == "__main__":
    sys.exit(main())
