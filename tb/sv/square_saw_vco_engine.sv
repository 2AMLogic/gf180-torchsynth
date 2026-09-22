// Bit-exact RTL square/saw VCO engine (issue #74).
//
// Reproduces the frozen fixed model's vco_2 lane bit-exactly:
// ``FixedVoiceModel.render``'s square/saw oscillator block
// (src/torchsynth_voice/fixed_voice.py, the m2/p2/v2 dataflow), with the
// DR-0010 #74 owner row's exact-integer sites in RTL and the declared
// binary64 shadow sites replayed host-side as deterministic words.
//
// DECLARED SHADOW BOUNDARY (DR-0008/DR-0010 open approximation items, the
// #74 declaration class -- replayed words, NO RTL transcendental exists
// here and none is claimed):
// - +fq_word: the per-sample midi->Hz ``440*2**((m/2**21-69)/12)`` exp2
//   shadow, already narrowed to its Q16.15 word host-side
//   (vco.midi_to_hz.exp2.shadow);
// - +partials_word: the per-clip ``partials_constant`` shadow, already
//   narrowed to its s14.17 word host-side (vco_2.partials_constant.shadow);
// - +square_q_word/+left_q_word: the per-sample ``tanh`` distortion shadow
//   and its single-rounding S4a fanout, already narrowed host-side
//   (vco_2.tanh.shadow). square_q is the model's declared dead-fanout site
//   (computed, never consumed); it is replayed for boundary completeness
//   and is intentionally unread below.
// The RTL computes m2 itself and exports it: the host harness asserts the
// replayed shadow words were derived from exactly this pitch word.
//
// EXACT INTEGER SITES OWNED HERE (all widths from gf180_rtl_constants):
// - the depth-mod multiply and saturated pitch sum (C4 Q10.21, C6
//   half-even, C7 saturate + sticky counter);
// - the [0, 127<<21] MIDI clamp (exact integer, DR-0008 Section 3 order);
// - the phase-increment formation k = half_even(fq * 2^32 /
//   (C3_scale * 44100)) -- a general exact-integer half-even division by
//   the model's constant denominator (no float, no approximation);
// - the C2 u32 wrapping phase accumulator (wrapping is semantics, never an
//   error), seeded by the S3-replayed initial-turn word at trigger;
// - two C5 quarter-wave LUT evaluations (cos at the post-step phase, sin a
//   quarter turn behind), exact integer linear interpolation per C6
//   (quarter_wave_lut, the issue #68 format-true path);
// - the driven multiply: half_even(partials * sin2 / 2^22) into s14.17,
//   C7 saturate -- the exact quantized operand the model feeds its tanh;
// - the right-branch narrowing: half_even(2^43 + shape * cos2, / 2^22)
//   into C1, C7 saturate -- the exact rational form of the model's
//   ``1 + (shape/2^21)*(cos2/2^22)`` binary64 expression;
// - the final combine: half_even(left_q * right_q / 2^21) into C1, C7
//   saturate (the model's ``vco_2.S4`` site).
//
// Op counters since trigger (the DR-0010 #74 owner row's counted ops,
// sample-exact against the model-derived expectations): op_mults counts
// the depth-mod, driven, right-shape, combine and the two LUT interp
// multiply-class products; op_adds counts the pitch-sum, phase, interp and
// right-branch add/sub-class ops; op_narrows counts the five declared
// narrowing sites plus the two interpolation roundings; op_sats counts
// sticky saturation events across the declared C7 sites.
//
// This module makes no synthesis, layout, signoff, hardware-playback, or
// sound-fidelity claim.

