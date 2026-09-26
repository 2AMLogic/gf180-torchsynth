// render_binding_top.sv — the one wire this repository was missing: the
// protocol receiver's clip-discard decision reaching the normalization
// replay controller (issue #211).
//
// spec/protocol/RENDER-TRIGGER.md "Where the binding reaches the RTL" says
// the receiver that answers ERR_RENDER_BINDING or ERR_NOISE_STREAM for an
// open render pulses the replay engine's ``bind_reject``. Before this
// module existed, both halves of that sentence were implemented and neither
// was connected: tb/sv/patch_control.sv (#69) is the receiver,
// tb/sv/normalization_replay_engine.sv (#77) has the input, and each had
// only its own standalone testbench driving it directly.
//
// This is a deliberately thin structural top, not a new owner row in
// DR-0010's module table. It declares exactly one composition: the
// receiver's one-cycle ``bind_reject`` output drives the engine's
// ``bind_reject`` input, combinationally, with no re-timing, gating or
// re-interpretation in between. Everything else is passed straight through
// so the two modules keep being exercised at their own declared
// boundaries.
//
// What this top deliberately does NOT declare (stated rather than implied):
//
//   - It does not bind the host-fed noise BYTE stream (NOISE_STREAM, the
//     #75 lane) to the pre-normalization mix SAMPLE stream (``mix_in``, the
//     #76 lane). Those are separate declared interfaces with separate owner
//     rows, and no landed RTL joins them yet. Here they are two independent
//     stimulus streams, exactly as each module's own testbench feeds them.
//     The property this top exists to demonstrate — a receiver-side
//     rejection discards the clip in the engine — does not depend on their
//     alignment.
//   - It makes no synthesis, layout, signoff, hardware-playback, or
//     sound-fidelity claim, and it is not the one-shot product top.
//
// PDK-free plain SystemVerilog for Icarus Verilog (-g2012): structural
// only, no interfaces, no classes, no vendor cells.

`timescale 1ns/1ps

import gf180_rtl_constants::*;

module render_binding_top #(
    parameter integer NOISE_CLIP_BYTES = SCHED_SAMPLES_PER_PASS * 4
) (
    input  wire clk,
    input  wire rst,

    // --- protocol receiver side (patch_control, #69) -------------------
    input  wire        cmd_valid,
    input  wire [7:0]  cmd_byte,
    output wire        cmd_ready,
    output wire        rsp_valid,
    output wire [7:0]  rsp_byte,
    input  wire        rsp_ready,
    output wire [1:0]  state_out,
    output wire        patch_active,
    output wire        bind_reject,   // observation only; the wire is internal

    // --- replay engine side (normalization_replay_engine, #77) ---------
    input  wire start,
    input  wire abort,
    input  wire signed [C1_WIDTH-1:0] mix_in,
    input  wire        mix_valid,
    input  wire        mix_done,
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
    output wire [C9_WIDTH-1:0] gain_word
);

    // The binding wire. A mutant that replaces this connection with a
    // constant is exactly the "the discard never reaches the engine"
    // regression tb/run_tb.py normreplay must detect.
    wire receiver_bind_reject;

    wire [C4_WIDTH-1:0] kbd_midi_f0_word_unused;
    wire [46:0]         kbd_duration_word_unused;
    wire [7:0]          sound_identity_len_unused;
    wire [511:0]        sound_identity_unused;
    wire [C4_WIDTH-1:0] probe_value_unused;
    wire                idle_out_unused;

    patch_control #(
        .NOISE_CLIP_BYTES(NOISE_CLIP_BYTES)
    ) receiver (
        .clk               (clk),
        .rst               (rst),
        .cmd_valid         (cmd_valid),
        .cmd_byte          (cmd_byte),
        .cmd_ready         (cmd_ready),
        .rsp_valid         (rsp_valid),
        .rsp_byte          (rsp_byte),
        .rsp_ready         (rsp_ready),
        .state_out         (state_out),
        .patch_active      (patch_active),
        .bind_reject       (receiver_bind_reject),
        .kbd_midi_f0_word  (kbd_midi_f0_word_unused),
        .kbd_duration_word (kbd_duration_word_unused),
        .sound_identity_len(sound_identity_len_unused),
        .sound_identity    (sound_identity_unused),
        .probe_slot        (7'd0),
        .probe_value       (probe_value_unused),
        .idle_out          (idle_out_unused)
    );

    normalization_replay_engine engine (
        .clk               (clk),
        .rst               (rst),
        .start             (start),
        .abort             (abort),
        .bind_reject       (receiver_bind_reject),
        .mix_in            (mix_in),
        .mix_valid         (mix_valid),
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
        .op_compares       (),
        .op_selects        (),
        .op_recip_divs     (),
        .op_mults          (),
        .op_narrows        (),
        .op_saturations    ()
    );

    assign bind_reject = receiver_bind_reject;

endmodule
