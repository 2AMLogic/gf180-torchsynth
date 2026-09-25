// Bit-exact RTL audio VCA + pre-normalization mixer engine (issue #76).
//
// Reproduces the frozen fixed model's three audio VCA paths, the mixer
// level terms, the 48-bit-class accumulator, the declared narrowing to
// the pre-normalization mix word, and the peak feed bit-exactly. The
// model source is the tail of src/torchsynth_voice/fixed_voice.py's
// per-sample loop plus its mixer.S4 rescale --
//
//   post_vca_1 = sat_C1(narrow_C1(v1 x amp_1))     [site vca_1.S4]
//   post_vca_2 = sat_C1(narrow_C1(v2 x amp_2))     [site vca_2.S4]
//   post_vca_n = sat_C1(narrow_C1(nq x amp_n))     [site vca_3.S4]
//   acc        = post_vca_1*L1 + post_vca_2*L2 + post_vca_n*Ln   (exact,
//                                    Q6.42, NO intermediate rounding)
//   mix        = sat_C1(narrow_C1(acc))            [site mixer.S4]
//   |mix|, running max                             (the #77 peak feed)
//
// -- and nothing else. Declared domains (all widths imported from
// gf180_rtl_constants; none invented here):
//
// - raw_vco_1 / raw_vco_2 / raw_noise: C1 Q2.21 audio-rate source words,
//   the #73 / #74 / #75 lanes' declared outputs (vco_1.raw, vco_2.raw,
//   noise.raw), consumed unchanged.
// - amp_vco_1 / amp_vco_2 / amp_noise: C1 Q2.21 audio-rate amplitude
//   columns, the #72 endpoint-aligned upsample engine's declared outputs
//   (control_upsample.vco_1_amp / vco_2_amp / noise_amp), consumed
//   unchanged: no zero-order hold and no re-blend exists here.
// - level_vco_1 / level_vco_2 / level_noise: C1 Q2.21 S1 entry words for
//   the OBSERVED PHYSICAL mixer levels, loaded at trigger. The recorded
//   upstream mixer input curves ([1.0, 1.0, 0.025] -- spec/FLOAT-MIX.md)
//   are already folded into those measured physical values at the
//   measured-observation seam, so the noise lane's gain enters this
//   engine through exactly the same entry site as the two oscillator
//   gains. No curve, renormalization, or per-lane gain law is
//   reimplemented in RTL: a wrong curve is a wrong fed level word, and
//   the tb's gain mutations demonstrate that it is caught.
//
// Arithmetic is integer RTL under the accepted register: C1 Q2.21 words,
// 24x24 -> 48-bit products (DR-0008 Section 2), a 48-bit-class Q6.42
// accumulator with NO intermediate rounding before the declared
// narrowing, C6 half-even at every declared narrowing (magnitude
// rounding, sign reapplied -- the model's canonical scalar), and C7
// saturation with sticky counters at all four declared sites. There are
// NO shadow sites in this lane: no transcendental and no binary64 value
// participates anywhere, so this lane is trace-load-bearing in full.
//
// The Q6.42 accumulator band is a model CONTRACT, not a saturation site:
// the frozen model refuses (raises) if the three-term sum leaves it. With
// |post_vca| <= 2^23 and |level| <= 2^23 the sum is bounded by 3*2^46 <
// 2^48, so it provably cannot happen; op_acc_faults exports the measured
// count anyway and the tb hard-asserts it to be zero (an unrun check is
// never a pass).
//
// Op counters since trigger (the DR-0010 #76 owner row: "audio rate: 6
// mults + 2 adds + 4 narrow sites; 48-bit-class accumulator; S4 rescale
// to the pre-normalization mix word", with the module table's split of
// "3x audio VCA: 3 mults / 0 adds / 3 narrows" + "Mixer (level mults,
// accumulate, S4): 3 mults / 2 adds / 1 narrow"): op_mults counts the
// three VCA products and the three level products (6/sample); op_adds
// counts the two accumulator adds (2/sample); op_narrows counts the three
// VCA narrowings and the mixer narrowing (4/sample); op_sats and
// op_rounds are the measured sticky C7 saturation and C6 rounding events
// across those same four sites.
//
// op_peak_cmps counts the peak-feed compare separately and is NOT folded
// into op_adds: DR-0010 assigns "pass 1 peak tracking (1 compare + 1
// abs-select folded into the mixer output)" to #77's owner row, so the
// feed is exported and reported here but charged there. This module
// implements NO part of the C9 normalization replay -- no peak>1 branch,
// no U1.22 reciprocal, no S5 gain multiply. peak_word is the
// pre-normalization magnitude maximum (strict >, so the earliest maximal
// sample wins, exactly fixed_voice.normalize_words) offered to #77.
//
// This module makes no synthesis, layout, signoff, hardware-playback, or
// sound-fidelity claim.

