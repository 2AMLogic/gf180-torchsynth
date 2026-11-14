#!/usr/bin/env python3
"""Issue #82: derive the Yosys-readable *synthesis view* of the whole-voice RTL.

The bit-exact whole-voice RTL (``tb/sv/*.sv``, issue #79) is written for
Icarus Verilog. Four of its constructs are simulation-only or outside the
subset of SystemVerilog that Yosys' native ``read_verilog`` frontend accepts
(``klt synthesize`` runs that frontend and offers no alternative; see
``asic/klt-friction.md``):

1. the constants *package* (``package``/``import pkg::*``/``localparam int``/
   ``localparam string``): ``klt synthesize`` emits ``read_verilog`` without
   ``-sv``, so a package is a syntax error. The view turns each integer
   package constant into a `` `define`` (same value; the string constants are
   unused by any module and are dropped) and every use into a macro
   expansion;
2. the ROM payload arrives via ``$value$plusargs`` + ``$readmemh`` inside
   ``initial`` blocks (``quarter_wave_lut``, ``sine_vco_engine``,
   ``lfo_vca_engine``) -- with no payload a synthesized ROM is undefined;
3. ``upsample_engine`` reads an optional ``+max_j`` plusarg (a simulation
   walk cap; ``0`` means the full walk the real top uses);
4. ``adsr_engine`` / ``lfo_vca_engine`` restoring-division functions loop
   ``for (i = den_msb + 60; i >= 0; ...)`` whose start is data dependent,
   which Yosys refuses to unroll ("not constant").

This module rewrites exactly those constructs by *exact-string, asserted*
replacement (a pattern that does not match exactly once is an error -- the
transform can never silently become a no-op) into a build directory, leaves
the committed ``tb/sv`` files untouched, and writes the two ROM payloads as
``.memh`` files. Replacements 1-3 change no synthesized behaviour (they
remove simulation hooks / bind the same constants the verified simulation
used); replacement 4 is a loop-guard rewrite that is arithmetically
identical (iterations with ``i > den_msb + 60`` are skipped by the guard) but
is a *derived-source* change: **the synthesis view is not itself simulated by
the committed bit-exactness evidence.** ``asic/README.md`` states that
limitation; ``tools/synthesize_oneshot.py`` records the hashes of both the
original and the derived sources.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TB = ROOT / "tb"
SV = TB / "sv"

#: Compile order == ``tb/run_voice.py`` ``files`` order minus the testbench.
SOURCE_ORDER = (
    "gf180_rtl_constants_pkg.sv",
    "adsr_engine.sv",
    "lfo_vca_engine.sv",
    "mod_matrix_engine.sv",
    "upsample_engine.sv",
    "quarter_wave_lut.sv",
    "sine_vco_engine.sv",
    "square_saw_vco_engine.sv",
    "noise_stream_dut.sv",
    "audio_mix_engine.sv",
    "normalization_replay_engine.sv",
    "one_shot_voice_top.sv",
)

AUDIO_MEMH = "quarter_wave_audio.memh"
CONTROL_MEMH = "quarter_wave_control.memh"


class SynthViewError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _replace_once(text: str, old: str, new: str, *, what: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SynthViewError(
            "synthesis-view rewrite %r matched %d times (expected exactly 1); "
            "the RTL changed -- update tools/synth_view.py, never skip it"
            % (what, count)
        )
    return text.replace(old, new)


PKG = "gf180_rtl_constants_pkg.sv"
_PKG_INT = re.compile(r"localparam int (\w+) = (-?\d+);")
_IMPORT = "import gf180_rtl_constants::*;\n"


def package_ints(text: str) -> list:
    """Integer constants of the package in declaration order."""
    found = [(name, int(value)) for name, value in _PKG_INT.findall(text)]
    if not found:
        raise SynthViewError("no integer localparams found in the package")
    return found


def _package_to_defines(text: str) -> str:
    out = ["// Synthesis view of gf180_rtl_constants_pkg.sv (tools/synth_view.py):\n",
           "// integer package constants as macros; string constants dropped.\n"]
    for name, value in package_ints(text):
        if not -2**31 <= value < 2**31:
            # Not representable as a 32-bit SV ``int`` and used by no module
            # (C2_UNITS_PER_TURN); left undefined so a future use fails loudly.
            continue
        out.append("`define %s %d\n" % (name, value))
    return "".join(out)


def _macro_ize(text: str, names: list) -> str:
    """Drop the package import and expand every package constant use."""
    if text.count(_IMPORT) != 1:
        raise SynthViewError("expected exactly one package import")
    text = text.replace(_IMPORT, "")
    pattern = re.compile(r"(?<![\w`])(%s)\b" % "|".join(sorted(names, key=len, reverse=True)))
    return pattern.sub(lambda m: "`" + m.group(1), text)


def _lut_initial(text: str, memh: str, *, what: str) -> str:
    """Replace the plusarg-driven ROM ``initial`` block by a fixed readmemh."""
    start = text.index("    initial begin")
    end = text.index("\n    end\n", text.index("$readmemh", start)) + len("\n    end\n")
    block = text[start:end]
    if "$value$plusargs" not in block or text.count("    initial begin") != 1:
        raise SynthViewError("unexpected ROM initial block in %s" % what)
    return text[:start] + '    initial $readmemh("%s", rom);\n' % memh + text[end:]


def _transform_one(name: str, text: str, memh_dir: str) -> str:
    """Return the synthesis view of one source file (identity if untouched)."""
    if name == PKG:
        return _package_to_defines(text)
    if name in ("quarter_wave_lut.sv", "sine_vco_engine.sv"):
        return _lut_initial(text, "%s/%s" % (memh_dir, AUDIO_MEMH), what=name)
    if name == "lfo_vca_engine.sv":
        text = _lut_initial(text, "%s/%s" % (memh_dir, CONTROL_MEMH), what=name)
        return _replace_once(
            text,
            "for (i = den_msb + 60; i >= 0; i = i - 1) begin",
            "for (i = 91; i >= 0; i = i - 1) if (i <= den_msb + 60) begin",
            what="lfo division loop guard (denominator is 32 bits, den_msb<=31)",
        )
    if name == "adsr_engine.sv":
        return _replace_once(
            text,
            "for (i = den_msb + 60; i >= 0; i = i - 1) begin",
            "for (i = 106; i >= 0; i = i - 1) if (i <= den_msb + 60) begin",
            what="adsr division loop guard (denominator is 47 bits, den_msb<=46)",
        )
    if name == "upsample_engine.sv":
        text = _replace_once(
            text, "    reg [31:0] max_j;",
            "    wire [31:0] max_j = 32'd0;",
            what="upsample max_j declaration",
        )
        start = text.index("    initial begin")
        end = text.index("\n    end\n", start) + len("\n    end\n")
        if "max_j" not in text[start:end] or "$value$plusargs" not in text[start:end]:
            raise SynthViewError("unexpected upsample initial block")
        return text[:start] + text[end:]
    return text


def transform(name: str, text: str, memh_dir: str, names: list) -> str:
    text = _transform_one(name, text, memh_dir)
    if name == PKG:
        return text
    return _macro_ize(text, names)


def _write_if_changed(path: Path, text: str) -> None:
    """Leave an identical file untouched (klt refuses inputs whose mtime moved
    during a run, so regenerating the view must not bump it)."""
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return
    path.write_text(text, encoding="utf-8")


def build(outdir: Path, memh_dir_text: str = "asic/build/src") -> dict:
    """Write the synthesis view into ``outdir``; return the hash ledger.

    ``memh_dir_text`` is the directory text written into ``$readmemh`` paths
    (relative to the directory Yosys runs in).
    """

    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(TB))
    import run_tb  # noqa: E402
    from torchsynth_voice.format_sweep import FixedControlPath  # noqa: E402

    _receipt, formats, _cases = run_tb.mix_load_receipt()
    fcp = FixedControlPath(formats.control_spec)
    for table, name in ((formats.table, AUDIO_MEMH), (fcp.table, CONTROL_MEMH)):
        scratch = outdir / (name + ".tmp")
        run_tb.write_lut_memh(table, scratch)
        _write_if_changed(outdir / name, scratch.read_text(encoding="utf-8"))
        scratch.unlink()

    ledger = {"original_sha256": {}, "synthesis_view_sha256": {},
              "memh_sha256": {AUDIO_MEMH: sha256_file(outdir / AUDIO_MEMH),
                              CONTROL_MEMH: sha256_file(outdir / CONTROL_MEMH)},
              "rewritten": []}
    names = [n for n, _v in package_ints((SV / PKG).read_text(encoding="utf-8"))]
    for name in SOURCE_ORDER:
        original = (SV / name).read_text(encoding="utf-8")
        derived = transform(name, original, memh_dir_text, names)
        _write_if_changed(outdir / name, derived)
        ledger["original_sha256"][name] = sha256_file(SV / name)
        ledger["synthesis_view_sha256"][name] = sha256_file(outdir / name)
        if derived != original:
            ledger["rewritten"].append(name)
    return ledger


if __name__ == "__main__":  # pragma: no cover
    import json
    out = Path(sys.argv[1])
    print(json.dumps(build(out), indent=2, sort_keys=True))
