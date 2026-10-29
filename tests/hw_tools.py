"""Shared host-tool probes for the RTL-simulation tests."""

import shutil


def has_iverilog() -> bool:
    return shutil.which("iverilog") is not None