`timescale 1ns/1ps

import gf180_rtl_constants::*;

module audio_mix_engine (
    input  wire                        clk,
    input  wire                        rst,
    input  wire                        en,
    input  wire                        trigger,  // loads the level words

    // Trigger-time C1 Q2.21 S1 entry words (observed physical levels).
    input  wire signed [C1_WIDTH-1:0]  level_vco_1,
    input  wire signed [C1_WIDTH-1:0]  level_vco_2,
    input  wire signed [C1_WIDTH-1:0]  level_noise,

    // Audio-rate streams (one word per enabled sample).
    input  wire signed [C1_WIDTH-1:0]  raw_vco_1,
    input  wire signed [C1_WIDTH-1:0]  raw_vco_2,
    input  wire signed [C1_WIDTH-1:0]  raw_noise,
    input  wire signed [C1_WIDTH-1:0]  amp_vco_1,
    input  wire signed [C1_WIDTH-1:0]  amp_vco_2,
    input  wire signed [C1_WIDTH-1:0]  amp_noise,

    output reg  signed [C1_WIDTH-1:0]  post_vca_1,  // vco_1.post_vca
    output reg  signed [C1_WIDTH-1:0]  post_vca_2,  // vco_2.post_vca
    output reg  signed [C1_WIDTH-1:0]  post_vca_n,  // noise.post_vca
    output reg  signed [C1_WIDTH-1:0]  mix_word,    // mixer.pre_normalization
    output reg         [C1_WIDTH-1:0]  mix_abs,     // |mix_word| (peak feed)
    output reg         [C1_WIDTH-1:0]  peak_word,   // running peak (for #77)
    output reg                         out_valid,
    // Op counters since trigger (op-count conformance surface).
    output reg  [31:0] op_mults,      // multiply-class ops (6 per sample)
    output reg  [31:0] op_adds,       // accumulator adds (2 per sample)
    output reg  [31:0] op_narrows,    // declared narrowings (4 per sample)
    output reg  [31:0] op_sats,       // sticky C7 saturations (measured)
    output reg  [31:0] op_rounds,     // sticky C6 rounding events (measured)
    output reg  [31:0] op_peak_cmps,  // peak-feed compares (#77's row)
    output reg  [31:0] op_acc_faults  // Q6.42 band faults (must stay 0)
);

    // Model-declared constants (cited above; never register-invented).
    localparam integer AUDIO_SAMPLES = 176400;  // 44.1 kHz x 4 s one-shot grid
    localparam integer IDX_BITS = 18;           // 0..176399
    localparam [IDX_BITS-1:0] IDX_LAST = AUDIO_SAMPLES - 1;
    // The model's mixer accumulator format: signed Q6.42 for C1 Q2.21
    // operands (fixed_voice.py's product_fmt), i.e. 2*C1_FRAC_BITS
    // fractional bits and the DR-0010 48-bit-class integer band.
    localparam integer ACC_FRAC_BITS = 2 * C1_FRAC_BITS;   // 42
    localparam integer ACC_INT_BITS  = 6;                  // Q6.42
    localparam integer ACC_WIDTH     = 1 + ACC_INT_BITS + ACC_FRAC_BITS;  // 49
    // 96-bit signed working band (helpers below compute at this width).
    localparam signed [95:0] C1_MAX96  = 96'sd8388607;   // (1<<23)-1
    localparam signed [95:0] C1_MIN96  = -96'sd8388608;  // -(1<<23)
    localparam signed [95:0] ACC_MAX96 = (96'sd1 << (ACC_WIDTH - 1)) - 96'sd1;
    localparam signed [95:0] ACC_MIN96 = -(96'sd1 << (ACC_WIDTH - 1));

    // --- run state (loaded at trigger; nothing survives a trigger) --------
    reg signed [C1_WIDTH-1:0] lvl1_r, lvl2_r, lvln_r;
    reg                       active;
    reg [IDX_BITS-1:0]        index;

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

    // The model's canonical narrowing scalar: round the MAGNITUDE half-even
    // and reapply the sign (fixedpoint.rounding.div_round_reported).
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

    // A declared narrowing site reports a C6 rounding event iff the
    // discarded remainder was nonzero (div_round_reported's `rounded`),
    // independently of whether the quotient moved.
    function rounding_event;
        input signed [95:0] numer;
        input integer shift;
        reg signed [95:0] mag;
        begin
            mag = (numer < 96'sd0) ? -numer : numer;
            rounding_event = ((mag & ((96'd1 << shift) - 96'd1)) != 96'd0);
        end
    endfunction

    // --- the three audio VCA paths (one declared narrowing each) ----------

    // Mutation seam for the gain/route tests: the tb runner rewrites these
    // three level selections (swap, curve-dropped, dropped source) and
    // requires detection.
    wire signed [C1_WIDTH-1:0] lvl1_eff = lvl1_r;
    wire signed [C1_WIDTH-1:0] lvl2_eff = lvl2_r;
    wire signed [C1_WIDTH-1:0] lvln_eff = lvln_r;

    wire signed [95:0] vca1_prod  = $signed(raw_vco_1) * $signed(amp_vco_1);
    wire signed [95:0] vca2_prod  = $signed(raw_vco_2) * $signed(amp_vco_2);
    wire signed [95:0] vcan_prod  = $signed(raw_noise) * $signed(amp_noise);

    wire signed [95:0] vca1_narrow =
        div_half_even_pow2_signed(vca1_prod, C1_FRAC_BITS);
    wire signed [95:0] vca2_narrow =
        div_half_even_pow2_signed(vca2_prod, C1_FRAC_BITS);
    wire signed [95:0] vcan_narrow =
        div_half_even_pow2_signed(vcan_prod, C1_FRAC_BITS);

    wire vca1_round = rounding_event(vca1_prod, C1_FRAC_BITS);
    wire vca2_round = rounding_event(vca2_prod, C1_FRAC_BITS);
    wire vcan_round = rounding_event(vcan_prod, C1_FRAC_BITS);

    wire vca1_sat = (vca1_narrow > C1_MAX96) || (vca1_narrow < C1_MIN96);
    wire vca2_sat = (vca2_narrow > C1_MAX96) || (vca2_narrow < C1_MIN96);
    wire vcan_sat = (vcan_narrow > C1_MAX96) || (vcan_narrow < C1_MIN96);

    wire signed [95:0] vca1_eff = vca1_sat
        ? ((vca1_narrow > 96'sd0) ? C1_MAX96 : C1_MIN96) : vca1_narrow;
    // Mutation seam for the polarity test: the tb runner flips the sign of
    // the vco_2 VCA output and requires detection localized to that lane.
    wire signed [95:0] vca2_eff = vca2_sat
        ? ((vca2_narrow > 96'sd0) ? C1_MAX96 : C1_MIN96) : vca2_narrow;
    wire signed [95:0] vcan_eff = vcan_sat
        ? ((vcan_narrow > 96'sd0) ? C1_MAX96 : C1_MIN96) : vcan_narrow;

    // --- mixer: three level products, exact Q6.42 accumulate, one narrow --
    // 24x24 -> 48-bit products; NO intermediate rounding before the single
    // declared narrowing site (DR-0008 Section 2 / DR-0010 A8).
    wire signed [95:0] term1 = vca1_eff * $signed(lvl1_eff);
    wire signed [95:0] term2 = vca2_eff * $signed(lvl2_eff);
    wire signed [95:0] termn = vcan_eff * $signed(lvln_eff);

    // Mutation seam for the dropped-source test: the tb runner removes the
    // noise term from the accumulation and requires detection.
    wire signed [95:0] acc_sum = term1 + term2 + termn;

    // The declared Q6.42 band is a model contract, not a saturation site.
    wire acc_fault = (acc_sum > ACC_MAX96) || (acc_sum < ACC_MIN96);

    // Mutation seam for the truncation test: the tb runner replaces the
    // half-even narrowing with a truncating shift and requires detection.
    wire signed [95:0] mix_narrow =
        div_half_even_pow2_signed(acc_sum, C1_FRAC_BITS);
    wire               mix_round = rounding_event(acc_sum, C1_FRAC_BITS);
    wire               mix_sat   = (mix_narrow > C1_MAX96)
                                || (mix_narrow < C1_MIN96);
    // Mutation seam for the dropped-saturation test: the tb runner lets the
    // word wrap instead of saturating and requires detection.
    wire signed [95:0] mix_eff = mix_sat
        ? ((mix_narrow > 96'sd0) ? C1_MAX96 : C1_MIN96) : mix_narrow;

    // Mutation seam for the DC-offset test: the tb runner adds a constant
    // to the emitted mix word and requires detection.
    wire signed [95:0] mix_out = mix_eff;

    // --- pre-normalization peak feed (#77's compare, exported here) -------
    wire signed [95:0] mix_mag96 = (mix_out < 96'sd0) ? -mix_out : mix_out;
    wire [C1_WIDTH-1:0] mix_mag  = mix_mag96[C1_WIDTH-1:0];
    // Strict greater-than: ties keep the earliest maximal sample, exactly
    // fixed_voice.normalize_words' reduction.
    wire peak_gt = (mix_mag > peak_word);

    // --- cycle-registered state -------------------------------------------
    always @(posedge clk or posedge rst) begin
        if (rst) begin
            active        <= 1'b0;
            index         <= {IDX_BITS{1'b0}};
            post_vca_1    <= {C1_WIDTH{1'b0}};
            post_vca_2    <= {C1_WIDTH{1'b0}};
            post_vca_n    <= {C1_WIDTH{1'b0}};
            mix_word      <= {C1_WIDTH{1'b0}};
            mix_abs       <= {C1_WIDTH{1'b0}};
            peak_word     <= {C1_WIDTH{1'b0}};
            out_valid     <= 1'b0;
            op_mults      <= 32'd0;
            op_adds       <= 32'd0;
            op_narrows    <= 32'd0;
            op_sats       <= 32'd0;
            op_rounds     <= 32'd0;
            op_peak_cmps  <= 32'd0;
            op_acc_faults <= 32'd0;
        end else begin
            out_valid <= 1'b0;
            if (trigger) begin
                index         <= {IDX_BITS{1'b0}};
                active        <= 1'b1;
                peak_word     <= {C1_WIDTH{1'b0}};
                op_mults      <= 32'd0;
                op_adds       <= 32'd0;
                op_narrows    <= 32'd0;
                op_sats       <= 32'd0;
                op_rounds     <= 32'd0;
                op_peak_cmps  <= 32'd0;
                op_acc_faults <= 32'd0;
            end else if (en && active) begin
                post_vca_1 <= vca1_eff[C1_WIDTH-1:0];
                post_vca_2 <= vca2_eff[C1_WIDTH-1:0];
                post_vca_n <= vcan_eff[C1_WIDTH-1:0];
                mix_word   <= mix_out[C1_WIDTH-1:0];
                mix_abs    <= mix_mag;
                if (peak_gt)
                    peak_word <= mix_mag;
                out_valid  <= 1'b1;
                op_mults   <= op_mults   + 32'd6;
                op_adds    <= op_adds    + 32'd2;
                op_narrows <= op_narrows + 32'd4;
                op_sats    <= op_sats
                            + {31'd0, vca1_sat}
                            + {31'd0, vca2_sat}
                            + {31'd0, vcan_sat}
                            + {31'd0, mix_sat};
                op_rounds  <= op_rounds
                            + {31'd0, vca1_round}
                            + {31'd0, vca2_round}
                            + {31'd0, vcan_round}
                            + {31'd0, mix_round};
                op_peak_cmps  <= op_peak_cmps  + 32'd1;
                op_acc_faults <= op_acc_faults + {31'd0, acc_fault};
                if (index == IDX_LAST) begin
                    active <= 1'b0;
                end else begin
                    index <= index + {{(IDX_BITS-1){1'b0}}, 1'b1};
                end
            end
        end
    end

    // Trigger-cycle level-word load (words must be stable at the trigger
    // edge, like the #70/#71/#72/#73 per-trigger control words).
    always @(posedge clk) begin
        if (trigger) begin
            lvl1_r <= level_vco_1;
            lvl2_r <= level_vco_2;
            lvln_r <= level_noise;
        end
    end

endmodule
