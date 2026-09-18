#!/usr/bin/env python3
"""Source-tree convenience wrapper for the installed CLI."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main(["render", *sys.argv[1:]]))

