"""Shared container-driver capture provider contract; stdlib-only.

Covers the ``trace_capture_provider`` module that both container drivers
(``tools/qualify_trace_artifacts.py`` and ``tools/capture_float_sources.py``)
now import instead of carrying their own byte-identical copy of the classes.
Three properties are asserted here because nothing else on an ordinary host
executes this code: the release-image import contract (Python 3.9 grammar, no
module-level Torch), the session's re-hash-on-exit behaviour, and the
structural guarantee that neither driver has re-grown a private copy.

The Torch-side behaviour of the capture itself is covered by
``tests/test_trace_capture.py``; fakes here never count as runtime evidence.
"""

import ast
import hashlib
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import trace_capture_provider  # noqa: E402

MODULE_PATH = ROOT / "src/torchsynth_voice/trace_capture_provider.py"
DRIVER_TOOLS = (
    "tools/qualify_trace_artifacts.py",
    "tools/capture_float_sources.py",
)


def driver_source(tool):
    """The container-side driver text embedded in ``tool``'s DRIVER_SOURCE."""
    tree = ast.parse((ROOT / tool).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "DRIVER_SOURCE"
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("no DRIVER_SOURCE assignment in " + tool)


class FakeTensor:
    def __init__(self, data):
        self.data = data


class FakeSession:
    """Minimal stand-in for a ``TraceCapture`` context manager."""

    def __init__(self, inventory, values):
        self.inventory = inventory
        self.values = values
        self.entered = False
        self.exited = None

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, exc_type, exc, tb):
        self.exited = exc_type
        return False


class FakeTorch:
    pass


def fake_tensor_bytes(value, torch):
    return value.data


class ImportContractTest(unittest.TestCase):
    def test_parses_under_the_python_39_grammar(self):
        # The release image runs this file on Python 3.9 from /repo.
        ast.parse(MODULE_PATH.read_text(), feature_version=(3, 9))

    def test_no_module_level_torch_import(self):
        tree = ast.parse(MODULE_PATH.read_text())
        names = []
        for node in tree.body:
            if isinstance(node, ast.Try):
                nodes = list(node.body)
                for handler in node.handlers:
                    nodes.extend(handler.body)
            else:
                nodes = [node]
            for inner in nodes:
                if isinstance(inner, ast.Import):
                    names.extend(alias.name for alias in inner.names)
                elif isinstance(inner, ast.ImportFrom):
                    names.append(inner.module or ".")
        self.assertTrue(names)
        for name in names:
            self.assertNotIn(name.split(".")[0], ("torch", "torchsynth"))

    def test_importing_the_module_does_not_import_torch(self):
        # A fresh interpreter: this process may already hold Torch for
        # unrelated reasons, which would make an in-process check vacuous.
        script = (
            "import sys;"
            "sys.path.insert(0, %r);"
            "import trace_capture_provider;"
            "print(sorted(m for m in sys.modules "
            "if m.split('.')[0] in ('torch', 'torchsynth')))"
            % str(ROOT / "src/torchsynth_voice")
        )
        out = subprocess.check_output([sys.executable, "-c", script], text=True)
        self.assertEqual(out.strip(), "[]")

    def test_importable_without_package_context(self):
        # The drivers put /repo/src/torchsynth_voice on sys.path and import the
        # module flat; the package-relative import must fall back cleanly.
        script = (
            "import sys;"
            "sys.path.insert(0, %r);"
            "import trace_capture_provider as p;"
            "print(p.ProviderFactory.__name__, p.ProviderSession.__name__)"
            % str(ROOT / "src/torchsynth_voice")
        )
        out = subprocess.check_output([sys.executable, "-c", script], text=True)
        self.assertEqual(out.strip(), "ProviderFactory ProviderSession")


class ProviderSessionTest(unittest.TestCase):
    def setUp(self):
        self.payload = b"\x00\x01\x02\x03"
        self.factory = trace_capture_provider.ProviderFactory({}, ["a.b"], 32)
        self.real_tensor_bytes = trace_capture_provider.trace_capture.tensor_bytes
        trace_capture_provider.trace_capture.tensor_bytes = fake_tensor_bytes

    def tearDown(self):
        trace_capture_provider.trace_capture.tensor_bytes = self.real_tensor_bytes

    def session_for(self, digest):
        inventory = [{"name": "a.b", "sha256": digest}]
        values = {"a.b": FakeTensor(self.payload)}
        return FakeSession(inventory, values)

    def test_clean_exit_publishes_bytes_and_descriptors(self):
        digest = hashlib.sha256(self.payload).hexdigest()
        session = self.session_for(digest)
        wrapper = trace_capture_provider.ProviderSession(
            self.factory, session, FakeTorch()
        )
        with wrapper as payloads:
            self.assertIs(payloads, self.factory.payloads)
            self.assertTrue(session.entered)
        self.assertEqual(self.factory.payloads, {"a.b": self.payload})
        self.assertEqual(self.factory.descriptors["a.b"]["sha256"], digest)

    def test_serialization_disagreeing_with_the_capture_is_refused(self):
        session = self.session_for("00" * 32)
        wrapper = trace_capture_provider.ProviderSession(
            self.factory, session, FakeTorch()
        )
        with self.assertRaises(ValueError) as caught:
            with wrapper:
                pass
        self.assertIn("serialized bytes disagree with capture", str(caught.exception))
        self.assertEqual(self.factory.payloads, {})

    def test_nothing_is_published_when_the_body_raised(self):
        session = self.session_for(hashlib.sha256(self.payload).hexdigest())
        wrapper = trace_capture_provider.ProviderSession(
            self.factory, session, FakeTorch()
        )
        with self.assertRaises(RuntimeError):
            with wrapper:
                raise RuntimeError("render failed")
        self.assertEqual(self.factory.payloads, {})
        self.assertEqual(self.factory.descriptors, {})


class DriverDeduplicationTest(unittest.TestCase):
    """The duplication this module exists to remove must not come back."""

    def test_no_driver_carries_a_private_provider_copy(self):
        for tool in DRIVER_TOOLS:
            with self.subTest(tool=tool):
                source = driver_source(tool)
                self.assertNotIn("class ProviderSession", source)
                self.assertNotIn("class ProviderFactory", source)

    def test_every_driver_uses_the_shared_provider(self):
        for tool in DRIVER_TOOLS:
            with self.subTest(tool=tool):
                source = driver_source(tool)
                self.assertIn("import trace_capture_provider", source)
                self.assertIn("trace_capture_provider.ProviderFactory(", source)
                # Neither driver imports trace_capture directly any more, so a
                # bare trace_capture.* reference would NameError in the image.
                self.assertEqual(re.findall(r"\btrace_capture\.\w+", source), [])

    def test_every_driver_still_parses_under_the_python_39_grammar(self):
        for tool in DRIVER_TOOLS:
            with self.subTest(tool=tool):
                ast.parse(driver_source(tool), feature_version=(3, 9))


if __name__ == "__main__":
    unittest.main()
