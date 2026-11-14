// fpga_oneshot_top.sv -- MEASUREMENT WRAPPER around one_shot_voice_top
// (issue #80, DR-0013). It exists only so the passing one-shot RTL can be
// synthesized, placed and routed on one ECP5 device and so its resource,
// timing and cycle numbers can be read. It is NOT a product control path:
// the physical control/audio path is issue #81.
//
// What the wrapper provides (all of it is "shim"; reported separately):
//   - reset synchronizer for the core's active-high async reset;
//   - sequencer: the exact phase contract of one_shot_voice_top.sv /
//     tb_one_shot_voice.sv (control walk of 1,764 four-cycle ticks, then two
//     audio passes of N four-cycle groups, noise priming, in_done,
//     mix_done), one state per clock;
//   - stimulus: a serially loaded static-word register (cfg_q, 2956 bits)
//     for the formation/patch words, and a free-running 768-bit shift
//     register (dyn_q) for the per-tick shadow words, per-sample host
//     streams and noise bytes. Inputs must be non-constant or synthesis
//     would constant-fold the arithmetic being measured. THE STIMULUS IS
//     NOT THE GOLDEN VECTOR; no numeric-correctness claim is made.
//   - observation: audio_out is folded into a CRC-32 and, with done/error
//     bits, into one output parity bit so the sample path is not pruned.
//
// What is deliberately NOT provided: the declared shadow words, the 4 B per
// sample noise stream and the 1,764x18 shadow table are host-supplied in the
// verified architecture (DR-0010 amendment 2026-09-22). Here they are
// pseudo-random shift-register bits, so their storage/transport cost is NOT
// in the core numbers.
//
// Parameter OBSERVE_ALL=0 (default): only functional outputs are observed;
// the verification op-counters (op_*) are therefore pruned by synthesis.
// OBSERVE_ALL=1: every core output is XOR-folded into the signature, so the
// instrumentation counters are retained (an upper bound).
// Parameter WALK sets samples per pass (176400 = the canonical clip);
// SIM_SEED=0 zeroes the dyn_q reset value (simulation of a valid all-zero
// noise stream); the synthesized default is a non-zero pattern.

