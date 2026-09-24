// Noise-stream DUT: the exact canonical noise lane in RTL (issue #75).
//
// The accepted noise policy is DR-0003 (Accepted) + DR-0008 C8 (Accepted):
// the canonical noise stream is HOST- OR TESTBENCH-FED bit-exactly; the
// canonical slot is sound_index % 32 with seed 13; there is NO error metric
// for noise — exactness. This module therefore contains NO generator: an
// on-chip generator reproducing the CPU torch.rand bitstream would be a new
// noise policy requiring its own decision record (DR-0008 Section 7 and
// Section 13 change control). The host/testbench feeds the resolved binary32
// byte stream; this DUT reassembles it, converts each sample to the C1
// Q2.21 audio word exactly as the frozen fixed model's noise lane
// (fixed_voice.py site "noise.source_q": half_even(x * 2^21) with C7
// saturation) — by exponent shift + half-even round, no multiplier, per the
// DR-0010 #75 owner row — and exports the word stream plus the sticky
// counter and a sticky identity/framing error register:
//
//   code 1 SLOT_IDENTITY   : declared_slot != sound_index % 32 (C8 rule)
//   code 2 BYTE_OVERRUN    : a byte was fed after the clip's full length
//   code 3 STREAM_TRUNCATED: in_done arrived before the clip's full length
//
// The clip byte length is SCHED_SAMPLES_PER_PASS x 4 from the generated
// constants package (DR-0010 Accepted schedule), not a hardcoded width.
// Every width/policy constant comes from gf180_rtl_constants, which the tb
// flow hash-checks against the accepted DR-0008 register before this module
// is elaborated. No floats participate anywhere.
//
// Between streams the host drops `en` for at least one cycle: all per-stream
// state clears to zero (replay/reset carries no off-by-one state), while a
// raised sticky error survives every trigger and only `rst` clears it — a
// fault is never hidden by a later run.
//
// Bit-exact obligation (AGENTS.md): this DUT reproduces the fixed model's
// integer noise dataflow bit-exactly; the tb runner (tb/run_tb.py 'noise')
// requires the captured stream to match the golden receipt's noise.raw
// digests via the model's own primitives. This module makes no synthesis,
// layout, signoff, hardware-playback, or sound-fidelity claim. Streaming
// transport integration (how the bytes arrive over a real link) belongs to
// the #66/#77 transport lanes, not this module.

