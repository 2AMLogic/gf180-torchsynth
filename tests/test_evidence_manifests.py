"""Tests for tools/check_artifact_manifests.py and tools/store_evidence.py (issue #409).

No broker is contacted: ``klt`` is faked. ``check`` runs against throwaway git repos.
"""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


chk = load("check_artifact_manifests")
store = load("store_evidence")

GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}


def entry(data=b"x", name="run1/out.json", **over):
    sha = hashlib.sha256(data).hexdigest()
    e = {"sha256": sha, "size": len(data), "kind": "evidence",
         "producer": {"tool": "klt", "version": "0.7.1", "loom.repo": "o/r", "commit": None},
         "uri": f"artifact:evidence/o__r/{sha[:2]}/{sha}", "name": name}
    e.update(over)
    return e


def manifest(*entries):
    return {"schema_version": 1, "entries": list(entries)}


class Schema(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(chk.manifest_problems(manifest(entry())), [])

    def test_rejections(self):
        e = entry()
        cases = {
            "must start with 'artifact:'": {**e, "uri": "s3://b/evidence/o__r/x/" + e["sha256"]},
            "does not end in": {**e, "uri": "artifact:evidence/o__r/aa/" + "a" * 64},
            "unknown field": {**e, "extra": 1},
            "missing": {k: v for k, v in e.items() if k != "kind"},
            "name must be": {**e, "name": "../escape"},
            "name must be ": {**e, "name": "/abs"},
            "size must be": {**e, "size": 0},
        }
        for needle, bad in cases.items():
            with self.subTest(needle):
                self.assertTrue(any(needle in p for p in chk.manifest_problems(manifest(bad))))

    def test_duplicate_names(self):
        probs = chk.manifest_problems(manifest(entry(b"a"), entry(b"b")))
        self.assertTrue(any("duplicate name" in p for p in probs))


class Check(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.root = Path(td.name)
        self.git("init", "-q", "-b", "main")
        self.write("README.md", "r")
        self.commit()

    def git(self, *a):
        subprocess.run(["git", "-C", str(self.root), *a], env=GIT_ENV, check=True,
                       capture_output=True)

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", "c")

    def add(self, m, name="b-r1.manifest.json"):
        self.write(f"manifests/artifacts/{name}", m if isinstance(m, str) else json.dumps(m))
        self.commit()

    def run_check(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = chk.main(["check", "--repo", str(self.root), "--tree", "HEAD"])
        return rc, out.getvalue(), err.getvalue()

    def test_empty_ok_and_says_not_hash_verified(self):
        rc, out, _ = self.run_check()
        self.assertEqual(rc, 0)
        self.assertIn("not hash-verified", out)

    def test_valid_manifest_passes(self):
        self.add(manifest(entry()))
        rc, out, err = self.run_check()
        self.assertEqual(rc, 0, err)
        self.assertIn("1 manifest(s), 1 entry", out)

    def test_tracked_bytes_fail(self):
        self.write("run1/out.json", "x")
        self.add(manifest(entry()))
        rc, _, err = self.run_check()
        self.assertEqual(rc, 1)
        self.assertIn("no blob fallback", err)

    def test_s3_and_arn_refused(self):
        for s in ("s3://bucket/key", "arn:aws:s3:::bucket"):
            with self.subTest(s):
                self.add(json.dumps(manifest(entry())).replace("run1", s))
                rc, _, err = self.run_check()
                self.assertEqual(rc, 1)
                self.assertIn("storage location", err)

    def test_stray_file_and_bad_json_fail(self):
        self.add("{not json")
        rc, _, err = self.run_check()
        self.assertEqual(rc, 1)
        self.assertIn("not valid JSON", err)

    def test_stray_file(self):
        self.write("manifests/artifacts/blob.bin", "x")
        self.commit()
        rc, _, err = self.run_check()
        self.assertEqual(rc, 1)
        self.assertIn("only <bench>-<run-id>.manifest.json", err)

    def test_unavailable_revision_is_exit_2(self):
        with contextlib.redirect_stderr(io.StringIO()):
            rc = chk.main(["check", "--repo", str(self.root), "--tree", "nope"])
        self.assertEqual(rc, 2)

    def test_repo_head_passes(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = chk.main(["check", "--repo", str(ROOT), "--tree", "HEAD"])
        self.assertEqual(rc, 0)

    def test_gitignored_scratch(self):
        p = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", "sim/stage/x/out.bin"])
        self.assertEqual(p.returncode, 0)

    def test_manifests_not_gitignored(self):
        p = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q",
                            "manifests/artifacts/b-r1.manifest.json"])
        self.assertEqual(p.returncode, 1)


class Store(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.root = Path(td.name) / "repo"
        self.root.mkdir()
        self.scratch = Path(td.name) / "scratch"
        (self.scratch / "sub").mkdir(parents=True)
        (self.scratch / "a.json").write_bytes(b"alpha")
        (self.scratch / "sub" / "b.log").write_bytes(b"beta")
        (self.scratch / "empty.txt").write_bytes(b"")

    def fake_run(self, fail=False):
        def run(cmd, **kw):
            if cmd[1:4] == ["artifact", "put", "--help"]:
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if fail:
                return SimpleNamespace(returncode=1, stdout="", stderr='{"error":{"code":"broker_unreachable","message":"down"}}')
            f, name, mpath = cmd[3], cmd[5], cmd[7]
            data = Path(f).read_bytes()
            m = json.loads(Path(mpath).read_text()) if os.path.exists(mpath) else manifest()
            m["entries"].append(entry(data, name))
            Path(mpath).write_text(json.dumps(m))
            return SimpleNamespace(returncode=0, stdout="{}", stderr="")
        return run

    def call(self, env=None, which=lambda x: "/bin/klt", run=None):
        return store.store(self.scratch, "bench", "r1", repo_root=self.root,
                           env={store.ENV_BROKER: "http://broker.invalid"} if env is None else env,
                           which=which, run=run or self.fake_run())

    def written(self):
        return [p for p in self.root.rglob("*") if p.is_file()]

    def test_success_writes_manifest_and_summary_only(self):
        mpath = self.call()
        m = json.loads(Path(mpath).read_text())
        self.assertEqual(chk.manifest_problems(m), [])
        self.assertEqual([e["name"] for e in m["entries"]], ["a.json", "sub/b.log"])
        summary = (self.root / "sim/bench/r1-stored-evidence.md").read_text()
        self.assertIn(m["entries"][0]["sha256"], summary)
        self.assertIn("empty.txt", summary)
        self.assertEqual(len(self.written()), 2)

    def test_no_broker_env_writes_nothing(self):
        with self.assertRaisesRegex(store.StoreError, "unset"):
            self.call(env={})
        self.assertEqual(self.written(), [])

    def test_no_klt_writes_nothing(self):
        with self.assertRaisesRegex(store.StoreError, "not on PATH"):
            self.call(which=lambda x: None)
        self.assertEqual(self.written(), [])

    def test_put_failure_writes_nothing(self):
        with self.assertRaisesRegex(store.StoreError, "broker_unreachable"):
            self.call(run=self.fake_run(fail=True))
        self.assertEqual(self.written(), [])

    def test_empty_scratch(self):
        for p in list(self.scratch.rglob("*")):
            if p.is_file():
                p.unlink()
        with self.assertRaises(store.StoreError):
            self.call()
        self.assertEqual(self.written(), [])

    def test_refuses_overwrite(self):
        self.call()
        with self.assertRaisesRegex(store.StoreError, "already exists"):
            self.call()


if __name__ == "__main__":
    unittest.main()
