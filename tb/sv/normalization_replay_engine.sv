// normalization_replay_engine.sv — normalization replay controller + the
// one-shot top's own internal two-pass sequencer (issue #77).
//
// DR-0010's module table assigns this issue exactly one owner row:
// "Normalization replay controller + one-shot top | #77 | two-pass schedule
// (P4): pass 1 peak tracking (1 compare + 1 abs-select folded into the
// mixer output), branch decision at sample 176,400, pass 2 replay with the
// U1.22 gain multiply + S5 narrow (normalized branch) or identity (unity
// branch)" (spec/decision-records/0010-one-shot-rtl-microarchitecture.md:155).
// The replay-vs-buffering cost evidence that selected two-pass re-render
// over a 516.8 KiB clip buffer is the same record's "Normalization replay
// architecture (P4 — decided)" section (same file, lines 259-307): the
// only per-clip storage this module carries is live scalar state (the
// running peak, the latched branch decision, the U1.22 gain word, the
// pass/sample counters) — never a full-clip sample buffer.
//
// Reference algorithm (bit-exact target): the frozen fixed-point model's
// ``normalize_words()`` (src/torchsynth_voice/fixed_voice.py:195-235) —
// peak is the max magnitude over the pre-normalization Q2.21 mix; the
// strict branch is ``peak_int > 2^21``; on the divide branch the gain word
// is ``div_round(2^(C1_FRAC_BITS + C9_FRAC_BITS), peak, HALF_EVEN)`` (U1.22,
// half-even at site S5) and every sample is narrowed once through the
// declared ``mul(...)`` primitive with saturation; on the bypass branch the
// gain word is unity (``1 << C9_FRAC_BITS``) and the input bytes pass
// through unchanged. This module reproduces that integer dataflow
// bit-exactly; no float ever participates.
//
// Declared interface note (dependency on issue #76, not yet landed): the
// pre-normalization mix word stream (``mix_in``) is DR-0010's declared
// mixer-output interface ("1 compare + 1 abs-select folded into the mixer
// output") — the audio VCA + mixer engine (#76) is this stream's eventual
// upstream RTL producer. Until #76 lands, this module is exercised at its
// own declared boundary: the host/testbench feeds the pre-normalization
// mix word stream directly (computed by the frozen fixed model's own
// ``FixedVoiceModel.render()``, or by the isolated directed case grid),
// exactly re-fed for pass 2 as DR-0003/DR-0010 require. No RTL claim is
// made about #76's eventual interface shape beyond the single Q2.21 word
// per sample DR-0010 already declares.
//
// Clip lifecycle contract (issue #63 AC, DR-0010 "Clip lifecycle"): one
// trigger (``start``) binds one render; pass 1 and pass 2 of that render
// (and no others) may write ``audio_out``; output release begins only
// after the branch decision (``audio_out_valid`` is asserted only in
// PASS2). ``rst`` discards all render state and clears the sticky error —
// there is no resumable render. ``abort`` returns to idle and discards the
// live render state (peak/gain/branch/counters-this-render) WITHOUT
// clearing a raised sticky error or the op counters exported "since rst" —
// a fault is never silently hidden by a later trigger, mirroring the #75
// noise-stream engine's sticky-error-register convention
// (tb/sv/noise_stream_dut.sv, tb/README.md's noise section). Backpressure
// is host-side only: the core never blocks mid-clip on its own.
//
// Framing (AC3 — exactly 176,400 samples, no stale/missing/duplicate
// sample): the host feeds ``mix_valid`` for exactly
// ``SCHED_SAMPLES_PER_PASS`` cycles per pass, then (with ``mix_valid``
// low) pulses ``mix_done`` for one cycle — the #75 noise engine's
// byte_valid/in_done handshake shape, at sample instead of byte
// granularity. A ``mix_valid`` beyond the declared count (before
// ``mix_done``) raises the sticky ``ERR_SAMPLE_OVERRUN``; a ``mix_done``
// short of the declared count raises the sticky ``ERR_PASS_TRUNCATED``.
// Once raised, further ``mix_valid``/``mix_done`` are ignored until
// ``rst``.
//
// Numeric mechanics: peak tracking is sign+magnitude compare (an
// unconditional abs-select every pass-1 sample, DR-0010's declared "1
// compare + 1 abs-select"); the reciprocal gain word is formed ONCE per
// clip by a combinational restoring long division (the same
// sign+magnitude, half-even-at-the-remainder technique
// tb/sv/adsr_engine.sv's ``div_half_even`` already uses, generalized to an
// unsigned numerator/denominator here since both operands are always
// non-negative); the pass-2 gain application is one signed x unsigned
// product narrowed once through a half-even shift-divide (a power-of-two
// divisor, C9_FRAC_BITS, so no general division is needed there) with C7
// saturation and a sticky counter. No float and no multiplier-class
// transcendental participates anywhere in this module.
//
// This module makes no synthesis, layout, signoff, hardware-playback, or
// sound-fidelity claim; PDK-free plain SystemVerilog for Icarus Verilog 13
// (-g2012): no interfaces, no classes, no vendor cells.

