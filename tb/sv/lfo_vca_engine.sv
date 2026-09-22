// Bit-exact RTL LFO + control-rate VCA engine (issue #71).
//
// Reproduces the frozen fixed model's LFO and control-VCA integer dataflow
// bit-exactly: the fixed control path's _lfo/_lfo_blend/_control_vca
// semantics (the frozen composition's control path,
// src/torchsynth_voice/format_sweep.py:456-561, consumed by
// src/torchsynth_voice/fixed_voice.py). One engine instance renders one
// logical LFO plus its control-rate VCA; the tb flow instantiates the
// registry's two (lfo_1, lfo_2).
//
// Declared domains (no DR-0008 register width is invented here):
// - entry words: C4 MIDI-domain Q10.21 (frequency, mod_depth),
//   imported from gf180_rtl_constants (C4_WIDTH).
// - rate formation: the declared S2-class site rounds
//   (freq + depth * rate_env) into the model's Q16.30 length domain
//   (format_sweep.py:71) with C6 half-even; the modulated rate clamps at
//   zero BEFORE phase accumulation (the s4.lfo.rate_clamp saturation
//   counter, format_sweep.py:498-501).
// - phase: C2 u32 wrapping accumulator (32 bits, never-saturate); the
//   increment K = round((rate / 441) * 2^32) half-even
//   (format_sweep.py:502-507); the phase accumulates the first increment
//   BEFORE the initial phase is added (sample zero is not the bare
//   initial phase, format_sweep.py:508-509).
// - initial-phase word: the declared S3-style initial-turn formation site
//   (initial_phase entry -> turns over the pinned binary64 2*pi ->
//   half-even u32 word, format_sweep.py:473-479) is formed host-side and
//   received in advance, like #70's S1/S2 length words (the pinned 2*pi
//   literal is a software constant of the model, not an RTL value).
// - shape domain: Q2.30 (format_sweep.py:67), 32-bit signed; SHAPE_ONE =
//   2^30. The five shapes at one u32 argument (format_sweep.py:518-541):
//   sin = (1 - cos x) / 2 from the C5 quarter-wave table (Q1.23 entry
//   widened <<7, exact), saw = the u32 phase over 2^32, rsaw = 1 - saw,
//   tri = 2*saw reflected above the mid turn (t = 1 kept), sqr =
//   (1 - sign(cos x)) / 2 (0.5 at exact zero).
// - shape weights: the declared binary64 shadow site w ** 2.718281828
//   (the literal, not math.e) with math.fsum normalization
//   (format_sweep.py:459-471) is an open approximation item (the #74
//   declaration class): the five post-normalization Q2.30 weight words
//   are supplied per trigger, replayed host-side from the model's own
//   recipe (torchsynth_voice.lfo_golden.weight_shadow). Everything else
//   here is exact integer RTL arithmetic.
// - streams: the rate and amplitude envelopes arrive as Q2.21 control
//   words (C1 shape, the #70 engines' declared outputs) — the control-VCA
//   amplitude input is a declared stream interface
//   (spec/VOICE-CONTRACT.md; "amplitude envelopes input").
// - output: the C1 audio word Q2.21 (C1_WIDTH), narrowed once per site
//   with C6 half-even rounding and C7 saturation; the control VCA is one
//   Q2.21 x Q2.21 multiply with a single declared narrowing
//   (format_sweep.py:545-561).
//
// C6 half-even at every narrowing; C7 saturation with counters folded
// into the per-site tallies. The blend accumulate is exact Q4.60 before
// its single declared narrowing.
//
// This module makes no synthesis, layout, signoff, hardware-playback, or
// sound-fidelity claim.

