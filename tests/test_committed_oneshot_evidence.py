"""The committed issue-#79 evidence records must stay committable (#79).

``sim/evidence/oneshot-whole-voice-regression-v1.json`` and
``sim/evidence/oneshot-tail-chain-regression-v1.json`` are evidence, not
just documentation: each is a real ``tb/run_oneshot.py`` / ``tb/run_voice.py``
output, verified committable by ``tools/verify_oneshot_evidence.py`` before
being added (see ``spec/ONESHOT-E2E.md`` -> "Evidence identity"). This suite
re-runs that same mechanical check against the files as committed, so a
future edit that quietly turns a citeable record into a non-citeable one
(a dirtied ``git_tree_dirty``, a flipped ``result``, a reintroduced float
tolerance) fails CI instead of going unnoticed. No simulator is invoked
here -- ``verify_oneshot_evidence`` only reads already-produced JSON and
hashes the source files that JSON names.

It also closes the *staleness* hole, which the fields inside a record cannot
close by themselves: a record pins a sha256 for every RTL source and frozen
vector it was produced from, but editing one of those files leaves the record
untouched -- ``result`` stays ``PASS``, ``git_tree_dirty`` stays ``false``,
and the cited ``git_head`` keeps naming a commit whose RTL is no longer the
RTL in the tree. So an engine could be changed with its bit-identity proof
silently inherited from the previous version, which is exactly the claim
``CLAUDE.md`` forbids ("never claim ... without a committed evidence record
that actually establishes it"). ``test_each_record_is_fresh_against_the_tree``
makes that case fail CI, and names the regeneration command; the mutation
check below proves the gate actually bites.

A third hole opened once the whole-voice flow grew more than one profile
(``regression`` < ``directed`` < ``full``, nested, #79): the filename is what a
reader greps and what ``spec/ONESHOT-E2E.md``'s acceptance-criteria ledger
cites, while ``profile`` inside the JSON is what the flow actually planned.
Nothing tied the two together, so a ``regression`` run filed as
``oneshot-whole-voice-full-v1.json`` would have read as the strictly stronger
proof while containing the weaker one -- and every other check here would
still have passed it. ``test_each_record_filename_names_the_profile_it_actually_ran``
closes that, and the record's ``profile`` is additionally required to be one
its own flow's CLI accepts, so the regeneration command printed on a stale
record is always a command that runs. Both are paired with a check that they
discriminate.
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tb"))

import verify_oneshot_evidence as voe  # noqa: E402
import run_oneshot  # noqa: E402
import run_voice  # noqa: E402
from run_tb import EVIDENCE_TREE_SCOPE  # noqa: E402

EVIDENCE_DIR = ROOT / "sim/evidence"

#: How a stale record is made citeable again -- regenerate, never re-pin.
#: Templated on the record's **own** ``profile`` rather than hardcoding
#: ``regression``: the whole-voice flow now has three profiles, and a message
#: that named the wrong one would send a reader to regenerate a weaker record
#: than the stale file they are replacing.
REGENERATE = {
    "gf180-torchsynth/oneshot-tail-chain-evidence-v1": (
        "python3 tb/run_oneshot.py --profile %s --workdir <dir>"
    ),
    "gf180-torchsynth/oneshot-whole-voice-evidence-v1": (
        "python3 tb/run_voice.py --profile %s --workdir <dir>"
    ),
}


def regenerate_command(record: dict) -> str:
    """The exact command that reproduces ``record``, profile included."""

    template = REGENERATE.get(record.get("schema"))
    if template is None:
        return "the lane's flow"
    return template % (record.get("profile") or "regression")


def profile_from_filename(name: str) -> str:
    """The profile a record's filename claims.

    Committed records are named ``oneshot-<lane>-<profile>-v1.json``, so the
    profile is the last dash-separated token of the stem.
    """

    return name[len("oneshot-"):-len("-v1.json")].rsplit("-", 1)[-1]


def filename_profile_mismatch(directory: Path, name: str):
    """``None`` when a record's filename agrees with the run inside it.

    Otherwise a human-readable diagnosis. These are two independent claims --
    the filename is what a reader greps and what ``spec/ONESHOT-E2E.md``'s
    acceptance-criteria ledger cites, while ``profile`` is what the flow
    actually planned -- and only the file's own contents can settle which is
    right, so a disagreement is refused rather than resolved here.
    """

    declared = profile_from_filename(name)
    actual = json.loads((directory / name).read_text()).get("profile")
    if declared == actual:
        return None
    return (
        "%s names profile %r but the record inside ran profile %r; rename "
        "the file or regenerate the record -- never let the filename "
        "overstate the run" % (name, declared, actual)
    )


def flow_accepts_profile(flow, profile: str) -> bool:
    """Whether ``flow``'s real CLI accepts ``--profile <profile>``.

    Probed through ``argparse``, which validates ``choices`` before ``--help``
    exits ``0`` and before either flow looks for a simulator, so this cannot
    drift from the flows' own declared choices the way a copied tuple would.
    """

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer), \
                contextlib.redirect_stderr(buffer):
            flow.main(["--profile", profile, "--help"])
    except SystemExit as exit_code:
        return exit_code.code == 0
    return False

#: The flow that owns each schema -- the one whose ``--profile`` choices a
#: committed record's ``profile`` field must be drawn from, and whose command
#: ``REGENERATE`` above names.
FLOW_FOR_SCHEMA = {
    "gf180-torchsynth/oneshot-tail-chain-evidence-v1": run_oneshot,
    "gf180-torchsynth/oneshot-whole-voice-evidence-v1": run_voice,
}

#: Each committed record, its declared schema and the lane it certifies.
COMMITTED_RECORDS = {
    "oneshot-whole-voice-regression-v1.json": (
        "gf180-torchsynth/oneshot-whole-voice-evidence-v1"
    ),
    "oneshot-tail-chain-regression-v1.json": (
        "gf180-torchsynth/oneshot-tail-chain-evidence-v1"
    ),
}


class CommittedOneshotEvidenceTest(unittest.TestCase):
    def test_every_declared_record_exists(self):
        for name in COMMITTED_RECORDS:
            self.assertTrue(
                (EVIDENCE_DIR / name).is_file(),
                "%s is declared here but missing from %s" % (name, EVIDENCE_DIR),
            )

    def test_no_undeclared_record_is_silently_ignored(self):
        # A record added to sim/evidence/ without an entry here would pass
        # this suite silently -- refuse that instead.
        on_disk = {p.name for p in EVIDENCE_DIR.glob("*.json")}
        self.assertEqual(on_disk, set(COMMITTED_RECORDS))

    def test_each_record_filename_names_the_profile_it_actually_ran(self):
        # The filename is what a reader greps and what spec/ONESHOT-E2E.md's
        # acceptance-criteria ledger cites; `profile` inside is what the flow
        # actually planned.
        for name in COMMITTED_RECORDS:
            with self.subTest(record=name):
                self.assertIsNone(filename_profile_mismatch(EVIDENCE_DIR, name))

    def test_a_filename_that_overstates_its_profile_is_detected(self):
        # Proof the check above is not vacuous, in this suite's own style: a
        # mirror directory, one deliberate mistake. The profiles are nested
        # (regression < directed < full), so the mistake that matters is
        # filing the weaker run under the stronger name -- it would read as
        # the strictly stronger proof while containing the weaker one.
        with tempfile.TemporaryDirectory(prefix="evidence-mirror-") as tmp:
            mirror = Path(tmp)
            source = EVIDENCE_DIR / "oneshot-whole-voice-regression-v1.json"
            overstated = "oneshot-whole-voice-full-v1.json"
            (mirror / overstated).write_text(source.read_text(), "utf-8")
            problem = filename_profile_mismatch(mirror, overstated)
        self.assertIsNotNone(
            problem,
            "a regression record filed under the `full` name was accepted",
        )
        self.assertIn("'full'", problem)
        self.assertIn("'regression'", problem)

    def test_each_record_profile_is_one_its_own_flow_accepts(self):
        # A profile name the owning flow does not accept cannot be
        # regenerated by anyone, so the record would be unreproducible even
        # though every digest in it still matched -- and the regeneration
        # command this suite prints on a stale record would be a command that
        # does not run.
        for name, schema in COMMITTED_RECORDS.items():
            with self.subTest(record=name):
                flow = FLOW_FOR_SCHEMA[schema]
                record = json.loads((EVIDENCE_DIR / name).read_text())
                profile = record.get("profile")
                self.assertTrue(
                    flow_accepts_profile(flow, profile),
                    "%s records profile %r, which %s does not accept"
                    % (name, profile, flow.__name__),
                )
                self.assertIn(profile, regenerate_command(record))

    def test_an_unknown_profile_is_rejected_by_both_flows(self):
        # Proof the probe above actually discriminates.
        for flow in FLOW_FOR_SCHEMA.values():
            with self.subTest(flow=flow.__name__):
                self.assertFalse(flow_accepts_profile(flow, "not-a-profile"))
        # ...and that it is reading each flow's real, differing choices: the
        # whole-voice lane gained `directed` (#79); the tail chain has not.
        self.assertTrue(flow_accepts_profile(run_voice, "directed"))
        self.assertFalse(flow_accepts_profile(run_oneshot, "directed"))

    def test_each_record_is_committable(self):
        for name, expected_schema in COMMITTED_RECORDS.items():
            with self.subTest(record=name):
                record = json.loads((EVIDENCE_DIR / name).read_text())
                errors = voe.verify(record, expect_head=None)
                self.assertEqual(
                    errors, [],
                    "%s is no longer committable: %s" % (name, errors),
                )
                self.assertEqual(record.get("schema"), expected_schema)
                self.assertEqual(record.get("result"), "PASS")
                self.assertIsNone(record.get("float_tolerance"))
                self.assertIs(record["identity"]["git_tree_dirty"], False)
                git_head = record["identity"]["git_head"]
                self.assertEqual(len(git_head), 40)

    def test_each_record_is_fresh_against_the_tree(self):
        # The substantive gate: every RTL source and frozen vector the record
        # pins must still be byte-identical here. A failure means the proof
        # was invalidated by a later edit -- regenerate it (the command is in
        # the message), never re-pin the digest by hand.
        for name in COMMITTED_RECORDS:
            with self.subTest(record=name):
                record = json.loads((EVIDENCE_DIR / name).read_text())
                errors = voe.stale_sources(record, ROOT)
                self.assertEqual(
                    errors, [],
                    "%s no longer describes this tree: %s\nRegenerate it on a "
                    "clean, already-landed commit with: %s" % (
                        name, errors,
                        regenerate_command(record),
                    ),
                )

    def test_every_digested_source_resolves_to_exactly_one_file(self):
        # Freshness is only meaningful if every digested name maps to one
        # real file: an unresolvable name would otherwise be a silent skip.
        for name in COMMITTED_RECORDS:
            record = json.loads((EVIDENCE_DIR / name).read_text())
            for block in voe.DIGEST_BLOCKS:
                for source in record["identity"][block]:
                    with self.subTest(record=name, source=source):
                        self.assertEqual(
                            len(voe.resolve_source(source, ROOT)), 1,
                            "%s names %s, which does not resolve to exactly "
                            "one file under %s"
                            % (name, source, voe.SOURCE_ROOTS),
                        )

    def test_a_changed_source_is_detected(self):
        # A control that cannot fail is not a control: prove the freshness
        # check bites on a one-byte change, in a throwaway tree (never by
        # touching the real sources).
        record = json.loads(
            (EVIDENCE_DIR / "oneshot-tail-chain-regression-v1.json").read_text()
        )
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp)
            for block in voe.DIGEST_BLOCKS:
                for source in record["identity"][block]:
                    original = voe.resolve_source(source, ROOT)[0]
                    mirror = fake / original.relative_to(ROOT)
                    mirror.parent.mkdir(parents=True, exist_ok=True)
                    mirror.write_bytes(original.read_bytes())
            self.assertEqual(voe.stale_sources(record, fake), [],
                             "the mirrored tree should start out fresh")

            victim = voe.resolve_source("audio_mix_engine.sv", fake)[0]
            victim.write_bytes(victim.read_bytes() + b"\n// drift\n")
            errors = voe.stale_sources(record, fake)
            self.assertTrue(
                any("audio_mix_engine.sv" in e and "changed" in e
                    for e in errors),
                "a changed RTL source went undetected: %s" % errors,
            )
            # And the full committability check must fail too, not just the
            # freshness helper on its own.
            self.assertNotEqual(
                voe.verify(record, expect_head=None, repo_root=fake), [],
                "verify() ignored a changed source",
            )
            self.assertEqual(
                voe.verify(record, expect_head=None), [],
                "verify() without a repo root must stay a self-consistency "
                "check only",
            )

    def test_a_missing_source_is_detected(self):
        # The other way a record stops describing a tree: the file is gone
        # (renamed, moved out of the declared roots). Must error, not skip.
        record = json.loads(
            (EVIDENCE_DIR / "oneshot-tail-chain-regression-v1.json").read_text()
        )
        with tempfile.TemporaryDirectory() as tmp:
            errors = voe.stale_sources(record, Path(tmp))
            self.assertTrue(errors, "an empty tree was reported as fresh")
            self.assertTrue(
                all("does not exist" in e for e in errors),
                "unexpected failure reasons for an empty tree: %s" % errors,
            )

    def test_every_digested_source_is_inside_the_dirty_scope(self):
        # Ties the two halves of evidence identity together: a record's
        # ``git_tree_dirty`` flag is only trustworthy if every input it
        # digests is one of the paths the flows check for uncommitted
        # changes. Adding a digested input outside EVIDENCE_TREE_SCOPE must
        # fail here rather than silently reopening that gap (the gap that
        # left ``sim/reference`` -- home of the frozen receipt -- unwatched).
        scope = tuple(Path(p) for p in EVIDENCE_TREE_SCOPE)
        for name in COMMITTED_RECORDS:
            record = json.loads((EVIDENCE_DIR / name).read_text())
            for block in voe.DIGEST_BLOCKS:
                for source in record["identity"][block]:
                    relative = voe.resolve_source(source, ROOT)[0]
                    relative = relative.relative_to(ROOT)
                    with self.subTest(record=name, source=str(relative)):
                        self.assertTrue(
                            any(relative.is_relative_to(prefix)
                                for prefix in scope),
                            "%s is digested by %s but lies outside the "
                            "evidence dirty scope %s"
                            % (relative, name, EVIDENCE_TREE_SCOPE),
                        )


if __name__ == "__main__":
    unittest.main()