`timescale 1ns/1ps

import gf180_rtl_constants::*;

module normalization_replay_engine (
    input  wire clk,
    input  wire rst,     // synchronous, active-high: discards ALL state

    input  wire start,   // idle/done, no sticky error: begin a new render
    input  wire abort,   // any state: -> idle, discard live render state

    input  wire signed [C1_WIDTH-1:0] mix_in,
    input  wire        mix_valid,  // one pre-normalization mix sample
    input  wire        mix_done,   // host: this pass's sample count is fed

    output reg  signed [C1_WIDTH-1:0] audio_out,
    output reg          audio_out_valid,

    output reg          busy,
    output reg          done,
    output reg          error,
    output reg  [7:0]   error_code,

    // 0 idle, 1 pass1 (peak tracking), 2 pass2 (replay), 3 done
    output reg  [1:0]   pass_index,
    output reg  [17:0]  samples_this_pass,

    output reg           branch_normalized,   // latched at the branch decision
    output reg  [C1_WIDTH-1:0] peak_word,      // running/final peak magnitude
    output reg  [C9_WIDTH-1:0] gain_word,      // U1.22; unity on the bypass branch

    // Op counters since rst (op-count conformance surface, DR-0010 #77 row).
    output reg  [31:0] op_compares,     // pass-1 peak compares (1/sample)
    output reg  [31:0] op_selects,      // pass-1 abs-selects (1/sample)
    output reg  [31:0] op_recip_divs,   // per-clip reciprocal divisions (0 or 1)
    output reg  [31:0] op_mults,        // pass-2 gain multiplies (normalized branch)
    output reg  [31:0] op_narrows,      // pass-2 declared narrowings (normalized branch)
    output reg  [31:0] op_saturations   // sticky S5 saturation count
);

    localparam [C1_WIDTH-1:0] UNITY_INT  = (1 << C1_FRAC_BITS);   // Q2.21 1.0
    localparam [C9_WIDTH-1:0] UNITY_GAIN = (1 << C9_FRAC_BITS);   // U1.22 1.0

    localparam [7:0] ERR_NONE            = 8'd0;
    localparam [7:0] ERR_SAMPLE_OVERRUN  = 8'd1;
    localparam [7:0] ERR_PASS_TRUNCATED  = 8'd2;

    localparam [1:0] P_IDLE  = 2'd0;
    localparam [1:0] P_PASS1 = 2'd1;
    localparam [1:0] P_PASS2 = 2'd2;
    localparam [1:0] P_DONE  = 2'd3;

    // The once-per-clip reciprocal numerator: 2^(C1_FRAC_BITS + C9_FRAC_BITS)
    // (site S5's ``div_round(1 << (audio.frac_bits + gain.frac_bits), peak,
    // HALF_EVEN)``), sized generously (64 bits) so no width edge case can
    // silently truncate it.
    localparam [63:0] RECIP_NUMER = (64'd1 << (C1_FRAC_BITS + C9_FRAC_BITS));

    localparam signed [63:0] C1_MAX64 = (64'sd1 <<< (C1_WIDTH - 1)) - 64'sd1;
    localparam signed [63:0] C1_MIN64 = -(64'sd1 <<< (C1_WIDTH - 1));

    // --- sign+magnitude of the incoming mix sample -------------------------
    wire signed [C1_WIDTH:0] mix_in_ext = {mix_in[C1_WIDTH-1], mix_in};
    wire [C1_WIDTH:0]        mix_mag_wide =
        mix_in[C1_WIDTH-1] ? (~mix_in_ext + { {C1_WIDTH{1'b0}}, 1'b1 })
                            : mix_in_ext;
    wire [C1_WIDTH-1:0]      mix_mag = mix_mag_wide[C1_WIDTH-1:0];

    // --- once-per-clip reciprocal: restoring long division, half-even -----
    // Generalizes tb/sv/adsr_engine.sv's div_half_even to an unsigned
    // numerator/denominator (both operands are magnitudes here, never
    // negative); a full 64-bit scan is combinational headroom, not a
    // per-sample cost — this function fires exactly once per clip, at the
    // pass-1/pass-2 boundary.
    function [63:0] div_half_even_u;
        input [63:0] numerator;
        input [63:0] denominator;
        reg [63:0] quo;
        reg [63:0] rem;
        integer i;
        begin
            quo = 64'd0;
            rem = 64'd0;
            for (i = 63; i >= 0; i = i - 1) begin
                rem = (rem << 1) | ((numerator >> i) & 64'd1);
                if (rem >= denominator) begin
                    rem = rem - denominator;
                    quo = quo | (64'd1 << i);
                end
            end
            if ((rem << 1) > denominator)
                quo = quo + 64'd1;
            else if ((rem << 1) == denominator)
                quo = quo + (quo & 64'd1);  // ties to even
            div_half_even_u = quo;
        end
    endfunction

    wire [63:0] recip_quo =
        div_half_even_u(RECIP_NUMER, {{(64 - C1_WIDTH){1'b0}}, peak_word});

    // --- pass-2 S5 narrow: mix_in x gain_word, half-even, saturate --------
    // gain_word is always non-negative (U1.22); the product's magnitude is
    // rounded (power-of-two divisor C9_FRAC_BITS, shift-based half-even —
    // no general division needed here) and the sign reapplied, exactly the
    // #75 noise engine's sign+magnitude narrowing shape.
    wire signed [2*C1_WIDTH-1:0] mul_product =
        $signed(mix_in) * $signed({1'b0, gain_word});
    wire                         mul_neg = mul_product[2*C1_WIDTH-1];
    wire [2*C1_WIDTH-1:0]        mul_mag =
        mul_neg ? (~mul_product + 1'b1) : mul_product;
    wire [2*C1_WIDTH-1:0]        mul_q   = mul_mag >> C9_FRAC_BITS;
    wire [2*C1_WIDTH-1:0]        mul_rem =
        mul_mag & ((({{(2*C1_WIDTH-1){1'b0}}, 1'b1}) << C9_FRAC_BITS) - 1'b1);
    wire [2*C1_WIDTH-1:0]        mul_half =
        ({{(2*C1_WIDTH-1){1'b0}}, 1'b1}) << (C9_FRAC_BITS - 1);
    wire                         mul_round_up =
        (mul_rem > mul_half) || (mul_rem == mul_half && mul_q[0]);
    wire [2*C1_WIDTH-1:0]        mul_q_rounded = mul_q + (mul_round_up ? 1'b1 : 1'b0);
    wire signed [63:0]           mul_narrow_signed =
        mul_neg ? -$signed({{(64-2*C1_WIDTH){1'b0}}, mul_q_rounded})
                :  $signed({{(64-2*C1_WIDTH){1'b0}}, mul_q_rounded});
    wire                         mul_saturates =
        (mul_narrow_signed > C1_MAX64) || (mul_narrow_signed < C1_MIN64);
    wire signed [63:0]           mul_saturated =
        (mul_narrow_signed > C1_MAX64) ? C1_MAX64 :
        (mul_narrow_signed < C1_MIN64) ? C1_MIN64 : mul_narrow_signed;
    wire signed [C1_WIDTH-1:0]   narrow_value = mul_saturated[C1_WIDTH-1:0];

    always @(posedge clk) begin
        if (rst) begin
            pass_index         <= P_IDLE;
            samples_this_pass  <= 18'd0;
            busy               <= 1'b0;
            done               <= 1'b0;
            error              <= 1'b0;
            error_code         <= ERR_NONE;
            branch_normalized  <= 1'b0;
            peak_word          <= {C1_WIDTH{1'b0}};
            gain_word          <= {C9_WIDTH{1'b0}};
            audio_out          <= {C1_WIDTH{1'b0}};
            audio_out_valid    <= 1'b0;
            op_compares        <= 32'd0;
            op_selects         <= 32'd0;
            op_recip_divs      <= 32'd0;
            op_mults           <= 32'd0;
            op_narrows         <= 32'd0;
            op_saturations     <= 32'd0;
        end else if (abort) begin
            pass_index         <= P_IDLE;
            samples_this_pass  <= 18'd0;
            busy               <= 1'b0;
            done               <= 1'b0;
            branch_normalized  <= 1'b0;
            peak_word          <= {C1_WIDTH{1'b0}};
            gain_word          <= {C9_WIDTH{1'b0}};
            audio_out_valid    <= 1'b0;
            // error/error_code and every op counter are sticky since rst.
        end else begin
            audio_out_valid <= 1'b0;  // default; PASS2's valid branch overrides
            case (pass_index)
                P_IDLE, P_DONE: begin
                    if (start && !error) begin
                        pass_index        <= P_PASS1;
                        samples_this_pass <= 18'd0;
                        branch_normalized <= 1'b0;
                        peak_word         <= {C1_WIDTH{1'b0}};
                        gain_word         <= {C9_WIDTH{1'b0}};
                        busy              <= 1'b1;
                        done              <= 1'b0;
                    end
                end

                P_PASS1: begin
                    if (mix_valid) begin
                        if (samples_this_pass == SCHED_SAMPLES_PER_PASS) begin
                            error      <= 1'b1;
                            error_code <= ERR_SAMPLE_OVERRUN;
                            busy       <= 1'b0;
                        end else begin
                            op_compares <= op_compares + 32'd1;
                            op_selects  <= op_selects  + 32'd1;
                            if (mix_mag > peak_word)
                                peak_word <= mix_mag;
                            samples_this_pass <= samples_this_pass + 18'd1;
                        end
                    end else if (mix_done) begin
                        if (samples_this_pass != SCHED_SAMPLES_PER_PASS) begin
                            error      <= 1'b1;
                            error_code <= ERR_PASS_TRUNCATED;
                            busy       <= 1'b0;
                        end else begin
                            if (peak_word > UNITY_INT) begin
                                branch_normalized <= 1'b1;
                                gain_word         <= recip_quo[C9_WIDTH-1:0];
                                op_recip_divs     <= op_recip_divs + 32'd1;
                            end else begin
                                branch_normalized <= 1'b0;
                                gain_word         <= UNITY_GAIN;
                            end
                            pass_index        <= P_PASS2;
                            samples_this_pass <= 18'd0;
                        end
                    end
                end

                P_PASS2: begin
                    if (mix_valid) begin
                        if (samples_this_pass == SCHED_SAMPLES_PER_PASS) begin
                            error      <= 1'b1;
                            error_code <= ERR_SAMPLE_OVERRUN;
                            busy       <= 1'b0;
                        end else begin
                            if (branch_normalized) begin
                                audio_out  <= narrow_value;
                                op_mults   <= op_mults   + 32'd1;
                                op_narrows <= op_narrows + 32'd1;
                                if (mul_saturates)
                                    op_saturations <= op_saturations + 32'd1;
                            end else begin
                                audio_out <= mix_in;
                            end
                            audio_out_valid   <= 1'b1;
                            samples_this_pass <= samples_this_pass + 18'd1;
                        end
                    end else if (mix_done) begin
                        if (samples_this_pass != SCHED_SAMPLES_PER_PASS) begin
                            error      <= 1'b1;
                            error_code <= ERR_PASS_TRUNCATED;
                            busy       <= 1'b0;
                        end else begin
                            pass_index <= P_DONE;
                            busy       <= 1'b0;
                            done       <= 1'b1;
                        end
                    end
                end

                default: begin
                    // Unreachable (pass_index only ever holds P_IDLE/
                    // P_PASS1/P_PASS2/P_DONE); declared for tool linting.
                end
            endcase
        end
    end

endmodule
