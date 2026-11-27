"""Issue #354 / #580: the shared emitted-constants check in ``tb/run_tb.py``.

Exercises ``_emitted_constants_match`` with matching text, changed text,
a missing schedule ID, emission refusal and unreadable package input, and
checks that callers such as ``noise`` fail with exit status 1 when emission
raises.
"""

import contextlib
import io
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tb"))

import run_tb  # noqa: E402


def _emission(text="PKG", ids=None):
    ids = [run_tb.sched.SCHEDULE_ID] if ids is None else ids
    return SimpleNamespace(package_text=text, emitted_ids=ids)


class EmittedConstantsMatchTest(unittest.TestCase):
    def _run(self, emit, landed_text, prefix=None):
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / "pkg.sv"
            if landed_text is not None:
                pkg.write_text(landed_text, encoding="utf-8")
            with mock.patch.object(run_tb, "CONSTANTS_PKG_SV", pkg), \
                    mock.patch.object(run_tb.codegen, "emit", emit), \
                    contextlib.redirect_stdout(out):
                args = {} if prefix is None else {"prefix": prefix}
                try:
                    result = run_tb._emitted_constants_match({"s": 1}, **args)
                except Exception as error:  # noqa: BLE001
                    return error, out.getvalue()
        return result, out.getvalue()

    def test_match(self):
        result, out = self._run(lambda **_: _emission(), "PKG")
        self.assertIs(result, True)
        self.assertIn("budget constants: landed pkg.sv matches", out)
        self.assertTrue(out.rstrip().endswith("-> OK"))

    def test_anchor_prefix(self):
        _, out = self._run(
            lambda **_: _emission(), "PKG", prefix="emitted budget constants"
        )
        self.assertTrue(out.startswith("  emitted budget constants: landed"))

    def test_changed_text(self):
        result, out = self._run(lambda **_: _emission(), "PKG\n")
        self.assertIs(result, False)
        self.assertTrue(out.rstrip().endswith("-> FAIL"))

    def test_missing_schedule_id(self):
        result, out = self._run(lambda **_: _emission(ids=[]), "PKG")
        self.assertIs(result, False)
        self.assertTrue(out.rstrip().endswith("-> FAIL"))

    def test_emission_refusal_propagates(self):
        def refuse(**_):
            raise ValueError("refused")

        result, out = self._run(refuse, "PKG")
        self.assertIsInstance(result, ValueError)
        self.assertEqual(out, "")

    def test_unreadable_package_propagates(self):
        result, out = self._run(lambda **_: _emission(), None)
        self.assertIsInstance(result, FileNotFoundError)
        self.assertEqual(out, "")


class NoiseEmissionRefusalExitStatusTest(unittest.TestCase):
    def test_noise_emission_refusal_exits_with_status_one(self):
        script = textwrap.dedent(
            """
            import json
            import sys
            import tempfile
            from pathlib import Path
            from unittest import mock

            root = Path(%r)
            sys.path.insert(0, str(root / "tb"))
            import run_tb

            committed = json.loads(run_tb.NOISE_RECEIPT_PATH.read_bytes())
            sliced = {
                "kind": committed["kind"],
                "cases": committed["cases"][:1],
            }
            staged = {}
            sim_dirs = []

            def fake_write_run(
                workdir, run, _sound_index, declared_slot, noise_bytes, **_kw
            ):
                staged[(workdir, run)] = (declared_slot, noise_bytes)

            def fake_simulate(workdir, _simulator, runs, _dut_sv):
                sim_dirs.append(workdir.name)
                captures = []
                statuses = []
                for run in range(runs):
                    slot, raw = staged[(workdir, run)]
                    mirror = run_tb.nsg.mirror_stream(raw)
                    captures.append(mirror)
                    statuses.append(
                        [0, 0, len(raw), len(mirror), len(mirror), slot]
                    )
                return captures, statuses

            def refuse_emit(**_kw):
                raise RuntimeError("injected refusal")

            with tempfile.TemporaryDirectory() as tmp:
                receipt_path = Path(tmp) / "receipt.json"
                receipt_path.write_text(
                    json.dumps(sliced), encoding="utf-8"
                )
                with mock.patch.object(
                    run_tb.shutil, "which", return_value="/mock/iverilog"
                ), mock.patch.object(
                    run_tb, "NOISE_RECEIPT_PATH", receipt_path
                ), mock.patch.object(
                    run_tb, "noise_write_run", fake_write_run
                ), mock.patch.object(
                    run_tb, "noise_simulate", fake_simulate
                ), mock.patch.object(
                    run_tb.codegen, "emit", refuse_emit
                ):
                    rc = run_tb.main(["noise"])
            assert sim_dirs == ["committed"], sim_dirs
            sys.exit(rc)
            """
            % str(ROOT)
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.stderr, "")
        self.assertIn(
            "  budget constants: emission failed -> injected refusal",
            proc.stdout,
        )
        self.assertNotIn("mutation ", proc.stdout)
        self.assertNotIn("NOISE RUN PASSED", proc.stdout)
        self.assertEqual(proc.returncode, 1)


if __name__ == "__main__":
    unittest.main()
