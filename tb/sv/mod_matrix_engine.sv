// Bit-exact RTL 4x5 modulation-matrix engine (issue #72).
//
// Reproduces the frozen fixed model's mod-matrix integer dataflow
// bit-exactly: the fixed control path's _mod_matrix semantics (the frozen
// composition's control path, src/torchsynth_voice/format_sweep.py:563-598,
// consumed by src/torchsynth_voice/fixed_voice.py). One engine instance
// renders the single upstream mod_matrix occurrence: five output routes
// (vco_1_pitch, vco_1_amp, vco_2_pitch, vco_2_amp, noise_amp), each a
// weighted sum over the four source columns in the pinned input order
// (main ADSR1, main ADSR2, post-VCA LFO1, post-VCA LFO2).
//
// Declared domains (no DR-0008 register width is invented here):
// - depth words: twenty C4 MIDI-domain Q10.21 entry words (one per
//   source->route), imported widths from gf180_rtl_constants (C4_WIDTH),
//   loaded at trigger in route-major, source-order packing.
// - source columns: four streamed Q2.21 control words per tick (C1 shape),
//   the #70/#71 engines' declared outputs.
// - per route and tick: the exact numerator d0*s0 + d1*s1 + d2*s2 + d3*s3
//   (exact integer accumulate over the common denominator
//   entry_scale * ctrl_scale = 2^42), then ONE declared narrowing:
//   round_half_even(numer / 2^21) into the C1 Q2.21 output word (pitch and
//   amp routes share the accepted Q2.21 word in the accepted
//   instantiation), followed by the C7 saturation policy. There is NO
//   clamp at any matrix output: the upstream clamp is a registered
//   negative mutation only (the tb plants it by tightening the C7 bounds
//   to +-1.0 and requires detection).
// - C6 half-even at the declared narrowing (magnitude rounding, sign
//   reapplied — the canonical scalar); C7 saturation with sticky counters.
//
// Op counters since trigger (op-count conformance surface, the DR-0010
// #72 owner row: "control rate: 20 MACs + 5 declared narrowings per
// tick"): op_mults counts 4 products x 5 routes per tick, op_adds counts
// the 3 accumulate adds x 5 routes per tick, op_narrows counts the single
// declared narrowing x 5 routes per tick, op_sats the saturation events.
//
// This module makes no synthesis, layout, signoff, hardware-playback, or
// sound-fidelity claim.