`timescale 1ns/1ps

import gf180_rtl_constants::*;

module lfo_vca_engine #(
    parameter integer INST_ID = 0
) (
    input wire        clk,
    input wire        rst,
    input wire        en,
    input wire        trigger,  // loads the run's control words, phase <- 0

    // Host-formed control words (declared sites, received in advance).
    input wire signed [C4_WIDTH-1:0] freq_word,   // Q10.21 base frequency in Hz
    input wire signed [C4_WIDTH-1:0] depth_word,  // Q10.21 rate-modulation depth
    input wire [C2_WIDTH-1:0] init_word,          // S3 initial-turns u32 word
    input wire signed [31:0] w_sin,               // Q2.30 shadow-replayed weights,
    input wire signed [31:0] w_tri,               // model order (sin, tri, saw,
    input wire signed [31:0] w_saw,               // rsaw, sqr), normalized
    input wire signed [31:0] w_rsaw,
    input wire signed [31:0] w_sqr,

    // Per-tick envelope streams (Q2.21 control words, #70 engine outputs).
    input wire signed [C1_WIDTH-1:0] rate_env,    // rate-ADSR output (Hz role)
    input wire signed [C1_WIDTH-1:0] gain,        // amp-ADSR output (VCA gain)

    output reg  signed [C1_WIDTH-1:0] raw_word,   // lfo_<n>.raw
    output reg  signed [C1_WIDTH-1:0] post_word,  // lfo_<n>.post_control_vca
    output reg                        out_valid,
    // Op counters since trigger (op-count conformance surface, the
    // DR-0010 #71 owner row).
    output reg  [31:0] op_mults,    // multiply-class ops
    output reg  [31:0] op_narrows,  // declared narrowing sites
    output reg  [31:0] op_phases,   // phase-step applications
    output reg  [31:0] op_clamps    // sticky rate-clamp (saturation) events
);

    // Model-declared constants (cited above; never register-invented).
    localparam integer CONTROL_SAMPLES = 1764;  // 441 Hz x 4 s control grid
    localparam signed [95:0] SHAPE_ONE = 96'sd1 << 30;  // Q2.30 1.0
    localparam integer IDX_BITS = 11;           // 0..1763
    localparam [IDX_BITS-1:0] IDX_LAST = CONTROL_SAMPLES - 1;
    localparam [31:0] PHASE_MID = 32'h8000_0000;

    // --- run state (loaded at trigger; nothing survives a trigger) --------
    reg signed [C4_WIDTH-1:0] freq_r, depth_r;
    reg [C2_WIDTH-1:0]        init_r;
    reg signed [31:0]         w0, w1, w2, w3, w4;
    reg [C2_WIDTH-1:0]        phase;   // accumulated AFTER each increment
    reg                       active;
    reg [IDX_BITS-1:0]        index;

    // Mutation seam for the wrong-shape-weight test: replaced by the tb
    // runner with the one-hot selector to plant the fault.
    wire signed [31:0] w_eff0 = w0;
    wire signed [31:0] w_eff1 = w1;
    wire signed [31:0] w_eff2 = w2;
    wire signed [31:0] w_eff3 = w3;
    wire signed [31:0] w_eff4 = w4;

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

    // Exact half-even division of a non-negative numerator by a positive
    // denominator (the increment's canonical division by 441): restoring
    // long division over every numerator bit — a subtraction at bit i
    // sets quotient bit i — then the half-even tie decision on the exact
    // remainder.
    function [95:0] div_half_even;
        input [95:0] numerator;
        input [31:0] denominator;
        reg [95:0] num;
        reg [95:0] quo;
        reg [95:0] rem;
        reg [95:0] den_z;
        reg [95:0] doubled;
        integer den_msb;
        integer i;
        begin
            num = numerator;
            den_z = {64'd0, denominator};
            quo = 96'd0;
            rem = 96'd0;
            den_msb = 0;
            for (i = 0; i < 32; i = i + 1)
                if (denominator[i])
                    den_msb = i;
            for (i = den_msb + 60; i >= 0; i = i - 1) begin
                rem = (rem << 1) | ((num >> i) & 96'd1);
                if (rem >= den_z) begin
                    rem = rem - den_z;
                    quo = quo | (96'd1 << i);
                end
            end
            doubled = rem << 96'd1;
            if (doubled > den_z)
                quo = quo + 96'd1;
            else if (doubled == den_z)
                quo = quo + (quo & 96'd1);  // ties to even
            div_half_even = quo;
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

    // --- per-tick datapath (combinational over current state) -------------

    // C5-shape quarter-wave table ROM (the LFO's table as instantiated by
    // the frozen control path): format_sweep.LUT_ENTRY_FORMAT is Q1.23
    // (format_sweep.py:72), a 25-bit signed word (values [-2^23, 2^23)
    // over scale 2^23) — deliberately distinct from the accepted C5
    // audio-path table's 24-bit C5_ENTRY_WIDTH. Index C5_N_ENTRIES holds
    // the quarter-turn endpoint entry (the reflected r == 0 neighbor of
    // the last entry).
    reg signed [24:0] rom [0:C5_N_ENTRIES];
    reg [1023:0] lut_file;
    initial begin
        if (!$value$plusargs("lut=%s", lut_file)) begin
            $display("DUT-ERROR inst %0d missing +lut=<memh path> plusarg", INST_ID);
            $finish;
        end
        $readmemh(lut_file, rom);
    end

    localparam integer QUADRANT_BITS    = C5_PHASE_BITS - 2;
    localparam integer ENTRY_FRAC_BITS  = C5_ENTRY_WIDTH - 2;  // signed Q1.<f>

    // Rate formation: numer = freq * 2^21 + depth * rate_env over the
    // common denominator entry_scale * ctrl_scale, one declared narrowing
    // into the Q16.30 length word (format_sweep.py:490-497): the exact
    // rational times 2^30 is numer / 2^42 * 2^30 = numer / 2^12.
    wire signed [95:0] depth_term = $signed(depth_r) * $signed(rate_env);
    wire signed [95:0] rate_numer = $signed(freq_r) * (96'sd1 << 21) + depth_term;
    wire signed [95:0] rate_rounded = div_half_even_pow2_signed(rate_numer, 12);
    // The modulated rate clamps at zero BEFORE accumulation; the event is
    // a declared sticky saturation counter (format_sweep.py:498-501).
    wire signed [46:0] rate_signed = rate_rounded[46:0];
    wire               rate_clamped = rate_signed < 47'sd0;
    wire [46:0]        rate_eff = rate_clamped ? 47'd0 : rate_signed;

    // Declared increment site: K = round((rate / 441) * 2^32). The rate
    // word is Q16.30, so (rate << 2) / 441 (format_sweep.py:502-507).
    wire [95:0] increment = div_half_even({49'd0, rate_eff << 2}, 32'd441);

    // Phase accumulates this tick's increment FIRST, then the initial
    // phase is added for the argument (format_sweep.py:508-509): sample
    // zero is not the bare initial phase. Wrapping is C2 semantics.
    wire [C2_WIDTH-1:0] phase_next  = phase + increment[C2_WIDTH-1:0];
    wire [C2_WIDTH-1:0] argument    = phase_next + init_r;

    // --- C5 table evaluation at the argument (model-exact op order) ------
    wire [1:0]                quadrant = argument[C2_WIDTH-1 -: 2];
    wire [QUADRANT_BITS-1:0]  r_raw    = argument[QUADRANT_BITS-1:0];
    wire                      reflect  = quadrant[0];
    wire                      negate   = quadrant[1] ^ quadrant[0];
    // Reflected r can equal exactly 2^QUADRANT_BITS -> needs one extra bit.
    wire [QUADRANT_BITS:0]    r = reflect
        ? ((97'd1 << QUADRANT_BITS) - r_raw)
        : {1'b0, r_raw};
    wire [C5_INDEX_BITS:0]    i = r[QUADRANT_BITS:C5_INTERP_BITS];
    wire [C5_INTERP_BITS-1:0] t = r[C5_INTERP_BITS-1:0];

    wire signed [24:0] a = rom[i[C5_INDEX_BITS-1:0]];
    wire [C5_INDEX_BITS:0]  i_next = i + 1'b1;
    wire signed [24:0] b = (i == C5_N_ENTRIES - 1)
        ? rom[C5_N_ENTRIES]
        : rom[i_next[C5_INDEX_BITS-1:0]];
    wire endpoint_hit = (i == C5_N_ENTRIES);

    // Integer-exact linear interpolation: (a*2^I + (b-a)*t) / 2^I, half-even.
    // Quarter-cos entries are non-negative, so the numerator is >= 0.
    // t is zero-extended before the signed reinterpretation (an 18-bit
    // $signed(t) would go negative above half scale).
    wire signed [95:0] t_ext = $signed({7'd0, t});
    wire signed [95:0] interp_num = $signed(a) * (96'sd1 << C5_INTERP_BITS)
                                  + ($signed(b) - $signed(a)) * t_ext;
    wire [95:0] entry_interp_u = div_half_even_pow2(interp_num, C5_INTERP_BITS);
    wire signed [24:0] interp_word = entry_interp_u[24:0];
    wire signed [24:0] lut_word = endpoint_hit
        ? rom[C5_N_ENTRIES]
        : interp_word;
    // Mutation seam for the wrong-shape-table test: the tb runner plants
    // the fault here (quadrant negation dropped -> corrupted sine table).
    wire signed [24:0] entry_word = negate ? -lut_word : lut_word;

    // --- the five pinned shapes at one u32 argument, Q2.30 ---------------
    wire signed [24:0] cos_q23 = entry_word;      // Q1.23
    wire signed [95:0] cos_q30 = $signed(cos_q23) <<< 7;        // exact widen
    wire signed [95:0] sign96 = (cos_q23 > 25'sd0) ? 96'sd1
                              : (cos_q23 < 25'sd0) ? -96'sd1 : 96'sd0;
    // sin = (1 - cos x) / 2: numerator in (0, 3*2^30], non-negative.
    wire signed [95:0] sin_num = SHAPE_ONE - cos_q30;
    wire [95:0] sin_q30 = div_half_even_pow2(sin_num, 1);
    // saw = the u32 phase over 2^32 = argument / 4, half-even.
    wire [95:0] saw_q30 = div_half_even_pow2({64'd0, argument}, 2);
    wire [95:0] rsaw_q30 = SHAPE_ONE - saw_q30;
    // tri = 2*saw, reflected to 2 - 2*saw above the mid turn; t = 1 kept
    // at the exact mid. The model divides the raw u32 phase by 2
    // (tri = phase/2^31 of a turn, mirrored), a shift of ONE.
    wire [33:0] tri_num = (argument > PHASE_MID)
        ? ({2'd0, 32'd4294967295} + 34'd1 - argument)  // 2^32 - argument
        : argument;
    wire [95:0] tri_q30 = div_half_even_pow2({62'd0, tri_num}, 1);
    // sqr = (1 - sign(cos x)) / 2: 1 where cos < 0, 0.5 at exact zero.
    wire [95:0] sqr_num = (sign96 < 96'sd0) ? (SHAPE_ONE << 1)
                        : (sign96 == 96'sd0) ? SHAPE_ONE : 96'd0;
    wire [95:0] sqr_q30 = div_half_even_pow2(sqr_num, 1);

    // --- continuous blend: exact Q4.60 accumulate, one declared narrowing
    // (format_sweep.py:536-541). Model weight order is
    // (sin, tri, saw, rsaw, sqr) against _lfo_shapes' return order.
    wire signed [95:0] merged =
        $signed(w_eff0) * $signed(sin_q30)
      + $signed(w_eff1) * $signed(tri_q30)
      + $signed(w_eff2) * $signed(saw_q30)
      + $signed(w_eff3) * $signed(rsaw_q30)
      + $signed(w_eff4) * $signed(sqr_q30);
    wire [95:0] blend_q30 = div_half_even_pow2(merged, 30);

    // One declared narrowing to the C1 control word (site s4.lfo.<side>):
    // round(blend / 2^9), C7 saturation.
    wire signed [95:0] raw_unsat = $signed(div_half_even_pow2(blend_q30, 9));
    wire signed [C1_WIDTH-1:0] raw_comb = saturate_q221(raw_unsat);

    // --- control-rate VCA: one multiply, one declared narrowing ----------
    // (format_sweep.py:545-561: mul(wave, ctrl, gain, ctrl -> ctrl), the
    // exact Q2.21 x Q2.21 product narrowed once through the canonical
    // scalar with C7 saturation.)
    wire signed [95:0] vca_prod = $signed(raw_comb) * $signed(gain);
    wire signed [95:0] vca_rounded = div_half_even_pow2_signed(vca_prod, C1_FRAC_BITS);
    wire signed [C1_WIDTH-1:0] post_comb = saturate_q221(vca_rounded);

    // --- sequential state --------------------------------------------------
    always @(posedge clk) begin
        if (rst) begin
            active    <= 1'b0;
            phase     <= {C2_WIDTH{1'b0}};
            index     <= {IDX_BITS{1'b0}};
            raw_word  <= {C1_WIDTH{1'b0}};
            post_word <= {C1_WIDTH{1'b0}};
            out_valid <= 1'b0;
            op_mults   <= 32'd0;
            op_narrows <= 32'd0;
            op_phases  <= 32'd0;
            op_clamps  <= 32'd0;
        end else if (en) begin
            if (trigger) begin
                // Trigger loads everything; no prior-LFO state survives.
                // Op counters are per-trigger (cleared with the state).
                freq_r  <= freq_word;
                depth_r <= depth_word;
                init_r  <= init_word;
                w0 <= w_sin; w1 <= w_tri; w2 <= w_saw; w3 <= w_rsaw; w4 <= w_sqr;
                phase     <= {C2_WIDTH{1'b0}};
                index     <= {IDX_BITS{1'b0}};
                active    <= 1'b1;
                raw_word  <= {C1_WIDTH{1'b0}};
                post_word <= {C1_WIDTH{1'b0}};
                out_valid <= 1'b0;
                op_mults   <= 32'd0;
                op_narrows <= 32'd0;
                op_phases  <= 32'd0;
                op_clamps  <= 32'd0;
            end else if (active) begin
                raw_word  <= raw_comb;
                post_word <= post_comb;
                out_valid <= 1'b1;
                op_mults   <= op_mults + 32'd6;
                op_narrows <= op_narrows + 32'd10;
                op_phases  <= op_phases + 32'd1;
                if (rate_clamped)
                    op_clamps <= op_clamps + 32'd1;
                phase <= phase_next;
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
