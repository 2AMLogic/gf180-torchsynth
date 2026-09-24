// Bit-exact RTL ADSR envelope engine core (issue #70).
//
// Reproduces the frozen fixed model's ADSR integer dataflow bit-exactly:
// the fixed control path's _ramp/_adsr semantics (the frozen composition's
// control path, src/torchsynth_voice/format_sweep.py:325-452, consumed by
// src/torchsynth_voice/fixed_voice.py). One engine instance renders one
// envelope; the tb flow instantiates the registry's six (adsr_1, adsr_2,
// lfo_1_rate_adsr, lfo_2_rate_adsr, lfo_1_amp_adsr, lfo_2_amp_adsr).
//
// Declared domains (no DR-0008 register width is invented here):
// - stage length words: the model's Q16.30 length domain (LENGTH_FORMAT,
//   format_sweep.py:71), 1+16+30 = 47 bits signed; formed host-side at the
//   S1/S2 sites and received in advance (spec/VOICE-CONTRACT.md:71) with
//   the exact pre-quantization zero flags (the model decides the
//   zero-length branch on the exact pre-quantization length, _ramp's
//   `exact_zero`).
// - ramp shadow domain: Q2.60 (format_sweep.py:342-361), 63 bits; the
//   1e-6 ramp epsilon word eps60 is a declared binary64-derived shadow
//   constant, supplied host-side like every shadow site.
// - shape domain: Q2.30 (format_sweep.py:67), 32-bit signed; SHAPE_ONE =
//   2^30.
// - output: C1 audio word Q2.21 (gf180_rtl_constants C1_WIDTH), narrowed
//   once at the model's declared S4 site with C6 half-even rounding and C7
//   saturation.
//
// Declared binary64 shadow sites (open approximation items, the #74
// declaration class): the `**alpha` power. The post-power Q2.30 shape word
// of each ramp row (attack/decay/release) is supplied per tick as
// shadow_attack/decay/release, replayed host-side from the model's own
// binary64 pow + half-even narrowing. Everything else here is exact
// integer RTL arithmetic.
//
// C6 half-even at every narrowing; C7 saturation with counters folded into
// the op_narrows tally.
//
// This module makes no synthesis, layout, signoff, hardware-playback, or
// sound-fidelity claim.