`timescale 1ns/1ps

import gf180_rtl_constants::*;

module fpga_oneshot_top #(
    parameter integer WALK        = SCHED_SAMPLES_PER_PASS,
    parameter integer OBSERVE_ALL = 0,
    parameter integer SIM_SEED    = 1
) (
    input  wire clk,
    input  wire rst_n,
    input  wire start_pin,
    input  wire cfg_in,
    input  wire cfg_shift,
    output wire done_pin,
    output wire error_pin,
    output wire sig_pin
);

    localparam integer CTL     = 1764;
    localparam integer CFG_W   = 2956;
    localparam integer DYN_W   = 768;

    // ---- reset synchronizer (assert async, release synchronous) --------
    reg [1:0] rst_sync;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) rst_sync <= 2'b00;
        else        rst_sync <= {rst_sync[0], 1'b1};
    end
    wire rst = ~rst_sync[1];

    // ---- static words: serial chain -------------------------------------
    reg [CFG_W-1:0] cfg_q;
    always @(posedge clk) if (cfg_shift) cfg_q <= {cfg_q[CFG_W-2:0], cfg_in};

    wire [6*47-1:0]         adsr_duration_q, adsr_attack_q, adsr_decay_q, adsr_release_q;
    wire [5:0]              adsr_duration_zero, adsr_attack_zero, adsr_decay_zero, adsr_release_zero;
    wire [6*C4_WIDTH-1:0]   adsr_sustain_entry;
    wire signed [62:0]      adsr_eps60;
    wire [2*C4_WIDTH-1:0]   lfo_freq, lfo_depth;
    wire [2*C2_WIDTH-1:0]   lfo_init;
    wire [2*5*32-1:0]       lfo_weights;
    wire [4*C4_WIDTH-1:0]   depths_r0, depths_r1, depths_r2, depths_r3, depths_r4;
    wire [C4_WIDTH-1:0]     midi_f0_word, vco1_tuning_word, vco1_depth_word;
    wire [C2_WIDTH-1:0]     vco1_init_phase_word;
    wire signed [C4_WIDTH-1:0] vco2_tuning_word, vco2_depth_word, vco2_shape_word;
    wire signed [31:0]      vco2_partials_word;
    wire [C2_WIDTH-1:0]     vco2_init_phase_word;
    wire signed [C1_WIDTH-1:0] level_vco_1, level_vco_2, level_noise;
    wire [C2_WIDTH-1:0]     noise_sound_index;
    wire [4:0]              noise_declared_slot;

    assign {adsr_duration_q, adsr_attack_q, adsr_decay_q, adsr_release_q,
            adsr_duration_zero, adsr_attack_zero, adsr_decay_zero, adsr_release_zero,
            adsr_sustain_entry, adsr_eps60,
            lfo_freq, lfo_depth, lfo_init, lfo_weights,
            depths_r0, depths_r1, depths_r2, depths_r3, depths_r4,
            midi_f0_word, vco1_tuning_word, vco1_depth_word, vco1_init_phase_word,
            vco2_tuning_word, vco2_depth_word, vco2_shape_word,
            vco2_partials_word, vco2_init_phase_word,
            level_vco_1, level_vco_2, level_noise,
            noise_sound_index, noise_declared_slot} = cfg_q;

    // ---- dynamic words: free-running shift register ----------------------
    reg [DYN_W-1:0] dyn_q;
    always @(posedge clk or posedge rst) begin
        if (rst) dyn_q <= (SIM_SEED != 0) ? {(DYN_W/32){32'h9e3779b9}} : {DYN_W{1'b0}};
        else     dyn_q <= {dyn_q[DYN_W-2:0], dyn_q[DYN_W-1] ^ dyn_q[DYN_W-5]
                                             ^ dyn_q[DYN_W-18] ^ dyn_q[DYN_W-38]};
    end

    wire [6*32-1:0] adsr_shadow_attack  = dyn_q[0*192 +: 192];
    wire [6*32-1:0] adsr_shadow_decay   = dyn_q[1*192 +: 192];
    wire [6*32-1:0] adsr_shadow_release = dyn_q[2*192 +: 192];
    wire signed [C3_WIDTH-1:0] vco1_fq_word       = dyn_q[576 +: 32];
    wire signed [C3_WIDTH-1:0] vco2_fq_word       = dyn_q[608 +: 32];
    wire signed [C1_WIDTH-1:0] vco2_square_q_word = dyn_q[640 +: 24];
    wire signed [C1_WIDTH-1:0] vco2_left_q_word   = dyn_q[664 +: 24];
    wire [7:0] noise_byte_in_w = dyn_q[688 +: 8];

    // ---- sequencer --------------------------------------------------------
    localparam [4:0]
        S_IDLE = 0, S_CT0 = 1, S_CT1 = 2, S_TICK = 3, S_GAP = 4, S_PTRIG = 5,
        S_PGO = 6, S_PRIME = 7, S_GROUP = 8, S_INDONE = 9, S_MIXDONE = 10,
        S_PIDLE = 11, S_PDRAIN = 12, S_FIN = 13;

    reg [4:0]  st;
    reg [1:0]  ph;          // phase inside a tick / group / prime counter
    reg [10:0] tick;
    reg [17:0] j;
    reg [19:0] bidx;
    reg        pass2;       // 0 = pass 1, 1 = pass 2
    reg        drain;       // second drain cycle

    localparam [19:0] NBYTES = WALK * 4;

    wire byte_ok = (bidx < NBYTES);

    reg ctl_trigger, ctl_en_adsr, ctl_en_lfo, ctl_en_mm, ctl_load;
    reg audio_trigger, audio_go, up_en, vco_en, mix_en;
    reg noise_en, noise_in_done, mix_done, start;
    reg noise_byte_valid;

    wire in_prime = (st == S_PRIME);
    wire in_group = (st == S_GROUP);

    // Registered control outputs decoded from the NEXT state's cycle. To
    // keep one state == one clock with outputs aligned to the state, the
    // outputs are combinational from (st, ph).
    always @* begin
        ctl_trigger = 0; ctl_en_adsr = 0; ctl_en_lfo = 0; ctl_en_mm = 0;
        ctl_load = 0; audio_trigger = 0; audio_go = 0; up_en = 0; vco_en = 0;
        mix_en = 0; noise_en = 0; noise_in_done = 0; mix_done = 0; start = 0;
        noise_byte_valid = 0;
        case (st)
            S_CT0: begin ctl_trigger = 1; ctl_en_adsr = 1; ctl_en_lfo = 1; ctl_en_mm = 1; end
            S_CT1: ctl_en_adsr = 1;
            S_TICK: begin
                ctl_en_adsr = (ph == 2'd0);
                ctl_en_lfo  = (ph == 2'd1);
                ctl_en_mm   = (ph == 2'd2);
                ctl_load    = (ph == 2'd3);
            end
            S_PTRIG: begin audio_trigger = 1; noise_en = 1; start = ~pass2; end
            S_PGO:   begin audio_go = 1; noise_en = 1; end
            S_PRIME: begin noise_en = 1; noise_byte_valid = byte_ok; end
            S_GROUP: begin
                noise_en = 1;
                noise_byte_valid = byte_ok;
                up_en  = (ph == 2'd0);
                vco_en = (ph == 2'd1);
                mix_en = (ph == 2'd2);
            end
            S_INDONE:  begin noise_en = 1; noise_in_done = (WALK == SCHED_SAMPLES_PER_PASS); end
            S_MIXDONE: begin noise_en = 1; mix_done = 1; end
            S_PIDLE:   noise_en = 1;
            default: ;
        endcase
    end

    always @(posedge clk or posedge rst) begin
        if (rst) begin
            st <= S_IDLE; ph <= 0; tick <= 0; j <= 0; bidx <= 0;
            pass2 <= 0; drain <= 0;
        end else begin
            case (st)
                S_IDLE: if (start_pin) begin st <= S_CT0; pass2 <= 0; tick <= 0; end
                S_CT0:  st <= S_CT1;
                S_CT1:  begin st <= S_TICK; ph <= 0; tick <= 0; end
                S_TICK: begin
                    ph <= ph + 2'd1;
                    if (ph == 2'd3) begin
                        tick <= tick + 11'd1;
                        if (tick == CTL - 1) st <= S_GAP;
                    end
                end
                S_GAP:   st <= S_PTRIG;
                S_PTRIG: st <= S_PGO;
                S_PGO:   begin st <= S_PRIME; ph <= 0; bidx <= 0; end
                S_PRIME: begin
                    bidx <= bidx + 20'd1;
                    ph <= ph + 2'd1;
                    if (ph == 2'd3) begin st <= S_GROUP; ph <= 0; j <= 0; end
                end
                S_GROUP: begin
                    if (byte_ok) bidx <= bidx + 20'd1;
                    ph <= ph + 2'd1;
                    if (ph == 2'd3) begin
                        j <= j + 18'd1;
                        if (j == WALK - 1) st <= S_INDONE;
                    end
                end
                S_INDONE:  st <= S_MIXDONE;
                S_MIXDONE: st <= S_PIDLE;
                S_PIDLE:   begin st <= S_PDRAIN; drain <= 0; end
                S_PDRAIN: begin
                    drain <= 1'b1;
                    if (drain) begin
                        drain <= 0;
                        if (!pass2) begin pass2 <= 1; st <= S_PTRIG; end
                        else st <= S_FIN;
                    end
                end
                S_FIN: ;
                default: st <= S_IDLE;
            endcase
        end
    end

    // ---- the core -----------------------------------------------------------
    wire [10:0] ctl_load_index = tick;
    wire [7:0]  noise_byte_in  = noise_byte_in_w;

    wire [6*C1_WIDTH-1:0] adsr_env;
    wire [5:0]            adsr_valid;
    wire [6*32-1:0]       adsr_op_mults, adsr_op_narrows, adsr_op_shadow, adsr_op_rampdivs;
    wire [2*C1_WIDTH-1:0] lfo_raw, lfo_post;
    wire [1:0]            lfo_valid;
    wire [2*32-1:0]       lfo_op_mults, lfo_op_narrows, lfo_op_phases, lfo_op_clamps;
    wire [5*C1_WIDTH-1:0] mm_out;
    wire                  mm_valid;
    wire [31:0]           mm_op_mults, mm_op_adds, mm_op_narrows, mm_op_sats;
    wire [5*C1_WIDTH-1:0] up_word;
    wire [4:0]            up_valid;
    wire [5*32-1:0]       up_out_count, up_op_mults, up_op_adds, up_op_narrows,
                          up_op_frac_words, up_op_coord_steps, up_op_sats;
    wire signed [C1_WIDTH-1:0] vco1_word;
    wire [C2_WIDTH-1:0]   vco1_phase_out;
    wire                  vco1_valid;
    wire [31:0]           vco1_op_mults, vco1_op_adds, vco1_op_narrows,
                          vco1_op_shadows, vco1_op_sats, vco1_op_clamps;
    wire signed [C4_WIDTH-1:0] vco2_m2_word;
    wire signed [31:0]    vco2_driven_word;
    wire signed [C1_WIDTH-1:0] vco2_right_q_word, vco2_word;
    wire                  vco2_valid;
    wire [31:0]           vco2_op_mults, vco2_op_adds, vco2_op_narrows, vco2_op_sats;
    wire signed [C1_WIDTH-1:0] noise_sample_word;
    wire                  noise_sample_valid, noise_error;
    wire [7:0]            noise_error_code;
    wire [19:0]           noise_bytes_accepted;
    wire [17:0]           noise_samples_produced;
    wire [31:0]           noise_narrow_count;
    wire [4:0]            noise_bound_slot;
    wire signed [C1_WIDTH-1:0] post_vca_1, post_vca_2, post_vca_n, mix_word;
    wire [C1_WIDTH-1:0]   mix_abs, mix_peak_word;
    wire                  mix_out_valid, link_valid_out;
    wire [31:0]           mix_op_mults, mix_op_adds, mix_op_narrows, mix_op_sats,
                          mix_op_rounds, mix_op_peak_cmps, mix_op_acc_faults;
    wire signed [C1_WIDTH-1:0] audio_out;
    wire                  audio_out_valid, busy, done, error;
    wire [7:0]            error_code;
    wire [1:0]            pass_index;
    wire [17:0]           samples_this_pass;
    wire                  branch_normalized;
    wire [C1_WIDTH-1:0]   peak_word;
    wire [C9_WIDTH-1:0]   gain_word;
    wire [31:0]           op_compares, op_selects, op_recip_divs, op_mults,
                          op_narrows, op_saturations;

    one_shot_voice_top core (.*);

    // ---- observation -----------------------------------------------------------
    reg [31:0] crc;
    wire [31:0] crc_next = {crc[30:0], 1'b0} ^ ({32{crc[31] ^ audio_out[0]}} & 32'h04C11DB7)
                           ^ {8'd0, audio_out};
    wire obs_bit = ^{adsr_env, adsr_valid, lfo_raw, lfo_post, mm_out, mm_valid,
        up_word, up_valid, vco1_word, vco1_phase_out, vco1_valid, vco2_m2_word,
        vco2_driven_word, vco2_right_q_word, vco2_word, vco2_valid,
        noise_sample_valid, noise_bytes_accepted, noise_samples_produced,
        noise_bound_slot, post_vca_1, post_vca_2, post_vca_n, mix_word, mix_abs,
        mix_peak_word, mix_out_valid, link_valid_out, busy, pass_index,
        samples_this_pass, branch_normalized, peak_word, gain_word};
    wire [31:0] ops_all = (OBSERVE_ALL != 0) ?
        (^adsr_op_mults) ^ (^adsr_op_narrows) ^ (^adsr_op_shadow) ^ (^adsr_op_rampdivs)
        ^ (^lfo_op_mults) ^ (^lfo_op_narrows) ^ (^lfo_op_phases) ^ (^lfo_op_clamps)
        ^ (^mm_op_mults) ^ (^mm_op_adds) ^ (^mm_op_narrows) ^ (^mm_op_sats)
        ^ (^up_out_count) ^ (^up_op_mults) ^ (^up_op_adds) ^ (^up_op_narrows)
        ^ (^up_op_frac_words) ^ (^up_op_coord_steps) ^ (^up_op_sats)
        ^ (^vco1_op_mults) ^ (^vco1_op_adds) ^ (^vco1_op_narrows) ^ (^vco1_op_shadows)
        ^ (^vco1_op_sats) ^ (^vco1_op_clamps) ^ (^vco2_op_mults) ^ (^vco2_op_adds)
        ^ (^vco2_op_narrows) ^ (^vco2_op_sats) ^ (^noise_narrow_count)
        ^ (^mix_op_mults) ^ (^mix_op_adds) ^ (^mix_op_narrows) ^ (^mix_op_sats)
        ^ (^mix_op_rounds) ^ (^mix_op_peak_cmps) ^ (^mix_op_acc_faults)
        ^ (^op_compares) ^ (^op_selects) ^ (^op_recip_divs) ^ (^op_mults)
        ^ (^op_narrows) ^ (^op_saturations) ^ {31'd0, obs_bit} : 32'd0;
    always @(posedge clk or posedge rst) begin
        if (rst) crc <= 32'hFFFFFFFF;
        else if (audio_out_valid) crc <= crc_next ^ (OBSERVE_ALL != 0 ? ops_all : 32'd0);
        else if (OBSERVE_ALL != 0)  crc <= crc ^ ops_all;
    end

    reg sig_q, done_q, err_q;
    always @(posedge clk) begin
        sig_q  <= ^crc ^ ^error_code ^ noise_error;
        done_q <= (st == S_FIN) & done;
        err_q  <= error | noise_error;
    end
    assign sig_pin   = sig_q;
    assign done_pin  = done_q;
    assign error_pin = err_q;

endmodule