`timescale 1ns/1ps

import gf180_rtl_constants::*;

module mod_matrix_engine (
    input wire        clk,
    input wire        rst,
    input wire        en,
    input wire        trigger,  // loads the run's twenty depth words

    // Twenty C4 Q10.21 depth words, route-major packing: bits
    // [4*C4_WIDTH-1 : 3*C4_WIDTH] carry the adsr_1 depth, then adsr_2,
    // lfo_1, lfo_2 in the pinned source order.
    input wire [4*C4_WIDTH-1:0] depths_r0,  // vco_1_pitch
    input wire [4*C4_WIDTH-1:0] depths_r1,  // vco_1_amp
    input wire [4*C4_WIDTH-1:0] depths_r2,  // vco_2_pitch
    input wire [4*C4_WIDTH-1:0] depths_r3,  // vco_2_amp
    input wire [4*C4_WIDTH-1:0] depths_r4,  // noise_amp

    // Per-tick source columns (Q2.21 control words, #70/#71 outputs).
    input wire signed [C1_WIDTH-1:0] col_adsr_1,
    input wire signed [C1_WIDTH-1:0] col_adsr_2,
    input wire signed [C1_WIDTH-1:0] col_lfo_1,
    input wire signed [C1_WIDTH-1:0] col_lfo_2,

    output reg  signed [C1_WIDTH-1:0] out_r0,  // mod_matrix.vco_1_pitch
    output reg  signed [C1_WIDTH-1:0] out_r1,  // mod_matrix.vco_1_amp
    output reg  signed [C1_WIDTH-1:0] out_r2,  // mod_matrix.vco_2_pitch
    output reg  signed [C1_WIDTH-1:0] out_r3,  // mod_matrix.vco_2_amp
    output reg  signed [C1_WIDTH-1:0] out_r4,  // mod_matrix.noise_amp
    output reg                        out_valid,
    // Op counters since trigger.
    output reg  [31:0] op_mults,    // multiply-class ops (20 per tick)
    output reg  [31:0] op_adds,     // accumulate adds (15 per tick)
    output reg  [31:0] op_narrows,  // declared narrowing sites (5 per tick)
    output reg  [31:0] op_sats      // sticky saturation events
);

    // Model-declared constants (cited above; never register-invented).
    localparam integer CONTROL_SAMPLES = 1764;  // 441 Hz x 4 s control grid
    localparam integer IDX_BITS = 11;           // 0..1763
    localparam [IDX_BITS-1:0] IDX_LAST = CONTROL_SAMPLES - 1;
    localparam signed [95:0] C1_SAT_HI = 96'sd8388607;   // (1<<23)-1: +4-2^-21
    localparam signed [95:0] C1_SAT_LO = -96'sd8388608;  // -(1<<23):  -4

    // --- run state (loaded at trigger; nothing survives a trigger) --------
    reg signed [C4_WIDTH-1:0] d_r0 [0:3];
    reg signed [C4_WIDTH-1:0] d_r1 [0:3];
    reg signed [C4_WIDTH-1:0] d_r2 [0:3];
    reg signed [C4_WIDTH-1:0] d_r3 [0:3];
    reg signed [C4_WIDTH-1:0] d_r4 [0:3];
    reg                       active;
    reg [IDX_BITS-1:0]        index;

    // Mutation seam for the selector-instead-of-blend test: the tb runner
    // replaces this shared blend with a discrete argmax pick to plant the
    // fault (every route then breaks on any multi-source case).
    function signed [95:0] blend4(
        input signed [C4_WIDTH-1:0] d0,
        input signed [C4_WIDTH-1:0] d1,
        input signed [C4_WIDTH-1:0] d2,
        input signed [C4_WIDTH-1:0] d3,
        input signed [C1_WIDTH-1:0] s0,
        input signed [C1_WIDTH-1:0] s1,
        input signed [C1_WIDTH-1:0] s2,
        input signed [C1_WIDTH-1:0] s3
    );
        blend4 = $signed(d0) * $signed(s0)
               + $signed(d1) * $signed(s1)
               + $signed(d2) * $signed(s2)
               + $signed(d3) * $signed(s3);
    endfunction

    // --- exact integer helpers (C6 half-even; no floats) ------------------

    // half-even rounding of a non-negative magnitude divided by 2^shift.
    function [95:0] div_half_even_pow2;
        input [95:0] magnitude;
        input integer shift;
        reg [95:0] q;
        reg [95:0] rem;
        begin
            q   = magnitude >> shift;
            rem = magnitude & ((96'd1 << shift) - 96'd1);
            if ((rem << 96'd1) > (96'd1 << shift))
                q = q + 96'd1;
            else if ((rem << 96'd1) == (96'd1 << shift))
                q = q + (q & 96'd1);  // ties to even
            div_half_even_pow2 = q;
        end
    endfunction

    // Signed half-even division of a numerator by 2^shift (the model's
    // canonical scalar rounds the magnitude and reapplies the sign).
    function signed [95:0] div_half_even_pow2_signed;
        input signed [95:0] numer;
        input integer shift;
        reg signed [95:0] mag;
        begin
            mag = (numer < 96'sd0) ? -numer : numer;
            div_half_even_pow2_signed =
                (numer < 96'sd0) ? -$signed(div_half_even_pow2(mag, shift))
                                 : $signed(div_half_even_pow2(mag, shift));
        end
    endfunction

    // One route's exact pre-narrowing numerator -> narrowed magnitude:
    // round_half_even(numer / 2^21) — the exact numerator over the common
    // denominator 2^42 times the C1 scale 2^21. C7 saturation is applied
    // by the caller so the sticky counter sees the pre-clamp word.
    // Mutation seam for the clamped-matrix-output test: the tb runner
    // tightens C1_SAT_HI/LO to +-1.0 (the upstream clamp, a registered
    // negative control) and requires detection.
    function signed [95:0] narrow_num;
        input signed [95:0] numer;
        narrow_num = div_half_even_pow2_signed(numer, 21);
    endfunction

    integer k;
    reg sat0, sat1, sat2, sat3, sat4;
    reg signed [95:0] q0, q1, q2, q3, q4;

    always @(posedge clk or posedge rst) begin
        if (rst) begin
            active    <= 1'b0;
            index     <= {IDX_BITS{1'b0}};
            out_valid <= 1'b0;
            out_r0    <= {C1_WIDTH{1'b0}};
            out_r1    <= {C1_WIDTH{1'b0}};
            out_r2    <= {C1_WIDTH{1'b0}};
            out_r3    <= {C1_WIDTH{1'b0}};
            out_r4    <= {C1_WIDTH{1'b0}};
            op_mults   <= 32'd0;
            op_adds    <= 32'd0;
            op_narrows <= 32'd0;
            op_sats    <= 32'd0;
        end else begin
            out_valid <= 1'b0;
            if (trigger) begin
                index       <= {IDX_BITS{1'b0}};
                active      <= 1'b1;
                op_mults    <= 32'd0;
                op_adds     <= 32'd0;
                op_narrows  <= 32'd0;
                op_sats     <= 32'd0;
            end else if (en && active) begin
                q0 = narrow_num(blend4(d_r0[0], d_r0[1], d_r0[2], d_r0[3],
                                       col_adsr_1, col_adsr_2, col_lfo_1, col_lfo_2));
                q1 = narrow_num(blend4(d_r1[0], d_r1[1], d_r1[2], d_r1[3],
                                       col_adsr_1, col_adsr_2, col_lfo_1, col_lfo_2));
                q2 = narrow_num(blend4(d_r2[0], d_r2[1], d_r2[2], d_r2[3],
                                       col_adsr_1, col_adsr_2, col_lfo_1, col_lfo_2));
                q3 = narrow_num(blend4(d_r3[0], d_r3[1], d_r3[2], d_r3[3],
                                       col_adsr_1, col_adsr_2, col_lfo_1, col_lfo_2));
                q4 = narrow_num(blend4(d_r4[0], d_r4[1], d_r4[2], d_r4[3],
                                       col_adsr_1, col_adsr_2, col_lfo_1, col_lfo_2));
                sat0 = (q0 > C1_SAT_HI) || (q0 < C1_SAT_LO);
                sat1 = (q1 > C1_SAT_HI) || (q1 < C1_SAT_LO);
                sat2 = (q2 > C1_SAT_HI) || (q2 < C1_SAT_LO);
                sat3 = (q3 > C1_SAT_HI) || (q3 < C1_SAT_LO);
                sat4 = (q4 > C1_SAT_HI) || (q4 < C1_SAT_LO);
                if (sat0) q0 = (q0 > 0) ? C1_SAT_HI : C1_SAT_LO;
                if (sat1) q1 = (q1 > 0) ? C1_SAT_HI : C1_SAT_LO;
                if (sat2) q2 = (q2 > 0) ? C1_SAT_HI : C1_SAT_LO;
                if (sat3) q3 = (q3 > 0) ? C1_SAT_HI : C1_SAT_LO;
                if (sat4) q4 = (q4 > 0) ? C1_SAT_HI : C1_SAT_LO;
                out_r0    <= q0[C1_WIDTH-1:0];
                out_r1    <= q1[C1_WIDTH-1:0];
                out_r2    <= q2[C1_WIDTH-1:0];
                // Mutation seam for the dropped-route test: the tb runner
                // forces this output low and requires the mismatch to
                // localize to this route's traces only.
                out_r3    <= q3[C1_WIDTH-1:0];
                out_r4    <= q4[C1_WIDTH-1:0];
                out_valid <= 1'b1;
                op_mults   <= op_mults   + 32'd20;
                op_adds    <= op_adds    + 32'd15;
                op_narrows <= op_narrows + 32'd5;
                op_sats    <= op_sats
                            + {31'd0, sat0} + {31'd0, sat1} + {31'd0, sat2}
                            + {31'd0, sat3} + {31'd0, sat4};
                if (index == IDX_LAST) begin
                    active <= 1'b0;
                end else begin
                    index <= index + {{(IDX_BITS-1){1'b0}}, 1'b1};
                end
            end
        end
    end

    // Trigger-cycle depth load (the words must be stable at the trigger
    // edge, like the #70/#71 per-trigger control words).
    always @(posedge clk) begin
        if (trigger) begin
            for (k = 0; k < 4; k = k + 1) begin
                d_r0[k] <= depths_r0[k*C4_WIDTH +: C4_WIDTH];
                d_r1[k] <= depths_r1[k*C4_WIDTH +: C4_WIDTH];
                d_r2[k] <= depths_r2[k*C4_WIDTH +: C4_WIDTH];
                d_r3[k] <= depths_r3[k*C4_WIDTH +: C4_WIDTH];
                d_r4[k] <= depths_r4[k*C4_WIDTH +: C4_WIDTH];
            end
        end
    end

endmodule
