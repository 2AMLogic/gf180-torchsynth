// Bit-exact RTL endpoint-aligned control upsampler (issue #72).
//
// Reproduces the frozen fixed model's control-upsample integer dataflow
// bit-exactly: the fixed control path's _upsample semantics (the frozen
// composition's control path, src/torchsynth_voice/format_sweep.py:600-687,
// consumed by src/torchsynth_voice/fixed_voice.py). One engine instance
// renders one shared control_upsample occurrence: a 1764-sample control
// column upsampled to the 176,400-sample audio grid.
//
// Declared coordinate contract (spec/FLOAT-INTERFACES.md; the registry's
// control_upsample.* interpolation blocks; align_corners=True):
// - output index j reads the exact rational source coordinate
//   j*(1764-1)/(176400-1) = j*1763/176399 (a 100x endpoint-aligned grid;
//   gcd(1763, 176399) = 1, so the only exact-boundary indices are the two
//   endpoints j = 0 and j = 176399);
// - endpoints are exact copies of control indices 0 and 1763;
// - the interpolation fraction is the candidate's quantized uQ.31 word
//   round_half_even(remainder * 2^31 / 176399) (the declared
//   format_sweep.up_coordinate_table policy; fraction word 0 at exact
//   boundaries);
// - the blend is the exact integer numerator
//   left*(2^31 - frac) + right*frac  [=  left*2^31 + (right-left)*frac]
//   with ONE declared half-even narrowing (divide by 2^31, magnitude
//   rounding, sign reapplied — the canonical scalar), then C7 saturation
//   into the Q2.21 word. ZOH and the off-endpoint scale j*1763/176400
//   (align_corners=False) are registered negative mutations only: both
//   must be DETECTED.
//
// The coordinate walk is EXACT and INCREMENTAL: the source coordinate is
// carried as its divmod pair (low, remainder) over j*1763/176399 — each
// step adds 1763 and borrows at 176399 (exactly one borrow, since
// 176399 + 1763 < 2*176399) — the identical (low, remainder) the model's
// up_coordinate_table materializes via divmod(j*1763, 176399), at one add
// and one compare per step (op_coord_steps, a declared extra).
//
// Op-count conformance surface (the DR-0010 #72 owner row: "audio rate:
// 2 mults + 1 add + 1 half-even blend per column per sample"): per
// interior sample op_mults counts the blend product (right-left)*frac,
// op_adds counts the blend sum left*2^31 + product, op_narrows counts the
// single declared blend narrowing, and op_frac_words counts fraction-word
// formations — 1/1/1 within the owner-row cap of 2/1/1. The two
// exact-boundary endpoints are exact copies by construction and carry no
// blend ops (the declared exception).
//
// A `+max_j=<n>` plusarg optionally caps the walk after the n-th emitted
// word (mutation demonstrations only; the committed-case runs use the
// full 176,400-sample walk).
//
// This module makes no synthesis, layout, signoff, hardware-playback, or
// sound-fidelity claim.

