#!/usr/bin/env python3
"""Stage the RTL for the FPGA measurement build (tb/sv stays untouched).

The verification RTL under ``tb/sv`` loads its ROMs and one debug cap from
simulator plusargs inside ``initial`` blocks. Those constructs are not
synthesizable, so this script copies exactly the source files the
one-shot top reaches into ``OUT_DIR`` and replaces ONLY those four
``initial`` blocks (asserted: exactly one match per file, nothing else is
edited):

  sine_vco_engine.sv, quarter_wave_lut.sv  +lut=          -> $readmemh audio ROM
  lfo_vca_engine.sv                        +ctl_lut=/+lut=-> $readmemh control ROM
  upsample_engine.sv                       +max_j=        -> max_j = 0 (full walk,
                                                              the plusarg's own default)

and rewrites ONE loop header in adsr_engine.sv (asserted: exactly one match).
The restoring-divide loop starts at the data-dependent bit ``den_msb + 60``,
which neither open front end (yosys-slang, Yosys read_verilog) can unroll.
It becomes a constant-bound loop (106 = max den_msb 46 + 60) with the
iteration guarded by the original start index, executing the identical set
of iterations in the identical order -- the arithmetic is unchanged:

  for (i = den_msb + 60; i >= 0; i = i - 1) begin
  -> for (i = 106; i >= 0; i = i - 1) if (i <= den_msb + 60) begin

Prints a JSON manifest (source hash, staged hash, replacement) to stdout.

Usage: prepare_sources.py OUT_DIR
"""
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SV = ROOT / "tb" / "sv"

# Compile order (package first). Everything reachable from one_shot_voice_top.
FILES = [
    "gf180_rtl_constants_pkg.sv",
    "adsr_engine.sv", "lfo_vca_engine.sv", "mod_matrix_engine.sv",
    "upsample_engine.sv", "quarter_wave_lut.sv", "sine_vco_engine.sv",
    "square_saw_vco_engine.sv", "noise_stream_dut.sv", "audio_mix_engine.sv",
    "normalization_replay_engine.sv", "one_shot_voice_top.sv",
]

REPLACEMENTS = {
    "sine_vco_engine.sv": (
        "    // [fpga/prepare_sources.py] plusarg load replaced by a fixed image\n"
        "    initial $readmemh(\"quarter_wave_audio.memh\", rom);\n"),
    "quarter_wave_lut.sv": (
        "    // [fpga/prepare_sources.py] plusarg load replaced by a fixed image\n"
        "    initial $readmemh(\"quarter_wave_audio.memh\", rom);\n"),
    "lfo_vca_engine.sv": (
        "    // [fpga/prepare_sources.py] plusarg load replaced by a fixed image\n"
        "    initial $readmemh(\"quarter_wave_control.memh\", rom);\n"),
    "upsample_engine.sv": (
        "    // [fpga/prepare_sources.py] +max_j plusarg replaced by its default\n"
        "    initial max_j = 32'd0;\n"),
}
# lut_file is declared (unused after replacement) in three files; harmless.
BLOCK = re.compile(
    r"    initial begin\n(?:(?!\n    end\n).)*?\$value\$plusargs.*?\n    end\n",
    re.DOTALL)


ADSR_LOOP_OLD = "            for (i = den_msb + 60; i >= 0; i = i - 1) begin\n"
ADSR_LOOP_NEW = ("            // [fpga/prepare_sources.py] constant bound, guarded by the\n"
                 "            // original start index (identical iterations)\n"
                 "            for (i = 106; i >= 0; i = i - 1) if (i <= den_msb + 60) begin\n")


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def main() -> int:
    out = Path(sys.argv[1]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for name in FILES:
        src = (SV / name).read_bytes()
        text = src.decode("utf-8")
        staged = text
        if name in REPLACEMENTS:
            staged, n = BLOCK.subn(lambda _m: REPLACEMENTS[name], text)
            if n != 1:
                raise SystemExit("%s: expected 1 plusarg initial block, got %d"
                                 % (name, n))
        if name == "adsr_engine.sv":
            if staged.count(ADSR_LOOP_OLD) != 1:
                raise SystemExit("adsr_engine.sv: divider loop header not found once")
            staged = staged.replace(ADSR_LOOP_OLD, ADSR_LOOP_NEW)
        if "$value$plusargs" in staged and name not in REPLACEMENTS:
            raise SystemExit("%s: unhandled plusarg" % name)
        (out / name).write_text(staged, encoding="utf-8")
        manifest.append({"file": name, "source_sha256": sha(src),
                         "staged_sha256": sha(staged.encode("utf-8")),
                         "edited": name in REPLACEMENTS or name == "adsr_engine.sv"})
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
