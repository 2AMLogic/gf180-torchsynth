// Bit-exact RTL sine VCO engine (issue #73).
//
// Reproduces the frozen fixed model's sine source lane (vco_1)
// bit-exactly: src/torchsynth_voice/fixed_voice.py's per-sample loop —
//
//   m1   = sat_C4(midi_f0 + tuning + sat_C4(narrow_C4(depth x up_pitch)))
//   m1   = clamp_midi(m1)                       (MIDI_CLAMP_MIN=0, MAX=127)
//   [shadow] hz = 440 * exp2((m/2^21 - 69)/12)  (binary64, host-derived)
//   fq   = half_even(hz * 2^15)                 (C3 Q16.15 word, host-fed)
//   k    = half_even(fq * 2^32 / (2^15 * 44100))
//   p1   = (p1 + k) mod 2^32                    (C2 u32 wrapping; the LUT
//                                               reads the POST-step phase:
//                                               first-increment-first)
//   v1   = sat_C1(rescale_C5entry_to_C1(cosc_lut(p1)))
//
// with the initial phase injected ONCE per trigger as a turn word
// (half_even(turns * 2^32) mod 2^32) before the first increment.
//
// Declared domains (all widths imported from gf180_rtl_constants; none
// invented here):
// - midi_f0/tuning/depth: C4 Q10.21 entry words (loaded at trigger).
// - up_pitch: C1 Q2.21 audio-rate pitch-modulation column, the #72
//   endpoint-aligned upsample engine's declared output (consumed
//   unchanged; the blend lives upstream).
// - fq_word: C3 Q16.15 shadow frequency word. The midi->Hz exp2 is
//   DR-0008's declared binary64 shadow site (vco.midi_to_hz.exp2.shadow,
//   an open approximation item): the host mirror derives the quantized
//   word per sample and feeds it in — exactly the #158 anchor-flow
//   pattern. No exp2 is implemented in RTL and no RTL claim is made for
//   the shadow site. Consequence, stated honestly: while the shadow is
//   host-replayed, the pitch path's own dataflow effect on the TRACE
//   (f -> fq -> K) completes host-side; the in-RTL pitch path (depth
//   product, pitch sum, clamp) is the DR-0010 #73 owner-row conformance
//   surface whose observable behavior today is the exported op-counter
//   property rows (clamps/saturations, asserted against the mirror's
//   measured counts), and it becomes trace-load-bearing when #74's
//   declared fixed exp2 approximation lands. The pitch path's
//   bit-exactness against the frozen model is proven at the host mirror
//   (pinned to the receipt's frozen vco_1.raw digests).
// - Everything else is integer RTL under the accepted register: C2 u32
//   wrapping (never-saturate word), C5 4096x24 quarter-wave LUT + linear
//   interpolation (12 index + 18 interp bits; the table payload is the
//   hash-linked accepted content, +lut=<memh>), C6 half-even at every
//   declared narrowing (magnitude rounding, sign reapplied), C7
//   saturation with sticky counters. The MIDI clamp is the model's own
//   registered clamp (a formatting clamp, not a Nyquist clamp).
//
// Op counters since trigger (the DR-0010 #73 owner rows: "vco_1 pitch
// path (depth-mod, clamp, MIDI->Hz, Q16.15 word, phase increment): 3
// mults / 6 adds / 3 narrows / 1 exp2" + "vco_1 phase + quarter-wave LUT
// + S4: 1 mult / 2 adds / 1 narrow"): op_mults counts the depth-mod
// product, the K division, and the interpolation product (3/sample; the
// 4th owner-row mult is the declared host shadow site, counted from the
// consumed fq stream as op_shadows); op_adds counts the 2 pitch-sum adds,
// the 2 clamp compares, the phase add, and the 2 interpolation adds
// (7/sample within the 8 cap); op_narrows counts the depth, K,
// interpolation, and S4 narrowings (4/sample).
//
// This module makes no synthesis, layout, signoff, hardware-playback, or
// sound-fidelity claim.

