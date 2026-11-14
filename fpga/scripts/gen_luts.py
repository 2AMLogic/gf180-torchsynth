#!/usr/bin/env python3
"""Emit the two quarter-wave ROM images used by the FPGA measurement build.

Uses the same accepted-register gate and table construction as
``tb/run_voice.py`` (the refusal gate runs first; an unaccepted register
raises). Writes ``quarter_wave_audio.memh`` (24-bit, C5 audio table) and
``quarter_wave_control.memh`` (25-bit signed control table) plus their
SHA-256 values to stdout as JSON.

Usage: gen_luts.py OUT_DIR
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tb"))
sys.path.insert(0, str(ROOT / "src"))

from run_tb import mix_load_receipt, write_lut_memh  # noqa: E402
from torchsynth_voice.format_sweep import FixedControlPath  # noqa: E402


def main() -> int:
    out = Path(sys.argv[1]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    _receipt, formats, _cases = mix_load_receipt()
    fcp = FixedControlPath(formats.control_spec)
    result = {}
    for name, table in (("quarter_wave_audio.memh", formats.table),
                        ("quarter_wave_control.memh", fcp.table)):
        path = out / name
        write_lut_memh(table, path)
        result[name] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "entries": len(table.entries) + 1,
        }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
