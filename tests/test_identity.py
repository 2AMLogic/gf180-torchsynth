from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.identity import SoundIdentity  # noqa: E402


class SoundIdentityTests(unittest.TestCase):
    def test_quickstart_identity_is_batch_size_independent(self) -> None:
        identity = SoundIdentity.from_batch(312, 6, 128)
        self.assertEqual(identity.sound_index, 39942)
        self.assertEqual(identity.batch_coordinates(32), (1248, 6))
        self.assertEqual(identity.batch_coordinates(64), (624, 6))
        self.assertEqual(identity.upstream_name, "synth1B1-312-6")

    def test_noise_slot_repeats_every_32_sounds(self) -> None:
        for sound_index in range(96):
            self.assertEqual(SoundIdentity(sound_index).noise_slot, sound_index % 32)

    def test_train_test_blocks(self) -> None:
        self.assertTrue(SoundIdentity(9 * 1024 - 1).is_train)
        self.assertFalse(SoundIdentity(9 * 1024).is_train)
        self.assertFalse(SoundIdentity(10 * 1024 - 1).is_train)
        self.assertTrue(SoundIdentity(10 * 1024).is_train)

    def test_rejects_invalid_values(self) -> None:
        with self.assertRaises(ValueError):
            SoundIdentity(-1)
        with self.assertRaises(TypeError):
            SoundIdentity(True)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            SoundIdentity(0).batch_coordinates(31)
        with self.assertRaises(ValueError):
            SoundIdentity.from_batch(0, 32, 32)


if __name__ == "__main__":
    unittest.main()

