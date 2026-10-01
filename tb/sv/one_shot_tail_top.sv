// one_shot_tail_top.sv -- the first integrated composition of landed RTL
// engines: the audio VCA + pre-normalization mixer (#76,
// audio_mix_engine) feeding the normalization replay controller + one-shot
// two-pass sequencer (#77, normalization_replay_engine), issue #79.
//
// This is a deliberately thin structural top. The mixer's registered
// ``mix_word`` / ``out_valid`` pair is wired straight into the replay
// engine's ``mix_in`` / ``mix_valid`` pair, with no re-timing, gating or
// reinterpretation in between, so the engine sees exactly the per-sample
// stream DR-0010 declares for its mixer-output interface. Pass 1 and pass 2
// are two re-triggered mixer walks over identical inputs (DR-0010 P4:
// two-pass re-render, no clip buffer): the replay engine's ``start`` /
// ``mix_done`` and the mixer's ``trigger`` are driven by the host/testbench
// and are the only sequencing signals.
//
// What this top deliberately does NOT contain (stated rather than implied;
// spec/ONESHOT-E2E.md carries the full ledger):
//
//   - the audio-rate sources (VCO 1 / VCO 2 / noise raw words) and the
//     control-rate amplitude columns arrive as host-fed per-sample streams,
//     exactly as the #76 lane declares its own inputs. No whole-source
//     composition of #70/#71/#72/#73/#74/#75 into this top exists, and the
//     shadow exp2/tanh/`**alpha` sites have no resident RTL
//     (DR-0008/DR-0010 open items);
//   - the serialized single-MAC schedule: both engines still retire one
//     sample per cycle;
//   - any synthesis, layout, signoff, hardware-playback, or sound-fidelity
//     claim.
//
// The ``link_valid`` wire is a named mutation seam (the tb runner plants a
// missing-sample fault there and requires the status/trace comparison to
// catch it). PDK-free plain SystemVerilog for Icarus Verilog (-g2012).

`timescale 1ns/1ps

import gf180_rtl_constants::*;

module one_shot_tail_top (
    input  wire clk,
    input  wire rst,

    // --- #76 mixer side --------------------------------------------------
    input  wire en,
    input  wire mix_trigger,
    input  wire signed [C1_WIDTH-1:0] level_vco_1,
    input  wire signed [C1_WIDTH-1:0] level_vco_2,
    input  wire signed [C1_WIDTH-1:0] level_noise,
    input  wire signed [C1_WIDTH-1:0] raw_vco_1,
    input  wire signed [C1_WIDTH-1:0] raw_vco_2,
    input  wire signed [C1_WIDTH-1:0] raw_noise,
    input  wire signed [C1_WIDTH-1:0] amp_vco_1,
    input  wire signed [C1_WIDTH-1:0] amp_vco_2,
    input  wire signed [C1_WIDTH-1:0] amp_noise,

    output wire signed [C1_WIDTH-1:0] post_vca_1,
    output wire signed [C1_WIDTH-1:0] post_vca_2,
    output wire signed [C1_WIDTH-1:0] post_vca_n,
    output wire signed [C1_WIDTH-1:0] mix_word,
    output wire        [C1_WIDTH-1:0] mix_abs,
    output wire        [C1_WIDTH-1:0] mix_peak_word,
    output wire                       mix_out_valid,
    output wire [31:0] mix_op_mults,
    output wire [31:0] mix_op_adds,
    output wire [31:0] mix_op_narrows,
    output wire [31:0] mix_op_sats,
    output wire [31:0] mix_op_rounds,
    output wire [31:0] mix_op_peak_cmps,
    output wire [31:0] mix_op_acc_faults,

    // --- #77 replay side -------------------------------------------------
    input  wire start,
    input  wire mix_done,
    output wire signed [C1_WIDTH-1:0] audio_out,
    output wire        audio_out_valid,
    output wire        busy,
    output wire        done,
    output wire        error,
    output wire [7:0]  error_code,
    output wire [1:0]  pass_index,
    output wire [17:0] samples_this_pass,
    output wire        branch_normalized,
    output wire [C1_WIDTH-1:0] peak_word,
    output wire [C9_WIDTH-1:0] gain_word,
    output wire [31:0] op_compares,
    output wire [31:0] op_selects,
    output wire [31:0] op_recip_divs,
    output wire [31:0] op_mults,
    output wire [31:0] op_narrows,
    output wire [31:0] op_saturations
);

    audio_mix_engine mixer (
        .clk           (clk),
        .rst           (rst),
        .en            (en),
        .trigger       (mix_trigger),
        .level_vco_1   (level_vco_1),
        .level_vco_2   (level_vco_2),
        .level_noise   (level_noise),
        .raw_vco_1     (raw_vco_1),
        .raw_vco_2     (raw_vco_2),
        .raw_noise     (raw_noise),
        .amp_vco_1     (amp_vco_1),
        .amp_vco_2     (amp_vco_2),
        .amp_noise     (amp_noise),
        .post_vca_1    (post_vca_1),
        .post_vca_2    (post_vca_2),
        .post_vca_n    (post_vca_n),
        .mix_word      (mix_word),
        .mix_abs       (mix_abs),
        .peak_word     (mix_peak_word),
        .out_valid     (mix_out_valid),
        .op_mults      (mix_op_mults),
        .op_adds       (mix_op_adds),
        .op_narrows    (mix_op_narrows),
        .op_sats       (mix_op_sats),
        .op_rounds     (mix_op_rounds),
        .op_peak_cmps  (mix_op_peak_cmps),
        .op_acc_faults (mix_op_acc_faults)
    );

    // The link. A mutant that drops, duplicates or re-times a sample here is
    // exactly the "missing-sample" regression tb/run_oneshot.py must catch.
    wire link_valid = mix_out_valid;

    normalization_replay_engine replay (
        .clk               (clk),
        .rst               (rst),
        .start             (start),
        .abort             (1'b0),
        .bind_reject       (1'b0),
        .mix_in            (mix_word),
        .mix_valid         (link_valid),
        .mix_done          (mix_done),
        .audio_out         (audio_out),
        .audio_out_valid   (audio_out_valid),
        .busy              (busy),
        .done              (done),
        .error             (error),
        .error_code        (error_code),
        .pass_index        (pass_index),
        .samples_this_pass (samples_this_pass),
        .branch_normalized (branch_normalized),
        .peak_word         (peak_word),
        .gain_word         (gain_word),
        .op_compares       (op_compares),
        .op_selects        (op_selects),
        .op_recip_divs     (op_recip_divs),
        .op_mults          (op_mults),
        .op_narrows        (op_narrows),
        .op_saturations    (op_saturations)
    );

endmodule
