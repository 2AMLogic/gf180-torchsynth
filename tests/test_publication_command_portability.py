"""Portable command descriptions in non-corpus evidence publications (#309).

Synthetic only: subprocesses are mocked, no Docker, rendering or recapture runs.
See spec/CORPUS-RUNNER.md, "Portable command descriptions in evidence
publications".
"""

import copy
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import artifact_renderer as ar  # noqa: E402


def load_tool(name):
    spec = importlib.util.spec_from_file_location(
        "portability_" + Path(name).stem, ROOT / name
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(path):
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def sample_launch(project, output):
    return [
        "docker", "run", "--rm", "--pull", "never",
        "--env", "OMP_NUM_THREADS=1",
        *ar.mount_options(project, output),
        "--entrypoint", "env", "sha256:" + "a" * 64,
        "-u", "ATEN_CPU_CAPABILITY",
        "python", "/repo/env/release-era/capture_traces.py", "--output", "/output",
    ]


def marked(command=None):
    _, described = ar.launch_and_described(sample_launch, "/h/a", "/h/b")
    return {
        "command": command if command is not None else described,
        "command_representation": ar.PORTABLE_COMMAND_REPRESENTATION,
    }


class CommandBuilderTest(unittest.TestCase):
    def test_two_host_roots_describe_identically_but_execute_differently(self):
        real_a, described_a = ar.launch_and_described(
            sample_launch, "/Users/a/work", "/tmp/out-a"
        )
        real_b, described_b = ar.launch_and_described(
            sample_launch, "C:\\build\\repo", "D:\\out-b"
        )
        self.assertEqual(described_a, described_b)
        self.assertNotEqual(real_a, real_b)
        self.assertIn("type=bind,src=/Users/a/work,dst=/repo,readonly", real_a)
        self.assertIn("type=bind,src=/tmp/out-a,dst=/output", real_a)
        self.assertIn("type=bind,src=<project-root>,dst=/repo,readonly", described_a)
        self.assertIn("type=bind,src=<worker-output>,dst=/output", described_a)

    def test_only_mount_sources_differ_from_executed_argv(self):
        real, described = ar.launch_and_described(sample_launch, "/p", "/o")
        self.assertEqual(len(real), len(described))
        differing = [i for i, (a, b) in enumerate(zip(real, described)) if a != b]
        self.assertEqual(len(differing), 2)
        for index in differing:
            self.assertEqual(real[index - 1], "--mount")
        for flag in ("--rm", "--pull", "never", "OMP_NUM_THREADS=1", "env",
                     "sha256:" + "a" * 64, "-u", "ATEN_CPU_CAPABILITY"):
            self.assertEqual(real.count(flag), described.count(flag))


class ValidatorTest(unittest.TestCase):
    def reject(self, owner, text):
        with self.assertRaises(ar.ReceiptPortabilityError, msg=text):
            ar.validate_published_command(owner)

    def test_marked_portable_and_unmarked_legacy(self):
        self.assertEqual(ar.validate_published_command(marked()), "portable")
        legacy = {"command": sample_launch("/Users/x/repo", "/Users/x/out")}
        self.assertEqual(ar.validate_published_command(legacy), "legacy")
        self.assertEqual(ar.validate_published_command({"command": []}), "legacy")

    def test_unknown_or_malformed_marker(self):
        for value in ("portable-placeholders-v2", "", None, 1, ["portable-placeholders-v1"]):
            owner = marked()
            owner["command_representation"] = value
            self.reject(owner, repr(value))

    def test_malformed_command(self):
        for value in (None, [], "docker run", [1], ["docker", None]):
            owner = marked()
            owner["command"] = value
            self.reject(owner, repr(value))
        self.reject({"command_representation": ar.PORTABLE_COMMAND_REPRESENTATION}, "absent")

    def test_undeclared_placeholder(self):
        _, described = ar.launch_and_described(sample_launch, "/p", "/o")
        forged = [a.replace("<project-root>", "<repo-root>") for a in described]
        self.reject(marked(forged), "undeclared")
        extra = described + ["<worker-output-2>"]
        self.reject(marked(extra), "extra")

    def test_host_sources_in_marked_commands(self):
        _, described = ar.launch_and_described(sample_launch, "/p", "/o")
        for host in ("/Users/a/repo", "C:\\repo", "C:/repo", "\\\\srv\\share", "~/repo"):
            forged = [
                a.replace("<project-root>", host) for a in described
            ]
            self.reject(marked(forged), host)
        self.reject(marked(described + ["/etc/passwd"]), "stray path")

    def test_forged_mount_targets_and_flags(self):
        _, described = ar.launch_and_described(sample_launch, "/p", "/o")
        cases = {
            "writable repo": [a.replace(",dst=/repo,readonly", ",dst=/repo") for a in described],
            "wrong repo target": [a.replace("dst=/repo", "dst=/etc") for a in described],
            "wrong output target": [a.replace("dst=/output", "dst=/repo2") for a in described],
            "swapped sources": [
                a.replace("<project-root>", "@").replace("<worker-output>", "<project-root>").replace("@", "<worker-output>")
                for a in described
            ],
            "extra mount": described + ["--mount", "type=bind,src=<project-root>,dst=/x"],
            "equals-form tmpfs mount": described + ["--mount=type=tmpfs,destination=/repo"],
            "equals-form bind mount": described + ["--mount=type=bind,src=<project-root>,dst=/x"],
            "volume": described + ["-v", "<project-root>:/x"],
            "volume equals": described + ["--volume=<project-root>:/x"],
            "stray source": described + ["src=<worker-output>"],
            "no mounts": [a for a in described if "type=bind" not in a and a != "--mount"],
        }
        for label, command in cases.items():
            self.reject(marked(command), label)

    def test_only_the_described_command_is_checked(self):
        owner = marked()
        owner["build_command"] = ["docker", "build", "-f", "/host/Dockerfile", "/host"]
        owner["stderr"] = "/host/path/in/log"
        self.assertEqual(ar.validate_published_command(owner), "portable")


class RetainedRecordsTest(unittest.TestCase):
    def test_retained_records_stay_legacy_and_valid(self):
        trace = json.loads((ROOT / "sim/reference/trace-capture.json").read_bytes())
        mutation = json.loads((ROOT / "sim/reference/mutation-runtime-v1.json").read_bytes())
        float_sources = json.loads((ROOT / "sim/reference/float-sources-v1.json").read_bytes())
        for owner in (trace["launch"], mutation["launch"], float_sources["execution"]):
            self.assertNotIn("command_representation", owner)
            self.assertEqual(ar.validate_published_command(owner), "legacy")

    def test_deferred_producers_remain_pinned_and_unedited(self):
        prototype = json.loads(
            (ROOT / "sim/reference/trace-registry-prototype.json").read_bytes()
        )["producer_sha256"]
        for path in (
            "tools/probe_trace_registry.py",
            "env/release-era/qualify_repeatability.py",
        ):
            self.assertEqual(sha(path), prototype[path], path)
        directed = json.loads(
            (ROOT / "sim/reference/directed-trace-paths-v1.json").read_bytes()
        )
        self.assertEqual(
            sha("tools/qualify_directed_trace_paths.py"),
            directed["input_sha256"]["producer_sha256"],
        )


class AdoptedProducersTest(unittest.TestCase):
    def test_float_sources_executes_real_mounts_and_publishes_placeholders(self):
        tool = load_tool("tools/capture_float_sources.py")
        runs = []

        def fake_run(command, **kwargs):
            runs.append(command)
            return mock.Mock(returncode=0, stdout=b"", stderr=b"")

        images = {}
        for label in ("one", "two"):
            with tempfile.TemporaryDirectory() as tmp:
                staging = Path(tmp) / ("cell-" + label)
                staging.mkdir()
                (staging / "capture-manifest.json").write_text("{}")
                with mock.patch.object(
                    tool, "qualified_image", return_value=("sha256:" + "b" * 64, {})
                ), mock.patch.object(tool.subprocess, "run", side_effect=fake_run):
                    _, described, image, _ = tool.run_capture(staging)
                images[label] = (described, str(staging))
        real_one, real_two = runs
        self.assertIn("type=bind,src=%s,dst=/repo,readonly" % tool.ROOT, real_one)
        self.assertIn("type=bind,src=%s,dst=/output" % images["one"][1], real_one)
        self.assertNotEqual(real_one, real_two)
        self.assertEqual(images["one"][0], images["two"][0])
        owner = {
            "command": images["one"][0],
            "command_representation": ar.PORTABLE_COMMAND_REPRESENTATION,
        }
        self.assertEqual(ar.validate_published_command(owner), "portable")
        self.assertIn("sha256:" + "b" * 64, images["one"][0])
        self.assertEqual(images["one"][0][-2:], ["python3", "/output/driver.py"])

    def test_other_adopted_tools_publish_the_described_command(self):
        for name, key in (
            ("tools/qualify_trace_capture.py", '"command": described,'),
            ("tools/qualify_mutations_runtime.py", '"command": described,'),
        ):
            source = (ROOT / name).read_text()
            self.assertIn("launch_and_described(launch, ROOT, output)", source, name)
            self.assertIn(key, source, name)
            self.assertIn('"command_representation": PORTABLE_COMMAND_REPRESENTATION', source, name)
            self.assertIn("subprocess.run(command,", source, name)
            self.assertNotIn('"command": command', source, name)

    def test_trace_capture_publication_validator_gates_marked_commands(self):
        tool = load_tool("tools/qualify_trace_capture.py")
        registry, _ = tool.load_production_modules()
        tool._check_launch_command(marked(), registry.require)
        tool._check_launch_command({"command": ["/Users/x"]}, registry.require)
        forged = copy.deepcopy(marked())
        forged["command"].append("/Users/x/repo")
        with self.assertRaises(Exception):
            tool._check_launch_command(forged, registry.require)

    def test_float_sources_checker_enforces_marked_execution_command(self):
        tool = load_tool("tools/capture_float_sources.py")
        committed = json.loads(tool.RECORD_PATH.read_bytes())
        _, described = ar.launch_and_described(sample_launch, "/p", "/o")

        def run_check(execution):
            record = copy.deepcopy(committed)
            record["execution"] = execution
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "float-sources-v1.json"
                path.write_text(json.dumps(record))
                with mock.patch.object(tool, "RECORD_PATH", path):
                    return tool.check()

        base = {k: v for k, v in committed["execution"].items() if k != "command"}
        self.assertEqual(run_check(committed["execution"]), [])
        self.assertEqual(run_check(dict(base, command=[])), [])
        self.assertEqual(run_check(dict(base, **marked())), [])
        invalid = {
            "unknown representation": dict(
                marked(), command_representation="portable-placeholders-v2"
            ),
            "host mount source": marked(
                [a.replace("<project-root>", "/Users/a/repo") for a in described]
            ),
            "forged target": marked(
                [a.replace("dst=/output", "dst=/etc") for a in described]
            ),
            "missing readonly": marked(
                [a.replace(",dst=/repo,readonly", ",dst=/repo") for a in described]
            ),
        }
        for label, owner in invalid.items():
            problems = run_check(dict(base, **owner))
            self.assertEqual(len(problems), 1, label)
            self.assertTrue(
                problems[0].startswith("publication execution command: "), label
            )


if __name__ == "__main__":
    unittest.main()