`timescale 1ns/1ps

import gf180_rtl_constants::*;

module square_saw_vco_engine (
    input wire        clk,
    input wire        rst,
    input wire        en,
    input wire        trigger,  // loads the run's static words + initial phase

    // Static C4 Q10.21 entry words (loaded at trigger).
    input wire signed [C4_WIDTH-1:0] midi_f0_word,
    input wire signed [C4_WIDTH-1:0] tuning_word,
    input wire signed [C4_WIDTH-1:0] depth_word,
    input wire signed [C4_WIDTH-1:0] shape_word,
    // Declared shadow replays (see header): s14.17 partials constant,
    // C2 initial-turn word, per-sample Q16.15 exp2 word, per-sample Q2.21
    // tanh fanout words.
    input wire signed [31:0]         partials_word,
    input wire [C2_WIDTH-1:0]        init_phase_word,

    // Per-tick streamed words.
    input wire signed [C1_WIDTH-1:0] up_pitch_word,  // Q2.21 pitch column
    input wire signed [C3_WIDTH-1:0] fq_word,        // exp2-shadow word
    input wire signed [C1_WIDTH-1:0] square_q_word,  // tanh fanout (unread)
    input wire signed [C1_WIDTH-1:0] left_q_word,    // tanh fanout

    // Per-tick exported streams (bit patterns; host applies the format).
    output reg  signed [C4_WIDTH-1:0] m2_word,       // clamped pitch word
    output reg  signed [31:0]         driven_word,   // s14.17
    output reg  signed [C1_WIDTH-1:0] right_q_word,  // C1
    output reg  signed [C1_WIDTH-1:0] v2_word,       // vco_2.raw (C1)
    output reg                        out_valid,
    // Op counters since trigger.
    output reg  [31:0] op_mults,
    output reg  [31:0] op_adds,
    output reg  [31:0] op_narrows,
    output reg  [31:0] op_sats
);

    // Model-declared constants (never register-invented).
    localparam signed [95:0] C4_MAX = 96'sd2147483647;       //  2^31 - 1
    localparam signed [95:0] C4_MIN = -96'sd2147483648;      // -2^31
    localparam signed [95:0] C1_SAT_HI = 96'sd8388607;       // (1<<23)-1
    localparam signed [95:0] C1_SAT_LO = -96'sd8388608;      // -(1<<23)
    localparam signed [95:0] MIDI_CLAMP_MAX_WORD = 96'sd266338304; // 127 << 21
    localparam signed [95:0] FREQ_DEN = 96'sd1445068800;     // (1<<15) * 44100
    // The model's sin read is ((p - 2^30) mod 2^32); added mod 2^32 this is
    // p + 3*2^30 (0xC000_0000), not p + 2^30.
    localparam [C2_WIDTH-1:0] PHASE_QUARTER = 32'hC0000000;

    // --- run state --------------------------------------------------------
    reg signed [C4_WIDTH-1:0] s_midi_f0, s_tuning, s_depth, s_shape;
    reg signed [31:0]         s_partials;
    reg [C2_WIDTH-1:0]        phase;
    reg                       active;

    // --- exact integer helpers (C6 half-even; no floats) ------------------

    // Signed half-even narrowing of a 96-bit value by 2^shift (the model's
    // canonical scalar: round the magnitude, reapply the sign).
    function signed [95:0] div_half_even_pow2_signed;
        input signed [95:0] numer;
        input integer shift;
        reg signed [95:0] mag;
        reg [95:0] q;
        reg [95:0] rem;
        begin
            mag = (numer < 96'sd0) ? -numer : numer;
            q   = mag >> shift;
            rem = mag & ((96'd1 << shift) - 96'd1);
            if ((rem << 96'd1) > (96'd1 << shift))
                q = q + 96'd1;
            else if ((rem << 96'd1) == (96'd1 << shift))
                q = q + (q & 96'd1);  // ties to even
            div_half_even_pow2_signed =
                (numer < 96'sd0) ? -$signed(q) : $signed(q);
        end
    endfunction

    // General exact half-even division of a non-negative numerator by a
    // non-negative denominator (restoring long division; the model computes
    // k through the exact Fraction scalar div_round). The loop walks the
    // dividend's low 64 bits: |fq| < 2^31 so |fq * 2^32| < 2^63 and every
    // quotient bit is formed within the walked range (the guard comment at
    // k_numer below; stimulus outside that domain cannot come from the
    // accepted model and is caught by the harness's mirror comparison).
    function [95:0] udiv_half_even;
        input [95:0] num;
        input [95:0] den;
        reg [95:0] q;
        reg [95:0] r;
        integer i;
        begin
            q = 96'd0;
            r = 96'd0;
            for (i = 63; i >= 0; i = i - 1) begin
                r = (r << 1) | num[i];
                q = q << 1;
                if (r >= den) begin
                    r = r - den;
                    q = q | 96'd1;
                end
            end
            if ((r << 96'd1) > den)
                q = q + 96'd1;
            else if ((r << 96'd1) == den)
                q = q + (q & 96'd1);  // ties to even
            udiv_half_even = q;
        end
    endfunction

    // C7 saturation into C4 (sQ10.21) or C1; the caller counts the event.
    function signed [95:0] sat_c4;
        input signed [95:0] value;
        begin
            if (value > C4_MAX)
                sat_c4 = C4_MAX;
            else if (value < C4_MIN)
                sat_c4 = C4_MIN;
            else
                sat_c4 = value;
        end
    endfunction

    function signed [95:0] sat_c1;
        input signed [95:0] value;
        begin
            if (value > C1_SAT_HI)
                sat_c1 = C1_SAT_HI;
            else if (value < C1_SAT_LO)
                sat_c1 = C1_SAT_LO;
            else
                sat_c1 = value;
        end
    endfunction

    // --- combinational sample datapath ------------------------------------

    wire signed [95:0] depth_prod = $signed(s_depth) * $signed(up_pitch_word);
    wire signed [95:0] depth_mod  = div_half_even_pow2_signed(depth_prod, 21);
    wire signed [95:0] m2_wide     = $signed(s_midi_f0) + s_tuning + depth_mod;
    wire signed [95:0] m2_sat      = sat_c4(m2_wide);
    wire        m2_sat_event       = (m2_wide > C4_MAX) || (m2_wide < C4_MIN);
    // The model's [0, 127<<21] clamp (exact integer, no counter).
    wire signed [95:0] m2_clamped  = (m2_sat < 96'sd0)
        ? 96'sd0
        : ((m2_sat > MIDI_CLAMP_MAX_WORD) ? MIDI_CLAMP_MAX_WORD : m2_sat);

    // Phase-increment formation: k = half_even(fq * 2^32 / FREQ_DEN).
    // fq is a positive Q16.15 word in the accepted model (Hz > 0), so the
    // magnitude path is the signed path; the guard keeps a non-positive
    // replayed word well-defined (it can only come from a corrupt stimulus).
    wire signed [95:0] k_numer = $signed(fq_word) * $signed(96'sd1 << 32);
    wire [95:0] k_mag = (k_numer < 96'sd0) ? -k_numer : k_numer;
    wire [95:0] k_u   = udiv_half_even(k_mag, FREQ_DEN);
    wire signed [95:0] k_val = (k_numer < 96'sd0)
        ? -$signed(k_u) : $signed(k_u);

    wire [C2_WIDTH-1:0] phase_next = phase + k_val[C2_WIDTH-1:0];  // C2 wrap

    // Two C5 evaluations: cos at the post-step phase, sin a quarter turn
    // behind ((p - 2^30) mod 2^32 == p + 2^30 mod 2^32).
    wire signed [C5_ENTRY_WIDTH-1:0] cos2;
    wire signed [C5_ENTRY_WIDTH-1:0] sin2;
    quarter_wave_lut lut_cos (.phase(phase_next),                 .entry_word(cos2));
    quarter_wave_lut lut_sin (.phase(phase_next + PHASE_QUARTER), .entry_word(sin2));

    // driven = half_even(partials * sin2 / 2^22) into s14.17, C7 saturate.
    wire signed [95:0] driven_prod = $signed(s_partials) * $signed(sin2);
    wire signed [95:0] driven_val  = div_half_even_pow2_signed(driven_prod, 22);
    wire signed [95:0] driven_sat  = sat_c4(driven_val);  // s14.17 spans 32 bits
    wire        driven_sat_event   = (driven_val > C4_MAX) || (driven_val < C4_MIN);

    // right = half_even(2^43 + shape * cos2, / 2^22) into C1, C7 saturate.
    wire signed [95:0] right_prod = $signed(s_shape) * $signed(cos2);
    wire signed [95:0] right_numer = (96'sd1 << 43) + right_prod;
    wire signed [95:0] right_val   = div_half_even_pow2_signed(right_numer, 22);
    wire signed [95:0] right_sat   = sat_c1(right_val);
    wire        right_sat_event    = (right_val > C1_SAT_HI) || (right_val < C1_SAT_LO);

    // Final combine: half_even(left_q * right_q / 2^21) into C1, C7.
    wire signed [95:0] v2_prod = $signed(left_q_word) * right_sat;
    wire signed [95:0] v2_val  = div_half_even_pow2_signed(v2_prod, 21);
    wire signed [95:0] v2_sat  = sat_c1(v2_val);
    wire        v2_sat_event   = (v2_val > C1_SAT_HI) || (v2_val < C1_SAT_LO);

    always @(posedge clk or posedge rst) begin
        if (rst) begin
            active     <= 1'b0;
            phase      <= {C2_WIDTH{1'b0}};
            m2_word    <= {C4_WIDTH{1'b0}};
            driven_word<= 32'd0;
            right_q_word <= {C1_WIDTH{1'b0}};
            v2_word    <= {C1_WIDTH{1'b0}};
            out_valid  <= 1'b0;
            op_mults   <= 32'd0;
            op_adds    <= 32'd0;
            op_narrows <= 32'd0;
            op_sats    <= 32'd0;
        end else begin
            out_valid <= 1'b0;
            if (trigger) begin
                active      <= 1'b1;
                s_midi_f0   <= midi_f0_word;
                s_tuning    <= tuning_word;
                s_depth     <= depth_word;
                s_shape     <= shape_word;
                s_partials  <= partials_word;
                phase       <= init_phase_word;
                op_mults    <= 32'd0;
                op_adds     <= 32'd0;
                op_narrows  <= 32'd0;
                op_sats     <= 32'd0;
            end else if (en && active) begin
                m2_word      <= m2_clamped[C4_WIDTH-1:0];
                driven_word  <= driven_sat[31:0];
                right_q_word <= right_sat[C1_WIDTH-1:0];
                v2_word      <= v2_sat[C1_WIDTH-1:0];
                out_valid    <= 1'b1;
                phase        <= phase_next;
                op_mults     <= op_mults + 32'd8;
                op_adds      <= op_adds + 32'd8;
                op_narrows   <= op_narrows + 32'd7;
                op_sats      <= op_sats
                              + {31'd0, m2_sat_event}
                              + {31'd0, driven_sat_event}
                              + {31'd0, right_sat_event}
                              + {31'd0, v2_sat_event};
            end
        end
    end

endmodule
