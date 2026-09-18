"""Batch-size-independent identities for synth1B1-compatible sounds."""

from __future__ import annotations

from dataclasses import asdict, dataclass

BASE_REPRODUCIBLE_BATCH_SIZE = 32
UPSTREAM_NOMINAL_BATCH_SIZE = 128
TRAIN_TEST_BLOCK_SIZE = 1024


@dataclass(frozen=True, slots=True)
class SoundIdentity:
    """A global TorchSynth sound index and its derived coordinates."""

    sound_index: int

    def __post_init__(self) -> None:
        if isinstance(self.sound_index, bool) or not isinstance(self.sound_index, int):
            raise TypeError("sound_index must be an integer")
        if self.sound_index < 0:
            raise ValueError("sound_index must be non-negative")

    @classmethod
    def from_batch(cls, batch_index: int, slot: int, batch_size: int) -> SoundIdentity:
        cls._validate_batch_size(batch_size)
        if batch_index < 0:
            raise ValueError("batch_index must be non-negative")
        if not 0 <= slot < batch_size:
            raise ValueError("slot must be within the batch")
        return cls(batch_index * batch_size + slot)

    @staticmethod
    def _validate_batch_size(batch_size: int) -> None:
        if isinstance(batch_size, bool) or not isinstance(batch_size, int):
            raise TypeError("batch_size must be an integer")
        if batch_size <= 0 or batch_size % BASE_REPRODUCIBLE_BATCH_SIZE:
            raise ValueError("reproducible batch_size must be a positive multiple of 32")

    def batch_coordinates(self, batch_size: int = BASE_REPRODUCIBLE_BATCH_SIZE) -> tuple[int, int]:
        self._validate_batch_size(batch_size)
        return divmod(self.sound_index, batch_size)

    @property
    def noise_slot(self) -> int:
        """Index of the one of 32 upstream deterministic noise streams."""

        return self.sound_index % BASE_REPRODUCIBLE_BATCH_SIZE

    @property
    def is_train(self) -> bool:
        """The train/test designation specified for synth1B1."""

        return (self.sound_index // TRAIN_TEST_BLOCK_SIZE) % 10 != 9

    @property
    def upstream_name(self) -> str:
        batch, slot = self.batch_coordinates(UPSTREAM_NOMINAL_BATCH_SIZE)
        return f"synth1B1-{batch}-{slot}"

    def to_dict(self, render_batch_size: int = BASE_REPRODUCIBLE_BATCH_SIZE) -> dict[str, object]:
        render_batch, render_slot = self.batch_coordinates(render_batch_size)
        upstream_batch, upstream_slot = self.batch_coordinates(UPSTREAM_NOMINAL_BATCH_SIZE)
        value = asdict(self)
        value.update(
            {
                "render_batch_size": render_batch_size,
                "render_batch_index": render_batch,
                "render_slot": render_slot,
                "upstream_nominal_batch_size": UPSTREAM_NOMINAL_BATCH_SIZE,
                "upstream_batch_index": upstream_batch,
                "upstream_slot": upstream_slot,
                "upstream_name": self.upstream_name,
                "noise_slot": self.noise_slot,
                "is_train": self.is_train,
            }
        )
        return value