`timescale 1ns/1ps

import gf180_rtl_constants::*;

module sine_vco_engine (
    input  wire                        clk,
    input  wire                        rst,
    input  wire                        en,
    input  wire                        trigger,  // loads params + initial phase

    // Trigger-time C4 Q10.21 entry words (the S1 entry sites).
    input  wire [C4_WIDTH-1:0]         midi_f0_word,
    input  wire [C4_WIDTH-1:0]         tuning_word,
    input  wire [C4_WIDTH-1:0]         depth_word,
    // C2 turn word: half_even(initial_turns * 2^32) mod 2^32.
    input  wire [C2_WIDTH-1:0]         initial_phase_word,

    // Audio-rate streams (one word per enabled sample).
    input  wire signed [C1_WIDTH-1:0]  up_pitch,   // #72 upsample column, Q2.21
    input  wire signed [C3_WIDTH-1:0]  fq_word,    // host shadow word, Q16.15

    output reg  signed [C1_WIDTH-1:0]  vco_word,   // vco_1.raw, Q2.21
    output reg  [C2_WIDTH-1:0]         phase_out,  // post-step phase
    output reg                         out_valid,
    // Op counters since trigger (op-count conformance surface).
    output reg  [31:0] op_mults,    // multiply-class ops (3 per sample)
    output reg  [31:0] op_adds,     // adds/compares (7 per sample)
    output reg  [31:0] op_narrows,  // declared narrowing sites (4 per sample)
    output reg  [31:0] op_shadows,  // consumed shadow sites (1 per sample)
    output reg  [31:0] op_sats,     // sticky saturation events (C4 sum + S4)
    output reg  [31:0] op_clamps    // measured MIDI clamp events
);

    // Model-declared constants (cited above; never register-invented).
    localparam integer AUDIO_SAMPLES = 176400;  // 44.1 kHz x 4 s one-shot grid
    localparam integer IDX_BITS = 18;           // 0..176399
    localparam [IDX_BITS-1:0] IDX_LAST = AUDIO_SAMPLES - 1;
    // 96-bit signed working band (helpers below compute at this width).
    localparam signed [95:0] C4_MAX96 = 96'sd2147483647;   // (1<<31)-1
    localparam signed [95:0] C4_MIN96 = -96'sd2147483648;  // -(1<<31)
    localparam signed [95:0] C1_MAX96 = 96'sd8388607;      // (1<<23)-1
    localparam signed [95:0] C1_MIN96 = -96'sd8388608;     // -(1<<23)
    // MIDI clamp band: [0, 127] in the C4 Q10.21 word domain.
    localparam signed [95:0] MIDI_CLAMP_MIN_WORD = 96'sd0;
    localparam signed [95:0] MIDI_CLAMP_MAX_WORD = 96'sd266338304;  // 127<<21
    // K denominator: freq_scale * fs = 2^C3_FRAC_BITS * 44100.
    localparam signed [95:0] FREQ_DENOMINATOR =
        (96'sd1 << C3_FRAC_BITS) * 96'sd44100;

    // --- run state (loaded at trigger; nothing survives a trigger) --------
    reg signed [C4_WIDTH-1:0] f0_r, tun_r, dep_r;
    reg [C2_WIDTH-1:0]        phase;
    reg                       active;
    reg [IDX_BITS-1:0]        index;

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

    localparam integer QUADRANT_BITS   = C5_PHASE_BITS - 2;
    localparam integer ENTRY_FRAC_BITS = C5_ENTRY_WIDTH - 2;  // signed Q1.<f>
    // entry-format -> audio-format narrowing shift (frac_bits difference).
    localparam integer VCO_RESCALE_SHIFT = ENTRY_FRAC_BITS - C1_FRAC_BITS;

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

    // Half-even division of a non-negative numerator by an arbitrary
    // positive denominator (the K site: fq * 2^32 / (2^15 * 44100)).
    function [95:0] div_half_even_den;
        input [95:0] magnitude;
        input [95:0] denominator;
        reg [95:0] q;
        reg [95:0] rem;
        begin
            q   = magnitude / denominator;
            rem = magnitude % denominator;
            if ((rem << 96'd1) > denominator)
                q = q + 96'd1;
            else if ((rem << 96'd1) == denominator)
                q = q + (q & 96'd1);  // ties to even
            div_half_even_den = q;
        end
    endfunction

    // --- per-sample integer dataflow (combinational, model-exact order) ---

    // S1 depth-mod product: depth (C4) x up_pitch (C1), ONE declared
    // narrowing to C4, C7 saturation.
    wire signed [95:0] depth_prod = $signed(dep_r) * $signed(up_pitch);
    wire signed [95:0] depth_term = div_half_even_pow2_signed(depth_prod, C4_FRAC_BITS);
    wire               depth_sat  = (depth_term > C4_MAX96) || (depth_term < C4_MIN96);
    wire signed [95:0] depth_eff  = depth_sat
        ? ((depth_term > 96'sd0) ? C4_MAX96 : C4_MIN96)
        : depth_term;

    // Pitch sum: midi_f0 + tuning + depth term, C7 saturation at C4.
    wire signed [95:0] pitch_sum = $signed(f0_r) + $signed(tun_r) + depth_eff;
    wire               sum_sat   = (pitch_sum > C4_MAX96) || (pitch_sum < C4_MIN96);
    wire signed [95:0] sum_eff   = sum_sat
        ? ((pitch_sum > 96'sd0) ? C4_MAX96 : C4_MIN96)
        : pitch_sum;

    // The model's own MIDI clamp (a formatting clamp; Nyquist clamps are
    // forbidden per C7). Mutation seam for the un-clamped-pitch test: the
    // tb runner drops the upper clamp arm and requires detection on the
    // depth-driven case.
    wire signed [95:0] m_eff = (sum_eff < MIDI_CLAMP_MIN_WORD)
        ? MIDI_CLAMP_MIN_WORD
        : ((sum_eff > MIDI_CLAMP_MAX_WORD) ? MIDI_CLAMP_MAX_WORD : sum_eff);

    // K site: half_even(fq * 2^32 / (2^15 * 44100)). The clamp keeps
    // m >= 0, so hz > 0 and fq >= 0; the magnitude path is kept anyway.
    wire signed [95:0] fq_sext   = $signed(fq_word);
    wire               fq_negative = (fq_sext < 96'sd0);
    wire [95:0]        fq_mag     = fq_negative ? (96'd0 - fq_sext) : fq_sext;
    wire [95:0]        k_mag      = div_half_even_den(fq_mag << 32, FREQ_DENOMINATOR);
    wire [C2_WIDTH-1:0] k_word    = fq_negative
        ? (96'd0 - k_mag)
        : k_mag[C2_WIDTH-1:0];

    // C2 wrapping phase: first-increment-first — the LUT reads the
    // POST-step phase.
    wire [C2_WIDTH-1:0] phase_next = phase + k_word;

    // --- C5 quarter-wave LUT + linear interpolation on the post-step phase
    wire [1:0]                quadrant = phase_next[C2_WIDTH-1 -: 2];
    wire [QUADRANT_BITS-1:0]  r_raw    = phase_next[QUADRANT_BITS-1:0];
    wire                      reflect  = quadrant[0];
    wire                      negate   = quadrant[1] ^ quadrant[0];
    // Reflected r can equal exactly 2^QUADRANT_BITS -> needs one extra bit.
    wire [QUADRANT_BITS:0]    r = reflect
        ? ((96'd1 << QUADRANT_BITS) - r_raw)
        : {1'b0, r_raw};
    wire [C5_INDEX_BITS:0]    i = r[QUADRANT_BITS:C5_INTERP_BITS];
    wire [C5_INTERP_BITS-1:0] t = r[C5_INTERP_BITS-1:0];

    // Mutation seam for the wrong-LUT-address test: the tb runner shifts
    // the primary table read one entry up and requires detection.
    wire [C5_INDEX_BITS-1:0] a_index = i[C5_INDEX_BITS-1:0];
    wire signed [C5_ENTRY_WIDTH-1:0] a = rom[a_index];
    wire [C5_INDEX_BITS:0]  i_next = i + 1'b1;
    wire signed [C5_ENTRY_WIDTH-1:0] b = (i == C5_N_ENTRIES - 1)
        ? rom[C5_N_ENTRIES]
        : rom[i_next[C5_INDEX_BITS-1:0]];
    wire endpoint_hit = (i == C5_N_ENTRIES);

    // Integer-exact linear interpolation: (a*2^I + (b-a)*t) / 2^I,
    // half-even. Quarter-cos entries are non-negative, so the numerator
    // is >= 0.
    wire signed [95:0] a64 = a;
    wire signed [95:0] b64 = b;
    wire signed [95:0] t64 = t;
    wire signed [95:0] interp_num = a64 * (96'sd1 << C5_INTERP_BITS)
                                  + (b64 - a64) * t64;
    wire [95:0] entry_interp_u = div_half_even_pow2(interp_num, C5_INTERP_BITS);
    wire signed [C5_ENTRY_WIDTH-1:0] interp_word = entry_interp_u[C5_ENTRY_WIDTH-1:0];
    wire signed [C5_ENTRY_WIDTH-1:0] lut_word = endpoint_hit
        ? rom[C5_N_ENTRIES]
        : interp_word;
    wire signed [C5_ENTRY_WIDTH-1:0] entry_word = negate ? -lut_word : lut_word;

    // --- S4: entry format -> audio format rescale (one declared site) ----
    wire signed [95:0] entry64   = entry_word;
    wire signed [95:0] entry_mag = (entry64 < 96'sd0) ? -entry64 : entry64;
    wire [95:0]        vco_mag   = div_half_even_pow2(entry_mag, VCO_RESCALE_SHIFT);
    wire signed [95:0] vco_unsat = (entry64 < 96'sd0)
        ? -$signed(vco_mag)
        : $signed(vco_mag);
    wire               vco_sat_ev = (vco_unsat > C1_MAX96) || (vco_unsat < C1_MIN96);
    wire signed [95:0] vco_sat    = vco_sat_ev
        ? ((vco_unsat > 96'sd0) ? C1_MAX96 : C1_MIN96)
        : vco_unsat;

    // --- cycle-registered state ------------------------------------------
    always @(posedge clk or posedge rst) begin
        if (rst) begin
            phase      <= {C2_WIDTH{1'b0}};
            active     <= 1'b0;
            index      <= {IDX_BITS{1'b0}};
            vco_word   <= {C1_WIDTH{1'b0}};
            phase_out  <= {C2_WIDTH{1'b0}};
            out_valid  <= 1'b0;
            op_mults   <= 32'd0;
            op_adds    <= 32'd0;
            op_narrows <= 32'd0;
            op_shadows <= 32'd0;
            op_sats    <= 32'd0;
            op_clamps  <= 32'd0;
        end else begin
            out_valid <= 1'b0;
            if (trigger) begin
                phase      <= initial_phase_word;
                index      <= {IDX_BITS{1'b0}};
                active     <= 1'b1;
                op_mults   <= 32'd0;
                op_adds    <= 32'd0;
                op_narrows <= 32'd0;
                op_shadows <= 32'd0;
                op_sats    <= 32'd0;
                op_clamps  <= 32'd0;
            end else if (en && active) begin
                phase     <= phase_next;
                phase_out <= phase_next;
                // Mutation seam for the dropped-phase-increment test: the
                // tb runner holds the phase instead of stepping it and
                // requires detection.
                vco_word  <= vco_sat[C1_WIDTH-1:0];
                out_valid <= 1'b1;
                op_mults   <= op_mults   + 32'd3;
                op_adds    <= op_adds    + 32'd7;
                op_narrows <= op_narrows + 32'd4;
                op_shadows <= op_shadows + 32'd1;
                op_sats    <= op_sats
                            + {31'd0, depth_sat}
                            + {31'd0, sum_sat}
                            + {31'd0, vco_sat_ev};
                op_clamps  <= op_clamps
                            + {31'd0, sum_eff < MIDI_CLAMP_MIN_WORD}
                            + {31'd0, (sum_eff >= MIDI_CLAMP_MIN_WORD)
                                      && (sum_eff > MIDI_CLAMP_MAX_WORD)};
                if (index == IDX_LAST) begin
                    active <= 1'b0;
                end else begin
                    index <= index + {{(IDX_BITS-1){1'b0}}, 1'b1};
                end
            end
        end
    end

    // Trigger-cycle parameter load (words must be stable at the trigger
    // edge, like the #70/#71/#72 per-trigger control words).
    always @(posedge clk) begin
        if (trigger) begin
            f0_r  <= midi_f0_word;
            tun_r <= tuning_word;
            dep_r <= depth_word;
        end
    end

endmodule
