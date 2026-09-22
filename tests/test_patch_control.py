"""Patch-control core checks (issue #69).

Mirror-level properties run everywhere; the RTL simulation is exercised
only when Icarus Verilog is installed (CI's tb-sim job arbitrates on such
a host; an unrun check is never reported as a pass).
"""

import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import golden_vectors as gv  # noqa: E402
from torchsynth_voice.core_protocol import (  # noqa: E402
    ErrorCode,
    encode_param_word,
    patch_hash,
)
from torchsynth_voice.patch_control_model import (  # noqa: E402
    PatchControlModel,
    decode_rsp_frames,
)

TOOLS_DIR = ROOT / "tools"
TB = ROOT / "tb" / "run_tb.py"
TABLE_INCLUDE = ROOT / "tb" / "sv" / "gf180_patch_table.svh"


def has_iverilog() -> bool:
    return shutil.which("iverilog") is not None


def build_model() -> PatchControlModel:
    inventory = gv.load_inventory_document()
    names = sorted(entry["name"] for entry in inventory["parameters"])
    table_sha = hashlib.sha256(gv.INVENTORY_PATH.read_bytes()).digest()
    return PatchControlModel(
        names,
        table_sha256=table_sha,
        contract_version=__import__(
            "torchsynth_voice.core_protocol", fromlist=["numeric_contract_version_bound"]
        ).numeric_contract_version_bound(),
        profile_id=b"torchsynth-1-voice-default",
    )


class TestGeneratedTableInclude(unittest.TestCase):
    def test_check_mode_passes_against_the_committed_include(self):
        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "generate_patch_names.py"), "--check"],
            capture_output=True,
            text=True,
            timeout=600,
        )
        self.assertEqual(
            result.returncode, 0, result.stdout + "\n" + result.stderr
        )

    def test_include_binds_the_pinned_documents(self):
        inventory_bytes = gv.INVENTORY_PATH.read_bytes()
        table_sha = hashlib.sha256(inventory_bytes).digest()
        contract = (
            __import__(
                "torchsynth_voice.core_protocol",
                fromlist=["numeric_contract_version_bound"],
            ).numeric_contract_version_bound()
        )
        text = TABLE_INCLUDE.read_text(encoding="utf-8")
        self.assertIn(table_sha.hex(), text)
        self.assertIn(contract.hex(), text)


class TestMirrorSemantics(unittest.TestCase):
    """Mirror-only properties: hash binding, order invariance, taxonomy."""

    @classmethod
    def setUpClass(cls):
        cls.model = build_model()
        cls.names = list(cls.model.names)

    def test_commit_hash_matches_core_protocol(self):
        """The oracle's accepted digest is THE patch-hash-v2 digest."""
        entries = {
            name: encode_param_word(0.5 if i % 3 else 0.25)
            for i, name in enumerate(sorted(self.model.names)[:4])
        }
        digest = patch_hash(
            entries, numeric_contract_version=self.model.contract_version
        )
        self.assertEqual(len(digest), 32)

    def test_shuffled_declaration_order_commits_identically(self):
        """Staging is order-independent: the sorted-order hash commits."""
        import random

        words = {
            name: encode_param_word(0.1 * (i % 7))
            for i, name in enumerate(self.names)
        }
        base = patch_hash(
            {n: words[n] for n in self.names},
            numeric_contract_version=self.model.contract_version,
        )
        for seed in (1, 42, 20260921):
            order = list(self.names)
            random.Random(seed).shuffle(order)
            shuffled = patch_hash(
                {n: words[n] for n in order},
                numeric_contract_version=self.model.contract_version,
            )
            self.assertEqual(base, shuffled, seed)

    def test_negative_taxonomy_codes(self):
        """Each negative control maps to its SESSION error code (mirror)."""
        model = build_model()
        from torchsynth_voice.core_protocol import (
            encode_frame,
            encode_hello,
            encode_patch_commit,
            encode_patch_name,
            encode_patch_open,
            encode_patch_value,
            KIND_COMMAND,
            CMD_HELLO,
            CMD_PATCH_OPEN,
            CMD_PATCH_NAME,
            CMD_PATCH_VALUE,
            CMD_PATCH_COMMIT,
        )

        def F(cmd, seq, payload=b""):
            return encode_frame(KIND_COMMAND, cmd, seq, payload)

        contract = model.contract_version
        alpha_word = encode_param_word(0.5)
        alpha = {"adsr_1.alpha": alpha_word}
        alpha_digest = patch_hash(
            alpha, numeric_contract_version=contract
        )
        schedule = []
        frames = [
            F(CMD_HELLO, 0, encode_hello(b"x", b"y", contract, 3)),
            # unknown name (value before declaration)
            F(CMD_PATCH_OPEN, 1, encode_patch_open(b"t1", b"", model.table_sha256, 1)),
            F(CMD_PATCH_VALUE, 2, encode_patch_value("adsr_1.alpha", alpha_word)),
            F(CMD_PATCH_NAME, 3, encode_patch_name("adsr_1.alpha")),
            # duplicate with conflicting bytes
            F(CMD_PATCH_VALUE, 4, encode_patch_value("adsr_1.alpha", alpha_word)),
            F(CMD_PATCH_VALUE, 5, encode_patch_value("adsr_1.alpha", b"\x07\x00\x00\x00")),
            # clean commit ends the transaction
            F(CMD_PATCH_COMMIT, 6, encode_patch_commit(alpha_digest)),
            # wrong table reference
            F(CMD_PATCH_OPEN, 7, encode_patch_open(b"t2", b"", bytes(32), 1)),
            # transaction frame with no open transaction
            F(CMD_PATCH_NAME, 8, encode_patch_name("adsr_1.alpha")),
        ]
        for frame in frames:
            schedule.extend(frame)
            schedule.extend([None] * 300)  # conforming host pacing
        schedule.extend([None] * 500)

        rsp, obs = model.run(schedule)
        frames_out = decode_rsp_frames(rsp)
        codes = [
            ErrorCode(f[3][0]) for f in frames_out if f[0] == 3
        ]
        self.assertEqual(
            [c.name for c in codes],
            [
                "UNKNOWN_NAME",
                "DUPLICATE_NAME",
                "UNKNOWN_NAME",
                "BAD_SEQUENCE",
            ],
        )
        self.assertFalse(obs["patch_active"])
        self.assertEqual(obs["session"], 1)  # ready


@unittest.skipUnless(has_iverilog(), "Icarus Verilog is not installed here")
class TestRtlFlow(unittest.TestCase):
    def test_full_patch_tb_flow(self):
        with tempfile.TemporaryDirectory(prefix="tb-patch-test-") as tmp:
            result = subprocess.run(
                [sys.executable, str(TB), "patch", "--workdir", tmp],
                capture_output=True,
                text=True,
                timeout=600,
            )
        self.assertEqual(
            result.returncode, 0, result.stdout[-4000:] + "\n" + result.stderr[-2000:]
        )
        self.assertIn("PATCH RUN PASSED", result.stdout)


if __name__ == "__main__":
    unittest.main()
