// File-driven testbench over the integrated WHOLE-VOICE one-shot top
// (issue #79, tb/sv/one_shot_voice_top.sv).
//
// Run protocol (paths cwd-relative, controlled by tb/run_voice.py). Every
// stimulus word is written by the flow as an unsigned hex/decimal bit
// pattern and sliced here; no float ever crosses this boundary.
//
//   runs.txt            one integer R: back-to-back clip renders, no reset
//                       between them (replay-independence evidence).
//   run<r>_walk.txt     "N FULL": audio samples per pass, and 1 when the
//                       pass feeds a complete 705,600-byte noise clip (so
//                       in_done may be asserted) else 0.
//   run<r>_params.txt   one line of statics:
//                         midi_f0 vco1_tun vco1_dep vco1_init
//                         vco2_tun vco2_dep vco2_shape vco2_partials
//                         vco2_init level1 level2 leveln
//                         sound_index declared_slot eps60_lo eps60_hi
//   run<r>_adsr.txt     6 lines (prefix order adsr_1, adsr_2, lfo_1_rate,
//                       lfo_2_rate, lfo_1_amp, lfo_2_amp), each:
//                         dur_lo dur_hi att_lo att_hi dec_lo dec_hi
//                         rel_lo rel_hi durz attz decz relz sustain
//   run<r>_lfo.txt      2 lines: freq depth init w_sin w_tri w_saw w_rsaw w_sqr
//   run<r>_depths.txt   5 lines (route order), 4 C4 depth words each in the
//                       pinned source order (adsr_1, adsr_2, lfo_1, lfo_2)
//   run<r>_shadow.txt   $readmemh, 1764*18 words: tick-major, then ADSR
//                       instance, then stage (attack, decay, release)
//   run<r>_fq1.txt      $readmemh, N words: vco_1 exp2 shadow stream
//   run<r>_fq2.txt      $readmemh, N words: vco_2 exp2 shadow stream
//   run<r>_sqq.txt      $readmemh, N words: vco_2 tanh square fanout
//   run<r>_lqq.txt      $readmemh, N words: vco_2 tanh left fanout
//   run<r>_bytes.txt    $readmemh, 4*N bytes: the exact C8 noise stream
//
// Captures:
//   run<r>_ctlcap.txt   1764 lines "tick e0..e5 raw0 raw1 post0 post1 m0..m4"
//   run<r>_audiocap.txt one line per audio sample per pass:
//                       "cycle hostpass engpass u0..u4 v1 v2 nz pv1 pv2 pvn mix"
//                       (hostpass is the host's own pass number, engpass the
//                       replay controller's own pass_index at that edge)
//   run<r>_linkcap.txt  one line per link-valid cycle "cycle hostpass word":
//                       what the replay controller actually consumes (its
//                       mix_in qualified by its mix_valid), so a link fault
//                       localizes to the faulting sample
//   run<r>_outcap.txt   one line per released output sample "cycle word"
//   run<r>_status.txt   one line of the replay controller's registers plus
//                       the mixer peak feed
//   run<r>_ops.txt      labeled op-counter lines (A/L/M once; U/V1/V2/MIX/NZ
//                       per pass)
//
// Phase contract (enforced here, not assumed): see one_shot_voice_top.sv.
// The tb asserts mm_valid at the control capture point, noise_sample_valid
// at the head of each audio group, and mix_out_valid at the audio capture
// point -- a composition that silently re-times would fail the bench rather
// than quietly produce comparable-looking numbers.
//
// PDK-free Icarus Verilog (-g2012); requires +lut=<memh> for the shared
// quarter-wave table.

