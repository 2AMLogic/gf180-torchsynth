#!/usr/bin/env python3
"""Issue #454: the whole-voice flow's shard (unit) and merge modes.

The 2026-11-18 operator ruling sends whole-voice evidence regeneration to
the Spot fleet's ``repo-script`` job kind, sharded under the sim image's
3600 s cap. These tests pin what makes that sharding sound:

- the planned unit set mirrors the flow exactly -- one unit per committed
  case, the fixed stages, one per negative control, profiles nested as
  declared, and the mutant table is the one source of both the flow's
  controls and the unit plan's ``mut:`` names;
- a shard result never carries git identity (the job tree's HEAD is a
  synthetic throwaway commit) and records its provenance from the
  submitter's environment, never from git;
- the merge refuses -- non-zero exit, nothing written -- on every gap that
  could otherwise pass silently: a missing planned unit (which is also how
  a shard that exited 0 while omitting its declared output surfaces), a
  duplicate unit, a unit that did not PASS, shards disagreeing on tools or
  sources, shard digests not matching the checkout, a shard that wrote git
  identity anyway, a non-shard JSON in the units tree, a payload missing
  its declared keys, or a dirty evidence tree.

The merge tests run against synthetic shard results carrying the real
local source digests: the merge is simulator-free, and its contract is
about the JSON it reads, not the simulations that produced it. One
integration test runs the real CLI for the cheapest unit and checks the
document it emits.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tb"))

from hw_tools import has_iverilog  # noqa: E402

import verify_oneshot_evidence as voe  # noqa: E402
import run_voice as rv  # noqa: E402

SYNTHETIC_IV = "Icarus Verilog version 99.0 (synthetic shard test)"
FAKE_HEAD = "a" * 40
OTHER_HEAD = "b" * 40
RECORD_NAME = "voice-evidence.json"


def pass_case_diagnostic(branch: str, peak: int) -> dict:
    return {
        "branch": branch, "peak_word": peak, "gain_word": 1,
        "frozen_pinned": False, "result": "PASS",
    }


def synthetic_payload(unit: str, specs_by_unit: dict) -> dict:
    """The payload a PASSing unit of this kind would carry."""

    if unit.startswith("case:"):
        # regression's two cases cover both branches and both envelope ends
        # (bypass at the zero peak, divide at the ceiling).
        branch = "bypass" if unit.endswith("below") else "divide"
        peak = 0 if branch == "bypass" else 8388608
        return pass_case_diagnostic(branch, peak)
    if unit.startswith("mut:"):
        spec = specs_by_unit[unit]
        return {"detected": True, "walk": spec["walk"], "case": spec["case"]}
    if unit == rv.UNIT_HASH:
        return {"ok": True, "artifact_hashes": {"run0_status.txt": "0" * 64}}
    return {"ok": True}


def write_synthetic_units(units_dir: Path, profile: str = "regression",
                          mutate=None) -> Path:
    """Every planned unit for ``profile``, PASS and self-consistent, with
    the real local source digests and a synthetic iverilog line.

    ``mutate(doc, unit)`` tweaks individual documents before they are
    written -- each refusal test pokes exactly one hole through it.
    """

    units = rv.voice_planned_units(profile, {})
    digests = rv.voice_source_digests()
    specs_by_unit = {rv.voice_unit_mut(s["label"]): s
                     for s in rv.voice_mutant_specs()}
    units_dir.mkdir(parents=True, exist_ok=True)
    for unit in units:
        doc = {
            "schema": rv.VOICE_SHARD_SCHEMA,
            "profile": profile,
            "unit": unit,
            "result": "PASS",
            "payload": synthetic_payload(unit, specs_by_unit),
            "source_commit": None,
            "rs_src_sha256": None,
        }
        doc.update(digests)
        doc["iverilog"] = SYNTHETIC_IV
        if mutate is not None:
            mutate(doc, unit)
        (units_dir / (unit.replace(":", "_") + ".json")).write_text(
            json.dumps(doc, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    return units_dir


class MutantSpecsAreTheOneControlTable(unittest.TestCase):
    """The flow's negative controls and the unit plan's ``mut:`` names come
    from one table, so neither can drift from the other."""

    def test_thirteen_unique_labels(self):
        specs = rv.voice_mutant_specs()
        labels = [spec["label"] for spec in specs]
        self.assertEqual(len(labels), 13)
        self.assertEqual(len(set(labels)), 13)

    def test_every_label_is_a_planned_unit(self):
        planned = set(rv.voice_planned_units("full", {}))
        for spec in rv.voice_mutant_specs():
            self.assertIn(rv.voice_unit_mut(spec["label"]), planned)

    def test_binding_controls_run_on_the_binding_case_at_the_cap(self):
        for spec in rv.voice_mutant_specs():
            if spec["label"] in rv.VOICE_BINDING_CONTROLS:
                self.assertEqual(spec["case"], rv.VOICE_MUTATION_BINDING_CASE)
                self.assertEqual(spec["walk"], rv.VOICE_MUTATION_WALK_CAP)
                self.assertIsNone(spec["localize"])

    def test_missing_sample_localizes_the_declared_drop(self):
        spec = next(s for s in rv.voice_mutant_specs()
                    if s["label"] == "missing-sample")
        self.assertEqual(spec["case"], rv.VOICE_MUTATION_DIVIDE_CASE)
        self.assertEqual(spec["localize"],
                         ("link.replay_input[pass1]", rv.VOICE_DROP_SAMPLE))

    def test_prefix_controls_use_the_declared_cap(self):
        for spec in rv.voice_mutant_specs():
            if spec["walk"] != rv.AUDIO_SAMPLES:
                self.assertEqual(spec["walk"], rv.VOICE_MUTATION_WALK_CAP)

    def test_normalization_controls_are_full_length_on_the_declared_cases(self):
        for spec in rv.voice_mutant_specs():
            if not spec["label"].startswith("normalization-"):
                continue
            self.assertEqual(spec["walk"], rv.AUDIO_SAMPLES)
            expected_case = (rv.VOICE_MUTATION_BYPASS_CASE
                             if spec["label"] == "normalization-always-on"
                             else rv.VOICE_MUTATION_DIVIDE_CASE)
            self.assertEqual(spec["case"], expected_case)


class PlannedUnitsMirrorTheFlow(unittest.TestCase):
    def test_regression_unit_set_is_exact_and_in_flow_order(self):
        units = rv.voice_planned_units("regression", {})
        self.assertEqual(units, [
            "case:normalization:below",
            "case:voice:divide-distinct-levels",
            rv.UNIT_BINDING_BASELINE, rv.UNIT_HASH, rv.UNIT_REPLAY,
        ] + [rv.voice_unit_mut(spec["label"])
             for spec in rv.voice_mutant_specs()])

    def test_profiles_are_nested_on_units(self):
        # `full` needs the real receipt's case ids; regression ⊂ directed is
        # checkable from constants alone.
        regression = set(rv.voice_planned_units("regression", {}))
        directed = set(rv.voice_planned_units("directed", {}))
        self.assertTrue(regression < directed)

    def test_directed_adds_exactly_the_three_directed_vectors(self):
        regression = rv.voice_planned_units("regression", {})
        directed = rv.voice_planned_units("directed", {})
        added = [u for u in directed if u not in regression]
        self.assertEqual(
            added, ["case:" + cid for cid in
                    ("special:silence", "special:near-silence",
                     "special:stress")])

    def test_unit_payload_keys_cover_every_planned_unit(self):
        for unit in rv.voice_planned_units("directed", {}):
            keys = rv.voice_unit_payload_keys(unit)
            self.assertTrue(keys, unit)
            if unit.startswith("case:"):
                self.assertEqual(
                    keys, ("branch", "frozen_pinned", "gain_word",
                           "peak_word", "result"))
            elif unit.startswith("mut:"):
                self.assertEqual(keys, ("case", "detected", "walk"))
            elif unit == rv.UNIT_HASH:
                self.assertEqual(keys, ("artifact_hashes", "ok"))
            else:
                self.assertEqual(keys, ("ok",))


class ShardProvenanceComesFromTheEnvironment(unittest.TestCase):
    def test_recorded_from_the_submitter_env(self):
        env = {rv.RS_SOURCE_COMMIT_ENV: "c" * 40,
               rv.RS_SRC_SHA256_ENV: "d" * 64}
        with unittest.mock.patch.dict(os.environ, env):
            provenance = rv.voice_shard_provenance()
        self.assertEqual(provenance["source_commit"], "c" * 40)
        self.assertEqual(provenance["rs_src_sha256"], "d" * 64)

    def test_empty_and_unset_are_none(self):
        env = {rv.RS_SOURCE_COMMIT_ENV: "", rv.RS_SRC_SHA256_ENV: ""}
        with unittest.mock.patch.dict(os.environ, env):
            provenance = rv.voice_shard_provenance()
        self.assertIsNone(provenance["source_commit"])
        self.assertIsNone(provenance["rs_src_sha256"])
        for key in (rv.RS_SOURCE_COMMIT_ENV, rv.RS_SRC_SHA256_ENV):
            os.environ.pop(key, None)
        provenance = rv.voice_shard_provenance()
        self.assertIsNone(provenance["source_commit"])
        self.assertIsNone(provenance["rs_src_sha256"])


class ShardIdentityOmitsGit(unittest.TestCase):
    """The job tree's HEAD is synthetic (RS_SYNTHETIC_HEAD=1); no shard may
    write ``git_head``/``git_tree_dirty`` -- the merge owns identity."""

    def test_full_identity_carries_the_git_fields(self):
        identity = rv.voice_tool_identity(git_identity=True)
        self.assertIn("git_head", identity)
        self.assertIn("git_tree_dirty", identity)
        self.assertIn("rtl_sha256", identity)
        self.assertIn("fixed_vector_sha256", identity)
        self.assertIn("iverilog", identity)

    def test_shard_identity_omits_the_git_fields(self):
        identity = rv.voice_tool_identity(git_identity=False)
        self.assertNotIn("git_head", identity)
        self.assertNotIn("git_tree_dirty", identity)
        self.assertEqual(
            sorted(k for k in identity if k.startswith("git")), [])
        self.assertEqual(len(identity["rtl_sha256"]), 13)
        self.assertEqual(len(identity["fixed_vector_sha256"]), 2)
        self.assertTrue(identity["iverilog"])

    @unittest.skipUnless(has_iverilog(), "iverilog is not installed")
    def test_shard_cli_result_carries_no_git_identity(self):
        with tempfile.TemporaryDirectory(prefix="voice-shard-") as tmp:
            work = Path(tmp) / "work"
            units_out = Path(tmp) / "units"
            with contextlib.redirect_stdout(io.StringIO()):
                status = rv.main([
                    "--profile", "regression", "--unit",
                    rv.UNIT_BINDING_BASELINE, "--workdir", str(work),
                    "--units-out", str(units_out),
                ])
            self.assertEqual(status, 0)
            docs = list(units_out.glob("*.json"))
            self.assertEqual(len(docs), 1)
            doc = json.loads(docs[0].read_text(encoding="utf-8"))
        self.assertEqual(doc["schema"], rv.VOICE_SHARD_SCHEMA)
        self.assertEqual(doc["unit"], rv.UNIT_BINDING_BASELINE)
        self.assertEqual(doc["result"], "PASS")
        self.assertNotIn("git_head", doc)
        self.assertNotIn("git_tree_dirty", doc)
        self.assertIn("iverilog", doc)
        self.assertIn("rtl_sha256", doc)
        self.assertEqual(doc["source_commit"],
                         os.environ.get(rv.RS_SOURCE_COMMIT_ENV) or None)
        self.assertEqual(doc["payload"], {"ok": True})


class MergeRefusals(unittest.TestCase):
    """The merge is the soundness gate over shard results; every gap must
    refuse with a non-zero exit and nothing written."""

    def merge(self, units_dir: Path, workdir: Path) -> int:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = rv.voice_merge("regression", units_dir, workdir,
                                    git_head=FAKE_HEAD, tree_dirty=False)
        self.stdout = stdout.getvalue()
        return status

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="voice-merge-")
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.workdir = self.base / "out"
        self.units = write_synthetic_units(self.base / "units")

    def assertRefused(self, status):
        self.assertNotEqual(status, 0)
        self.assertFalse((self.workdir / RECORD_NAME).exists())

    def test_happy_path_assembles_a_committable_record(self):
        self.assertEqual(self.merge(self.units, self.workdir), 0)
        record = json.loads(
            (self.workdir / RECORD_NAME).read_text(encoding="utf-8"))
        self.assertEqual(record["schema"],
                         "gf180-torchsynth/oneshot-whole-voice-evidence-v1")
        self.assertEqual(record["profile"], "regression")
        self.assertEqual(record["result"], "PASS")
        self.assertEqual(record["cases"],
                         ["normalization:below",
                          "voice:divide-distinct-levels"])
        self.assertEqual(record["case_count"], 2)
        self.assertEqual(len(record["mutations"]), 13)
        self.assertTrue(all(m["verdict"] == "DETECTED"
                            for m in record["mutations"].values()))
        self.assertEqual(record["binding_baseline"], "PASS")
        self.assertEqual(record["branch_coverage"],
                         {"divide": True, "bypass": True})
        self.assertEqual(record["identity"]["git_head"], FAKE_HEAD)
        self.assertIs(record["identity"]["git_tree_dirty"], False)
        # The simulator line is the SHARDS' -- they are the runs that
        # produced the captures -- not whatever this merge host has.
        self.assertEqual(record["identity"]["iverilog"], SYNTHETIC_IV)
        self.assertEqual(record["identity"]["rtl_sha256"],
                         rv.voice_source_digests()["rtl_sha256"])
        self.assertEqual(
            voe.verify(record, expect_head=None, repo_root=None), [])

    def test_missing_unit_refuses_and_nothing_is_written(self):
        victim = "mut:wrong-noise"
        (self.units / (victim.replace(":", "_") + ".json")).unlink()
        self.assertRefused(self.merge(self.units, self.workdir))
        self.assertIn(victim, self.stdout)

    def test_a_shard_that_omitted_its_declared_output_refuses(self):
        # Same refusal from the merge's side: the submitter fails such a
        # shard (exit 3, missing output); the merge must not assemble
        # around the hole either.
        victim = "case:normalization:below"
        (self.units / (victim.replace(":", "_") + ".json")).unlink()
        self.assertRefused(self.merge(self.units, self.workdir))
        self.assertIn(victim, self.stdout)

    def test_duplicate_unit_refuses(self):
        first = sorted(self.units.glob("*.json"))[0]
        doc = json.loads(first.read_text(encoding="utf-8"))
        twin = self.units / (first.stem + "-twin.json")
        twin.write_text(json.dumps(doc), encoding="utf-8")
        self.assertRefused(self.merge(self.units, self.workdir))
        self.assertIn(doc["unit"], self.stdout)

    def test_differing_rtl_sha256_refuses(self):
        def mutate(doc, unit):
            if unit == "mut:missing-sample":
                doc["rtl_sha256"] = {
                    **doc["rtl_sha256"],
                    "one_shot_voice_top.sv": "e" * 64,
                }
        self.assertRefused(
            self.merge(write_synthetic_units(self.base / "units2", mutate=mutate),
                       self.workdir))

    def test_differing_iverilog_refuses(self):
        def mutate(doc, unit):
            if unit == "mut:wrong-noise":
                doc["iverilog"] = "Icarus Verilog version 12.0 (other shard)"
        self.assertRefused(
            self.merge(write_synthetic_units(self.base / "units2", mutate=mutate),
                       self.workdir))

    def test_differing_source_commit_refuses(self):
        def mutate(doc, unit):
            if unit == "hash":
                doc["source_commit"] = OTHER_HEAD
        self.assertRefused(
            self.merge(write_synthetic_units(self.base / "units2", mutate=mutate),
                       self.workdir))

    def test_differing_rs_src_sha256_refuses(self):
        def mutate(doc, unit):
            if unit == "replay":
                doc["rs_src_sha256"] = "d" * 64
        self.assertRefused(
            self.merge(write_synthetic_units(self.base / "units2", mutate=mutate),
                       self.workdir))

    def test_failed_unit_refuses(self):
        def mutate(doc, unit):
            if unit == "binding-baseline":
                doc["result"] = "FAIL"
                doc["payload"] = {"ok": False}
        self.assertRefused(
            self.merge(write_synthetic_units(self.base / "units2", mutate=mutate),
                       self.workdir))
        self.assertIn("binding-baseline", self.stdout)

    def test_malformed_payload_refuses(self):
        def mutate(doc, unit):
            if unit == "hash":
                del doc["payload"]["artifact_hashes"]
        self.assertRefused(
            self.merge(write_synthetic_units(self.base / "units2", mutate=mutate),
                       self.workdir))

    def test_unexpected_unit_refuses(self):
        extra = {
            "schema": rv.VOICE_SHARD_SCHEMA, "profile": "regression",
            "unit": "case:not-planned", "result": "PASS",
            "payload": pass_case_diagnostic("divide", 1),
            "iverilog": SYNTHETIC_IV, "source_commit": None,
            "rs_src_sha256": None,
        }
        extra.update(rv.voice_source_digests())
        (self.units / "case_not-planned.json").write_text(
            json.dumps(extra, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        self.assertRefused(self.merge(self.units, self.workdir))
        self.assertIn("case:not-planned", self.stdout)

    def test_profile_mismatch_refuses(self):
        def mutate(doc, unit):
            if unit == "replay":
                doc["profile"] = "directed"
        self.assertRefused(
            self.merge(write_synthetic_units(self.base / "units2", mutate=mutate),
                       self.workdir))

    def test_non_shard_json_refuses(self):
        (self.units / "status.json").write_text("{}\n", encoding="utf-8")
        self.assertRefused(self.merge(self.units, self.workdir))

    def test_shard_doc_carrying_git_identity_refuses(self):
        def mutate(doc, unit):
            if unit == "hash":
                doc["git_head"] = OTHER_HEAD
        self.assertRefused(
            self.merge(write_synthetic_units(self.base / "units2", mutate=mutate),
                       self.workdir))
        self.assertIn("git_head", self.stdout)

    def test_digests_not_matching_this_checkout_refuse(self):
        # Consistent across the shards, but not this tree's bytes: the
        # shards and the merge must run the same committed sources.
        def mutate(doc, unit):
            doc["rtl_sha256"] = {
                **doc["rtl_sha256"],
                "one_shot_voice_top.sv": "e" * 64,
            }
        self.assertRefused(
            self.merge(write_synthetic_units(self.base / "units2", mutate=mutate),
                       self.workdir))
        self.assertIn("one_shot_voice_top.sv", self.stdout)

    def test_missing_digest_table_refuses(self):
        def mutate(doc, unit):
            if unit == "hash":
                del doc["rtl_sha256"]
        self.assertRefused(
            self.merge(write_synthetic_units(self.base / "units2", mutate=mutate),
                       self.workdir))

    def test_dirty_tree_refuses(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = rv.voice_merge("regression", self.units, self.workdir,
                                    git_head=FAKE_HEAD, tree_dirty=True)
        self.assertNotEqual(status, 0)
        self.assertFalse((self.workdir / RECORD_NAME).exists())
        self.assertIn("dirty", stdout.getvalue())

    def test_unresolvable_head_refuses(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = rv.voice_merge("regression", self.units, self.workdir,
                                    git_head="unavailable", tree_dirty=False)
        self.assertNotEqual(status, 0)
        self.assertFalse((self.workdir / RECORD_NAME).exists())

    def test_source_commit_citing_another_commit_refuses(self):
        def mutate(doc, unit):
            doc["source_commit"] = OTHER_HEAD
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = rv.voice_merge(
                "regression",
                write_synthetic_units(self.base / "units2", mutate=mutate),
                self.workdir, git_head=FAKE_HEAD, tree_dirty=False)
        self.assertNotEqual(status, 0)
        self.assertFalse((self.workdir / RECORD_NAME).exists())
        self.assertIn(OTHER_HEAD, stdout.getvalue())

    def test_source_commit_matching_head_is_accepted(self):
        def mutate(doc, unit):
            doc["source_commit"] = FAKE_HEAD
        self.assertEqual(
            self.merge(
                write_synthetic_units(self.base / "units2", mutate=mutate),
                self.workdir),
            0)
        self.assertTrue((self.workdir / RECORD_NAME).exists())

    def test_merge_into_a_missing_units_dir_refuses_as_all_missing(self):
        self.assertRefused(self.merge(self.base / "does-not-exist",
                                      self.workdir))
        self.assertIn("missing", self.stdout)


class TreeGuardsFailClosed(unittest.TestCase):
    """The merge's real git detectors: clean tree / dirty tree / no git at
    all, and a resolvable vs unresolvable HEAD."""

    def git(self, repo: Path, *args):
        return subprocess.run(
            ["git", "-C", str(repo)] + list(args), check=True,
            capture_output=True, text=True)

    def repo(self, commits=False) -> Path:
        repo = Path(tempfile.mkdtemp(prefix="voice-tree-"))
        self.addCleanup(subprocess.run,
                        ["rm", "-rf", str(repo)])
        self.git(repo, "init", "-q")
        self.git(repo, "config", "user.email", "t@example.invalid")
        self.git(repo, "config", "user.name", "t")
        (repo / "tb").mkdir()
        (repo / "tb" / "keep.sv").write_text("module m; endmodule\n",
                                             encoding="utf-8")
        if commits:
            self.git(repo, "add", "tb")
            self.git(repo, "commit", "-qm", "clean")
        return repo

    def test_committed_tree_is_clean_and_head_resolves(self):
        repo = self.repo(commits=True)
        self.assertFalse(rv.voice_tree_dirty(repo))
        head = rv.voice_git_head(repo)
        self.assertEqual(len(head), 40)
        self.assertTrue(all(c in "0123456789abcdef" for c in head))

    def test_uncommitted_change_under_the_scope_is_dirty(self):
        repo = self.repo(commits=True)
        (repo / "tb" / "dirty.sv").write_text("module d; endmodule\n",
                                              encoding="utf-8")
        self.assertTrue(rv.voice_tree_dirty(repo))

    def test_change_outside_the_scope_is_not_dirty(self):
        repo = self.repo(commits=True)
        (repo / "UNRELATED.md").write_text("x\n", encoding="utf-8")
        self.assertFalse(rv.voice_tree_dirty(repo))

    def test_a_repo_with_no_commits_has_no_head(self):
        repo = self.repo(commits=False)
        self.assertIsNone(rv.voice_git_head(repo))


if __name__ == "__main__":
    unittest.main()
