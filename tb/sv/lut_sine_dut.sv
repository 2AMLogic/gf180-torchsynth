// Format-true harness DUT for the real golden-vector anchor run (issue #68).
//
// Minimal fixed-point synthesis block: the C5 quarter-wave LUT sine path
// (u32 wrapping phase accumulator per C2, 4096x24 table with linear
// interpolation per C5, half-even rounding per C6, saturation per C7)
// followed by one Q2.21 x Q2.21 mixer-level multiply. Every width, geometry,
// rounding mode, and policy constant is imported from the generated
// constants package (gf180_rtl_constants), which the tb flow hash-checks
// against the accepted DR-0008 register before this module is ever
// elaborated. No width is hardcoded here.
//
// Sample-exact obligation (AGENTS.md): this DUT reproduces the fixed model's
// integer dataflow bit-exactly. The declared binary64 shadow sites (the
// midi->Hz exp2 and friends) are NOT implemented here: the tb harness
// derives their quantized results (the phase-increment word, the S1-entry
// mixer-level word) host-side and drives them in as control inputs, exactly
// as DR-0008 declares them open approximation items. This module makes no
// synthesis, layout, signoff, hardware-playback, or sound-fidelity claim.
//
// LUT payload: +lut=<memh-path> supplies the hash-linked table emitted by
// the harness from the accepted-register geometry (C5_N_ENTRIES entries
// followed by the quarter-turn endpoint entry).

`timescale 1ns/1ps

import gf180_rtl_constants::*;

module lut_sine_dut (
    input  wire                        clk,
    input  wire                        rst,
    input  wire                        en,
    input  wire [C2_WIDTH-1:0]         phase_step,
    input  wire signed [C1_WIDTH-1:0]  level,
    output reg  signed [C1_WIDTH-1:0]  vco_word,
    output reg  signed [C1_WIDTH-1:0]  mix_word,
    output reg                         out_valid
);

    // C2: u32 wrapping phase accumulator (never-saturate word; wrapping is
    // semantics, not an error event).
    reg [C2_WIDTH-1:0] phase;

    // C5: quarter-wave table ROM; index C5_N_ENTRIES holds the quarter-turn
    // endpoint entry (the reflected r == 0 neighbor of the last entry).
    reg signed [C5_ENTRY_WIDTH-1:0] rom [0:C5_N_ENTRIES];

    reg [1023:0] lut_file;
    initial begin
        if (!$value$plusargs("lut=%s", lut_file)) begin
            $display("DUT-ERROR missing +lut=<memh path> plusarg");
            $finish;
        end
        $readmemh(lut_file, rom);
    end

    localparam integer QUADRANT_BITS    = C5_PHASE_BITS - 2;
    localparam integer ENTRY_FRAC_BITS  = C5_ENTRY_WIDTH - 2;  // signed Q1.<f>
    // entry-format -> audio-format narrowing shift (frac_bits difference).
    localparam integer VCO_RESCALE_SHIFT = ENTRY_FRAC_BITS - C1_FRAC_BITS;
    localparam signed [63:0] C1_MAX = (64'sd1 << (C1_WIDTH - 1)) - 64'sd1;
    localparam signed [63:0] C1_MIN = -(64'sd1 << (C1_WIDTH - 1));

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

    function signed [63:0] saturate_c1;
        input signed [63:0] value;
        begin
            if (value > C1_MAX)
                saturate_c1 = C1_MAX;
            else if (value < C1_MIN)
                saturate_c1 = C1_MIN;
            else
                saturate_c1 = value;
        end
    endfunction

    // --- LUT evaluation of the post-step phase (model-exact op order) -----
    wire [C2_WIDTH-1:0]      phase_next = phase + phase_step;  // C2 modular wrap
    wire [1:0]               quadrant   = phase_next[C2_WIDTH-1 -: 2];
    wire [QUADRANT_BITS-1:0] r_raw      = phase_next[QUADRANT_BITS-1:0];
    wire                     reflect    = quadrant[0];
    wire                     negate     = quadrant[1] ^ quadrant[0];
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
    wire signed [C5_ENTRY_WIDTH-1:0] entry_word = negate ? -lut_word : lut_word;

    // --- entry format -> audio format rescale (single declared site) ------
    wire signed [63:0] entry64 = entry_word;
    wire signed [63:0] entry_mag = (entry64 < 0) ? -entry64 : entry64;
    wire [63:0] vco_mag = div_half_even_pow2(entry_mag, VCO_RESCALE_SHIFT);
    wire signed [63:0] vco_unsat = (entry64 < 0) ? -$signed(vco_mag) : $signed(vco_mag);
    wire signed [63:0] vco_sat = saturate_c1(vco_unsat);  // C7 policy

    // --- mixer-level multiply (Q2.21 x Q2.21 -> Q2.21, one narrowing) -----
    wire signed [63:0] level64 = level;
    wire signed [63:0] product = vco_sat * level64;
    wire signed [63:0] product_mag = (product < 0) ? -product : product;
    wire [63:0] mix_mag = div_half_even_pow2(product_mag, C1_FRAC_BITS);
    wire signed [63:0] mix_unsat = (product < 0) ? -$signed(mix_mag) : $signed(mix_mag);
    wire signed [63:0] mix_sat = saturate_c1(mix_unsat);

    always @(posedge clk) begin
        if (rst) begin
            phase     <= {C2_WIDTH{1'b0}};
            vco_word  <= {C1_WIDTH{1'b0}};
            mix_word  <= {C1_WIDTH{1'b0}};
            out_valid <= 1'b0;
        end else if (en) begin
            phase     <= phase_next;
            vco_word  <= vco_sat[C1_WIDTH-1:0];
            mix_word  <= mix_sat[C1_WIDTH-1:0];
            out_valid <= 1'b1;
        end else begin
            out_valid <= 1'b0;
        end
    end

endmodule