`timescale 1ns/1ps

import gf180_rtl_constants::*;

module tb;

    localparam integer CTL        = 1764;
    localparam integer AUDIO_MAX  = SCHED_SAMPLES_PER_PASS;
    localparam integer BYTES_MAX  = SCHED_SAMPLES_PER_PASS * 4;
    localparam integer MAX_RUNS   = 4;

    reg clk = 1'b0;
    reg rst = 1'b1;
    always #5 clk = ~clk;

    // ---- sequencing ----------------------------------------------------
    reg        ctl_trigger = 1'b0;
    reg        ctl_en_adsr = 1'b0;
    reg        ctl_en_lfo  = 1'b0;
    reg        ctl_en_mm   = 1'b0;
    reg        ctl_load    = 1'b0;
    reg [10:0] ctl_load_index = 11'd0;
    reg        audio_trigger = 1'b0;
    reg        audio_go      = 1'b0;
    reg        up_en  = 1'b0;
    reg        vco_en = 1'b0;
    reg        mix_en = 1'b0;
    reg        start    = 1'b0;
    reg        mix_done = 1'b0;

    // ---- stimulus registers --------------------------------------------
    reg [6*47-1:0]       adsr_duration_q, adsr_attack_q, adsr_decay_q,
                         adsr_release_q;
    reg [5:0]            adsr_duration_zero, adsr_attack_zero,
                         adsr_decay_zero, adsr_release_zero;
    reg [6*C4_WIDTH-1:0] adsr_sustain_entry;
    reg signed [62:0]    adsr_eps60;
    reg [6*32-1:0]       adsr_shadow_attack, adsr_shadow_decay,
                         adsr_shadow_release;
    reg [2*C4_WIDTH-1:0] lfo_freq, lfo_depth;
    reg [2*C2_WIDTH-1:0] lfo_init;
    reg [2*5*32-1:0]     lfo_weights;
    reg [4*C4_WIDTH-1:0] depths_r0, depths_r1, depths_r2, depths_r3, depths_r4;
    reg [C4_WIDTH-1:0]   midi_f0_word, vco1_tuning_word, vco1_depth_word;
    reg [C2_WIDTH-1:0]   vco1_init_phase_word;
    reg signed [C3_WIDTH-1:0] vco1_fq_word;
    reg signed [C4_WIDTH-1:0] vco2_tuning_word, vco2_depth_word,
                              vco2_shape_word;
    reg signed [31:0]    vco2_partials_word;
    reg [C2_WIDTH-1:0]   vco2_init_phase_word;
    reg signed [C3_WIDTH-1:0] vco2_fq_word;
    reg signed [C1_WIDTH-1:0] vco2_square_q_word, vco2_left_q_word;
    reg                  noise_en = 1'b0;
    reg [C2_WIDTH-1:0]   noise_sound_index;
    reg [4:0]            noise_declared_slot;
    reg [7:0]            noise_byte_in;
    reg                  noise_byte_valid = 1'b0;
    reg                  noise_in_done = 1'b0;
    reg signed [C1_WIDTH-1:0] level_vco_1, level_vco_2, level_noise;

    // ---- observation ---------------------------------------------------
    wire [6*C1_WIDTH-1:0] adsr_env;
    wire [5:0]            adsr_valid;
    wire [6*32-1:0]       adsr_op_mults, adsr_op_narrows, adsr_op_shadow,
                          adsr_op_rampdivs;
    wire [2*C1_WIDTH-1:0] lfo_raw, lfo_post;
    wire [1:0]            lfo_valid;
    wire [2*32-1:0]       lfo_op_mults, lfo_op_narrows, lfo_op_phases,
                          lfo_op_clamps;
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
    wire [31:0]           vco2_op_mults, vco2_op_adds, vco2_op_narrows,
                          vco2_op_sats;
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
    wire [31:0]           mix_op_mults, mix_op_adds, mix_op_narrows,
                          mix_op_sats, mix_op_rounds, mix_op_peak_cmps,
                          mix_op_acc_faults;
    wire signed [C1_WIDTH-1:0] audio_out;
    wire                  audio_out_valid, busy, done, error,
                          branch_normalized;
    wire [7:0]            error_code;
    wire [1:0]            pass_index;
    wire [17:0]           samples_this_pass;
    wire [C1_WIDTH-1:0]   peak_word;
    wire [C9_WIDTH-1:0]   gain_word;
    wire [31:0]           op_compares, op_selects, op_recip_divs, op_mults,
                          op_narrows, op_saturations;

    one_shot_voice_top dut (
        .clk(clk), .rst(rst),
        .ctl_trigger(ctl_trigger), .ctl_en_adsr(ctl_en_adsr),
        .ctl_en_lfo(ctl_en_lfo), .ctl_en_mm(ctl_en_mm),
        .ctl_load(ctl_load), .ctl_load_index(ctl_load_index),
        .adsr_duration_q(adsr_duration_q), .adsr_attack_q(adsr_attack_q),
        .adsr_decay_q(adsr_decay_q), .adsr_release_q(adsr_release_q),
        .adsr_duration_zero(adsr_duration_zero),
        .adsr_attack_zero(adsr_attack_zero),
        .adsr_decay_zero(adsr_decay_zero),
        .adsr_release_zero(adsr_release_zero),
        .adsr_sustain_entry(adsr_sustain_entry), .adsr_eps60(adsr_eps60),
        .adsr_shadow_attack(adsr_shadow_attack),
        .adsr_shadow_decay(adsr_shadow_decay),
        .adsr_shadow_release(adsr_shadow_release),
        .adsr_env(adsr_env), .adsr_valid(adsr_valid),
        .adsr_op_mults(adsr_op_mults), .adsr_op_narrows(adsr_op_narrows),
        .adsr_op_shadow(adsr_op_shadow), .adsr_op_rampdivs(adsr_op_rampdivs),
        .lfo_freq(lfo_freq), .lfo_depth(lfo_depth), .lfo_init(lfo_init),
        .lfo_weights(lfo_weights),
        .lfo_raw(lfo_raw), .lfo_post(lfo_post), .lfo_valid(lfo_valid),
        .lfo_op_mults(lfo_op_mults), .lfo_op_narrows(lfo_op_narrows),
        .lfo_op_phases(lfo_op_phases), .lfo_op_clamps(lfo_op_clamps),
        .depths_r0(depths_r0), .depths_r1(depths_r1), .depths_r2(depths_r2),
        .depths_r3(depths_r3), .depths_r4(depths_r4),
        .mm_out(mm_out), .mm_valid(mm_valid),
        .mm_op_mults(mm_op_mults), .mm_op_adds(mm_op_adds),
        .mm_op_narrows(mm_op_narrows), .mm_op_sats(mm_op_sats),
        .audio_trigger(audio_trigger), .audio_go(audio_go),
        .up_en(up_en), .vco_en(vco_en), .mix_en(mix_en),
        .up_word(up_word), .up_valid(up_valid),
        .up_out_count(up_out_count), .up_op_mults(up_op_mults),
        .up_op_adds(up_op_adds), .up_op_narrows(up_op_narrows),
        .up_op_frac_words(up_op_frac_words),
        .up_op_coord_steps(up_op_coord_steps), .up_op_sats(up_op_sats),
        .midi_f0_word(midi_f0_word), .vco1_tuning_word(vco1_tuning_word),
        .vco1_depth_word(vco1_depth_word),
        .vco1_init_phase_word(vco1_init_phase_word),
        .vco1_fq_word(vco1_fq_word),
        .vco1_word(vco1_word), .vco1_phase_out(vco1_phase_out),
        .vco1_valid(vco1_valid),
        .vco1_op_mults(vco1_op_mults), .vco1_op_adds(vco1_op_adds),
        .vco1_op_narrows(vco1_op_narrows), .vco1_op_shadows(vco1_op_shadows),
        .vco1_op_sats(vco1_op_sats), .vco1_op_clamps(vco1_op_clamps),
        .vco2_tuning_word(vco2_tuning_word),
        .vco2_depth_word(vco2_depth_word),
        .vco2_shape_word(vco2_shape_word),
        .vco2_partials_word(vco2_partials_word),
        .vco2_init_phase_word(vco2_init_phase_word),
        .vco2_fq_word(vco2_fq_word),
        .vco2_square_q_word(vco2_square_q_word),
        .vco2_left_q_word(vco2_left_q_word),
        .vco2_m2_word(vco2_m2_word), .vco2_driven_word(vco2_driven_word),
        .vco2_right_q_word(vco2_right_q_word), .vco2_word(vco2_word),
        .vco2_valid(vco2_valid),
        .vco2_op_mults(vco2_op_mults), .vco2_op_adds(vco2_op_adds),
        .vco2_op_narrows(vco2_op_narrows), .vco2_op_sats(vco2_op_sats),
        .noise_en(noise_en), .noise_sound_index(noise_sound_index),
        .noise_declared_slot(noise_declared_slot),
        .noise_byte_in(noise_byte_in), .noise_byte_valid(noise_byte_valid),
        .noise_in_done(noise_in_done),
        .noise_sample_word(noise_sample_word),
        .noise_sample_valid(noise_sample_valid),
        .noise_error(noise_error), .noise_error_code(noise_error_code),
        .noise_bytes_accepted(noise_bytes_accepted),
        .noise_samples_produced(noise_samples_produced),
        .noise_narrow_count(noise_narrow_count),
        .noise_bound_slot(noise_bound_slot),
        .level_vco_1(level_vco_1), .level_vco_2(level_vco_2),
        .level_noise(level_noise),
        .post_vca_1(post_vca_1), .post_vca_2(post_vca_2),
        .post_vca_n(post_vca_n), .mix_word(mix_word), .mix_abs(mix_abs),
        .mix_peak_word(mix_peak_word), .mix_out_valid(mix_out_valid),
        .link_valid_out(link_valid_out),
        .mix_op_mults(mix_op_mults), .mix_op_adds(mix_op_adds),
        .mix_op_narrows(mix_op_narrows), .mix_op_sats(mix_op_sats),
        .mix_op_rounds(mix_op_rounds), .mix_op_peak_cmps(mix_op_peak_cmps),
        .mix_op_acc_faults(mix_op_acc_faults),
        .start(start), .mix_done(mix_done),
        .audio_out(audio_out), .audio_out_valid(audio_out_valid),
        .busy(busy), .done(done), .error(error), .error_code(error_code),
        .pass_index(pass_index), .samples_this_pass(samples_this_pass),
        .branch_normalized(branch_normalized), .peak_word(peak_word),
        .gain_word(gain_word),
        .op_compares(op_compares), .op_selects(op_selects),
        .op_recip_divs(op_recip_divs), .op_mults(op_mults),
        .op_narrows(op_narrows), .op_saturations(op_saturations)
    );

    // ---- stimulus memories ---------------------------------------------
    reg [31:0] shad   [0:CTL*18-1];
    reg [31:0] s_fq1  [0:AUDIO_MAX-1];
    reg [31:0] s_fq2  [0:AUDIO_MAX-1];
    reg [31:0] s_sqq  [0:AUDIO_MAX-1];
    reg [31:0] s_lqq  [0:AUDIO_MAX-1];
    reg [7:0]  nbytes [0:BYTES_MAX-1];

    integer ctlcap_fd   [0:MAX_RUNS-1];
    integer audiocap_fd [0:MAX_RUNS-1];
    integer linkcap_fd  [0:MAX_RUNS-1];
    integer outcap_fd   [0:MAX_RUNS-1];
    integer ops_fd      [0:MAX_RUNS-1];

    integer RUNS, r, fd, code, k;
    integer WALK, FULL, bidx, nbytes_total;
    integer host_pass;
    reg [31:0] a0, a1, a2, a3, a4, a5, a6, a7;
    reg [31:0] a8, a9, a10, a11, a12, a13, a14, a15;
    reg [1023:0] fname;
    integer cyc;

    initial cyc = 0;
    initial host_pass = 0;
    always @(posedge clk) begin
        cyc <= cyc + 1;
        if (!rst && r >= 0) begin
            if (audio_out_valid)
                $fwrite(outcap_fd[r], "%0d %0d\n", cyc, audio_out);
            // The replay controller's own consumed stream: mix_in qualified
            // by its mix_valid. A mutant that drops, duplicates or re-times a
            // sample at the link seam shows up HERE and nowhere upstream.
            if (link_valid_out)
                $fwrite(linkcap_fd[r], "%0d %0d %0d\n",
                        cyc, host_pass, mix_word);
        end
    end

    task fail(input reg [1023:0] why);
        begin
            $display("TB-ERROR %0s", why);
            $finish;
        end
    endtask

    // -------------------------------------------------------------------
    task load_run(input integer run);
        integer f, c, m;
        begin
            $sformat(fname, "run%0d_walk.txt", run);
            f = $fopen(fname, "r");
            if (f == 0) fail("cannot open walk file");
            c = $fscanf(f, "%d %d", WALK, FULL);
            $fclose(f);
            if (c != 2 || WALK < 1 || WALK > AUDIO_MAX)
                fail("bad walk file");
            nbytes_total = WALK * 4;

            $sformat(fname, "run%0d_params.txt", run);
            f = $fopen(fname, "r");
            if (f == 0) fail("cannot open params file");
            c = $fscanf(f, "%d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d",
                a0, a1, a2, a3, a4, a5, a6, a7,
                a8, a9, a10, a11, a12, a13, a14, a15);
            $fclose(f);
            if (c != 16) fail("short params line");
            midi_f0_word         = a0[C4_WIDTH-1:0];
            vco1_tuning_word     = a1[C4_WIDTH-1:0];
            vco1_depth_word      = a2[C4_WIDTH-1:0];
            vco1_init_phase_word = a3[C2_WIDTH-1:0];
            vco2_tuning_word     = a4[C4_WIDTH-1:0];
            vco2_depth_word      = a5[C4_WIDTH-1:0];
            vco2_shape_word      = a6[C4_WIDTH-1:0];
            vco2_partials_word   = a7[31:0];
            vco2_init_phase_word = a8[C2_WIDTH-1:0];
            level_vco_1          = a9[C1_WIDTH-1:0];
            level_vco_2          = a10[C1_WIDTH-1:0];
            level_noise          = a11[C1_WIDTH-1:0];
            noise_sound_index    = a12[C2_WIDTH-1:0];
            noise_declared_slot  = a13[4:0];
            adsr_eps60           = {a15[30:0], a14[31:0]};

            $sformat(fname, "run%0d_adsr.txt", run);
            f = $fopen(fname, "r");
            if (f == 0) fail("cannot open adsr file");
            for (m = 0; m < 6; m = m + 1) begin
                c = $fscanf(f, "%d %d %d %d %d %d %d %d %d %d %d %d %d",
                    a0, a1, a2, a3, a4, a5, a6, a7,
                    a8, a9, a10, a11, a12);
                if (c != 13) fail("short adsr line");
                adsr_duration_q[m*47 +: 47] = {a1[14:0], a0[31:0]};
                adsr_attack_q[m*47 +: 47]   = {a3[14:0], a2[31:0]};
                adsr_decay_q[m*47 +: 47]    = {a5[14:0], a4[31:0]};
                adsr_release_q[m*47 +: 47]  = {a7[14:0], a6[31:0]};
                adsr_duration_zero[m]       = a8[0];
                adsr_attack_zero[m]         = a9[0];
                adsr_decay_zero[m]          = a10[0];
                adsr_release_zero[m]        = a11[0];
                adsr_sustain_entry[m*C4_WIDTH +: C4_WIDTH] = a12[C4_WIDTH-1:0];
            end
            $fclose(f);

            $sformat(fname, "run%0d_lfo.txt", run);
            f = $fopen(fname, "r");
            if (f == 0) fail("cannot open lfo file");
            for (m = 0; m < 2; m = m + 1) begin
                c = $fscanf(f, "%d %d %d %d %d %d %d %d",
                    a0, a1, a2, a3, a4, a5, a6, a7);
                if (c != 8) fail("short lfo line");
                lfo_freq[m*C4_WIDTH +: C4_WIDTH]  = a0[C4_WIDTH-1:0];
                lfo_depth[m*C4_WIDTH +: C4_WIDTH] = a1[C4_WIDTH-1:0];
                lfo_init[m*C2_WIDTH +: C2_WIDTH]  = a2[C2_WIDTH-1:0];
                lfo_weights[(m*5 + 0)*32 +: 32]   = a3[31:0];
                lfo_weights[(m*5 + 1)*32 +: 32]   = a4[31:0];
                lfo_weights[(m*5 + 2)*32 +: 32]   = a5[31:0];
                lfo_weights[(m*5 + 3)*32 +: 32]   = a6[31:0];
                lfo_weights[(m*5 + 4)*32 +: 32]   = a7[31:0];
            end
            $fclose(f);

            $sformat(fname, "run%0d_depths.txt", run);
            f = $fopen(fname, "r");
            if (f == 0) fail("cannot open depths file");
            for (m = 0; m < 5; m = m + 1) begin
                c = $fscanf(f, "%d %d %d %d", a0, a1, a2, a3);
                if (c != 4) fail("short depths line");
                case (m)
                    0: depths_r0 = {a3[C4_WIDTH-1:0], a2[C4_WIDTH-1:0],
                                    a1[C4_WIDTH-1:0], a0[C4_WIDTH-1:0]};
                    1: depths_r1 = {a3[C4_WIDTH-1:0], a2[C4_WIDTH-1:0],
                                    a1[C4_WIDTH-1:0], a0[C4_WIDTH-1:0]};
                    2: depths_r2 = {a3[C4_WIDTH-1:0], a2[C4_WIDTH-1:0],
                                    a1[C4_WIDTH-1:0], a0[C4_WIDTH-1:0]};
                    3: depths_r3 = {a3[C4_WIDTH-1:0], a2[C4_WIDTH-1:0],
                                    a1[C4_WIDTH-1:0], a0[C4_WIDTH-1:0]};
                    4: depths_r4 = {a3[C4_WIDTH-1:0], a2[C4_WIDTH-1:0],
                                    a1[C4_WIDTH-1:0], a0[C4_WIDTH-1:0]};
                endcase
            end
            $fclose(f);

            $sformat(fname, "run%0d_shadow.txt", run);
            $readmemh(fname, shad);
            $sformat(fname, "run%0d_fq1.txt", run);
            $readmemh(fname, s_fq1);
            $sformat(fname, "run%0d_fq2.txt", run);
            $readmemh(fname, s_fq2);
            $sformat(fname, "run%0d_sqq.txt", run);
            $readmemh(fname, s_sqq);
            $sformat(fname, "run%0d_lqq.txt", run);
            $readmemh(fname, s_lqq);
            $sformat(fname, "run%0d_bytes.txt", run);
            $readmemh(fname, nbytes);
        end
    endtask

    // ---- the control walk: 1,764 four-cycle ticks ----------------------
    task control_walk(input integer run);
        integer tt, mm2;
        begin
            @(negedge clk);
            ctl_en_adsr = 1'b1;
            ctl_en_lfo  = 1'b1;
            ctl_en_mm   = 1'b1;
            ctl_trigger = 1'b1;
            ctl_load    = 1'b0;
            @(negedge clk);
            ctl_trigger = 1'b0;
            ctl_en_lfo  = 1'b0;
            ctl_en_mm   = 1'b0;
            for (tt = 0; tt < CTL; tt = tt + 1) begin
                // p0: the six envelope engines retire tick tt
                for (mm2 = 0; mm2 < 6; mm2 = mm2 + 1) begin
                    adsr_shadow_attack[mm2*32 +: 32]  = shad[tt*18 + mm2*3 + 0];
                    adsr_shadow_decay[mm2*32 +: 32]   = shad[tt*18 + mm2*3 + 1];
                    adsr_shadow_release[mm2*32 +: 32] = shad[tt*18 + mm2*3 + 2];
                end
                ctl_en_adsr = 1'b1;
                @(negedge clk);
                // p1: the two LFO/control-VCA engines retire tick tt
                ctl_en_adsr = 1'b0;
                ctl_en_lfo  = 1'b1;
                @(negedge clk);
                // p2: the matrix retires tick tt
                ctl_en_lfo = 1'b0;
                ctl_en_mm  = 1'b1;
                @(negedge clk);
                // p3: the matrix's route words land in the column memories
                ctl_en_mm      = 1'b0;
                ctl_load       = 1'b1;
                ctl_load_index = tt[10:0];
                if (mm_valid !== 1'b1)
                    fail("matrix out_valid low at the control capture point");
                if (adsr_valid !== 6'b111111)
                    fail("an envelope engine is not valid at tick capture");
                if (lfo_valid !== 2'b11)
                    fail("an LFO engine is not valid at tick capture");
                $fwrite(ctlcap_fd[run],
                    "%0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d\n",
                    tt,
                    $signed(adsr_env[0*C1_WIDTH +: C1_WIDTH]),
                    $signed(adsr_env[1*C1_WIDTH +: C1_WIDTH]),
                    $signed(adsr_env[2*C1_WIDTH +: C1_WIDTH]),
                    $signed(adsr_env[3*C1_WIDTH +: C1_WIDTH]),
                    $signed(adsr_env[4*C1_WIDTH +: C1_WIDTH]),
                    $signed(adsr_env[5*C1_WIDTH +: C1_WIDTH]),
                    $signed(lfo_raw[0*C1_WIDTH +: C1_WIDTH]),
                    $signed(lfo_raw[1*C1_WIDTH +: C1_WIDTH]),
                    $signed(lfo_post[0*C1_WIDTH +: C1_WIDTH]),
                    $signed(lfo_post[1*C1_WIDTH +: C1_WIDTH]),
                    $signed(mm_out[0*C1_WIDTH +: C1_WIDTH]),
                    $signed(mm_out[1*C1_WIDTH +: C1_WIDTH]),
                    $signed(mm_out[2*C1_WIDTH +: C1_WIDTH]),
                    $signed(mm_out[3*C1_WIDTH +: C1_WIDTH]),
                    $signed(mm_out[4*C1_WIDTH +: C1_WIDTH]));
                @(negedge clk);
                ctl_load = 1'b0;
            end
            // Control-domain op counters: one record per instance, written
            // once (the control walk happens once per clip).
            for (mm2 = 0; mm2 < 6; mm2 = mm2 + 1)
                $fwrite(ops_fd[run], "A %0d %0d %0d %0d %0d\n", mm2,
                    adsr_op_mults[mm2*32 +: 32], adsr_op_narrows[mm2*32 +: 32],
                    adsr_op_shadow[mm2*32 +: 32],
                    adsr_op_rampdivs[mm2*32 +: 32]);
            for (mm2 = 0; mm2 < 2; mm2 = mm2 + 1)
                $fwrite(ops_fd[run], "L %0d %0d %0d %0d %0d\n", mm2,
                    lfo_op_mults[mm2*32 +: 32], lfo_op_narrows[mm2*32 +: 32],
                    lfo_op_phases[mm2*32 +: 32], lfo_op_clamps[mm2*32 +: 32]);
            $fwrite(ops_fd[run], "M %0d %0d %0d %0d\n",
                mm_op_mults, mm_op_adds, mm_op_narrows, mm_op_sats);
        end
    endtask

    // One noise byte per cycle while bytes remain.
    task feed_byte;
        begin
            if (bidx < nbytes_total) begin
                noise_byte_in    = nbytes[bidx];
                noise_byte_valid = 1'b1;
                bidx = bidx + 1;
            end else begin
                noise_byte_valid = 1'b0;
            end
        end
    endtask

    // ---- one audio pass: WALK four-cycle groups ------------------------
    task walk_pass(input integer run, input integer pass);
        integer jj, pp, kk;
        begin
            @(negedge clk);
            host_pass = pass;
            up_en = 1'b0; vco_en = 1'b0; mix_en = 1'b0;
            noise_en      = 1'b1;
            audio_trigger = 1'b1;
            if (pass == 1) start = 1'b1;
            @(negedge clk);
            audio_trigger = 1'b0;
            start         = 1'b0;
            audio_go      = 1'b1;
            @(negedge clk);
            audio_go = 1'b0;
            // Prime the noise lane: four bytes so sample 0 is resident
            // before the first group's mixer cycle.
            bidx = 0;
            for (pp = 0; pp < 4; pp = pp + 1) begin
                feed_byte;
                @(negedge clk);
            end
            noise_byte_valid = 1'b0;
            for (jj = 0; jj < WALK; jj = jj + 1) begin
                // a0: the five upsample engines emit sample jj
                if (noise_sample_valid !== 1'b1)
                    fail("noise sample not valid at the head of a group");
                vco1_fq_word = s_fq1[jj][C3_WIDTH-1:0];
                vco2_fq_word = s_fq2[jj][C3_WIDTH-1:0];
                vco2_square_q_word = s_sqq[jj][C1_WIDTH-1:0];
                vco2_left_q_word   = s_lqq[jj][C1_WIDTH-1:0];
                up_en = 1'b1;
                feed_byte;
                @(negedge clk);
                // a1: both VCOs consume up[jj]
                up_en  = 1'b0;
                vco_en = 1'b1;
                feed_byte;
                @(negedge clk);
                // a2: the mixer consumes raw[jj], noise[jj], amp[jj]
                vco_en = 1'b0;
                mix_en = 1'b1;
                feed_byte;
                @(negedge clk);
                // a3: the replay engine consumes mix[jj] on the link seam
                mix_en = 1'b0;
                feed_byte;
                if (mix_out_valid !== 1'b1)
                    fail("mixer out_valid low at the audio capture point");
                $fwrite(audiocap_fd[run],
                    "%0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d\n",
                    cyc, pass, pass_index,
                    $signed(up_word[0*C1_WIDTH +: C1_WIDTH]),
                    $signed(up_word[1*C1_WIDTH +: C1_WIDTH]),
                    $signed(up_word[2*C1_WIDTH +: C1_WIDTH]),
                    $signed(up_word[3*C1_WIDTH +: C1_WIDTH]),
                    $signed(up_word[4*C1_WIDTH +: C1_WIDTH]),
                    vco1_word, vco2_word, noise_sample_word,
                    post_vca_1, post_vca_2, post_vca_n, mix_word);
                @(negedge clk);
            end
            noise_byte_valid = 1'b0;
            if (FULL == 1) noise_in_done = 1'b1;
            @(negedge clk);   // the replay engine consumes the last word
            noise_in_done = 1'b0;
            mix_done = 1'b1;
            @(negedge clk);
            mix_done = 1'b0;
            @(negedge clk);
            for (kk = 0; kk < 5; kk = kk + 1)
                $fwrite(ops_fd[run], "U %0d %0d %0d %0d %0d %0d %0d %0d %0d\n",
                    pass, kk,
                    up_op_mults[kk*32 +: 32], up_op_adds[kk*32 +: 32],
                    up_op_narrows[kk*32 +: 32], up_op_frac_words[kk*32 +: 32],
                    up_op_coord_steps[kk*32 +: 32], up_op_sats[kk*32 +: 32],
                    up_out_count[kk*32 +: 32]);
            $fwrite(ops_fd[run], "V1 %0d %0d %0d %0d %0d %0d %0d\n", pass,
                vco1_op_mults, vco1_op_adds, vco1_op_narrows,
                vco1_op_shadows, vco1_op_sats, vco1_op_clamps);
            $fwrite(ops_fd[run], "V2 %0d %0d %0d %0d %0d\n", pass,
                vco2_op_mults, vco2_op_adds, vco2_op_narrows, vco2_op_sats);
            $fwrite(ops_fd[run], "MIX %0d %0d %0d %0d %0d %0d %0d %0d\n", pass,
                mix_op_mults, mix_op_adds, mix_op_narrows, mix_op_sats,
                mix_op_rounds, mix_op_peak_cmps, mix_op_acc_faults);
            $fwrite(ops_fd[run], "NZ %0d %0d %0d %0d %0d %0d %0d\n", pass,
                noise_bytes_accepted, noise_samples_produced,
                noise_narrow_count, noise_error, noise_error_code,
                noise_bound_slot);
            // Drop the stream level for >= 1 cycle: per-stream noise state
            // clears between passes (the clip is re-streamed, never buffered).
            noise_en = 1'b0;
            @(negedge clk);
            @(negedge clk);
        end
    endtask

    initial begin
        fd = $fopen("runs.txt", "r");
        if (fd == 0) fail("cannot open runs.txt");
        code = $fscanf(fd, "%d", RUNS);
        $fclose(fd);
        if (code != 1 || RUNS < 1 || RUNS > MAX_RUNS)
            fail("bad run count");
        r = -1;
        for (k = 0; k < RUNS; k = k + 1) begin
            $sformat(fname, "run%0d_ctlcap.txt", k);
            ctlcap_fd[k] = $fopen(fname, "w");
            $sformat(fname, "run%0d_audiocap.txt", k);
            audiocap_fd[k] = $fopen(fname, "w");
            $sformat(fname, "run%0d_linkcap.txt", k);
            linkcap_fd[k] = $fopen(fname, "w");
            $sformat(fname, "run%0d_outcap.txt", k);
            outcap_fd[k] = $fopen(fname, "w");
            $sformat(fname, "run%0d_ops.txt", k);
            ops_fd[k] = $fopen(fname, "w");
            if (ctlcap_fd[k] == 0 || audiocap_fd[k] == 0
                || linkcap_fd[k] == 0 || outcap_fd[k] == 0 || ops_fd[k] == 0)
                fail("cannot open a capture file");
        end
        vco1_fq_word = 0; vco2_fq_word = 0;
        vco2_square_q_word = 0; vco2_left_q_word = 0;
        noise_byte_in = 8'd0;
        @(negedge clk);
        @(negedge clk);
        rst = 1'b0;
        @(negedge clk);
        for (r = 0; r < RUNS; r = r + 1) begin
            load_run(r);
            control_walk(r);
            walk_pass(r, 1);
            walk_pass(r, 2);
            @(negedge clk);
            $sformat(fname, "run%0d_status.txt", r);
            fd = $fopen(fname, "w");
            $fwrite(fd, "%0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d\n",
                error, error_code, peak_word, gain_word, branch_normalized,
                done, pass_index, op_compares, op_selects, op_recip_divs,
                op_mults, op_narrows, op_saturations, mix_peak_word);
            $fclose(fd);
        end
        for (k = 0; k < RUNS; k = k + 1) begin
            $fclose(ctlcap_fd[k]);
            $fclose(audiocap_fd[k]);
            $fclose(linkcap_fd[k]);
            $fclose(outcap_fd[k]);
            $fclose(ops_fd[k]);
        end
        $display("TB-DONE %0d", RUNS);
        $finish;
    end

endmodule