`timescale 1ns/1ps

import gf180_rtl_constants::*;

module noise_stream_dut (
    input  wire                        clk,
    input  wire                        rst,
    // Stream-active level: held high while one clip's bytes are fed; dropped
    // for >= 1 cycle between streams to clear per-stream state.
    input  wire                        en,
    input  wire [C2_WIDTH-1:0]         sound_index,
    input  wire [4:0]                  declared_slot,
    input  wire [7:0]                  byte_in,
    input  wire                        byte_valid,
    // Host/testbench asserts this (with en high) once it has fed the
    // intended byte count of the current stream.
    input  wire                        in_done,
    output reg  signed [C1_WIDTH-1:0]  sample_word,
    output reg                         sample_valid,
    output reg                         error,
    output reg  [7:0]                  error_code,
    output reg  [19:0]                 bytes_accepted,
    output reg  [17:0]                 samples_produced,
    output reg  [31:0]                 narrow_count
);

    // One clip of host-fed binary32 noise = 4 bytes per audio sample.
    localparam [19:0] EXPECTED_BYTES = SCHED_SAMPLES_PER_PASS * 4;
    localparam signed [63:0] C1_MAX64 = 64'sd8388607;
    localparam signed [63:0] C1_MIN64 = -64'sd8388608;

    // C8 slot rule: the canonical slot for a sound is sound_index % 32.
    wire [4:0] slot_expected = sound_index[4:0];

    reg [31:0] byte_acc;      // reassembled little-endian bytes (b0 = LSB)
    reg [1:0]  byte_phase;    // position within the current sample

    // --- binary32 -> Q2.21 convert (the one declared narrowing site) -------
    // `bits` is the sample word completed by THIS cycle's byte (bytes arrive
    // little-endian, so shifting in from the top lands b0 at [7:0]).
    wire [31:0] bits  = {byte_in, byte_acc[31:8]};
    wire        sign  = bits[31];
    wire [7:0]  expo  = bits[30:23];
    wire [22:0] frac  = bits[22:0];
    wire        is_sub    = (expo == 8'd0);
    wire        nonfinite = (expo == 8'hFF);
    // 24-bit significand (implicit bit); subnormals have none.
    wire [23:0] mag = is_sub ? {1'b0, frac} : {1'b1, frac};
    // Scaled exponent: value * 2^21 = mag * 2^exp2
    //   normal:    2^(e-150) * 2^21 = 2^(e-129)
    //   subnormal: 2^-149 * 2^21    = 2^-128
    wire signed [8:0] exp2 = is_sub ? -9'sd128 : ($signed({1'b0, expo}) - 9'sd129);

    // Half-even round of mag * 2^exp2 to an integer (non-negative magnitude,
    // ties to even) by shift — no multiplier (DR-0010 #75 owner row).
    //   exp2 >= 1  : scaled magnitude >= 2^24 -> saturates (C7)
    //   exp2 == 0  : exact integer, plain saturate check
    //   exp2 <= -27: scaled magnitude < 0.5 -> rounds to 0
    //   otherwise  : shift path with exact integer tie detection
    wire [63:0] mag64     = {40'd0, mag};
    wire [5:0]  shift_amt = -exp2[5:0];   // in [1, 26] on the shift path
    wire [63:0] q_raw     = mag64 >> shift_amt;
    wire [63:0] rem       = mag64 & ((64'd1 << shift_amt) - 64'd1);
    wire [63:0] half      = 64'd1 << (shift_amt - 6'd1);
    wire        round_up  = (rem > half) || ((rem == half) && q_raw[0]);
    wire [63:0] q_rounded = q_raw + (round_up ? 64'd1 : 64'd0);

    reg  [63:0] rounded;
    reg         saturate_pos;
    reg         saturate_neg;
    always @* begin
        if (nonfinite || exp2 >= 9'sd1)
            rounded = 64'd0;
        else if (exp2 == 9'sd0)
            rounded = mag64;
        else if (exp2 <= -9'sd27)
            rounded = 64'd0;
        else
            rounded = q_rounded;
        // Unreachable from the canonical feed (uniform_(-1, 1)); declared
        // policy branch for the unobservable pattern class: saturate to the
        // sign's extreme (C7), never wrap.
        saturate_pos = (~sign) && (nonfinite || exp2 >= 9'sd1
                                   || rounded > C1_MAX64);
        saturate_neg = ( sign) && (nonfinite || exp2 >= 9'sd1
                                   || rounded > C1_MAX64);
    end

    wire signed [63:0] narrow_value = saturate_pos ? C1_MAX64
                                    : saturate_neg ? C1_MIN64
                                    : (sign ? -$signed({1'b0, rounded})
                                            : $signed({1'b0, rounded}));

    wire sample_boundary = (byte_phase == 2'd3);

    always @(posedge clk) begin
        if (rst) begin
            sample_word      <= {C1_WIDTH{1'b0}};
            sample_valid     <= 1'b0;
            error            <= 1'b0;
            error_code       <= 8'd0;
            bytes_accepted   <= 20'd0;
            samples_produced <= 18'd0;
            narrow_count     <= 32'd0;
            byte_acc         <= 32'd0;
            byte_phase       <= 2'd0;
        end else if (!en) begin
            // Between streams: per-stream state clears to zero; the sticky
            // error survives (only rst clears it).
            sample_valid     <= 1'b0;
            bytes_accepted   <= 20'd0;
            samples_produced <= 18'd0;
            narrow_count     <= 32'd0;
            byte_acc         <= 32'd0;
            byte_phase       <= 2'd0;
        end else begin
            sample_valid <= 1'b0;
            // C8 slot identity: sticky.
            if (!error && (declared_slot != slot_expected)) begin
                error      <= 1'b1;
                error_code <= 8'd1;  // SLOT_IDENTITY
            end
            // Length validation: the stream must carry exactly one clip.
            if (in_done && (bytes_accepted != EXPECTED_BYTES) && !error) begin
                error      <= 1'b1;
                error_code <= 8'd3;  // STREAM_TRUNCATED
            end
            if (byte_valid && !error) begin
                if (bytes_accepted == EXPECTED_BYTES) begin
                    error      <= 1'b1;
                    error_code <= 8'd2;  // BYTE_OVERRUN
                end else begin
                    bytes_accepted <= bytes_accepted + 20'd1;
                    byte_acc       <= {byte_in, byte_acc[31:8]};
                    if (sample_boundary) begin
                        byte_phase       <= 2'd0;
                        sample_word      <= narrow_value[C1_WIDTH-1:0];
                        sample_valid     <= 1'b1;
                        samples_produced <= samples_produced + 18'd1;
                        // Sticky counter: exactly one declared narrowing per
                        // sample (DR-0010 #75 owner row).
                        narrow_count     <= narrow_count + 32'd1;
                    end else begin
                        byte_phase <= byte_phase + 2'd1;
                    end
                end
            end
        end
    end

endmodule
