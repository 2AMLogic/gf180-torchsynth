// C5 quarter-wave LUT evaluator (issue #74; the issue #68 format-true path).
//
// One evaluated phase word in, one interpolated C5 entry word out. The ROM
// layout, quadrant reflection/negation, exact integer linear interpolation
// (half-even per C6), and the quarter-turn endpoint entry are exactly the
// landed lut_sine_dut.sv path (issue #68/#158); they are factored into this
// module only so the square/saw engine can evaluate the phase twice (cos at
// the phase, sin a quarter turn behind) without duplicating the logic.
// Every width/geometry constant is imported from gf180_rtl_constants; the
// ROM payload arrives via the +lut=<memh> plusarg, hash-linked by the tb
// runner to the accepted DR-0008 register. No floats participate.

`timescale 1ns/1ps

import gf180_rtl_constants::*;

module quarter_wave_lut (
    input  wire [C2_WIDTH-1:0]            phase,       // post-step phase word
    output wire signed [C5_ENTRY_WIDTH-1:0] entry_word // signed Q1.<C5_ENTRY_WIDTH-2>
);

    // C5: quarter-wave table ROM; index C5_N_ENTRIES holds the quarter-turn
    // endpoint entry (the reflected r == 0 neighbor of the last entry).
    reg signed [C5_ENTRY_WIDTH-1:0] rom [0:C5_N_ENTRIES];

    reg [1023:0] lut_file;
    initial begin
        if (!$value$plusargs("lut=%s", lut_file)) begin
            $display("LUT-ERROR missing +lut=<memh path> plusarg");
            $finish;
        end
        $readmemh(lut_file, rom);
    end

    localparam integer QUADRANT_BITS   = C5_PHASE_BITS - 2;
    localparam integer ENTRY_FRAC_BITS = C5_ENTRY_WIDTH - 2;  // signed Q1.<f>

    // C6 half-even rounding of a non-negative magnitude by 2^shift.
    // Tie detection is exact integer comparison; no floats participate.
    function [63:0] div_half_even_pow2;
        input [63:0] magnitude;
        input integer shift;
        reg [63:0] q;
        reg [63:0] rem;
        begin
            q   = magnitude >> shift;
            rem = magnitude & ((64'd1 << shift) - 64'd1);
            if ((rem << 64'd1) > (64'd1 << shift))
                q = q + 64'd1;
            else if ((rem << 64'd1) == (64'd1 << shift))
                q = q + (q & 64'd1);  // ties to even
            div_half_even_pow2 = q;
        end
    endfunction

    // --- LUT evaluation of the post-step phase (model-exact op order) -----
    wire [1:0]               quadrant = phase[C2_WIDTH-1 -: 2];
    wire [QUADRANT_BITS-1:0] r_raw    = phase[QUADRANT_BITS-1:0];
    wire                     reflect  = quadrant[0];
    wire                     negate   = quadrant[1] ^ quadrant[0];
    // Reflected r can equal exactly 2^QUADRANT_BITS -> needs one extra bit.
    wire [QUADRANT_BITS:0]   r = reflect
        ? ((64'd1 << QUADRANT_BITS) - r_raw)
        : {1'b0, r_raw};
    wire [C5_INDEX_BITS:0]   i = r[QUADRANT_BITS:C5_INTERP_BITS];
    wire [C5_INTERP_BITS-1:0] t = r[C5_INTERP_BITS-1:0];

    wire signed [C5_ENTRY_WIDTH-1:0] a = rom[i[C5_INDEX_BITS-1:0]];
    wire [C5_INDEX_BITS:0]  i_next = i + 1'b1;
    wire signed [C5_ENTRY_WIDTH-1:0] b = (i == C5_N_ENTRIES - 1)
        ? rom[C5_N_ENTRIES]
        : rom[i_next[C5_INDEX_BITS-1:0]];
    wire endpoint_hit = (i == C5_N_ENTRIES);

    // Integer-exact linear interpolation: (a*2^I + (b-a)*t) / 2^I, half-even.
    // Quarter-cos entries are non-negative, so the numerator is >= 0.
    wire signed [63:0] a64 = a;
    wire signed [63:0] b64 = b;
    wire signed [63:0] t64 = t;
    wire signed [63:0] interp_num = a64 * (64'sd1 << C5_INTERP_BITS)
                                  + (b64 - a64) * t64;
    wire [63:0] entry_interp_u = div_half_even_pow2(interp_num, C5_INTERP_BITS);
    wire signed [C5_ENTRY_WIDTH-1:0] interp_word = entry_interp_u[C5_ENTRY_WIDTH-1:0];
    wire signed [C5_ENTRY_WIDTH-1:0] lut_word = endpoint_hit
        ? rom[C5_N_ENTRIES]
        : interp_word;

    assign entry_word = negate ? -lut_word : lut_word;

endmodule
