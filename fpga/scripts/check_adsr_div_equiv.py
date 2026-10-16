#!/usr/bin/env python3
"""Bounded differential check of the DR-0013 D3 edit 4 (adsr_engine loop).

`prepare_sources.py` rewrites the restoring-divide loop header of
`div_half_even` in the *staged copy* of tb/sv/adsr_engine.sv. This script
extracts `div_half_even` from the unmodified tb/sv source, applies the exact
same ADSR_LOOP_OLD -> ADSR_LOOP_NEW replacement that prepare_sources.py
applies, instantiates both functions side by side in a generated Icarus
testbench, and compares their outputs on:

  * directed edges: denominator 0, every single-bit denominator, all-ones
    denominator, numerator 0 / all-ones / 2^k, and the in-contract boundary
    numerator (den << 60) and its neighbours;
  * N pseudo-random vectors (fixed seed) over the full 142-bit numerator and
    47-bit denominator, plus N in-contract vectors (numerator reduced so the
    quotient is <= 2^60, the range the caller guarantees).

This is a BOUNDED SIMULATION check, not a formal equivalence proof: it shows
the two function bodies agree on every vector it runs, nothing more. It
checks the function in isolation under Icarus, not the synthesized netlist.

Usage: check_adsr_div_equiv.py [N_RANDOM]   (default 50000)
Exit 0 only if the testbench ran and reported zero mismatches.
"""
import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "tb" / "sv" / "adsr_engine.sv"


def load_prepare():
    spec = importlib.util.spec_from_file_location(
        "prepare_sources", Path(__file__).with_name("prepare_sources.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def extract_function(text: str) -> str:
    m = re.search(r"^    function \[141:0\] div_half_even;\n.*?^    endfunction\n",
                  text, re.DOTALL | re.MULTILINE)
    if not m:
        raise SystemExit("div_half_even not found in %s" % SRC)
    return m.group(0)


TB = r"""
module tb_adsr_div_equiv;
%(orig)s
%(staged)s
    integer n, k, mism, total, seed;
    reg [31:0] r;
    reg [141:0] num;
    reg [46:0]  den;
    reg [141:0] a, b, lim;
    task check;
        begin
            a = div_orig(num, den);
            b = div_staged(num, den);
            total = total + 1;
            if (a !== b) begin
                mism = mism + 1;
                if (mism <= 5)
                    $display("MISMATCH num=%%h den=%%h orig=%%h staged=%%h",
                             num, den, a, b);
            end
        end
    endtask
    initial begin
        mism = 0; total = 0;
        n = %(n)d;
        seed = %(seed)d;
        // directed edges
        for (k = -1; k < 47; k = k + 1) begin
            den = (k < 0) ? 47'd0 : (47'd1 << k);
            num = 142'd0; check;
            num = ~142'd0; check;
            num = {95'd0, den} << 60; check;
            num = ({95'd0, den} << 60) + 142'd1; check;
            if (den != 0) begin num = ({95'd0, den} << 60) - 142'd1; check; end
            num = 142'd1 << (k < 0 ? 0 : k); check;
        end
        den = ~47'd0;
        num = 142'd0; check;
        num = ~142'd0; check;
        num = {95'd0, den} << 60; check;
        // random, full width
        for (k = 0; k < n; k = k + 1) begin
            num = {$random(seed), $random(seed), $random(seed), $random(seed), $random(seed)};
            den = {$random(seed), $random(seed)};
            r = $random(seed);
            if (k %% 4 == 1) den = den >> (r %% 47);
            check;
        end
        // random, in-contract (quotient <= 2^60)
        for (k = 0; k < n; k = k + 1) begin
            r = $random(seed);
            den = {$random(seed), $random(seed)} >> (r %% 47);
            if (den == 0) den = 47'd1;
            lim = ({95'd0, den} << 60) + 142'd1;
            num = {$random(seed), $random(seed), $random(seed), $random(seed), $random(seed)} %% lim;
            check;
        end
        $display("ADSR_DIV_EQUIV total=%%0d mismatches=%%0d", total, mism);
        $finish;
    end
endmodule
"""


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 50000
    prep = load_prepare()
    func = extract_function(SRC.read_text(encoding="utf-8"))
    if func.count(prep.ADSR_LOOP_OLD) != 1:
        raise SystemExit("loop header not found once in div_half_even")
    orig = func.replace("div_half_even", "div_orig")
    staged = func.replace(prep.ADSR_LOOP_OLD, prep.ADSR_LOOP_NEW) \
                 .replace("div_half_even", "div_staged")
    iverilog = shutil.which("iverilog")
    vvp = shutil.which("vvp")
    if not (iverilog and vvp):
        print("NOT RUN: iverilog/vvp not on PATH")
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        tb = Path(tmp) / "tb.sv"
        tb.write_text(TB % {"orig": orig, "staged": staged, "n": n,
                            "seed": 80}, encoding="utf-8")
        out = Path(tmp) / "tb.vvp"
        subprocess.run([iverilog, "-g2012", "-o", str(out), str(tb)], check=True)
        res = subprocess.run([vvp, "-n", str(out)], capture_output=True,
                             text=True)
    print(res.stdout, end="")
    if res.returncode != 0:
        print(res.stderr, end="")
        print("FAIL: vvp exited %d" % res.returncode)
        return 1
    m = re.search(r"ADSR_DIV_EQUIV total=(\d+) mismatches=(\d+)", res.stdout)
    if not m:
        print("FAIL: no summary line (testbench did not complete)")
        return 1
    if int(m.group(2)) != 0:
        print("FAIL")
        return 1
    print("PASS (bounded simulation, %s vectors; not a formal proof)" % m.group(1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