`timescale 1ns/1ps

import gf180_rtl_constants::*;

module adsr_engine #(
    parameter integer INST_ID = 0
) (
    input wire        clk,
    input wire        rst,
    input wire        en,
    input wire        trigger,  // loads stage words, index <- 0 (no retained state)

    // Stage formation outputs, received in advance (S1/S2 host sites).
    input wire signed [46:0] duration_q,  // Q16.30 fractional control samples
    input wire signed [46:0] attack_q,
    input wire signed [46:0] decay_q,    // the model's new_decay (post attack/decay cut)
    input wire signed [46:0] release_q,
    input wire        duration_exact_zero,  // exact pre-quantization keyboard.duration == 0
    input wire        attack_exact_zero,    // exact min(attack, duration) == 0
    input wire        decay_exact_zero,     // exact new_decay length == 0
    input wire        release_exact_zero,   // exact release length == 0

    // Sustain entry word in the C4 MIDI-domain Q10.21; rescaled to the
    // Q2.30 shape domain inside (exact <<9 widening, no rounding).
    input wire signed [C4_WIDTH-1:0] sustain_entry,

    // Declared shadow-domain epsilon word (Q2.60), host-replayed.
    input wire signed [62:0] eps60,

    // Per-tick post-power shape words (Q2.30) from the host shadow replay.
    input wire signed [31:0] shadow_attack,
    input wire signed [31:0] shadow_decay,
    input wire signed [31:0] shadow_release,

    output reg  signed [C1_WIDTH-1:0] env_word,
    output reg                        out_valid,
    // Op counters since rst (op-count conformance surface, DR-0010 #70 row).
    output reg  [31:0] op_mults,    // multiply-class ops
    output reg  [31:0] op_narrows,  // declared narrowing sites
    output reg  [31:0] op_shadow,   // consumed `**alpha` shadow words
    output reg  [31:0] op_rampdivs  // ramp division-class ops (declared extra)
);

    // Model-declared constants (cited above; never register-invented).
    localparam integer CONTROL_SAMPLES = 1764;  // 441 Hz x 4 s control grid
    localparam signed [62:0] SHAPE_ONE = 64'sd1 << 30;  // Q2.30 1.0
    localparam integer IDX_BITS = 11;           // 0..1763
    localparam [IDX_BITS-1:0] IDX_LAST = CONTROL_SAMPLES - 1;

    // --- stage state (loaded at trigger; nothing survives a trigger) ------
    reg signed [46:0] len_a, len_d, len_r, start_d, start_r;
    reg               az, dz, rz;       // exact-zero flags per stage
    reg               active;
    reg [IDX_BITS-1:0] index;

    // Mutation seam for the wrong-sustain-level test: replaced by the tb
    // runner with `-sustain_entry` to plant the fault.
    wire signed [C4_WIDTH-1:0] sustain_eff = sustain_entry;

    // --- exact integer helpers (C6 half-even; no floats) ------------------

    // half-even rounding of a non-negative magnitude divided by 2^shift.
    function [141:0] div_half_even_pow2;
        input [141:0] magnitude;
        input integer shift;
        reg [141:0] q;
        reg [141:0] rem;
        begin
            q   = magnitude >> shift;
            rem = magnitude & ((142'd1 << shift) - 142'd1);
            if ((rem << 142'd1) > (142'd1 << shift))
                q = q + 142'd1;
            else if ((rem << 142'd1) == (142'd1 << shift))
                q = q + (q & 142'd1);  // ties to even
            div_half_even_pow2 = q;
        end
    endfunction

    // Exact half-even division of a non-negative numerator by a positive
    // denominator (the ramp's canonical division): restoring long division
    // over every numerator bit — a subtraction at bit i sets quotient bit
    // i — then the half-even tie decision on the exact remainder (the #71
    // lfo_vca_engine structure; issue #165 corrected the #70 version, which
    // stopped the scan at the denominator's MSB and offset the quotient by
    // 2^den_msb). The caller guarantees quotient <= 2^60 (early clamp
    // below), so the scan starts at bit den_msb + 60. Numerator sized for
    // the Q2.60 shadow domain: (x + eps) << 30 fits in 110 bits.
    function [141:0] div_half_even;
        input [141:0] numerator;
        input [46:0]  denominator;
        reg [141:0] num;
        reg [141:0] quo;
        reg [141:0] rem;
        reg [141:0] den_z;
        reg [141:0] doubled;
        integer den_msb;
        integer i;
        begin
            num = numerator;
            quo = 142'd0;
            rem = 142'd0;
            den_z = {95'd0, denominator};
            den_msb = 0;
            for (i = 0; i < 47; i = i + 1)
                if (denominator[i])
                    den_msb = i;
            for (i = den_msb + 60; i >= 0; i = i - 1) begin
                rem = (rem << 1) | ((num >> i) & 142'd1);
                if (rem >= den_z) begin
                    rem = rem - den_z;
                    quo = quo | (142'd1 << i);
                end
            end
            doubled = rem << 142'd1;
            if (doubled > den_z)
                quo = quo + 142'd1;
            else if (doubled == den_z)
                quo = quo + (quo & 142'd1);  // ties to even
            div_half_even = quo;
        end
    endfunction

    function signed [31:0] saturate_q230;
        input signed [95:0] value;
        begin
            if (value > 96'sd2147483647)
                saturate_q230 = 32'sd2147483647;
            else if (value < -96'sd2147483648)
                saturate_q230 = -32'sd2147483648;
            else
                saturate_q230 = value[31:0];
        end
    endfunction

    function signed [C1_WIDTH-1:0] saturate_q221;
        input signed [95:0] value;
        begin
            if (value > 96'sd8388607)        // 2^23 - 1
                saturate_q221 = 24'sd8388607;
            else if (value < -96'sd8388608)  // -2^23
                saturate_q221 = -24'sd8388608;
            else
                saturate_q221 = value[C1_WIDTH-1:0];
        end
    endfunction

    // --- one ramp row value at the current index --------------------------
    // Mirrors format_sweep._ramp exactly: tilt by start, clamp at zero,
    // (x + eps)/len + eps through the canonical half-even scalar, clamp at
    // one, conditional inversion for positive lengths; zero/degenerate
    // branches decided on (exact_zero, length_q == 0). The quotient is
    // clamped in the wide domain before narrowing (the model's Python
    // integers never truncate; neither may the RTL).
    function [62:0] ramp_value60;
        input [IDX_BITS-1:0] idx;
        input signed [46:0]  length_q;
        input                exact_zero;
        input signed [46:0]  start_q;
        input                inverse;
        input signed [62:0]  eps;
        reg signed [79:0]    x;
        reg signed [79:0]    xeps;
        reg signed [109:0]   numer;
        reg [141:0]          qwide;
        reg [62:0]           value;
        reg                  degenerate;
        begin
            x = (80'sd1 <<< 60) * $signed({21'd0, idx})
              - ($signed({{33{start_q[46]}}, start_q}) <<< 30);
            if (x < 80'sd0)
                x = 80'sd0;
            degenerate = (~exact_zero) & (length_q == 47'sd0);
            if (exact_zero | degenerate) begin
                value = 63'd1 << 60;
                if (degenerate & inverse)
                    value = 63'd0;
            end else begin
                // len60 = length_q << 30. The canonical division's quotient
                // exceeds 2^60 exactly when (x + eps) > length_q << 30, and
                // the model then clamps to one (zero inverted) — decided
                // here without dividing; the divide path therefore always
                // carries a quotient within 2^60.
                xeps = x + $signed({{17{eps[62]}}, eps});
                if (xeps > ($signed({{33{length_q[46]}}, length_q}) <<< 30)) begin
                    value = 63'd1 << 60;
                    if (inverse)
                        value = 63'd0;
                end else begin
                    // ((x + eps) << 30) / length_q, half-even (len60's
                    // factor 2^30 cancels exactly).
                    numer = xeps <<< 30;
                    qwide = div_half_even({32'd0, numer}, length_q);
                    value = qwide[62:0] + eps;
                    if (value > 63'd1 << 60)
                        value = 63'd1 << 60;
                    if (inverse)
                        value = (63'd1 << 60) - value;
                end
            end
            ramp_value60 = value;
        end
    endfunction

    // --- per-tick datapath (combinational over current state) -------------
    wire [62:0] v60_a = ramp_value60(index, len_a, az, 47'sd0, 1'b0, eps60);
    wire [62:0] v60_d = ramp_value60(index, len_d, dz, start_d, 1'b1, eps60);
    wire [62:0] v60_r = ramp_value60(index, len_r, rz, start_r, 1'b1, eps60);

    // factor = (1 - sustain)*decay + sustain: exact Q4.60 products, one
    // declared narrowing back to Q2.30 (site s4.adsr.factor).
    wire signed [95:0] a64 = $signed(shadow_attack);
    wire signed [95:0] d64 = $signed(shadow_decay);
    wire signed [95:0] r64 = $signed(shadow_release);
    wire signed [95:0] s64 = $signed(sustain_eff) <<< 9;  // Q10.21 -> Q2.30 exact

    wire signed [95:0] one_minus_s  = SHAPE_ONE - s64;
    wire signed [95:0] term_d       = one_minus_s * d64;           // mult 1
    wire signed [95:0] term_s       = s64 * SHAPE_ONE;             // mult 2
    wire signed [95:0] factor_num   = term_d + term_s;
    wire signed [95:0] factor_abs   = (factor_num < 96'sd0) ? -factor_num : factor_num;
    wire [95:0]        factor_mag   = div_half_even_pow2({47'd0, factor_abs[94:0]}, 30);
    wire signed [95:0] factor_unsat = (factor_num < 96'sd0)
                                    ? -$signed({1'd0, factor_mag[94:0]})
                                    : $signed({1'd0, factor_mag[94:0]});
    wire signed [31:0] factor_word  = saturate_q230(factor_unsat);

    // envelope = attack * factor * release: exact Q6.90 product, one
    // declared narrowing to the control word (site S4): the model narrows
    // via round(product * ctrl_scale / shape_scale^3) = round(product/2^69).
    wire signed [95:0] prod_ad   = a64 * factor_word;              // mult 3
    wire signed [95:0] env_prod  = prod_ad * r64;                  // mult 4
    wire signed [95:0] env_abs   = (env_prod < 96'sd0) ? -env_prod : env_prod;
    wire [95:0]        env_mag   = div_half_even_pow2({47'd0, env_abs[94:0]} << 21, 90);
    wire signed [95:0] env_unsat = (env_prod < 96'sd0)
                                 ? -$signed({1'd0, env_mag[94:0]})
                                 : $signed({1'd0, env_mag[94:0]});
    wire signed [C1_WIDTH-1:0] env_comb = saturate_q221(env_unsat);

    // --- sequential state --------------------------------------------------
    always @(posedge clk) begin
        if (rst) begin
            active      <= 1'b0;
            index       <= {IDX_BITS{1'b0}};
            env_word    <= {C1_WIDTH{1'b0}};
            out_valid   <= 1'b0;
            op_mults    <= 32'd0;
            op_narrows  <= 32'd0;
            op_shadow   <= 32'd0;
            op_rampdivs <= 32'd0;
        end else if (en) begin
            if (trigger) begin
                // Trigger loads everything; no prior-envelope state survives.
                // Op counters are per-trigger (cleared with the state).
                len_a   <= attack_q;
                len_d   <= decay_q;
                len_r   <= release_q;
                start_d <= attack_q;    // decay tilt start = new_attack
                start_r <= duration_q;  // release tilt start = duration
                az      <= attack_exact_zero;
                dz      <= decay_exact_zero;
                rz      <= release_exact_zero;
                index   <= {IDX_BITS{1'b0}};
                active  <= 1'b1;
                env_word  <= {C1_WIDTH{1'b0}};
                out_valid <= 1'b0;
                op_mults    <= 32'd0;
                op_narrows  <= 32'd0;
                op_shadow   <= 32'd0;
                op_rampdivs <= 32'd0;
            end else if (active) begin
                env_word    <= env_comb;
                out_valid   <= 1'b1;
                op_mults    <= op_mults + 32'd4;
                op_narrows  <= op_narrows + 32'd2;
                op_shadow   <= op_shadow + 32'd3;
                op_rampdivs <= op_rampdivs + 32'd3;
                if (index == IDX_LAST)
                    active <= 1'b0;
                else
                    index <= index + 1'b1;
            end else begin
                out_valid <= 1'b0;
            end
        end
    end

endmodule