`timescale 1ns/1ps

import gf180_rtl_constants::*;

module upsample_engine #(
    parameter integer ROUTE_ID = 0
) (
    input wire        clk,
    input wire        rst,
    input wire        en,
    input wire        trigger,      // clears counters and the audio walk

    // Column load phase: mem[load_index] <- column_word (1764 cycles).
    input wire        load,
    input wire [10:0] load_index,
    input wire signed [C1_WIDTH-1:0] column_word,

    input wire        go,           // one-cycle pulse: start the walk at j = 0

    output reg  signed [C1_WIDTH-1:0] audio_word,
    output reg                        audio_valid,
    output reg  [31:0] out_index,     // j of the last emitted word (debug)
    // Op counters since trigger.
    output reg  [31:0] op_mults,      // blend product per interior sample
    output reg  [31:0] op_adds,       // blend sum per interior sample
    output reg  [31:0] op_narrows,    // declared blend narrowing per interior
    output reg  [31:0] op_frac_words, // fraction-word formations
    output reg  [31:0] op_coord_steps, // exact incremental coordinate steps
    output reg  [31:0] op_sats,       // sticky saturation events
    output reg  [31:0] out_count      // emitted words
);

    // Model-declared constants (cited above; never register-invented).
    localparam integer CONTROL_SAMPLES = 1764;
    localparam integer AUDIO_SAMPLES   = 176400;
    localparam [31:0] J_LAST = AUDIO_SAMPLES - 1;
    localparam [17:0] UP_NUM_UNIT = 18'd1763;    // CONTROL_SAMPLES - 1
    // Mutation seam for the off-endpoint test (align_corners=False, the
    // dropped-endpoint scale j*1763/176400): the tb runner replaces this
    // denominator and requires detection.
    localparam [17:0] UP_DEN      = 18'd176399;  // AUDIO_SAMPLES - 1
    localparam signed [95:0] C1_SAT_HI = 96'sd8388607;   // (1<<23)-1
    localparam signed [95:0] C1_SAT_LO = -96'sd8388608;  // -(1<<23)

    // --- state -------------------------------------------------------------
    reg signed [C1_WIDTH-1:0] mem [0:CONTROL_SAMPLES-1];
    reg [31:0] j;            // audio output index
    reg        walking;
    reg [10:0] low_r;        // floor of the source coordinate
    reg [17:0] rem_r;        // coordinate remainder (< 176399)
    reg [31:0] max_j;        // 0 = full walk; else stop after max_j words

    // --- exact integer helpers (C6 half-even; no floats) -------------------

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

    // The uQ.31 fraction word round_half_even(remainder * 2^31 / den):
    // exact restoring long division of the 49-bit numerator
    // {remainder, 31'b0} by the 18-bit denominator (the quotient cannot
    // exceed 2^31-1 because remainder < den), then the half-even tie
    // decision on the exact remainder. No floats.
    function [31:0] frac_word_half_even;
        input [17:0] remainder;
        input [17:0] denominator;
        reg [48:0] num;
        reg [48:0] acc;
        reg [48:0] den_z;
        reg [48:0] quo;
        integer i;
        begin
            num   = {remainder, 31'd0};
            acc   = 49'd0;
            quo   = 49'd0;
            den_z = {31'd0, denominator};
            for (i = 48; i >= 0; i = i - 1) begin
                acc = (acc << 1) | ((num >> i) & 49'd1);
                if (acc >= den_z) begin
                    acc = acc - den_z;
                    quo[i] = 1'b1;
                end else begin
                    quo[i] = 1'b0;
                end
            end
            if ((acc << 1) > den_z)
                quo = quo + 49'd1;
            else if ((acc << 1) == den_z)
                quo = quo + (quo & 49'd1);  // ties to even
            frac_word_half_even = quo[31:0];
        end
    endfunction

    reg [31:0]  frac_word;     // uQ.31 quantized fraction
    reg signed [C1_WIDTH-1:0] left, right, diff;
    reg signed [95:0] blend_numer, q;
    reg         interior;

    initial begin
        if (!$value$plusargs("max_j=%d", max_j)) begin
            max_j = 32'd0;  // full walk
        end
    end

    always @(posedge clk or posedge rst) begin
        if (rst) begin
            walking     <= 1'b0;
            j           <= 32'd0;
            low_r       <= 11'd0;
            rem_r       <= 18'd0;
            out_count   <= 32'd0;
            audio_valid <= 1'b0;
            audio_word  <= {C1_WIDTH{1'b0}};
            out_index   <= 32'd0;
            op_mults        <= 32'd0;
            op_adds         <= 32'd0;
            op_narrows      <= 32'd0;
            op_frac_words   <= 32'd0;
            op_coord_steps  <= 32'd0;
            op_sats         <= 32'd0;
        end else begin
            audio_valid <= 1'b0;
            if (trigger) begin
                j             <= 32'd0;
                walking       <= 1'b0;
                low_r         <= 11'd0;
                rem_r         <= 18'd0;
                out_count     <= 32'd0;
                op_mults      <= 32'd0;
                op_adds       <= 32'd0;
                op_narrows    <= 32'd0;
                op_frac_words <= 32'd0;
                op_coord_steps <= 32'd0;
                op_sats       <= 32'd0;
            end else if (load && en) begin
                mem[load_index] <= column_word;
            end else if (go && en && !walking) begin
                walking <= 1'b1;
                j       <= 32'd0;
                low_r   <= 11'd0;
                rem_r   <= 18'd0;
            end else if (walking && en) begin
                interior = (rem_r != 18'd0);
                if (!interior) begin
                    // Exact boundary: an exact copy of control index low
                    // (declared endpoints j = 0 and j = 176399).
                    audio_word  <= mem[low_r];
                    audio_valid <= 1'b1;
                end else begin
                    frac_word = frac_word_half_even(rem_r, UP_DEN);
                    op_frac_words <= op_frac_words + 32'd1;
                    left  = mem[low_r];
                    right = mem[low_r + 11'd1];
                    diff  = right - left;
                    // left*(2^31 - frac) + right*frac, exactly:
                    //   left*2^31 + (right-left)*frac.
                    blend_numer = $signed(left) * (96'sd1 <<< 31)
                                + $signed(diff) * $signed({1'b0, frac_word});
                    op_mults <= op_mults + 32'd1;
                    op_adds  <= op_adds + 32'd1;
                    q = div_half_even_pow2_signed(blend_numer, 31);
                    op_narrows <= op_narrows + 32'd1;
                    if (q > C1_SAT_HI) begin
                        q = C1_SAT_HI;
                        op_sats <= op_sats + 32'd1;
                    end else if (q < C1_SAT_LO) begin
                        q = C1_SAT_LO;
                        op_sats <= op_sats + 32'd1;
                    end
                    // Mutation seam for the ZOH test: the tb runner replaces
                    // this registered blend result with the floor column word
                    // (no interpolation) and requires detection.
                    audio_word  <= q[C1_WIDTH-1:0];
                    audio_valid <= 1'b1;
                end
                out_index <= j;
                out_count <= out_count + 32'd1;
                if (j == J_LAST || (max_j != 32'd0 && j + 32'd1 == max_j)) begin
                    walking <= 1'b0;
                end else begin
                    // Exact incremental coordinate step: the identical
                    // divmod(j*1763, 176399) update (one add, one borrow,
                    // since 176399 + 1763 < 2*176399).
                    op_coord_steps <= op_coord_steps + 32'd1;
                    if (rem_r >= UP_DEN - UP_NUM_UNIT) begin
                        rem_r <= rem_r + UP_NUM_UNIT - UP_DEN;
                        low_r <= low_r + 11'd1;
                    end else begin
                        rem_r <= rem_r + UP_NUM_UNIT;
                    end
                    j <= j + 32'd1;
                end
            end
        end
    end

endmodule
